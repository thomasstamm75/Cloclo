"""Orchestration : de la page web au fichier XML publié."""

from __future__ import annotations

import html as html_module
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, FeedConfig
from .dates import now
from .extract import Article, extract_articles, extract_full_content
from .fetch import Fetcher, FetchError
from .rss import build_opml, build_rss
from .state import FeedState
from .util import digest, domain_of, truncate


@dataclass
class BuildResult:
    feed: FeedConfig
    ok: bool = True
    items: int = 0
    new_items: int = 0
    error: str = ""
    path: Path | None = None
    from_cache: bool = False
    articles: list[Article] = field(default_factory=list)

    @property
    def status_line(self) -> str:
        if not self.ok:
            return f"✗ {self.feed.id} — {self.error}"
        detail = f"{self.items} article(s)"
        if self.new_items:
            detail += f", dont {self.new_items} nouveau(x)"
        if self.from_cache:
            detail += " [copie en cache]"
        return f"✓ {self.feed.id} — {detail}"


class Builder:
    def __init__(self, config: Config, output_dir: Path | str = "docs",
                 state_dir: Path | str = "state",
                 cache_dir: Path | str | None = ".cache") -> None:
        self.config = config
        self.output_dir = Path(output_dir)
        self.feeds_dir = self.output_dir / "feeds"
        self.state_dir = Path(state_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.fetcher = Fetcher(
            cache_dir=self.cache_dir,
            timeout=config.timeout,
            delay=config.request_delay,
            **({"user_agent": config.user_agent} if config.user_agent else {}),
        )

    # --------------------------------------------------------------- un flux
    def build_feed(self, feed: FeedConfig, write: bool = True) -> BuildResult:
        result = BuildResult(feed=feed)
        try:
            reply = self.fetcher.get(feed.url)
        except FetchError as exc:
            result.ok, result.error = False, str(exc)
            return result

        result.from_cache = reply.from_cache
        try:
            articles = extract_articles(reply.text, reply.url, feed.selectors,
                                        limit=feed.max_items * 3)
        except Exception as exc:  # un sélecteur exotique ne doit pas tout arrêter
            result.ok, result.error = False, f"extraction impossible : {exc}"
            return result

        articles = [a for a in articles if feed.matches(a.title, a.summary, a.link)]
        if not articles:
            result.ok = False
            result.error = ("aucun article trouvé — vérifiez les sélecteurs "
                            "avec « rssgen inspect »")
            return result

        state = FeedState(self.state_dir / f"{feed.id}.json")
        new_count = 0
        for article in articles:
            if state.is_new(article.guid):
                new_count += 1
            article.published = state.stamp(article.guid, article.title, article.published)

        articles.sort(key=lambda a: a.published, reverse=True)
        articles = articles[:feed.max_items]

        if feed.full_text:
            self._add_full_text(feed, articles)

        result.items = len(articles)
        result.new_items = new_count
        result.articles = articles

        if write:
            result.path = self._write_feed(feed, articles, reply.url)
            state.save()
        return result

    def _add_full_text(self, feed: FeedConfig, articles: list[Article]) -> None:
        """Complète les articles avec leur contenu intégral, dans la limite fixée.

        Le contenu est mis en cache sur disque : une page d'article ne change
        pratiquement jamais, inutile de la retélécharger à chaque génération.
        """
        store = self.cache_dir / "content" if self.cache_dir else None
        if store:
            store.mkdir(parents=True, exist_ok=True)

        budget = feed.max_full_text
        for article in articles:
            if article.content_html:
                continue
            cached = store / f"{digest(article.guid)}.html" if store else None
            if cached and cached.exists():
                try:
                    article.content_html = cached.read_text(encoding="utf-8")
                    continue
                except OSError:
                    pass
            if budget <= 0:
                continue
            budget -= 1
            try:
                page = self.fetcher.get(article.link)
            except FetchError:
                continue
            content = extract_full_content(page.text, page.url, feed.full_text_selector)
            if not content:
                continue
            article.content_html = content
            if cached:
                try:
                    cached.write_text(content, encoding="utf-8")
                except OSError:
                    pass

    def _write_feed(self, feed: FeedConfig, articles: list[Article],
                    source_url: str) -> Path:
        self.feeds_dir.mkdir(parents=True, exist_ok=True)
        base = self.config.site.base_url
        feed_url = f"{base}/feeds/{feed.filename}" if base else ""
        title = feed.title or f"{domain_of(source_url)} — {feed.id}"
        description = feed.description or (
            f"Flux non officiel généré à partir de {source_url}")

        xml = build_rss(articles, title=title, link=source_url,
                        description=description, feed_url=feed_url,
                        language=feed.language, ttl=feed.ttl)
        path = self.feeds_dir / feed.filename
        path.write_text(xml, encoding="utf-8")
        return path

    # ------------------------------------------------------------ tous les flux
    def build_all(self, only: list[str] | None = None,
                  write: bool = True) -> list[BuildResult]:
        selected = [f for f in self.config.active if not only or f.id in only]
        results = [self.build_feed(feed, write=write) for feed in selected]
        if write:
            self.write_index(results)
        return results

    def write_index(self, results: list[BuildResult]) -> None:
        """Écrit la page d'accueil et le fichier OPML d'import groupé."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        base = self.config.site.base_url
        entries = [
            (r.feed.title or r.feed.id,
             f"{base}/feeds/{r.feed.filename}" if base else f"feeds/{r.feed.filename}",
             r.feed.url)
            for r in results if r.ok
        ]
        (self.output_dir / "feeds.opml").write_text(
            build_opml(entries, title=self.config.site.title), encoding="utf-8")
        (self.output_dir / "index.html").write_text(
            _render_index(self.config, results), encoding="utf-8")
        # Empêche GitHub Pages de passer le dossier dans Jekyll.
        (self.output_dir / ".nojekyll").write_text("", encoding="utf-8")


def _render_index(config: Config, results: list[BuildResult]) -> str:
    """Page listant les flux, avec les URL à copier dans Feeder."""
    base = config.site.base_url
    escape = html_module.escape
    rows = []
    for result in sorted(results, key=lambda r: (not r.ok, r.feed.id)):
        feed = result.feed
        url = f"{base}/feeds/{feed.filename}" if base else f"feeds/{feed.filename}"
        name = escape(feed.title or feed.id)
        source = escape(feed.url)
        if result.ok:
            latest = result.articles[0].title if result.articles else ""
            rows.append(f"""      <li class="feed">
        <div class="feed__head"><a class="feed__name" href="{escape(url)}">{name}</a>
          <span class="feed__count">{result.items} articles</span></div>
        <p class="feed__source">source : <a href="{source}">{source}</a></p>
        <p class="feed__last">{escape(truncate(latest, 90))}</p>
        <input class="feed__url" value="{escape(url)}" readonly onclick="this.select()">
      </li>""")
        else:
            rows.append(f"""      <li class="feed feed--ko">
        <div class="feed__head"><span class="feed__name">{name}</span>
          <span class="feed__count feed__count--ko">en échec</span></div>
        <p class="feed__source">source : <a href="{source}">{source}</a></p>
        <p class="feed__last">{escape(result.error)}</p>
      </li>""")

    opml = f"{base}/feeds.opml" if base else "feeds.opml"
    generated = now().strftime("%d/%m/%Y à %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(config.site.title)}</title>
<style>
  :root {{ color-scheme: light dark; --bg:#fbfaf8; --fg:#1c1b19; --muted:#6b675f;
           --card:#fff; --line:#e5e1d8; --accent:#8a5a20; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#16150f; --fg:#eeeae1; --muted:#a29c90; --card:#211f18;
             --line:#332f26; --accent:#d9a441; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:2.5rem 1.25rem; background:var(--bg); color:var(--fg);
          font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width: 46rem; margin: 0 auto; }}
  h1 {{ font-size:1.6rem; margin:0 0 .35rem; letter-spacing:-.01em; }}
  .lede {{ color:var(--muted); margin:0 0 1.5rem; }}
  .opml {{ display:inline-block; padding:.55rem .9rem; border:1px solid var(--line);
           border-radius:.5rem; background:var(--card); color:var(--accent);
           text-decoration:none; font-weight:600; font-size:.9rem; }}
  ul {{ list-style:none; padding:0; margin:1.75rem 0 0; display:grid; gap:.85rem; }}
  .feed {{ background:var(--card); border:1px solid var(--line); border-radius:.65rem;
           padding:1rem 1.1rem; }}
  .feed--ko {{ opacity:.72; }}
  .feed__head {{ display:flex; justify-content:space-between; align-items:baseline;
                 gap:.75rem; }}
  .feed__name {{ font-weight:650; color:var(--fg); text-decoration:none; }}
  .feed__name:hover {{ color:var(--accent); }}
  .feed__count {{ font-size:.78rem; color:var(--muted); white-space:nowrap; }}
  .feed__count--ko {{ color:#c0392b; }}
  .feed__source, .feed__last {{ margin:.3rem 0 0; font-size:.85rem; color:var(--muted);
                                overflow-wrap:anywhere; }}
  .feed__source a {{ color:inherit; }}
  .feed__url {{ width:100%; margin-top:.7rem; padding:.45rem .6rem; font-size:.8rem;
                font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
                border:1px solid var(--line); border-radius:.4rem;
                background:var(--bg); color:var(--fg); }}
  footer {{ margin-top:2.5rem; color:var(--muted); font-size:.82rem; }}
</style>
</head>
<body>
<main>
  <h1>{escape(config.site.title)}</h1>
  <p class="lede">{escape(config.site.description)}</p>
  <a class="opml" href="{escape(opml)}">Importer tous les flux dans Feeder (OPML)</a>
  <ul>
{chr(10).join(rows)}
  </ul>
  <footer>Généré le {generated} par rssgen.</footer>
</main>
</body>
</html>
"""
