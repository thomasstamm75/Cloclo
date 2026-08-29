"""Détection des entrées d'une page web quelconque.

Quatre stratégies, de la plus fiable à la plus générique :

1. `selectors` — sélecteurs CSS fournis par l'utilisateur ou par une recette ;
2. `feed`      — la page *est* déjà un flux RSS/Atom, on le normalise ;
3. `jsonld`    — données structurées schema.org (`ItemList`, `Article`...) ;
4. `heuristic` — repérage des fratries répétées contenant un lien titré.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from xml.etree import ElementTree

from . import dom
from .dates import parse_date

TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|fbclid|gclid|mc_[ce]id|igshid|s_cid|ref_?src|ref|xtor|at_medium"
    r"|at_campaign|__twitter_impression|spm|cmpid|ncid)$",
    re.IGNORECASE,
)

# Conteneurs qui n'abritent presque jamais la liste principale.
CHROME_TAGS = frozenset(("nav", "header", "footer", "aside", "form"))
CHROME_WORDS = re.compile(
    r"nav|menu|footer|header|sidebar|widget|related|comment|breadcrumb|pagination"
    r"|social|share|newsletter|cookie|banner|advert|pub-|promo|tag-cloud|subnav"
    r"|topbar|masthead|skip|search",
    re.IGNORECASE,
)
CONTENT_WORDS = re.compile(
    r"article|post|entry|item|card|story|news|result|teaser|feed|list|blog|media"
    r"|content|stream|chronique|actu|billet|publication|node",
    re.IGNORECASE,
)
# Liens de navigation à écarter (pages d'index, comptes, réseaux sociaux).
NON_ARTICLE_PATH = re.compile(
    r"/(tag|tags|category|categories|categorie|rubrique|auteur|author|page|search"
    r"|recherche|login|connexion|inscription|abonnement|subscribe|cgu|mentions"
    r"|contact|newsletter|rss|feed|panier|compte)(/|$|\?)",
    re.IGNORECASE,
)
SOCIAL_HOSTS = frozenset(
    (
        "facebook.com",
        "www.facebook.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "www.linkedin.com",
        "instagram.com",
        "youtube.com",
        "www.youtube.com",
        "t.me",
        "whatsapp.com",
        "bsky.app",
        "mastodon.social",
        "pinterest.com",
        "reddit.com",
    )
)

PAYWALL_WORDS = re.compile(
    r"paywall|premium|abonn[ée]|subscriber|subscription|reserve-?aux|locked|paid-?only"
    r"|member-?only|restricted|payant|metered|gated",
    re.IGNORECASE,
)
PAYWALL_LABELS = re.compile(
    r"^\s*(?:🔒|🔓)?\s*(article\s+)?(?:r[ée]serv[ée]\s+aux\s+abonn[ée]s|abonn[ée]s?"
    r"|premium|payant|exclusif\s+abonn[ée]s|subscribers?\s+only|for\s+subscribers"
    r"|paid|members?\s+only)\s*$",
    re.IGNORECASE,
)

SUMMARY_HINTS = (
    ".chapo",
    ".excerpt",
    ".summary",
    ".description",
    ".teaser",
    ".standfirst",
    ".resume",
    ".intro",
    ".lead",
    ".subtitle",
    ".sous-titre",
    "[itemprop=description]",
)
AUTHOR_HINTS = (
    "[rel=author]",
    "[itemprop=author]",
    ".author",
    ".byline",
    ".signature",
    ".auteur",
)
DATE_HINTS = (
    "time[datetime]",
    "time",
    "[datetime]",
    "[itemprop=datePublished]",
    ".date",
    ".published",
    ".timestamp",
    ".time",
)

FEED_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/json",
)


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


@dataclass
class Item:
    title: str = ""
    link: str = ""
    guid: str = ""
    date: datetime | None = None
    author: str = ""
    summary: str = ""
    content_html: str = ""
    image: str = ""
    categories: list = field(default_factory=list)
    free: bool = True

    def as_dict(self):
        from .dates import to_rfc3339

        return {
            "title": self.title,
            "link": self.link,
            "guid": self.guid or self.link,
            "date": to_rfc3339(self.date),
            "author": self.author,
            "summary": self.summary,
            "image": self.image,
            "categories": list(self.categories),
            "free": self.free,
        }


@dataclass
class PageInfo:
    url: str = ""
    title: str = ""
    description: str = ""
    site_name: str = ""
    language: str = ""
    icon: str = ""
    feeds: list = field(default_factory=list)


@dataclass
class Options:
    """Réglages d'extraction. Tous les sélecteurs acceptent le suffixe `@attr`."""

    item: str = ""
    title: str = ""
    link: str = ""
    date: str = ""
    summary: str = ""
    author: str = ""
    image: str = ""
    category: str = ""
    limit: int = 30
    min_items: int = 3
    include_paid: bool = False
    strategy: str = "auto"  # auto | selectors | jsonld | heuristic | feed


