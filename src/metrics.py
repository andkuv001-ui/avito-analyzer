"""Метрики, которые считает код: бакеты свежести, views_per_day, промо, продавцы, сводки."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime

from .dates import NO_FRESHNESS

FRESHNESS_BUCKETS = [
    ("0–1", 0, 1),
    ("2–3", 2, 3),
    ("4–7", 4, 7),
    ("8–30", 8, 30),
    ("31+", 31, None),
]
BUCKET_LABELS = [b[0] for b in FRESHNESS_BUCKETS]

PROMO_KEYWORDS = (
    "поднят", "подним", "продвиж", "витрин", "приоритет", "выдел",
    "подсвет", "турбо", "реклам", "акци", "ускор", "топ-",
)


def bucket_for_age(age_days: int | None) -> str:
    if age_days is None:
        return NO_FRESHNESS
    for label, lo, hi in FRESHNESS_BUCKETS:
        if age_days >= lo and (hi is None or age_days <= hi):
            return label
    return NO_FRESHNESS


def _promo_flags(paid_services: str | None) -> list[str]:
    if not paid_services:
        return []
    text = paid_services.lower()
    return [kw.rstrip("-") for kw in PROMO_KEYWORDS if kw in text]


def compute_listing_metrics(record: dict, reference_now: datetime) -> dict:
    out = dict(record)
    published = record.get("published_at")
    if published is None:
        out["age_days"] = None
        out["freshness_bucket"] = NO_FRESHNESS
        out["freshness_note"] = NO_FRESHNESS
    else:
        out["freshness_note"] = None
        age = (reference_now.date() - published.date()).days
        out["age_days"] = max(age, 0)
        out["freshness_bucket"] = bucket_for_age(out["age_days"])

    # views_per_day не считаем: «Дата публикации» может быть датой продления,
    # а «Всего просмотров» накапливается за всё время жизни объявления.
    total = record.get("views_total")
    today = record.get("views_today")
    if total and today is not None and total > 0:
        out["views_today_share"] = round(today / total * 100, 1)
    else:
        out["views_today_share"] = None

    out["promo_flags"] = _promo_flags(record.get("paid_services"))
    out["has_paid_services"] = bool(record.get("paid_services"))
    return out


def _price_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values)),
        "median": round(statistics.median(values)),
        "max": round(max(values)),
    }


def build_summary(listings: list[dict]) -> dict:
    """listings — записи после compute_listing_metrics."""
    bucket_counts = Counter(l["freshness_bucket"] for l in listings)

    price_by_bucket: dict[str, list[float]] = {label: [] for label in BUCKET_LABELS}
    for l in listings:
        if l.get("price") is not None and l["freshness_bucket"] in price_by_bucket:
            price_by_bucket[l["freshness_bucket"]].append(l["price"])

    prices = [l["price"] for l in listings if l.get("price") is not None]
    negotiable = sum(1 for l in listings if l.get("price_negotiable"))
    ranges = sum(1 for l in listings if l.get("price_range"))

    promo_counter = Counter(
        l["paid_services"].strip() for l in listings if l.get("paid_services")
    )

    cat1 = Counter()
    cat2 = Counter()
    for l in listings:
        cats = l.get("categories") or []
        if cats:
            cat1[cats[0]] += 1
        if len(cats) > 1:
            cat2[f"{cats[0]} / {cats[1]}"] += 1

    sellers: dict[str, dict] = {}
    for l in listings:
        key = l.get("seller_url") or l.get("seller_name") or "[БЕЗ ИМЕНИ]"
        entry = sellers.setdefault(key, {
            "name": l.get("seller_name") or key,
            "listings_in_serp": 0,
            "total_listings": l.get("seller_listings"),
            "rating": l.get("rating"),
            "reviews": l.get("reviews"),
            "docs_verified": bool(l.get("docs_verified")),
            "has_paid_services": False,
            "titles": [],
        })
        entry["listings_in_serp"] += 1
        entry["has_paid_services"] = entry["has_paid_services"] or bool(l.get("paid_services"))
        if l.get("title"):
            entry["titles"].append(l["title"])
        if entry["total_listings"] is None and l.get("seller_listings") is not None:
            entry["total_listings"] = l["seller_listings"]

    seller_list = sorted(sellers.values(), key=lambda s: -s["listings_in_serp"])

    views_today_top = sorted(
        ({"n": l["n"], "title": l.get("title"),
          "views_today": l.get("views_today"), "views_total": l.get("views_total")}
         for l in listings if l.get("views_today") is not None),
        key=lambda x: -x["views_today"],
    )[:10]

    return {
        "total": len(listings),
        "bucket_counts": {label: bucket_counts.get(label, 0) for label in BUCKET_LABELS}
        | {NO_FRESHNESS: bucket_counts.get(NO_FRESHNESS, 0)},
        "price_overall": _price_stats(prices),
        "price_by_bucket": {label: _price_stats(vals) for label, vals in price_by_bucket.items()},
        "price_negotiable": negotiable,
        "price_range": ranges,
        "promo_top": promo_counter.most_common(10),
        "promo_used": sum(1 for l in listings if l.get("has_paid_services")),
        "category_1": cat1.most_common(15),
        "category_2": cat2.most_common(15),
        "sellers": seller_list,
        "unique_sellers": len(sellers),
        "docs_verified": sum(1 for l in listings if l.get("docs_verified")),
        "views_today_top": views_today_top,
    }
