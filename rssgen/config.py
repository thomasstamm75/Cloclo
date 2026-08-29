"""Lecture et validation de feeds.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .extract import Rules
from .util import slugify

SELECTOR_KEYS = ("item", "title", "link", "date", "summary", "author",
                 "image", "content", "date_attr")


class ConfigError(ValueError):
    """La configuration est inutilisable en l'état."""


@dataclass
class FeedConfig:
    id: str
    url: str
    title: str = ""
    description: str = ""
    selectors: Rules = field(default_factory=Rules)
    max_items: int = 25
    full_text: bool = False
    full_text_selector: str = ""
    max_full_text: int = 10
    include: str = ""
    exclude: str = ""
    language: str = "fr"
    ttl: int = 60
    enabled: bool = True

    @property
    def filename(self) -> str:
        return f"{self.id}.xml"

    def matches(self, *fields: str) -> bool:
        """Applique les filtres include/exclude sur les champs d'un article."""
        haystack = " ".join(f for f in fields if f)
        if self.include and not re.search(self.include, haystack, re.IGNORECASE):
            return False
        if self.exclude and re.search(self.exclude, haystack, re.IGNORECASE):
            return False
        return True


@dataclass
class SiteConfig:
    base_url: str = ""
    title: str = "Mes flux RSS"
    description: str = "Flux générés pour des pages qui n'en proposent pas."


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    feeds: list[FeedConfig] = field(default_factory=list)
    request_delay: float = 1.0
    user_agent: str = ""
    timeout: int = 25
    path: Path | None = None

    def get(self, feed_id: str) -> FeedConfig | None:
        return next((f for f in self.feeds if f.id == feed_id), None)

    @property
    def active(self) -> list[FeedConfig]:
        return [f for f in self.feeds if f.enabled]


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} doit contenir un dictionnaire à la racine.")

    site_raw = raw.get("site") or {}
    site = SiteConfig(
        base_url=str(site_raw.get("base_url", "")).rstrip("/"),
        title=str(site_raw.get("title", "Mes flux RSS")),
        description=str(site_raw.get("description", SiteConfig.description)),
    )
    defaults = raw.get("defaults") or {}

    entries = raw.get("feeds")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ConfigError("La clé « feeds » doit être une liste.")

    feeds: list[FeedConfig] = []
    used_ids: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        feeds.append(_parse_feed(entry, index, defaults, used_ids))

    return Config(
        site=site,
        feeds=feeds,
        request_delay=float(defaults.get("request_delay", 1.0)),
        user_agent=str(defaults.get("user_agent", "")),
        timeout=int(defaults.get("timeout", 25)),
        path=path,
    )


def _parse_feed(entry, index: int, defaults: dict, used_ids: set[str]) -> FeedConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"Entrée n°{index} de « feeds » : un dictionnaire est attendu.")
    url = str(entry.get("url", "")).strip()
    if not url:
        raise ConfigError(f"Entrée n°{index} de « feeds » : la clé « url » est obligatoire.")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"URL invalide pour l'entrée n°{index} : {url}")

    feed_id = slugify(str(entry.get("id", "")) or url, fallback=f"flux-{index}")
    if feed_id in used_ids:
        raise ConfigError(f"Identifiant de flux en double : « {feed_id} ».")
    used_ids.add(feed_id)

    selectors_raw = entry.get("selectors") or {}
    if not isinstance(selectors_raw, dict):
        raise ConfigError(f"« selectors » doit être un dictionnaire (flux {feed_id}).")
    unknown = set(selectors_raw) - set(SELECTOR_KEYS)
    if unknown:
        raise ConfigError(
            f"Sélecteur inconnu pour le flux {feed_id} : {', '.join(sorted(unknown))}. "
            f"Clés acceptées : {', '.join(SELECTOR_KEYS)}.")
    selectors = Rules(**{k: str(v) for k, v in selectors_raw.items()})

    def option(key: str, fallback):
        value = entry.get(key, defaults.get(key, fallback))
        return fallback if value is None else value

    for pattern_key in ("include", "exclude"):
        pattern = str(entry.get(pattern_key, "") or "")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(
                    f"Expression régulière « {pattern_key} » invalide "
                    f"pour le flux {feed_id} : {exc}") from exc

    return FeedConfig(
        id=feed_id,
        url=url,
        title=str(entry.get("title", "") or ""),
        description=str(entry.get("description", "") or ""),
        selectors=selectors,
        max_items=int(option("max_items", 25)),
        full_text=bool(option("full_text", False)),
        full_text_selector=str(entry.get("full_text_selector", "") or ""),
        max_full_text=int(option("max_full_text", 10)),
        include=str(entry.get("include", "") or ""),
        exclude=str(entry.get("exclude", "") or ""),
        language=str(option("language", "fr")),
        ttl=int(option("ttl", 60)),
        enabled=bool(entry.get("enabled", True)),
    )


def append_feed(path: Path | str, entry: dict) -> None:
    """Ajoute un flux à feeds.yaml en préservant le fichier existant."""
    path = Path(path)
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("feeds", [])
    if not isinstance(raw["feeds"], list):
        raise ConfigError("La clé « feeds » doit être une liste.")
    raw["feeds"].append(entry)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8")