@dataclass
class Extraction:
    items: list
    strategy: str
    page: PageInfo
    selectors: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilitaires d'URL et de texte
# ---------------------------------------------------------------------------


def clean_url(href, base):
    """Absolutise une URL et retire les paramètres de traçage."""
    if not href:
        return ""
    href = href.strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
        return ""
    absolute = urllib.parse.urljoin(base, href)
    parts = urllib.parse.urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return ""
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING_PARAMS.match(k)
    ]
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urllib.parse.urlencode(query),
            "",  # le fragment ne distingue pas deux articles
        )
    )


def squeeze(text, limit=None):
    text = re.sub(r"\s+", " ", text or "").strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        return cut + "…"
    return text


def split_field(selector):
    """`"h2 a@href"` -> `("h2 a", "href")`. Sans suffixe, l'attribut est `text`."""
    if not selector:
        return "", "text"
    if "@" in selector:
        css, _, attr = selector.rpartition("@")
        return css.strip(), (attr.strip() or "text")
    return selector.strip(), "text"


def node_value(node, attr):
    if node is None:
        return ""
    if attr == "text":
        return squeeze(node.text())
    if attr == "html":
        return node.html()
    return squeeze(node.get(attr))


def pick(root, selector, base=None):
    """Applique un sélecteur `css@attr` sur un nœud et renvoie une chaîne."""
    css, attr = split_field(selector)
    node = root.select_one(css) if css else root
    value = node_value(node, attr)
    if base and attr in ("href", "src", "data-src", "content"):
        value = clean_url(value, base) or value
    return value


# ---------------------------------------------------------------------------
# Métadonnées de page
# ---------------------------------------------------------------------------


def meta_content(doc, *names):
    wanted = {n.lower() for n in names}
    for node in doc.find_all("meta"):
        key = (node.get("property") or node.get("name") or node.get("itemprop")).lower()
        if key in wanted:
            value = squeeze(node.get("content"))
            if value:
                return value
    return ""


def page_info(doc, url):
    html_node = doc.find("html")
    title_node = doc.find("title")
    info = PageInfo(
        url=url,
        title=meta_content(doc, "og:title") or squeeze(title_node.text() if title_node else ""),
        description=meta_content(doc, "og:description", "description"),
        site_name=meta_content(doc, "og:site_name"),
        language=(html_node.get("lang") if html_node else "") or meta_content(doc, "og:locale"),
        feeds=discover_feeds(doc, url),
    )
    icon = ""
    for link in doc.find_all("link"):
        rel = link.get("rel", "").lower()
        if "icon" in rel:
            icon = clean_url(link.get("href"), url)
            if "apple" not in rel:
                break
    info.icon = icon or clean_url("/favicon.ico", url)
    return info


