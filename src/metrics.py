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


NO_DATA = "[НЕТ ДАННЫХ]"

# Стабильные имена фактов: промпт и валидация Grounding ссылаются на них по имени.
FACT_NAMES = (
    "объём", "классы", "нулевые просмотры", "концентрация топ-10",
    "promo-эффект", "свежие vs старые", "цена vs спрос", "продавцы",
    "кластеры без свежих", "B2B-доля", "топ-5 запросов",
)


def _fmt_int(value: int) -> str:
    """Разряды через пробел — канонический формат чисел в дайджесте."""
    return f"{value:,}".replace(",", " ")


def _fmt_value(value) -> str:
    return NO_DATA if value is None else str(value)


def _pct(part: int, whole: int) -> str:
    """Доля в процентах; знаменатель ≤0 не делится (вызывающий обязан проверить базу)."""
    return f"{part / whole * 100:.1f}"


def _ratio(numerator, denominator) -> str:
    if numerator is None or denominator is None or denominator == 0:
        return NO_DATA
    return f"×{numerator / denominator:.1f}"


def build_fact_pack(listings: list[dict], summary: dict,
                    classifications: dict[int, dict] | None) -> list[str]:
    """Детерминированные факты для секции «Ключевые факты» дайджеста.

    Правила пустых баз (без исключений и без деления на 0):
    - метрика с пустой базой (count == 0, пустой список, знаменатель 0) → [НЕТ ДАННЫХ];
    - проценты — только при знаменателе > 0; топ-N — по min(N, len(...));
    - classifications is None → факты «кластеры без свежих», «B2B-доля», «топ-5 запросов»;
    - listings == [] → все факты [НЕТ ДАННЫХ];
    - «нет данных» ≠ 0: views_today = 0 — реальный факт, views_today = None — без данных.
    """
    if not listings:
        return [f"[{name}] {NO_DATA}" for name in FACT_NAMES]

    total = len(listings)
    facts: dict[str, str] = {}

    with_date = sum(1 for l in listings if l.get("published_at") is not None)
    facts["объём"] = (
        f"всего {total}; с датой публикации {with_date}; без даты {total - with_date}"
    )

    counts = summary["class_counts"]
    stale_ages = [l["age_days"] for l in listings
                  if l.get("freshness_class") == STALE_LABEL and l.get("age_days") is not None]
    prolonged = sum(1 for age in stale_ages if age <= 7)
    facts["классы"] = (
        f"свежее {counts.get(FRESH_LABEL, 0)} / старое {counts.get(STALE_LABEL, 0)} / "
        f"без оценки {counts.get(NO_FRESHNESS, 0)}; медиана возраста старых "
        f"{_fmt_value(_median(stale_ages))} дн.; продлённых (старое при возрасте ≤7 дн.) "
        f"{prolonged}"
    )

    today_values = [l["views_today"] for l in listings if l.get("views_today") is not None]
    without_views = total - len(today_values)
    zeros = sum(1 for v in today_values if v == 0)
    zero_part = (
        f"с 0 просмотров сегодня — {zeros} ({_pct(zeros, len(today_values))}%)"
        if today_values else f"с 0 просмотров сегодня — {NO_DATA}"
    )
    facts["нулевые просмотры"] = (
        f"суммарно за сегодня {_fmt_int(sum(today_values)) if today_values else NO_DATA}; "
        f"{zero_part}; без данных о «Просмотров сегодня» — {without_views}"
    )

    today_sorted = sorted(today_values, reverse=True)
    top_sum = sum(today_sorted[:10])
    all_sum = sum(today_sorted)
    facts["концентрация топ-10"] = (
        NO_DATA if not today_sorted or all_sum == 0 else
        f"топ-{len(today_sorted[:10])} по «Просмотров сегодня» держат "
        f"{_pct(top_sum, all_sum)}% суммарных сегодняшних "
        f"({_fmt_int(top_sum)} из {_fmt_int(all_sum)})"
    )

    promo = [l for l in listings if l.get("has_paid_services")]
    plain = [l for l in listings if not l.get("has_paid_services")]
    promo_median = _median([l["views_today"] for l in promo if l.get("views_today") is not None])
    plain_median = _median([l["views_today"] for l in plain if l.get("views_today") is not None])
    facts["promo-эффект"] = (
        f"медиана «Просмотров сегодня» с платными услугами — "
        f"{_fmt_value(promo_median)} ({len(promo)} шт.), без — "
        f"{_fmt_value(plain_median)} ({len(plain)} шт.), отношение "
        f"{_ratio(promo_median, plain_median)}"
    )

    fvs = summary["fresh_vs_stale"]
    fresh_stats = fvs.get(FRESH_LABEL, {})
    stale_stats = fvs.get(STALE_LABEL, {})
    facts["свежие vs старые"] = (
        f"медиана «Просмотров сегодня»: свежее — "
        f"{_fmt_value(fresh_stats.get('views_today_median'))} "
        f"({fresh_stats.get('count', 0)} шт.), старое — "
        f"{_fmt_value(stale_stats.get('views_today_median'))} "
        f"({stale_stats.get('count', 0)} шт.), отношение "
        f"{_ratio(fresh_stats.get('views_today_median'), stale_stats.get('views_today_median'))}"
    )

    priced = [l for l in listings if l.get("price") is not None]
    if priced:
        prices = [l["price"] for l in priced]
        price_median = statistics.median(prices)
        price_part = (
            f"медиана цены {_fmt_int(round(price_median))}, коридор "
            f"{_fmt_int(round(min(prices)))}–{_fmt_int(round(max(prices)))}"
        )
        cheap = _median([l["views_today"] for l in priced
                         if l["price"] <= price_median and l.get("views_today") is not None])
        rich = _median([l["views_today"] for l in priced
                        if l["price"] > price_median and l.get("views_today") is not None])
        half_part = (
            f"медиана «Просмотров сегодня» дешёвой половины — {_fmt_value(cheap)}, "
            f"дорогой — {_fmt_value(rich)}, отношение {_ratio(cheap, rich)}"
        )
    else:
        price_part = NO_DATA
        half_part = (
            f"медиана «Просмотров сегодня» дешёвой половины — {NO_DATA}, "
            f"дорогой — {NO_DATA}, отношение {NO_DATA}"
        )
    facts["цена vs спрос"] = (
        f"{price_part}; «договорная» — {summary['price_negotiable']}; {half_part}"
    )

    sellers = summary["sellers"]
    if not sellers:
        facts["продавцы"] = NO_DATA
    else:
        top3_n = sum(s["listings_in_serp"] for s in sellers[:3])
        facts["продавцы"] = (
            f"топ-{min(3, len(sellers))} продавцов — {top3_n} из {total} объявлений "
            f"({_pct(top3_n, total)}%); с >1 объявлением — "
            f"{sum(1 for s in sellers if s['listings_in_serp'] > 1)}; "
            f"с платными услугами — {sum(1 for s in sellers if s['has_paid_services'])}"
        )

    if classifications is None:
        facts["кластеры без свежих"] = NO_DATA
        facts["B2B-доля"] = NO_DATA
        facts["топ-5 запросов"] = NO_DATA
    else:
        per_tag: dict[str, list[int]] = {}
        for l in listings:
            for cluster in (classifications.get(l["n"]) or {}).get("clusters") or []:
                tag = str(cluster.get("tag") or "").strip()
                if not tag:
                    continue
                slot = per_tag.setdefault(tag, [0, 0])
                slot[0] += 1
                if l.get("freshness_class") == FRESH_LABEL:
                    slot[1] += 1
        if not per_tag:
            facts["кластеры без свежих"] = NO_DATA
        else:
            without_fresh = [f"{tag} ({slot[0]})"
                             for tag, slot in sorted(per_tag.items()) if slot[1] == 0]
            facts["кластеры без свежих"] = (
                "теги без свежих объявлений: " + (", ".join(without_fresh) if without_fresh else "нет")
            )

        b2b = sum(1 for l in listings
                  if (classifications.get(l["n"]) or {}).get("b2b") in ("B2B", "B2C+B2B"))
        facts["B2B-доля"] = f"объявлений с B2B {b2b} ({_pct(b2b, total)}%)"

        counter: Counter = Counter()
        for l in listings:
            for query in (classifications.get(l["n"]) or {}).get("queries") or []:
                query = str(query).strip()
                if query:
                    counter[query] += 1
        facts["топ-5 запросов"] = (
            ", ".join(f"{query} ({count})" for query, count in counter.most_common(5))
            if counter else NO_DATA
        )

    return [f"[{name}] {facts[name]}" for name in FACT_NAMES]


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
