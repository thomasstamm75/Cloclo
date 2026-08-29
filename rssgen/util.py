"""Petites fonctions partagées : URLs, texte, empreintes."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

# Paramètres de tracking retirés des liens : ils font croire à l'agrégateur
# qu'un article déjà lu est nouveau.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "fbclid", "gclid", "mc_cid", "mc_eid",
    "xtor", "xtref", "ref_src", "igshid", "spm", "_ga",
}

_WS = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """Normalise les espaces et supprime les caractères invisibles."""
    if not value:
        return ""
    value = value.replace(" ", " ").replace("​", "")
    return _WS.sub(" ", value).strip()


def absolute_url(href: str | None, base: str) -> str:
    """Transforme un lien relatif en URL absolue."""
    if not href:
        return ""
    href = href.strip()
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return ""
    return urljoin(base, href)


def canonical_url(url: str) -> str:
    """Version stable d'une URL, utilisée comme identifiant d'article."""
    if not url:
        return ""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        parts.params,
        urlencode(query),
        "",  # le fragment ne distingue pas deux articles
    ))


def slugify(value: str, fallback: str = "flux") -> str:
    """Identifiant de fichier lisible, dérivé d'un titre ou d'une URL."""
    value = re.sub(r"^https?://(www\.)?", "", (value or "").strip().lower())
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:60] or fallback


def digest(*parts: str) -> str:
    """Empreinte courte et déterministe pour un identifiant d'article."""
    payload = "\x1e".join(p or "" for p in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def truncate(text: str, limit: int = 400) -> str:
    """Coupe proprement au dernier mot avant la limite."""
    text = clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"
