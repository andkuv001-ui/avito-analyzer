from datetime import datetime
from pathlib import Path

import pytest

from src.dates import NO_FRESHNESS, parse_published
from src.metrics import bucket_for_age, build_summary, compute_listing_metrics
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


@pytest.mark.parametrize("age,expected", [
    (0, "0–1"), (1, "0–1"),
    (2, "2–3"), (3, "2–3"),
    (4, "4–7"), (7, "4–7"),
    (8, "8–30"), (30, "8–30"),
    (31, "31+"), (400, "31+"),
    (None, NO_FRESHNESS),
])
def test_freshness_buckets(age, expected):
    assert bucket_for_age(age) == expected


def test_age_and_views_per_day():
    published = datetime(2026, 9, 1, 10, 0)
    m = compute_listing_metrics(_listing(published_at=published, views_total=100), REF)
    assert m["age_days"] == 30
    assert m["freshness_bucket"] == "8–30"
    assert m["views_per_day"] == pytest.approx(3.3)


def test_fresh_listing_uses_full_day_divisor():
    published = datetime(2026, 10, 1, 12, 0)
    m = compute_listing_metrics(_listing(published_at=published, views_total=50), REF)
    assert m["age_days"] == 0
    assert m["views_per_day"] == 50.0


def test_missing_date_no_freshness_no_vpd():
    m = compute_listing_metrics(_listing(), REF)
    assert m["age_days"] is None
    assert m["freshness_bucket"] == NO_FRESHNESS
    assert m["views_per_day"] is None
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


def test_summary_buckets_sum_to_total():
    listings = [
        compute_listing_metrics(_listing(
            published_at=datetime(2026, 10, 1), views_total=10, price=100.0,
            categories=["Кактусы"], seller_name="A"), REF),
        compute_listing_metrics(_listing(
            published_at=datetime(2026, 8, 1), views_total=500, price=200.0,
            categories=["Суккуленты"], seller_name="A", paid_services="Витрина"), REF),
        compute_listing_metrics(_listing(seller_name="B"), REF),
    ]
    summary = build_summary(listings)
    assert summary["total"] == 3
    assert sum(summary["bucket_counts"].values()) == 3
    assert summary["bucket_counts"]["0–1"] == 1
    assert summary["bucket_counts"]["31+"] == 1
    assert summary["bucket_counts"][NO_FRESHNESS] == 1
    assert summary["price_overall"]["count"] == 2
    assert summary["promo_top"] == [("Витрина", 1)]
    assert summary["unique_sellers"] == 2
    assert summary["sellers"][0]["listings_in_serp"] == 2


def test_fixture_parse_and_metrics():
    parsed = parse_workbook(FIXTURE, REF)
    records = parsed["records"]
    assert len(records) == 12
    assert len(parsed["ignored"]) == 3
    assert not any("Нет колонки" in w for w in parsed["warnings"])

    listings = [compute_listing_metrics(r, REF) for r in records]

    by_id = {l["avito_id"]: l for l in listings}
    assert by_id["2891234567"]["age_days"] == 0          # сегодня в 12:35
    assert by_id["2891234568"]["age_days"] == 1          # вчера в 12:26
    assert by_id["2891234569"]["age_days"] == 24         # 7 сентября
    assert by_id["2891234570"]["age_days"] == 31         # 31 августа → 31+
    assert by_id["2891234570"]["freshness_bucket"] == "31+"
    assert by_id["2891234573"]["age_days"] == 6          # datetime 25.09 → 4–7
    assert by_id["2891234574"]["age_days"] == 3          # 28.09.2026 → 2–3
    assert by_id["2891234575"]["age_days"] == 1          # «вчера»
    assert by_id["2891234577"]["published_at"] is None   # «когда-то»
    assert by_id["2891234577"]["freshness_bucket"] == NO_FRESHNESS
    assert by_id["2891234576"]["published_at"].year == 2025  # «2 октября» из будущего

    summary = build_summary(listings)
    assert summary["total"] == 12
    assert sum(summary["bucket_counts"].values()) == 12
    assert summary["bucket_counts"][NO_FRESHNESS] == 1


def test_fixture_without_date_column():
    import pandas as pd
    import io

    frame = pd.read_excel(FIXTURE)
    frame = frame.drop(columns=["Дата публикации"])
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    buffer.seek(0)

    parsed = parse_workbook(buffer, REF)
    assert any("Дата публикации" in w for w in parsed["warnings"])
    listings = [compute_listing_metrics(r, REF) for r in parsed["records"]]
    assert all(l["freshness_bucket"] == NO_FRESHNESS for l in listings)
    summary = build_summary(listings)
    assert summary["bucket_counts"][NO_FRESHNESS] == 12
