"""Récupération HTTP : décompression, détection d'encodage, robots.txt, cache."""

from __future__ import annotations

import gzip
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; Cloclo/1.0; +https://github.com/thomasstamm75/cloclo)"
)
DEFAULT_TIMEOUT = 15.0
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5

_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.IGNORECASE
)
_XML_DECL = re.compile(rb"""<\?xml[^>]+encoding\s*=\s*["']([\w-]+)["']""", re.IGNORECASE)


class FetchError(RuntimeError):
    """Erreur réseau, HTTP ou de politique d'accès."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Response:
    __slots__ = ("url", "status", "headers", "content", "encoding", "from_cache")

    def __init__(self, url, status, headers, content, encoding, from_cache=False):
        self.url = url
        self.status = status
        self.headers = headers
        self.content = content
        self.encoding = encoding
        self.from_cache = from_cache

    @property
    def content_type(self):
        return (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()

    @property
    def text(self):
        return self.content.decode(self.encoding, errors="replace")


# ---------------------------------------------------------------------------
# Cache mémoire à durée de vie
# ---------------------------------------------------------------------------


class TTLCache:
    def __init__(self, ttl=300.0, max_entries=256):
        self.ttl = ttl
        self.max_entries = max_entries
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires, value = entry
            if expires < time.time():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key, value, ttl=None):
        with self._lock:
            if len(self._data) >= self.max_entries:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time() + (self.ttl if ttl is None else ttl), value)

    def clear(self):
        with self._lock:
            self._data.clear()


_page_cache = TTLCache(ttl=300.0, max_entries=256)
_robots_cache = TTLCache(ttl=3600.0, max_entries=128)


# ---------------------------------------------------------------------------
# Garde-fous
# ---------------------------------------------------------------------------


def is_private_host(host):
    """Vrai si l'hôte résout vers une adresse privée, locale ou réservée."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def normalize_url(url, base=None):
    """Complète un schéma manquant et résout les liens relatifs."""
    url = (url or "").strip()
    if not url:
        raise FetchError("URL vide")
    if base:
        url = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme:
        url = "https://" + url.lstrip("/")
        parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"schéma non supporté : {parsed.scheme!r}")
    if not parsed.netloc:
        raise FetchError(f"URL invalide : {url!r}")
    return urllib.parse.urlunsplit(parsed)


def robots_allows(url, user_agent=DEFAULT_UA, timeout=5.0):
    """Consulte robots.txt (résultat mis en cache une heure)."""
    parsed = urllib.parse.urlsplit(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = _robots_cache.get(robots_url)
    if parser is None:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            request = urllib.request.Request(
                robots_url, headers={"User-Agent": user_agent}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(256 * 1024).decode("utf-8", errors="replace")
            parser.parse(body.splitlines())
        except Exception:
            # robots.txt absent ou illisible : on considère l'accès permis.
            parser.allow_all = True
        _robots_cache.set(robots_url, parser)
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Requête
# ---------------------------------------------------------------------------


def _decompress(raw, encoding):
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    return raw


def detect_encoding(raw, content_type):
    match = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    candidates = []
    if match:
        candidates.append(match.group(1))
    head = raw[:4096]
    meta = _META_CHARSET.search(head) or _XML_DECL.search(head)
    if meta:
        candidates.append(meta.group(1).decode("ascii", errors="ignore"))
    candidates.append("utf-8")
    for candidate in candidates:
        name = candidate.strip().lower()
        if name in ("", "none"):
            continue
        try:
            raw[:2048].decode(name)
            return name
        except (LookupError, UnicodeDecodeError):
            continue
    return "utf-8"


def fetch(
    url,
    timeout=DEFAULT_TIMEOUT,
    user_agent=DEFAULT_UA,
    max_bytes=MAX_BYTES,
    respect_robots=True,
    allow_private=False,
    cache=True,
    ttl=None,
    headers=None,
):
    """Télécharge une URL et renvoie une `Response`.

    Lève `FetchError` en cas d'erreur réseau, HTTP, ou si l'accès est
    interdit (robots.txt, adresse privée, contenu trop volumineux).
    """
    url = normalize_url(url)
    cache_key = (url, user_agent)
    if cache:
        cached = _page_cache.get(cache_key)
        if cached is not None:
            return Response(
                cached.url,
                cached.status,
                cached.headers,
                cached.content,
                cached.encoding,
                from_cache=True,
            )

    host = urllib.parse.urlsplit(url).hostname or ""
    if not allow_private and is_private_host(host):
        raise FetchError(f"hôte non autorisé (adresse privée ou introuvable) : {host}")
    if respect_robots and not robots_allows(url, user_agent):
        raise FetchError(f"robots.txt interdit l'accès à {url}", status=403)

    request_headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr,fr-FR;q=0.9,en;q=0.6",
        "Accept-Encoding": "gzip, deflate",
    }
    request_headers.update(headers or {})

    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise FetchError(f"réponse trop volumineuse (> {max_bytes} octets)")
            raw = _decompress(raw, response.headers.get("Content-Encoding"))
            final_url = response.geturl()
            status = response.status
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} sur {url}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"échec réseau sur {url} : {exc.reason}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise FetchError(f"délai dépassé sur {url}") from exc

    encoding = detect_encoding(raw, response_headers.get("Content-Type", ""))
    result = Response(final_url, status, response_headers, raw, encoding)
    if cache:
        _page_cache.set(cache_key, result, ttl=ttl)
    return result


def clear_cache():
    _page_cache.clear()
    _robots_cache.clear()
