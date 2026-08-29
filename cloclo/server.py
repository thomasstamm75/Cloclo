"""Service HTTP : expose un flux stable pour n'importe quelle page.

    GET /                      formulaire d'assistance
    GET /feed?url=…            le flux (rss | atom | json)
    GET /preview?url=…         diagnostic JSON de la détection
    GET /discover?url=…        flux déjà publiés par la page
    GET /healthz               sonde de vivacité
"""

from __future__ import annotations

import json
import threading
import traceback
import urllib.parse
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .extract import Options
from .fetch import FetchError, TTLCache, normalize_url
from .generator import Settings, build, collect

MAX_CONCURRENT = 8

INDEX = """<!doctype html>
<html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cloclo — flux RSS pour n'importe quelle page</title>
<style>
:root{color-scheme:light dark;--bg:#fbfbf9;--fg:#1b1b1a;--muted:#6b6b66;--line:#dedcd6;--accent:#a2542a}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--fg:#ececea;--muted:#9a9a95;--line:#2e3035;--accent:#e08a52}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:52rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
h1{font-size:1.6rem;margin:0 0 .25rem}
p.lead{color:var(--muted);margin:0 0 2rem}
fieldset{border:1px solid var(--line);border-radius:.6rem;padding:1rem 1.1rem;margin:0 0 1rem}
legend{padding:0 .4rem;color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.06em}
label{display:block;font-size:.85rem;color:var(--muted);margin:.6rem 0 .2rem}
input,select{width:100%;padding:.55rem .65rem;border:1px solid var(--line);border-radius:.4rem;background:var(--bg);color:var(--fg);font:inherit;font-size:.95rem}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:0 1rem}
.actions{display:flex;gap:.6rem;flex-wrap:wrap;margin:1.2rem 0}
button{padding:.6rem 1.1rem;border:1px solid var(--accent);border-radius:.4rem;background:var(--accent);color:#fff;font:inherit;cursor:pointer}
button.ghost{background:transparent;color:var(--accent)}
code,output{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85rem;word-break:break-all}
#url-out{display:block;padding:.7rem .8rem;border:1px dashed var(--line);border-radius:.4rem;background:transparent;margin-bottom:1.5rem}
#result{border-top:1px solid var(--line);padding-top:1rem}
.item{padding:.7rem 0;border-bottom:1px solid var(--line)}
.item a{color:var(--fg)}
.item .meta{color:var(--muted);font-size:.8rem}
.tag{display:inline-block;padding:.05rem .4rem;border:1px solid var(--line);border-radius:1rem;font-size:.72rem;color:var(--muted)}
.err{color:#c0392b}
details{margin-top:1rem}
</style>
<main>
<h1>Cloclo</h1>
<p class="lead">Un flux RSS pour n'importe quelle page au contenu gratuit. Collez une URL,
vérifiez ce qui est détecté, abonnez-vous.</p>
<form id="f">
  <label for="url">Adresse de la page</label>
  <input id="url" name="url" type="url" required placeholder="https://exemple.fr/actualites">
  <div class="row">
    <div><label for="format">Format</label>
      <select id="format" name="format"><option>rss</option><option>atom</option><option>json</option></select></div>
    <div><label for="limit">Entrées</label><input id="limit" name="limit" type="number" min="1" max="100" value="20"></div>
    <div><label for="full">Texte intégral</label>
      <select id="full" name="full"><option value="0">non</option><option value="1">oui</option></select></div>
    <div><label for="include_paid">Articles payants</label>
      <select id="include_paid" name="include_paid"><option value="0">exclure</option><option value="1">inclure</option></select></div>
  </div>
  <details><summary>Sélecteurs CSS (si la détection automatique se trompe)</summary>
    <fieldset><legend>Ciblage manuel</legend>
    <div class="row">
      <div><label for="item">Bloc d'une entrée</label><input id="item" name="item" placeholder=".post"></div>
      <div><label for="title">Titre</label><input id="title" name="title" placeholder="h2 a"></div>
      <div><label for="link">Lien</label><input id="link" name="link" placeholder="h2 a@href"></div>
      <div><label for="date">Date</label><input id="date" name="date" placeholder="time@datetime"></div>
      <div><label for="summary">Résumé</label><input id="summary" name="summary" placeholder=".chapo"></div>
      <div><label for="author">Auteur</label><input id="author" name="author" placeholder=".byline"></div>
    </div></fieldset>
  </details>
  <div class="actions">
    <button type="submit">Prévisualiser</button>
    <button type="button" class="ghost" id="open">Ouvrir le flux</button>
    <button type="button" class="ghost" id="copy">Copier l'adresse</button>
  </div>
</form>
<output id="url-out">—</output>
<div id="result"></div>
</main>
<script>
const form = document.getElementById('f');
const out = document.getElementById('url-out');
const result = document.getElementById('result');

function params(){
  const data = new FormData(form), q = new URLSearchParams();
  for (const [k, v] of data) if (v && v !== '0') q.set(k, v);
  return q;
}
function feedUrl(){
  const q = params();
  if (!q.get('url')) return '';
  return location.origin + '/feed?' + q.toString();
}
function refresh(){ out.textContent = feedUrl() || '—'; }
form.addEventListener('input', refresh);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  refresh();
  result.innerHTML = '<p>Analyse en cours…</p>';
  const q = params(); q.delete('format');
  try {
    const response = await fetch('/preview?' + q.toString());
    const data = await response.json();
    if (data.error) { result.innerHTML = '<p class="err">' + data.error + '</p>'; return; }
    const head = `<p><span class="tag">${data.strategy}</span> ${data.items.length} entrée(s)` +
      (data.selectors && data.selectors.item ? ` · <code>${data.selectors.item}</code>` : '') + '</p>';
    const feeds = (data.feeds || []).length
      ? '<p>Le site publie déjà : ' + data.feeds.map(f => `<a href="${f.url}">${f.url}</a>`).join(', ') + '</p>' : '';
    const items = data.items.map(i => `<div class="item">
        <a href="${i.link}">${i.title}</a>
        <div class="meta">${[i.date || '', i.author || '', i.free ? '' : 'payant'].filter(Boolean).join(' · ')}</div>
        <div class="meta">${(i.summary || '').slice(0, 180)}</div></div>`).join('');
    result.innerHTML = head + feeds + items;
  } catch (error) {
    result.innerHTML = '<p class="err">' + error + '</p>';
  }
});
document.getElementById('open').onclick = () => { const u = feedUrl(); if (u) location.href = u; };
document.getElementById('copy').onclick = async () => {
  const u = feedUrl(); if (!u) return;
  try { await navigator.clipboard.writeText(u); out.textContent = u + '  ✓ copié'; } catch (e) { out.textContent = u; }
};
</script>
</html>
"""


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "oui", "yes", "on")