def discover_feeds(doc, url):
    """Flux déjà publiés par la page (`<link rel="alternate">`)."""
    found, seen = [], set()
    for link in doc.find_all("link"):
        rel = link.get("rel", "").lower()
        mime = link.get("type", "").lower()
        if "alternate" not in rel or mime not in FEED_TYPES:
            continue
        href = clean_url(link.get("href"), url)
        if href and href not in seen:
            seen.add(href)
            found.append(
                {"url": href, "type": mime, "title": squeeze(link.get("title"))}
            )
    for anchor in doc.find_all("a"):
        href = anchor.get("href", "")
        if re.search(r"(^|/)(rss|feed|atom)(\.xml|/?$)", href, re.IGNORECASE):
            absolute = clean_url(href, url)
            if absolute and absolute not in seen:
                seen.add(absolute)
                found.append(
                    {"url": absolute, "type": "", "title": squeeze(anchor.text())}
                )
    return found


ARTICLE_TYPES = frozenset(
    (
        "article",
        "newsarticle",
        "blogposting",
        "report",
        "techarticle",
        "scholarlyarticle",
        "liveblogposting",
        "socialmediaposting",
        "videoobject",
        "podcastepisode",
        "creativework",
        "webpage",
        "product",
        "event",
        "jobposting",
        "recipe",
    )
)


# ---------------------------------------------------------------------------
# Contenu gratuit / payant
# ---------------------------------------------------------------------------


def page_is_free(doc):
    """Lit les signaux *de page* indiquant un accès restreint.

    Seuls les objets JSON-LD de premier niveau comptent : dans une liste
    d'articles, une entrée payante ne rend pas la page entière payante.
    """
    tier = meta_content(doc, "article:content_tier", "article:content-tier")
    if tier and tier.lower() in ("locked", "metered", "premium", "paid"):
        return False
    denied = (False, "False", "false")
    page_types = ARTICLE_TYPES | {"webpage", "itempage", "collectionpage"}
    for root in _jsonld_roots(doc):
        if not _types_of(root) & page_types:
            continue
        if root.get("isAccessibleForFree") in denied:
            return False
        parts = root.get("hasPart")
        parts = parts if isinstance(parts, list) else [parts]
        for part in parts:
            if isinstance(part, dict) and part.get("isAccessibleForFree") in denied:
                return False
    return True


def item_is_free(node):
    """Vrai si rien, dans le bloc, ne signale un contenu réservé."""
    if node is None:
        return True
    for element in [node] + list(node.elements()):
        haystack = " ".join(
            (
                element.get("class"),
                element.get("data-premium"),
                element.get("data-paywall"),
                element.get("data-restricted"),
                element.get("aria-label"),
                element.get("title") if element.tag != "a" else "",
            )
        )
        if PAYWALL_WORDS.search(haystack):
            return False
        if element.tag in ("span", "small", "em", "strong", "i", "b", "div", "p"):
            if PAYWALL_LABELS.match(element.text()):
                return False
    if "🔒" in node.text():
        return False
    return True


# ---------------------------------------------------------------------------
# Stratégie « feed » : la page est déjà un flux
# ---------------------------------------------------------------------------


def looks_like_feed(text, content_type=""):
    if any(t in (content_type or "") for t in ("rss", "atom", "xml")):
        head = text[:2000].lower()
        if "<rss" in head or "<feed" in head or "<rdf:rdf" in head:
            return True
    head = text[:2000].lower().lstrip()
    return head.startswith(("<?xml", "<rss", "<feed")) and (
        "<rss" in head or "<feed" in head or "<rdf:rdf" in head
    )


def _tag(element):
    return element.tag.rsplit("}", 1)[-1].lower()


