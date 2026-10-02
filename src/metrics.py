"""Метрики, которые считает код: свежее/старое (косвенно), «Просмотров сегодня», сводки, продавцы."""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime

from .dates import NO_FRESHNESS

FRESH_LABEL = "свежее"
STALE_LABEL = "старое"
FRESHNESS_CLASSES = [FRESH_LABEL, STALE_LABEL]

# Косвенная проверка свежести: «Всего» не должно превышать сегодняшний темп
# на (возраст+1) дней с запасом ×5. «Дата публикации» — дата продления,
# истинный возраст неизвестен, поэтому свежесть только косвенная.
CORRELATION_K = 5

PROMO_KEYWORDS = (
    "поднят", "подним", "продвиж", "витрин", "приоритет", "выдел",
    "подсвет", "турбо", "реклам", "акци", "ускор", "топ-",
)


def _promo_flags(paid_services: str | None) -> list[str]:
    if not paid_services:
        return []
    text = paid_services.lower()
    return [kw.rstrip("-") for kw in PROMO_KEYWORDS if kw in text]


def classify_freshness(published, age_days: int | None,
                       views_today: int | None, views_total: int | None):
    """→ (класс, обоснование). Свежесть — только по косвенным показателям.

    - старше 7 дней по дате (пере)публикации → «старое» без проверок;
    - ≤7 дней: связка «Просмотров сегодня» ↔ «Просмотров всего»:
      если «Всего» набираются за разумное число дней при сегодняшнем темпе — «свежее»,
      иначе это продлённое старое объявление — «старое»;
    - данных мало → [НЕДОСТАТОЧНО ДАННЫХ…], свежесть не утверждаем.
    """
    if published is None or age_days is None:
        return NO_FRESHNESS, "нет «Дата публикации» — свежесть не определить"
    if age_days > 7:
        return STALE_LABEL, (
            f"{age_days} дн. с (пере)публикации (>7) — старое; истинный возраст неизвестен"
        )
    if (views_today is not None and views_today > 0 and views_total is not None):
        implied_days = views_total / views_today
        limit = (max(age_days, 1) + 1) * CORRELATION_K
        if views_total <= limit * views_today:
            return FRESH_LABEL, (
                f"дата ≤7 дн. ({age_days}), «Всего» {views_total} согласуется с сегодняшним "
                f"темпом ({views_today}) — свежее (косвенно)"
            )
        return STALE_LABEL, (
            f"дата ≤7 дн. ({age_days}), но «Всего» {views_total} при сегодняшних {views_today} "
            f"≈ {implied_days:.0f} дн. набора — похоже на продлённое старое"
        )
    return NO_FRESHNESS, (
        f"дата ≤7 дн. ({age_days}), но нет связки «Просмотров сегодня» ↔ «Всего» — "
        "свежесть по косвенным признакам не подтверждена"
    )


def compute_listing_metrics(record: dict, reference_now: datetime) -> dict:
    out = dict(record)
    published = record.get("published_at")
    age_days = None
    if published is not None:
        age_days = max((reference_now.date() - published.date()).days, 0)
    out["age_days"] = age_days

    klass, basis = classify_freshness(
        published, age_days, record.get("views_today"), record.get("views_total")
    )
    out["freshness_class"] = klass
    out["freshness_basis"] = basis
    out["freshness_note"] = NO_FRESHNESS if klass == NO_FRESHNESS else None

    total = record.get("views_total")
    today = record.get("views_today")
    if total and today is not None and total > 0:
        out["views_today_share"] = round(today / total * 100, 1)
    else:
        out["views_today_share"] = None

    out["promo_flags"] = _promo_flags(record.get("paid_services"))
    out["has_paid_services"] = bool(record.get("paid_services"))
    return out


def _median(values: list) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _price_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values)),
        "median": round(statistics.median(values)),
        "max": round(max(values)),
    }


def _class_stats(items: list[dict]) -> dict:
    today = [l["views_today"] for l in items if l.get("views_today") is not None]
    total = [l["views_total"] for l in items if l.get("views_total") is not None]
    prices = [l["price"] for l in items if l.get("price") is not None]
    return {
        "count": len(items),
        "views_today_median": _median(today),
        "views_total_median": _median(total),
        "views_today_sum": sum(today) if today else None,
        "promo_share": round(
            sum(1 for l in items if l.get("has_paid_services")) / len(items) * 100
        ) if items else None,
        "price_median": round(statistics.median(prices)) if prices else None,
    }


def build_summary(listings: list[dict]) -> dict:
    """listings — записи после compute_listing_metrics."""
    class_counts = Counter(l["freshness_class"] for l in listings)

    by_class: dict[str, list[dict]] = {label: [] for label in FRESHNESS_CLASSES}
    for l in listings:
        if l["freshness_class"] in by_class:
            by_class[l["freshness_class"]].append(l)

    price_by_class = {
        label: _price_stats([x["price"] for x in items if x.get("price") is not None])
        for label, items in by_class.items()
    }

    prices = [l["price"] for l in listings if l.get("price") is not None]
    negotiable = sum(1 for l in listings if l.get("price_negotiable"))
    ranges = sum(1 for l in listings if l.get("price_range"))

    promo_counter = Counter(
        l["paid_services"].strip() for l in listings if l.get("paid_services")
    )

    cat1 = Counter()
    for l in listings:
        cats = l.get("categories") or []
        if cats:
            cat1[cats[0]] += 1

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
            "fresh": 0,
            "stale": 0,
        })
        entry["listings_in_serp"] += 1
        entry["has_paid_services"] = entry["has_paid_services"] or bool(l.get("paid_services"))
        if l["freshness_class"] == FRESH_LABEL:
            entry["fresh"] += 1
        elif l["freshness_class"] == STALE_LABEL:
            entry["stale"] += 1
        if entry["total_listings"] is None and l.get("seller_listings") is not None:
            entry["total_listings"] = l["seller_listings"]

    seller_list = sorted(sellers.values(), key=lambda s: -s["listings_in_serp"])

    views_today_top = sorted(
        ({"n": l["n"], "title": l.get("title"),
          "views_today": l.get("views_today"), "views_total": l.get("views_total"),
          "freshness_class": l["freshness_class"]}
         for l in listings if l.get("views_today") is not None),
        key=lambda x: -x["views_today"],
    )[:10]

    return {
        "total": len(listings),
        "class_counts": {**{label: class_counts.get(label, 0) for label in FRESHNESS_CLASSES},
                         NO_FRESHNESS: class_counts.get(NO_FRESHNESS, 0)},
        "fresh_vs_stale": {label: _class_stats(items) for label, items in by_class.items()},
        "price_overall": _price_stats(prices),
        "price_by_class": price_by_class,
        "price_negotiable": negotiable,
        "price_range": ranges,
        "promo_top": promo_counter.most_common(10),
        "promo_used": sum(1 for l in listings if l.get("has_paid_services")),
        "category_1": cat1.most_common(15),
        "sellers": seller_list,
        "unique_sellers": len(sellers),
        "docs_verified": sum(1 for l in listings if l.get("docs_verified")),
        "views_today_top": views_today_top,
    }