class FeedHandler(BaseHTTPRequestHandler):
    server_version = f"Cloclo/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- utilitaires -----------------------------------------------------
    def _send(self, status, body, content_type, extra=None):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, status, payload, extra=None):
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            "application/json; charset=utf-8",
            extra,
        )

    def _error(self, status, message):
        if "text/html" in (self.headers.get("Accept") or ""):
            self._send(status, f"<h1>{status}</h1><p>{message}</p>", "text/html; charset=utf-8")
        else:
            self._json(status, {"error": message})

    def log_message(self, fmt, *args):  # pragma: no cover - bruit console
        if self.server.verbose:
            super().log_message(fmt, *args)

    # -- réglages issus de la requête ------------------------------------
    def _target(self, query):
        raw = (query.get("url") or [""])[0]
        if not raw:
            raise ValueError("paramètre 'url' manquant")
        url = normalize_url(raw)
        allowed = self.server.allow_hosts
        if allowed:
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
            if not any(host == a or host.endswith("." + a) for a in allowed):
                raise PermissionError(f"domaine non autorisé : {host}")
        return url

    def _options(self, query):
        def first(name, default=""):
            return (query.get(name) or [default])[0]

        try:
            limit = int(first("limit", "30"))
        except ValueError:
            limit = 30
        return Options(
            item=first("item"),
            title=first("title"),
            link=first("link"),
            date=first("date"),
            summary=first("summary"),
            author=first("author"),
            image=first("image"),
            category=first("category"),
            limit=max(1, min(limit, self.server.max_limit)),
            include_paid=_bool(first("include_paid")),
            strategy=first("strategy", "auto") or "auto",
        )

    def _settings(self, query, self_url=""):
        def first(name, default=""):
            return (query.get(name) or [default])[0]

        fmt = first("format", "rss").lower()
        if fmt not in ("rss", "atom", "json"):
            fmt = "rss"
        try:
            full_limit = int(first("full_limit", "5"))
        except ValueError:
            full_limit = 5
        return Settings(
            fmt=fmt,
            full=_bool(first("full")),
            full_limit=max(0, min(full_limit, 20)),
            respect_robots=self.server.respect_robots,
            allow_private=self.server.allow_private,
            self_url=self_url,
            title=first("feed_title"),
            recipes_path=self.server.recipes_path,
        )

    def _self_url(self, path, query_string):
        base = self.server.base_url or f"http://{self.headers.get('Host', 'localhost')}"
        return f"{base.rstrip('/')}{path}?{query_string}" if query_string else base

    # -- routage ---------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        route = parsed.path.rstrip("/") or "/"

        if route == "/":
            self._send(200, INDEX, "text/html; charset=utf-8")
            return
        if route == "/healthz":
            self._json(200, {"status": "ok", "version": __version__})
            return
        if route == "/robots.txt":
            self._send(200, "User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
            return
        if route not in ("/feed", "/preview", "/discover"):
            self._error(404, "route inconnue")
            return

        try:
            url = self._target(query)
        except ValueError as error:
            self._error(400, str(error))
            return
        except PermissionError as error:
            self._error(403, str(error))
            return

        with self.server.slots:
            try:
                if route == "/feed":
                    self._serve_feed(url, query, parsed)
                elif route == "/preview":
                    self._serve_preview(url, query)
                else:
                    self._serve_discover(url, query)
            except FetchError as error:
                self._error(error.status if error.status in (403, 404) else 502, str(error))
            except ValueError as error:
                self._error(400, str(error))
            except Exception:  # pragma: no cover - filet de sécurité
                if self.server.verbose:
                    traceback.print_exc()
                self._error(500, "erreur interne")

    def _cache_key(self, route, query):
        return route + "?" + urllib.parse.urlencode(sorted(query.items()), doseq=True)

    def _serve_feed(self, url, query, parsed):
        key = self._cache_key("/feed", query)
        cached = self.server.cache.get(key)
        if cached is None:
            options = self._options(query)
            settings = self._settings(query, self._self_url("/feed", parsed.query))
            result = build(url, options, settings)
            cached = (result.body, result.content_type)
            self.server.cache.set(key, cached)
        body, content_type = cached
        etag = '"%s"' % sha1(body.encode("utf-8")).hexdigest()[:20]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(
            200,
            body,
            content_type,
            {
                "ETag": etag,
                "Cache-Control": f"public, max-age={int(self.server.cache.ttl)}",
                "X-Robots-Tag": "noindex",
            },
        )

    def _serve_preview(self, url, query):
        extraction, recipe = collect(url, self._options(query), self._settings(query))
        self._json(
            200,
            {
                "url": extraction.page.url,
                "title": extraction.page.title,
                "strategy": extraction.strategy,
                "selectors": extraction.selectors,
                "candidates": extraction.candidates,
                "recipe": recipe,
                "feeds": extraction.page.feeds,
                "items": [item.as_dict() for item in extraction.items],
            },
        )

    def _serve_discover(self, url, query):
        from . import dom
        from .extract import discover_feeds
        from .fetch import fetch as http_get

        response = http_get(
            url,
            respect_robots=self.server.respect_robots,
            allow_private=self.server.allow_private,
        )
        self._json(200, {"url": response.url, "feeds": discover_feeds(dom.parse(response.text), response.url)})


class FeedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, **config):
        super().__init__(address, handler)
        self.cache = TTLCache(ttl=config.get("ttl", 900.0), max_entries=512)
        self.base_url = config.get("base_url", "")
        self.respect_robots = config.get("respect_robots", True)
        self.allow_private = config.get("allow_private", False)
        self.allow_hosts = [h.lower().lstrip(".") for h in config.get("allow_hosts") or []]
        self.recipes_path = config.get("recipes_path", "")
        self.max_limit = config.get("max_limit", 100)
        self.verbose = config.get("verbose", True)
        self.slots = threading.Semaphore(MAX_CONCURRENT)


def serve(host="127.0.0.1", port=8787, **config):
    server = FeedServer((host, port), FeedHandler, **config)
    shown = config.get("base_url") or f"http://{host}:{port}"
    print(f"Cloclo {__version__} écoute sur {shown}")
    print(f"  {shown}/feed?url=https://exemple.fr/actualites")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        server.server_close()
    return server
