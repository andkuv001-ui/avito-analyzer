from datetime import datetime
from pathlib import Path

import pytest

from src.dates import NO_FRESHNESS
from src.metrics import (
    FACT_NAMES,
    FRESH_LABEL,
    SEGMENT_LABELS,
    STALE_LABEL,
    build_fact_pack,
    build_summary,
    classify_freshness,
    cluster_price_stats,
    compute_listing_metrics,
    price_segments,
)
from src.parse_excel import parse_price, parse_workbook

REF = datetime(2026, 10, 1, 18, 0)
FIXTURE = Path(__file__).parent / "fixtures" / "sample.xlsx"


def _listing(**overrides):
    base = {
        "n": 1, "title": "т", "price": None, "price_raw": None,
        "price_negotiable": False, "price_range": False,
        "description": "", "published_at": None, "published_display": "—",
        "freshness_note": None, "views_today": None, "views_total": None,
        "paid_services": None, "seller_name": "S", "seller_url": None,
        "docs_verified": False, "seller_listings": None, "rating": None,
        "reviews": None, "categories": [], "position": None,
    }
    base.update(overrides)
    return base


def test_stale_unconditionally_after_7_days():
    klass, _ = classify_freshness(datetime(2026, 9, 1), 30, 5, 500)
    assert klass == STALE_LABEL
    klass, _ = classify_freshness(datetime(2026, 8, 1), 60, None, None)
    assert klass == STALE_LABEL


def test_fresh_confirmed_by_views_correlation():
    # возраст 1 дн., «сегодня» 12, «всего» 96 ≈ темп за 2 дня — свежее
    klass, basis = classify_freshness(datetime(2026, 9, 30), 1, 12, 96)
    assert klass == FRESH_LABEL
    assert "свежее" in basis


def test_fresh_date_but_accumulated_views_is_stale():
    # дата «сегодня», но «всего» 812 при «сегодня» 45 ≈ 18 дн. набора — продлённое
    klass, basis = classify_freshness(datetime(2026, 10, 1), 0, 45, 812)
    assert klass == STALE_LABEL
    assert "продлённое" in basis


def test_no_date_is_insufficient():
    klass, _ = classify_freshness(None, None, 10, 100)
    assert klass == NO_FRESHNESS


def test_young_date_without_view_link_is_insufficient():
    klass, _ = classify_freshness(datetime(2026, 9, 30), 1, None, 5000)
    assert klass == NO_FRESHNESS
    klass, _ = classify_freshness(datetime(2026, 9, 30), 1, 0, 5000)
    assert klass == NO_FRESHNESS


def test_compute_sets_class_and_share():
    published = datetime(2026, 10, 1, 12, 0)
    m = compute_listing_metrics(
        _listing(published_at=published, views_total=50, views_today=10), REF
    )
    assert m["age_days"] == 0
    assert m["freshness_class"] == FRESH_LABEL
    assert "views_per_day" not in m
    assert m["views_today_share"] == 20.0


def test_missing_date_marks_note():
    m = compute_listing_metrics(_listing(), REF)
    assert m["age_days"] is None
    assert m["freshness_class"] == NO_FRESHNESS
    assert m["freshness_note"] == NO_FRESHNESS


def test_promo_flags():
    m = compute_listing_metrics(_listing(paid_services="Поднятие в выдаче, Витрина"), REF)
    assert m["has_paid_services"] is True
    assert "поднят" in m["promo_flags"]
    assert "витрин" in m["promo_flags"]
    m2 = compute_listing_metrics(_listing(paid_services=None), REF)
    assert m2["has_paid_services"] is False
    assert m2["promo_flags"] == []


def test_price_parsing():
    assert parse_price(1200) == (1200.0, {"negotiable": False, "range": False})
    price, flags = parse_price("1 200 ₽")
    assert price == 1200 and not flags["range"]
    price, flags = parse_price("600 - 900 ₽")
    assert price == 600 and flags["range"]
    price, flags = parse_price("Договорная")
    assert price is None and flags["negotiable"]


