"""Récupération HTTP : en-têtes polis, cache conditionnel, tolérance aux pannes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .util import digest

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; rssgen/1.0; générateur de flux RSS personnel; "
    "+https://github.com/thomasstamm75/Cloclo)"
)
DEFAULT_TIMEOUT = 25
RETRY_DELAYS = (2, 5, 10)
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """La page n'a pas pu être récupérée après les tentatives prévues."""


@dataclass
class Response:
    url: str          # URL finale, après redirections
    status: int
    text: str
    from_cache: bool = False
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class Fetcher:
    """Client HTTP réutilisable, avec cache disque des en-têtes de validation.

    Le cache sert deux choses : éviter de retélécharger une page inchangée
    (304), et garder le dernier HTML connu pour ne pas produire un flux vide
    quand le site est momentanément indisponible.
    """

    def __init__(self, cache_dir: Path | str | None = ".cache",
                 user_agent: str = DEFAULT_UA, timeout: int = DEFAULT_TIMEOUT,
                 delay: float = 1.0) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        })
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache
    def _cache_path(self, url: str) -> Path | None:
        return self.cache_dir / f"{digest(url)}.json" if self.cache_dir else None

    def _read_cache(self, url: str) -> dict[str, Any]:
        path = self._cache_path(url)
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_cache(self, url: str, payload: dict[str, Any]) -> None:
        path = self._cache_path(url)
        if not path:
            return
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass  # un cache indisponible ne doit jamais faire échouer la génération

    # ------------------------------------------------------------------- HTTP
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str, use_cache: bool = True) -> Response:
        """Télécharge une page. Lève FetchError si tout échoue sans cache utilisable."""
        cached = self._read_cache(url) if use_cache else {}
        headers: dict[str, str] = {}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]

        last_error: Exception | None = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            if attempt:
                time.sleep(RETRY_DELAYS[attempt - 1])
            self._throttle()
            try:
                reply = self.session.get(url, headers=headers, timeout=self.timeout,
                                         allow_redirects=True)
            except requests.RequestException as exc:
                last_error = exc
                continue

            if reply.status_code == 304 and cached.get("body"):
                return Response(url=cached.get("url", url), status=304,
                                text=cached["body"], from_cache=True,
                                etag=cached.get("etag"),
                                last_modified=cached.get("last_modified"))
            if reply.status_code in RETRYABLE_STATUS:
                last_error = FetchError(f"HTTP {reply.status_code} sur {url}")
                continue
            if reply.status_code >= 400:
                raise FetchError(f"HTTP {reply.status_code} sur {url}")

            if not reply.encoding or reply.encoding.lower() == "iso-8859-1":
                reply.encoding = reply.apparent_encoding or "utf-8"
            body = reply.text
            payload = {
                "url": reply.url,
                "body": body,
                "etag": reply.headers.get("ETag"),
                "last_modified": reply.headers.get("Last-Modified"),
            }
            if use_cache:
                self._write_cache(url, payload)
            return Response(url=reply.url, status=reply.status_code, text=body,
                            etag=payload["etag"], last_modified=payload["last_modified"])

        if cached.get("body"):
            # Le site est en panne : on repart du dernier HTML connu plutôt que
            # de publier un flux vide, ce qui viderait la liste dans Feeder.
            return Response(url=cached.get("url", url), status=200,
                            text=cached["body"], from_cache=True)
        raise FetchError(f"Échec de récupération de {url} : {last_error}")
