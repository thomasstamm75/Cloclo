"""Extraction des articles d'une page HTML.

Trois stratégies, essayées dans cet ordre :
  1. les sélecteurs CSS fournis dans la configuration ;
  2. les données structurées JSON-LD (schema.org) si la page en publie ;
  3. la détection automatique des blocs répétés.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from .dates import parse_date
from .util import absolute_url, canonical_url, clean_text, digest, truncate

# Blocs qui ne contiennent jamais la liste d'articles : les retirer évite de
# confondre un menu de navigation avec une liste de publications.
NOISE_SELECTORS = (
    "script", "style", "noscript", "svg", "form", "iframe", "template",
    "nav", "header", "footer", "aside",
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[class*="menu"]', '[class*="nav-"]', '[class*="breadcrumb"]',
    '[class*="cookie"]', '[class*="newsletter"]', '[class*="sidebar"]',
    '[class*="footer"]', '[class*="header"]', '[id*="comment"]',
)

TITLE_SELECTORS = ("h1", "h2", "h3", "h4", "h5",
                   '[class*="title"]', '[class*="titre"]', '[class*="heading"]')
DATE_SELECTORS = ("time", "[datetime]", '[class*="date"]', '[class*="publi"]',
                  '[class*="time"]', "[pubdate]")
SUMMARY_SELECTORS = ('[class*="excerpt"]', '[class*="chapo"]', '[class*="summary"]',
                     '[class*="resume"]', '[class*="description"]', '[class*="teaser"]',
                     '[class*="intro"]', "p")
AUTHOR_SELECTORS = ('[rel="author"]', '[class*="author"]', '[class*="auteur"]',
                    '[class*="byline"]', '[itemprop="author"]')

CONTENT_SELECTORS = ("article", '[itemprop="articleBody"]', '[class*="article-body"]',
                     '[class*="post-content"]', '[class*="entry-content"]',
                     '[class*="content"]', "main")

MIN_TITLE_LENGTH = 12
MIN_GROUP_SIZE = 3
MAX_ANCESTOR_DEPTH = 8

_CLASS_NOISE = re.compile(r"[-_]?\d+")
_JSONLD_TYPES = {"article", "newsarticle", "blogposting", "report",
                 "techarticle", "webpage", "creativework", "podcastepisode",
                 "videoobject", "socialmediaposting"}


@dataclass
class Article:
    title: str
    link: str
    summary: str = ""
    published: datetime | None = None
    author: str = ""
    image: str = ""
    content_html: str = ""
    guid: str = field(default="")

    def __post_init__(self) -> None:
        if not self.guid:
            self.guid = canonical_url(self.link) or f"urn:rssgen:{digest(self.title)}"


@dataclass
class Rules:
    """Sélecteurs CSS d'un flux. Tous facultatifs : vides = détection auto."""
    item: str = ""
    title: str = ""
    link: str = ""
    date: str = ""
    summary: str = ""
    author: str = ""
    image: str = ""
    content: str = ""
    date_attr: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.item


def make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml absent ou HTML trop abîmé
        return BeautifulSoup(html, "html.parser")


def extract_articles(html: str, base_url: str, rules: Rules | None = None,
                     limit: int = 30) -> list[Article]:
    """Retourne les articles trouvés dans la page, du plus récent au plus ancien."""
    soup = make_soup(html)
    rules = rules or Rules()

    if not rules.is_empty:
        articles = _from_selectors(soup, base_url, rules)
    else:
        articles = _from_jsonld(soup, base_url)
        if len(articles) < MIN_GROUP_SIZE:
            auto = _from_autodetect(soup, base_url)
            if len(auto) > len(articles):
                articles = auto

    return _deduplicate(articles)[:limit]


# --------------------------------------------------------------- sélecteurs
def _from_selectors(soup: BeautifulSoup, base_url: str, rules: Rules) -> list[Article]:
    articles = []
    for node in soup.select(rules.item):
        article = _build_article(node, base_url, rules)
        if article:
            articles.append(article)
    return articles