def test_summary_fresh_vs_stale():
    listings = [
        compute_listing_metrics(_listing(
            published_at=datetime(2026, 10, 1), views_today=10, views_total=20,
            price=100.0, categories=["Кактусы"], seller_name="A"), REF),
        compute_listing_metrics(_listing(
            published_at=datetime(2026, 8, 1), views_today=1, views_total=5000,
            price=200.0, categories=["Суккуленты"], seller_name="A",
            paid_services="Витрина"), REF),
        compute_listing_metrics(_listing(seller_name="B"), REF),
    ]
    summary = build_summary(listings)
    assert summary["total"] == 3
    assert sum(summary["class_counts"].values()) == 3
    assert summary["class_counts"][FRESH_LABEL] == 1
    assert summary["class_counts"][STALE_LABEL] == 1
    assert summary["class_counts"][NO_FRESHNESS] == 1
    fvs = summary["fresh_vs_stale"]
    assert fvs[FRESH_LABEL]["count"] == 1
    assert fvs[FRESH_LABEL]["views_today_median"] == 10
    assert fvs[STALE_LABEL]["count"] == 1
    assert summary["views_today_top"][0]["views_today"] == 10
    assert summary["unique_sellers"] == 2
    assert summary["sellers"][0]["listings_in_serp"] == 2


def test_fixture_parse_and_metrics():
    parsed = parse_workbook(FIXTURE, REF)
    records = parsed["records"]
    assert len(records) == 12
    assert len(parsed["ignored"]) == 3
    assert parsed["parsed_at"] is not None
    assert not any("Нет колонки" in w for w in parsed["warnings"])

    listings = [compute_listing_metrics(r, REF) for r in records]
    by_id = {l["avito_id"]: l for l in listings}

    assert by_id["2891234567"]["age_days"] == 0
    # «сегодня», но 812 «Всего» при 45 «Сегодня» → продлённое старое
    assert by_id["2891234567"]["freshness_class"] == STALE_LABEL
    assert by_id["2891234568"]["freshness_class"] == FRESH_LABEL   # 1 дн., 96/12
    assert by_id["2891234569"]["freshness_class"] == STALE_LABEL   # 24 дн.
    assert by_id["2891234570"]["age_days"] == 31
    assert by_id["2891234570"]["freshness_class"] == STALE_LABEL
    assert by_id["2891234577"]["published_at"] is None
    assert by_id["2891234577"]["freshness_class"] == NO_FRESHNESS
    assert by_id["2891234576"]["published_at"].year == 2025
    assert by_id["2891234576"]["freshness_class"] == STALE_LABEL
    assert by_id["2891234578"]["freshness_class"] == FRESH_LABEL   # 210/60

    summary = build_summary(listings)
    assert summary["total"] == 12
    assert sum(summary["class_counts"].values()) == 12
    assert summary["class_counts"][NO_FRESHNESS] == 1


def test_fixture_without_date_column():
    import io

    import pandas as pd

    frame = pd.read_excel(FIXTURE)
    frame = frame.drop(columns=["Дата публикации"])
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    buffer.seek(0)

    parsed = parse_workbook(buffer, REF)
    assert any("Дата публикации" in w for w in parsed["warnings"])
    listings = [compute_listing_metrics(r, REF) for r in parsed["records"]]
    assert all(l["freshness_class"] == NO_FRESHNESS for l in listings)
    summary = build_summary(listings)
    assert summary["class_counts"][NO_FRESHNESS] == 12


# --- build_fact_pack -------------------------------------------------------

NO_DATA = "[НЕТ ДАННЫХ]"


def _fact_listings():
    return [
        # свежее
        compute_listing_metrics(_listing(
            n=1, published_at=datetime(2026, 10, 1), views_today=10, views_total=20,
            price=100.0, seller_name="A"), REF),
        # старое (возраст > 7 дней)
        compute_listing_metrics(_listing(
            n=2, published_at=datetime(2026, 8, 1), views_today=0, views_total=5000,
            price=200.0, seller_name="A", paid_services="Витрина"), REF),
        # без даты, без просмотров, без цены
        compute_listing_metrics(_listing(n=3, seller_name="B"), REF),
        # продлённое: дата ≤7 дн., накопленные просмотры
        compute_listing_metrics(_listing(
            n=4, published_at=datetime(2026, 9, 28), views_today=5, views_total=1000,
            price=300.0, seller_name="B"), REF),
    ]


