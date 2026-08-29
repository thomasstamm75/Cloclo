"""Sérialisation des entrées en RSS 2.0, Atom 1.0 ou JSON Feed 1.1."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from .dates import to_rfc822, to_rfc3339
from .dom import escape_attr, escape_text

GENERATOR = "Cloclo"
# XML 1.0 interdit ces caractères de contrôle, même échappés.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(value):
    return _ILLEGAL.sub("", value or "")


def _tag(name, value, cdata=False, **attrs):
    value = _clean(value)
    if not value and not attrs:
        return ""
    rendered = "".join(f' {k}="{escape_attr(_clean(v))}"' for k, v in attrs.items() if v)
    if value == "":
        return f"  <{name}{rendered}/>\n"
    body = f"<![CDATA[{value.replace(']]>', ']]&gt;')}]]>" if cdata else escape_text(value)
    return f"  <{name}{rendered}>{body}</{name}>\n"


def stable_guid(item):
    """Identifiant stable : le lien, sinon une empreinte du titre et de la date."""
    if item.guid:
        return item.guid
    if item.link:
        return item.link
    seed = f"{item.title}|{to_rfc3339(item.date) or ''}"
    return "urn:cloclo:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()


class FeedMeta:
    def __init__(self, title, link, description="", self_url="", language="", icon="", author=""):
        self.title = title or link
        self.link = link
        self.description = description or f"Flux généré depuis {link}"
        self.self_url = self_url
        self.language = language
        self.icon = icon
        self.author = author


def _item_description(item, full=False):
    if full and item.content_html:
        return item.content_html
    parts = []
    if item.image:
        parts.append(
            f'<p><img src="{escape_attr(item.image)}" alt="" /></p>'
        )
    if item.summary:
        parts.append(f"<p>{escape_text(item.summary)}</p>")
    if not parts and item.title:
        parts.append(f"<p>{escape_text(item.title)}</p>")
    return "".join(parts)


def to_rss(items, meta, full=False):
    now = to_rfc822(datetime.now(timezone.utc))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:content="http://purl.org/rss/1.0/modules/content/">\n',
        "<channel>\n",
        _tag("title", meta.title),
        _tag("link", meta.link),
        _tag("description", meta.description),
        _tag("language", meta.language) if meta.language else "",
        _tag("generator", GENERATOR),
        _tag("lastBuildDate", now),
        _tag("docs", "https://www.rssboard.org/rss-specification"),
    ]
    if meta.self_url:
        out.append(
            f'  <atom:link href="{escape_attr(meta.self_url)}" rel="self"'
            ' type="application/rss+xml"/>\n'
        )
    if meta.icon:
        out.append(
            "  <image>\n"
            f"    <url>{escape_text(meta.icon)}</url>\n"
            f"    <title>{escape_text(meta.title)}</title>\n"
            f"    <link>{escape_text(meta.link)}</link>\n"
            "  </image>\n"
        )
    for item in items:
        guid = stable_guid(item)
        out.append("  <item>\n")
        out.append("  " + _tag("title", item.title))
        if item.link:
            out.append("  " + _tag("link", item.link))
        out.append(
            "  "
            + _tag("guid", guid, isPermaLink="true" if guid.startswith("http") else "false")
        )
        if item.date:
            out.append("  " + _tag("pubDate", to_rfc822(item.date)))
        if item.author:
            out.append("  " + _tag("dc:creator", item.author, cdata=True))
        out.append("  " + _tag("description", _item_description(item, full), cdata=True))
        if full and item.content_html:
            out.append("  " + _tag("content:encoded", item.content_html, cdata=True))
        for category in item.categories:
            out.append("  " + _tag("category", category, cdata=True))
        if item.image:
            out.append(
                f'    <enclosure url="{escape_attr(item.image)}" type="image/jpeg"/>\n'
            )
        out.append("  </item>\n")
    out.append("</channel>\n</rss>\n")
    return "".join(out)


def to_atom(items, meta, full=False):
    now = to_rfc3339(datetime.now(timezone.utc))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        + (f' xml:lang="{escape_attr(meta.language)}"' if meta.language else "")
        + ">\n",
        _tag("title", meta.title),
        _tag("subtitle", meta.description),
        _tag("id", meta.self_url or meta.link),
        _tag("updated", now),
        _tag("generator", GENERATOR),
        f'  <link href="{escape_attr(meta.link)}" rel="alternate"/>\n',
    ]
    if meta.self_url:
        out.append(f'  <link href="{escape_attr(meta.self_url)}" rel="self"/>\n')
    if meta.icon:
        out.append(_tag("icon", meta.icon))
    for item in items:
        out.append("  <entry>\n")
        out.append("  " + _tag("title", item.title))
        out.append("  " + _tag("id", stable_guid(item)))
        if item.link:
            out.append(f'    <link href="{escape_attr(item.link)}" rel="alternate"/>\n')
        out.append("  " + _tag("updated", to_rfc3339(item.date) or now))
        if item.date:
            out.append("  " + _tag("published", to_rfc3339(item.date)))
        if item.author:
            out.append(f"    <author><name>{escape_text(item.author)}</name></author>\n")
        for category in item.categories:
            out.append(f'    <category term="{escape_attr(category)}"/>\n')
        content = _item_description(item, full)
        out.append(
            f'    <content type="html"><![CDATA[{_clean(content)}]]></content>\n'
        )
        out.append("  </entry>\n")
    out.append("</feed>\n")
    return "".join(out)


def to_json_feed(items, meta, full=False):
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": meta.title,
        "home_page_url": meta.link,
        "description": meta.description,
        "language": meta.language or None,
        "icon": meta.icon or None,
        "items": [],
    }
    if meta.self_url:
        payload["feed_url"] = meta.self_url
    for item in items:
        entry = {
            "id": stable_guid(item),
            "url": item.link or None,
            "title": item.title,
            "content_html": _item_description(item, full),
            "summary": item.summary or None,
            "image": item.image or None,
            "date_published": to_rfc3339(item.date),
            "tags": item.categories or None,
        }
        if item.author:
            entry["authors"] = [{"name": item.author}]
        payload["items"].append({k: v for k, v in entry.items() if v is not None})
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


FORMATS = {
    "rss": (to_rss, "application/rss+xml; charset=utf-8"),
    "atom": (to_atom, "application/atom+xml; charset=utf-8"),
    "json": (to_json_feed, "application/feed+json; charset=utf-8"),
}


def render(items, meta, fmt="rss", full=False):
    try:
        builder, content_type = FORMATS[fmt]
    except KeyError:
        raise ValueError(f"format inconnu : {fmt!r} (rss, atom, json)") from None
    return builder(items, meta, full=full), content_type
