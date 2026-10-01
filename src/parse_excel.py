"""Чтение Excel-выгрузки Avito → нормализованные записи + warnings."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .columns import CORE_COLUMNS, ignored_columns, map_columns, missing_core_warnings
from .dates import NO_FRESHNESS, parse_published

NEGOTIABLE_RE = re.compile(r"договор|по запросу|уточняйте", re.IGNORECASE)
RANGE_RE = re.compile(r"\d[\d\s  ]*\s*[-–—]\s*\d")

NAN_STRINGS = {"", "nan", "none", "nat", "n/a", "-"}


def _clean(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text and text.lower() not in NAN_STRINGS else None
    return str(value)


def _to_number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = _clean(value)
    if text is None:
        return None
    digits = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", digits)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_price(raw):
    """→ (числовая цена | None, {'negotiable': bool, 'range': bool})."""
    flags = {"negotiable": False, "range": False}
    if raw is None:
        return None, flags
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if isinstance(raw, float) and math.isnan(raw):
            return None, flags
        return float(raw), flags

    text = _clean(raw)
    if text is None:
        return None, flags
    if NEGOTIABLE_RE.search(text):
        flags["negotiable"] = True
    if RANGE_RE.search(text):
        flags["range"] = True
    return _to_number(text), flags


def _to_int(value) -> int | None:
    num = _to_number(value)
    return int(num) if num is not None else None


def _is_verified(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("nan", "none", ""):
        return False
    return text in ("да", "yes", "true", "1", "проверено", "проверены", "есть") or "документ" in text


def parse_workbook(path: str | Path, reference_now: datetime | None = None) -> dict:
    """→ {'records': [...], 'warnings': [...], 'mapping': {...}, 'ignored': [...], 'total_columns': int}"""
    reference_now = reference_now or datetime.now()
    frame = pd.read_excel(path, dtype=object)
    columns = list(frame.columns)
    mapping = map_columns(columns)
    ignored = ignored_columns(columns, mapping)

    warnings = missing_core_warnings(mapping)
    if not mapping:
        warnings.append("Не найдено ни одной ядерной колонки — файл не похож на выгрузку Avito")
    if ignored:
        warnings.append(f"Проигнорировано товарных колонок: {len(ignored)}")

    records = []
    for idx, row in frame.iterrows():
        def get(key):
            col = mapping.get(key)
            return _clean(row[col]) if col is not None else None

        published_raw = get("published_at")
        published_dt = parse_published(published_raw, reference_now)
        price, price_flags = parse_price(row[mapping["price"]] if "price" in mapping else None)

        categories = [get(f"category_{i}") for i in range(1, 7)]
        categories = [c for c in categories if c]

        record = {
            "n": len(records) + 1,
            "title": get("title"),
            "price": price,
            "price_raw": get("price"),
            "price_negotiable": price_flags["negotiable"],
            "price_range": price_flags["range"],
            "description": get("description"),
            "url": get("url"),
            "avito_id": get("avito_id"),
            "published_raw": published_raw,
            "published_at": published_dt,
            "published_display": published_raw or "[НЕТ ДАННЫХ]",
            "freshness_note": NO_FRESHNESS if published_dt is None else None,
            "position": _to_int(row[mapping["position"]]) if "position" in mapping else None,
            "views_today": _to_int(row[mapping["views_today"]]) if "views_today" in mapping else None,
            "views_total": _to_int(row[mapping["views_total"]]) if "views_total" in mapping else None,
            "paid_services": get("paid_services"),
            "address": get("address"),
            "seller_name": get("seller_name"),
            "seller_url": get("seller_url"),
            "docs_verified": _is_verified(row[mapping["docs_verified"]]) if "docs_verified" in mapping else False,
            "seller_listings": _to_int(row[mapping["seller_listings"]]) if "seller_listings" in mapping else None,
            "rating": _to_number(row[mapping["rating"]]) if "rating" in mapping else None,
            "reviews": _to_int(row[mapping["reviews"]]) if "reviews" in mapping else None,
            "categories": categories,
        }
        records.append(record)

    return {
        "records": records,
        "warnings": warnings,
        "mapping": mapping,
        "ignored": ignored,
        "total_columns": len(columns),
        "core_keys": list(CORE_COLUMNS.keys()),
    }