def _fact_classifications():
    return {
        1: {"b2b": "B2C", "clusters": [{"kind": "товарный", "tag": "мох"}],
            "queries": ["мох купить", "живой мох"], "usp": None, "scenario": None},
        2: {"b2b": "B2B", "clusters": [{"kind": "коммерческий", "tag": "опт"}],
            "queries": ["мох оптом"], "usp": None, "scenario": None},
        3: {"b2b": "B2C", "clusters": [], "queries": [], "usp": None, "scenario": None},
        4: {"b2b": "B2C", "clusters": [{"kind": "товарный", "tag": "мох"},
                                       {"kind": "сценарный", "tag": "подарок"}],
            "queries": [], "usp": None, "scenario": None},
    }


def _facts(listings, classifications=None):
    return build_fact_pack(listings, build_summary(listings), classifications)


def _fact(lines: list[str], name: str) -> str:
    prefix = f"[{name}] "
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):]
    raise AssertionError(f"нет факта «{name}» в {lines}")


def test_fact_pack_empty_listings_all_no_data():
    facts = build_fact_pack([], build_summary([]), None)
    assert len(facts) == len(FACT_NAMES)
    assert [line.split("]")[0][1:] for line in facts] == list(FACT_NAMES)
    assert all(line.endswith(NO_DATA) for line in facts)


def test_fact_pack_volume_and_classes():
    facts = _facts(_fact_listings(), _fact_classifications())
    assert _fact(facts, "объём") == "всего 4; с датой публикации 3; без даты 1"
    classes = _fact(facts, "классы")
    assert "свежее 1 / старое 2 / без оценки 1" in classes
    assert "медиана возраста старых 32" in classes
    assert "продлённых (старое при возрасте ≤7 дн.) 1" in classes


def test_fact_pack_zero_views():
    facts = _facts(_fact_listings(), _fact_classifications())
    zero = _fact(facts, "нулевые просмотры")
    assert "суммарно за сегодня 15" in zero
    assert "с 0 просмотров сегодня — 1 (33.3%)" in zero
    assert "без данных о «Просмотров сегодня» — 1" in zero


def test_fact_pack_zero_views_is_not_missing_data():
    listings = [
        compute_listing_metrics(_listing(n=1, views_today=0, views_total=100), REF),
        compute_listing_metrics(_listing(n=2), REF),
    ]
    zero = _fact(_facts(listings), "нулевые просмотры")
    assert "суммарно за сегодня 0" in zero
    assert "с 0 просмотров сегодня — 1 (100.0%)" in zero
    assert "без данных о «Просмотров сегодня» — 1" in zero
    # 0 просмотров при знаменателе 0 → концентрация не считается
    assert _fact(_facts(listings), "концентрация топ-10") == NO_DATA


def test_fact_pack_top10_concentration():
    facts = _facts(_fact_listings(), _fact_classifications())
    top = _fact(facts, "концентрация топ-10")
    assert "топ-3 по «Просмотров сегодня» держат 100.0% суммарных сегодняшних (15 из 15)" in top


def test_fact_pack_promo_effect():
    facts = _facts(_fact_listings(), _fact_classifications())
    promo = _fact(facts, "promo-эффект")
    assert "по всей выдаче: медиана «Просмотров сегодня» с платными услугами — 0 (1 шт.)" in promo
    assert "без — 7.5 (3 шт.)" in promo
    assert "отношение ×0.0" in promo
    # база <9 с ценой → сегменты не считаются
    assert "внутри сегментов: дешёвые — [НЕТ ДАННЫХ]" in promo
    assert "средние — [НЕТ ДАННЫХ]" in promo
    assert "дорогие — [НЕТ ДАННЫХ]" in promo


def test_fact_pack_fresh_vs_stale():
    facts = _facts(_fact_listings(), _fact_classifications())
    fvs = _fact(facts, "свежие vs старые")
    assert "свежее — 10 (1 шт.)" in fvs
    assert "старое — 2.5 (2 шт.)" in fvs
    assert "отношение ×4.0" in fvs
    assert "внутри ценовых сегментов: дешёвые — [НЕТ ДАННЫХ]" in fvs