def parse_feed(text, url):
    """Normalise un flux RSS 2.0 / RDF / Atom existant."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8", errors="replace"))
    except ElementTree.ParseError:
        return [], PageInfo(url=url)

    def first(parent, *names):
        wanted = {n.lower() for n in names}
        for child in parent:
            if _tag(child) in wanted:
                return child
        return None

    def text_of(parent, *names):
        node = first(parent, *names)
        return squeeze("".join(node.itertext())) if node is not None else ""

    channel = first(root, "channel") or root
    info = PageInfo(
        url=url,
        title=text_of(channel, "title"),
        description=text_of(channel, "description", "subtitle"),
    )
    entries = []
    for element in root.iter():
        if _tag(element) in ("item", "entry"):
            entries.append(element)

    items = []
    for entry in entries:
        link = ""
        for child in entry:
            if _tag(child) == "link":
                rel = child.get("rel", "alternate")
                if rel == "alternate" or not link:
                    link = child.get("href") or squeeze("".join(child.itertext()))
                if rel == "alternate" and link:
                    break
        content = first(entry, "encoded", "content", "description", "summary")
        items.append(
            Item(
                title=text_of(entry, "title"),
                link=clean_url(link, url),
                guid=text_of(entry, "guid", "id") or clean_url(link, url),
                date=parse_date(
                    text_of(entry, "pubdate", "published", "updated", "date")
                ),
                author=text_of(entry, "creator", "author", "name"),
                summary=squeeze(text_of(entry, "description", "summary"), 600),
                content_html="".join(content.itertext()) if content is not None else "",
            )
        )
    return items, info


# ---------------------------------------------------------------------------
# Stratégie « jsonld »
# ---------------------------------------------------------------------------

def _jsonld_payloads(doc):
    """Contenu JSON de chaque `<script type="application/ld+json">`."""
    payloads = []
    for script in doc.find_all("script"):
        if "ld+json" not in script.get("type", "").lower():
            continue
        raw = script.raw_text().strip()
        if not raw:
            continue
        raw = re.sub(r"^\s*//.*$", "", raw, flags=re.MULTILINE)
        try:
            payloads.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return payloads


def _jsonld_roots(doc):
    """Objets de premier niveau (`@graph` déplié) : ils décrivent la page."""
    roots = []
    for payload in _jsonld_payloads(doc):
        queue = payload if isinstance(payload, list) else [payload]
        for entry in queue:
            if not isinstance(entry, dict):
                continue
            graph = entry.get("@graph")
            if isinstance(graph, list):
                roots.extend(g for g in graph if isinstance(g, dict))
            else:
                roots.append(entry)
    return roots


def _jsonld_objects(doc):
    """Tous les objets JSON-LD, dans l'ordre du document."""
    out = []
    queue = list(_jsonld_roots(doc))
    while queue:
        current = queue.pop(0)
        if isinstance(current, list):
            queue = list(current) + queue
            continue
        if not isinstance(current, dict):
            continue
        out.append(current)
        children = []
        for key in ("itemListElement", "hasPart", "blogPost", "item", "mainEntity"):
            value = current.get(key)
            if isinstance(value, (list, dict)):
                children.append(value)
        queue = children + queue
    return out


def _types_of(obj):
    raw = obj.get("@type") or obj.get("type") or ""
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).lower() for v in values}


