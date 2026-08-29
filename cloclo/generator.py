"""Chaîne complète : URL → page → entrées → flux sérialisé."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import article, dom, extract, feed, fetch, recipes
from .extract import Options


@dataclass
class Settings:
    """Paramètres réseau et de rendu (les réglages d'extraction sont dans `Options`)."""

    fmt: str = "rss"
    full: bool = False
    full_limit: int = 5
    full_workers: int = 4
    timeout: float = fetch.DEFAULT_TIMEOUT
    user_agent: str = fetch.DEFAULT_UA
    respect_robots: bool = True
    allow_private: bool = False
    cache: bool = True
    self_url: str = ""
    title: str = ""
    recipes_path: str = ""
    use_recipes: bool = True


@dataclass
class Result:
    body: str
    content_type: str
    items: list = field(default_factory=list)
    extraction: object = None
    recipe: object = None


def collect(url, options=None, settings=None):
    """Télécharge la page et renvoie l'`Extraction` correspondante."""
    options = options or Options()
    settings = settings or Settings()
    url = fetch.normalize_url(url)

    recipe = None
    if settings.use_recipes:
        recipe = recipes.find(url, settings.recipes_path)
        options = recipes.apply(recipe, options)

    response = fetch.fetch(
        url,
        timeout=settings.timeout,
        user_agent=settings.user_agent,
        respect_robots=settings.respect_robots,
        allow_private=settings.allow_private,
        cache=settings.cache,
    )
    extraction = extract.extract(
        response.text, response.url, options, content_type=response.content_type
    )
    return extraction, recipe


def enrich(items, options, settings):
    """Récupère le texte intégral des entrées (dans la limite de `full_limit`)."""
    targets = [i for i in items if i.link][: max(settings.full_limit, 0)]
    if not targets:
        return items

    def load(item):
        try:
            response = fetch.fetch(
                item.link,
                timeout=settings.timeout,
                user_agent=settings.user_agent,
                respect_robots=settings.respect_robots,
                allow_private=settings.allow_private,
                cache=settings.cache,
            )
        except fetch.FetchError:
            return
        page = article.readable(response.text, response.url)
        if not extract.page_is_free(dom.parse(response.text)):
            item.free = False
        item.content_html = page["html"] or item.content_html
        if not item.summary:
            item.summary = page["excerpt"]
        if item.date is None:
            item.date = page["date"]
        if not item.image:
            item.image = page["image"]
        if not item.author:
            item.author = page["byline"]
        if len(item.title) < 8 and page["title"]:
            item.title = page["title"]

    workers = max(1, min(settings.full_workers, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(load, targets))

    if not options.include_paid:
        items = [i for i in items if i.free]
    return items


def build(url, options=None, settings=None):
    """Construit le flux et renvoie un `Result` prêt à écrire ou à servir."""
    options = options or Options()
    settings = settings or Settings()
    extraction, recipe = collect(url, options, settings)
    items = extraction.items
    if settings.full:
        items = enrich(items, options, settings)

    page = extraction.page
    meta = feed.FeedMeta(
        title=settings.title or page.title or page.site_name or url,
        link=page.url or url,
        description=page.description,
        self_url=settings.self_url,
        language=(page.language or "").replace("_", "-"),
        icon=page.icon,
    )
    body, content_type = feed.render(items, meta, settings.fmt, full=settings.full)
    return Result(body, content_type, items, extraction, recipe)
