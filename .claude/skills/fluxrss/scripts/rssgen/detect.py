"""Détection des flux déjà publiés par un site.

L'outil ne sert qu'aux pages dépourvues de flux : avant d'en fabriquer un, on
vérifie que le site n'en propose pas déjà un, souvent non annoncé sur la page.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from .extract import describe_autodetect, make_soup
from .fetch import Fetcher, FetchError
from .util import clean_text, domain_of, slugify

FEED_MIME_TYPES = (
    "application/rss+xml", "application/atom+xml", "application/feed+json",
    "application/json", "text/xml", "application/xml", "application/rdf+xml",
)

# Adresses conventionnelles testées quand la page n'annonce aucun flux.
COMMON_FEED_PATHS = (
    "/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
    "/index.xml", "/blog/feed", "/actualites/feed", "/news/feed",
    "/?feed=rss2", "/feeds/posts/default", "/rss/feed.xml",
)


def declared_feeds(html: str, base_url: str) -> list[dict]:
    """Flux annoncés dans le <head> par <link rel="alternate">."""
    soup = make_soup(html)
    found: list[dict] = []
    seen: set[str] = set()
    for link in soup.find_all("link", href=True):
        relations = {str(r).lower() for r in (link.get("rel") or [])}
        mime = (link.get("type") or "").lower()
        if "alternate" not in relations or mime not in FEED_MIME_TYPES:
            continue
        if mime in ("text/xml", "application/xml", "application/json"):
            # Trop générique pour être un flux sans indice supplémentaire.
            if "feed" not in (link.get("href") or "").lower() and "rss" not in (
                    link.get("title") or "").lower():
                continue
        url = urljoin(base_url, link["href"])
        if url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "title": clean_text(link.get("title", "")) or "Flux",
                      "type": mime, "source": "déclaré dans la page"})
    return found


def probe_common_paths(base_url: str, fetcher: Fetcher) -> list[dict]:
    """Essaie les adresses de flux les plus répandues sur la racine du site."""
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    found = []
    for path in COMMON_FEED_PATHS:
        candidate = urljoin(root + "/", path.lstrip("/"))
        try:
            reply = fetcher.get(candidate, use_cache=False)
        except FetchError:
            continue
        head = reply.text.lstrip()[:600].lower()
        if any(marker in head for marker in ("<rss", "<feed", "<rdf:rdf", '"version":"https://jsonfeed.org')):
            found.append({"url": reply.url, "title": "Flux existant",
                          "type": "détecté", "source": f"adresse conventionnelle {path}"})
            break  # un seul suffit à conclure que le site publie déjà un flux
    return found


def inspect(url: str, fetcher: Fetcher, probe: bool = True) -> dict:
    """Analyse complète d'une page : flux existants et structure détectée.

    Retourne de quoi décider s'il faut fabriquer un flux et, si oui, avec
    quels sélecteurs.
    """
    reply = fetcher.get(url, use_cache=False)
    soup = make_soup(reply.text)
    title_node = soup.find("title")
    site_title = clean_text(title_node.get_text()) if title_node else domain_of(reply.url)

    feeds = declared_feeds(reply.text, reply.url)
    if not feeds and probe:
        feeds = probe_common_paths(reply.url, fetcher)

    structure = describe_autodetect(reply.text, reply.url)
    return {
        "url": reply.url,
        "site_title": site_title,
        "existing_feeds": feeds,
        "structure": structure,
        "suggested_id": slugify(reply.url),
        "html": reply.text,
    }