def test_fact_pack_price_vs_demand():
    facts = _facts(_fact_listings(), _fact_classifications())
    price = _fact(facts, "цена vs спрос")
    assert "медиана цены 200, коридор 100–300" in price
    assert "сегменты не считаются — с числовой ценой 3 (<9)" in price
    assert "дешёвые — [НЕТ ДАННЫХ], средние — [НЕТ ДАННЫХ], дорогие — [НЕТ ДАННЫХ]" in price
    assert "«договорная» — 0" in price
    assert "без числовой цены — 1" in price


def test_fact_pack_top3_sellers():
    facts = _facts(_fact_listings(), _fact_classifications())
    sellers = _fact(facts, "продавцы")
    assert "топ-2 продавцов — 4 из 4 объявлений (100.0%)" in sellers
    assert "с >1 объявлением — 2" in sellers
    assert "с платными услугами — 1" in sellers


def test_fact_pack_clusters_without_fresh_name_and_content():
    facts = _facts(_fact_listings(), _fact_classifications())
    assert facts[8].startswith("[кластеры без свежих] ")
    clusters = _fact(facts, "кластеры без свежих")
    assert "теги без свежих объявлений: опт (1), подарок (1)" in clusters


def test_fact_pack_b2b_and_top_queries():
    facts = _facts(_fact_listings(), _fact_classifications())
    assert _fact(facts, "B2B-доля") == "объявлений с B2B 1 (25.0%)"
    assert _fact(facts, "топ-5 запросов") == "мох купить (1), живой мох (1), мох оптом (1)"


def test_fact_pack_without_classifications():
    facts = _facts(_fact_listings(), None)
    for name in ("кластеры без свежих", "B2B-доля", "топ-5 запросов", "цены по кластерам"):
        assert _fact(facts, name) == NO_DATA
    assert _fact(facts, "объём") == "всего 4; с датой публикации 3; без даты 1"


def test_fact_pack_empty_bases():
    listings = [
        compute_listing_metrics(_listing(n=1), REF),
        compute_listing_metrics(_listing(n=2), REF),
    ]
    facts = _facts(listings, {})
    assert _fact(facts, "концентрация топ-10") == NO_DATA
    assert NO_DATA in _fact(facts, "нулевые просмотры")
    assert NO_DATA in _fact(facts, "promo-эффект")
    assert NO_DATA in _fact(facts, "свежие vs старые")
    assert NO_DATA in _fact(facts, "цена vs спрос")
    assert "«договорная» — 0" in _fact(facts, "цена vs спрос")
    assert "без числовой цены — 2" in _fact(facts, "цена vs спрос")
    assert _fact(facts, "кластеры без свежих") == NO_DATA
    assert _fact(facts, "топ-5 запросов") == NO_DATA
    assert _fact(facts, "цены по кластерам") == NO_DATA
    assert _fact(facts, "B2B-доля") == "объявлений с B2B 0 (0.0%)"


# --- ценовые сегменты ------------------------------------------------------

def _segment_listings(count: int = 9):
    out = []
    for i in range(count):
        views = (i + 1) * 10
        fresh = i % 2 == 0
        out.append(compute_listing_metrics(_listing(
            n=i + 1, price=float((i + 1) * 100), views_today=views,
            published_at=datetime(2026, 10, 1) if fresh else datetime(2026, 8, 1),
            views_total=views * 2 if fresh else 5000), REF))
    return out


def test_price_segments_terciles():
    segs = price_segments(_segment_listings())
    assert segs["границы"] == (400.0, 700.0)
    assert [l["n"] for l in segs["дешёвые"]] == [1, 2, 3, 4]
    assert [l["n"] for l in segs["средние"]] == [5, 6, 7]
    assert [l["n"] for l in segs["дорогие"]] == [8, 9]
    assert segs["без цены"] == 0
    p33, p66 = segs["границы"]
    assert all(l["price"] <= p33 for l in segs["дешёвые"])
    assert all(p33 < l["price"] <= p66 for l in segs["средние"])
    assert all(l["price"] > p66 for l in segs["дорогие"])


