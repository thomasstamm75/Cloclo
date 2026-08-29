"""Extraction du corps d'un article (lisibilité), pour les flux en texte intégral."""

from __future__ import annotations

import re

from . import dom
from .dates import parse_date
from .extract import clean_url, meta_content, squeeze

STRIP_TAGS = frozenset(
    "script style noscript template nav aside footer header form iframe button"
    " select input label svg canvas video audio object embed".split()
)
STRIP_WORDS = re.compile(
    r"comment|share|social|related|sidebar|widget|newsletter|promo|advert|pub-"
    r"|cookie|banner|breadcrumb|pagination|menu|nav|footer|header|author-box"
    r"|tags?-list|meta|toolbar|subscribe|paywall|lire-aussi|a-lire|read-also",
    re.IGNORECASE,
)
KEEP_TAGS = frozenset(
    "p br h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code em strong b i a img"
    " figure figcaption table thead tbody tr td th sub sup hr span div".split()
)
KEEP_ATTRS = {"a": ("href",), "img": ("src", "alt")}

CONTENT_HINTS = (
    "article",
    '[itemprop="articleBody"]',
    ".article-body",
    ".article-content",
    ".post-content",
    ".entry-content",
    ".content-body",
    "main",
    "#content",
)


def _prune(node):
    """Supprime en place le mobilier de page."""
    for child in list(node.children):
        if child.tag in STRIP_TAGS or child.tag == "#comment":
            node.children.remove(child)
            continue
        if child.is_element:
            identity = f"{child.get('class')} {child.get('id')} {child.get('role')}"
            if STRIP_WORDS.search(identity) and child.text_length() < 800:
                node.children.remove(child)
                continue
            _prune(child)


def _score(node):
    """Densité de texte d'un bloc : longueur, ponctuation, part de liens."""
    text = node.text()
    length = len(text)
    if length < 60:
        return 0.0
    link_length = sum(len(a.text()) for a in node.find_all("a"))
    link_ratio = link_length / max(length, 1)
    if link_ratio > 0.5:
        return 0.0
    paragraphs = len(node.find_all("p"))
    value = length / 100.0
    value += text.count(",") * 0.4 + text.count(".") * 0.2
    value += paragraphs * 3.0
    value *= 1.0 - link_ratio
    identity = f"{node.get('class')} {node.get('id')}"
    if re.search(r"article|content|post|entry|story|texte|body", identity, re.IGNORECASE):
        value *= 1.35
    if STRIP_WORDS.search(identity):
        value *= 0.4
    return value


def _sanitize(node, base):
    """Ne conserve que du HTML sûr et lisible."""
    out = []
    for child in node.children:
        if child.tag == "#text":
            out.append(dom.escape_text(child.data))
        elif child.tag == "#comment" or child.tag in STRIP_TAGS:
            continue
        elif child.tag in KEEP_TAGS:
            attrs = ""
            for name in KEEP_ATTRS.get(child.tag, ()):
                value = child.get(name)
                if name in ("href", "src"):
                    value = clean_url(value, base)
                if value:
                    attrs += f' {name}="{dom.escape_attr(value)}"'
            if child.tag in dom.VOID_TAGS:
                out.append(f"<{child.tag}{attrs}>")
            else:
                inner = _sanitize(child, base)
                if child.tag in ("span", "div") and not attrs:
                    out.append(inner)
                else:
                    out.append(f"<{child.tag}{attrs}>{inner}</{child.tag}>")
        else:
            out.append(_sanitize(child, base))
    return "".join(out)


def readable(html, url):
    """Renvoie `{title, byline, date, text, html, image}` pour une page article."""
    doc = dom.parse(html)
    body = doc.find("body") or doc
    _prune(body)

    best, best_score = None, 0.0
    for selector in CONTENT_HINTS:
        for node in body.select(selector):
            value = _score(node) * 1.2
            if value > best_score:
                best, best_score = node, value
    for node in body.elements():
        if node.tag in ("p", "a", "li", "span", "td"):
            continue
        value = _score(node)
        if value > best_score:
            best, best_score = node, value
    if best is None:
        best = body

    title = (
        meta_content(doc, "og:title")
        or squeeze((doc.find("h1").text() if doc.find("h1") else ""))
        or squeeze(doc.find("title").text() if doc.find("title") else "")
    )
    date = parse_date(
        meta_content(doc, "article:published_time", "datePublished", "date")
    )
    if date is None:
        time_node = doc.find("time")
        if time_node is not None:
            date = parse_date(time_node.get("datetime") or time_node.text())

    return {
        "title": title,
        "byline": meta_content(doc, "author", "article:author"),
        "date": date,
        "image": clean_url(meta_content(doc, "og:image", "twitter:image"), url),
        "text": best.block_text(),
        "html": _sanitize(best, url).strip(),
        "excerpt": squeeze(best.block_text().replace("\n", " "), 600),
    }