def _first_str(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for entry in value:
            found = _first_str(entry)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ("name", "url", "@id", "contentUrl", "headline"):
            if key in value:
                return _first_str(value[key])
    return ""


def jsonld_items(doc, base):
    items, seen = [], set()
    for obj in _jsonld_objects(doc):
        types = _types_of(obj)
        if not types & ARTICLE_TYPES:
            continue
        url = clean_url(_first_str(obj.get("url") or obj.get("@id")), base)
        title = squeeze(_first_str(obj.get("headline") or obj.get("name")))
        if not title or not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        free = obj.get("isAccessibleForFree") not in (False, "False", "false")
        categories = obj.get("keywords") or obj.get("articleSection") or []
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(",") if c.strip()]
        elif not isinstance(categories, list):
            categories = []
        items.append(
            Item(
                title=title,
                link=url,
                guid=url,
                date=parse_date(
                    obj.get("datePublished")
                    or obj.get("dateCreated")
                    or obj.get("uploadDate")
                    or obj.get("dateModified")
                ),
                author=squeeze(_first_str(obj.get("author"))),
                summary=squeeze(
                    _first_str(obj.get("description") or obj.get("abstract")), 600
                ),
                image=clean_url(_first_str(obj.get("image")), base),
                categories=[squeeze(str(c)) for c in categories][:6],
                free=free,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Stratégie « heuristic »
# ---------------------------------------------------------------------------


def _anchors(node, base, page_url):
    """Liens candidats d'un bloc, du plus au moins probable."""
    found = []
    for anchor in node.find_all("a"):
        href = clean_url(anchor.get("href"), base)
        if not href or href == page_url:
            continue
        host = urllib.parse.urlsplit(href).hostname or ""
        if host in SOCIAL_HOSTS:
            continue
        text = squeeze(anchor.text()) or squeeze(anchor.get("aria-label")) or squeeze(
            anchor.get("title")
        )
        if not text:
            image = anchor.find("img")
            if image is not None:
                text = squeeze(image.get("alt"))
        found.append((anchor, href, text))
    return found


def _best_anchor(node, base, page_url):
    candidates = _anchors(node, base, page_url)
    if not candidates:
        return None, "", ""
    heading_tags = ("h1", "h2", "h3", "h4", "h5", "h6")

    def score(entry):
        anchor, href, text = entry
        value = min(len(text), 120) / 10.0
        if anchor.has_ancestor(*heading_tags) or anchor.find(*heading_tags):
            value += 12
        if NON_ARTICLE_PATH.search(urllib.parse.urlsplit(href).path):
            value -= 8
        path = urllib.parse.urlsplit(href).path.strip("/")
        if not path:
            value -= 10
        value += min(path.count("-"), 8) * 0.4  # les URL d'articles sont bavardes
        if anchor.get("rel", "").lower() in ("tag", "category", "nofollow"):
            value -= 4
        return value

    anchor, href, text = max(candidates, key=score)
    return anchor, href, text


def _extract_title(node, anchor, anchor_text):
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        heading = node.find(tag)
        if heading is not None:
            text = squeeze(heading.text())
            if len(text) >= 8:
                return text
    for selector in ("[itemprop=headline]", ".title", ".titre", ".headline"):
        found = node.select_one(selector)
        if found is not None:
            text = squeeze(found.text())
            if len(text) >= 8:
                return text
    if anchor_text:
        return anchor_text
    if anchor is not None:
        image = anchor.find("img")
        if image is not None and image.get("alt"):
            return squeeze(image.get("alt"))
    return squeeze(node.text(), 140)


def _extract_date(node):
    for selector in DATE_HINTS:
        for found in node.select(selector):
            value = (
                found.get("datetime")
                or found.get("content")
                or found.get("data-date")
                or found.text()
            )
            parsed = parse_date(value)
            if parsed:
                return parsed
    for meta in node.find_all("meta"):
        if "date" in (meta.get("itemprop") or meta.get("property") or "").lower():
            parsed = parse_date(meta.get("content"))
            if parsed:
                return parsed
    return parse_date(squeeze(node.text(), 400))


def _extract_image(node, base):
    for image in node.find_all("img"):
        for attribute in ("src", "data-src", "data-original", "data-lazy-src"):
            url = clean_url(image.get(attribute), base)
            if url:
                return url
        srcset = image.get("srcset") or image.get("data-srcset")
        if srcset:
            first = srcset.split(",")[0].strip().split(" ")[0]
            url = clean_url(first, base)
            if url:
                return url
    for source in node.find_all("source"):
        srcset = source.get("srcset", "")
        if srcset:
            url = clean_url(srcset.split(",")[0].strip().split(" ")[0], base)
            if url:
                return url
    for element in [node] + list(node.elements()):
        style = element.get("style", "")
        match = re.search(r"url\((['\"]?)(.*?)\1\)", style)
        if match:
            url = clean_url(match.group(2), base)
            if url:
                return url
    return ""


def _extract_summary(node, title):
    for selector in SUMMARY_HINTS:
        found = node.select_one(selector)
        if found is not None:
            text = squeeze(found.text(), 600)
            if len(text) >= 30 and text != title:
                return text
    best = ""
    for paragraph in node.find_all("p"):
        text = squeeze(paragraph.text())
        if text == title or len(text) < 30:
            continue
        if len(text) > len(best):
            best = text
    if best:
        return squeeze(best, 600)
    # Repli : le texte du bloc, débarrassé du titre. S'il ne reste presque
    # rien, c'est que le bloc ne porte pas de résumé.
    text = squeeze(node.text())
    if title:
        text = text.replace(title, " ")
    text = re.sub(r"^[\s\d/.:–—\-·|]+", "", squeeze(text))
    return squeeze(text, 400) if len(text) >= 40 else ""


def _extract_author(node):
    for selector in AUTHOR_HINTS:
        found = node.select_one(selector)
        if found is not None:
            text = squeeze(found.text(), 120)
            if text:
                return re.sub(r"^(par|by)\s+", "", text, flags=re.IGNORECASE)
    return ""


def _extract_categories(node):
    out = []
    for selector in ("[rel=tag]", ".tag", ".category", ".rubrique", "[itemprop=articleSection]"):
        for found in node.select(selector):
            text = squeeze(found.text(), 40)
            if text and text.lower() not in {c.lower() for c in out}:
                out.append(text)
    return out[:6]


def item_from_node(node, base, page_url, options=None):
    options = options or Options()
    anchor, href, anchor_text = (None, "", "")
    if options.link:
        href = pick(node, options.link, base)
    if not href:
        anchor, href, anchor_text = _best_anchor(node, base, page_url)
    if not href:
        return None

    title = pick(node, options.title, base) if options.title else ""
    if not title:
        title = _extract_title(node, anchor, anchor_text)
    title = squeeze(title, 300)
    if len(title) < 3:
        return None

    date = None
    if options.date:
        date = parse_date(pick(node, options.date, base))
    if date is None:
        date = _extract_date(node)

    summary = pick(node, options.summary, base) if options.summary else ""
    if not summary:
        summary = _extract_summary(node, title)

    author = pick(node, options.author, base) if options.author else _extract_author(node)
    image = pick(node, options.image, base) if options.image else _extract_image(node, base)
    categories = (
        [squeeze(n.text(), 40) for n in node.select(split_field(options.category)[0])]
        if options.category
        else _extract_categories(node)
    )

    return Item(
        title=title,
        link=href,
        guid=href,
        date=date,
        author=author,
        summary=summary,
        image=image,
        categories=[c for c in categories if c][:6],
        free=item_is_free(node),
    )


class _Analyzer:
    """Mémorise les analyses coûteuses (lien principal, date) par nœud."""

    def __init__(self, base, page_url):
        self.base = base
        self.page_url = page_url
        self._anchors = {}
        self._dates = {}

    def anchor(self, node):
        key = id(node)
        if key not in self._anchors:
            self._anchors[key] = _best_anchor(node, self.base, self.page_url)
        return self._anchors[key]

    def date(self, node):
        key = id(node)
        if key not in self._dates:
            self._dates[key] = _extract_date(node)
        return self._dates[key]


def _group_score(parent, members, analyzer):
    """Note un groupe de blocs candidats ; plus c'est haut, mieux c'est."""
    count = len(members)
    linked, titled, dated, hrefs, lengths = 0, 0, 0, set(), []
    for member in members:
        _, href, text = analyzer.anchor(member)
        if href:
            linked += 1
            hrefs.add(href)
            if len(text) >= 12 or member.find("h1", "h2", "h3", "h4", "h5", "h6"):
                titled += 1
        if analyzer.date(member):
            dated += 1
        lengths.append(member.text_length())

    if linked < 2:
        return -1e9
    score = 12.0 * math.log(count + 1)
    score += 14.0 * (linked / count)
    score += 16.0 * (titled / count)
    score += 6.0 * (len(hrefs) / max(linked, 1))
    score += 5.0 * (dated / count)

    average = sum(lengths) / count
    if 30 <= average <= 1200:
        score += 8.0
    elif average < 15:
        score -= 12.0
    elif average > 4000:
        score -= 6.0

    sample = members[0]
    identity = " ".join(
        filter(None, (parent.get("class"), parent.get("id"), sample.get("class")))
    )
    if CONTENT_WORDS.search(identity):
        score += 8.0
    if CHROME_WORDS.search(identity):
        score -= 14.0
    if parent.tag in CHROME_TAGS or parent.has_ancestor(*CHROME_TAGS):
        score -= 20.0
    if sample.tag == "article":
        score += 10.0
    elif sample.tag in ("li", "div", "section"):
        score += 2.0

    navish = sum(
        1
        for member in members
        if NON_ARTICLE_PATH.search(
            urllib.parse.urlsplit(analyzer.anchor(member)[1]).path
        )
    )
    score -= 12.0 * (navish / count)
    score += min(parent.depth(), 12) * 0.3
    return score


SKIP_TAGS = frozenset(("script", "style", "noscript", "template", "br", "hr", "svg"))


def _cluster(children):
    """Regroupe des fratries : par balise, puis par balise + classes.

    Le double regroupement évite qu'une classe supplémentaire sur un seul bloc
    (`article.post.premium` au milieu de `article.post`) casse la série.
    """
    by_tag, by_signature = {}, {}
    for child in children:
        if child.tag in SKIP_TAGS:
            continue
        by_tag.setdefault(child.tag, []).append(child)
        by_signature.setdefault(child.signature(), []).append(child)
    groups, seen = [], set()
    for members in list(by_tag.values()) + list(by_signature.values()):
        key = tuple(sorted(id(m) for m in members))
        if key in seen:
            continue
        seen.add(key)
        groups.append(members)
    return groups


def heuristic_candidates(doc, base, page_url, min_items=3):
    """Groupes de fratries homogènes, triés par pertinence décroissante."""
    analyzer = _Analyzer(base, page_url)
    candidates = []
    body = doc.find("body") or doc

    for parent in [body] + list(body.elements()):
        children = parent.child_elements()
        if not min_items <= len(children) <= 500:
            continue
        for members in _cluster(children):
            if len(members) < min_items:
                continue
            if sum(1 for m in members if m.find("a")) < 2:
                continue
            score = _group_score(parent, members, analyzer)
            if score > 0:
                candidates.append(
                    {
                        "score": score,
                        "parent": parent,
                        "members": members,
                        "selector": _selector_for(parent, members[0]),
                    }
                )

    # Repli : blocs de même signature dispersés dans la page.
    if not candidates:
        by_signature = {}
        for element in body.elements():
            if element.tag in SKIP_TAGS or element.tag in ("a", "img", "span"):
                continue
            if not element.classes and element.tag != "article":
                continue
            by_signature.setdefault(element.signature(), []).append(element)
        for members in by_signature.values():
            members = [m for m in members if not m.has_ancestor(*CHROME_TAGS)]
            if len(members) < min_items:
                continue
            parent = members[0].parent or body
            score = _group_score(parent, members, analyzer)
            if score > 0:
                candidates.append(
                    {
                        "score": score,
                        "parent": parent,
                        "members": members,
                        "selector": _selector_for(parent, members[0]),
                    }
                )

    def links_of(candidate):
        return frozenset(
            analyzer.anchor(m)[1] for m in candidate["members"] if analyzer.anchor(m)[1]
        )

    # Une même liste est souvent détectée à plusieurs niveaux d'emboîtement
    # (le conteneur et le bloc qu'il enveloppe). À liens identiques, on garde
    # le plus profond : son sélecteur est le plus précis.
    grouped = {}
    for index, candidate in enumerate(candidates):
        links = links_of(candidate)
        key = links or f"#{index}"
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = candidate
        elif candidate["members"][0].depth() > previous["members"][0].depth():
            candidate["score"] = max(candidate["score"], previous["score"])
            grouped[key] = candidate
        else:
            previous["score"] = max(previous["score"], candidate["score"])

    ranked = sorted(grouped.values(), key=lambda c: c["score"], reverse=True)

    # Une série incluse dans une autre déjà retenue n'apporte rien.
    unique, seen_links = [], []
    for candidate in ranked:
        links = links_of(candidate)
        if any(links and links <= previous for previous in seen_links):
            continue
        seen_links.append(links)
        unique.append(candidate)
    return unique


def _selector_for(parent, member):
    """Sélecteur CSS lisible, réutilisable tel quel avec `--item`."""
    def part(node):
        if node.get("id"):
            return f"#{node.get('id')}"
        classes = [c for c in sorted(node.classes) if not re.fullmatch(r"[\w-]*\d{3,}[\w-]*", c)]
        if classes:
            return node.tag + "".join(f".{c}" for c in classes[:2])
        return node.tag

    child = part(member)
    if child.startswith("#") or "." in child:
        return child
    return f"{part(parent)} > {child}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _dedupe(items, limit):
    seen, out = set(), []
    for item in items:
        key = item.link or item.guid or item.title
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def extract(html, url, options=None, content_type=""):
    """Extrait les entrées d'une page et renvoie une `Extraction`."""
    options = options or Options()

    if options.strategy in ("auto", "feed") and looks_like_feed(html, content_type):
        items, info = parse_feed(html, url)
        if items:
            info.url = url
            return Extraction(_dedupe(items, options.limit), "feed", info)

    doc = dom.parse(html)
    base_node = doc.find("base")
    base = clean_url(base_node.get("href"), url) if base_node is not None else url
    base = base or url
    info = page_info(doc, url)
    free_page = page_is_free(doc)

    def finish(items, strategy, selectors=None, candidates=None):
        for item in items:
            item.free = item.free and free_page
        if not options.include_paid:
            items = [i for i in items if i.free]
        return Extraction(
            _dedupe(items, options.limit),
            strategy,
            info,
            selectors or {},
            candidates or [],
        )

    if options.item:
        nodes = doc.select(split_field(options.item)[0])
        items = [item_from_node(n, base, url, options) for n in nodes]
        return finish(
            [i for i in items if i],
            "selectors",
            {"item": options.item, "title": options.title, "link": options.link},
        )

    if options.strategy in ("auto", "jsonld"):
        items = jsonld_items(doc, base)
        if len(items) >= 2 or options.strategy == "jsonld":
            if items:
                return finish(items, "jsonld")

    candidates = heuristic_candidates(doc, base, url, options.min_items)
    for candidate in candidates[:3]:
        items = [
            item_from_node(member, base, url, options) for member in candidate["members"]
        ]
        items = [i for i in items if i]
        if len(items) >= min(options.min_items, 2):
            return finish(
                items,
                "heuristic",
                {"item": candidate["selector"], "score": round(candidate["score"], 1)},
                [
                    {
                        "selector": c["selector"],
                        "score": round(c["score"], 1),
                        "count": len(c["members"]),
                    }
                    for c in candidates[:8]
                ],
            )

    # Dernier recours : les `<article>`, puis les liens titrés de la page.
    articles = doc.find_all("article")
    if len(articles) >= 2:
        items = [item_from_node(a, base, url, options) for a in articles]
        return finish([i for i in items if i], "heuristic", {"item": "article"})

    # Dernier recours : des titres liés, en nombre suffisant pour ressembler à
    # une liste. Sous ce seuil, mieux vaut un flux vide qu'un flux trompeur.
    items = []
    for heading in doc.find_all("h1", "h2", "h3"):
        holder = heading.parent or heading
        if holder.has_ancestor(*CHROME_TAGS):
            continue
        identity = f"{holder.get('class')} {holder.get('id')}"
        if CHROME_WORDS.search(identity):
            continue
        item = item_from_node(holder, base, url, options)
        if item:
            items.append(item)
    if len(items) >= max(3, options.min_items):
        return finish(items, "headings", {"item": "h1, h2, h3"})
    return finish([], "empty")
