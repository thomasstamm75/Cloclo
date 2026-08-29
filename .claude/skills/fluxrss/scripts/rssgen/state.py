"""Mémoire des articles déjà vus.

Beaucoup de pages n'affichent aucune date. Sans mémoire, chaque génération
leur donnerait l'heure courante et l'agrégateur remonterait tout en haut des
articles anciens. On enregistre donc la date de première apparition de chaque
article et on s'y tient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .dates import UTC, now, parse_date

MAX_TRACKED_ITEMS = 600


def _to_second(value: datetime) -> datetime:
    """Tronque aux secondes : c'est la précision d'une date RSS, et cela rend
    l'état exactement identique après un aller-retour par le fichier JSON."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.replace(microsecond=0)


@dataclass
class Seen:
    first_seen: datetime
    title: str = ""

    def __post_init__(self) -> None:
        self.first_seen = _to_second(self.first_seen)

    def to_json(self) -> dict:
        return {"first_seen": self.first_seen.isoformat(), "title": self.title}


class FeedState:
    """État d'un flux, stocké dans un fichier JSON versionné avec le dépôt."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.items: dict[str, Seen] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for guid, payload in (data.get("items") or {}).items():
            stamp = parse_date(payload.get("first_seen")) if isinstance(payload, dict) else None
            if stamp:
                self.items[guid] = Seen(first_seen=stamp, title=payload.get("title", ""))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # On ne garde que les entrées les plus récentes : le fichier reste petit
        # et lisible dans un diff Git.
        kept = sorted(self.items.items(), key=lambda kv: kv[1].first_seen,
                      reverse=True)[:MAX_TRACKED_ITEMS]
        payload = {
            "updated": now().isoformat(),
            "items": {guid: seen.to_json() for guid, seen in kept},
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    def is_new(self, guid: str) -> bool:
        return guid not in self.items

    def stamp(self, guid: str, title: str, published: datetime | None,
              position: int = 0) -> datetime:
        """Retourne la date à publier et enregistre l'article s'il est inédit.

        Une date lue sur la page fait toujours autorité ; la date de première
        vue ne sert que de repli.

        « position » est le rang de l'article dans la page. Sur une page sans
        dates, tous les articles découverts ensemble recevraient sinon le même
        horodatage et l'agrégateur les afficherait dans un ordre quelconque ;
        décaler d'une seconde par rang conserve l'ordre voulu par le site.
        """
        published = _to_second(published) if published else None
        existing = self.items.get(guid)
        if existing is None:
            defaut = published or (now() - timedelta(seconds=position))
            existing = Seen(first_seen=defaut, title=title)
            self.items[guid] = existing
        elif published and abs((published - existing.first_seen).total_seconds()) > 86400:
            # La page a corrigé sa date : on l'accepte.
            existing.first_seen = published
        return published or existing.first_seen