def test_price_segments_small_base_no_segments():
    segs = price_segments(_segment_listings()[:8])
    assert segs["границы"] is None
    for label in SEGMENT_LABELS:
        assert segs[label] == []
    assert segs["без цены"] == 0


def test_price_segments_negotiable_and_missing_price_outside():
    listings = _segment_listings() + [
        compute_listing_metrics(_listing(n=10, price=None, price_negotiable=True), REF),
        compute_listing_metrics(_listing(n=11, price=None, price_raw="Договорная"), REF),
    ]
    segs = price_segments(listings)
    assert segs["без цены"] == 2
    inside = [l["n"] for label in SEGMENT_LABELS for l in segs[label]]
    assert len(inside) == 9
    assert 10 not in inside and 11 not in inside


def test_summary_price_segments_stats():
    ps = build_summary(_segment_listings())["price_segments"]
    assert ps["границы"] == (400.0, 700.0)
    assert ps["база"] == 9
    assert ps["без цены"] == 0
    cheap = ps["segments"]["дешёвые"]
    assert cheap["count"] == 4
    assert (cheap["price_min"], cheap["price_median"], cheap["price_max"]) == (100, 250, 400)
    assert cheap["views_today_median"] == 25
    assert cheap["promo_share"] == 0
    assert cheap["fresh"]["count"] == 2 and cheap["stale"]["count"] == 2
    assert ps["segments"]["средние"]["count"] == 3
    assert ps["segments"]["дорогие"]["count"] == 2


def test_summary_price_segments_small_base_no_data():
    ps = build_summary(_segment_listings()[:8])["price_segments"]
    assert ps["границы"] is None
    assert ps["база"] == 8
    for label in SEGMENT_LABELS:
        seg = ps["segments"][label]
        assert seg["count"] is None
        assert seg["price_min"] is None and seg["views_today_median"] is None
        assert seg["fresh"]["count"] is None and seg["stale"]["count"] is None


def test_fact_pack_price_vs_demand_with_segments():
    price = _fact(_facts(_segment_listings()), "цена vs спрос")
    assert "границы тертилей 400 / 700" in price
    assert "дешёвые — 4 шт., медиана «Просмотров сегодня» 25.0" in price
    assert "средние — 3 шт., медиана «Просмотров сегодня» 60" in price
    assert "дорогие — 2 шт., медиана «Просмотров сегодня» 85.0" in price
    assert "«договорная» — 0" in price
    assert "без числовой цены — 0" in price


def test_fact_pack_fresh_vs_stale_within_segments():
    fvs = _fact(_facts(_segment_listings()), "свежие vs старые")
    assert "дешёвые — свежее 20.0 (2 шт.), старое 30.0 (2 шт.), отношение ×0.7" in fvs
    assert "средние — свежее 60.0 (2 шт.), старое 60 (1 шт.), отношение ×1.0" in fvs
    assert "дорогие — свежее 90 (1 шт.), старое 80 (1 шт.), отношение ×1.1" in fvs


def test_fact_pack_promo_within_segments():
    listings = _segment_listings()
    listings[0]["paid_services"] = "Витрина"
    listings[0] = compute_listing_metrics(listings[0], REF)
    promo = _fact(_facts(listings), "promo-эффект")
    assert "дешёвые — с 10 (1 шт.) / без 30 (3 шт.), отношение ×0.3" in promo
    assert "средние — с [НЕТ ДАННЫХ] (0 шт.)" in promo


def test_fact_pack_cluster_prices():
    listings = _segment_listings()
    classifications = {
        i + 1: {
            "b2b": "B2C",
            "clusters": [{"kind": "товарный", "tag": "мох" if i < 6 else "кактус"}],
            "queries": [], "usp": None, "scenario": None,
        }
        for i in range(9)
    }
    assert _fact(_facts(listings, classifications), "цены по кластерам") == \
        "мох (6): 350, кактус (3): 800"


def test_cluster_price_stats_without_classifications():
    assert cluster_price_stats(_segment_listings(), None) == []