def _build_article(node: Tag, base_url: str, rules: Rules) -> Article | None:
    link_node = _pick(node, rules.link) if rules.link else None
    href = link_node.get("href") if isinstance(link_node, Tag) else None
    if not href:
        anchor = node.find("a", href=True)
        # Un bloc peut être lui-même le lien (<a class="card">…</a>).
        if anchor is None and node.name == "a":
            anchor = node
        href = anchor.get("href") if anchor else None
    link = absolute_url(href, base_url)
    if not link:
        return None

    title = _text_of(_pick(node, rules.title)) if rules.title else ""
    if not title:
        title = _guess_title(node)
    if len(title) < 3:
        return None

    published = _guess_date(node, rules)
    summary = _text_of(_pick(node, rules.summary)) if rules.summary else ""
    if not summary:
        summary = _guess_summary(node, title)
    author = _text_of(_pick(node, rules.author)) if rules.author else _guess_author(node)
    image = _guess_image(node, base_url, rules.image)

    content_html = ""
    if rules.content:
        content_node = _pick(node, rules.content)
        if isinstance(content_node, Tag):
            content_html = content_node.decode_contents()

    return Article(title=title, link=link, summary=truncate(summary),
                   published=published, author=author, image=image,
                   content_html=content_html)


def _pick(node: Tag, selector: str) -> Tag | None:
    if not selector:
        return None
    try:
        return node.select_one(selector)
    except Exception:
        return None


def _text_of(node: Tag | None) -> str:
    return clean_text(node.get_text(" ")) if isinstance(node, Tag) else ""


# ------------------------------------------------------------------ JSON-LD
def _from_jsonld(soup: BeautifulSoup, base_url: str) -> list[Article]:
    """Lit les données schema.org intégrées à la page, quand elles existent."""
    articles: list[Article] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for entry in _walk_jsonld(data):
            article = _article_from_jsonld(entry, base_url)
            if article:
                articles.append(article)
    return articles


def _walk_jsonld(data, depth: int = 0):
    """Parcourt les graphes et listes imbriqués de JSON-LD."""
    if depth > 6:
        return
    if isinstance(data, list):
        for item in data:
            yield from _walk_jsonld(item, depth + 1)
    elif isinstance(data, dict):
        for key in ("@graph", "itemListElement", "mainEntity", "hasPart", "blogPost"):
            if key in data:
                yield from _walk_jsonld(data[key], depth + 1)
        if "item" in data and isinstance(data["item"], (dict, list)):
            yield from _walk_jsonld(data["item"], depth + 1)
        yield data


def _article_from_jsonld(entry: dict, base_url: str) -> Article | None:
    types = entry.get("@type") or entry.get("type") or ""
    types = [types] if isinstance(types, str) else list(types)
    if not any(str(t).lower() in _JSONLD_TYPES for t in types):
        return None

    link = absolute_url(_jsonld_str(entry.get("url") or entry.get("@id")), base_url)
    title = clean_text(_jsonld_str(entry.get("headline") or entry.get("name")))
    if not link or not title:
        return None

    published = parse_date(_jsonld_str(
        entry.get("datePublished") or entry.get("dateCreated")
        or entry.get("uploadDate") or entry.get("dateModified")))
    author = entry.get("author")
    if isinstance(author, dict):
        author = author.get("name", "")
    elif isinstance(author, list) and author:
        first = author[0]
        author = first.get("name", "") if isinstance(first, dict) else str(first)

    image = entry.get("image")
    if isinstance(image, dict):
        image = image.get("url", "")
    elif isinstance(image, list) and image:
        image = image[0].get("url", "") if isinstance(image[0], dict) else str(image[0])

    return Article(
        title=title,
        link=link,
        summary=truncate(clean_text(_jsonld_str(
            entry.get("description") or entry.get("abstract")))),
        published=published,
        author=clean_text(str(author or "")),
        image=absolute_url(str(image or ""), base_url),
    )


def _jsonld_str(value) -> str:
    if isinstance(value, dict):
        return str(value.get("url") or value.get("@id") or value.get("name") or "")
    if isinstance(value, list) and value:
        return _jsonld_str(value[0])
    return str(value or "")


# ------------------------------------------------------ détection automatique
def _from_autodetect(soup: BeautifulSoup, base_url: str) -> list[Article]:
    """Repère la plus grande famille de liens partageant la même structure."""
    working = make_soup(str(soup))
    for selector in NOISE_SELECTORS:
        for node in working.select(selector):
            node.decompose()

    groups: dict[tuple, list[Tag]] = {}
    for anchor in working.find_all("a", href=True):
        if not _is_article_link(anchor, base_url):
            continue
        groups.setdefault(_signature(anchor), []).append(anchor)

    best_group = _best_group(groups)
    if not best_group:
        return []

    articles = []
    seen_containers: set[int] = set()
    for anchor in best_group:
        container = _item_container(anchor, best_group)
        if id(container) in seen_containers:
            continue
        seen_containers.add(id(container))
        article = _build_article(container, base_url, Rules())
        if article:
            articles.append(article)
    return articles


