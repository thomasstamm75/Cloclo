"""Analyse de dates hétérogènes (ISO, français, anglais, relatives)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime

MONTHS = {}
for _index, _names in enumerate(
    [
        ("janvier", "janv", "jan", "january"),
        ("février", "fevrier", "févr", "fevr", "feb", "february"),
        ("mars", "mar", "march"),
        ("avril", "avr", "apr", "april"),
        ("mai", "may"),
        ("juin", "jun", "june"),
        ("juillet", "juil", "jul", "july"),
        ("août", "aout", "aug", "august"),
        ("septembre", "sept", "sep", "september"),
        ("octobre", "oct", "october"),
        ("novembre", "nov", "november"),
        ("décembre", "decembre", "déc", "dec", "december"),
    ],
    start=1,
):
    for _name in _names:
        MONTHS[_name] = _index

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

_PATTERNS = [
    # 2024-05-17T08:30:00+02:00 / 2024-05-17 08:30
    (
        re.compile(
            r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
            r"(?:[T ](?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?"
            r"(?P<tz>Z|[+-]\d{2}:?\d{2})?)?"
        ),
        "iso",
    ),
    # 17 mai 2024 à 08h30
    (
        re.compile(
            rf"(?P<d>\d{{1,2}})(?:er)?\s+(?P<mon>{_MONTH_ALT})\.?\s+(?P<y>\d{{4}})"
            r"(?:[\s,]+(?:à|at)?\s*(?P<H>\d{1,2})\s*[:h]\s*(?P<M>\d{2}))?",
            re.IGNORECASE,
        ),
        "dmy_name",
    ),
    # May 17, 2024
    (
        re.compile(
            rf"(?P<mon>{_MONTH_ALT})\.?\s+(?P<d>\d{{1,2}}),?\s+(?P<y>\d{{4}})",
            re.IGNORECASE,
        ),
        "mdy_name",
    ),
    # 17/05/2024 ou 17.05.2024
    (
        re.compile(
            r"(?P<d>\d{1,2})[/.](?P<m>\d{1,2})[/.](?P<y>\d{4})"
            r"(?:\s+(?P<H>\d{1,2})[:h](?P<M>\d{2}))?"
        ),
        "dmy",
    ),
]

_RELATIVE = re.compile(
    r"(?:il y a|depuis)\s+(?P<n>\d+)\s+(?P<unit>minute|heure|jour|semaine|mois|an)"
    r"|(?P<n2>\d+)\s*(?P<unit2>min|h|j|d|w|mo|y)\s*(?:ago)?"
    r"|(?P<n3>\d+)\s+(?P<unit3>minute|hour|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "minute": 60,
    "min": 60,
    "heure": 3600,
    "hour": 3600,
    "h": 3600,
    "jour": 86400,
    "day": 86400,
    "j": 86400,
    "d": 86400,
    "semaine": 604800,
    "week": 604800,
    "w": 604800,
    "mois": 2592000,
    "month": 2592000,
    "mo": 2592000,
    "an": 31536000,
    "year": 31536000,
    "y": 31536000,
}


def _tzinfo(raw):
    if not raw:
        return timezone.utc
    if raw in ("Z", "z"):
        return timezone.utc
    raw = raw.replace(":", "")
    sign = 1 if raw[0] == "+" else -1
    hours, minutes = int(raw[1:3]), int(raw[3:5])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _build(year, month, day, hour=0, minute=0, second=0, tzinfo=timezone.utc):
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=tzinfo,
        )
    except ValueError:
        return None


def parse_date(value, now=None):
    """Renvoie un `datetime` conscient du fuseau, ou `None`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None

    # Horodatage Unix
    if re.fullmatch(r"\d{10}", text):
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    if re.fullmatch(r"\d{13}", text):
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)

    # RFC 822/2822 (« Fri, 17 May 2024 08:30:00 +0200 »)
    if re.match(r"^[A-Za-z]{3},\s", text):
        try:
            parsed = parsedate_to_datetime(text)
            if parsed:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass

    for pattern, kind in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        if kind == "iso":
            built = _build(
                groups["y"],
                groups["m"],
                groups["d"],
                groups.get("H"),
                groups.get("M"),
                groups.get("S"),
                _tzinfo(groups.get("tz")),
            )
        elif kind in ("dmy_name", "mdy_name"):
            month = MONTHS.get(groups["mon"].lower().rstrip("."))
            if not month:
                continue
            built = _build(
                groups["y"],
                month,
                groups["d"],
                groups.get("H"),
                groups.get("M"),
            )
        else:
            built = _build(
                groups["y"],
                groups["m"],
                groups["d"],
                groups.get("H"),
                groups.get("M"),
            )
        if built:
            return built

    relative = _RELATIVE.search(text)
    if relative:
        data = relative.groupdict()
        count = data["n"] or data["n2"] or data["n3"]
        unit = (data["unit"] or data["unit2"] or data["unit3"] or "").lower()
        seconds = _UNIT_SECONDS.get(unit)
        if count and seconds:
            reference = now or datetime.now(timezone.utc)
            return reference - timedelta(seconds=int(count) * seconds)

    if re.search(r"\b(aujourd'hui|today)\b", text, re.IGNORECASE):
        return now or datetime.now(timezone.utc)
    if re.search(r"\b(hier|yesterday)\b", text, re.IGNORECASE):
        return (now or datetime.now(timezone.utc)) - timedelta(days=1)
    return None


def to_rfc822(value):
    """Formate un `datetime` pour `<pubDate>`."""
    if value is None:
        return None
    if not value.tzinfo:
        value = value.replace(tzinfo=timezone.utc)
    return format_datetime(value)


def to_rfc3339(value):
    """Formate un `datetime` pour Atom / JSON Feed."""
    if value is None:
        return None
    if not value.tzinfo:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
