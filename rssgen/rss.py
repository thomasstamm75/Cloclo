"""Écriture du XML RSS 2.0 et des fichiers OPML."""

from __future__ import annotations

import html
from xml.etree import ElementTree as ET

from .dates import now, to_rfc822
from .extract import Article
from .util import clean_text

ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"

# Caractères interdits en XML 1.0 : présents dans certaines pages mal encodées,
# ils rendraient le flux illisible par l'agrégateur.
_ILLEGAL = dict.fromkeys(
    list(range(0x00, 0x09)) + [0x0B, 0x0C] + list(range(0x0E, 0x20)) + [0xFFFE, 0xFFFF]
)


def _sanitize(value: str) -> str:
    return (value or "").translate(_ILLEGAL)


def build_rss(articles: list[Article], *, title: str, link: str,
              description: str = "", feed_url: str = "",
              language: str = "fr", ttl: int = 60,
              generator: str = "rssgen") -> str:
    """Produit un flux RSS 2.0 complet sous forme de chaîne XML."""
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("content", CONTENT_NS)
    ET.register_namespace("dc", DC_NS)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    _text(channel, "title", title)
    _text(channel, "link", link)
    _text(channel, "description", description or f"Flux généré à partir de {link}")
    _text(channel, "language", language)
    _text(channel, "generator", generator)
    _text(channel, "lastBuildDate", to_rfc822(now()))
    _text(channel, "ttl", str(ttl))
    if feed_url:
        # Auto-référence : Feeder et les autres lecteurs l'utilisent pour
        # retrouver l'adresse canonique du flux.
        ET.SubElement(channel, f"{{{ATOM_NS}}}link",
                      {"href": feed_url, "rel": "self",
                       "type": "application/rss+xml"})

    for article in articles:
        _append_item(channel, article)

    ET.indent(rss, space="  ")
    body = ET.tostring(rss, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def _append_item(channel: ET.Element, article: Article) -> None:
    item = ET.SubElement(channel, "item")
    _text(item, "title", article.title or "(sans titre)")
    _text(item, "link", article.link)

    guid = ET.SubElement(item, "guid",
                         {"isPermaLink": "true" if article.guid.startswith("http") else "false"})
    guid.text = _sanitize(article.guid)

    if article.published:
        _text(item, "pubDate", to_rfc822(article.published))
    if article.author:
        _text(item, f"{{{DC_NS}}}creator", article.author)
    if article.image:
        ET.SubElement(item, "enclosure",
                      {"url": article.image, "type": _mime_of(article.image),
                       "length": "0"})

    description = _description_html(article)
    if description:
        _text(item, "description", description)
    if article.content_html:
        _text(item, f"{{{CONTENT_NS}}}encoded", article.content_html)


def _description_html(article: Article) -> str:
    """Résumé affiché dans le lecteur : vignette éventuelle puis texte."""
    pieces = []
    if article.image:
        pieces.append(
            f'<p><img src="{html.escape(article.image, quote=True)}" alt=""></p>')
    if article.summary:
        pieces.append(f"<p>{html.escape(article.summary)}</p>")
    if not pieces and article.content_html:
        return article.content_html
    return "".join(pieces)


def _mime_of(url: str) -> str:
    extension = url.rsplit(".", 1)[-1].lower().split("?")[0]
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "gif": "image/gif", "webp": "image/webp", "avif": "image/avif",
        "svg": "image/svg+xml",
    }.get(extension, "image/jpeg")


def _text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    node = ET.SubElement(parent, tag)
    node.text = _sanitize(clean_text(value) if tag in ("title", "language") else value)
    return node


def build_opml(entries: list[tuple[str, str, str]], title: str = "Flux rssgen") -> str:
    """Fichier OPML importable en une fois dans Feeder.

    Chaque entrée est un triplet (titre, URL du flux, URL du site).
    """
    opml = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(opml, "head")
    _text(head, "title", title)
    _text(head, "dateCreated", to_rfc822(now()))
    body = ET.SubElement(opml, "body")
    for feed_title, feed_url, site_url in entries:
        ET.SubElement(body, "outline", {
            "type": "rss",
            "text": clean_text(feed_title),
            "title": clean_text(feed_title),
            "xmlUrl": feed_url,
            "htmlUrl": site_url,
        })
    ET.indent(opml, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(opml, encoding="unicode") + "\n")