def _is_article_link(anchor: Tag, base_url: str) -> bool:
    href = anchor.get("href", "")
    if not absolute_url(href, base_url):
        return False
    text = clean_text(anchor.get_text(" "))
    # Un lien d'article porte un titre ; les liens courts sont des menus,
    # sauf si le bloc contient un titre à côté d'une vignette.
    if len(text) >= MIN_TITLE_LENGTH:
        return True
    heading = anchor.find(TITLE_SELECTORS[:5])
    return heading is not None and len(clean_text(heading.get_text(" "))) >= MIN_TITLE_LENGTH


def _signature(anchor: Tag) -> tuple:
    """Chemin structurel du lien, insensible aux numéros et à la position."""
    parts = []
    node: Tag | None = anchor
    for _ in range(MAX_ANCESTOR_DEPTH):
        if node is None or node.name in ("body", "html", "[document]"):
            break
        classes = node.get("class") or []
        normalized = tuple(sorted(
            _CLASS_NOISE.sub("", str(c)) for c in classes
            if not str(c).startswith(("is-", "js-"))
        ))
        parts.append((node.name, normalized))
        node = node.parent
    return tuple(parts)


def _best_group(groups: dict[tuple, list[Tag]]) -> list[Tag]:
    """Choisit la famille de liens la plus crédible comme liste d'articles."""
    best: list[Tag] = []
    best_score = 0.0
    for anchors in groups.values():
        if len(anchors) < MIN_GROUP_SIZE:
            continue
        lengths = [len(clean_text(a.get_text(" "))) for a in anchors]
        average = sum(lengths) / len(lengths)
        distinct = len({clean_text(a.get_text(" ")) for a in anchors})
        # Beaucoup d'entrées distinctes, avec des textes de longueur d'un titre.
        score = distinct * min(average, 120) ** 0.5
        if score > best_score:
            best, best_score = anchors, score
    return best


def _item_container(anchor: Tag, group: list[Tag]) -> Tag:
    """Remonte jusqu'au bloc qui décrit un seul article de la famille."""
    others = {id(a) for a in group if a is not anchor}
    container: Tag = anchor
    node = anchor.parent
    for _ in range(MAX_ANCESTOR_DEPTH):
        if not isinstance(node, Tag) or node.name in ("body", "html", "[document]"):
            break
        if any(id(a) in others for a in node.find_all("a", href=True)):
            break  # ce parent regroupe plusieurs articles : on s'arrête avant
        container = node
        node = node.parent
    return container


# ------------------------------------------------------------ champs devinés
def _guess_title(node: Tag) -> str:
    for selector in TITLE_SELECTORS:
        candidate = _text_of(_pick(node, selector))
        if len(candidate) >= 3:
            return candidate
    anchor = node.find("a", href=True) or (node if node.name == "a" else None)
    if isinstance(anchor, Tag):
        for attribute in ("title", "aria-label"):
            candidate = clean_text(anchor.get(attribute, ""))
            if len(candidate) >= 3:
                return candidate
        candidate = clean_text(anchor.get_text(" "))
        if candidate:
            return candidate
    return truncate(clean_text(node.get_text(" ")), 120)


def _guess_date(node: Tag, rules: Rules) -> datetime | None:
    candidates: list[str] = []
    selectors = (rules.date,) if rules.date else DATE_SELECTORS
    for selector in selectors:
        for found in (node.select(selector) if selector else []):
            attribute = rules.date_attr or "datetime"
            candidates.append(found.get(attribute) or found.get("content")
                              or found.get("title") or "")
            candidates.append(clean_text(found.get_text(" ")))
    if not rules.date:
        candidates.append(node.get("data-date", ""))
    for candidate in candidates:
        parsed = parse_date(candidate)
        if parsed:
            return parsed
    return None


def _guess_summary(node: Tag, title: str) -> str:
    for selector in SUMMARY_SELECTORS:
        for found in node.select(selector):
            text = clean_text(found.get_text(" "))
            # Un résumé n'est pas la répétition du titre.
            if len(text) >= 40 and text[:40].lower() != title[:40].lower():
                return text
    return ""


