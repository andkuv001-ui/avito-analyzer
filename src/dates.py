"""Парсер русских форматов дат Avito: «сегодня в 12:35», «вчера в 12:26», «7 сентября в 13:27»."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

NO_FRESHNESS = "[НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ОЦЕНКИ СВЕЖЕСТИ]"

_MONTH_PREFIXES = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "ма": 5,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "сент": 9, "окт": 10, "ноя": 11,
    "дек": 12,
}

_WS_RE = re.compile(r"\s+")
_RE_TODAY = re.compile(r"^сегодня(?:\s+в)?(?:\s+(\d{1,2}):(\d{2}))?$")
_RE_YESTERDAY = re.compile(r"^вчера(?:\s+в)?(?:\s+(\d{1,2}):(\d{2}))?$")
_RE_HUMAN = re.compile(r"^(\d{1,2})\s+([а-яё]+)(?:\s+в)?(?:\s+(\d{1,2}):(\d{2}))?$")
_RE_NUMERIC_DMY = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})(?:[ ,]+(\d{1,2}):(\d{2}))?$")
_RE_NUMERIC_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::\d{2})?)?$")


def normalize(text: str) -> str:
    text = str(text).strip().lower().replace("ё", "е")
    return _WS_RE.sub(" ", text)


def _month_num(token: str) -> int | None:
    token = token.replace("ё", "е")
    if token.startswith("сент"):
        return 9
    for pref, num in _MONTH_PREFIXES.items():
        if token.startswith(pref):
            return num
    return None


def _resolve_year(d: date, reference_now: datetime) -> date:
    """Без года: берём текущий год; если дата вышла в будущем — предыдущий."""
    try:
        candidate = d.replace(year=reference_now.year)
    except ValueError:
        candidate = date(reference_now.year, d.month, 28)
    if candidate > reference_now.date():
        try:
            candidate = candidate.replace(year=reference_now.year - 1)
        except ValueError:
            candidate = date(reference_now.year - 1, d.month, 28)
    return candidate


def parse_published(raw, reference_now: datetime | None = None) -> datetime | None:
    """Возвращает datetime публикации или None, если дата не распознана."""
    if raw is None:
        return None
    reference_now = reference_now or datetime.now()

    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, date):
        return datetime.combine(raw, datetime.min.time())

    text = normalize(raw)
    if not text or text in ("nan", "none", "nat", "-", "—"):
        return None

    m = _RE_TODAY.match(text)
    if m:
        h, mi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (0, 0)
        return reference_now.replace(hour=h, minute=mi, second=0, microsecond=0)

    m = _RE_YESTERDAY.match(text)
    if m:
        y = reference_now.date() - timedelta(days=1)
        h, mi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (0, 0)
        return datetime.combine(y, datetime.min.time()).replace(hour=h, minute=mi)

    m = _RE_NUMERIC_ISO.match(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h, mi = int(m.group(4) or 0), int(m.group(5) or 0)
        try:
            return datetime(y, mo, d, h, mi)
        except ValueError:
            return None

    m = _RE_NUMERIC_DMY.match(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h, mi = int(m.group(4) or 0), int(m.group(5) or 0)
        try:
            return datetime(y, mo, d, h, mi)
        except ValueError:
            return None

    m = _RE_HUMAN.match(text)
    if m:
        day = int(m.group(1))
        month = _month_num(m.group(2))
        if month is None or not (1 <= day <= 31):
            return None
        h, mi = int(m.group(3) or 0), int(m.group(4) or 0)
        resolved = _resolve_year(date(2000, month, day), reference_now)
        try:
            return datetime.combine(resolved, datetime.min.time()).replace(hour=h, minute=mi)
        except ValueError:
            return None

    return None
