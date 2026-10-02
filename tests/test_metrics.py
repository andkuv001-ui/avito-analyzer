from datetime import datetime
from pathlib import Path

import pytest

from src.dates import NO_FRESHNESS
from src.metrics import (
    FRESH_LABEL,
    STALE_LABEL,
    build_summary,
    classify_freshness,
    compute_listing_metrics,
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