def _guess_author(node: Tag) -> str:
    for selector in AUTHOR_SELECTORS:
        text = _text_of(_pick(node, selector))
        if 2 <= len(text) <= 80:
            return re.sub(r"^(par|by)\s+", "", text, flags=re.IGNORECASE)
    return ""


def _guess_image(node: Tag, base_url: str, selector: str = "") -> str:
    image = _pick(node, selector) if selector else node.find("img")
    if not isinstance(image, Tag):
        return ""
    source = (image.get("src") or image.get("data-src")
              or image.get("data-lazy-src") or "")
    if not source:
        srcset = image.get("srcset") or image.get("data-srcset") or ""
        source = srcset.split(",")[0].strip().split(" ")[0] if srcset else ""
    if source.startswith("data:"):
        return ""
    return absolute_url(source, base_url)


def _deduplicate(articles: list[Article]) -> list[Article]:
    """Fusionne les doublons en gardant la version la plus complète."""
    merged: dict[str, Article] = {}
    for article in articles:
        existing = merged.get(article.guid)
        if existing is None:
            merged[article.guid] = article
            continue
        if len(article.title) > len(existing.title):
            existing.title = article.title
        existing.summary = existing.summary or article.summary
        existing.published = existing.published or article.published
        existing.author = existing.author or article.author
        existing.image = existing.image or article.image
        existing.content_html = existing.content_html or article.content_html
    return list(merged.values())


def extract_full_content(html: str, base_url: str, selector: str = "") -> str:
    """Récupère le corps d'un article pour un flux en texte intégral."""
    soup = make_soup(html)
    for noise in ("script", "style", "noscript", "form", "iframe",
                  '[class*="share"]', '[class*="social"]', '[class*="related"]',
                  '[class*="newsletter"]', '[id*="comment"]'):
        for node in soup.select(noise):
            node.decompose()

    node = _pick(soup, selector) if selector else None
    if node is None:
        best_length = 0
        for candidate_selector in CONTENT_SELECTORS:
            for candidate in soup.select(candidate_selector):
                length = len(clean_text(candidate.get_text(" ")))
                if length > best_length:
                    node, best_length = candidate, length
    if node is None:
        return ""

    for anchor in node.find_all("a", href=True):
        anchor["href"] = absolute_url(anchor["href"], base_url) or anchor["href"]
    for image in node.find_all("img", src=True):
        image["src"] = absolute_url(image["src"], base_url) or image["src"]
    return node.decode_contents().strip()


def describe_autodetect(html: str, base_url: str) -> dict:
    """Explique ce que la détection automatique a trouvé.

    Sert à proposer des sélecteurs CSS que l'on peut figer dans feeds.yaml
    quand on veut un résultat stable dans le temps.
    """
    soup = make_soup(html)
    working = make_soup(str(soup))
    for selector in NOISE_SELECTORS:
        for node in working.select(selector):
            node.decompose()

    groups: dict[tuple, list[Tag]] = {}
    for anchor in working.find_all("a", href=True):
        if _is_article_link(anchor, base_url):
            groups.setdefault(_signature(anchor), []).append(anchor)

    best = _best_group(groups)
    if not best:
        return {"found": False, "count": 0, "item_selector": "", "sample": []}

    containers = []
    seen: set[int] = set()
    for anchor in best:
        container = _item_container(anchor, best)
        if id(container) not in seen:
            seen.add(id(container))
            containers.append(container)

    return {
        "found": True,
        "count": len(containers),
        "item_selector": _css_selector_for(containers[0]) if containers else "",
        "sample": [clean_text(c.get_text(" "))[:80] for c in containers[:3]],
    }


def _css_selector_for(node: Tag) -> str:
    """Sélecteur CSS lisible et raisonnablement stable pour un bloc d'article."""
    classes = [c for c in (node.get("class") or [])
               if not str(c).startswith(("is-", "js-")) and not str(c).isdigit()
               and not re.fullmatch(r"[a-z-]*\d+", str(c))]
    if classes:
        # Une seule classe suffit le plus souvent et résiste mieux aux
        # changements de thème qu'une longue chaîne de classes.
        return f"{node.name}.{classes[0]}"
    parent = node.parent
    if isinstance(parent, Tag):
        parent_classes = [c for c in (parent.get("class") or []) if not str(c).isdigit()]
        if parent_classes:
            return f"{parent.name}.{parent_classes[0]} > {node.name}"
    return node.name
