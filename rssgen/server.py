"""Serveur local : génère les flux à la demande, sans publication préalable.

Utile pour tester un sélecteur, ou pour faire tourner rssgen en permanence sur
une machine du réseau que Feeder interroge directement.
"""

from __future__ import annotations

import html as html_module
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .extract import Rules, extract_articles
from .fetch import Fetcher, FetchError
from .rss import build_rss
from .state import FeedState
from .util import domain_of, slugify

RSS_MIME = "application/rss+xml; charset=utf-8"


class FeedHandler(BaseHTTPRequestHandler):
    server_version = "rssgen"
    config: Config
    fetcher: Fetcher
    state_dir: Path
    open_urls: bool

    def do_GET(self) -> None:  # noqa: N802 (nom imposé par la classe de base)
        route = urlparse(self.path)
        query = parse_qs(route.query)
        try:
            if route.path in ("/", "/index.html"):
                self._send_html(self._index_page())
            elif route.path == "/feed":
                self._serve_adhoc(query)
            elif route.path.startswith("/feeds/"):
                self._serve_configured(route.path.removeprefix("/feeds/"))
            elif route.path == "/healthz":
                self._send(200, "text/plain; charset=utf-8", b"ok\n")
            else:
                self._error(404, "Adresse inconnue.")
        except FetchError as exc:
            self._error(502, f"Page inaccessible : {exc}")
        except BrokenPipeError:
            pass  # le client a fermé la connexion, rien à signaler
        except Exception:
            traceback.print_exc()
            self._error(500, "Erreur interne du générateur.")

    # ------------------------------------------------------------------ routes
    def _serve_adhoc(self, query: dict[str, list[str]]) -> None:
        if not self.open_urls:
            self._error(403, "Le mode URL libre est désactivé. "
                             "Relancez avec « rssgen serve --open-urls ».")
            return
        url = (query.get("url") or [""])[0].strip()
        if not url.startswith(("http://", "https://")):
            self._error(400, "Paramètre « url » manquant ou invalide.")
            return

        rules = Rules(
            item=(query.get("item") or [""])[0],
            title=(query.get("title") or [""])[0],
            date=(query.get("date") or [""])[0],
            summary=(query.get("summary") or [""])[0],
        )
        limit = _int_param(query, "limit", 25)
        reply = self.fetcher.get(url, use_cache=True)
        articles = extract_articles(reply.text, reply.url, rules, limit=limit)
        if not articles:
            self._error(422, "Aucun article trouvé sur cette page.")
            return

        state = FeedState(self.state_dir / f"adhoc-{slugify(url)}.json")
        for article in articles:
            article.published = state.stamp(article.guid, article.title, article.published)
        state.save()
        articles.sort(key=lambda a: a.published, reverse=True)

        xml = build_rss(articles, title=domain_of(reply.url), link=reply.url,
                        description=f"Flux généré à la demande depuis {reply.url}")
        self._send(200, RSS_MIME, xml.encode("utf-8"))

    def _serve_configured(self, name: str) -> None:
        feed_id = slugify(name.removesuffix(".xml"))
        feed = self.config.get(feed_id)
        if feed is None:
            self._error(404, f"Flux « {feed_id} » absent de la configuration.")
            return

        reply = self.fetcher.get(feed.url, use_cache=True)
        articles = extract_articles(reply.text, reply.url, feed.selectors,
                                    limit=feed.max_items * 2)
        articles = [a for a in articles if feed.matches(a.title, a.summary, a.link)]
        if not articles:
            self._error(422, "Aucun article trouvé — vérifiez les sélecteurs.")
            return

        state = FeedState(self.state_dir / f"{feed.id}.json")
        for article in articles:
            article.published = state.stamp(article.guid, article.title, article.published)
        state.save()
        articles.sort(key=lambda a: a.published, reverse=True)

        xml = build_rss(articles[:feed.max_items],
                        title=feed.title or feed.id, link=reply.url,
                        description=feed.description, language=feed.language,
                        ttl=feed.ttl)
        self._send(200, RSS_MIME, xml.encode("utf-8"))

    # ------------------------------------------------------------------ sortie
    def _index_page(self) -> str:
        escape = html_module.escape
        host = self.headers.get("Host", "localhost")
        items = "".join(
            f'<li><a href="/feeds/{escape(f.id)}.xml">{escape(f.title or f.id)}</a>'
            f' — <code>http://{escape(host)}/feeds/{escape(f.id)}.xml</code></li>'
            for f in self.config.active
        ) or "<li>Aucun flux configuré.</li>"
        adhoc = ("<p>Mode URL libre actif : "
                 f"<code>http://{escape(host)}/feed?url=https://exemple.fr/actus</code></p>"
                 if self.open_urls else
                 "<p>Mode URL libre désactivé (option <code>--open-urls</code>).</p>")
        return (
            '<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>rssgen</title><style>body{font:16px/1.5 system-ui;margin:2rem auto;"
            "max-width:44rem;padding:0 1rem}code{background:#eee9df;padding:.1rem .3rem;"
            "border-radius:.25rem;font-size:.85em}li{margin:.4rem 0}</style></head><body>"
            "<h1>rssgen</h1><p>Flux servis à la demande. Copiez une adresse dans Feeder.</p>"
            f"<ul>{items}</ul>{adhoc}</body></html>"
        )

    def _send(self, status: int, mime: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=900")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, markup: str) -> None:
        self._send(200, "text/html; charset=utf-8", markup.encode("utf-8"))

    def _error(self, status: int, message: str) -> None:
        self._send(status, "text/plain; charset=utf-8", f"{message}\n".encode("utf-8"))

    def log_message(self, fmt: str, *fmt_args) -> None:
        print(f"{self.address_string()} — {fmt % fmt_args}")


def _int_param(query: dict[str, list[str]], key: str, fallback: int) -> int:
    try:
        return max(1, min(100, int((query.get(key) or [str(fallback)])[0])))
    except ValueError:
        return fallback


def serve(config: Config, host: str = "127.0.0.1", port: int = 8777,
          state_dir: Path | str = "state", open_urls: bool = False) -> None:
    handler = type("BoundFeedHandler", (FeedHandler,), {
        "config": config,
        "fetcher": Fetcher(cache_dir=".cache", timeout=config.timeout,
                           delay=config.request_delay),
        "state_dir": Path(state_dir),
        "open_urls": open_urls,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"rssgen écoute sur http://{host}:{port}")
    print(f"{len(config.active)} flux configuré(s). Ctrl+C pour arrêter.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        httpd.server_close()
