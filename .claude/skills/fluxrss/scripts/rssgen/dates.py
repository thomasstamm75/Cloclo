"""Analyse des dates trouvées dans les pages : formats FR, EN, ISO et relatifs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime

MONTHS = {
    # français (avec et sans accent, abrégés courants)
    "janvier": 1, "janv": 1, "jan": 1,
    "fevrier": 2, "février": 2, "fevr": 2, "fev": 2, "feb": 2,
    "mars": 3, "mar": 3,
    "avril": 4, "avr": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "jun": 6,
    "juillet": 7, "juil": 7, "jul": 7,
    "aout": 8, "août": 8, "aou": 8, "aug": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "décembre": 12, "dec": 12, "déc": 12,
    # anglais
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

# « il y a 3 heures », « 2 days ago », « publié il y a 5 min »
_RELATIVE = re.compile(
    r"(?:il\s+y\s+a|depuis|there\s+is)?\s*(\d+)\s*"
    r"(minute|min|heure|hour|h|jour|day|j|semaine|week|mois|month|an|année|year)s?"
    r"(?:\s+ago)?",
    re.IGNORECASE,
)
_RELATIVE_UNITS = {
    "minute": 60, "min": 60,
    "heure": 3600, "hour": 3600, "h": 3600,
    "jour": 86400, "day": 86400, "j": 86400,
    "semaine": 604800, "week": 604800,
    "mois": 2592000, "month": 2592000,
    "an": 31536000, "année": 31536000, "year": 31536000,
}

# 12 janvier 2026 / 12 jan. 2026 / January 12, 2026
_TEXTUAL = re.compile(
    r"(?:(\d{1,2})\s*(?:er)?\s+([A-Za-zÀ-ÿ]{3,10})\.?\s+(\d{4})"
    r"|([A-Za-zÀ-ÿ]{3,10})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}))",
    re.IGNORECASE,
)
# 12/01/2026, 12-01-2026, 12.01.2026 (jour d'abord, convention française)
_NUMERIC_DMY = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")
# 2026-01-12, éventuellement suivi d'une heure
_ISO = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?)?"
)
_TIME = re.compile(r"\b(\d{1,2})\s*[h:]\s*(\d{2})\b")

UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def _safe(year: int, month: int, day: int, hour: int = 0, minute: int = 0,
          second: int = 0, tz: timezone = UTC) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None


def _parse_offset(raw: str | None) -> timezone:
    if not raw or raw == "Z":
        return UTC
    sign = -1 if raw[0] == "-" else 1
    body = raw[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(body[:2]), minutes=int(body[2:4] or 0)))


def parse_date(raw: str | None, reference: datetime | None = None) -> datetime | None:
    """Retourne un datetime conscient du fuseau, ou None si rien n'est reconnu.

    Les dates situées loin dans le futur sont rejetées : elles viennent presque
    toujours d'un faux positif (un numéro de page pris pour une année).
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    reference = reference or now()

    parsed = (
        _try_iso(text)
        or _try_rfc2822(text)
        or _try_textual(text)
        or _try_numeric(text)
        or _try_relative(text, reference)
    )
    if parsed and parsed <= reference + timedelta(days=2):
        return parsed
    return None


def _try_iso(text: str) -> datetime | None:
    match = _ISO.search(text)
    if not match:
        return None
    year, month, day, hour, minute, second, offset = match.groups()
    return _safe(int(year), int(month), int(day), int(hour or 0), int(minute or 0),
                 int(second or 0), _parse_offset(offset))


def _try_rfc2822(text: str) -> datetime | None:
    if "," not in text and " " not in text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _try_textual(text: str) -> datetime | None:
    match = _TEXTUAL.search(text)
    if not match:
        return None
    day, month_name, year, month_name2, day2, year2 = match.groups()
    if month_name2:
        day, month_name, year = day2, month_name2, year2
    month = MONTHS.get(month_name.lower().rstrip("."))
    if not month:
        return None
    hour, minute = _extract_time(text)
    return _safe(int(year), month, int(day), hour, minute)


def _try_numeric(text: str) -> datetime | None:
    match = _NUMERIC_DMY.search(text)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    if month > 12 and day <= 12:  # tolère l'ordre américain mois/jour
        day, month = month, day
    hour, minute = _extract_time(text)
    return _safe(year, month, day, hour, minute)


def _try_relative(text: str, reference: datetime) -> datetime | None:
    match = _RELATIVE.search(text)
    if not match:
        return None
    amount, unit = match.groups()
    seconds = _RELATIVE_UNITS.get(unit.lower())
    if not seconds:
        return None
    return reference - timedelta(seconds=int(amount) * seconds)


def _extract_time(text: str) -> tuple[int, int]:
    match = _TIME.search(text)
    if not match:
        return 0, 0
    hour, minute = int(match.group(1)), int(match.group(2))
    return (hour, minute) if hour < 24 and minute < 60 else (0, 0)


def to_rfc822(value: datetime) -> str:
    """Format de date attendu dans un flux RSS 2.0."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return format_datetime(value)
