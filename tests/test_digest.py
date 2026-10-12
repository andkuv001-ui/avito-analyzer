from datetime import datetime

from src.digest import build_digest
from src.metrics import FACT_NAMES, build_summary, compute_listing_metrics

REF = datetime(2026, 10, 1, 18, 0)


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


def _listings():
    return [
        compute_listing_metrics(_listing(
            n=1, title="мох в коробках", published_at=datetime(2026, 10, 1),
            views_today=10, views_total=20, price=1200.0,
        ), REF),
        compute_listing_metrics(_listing(
            n=2, title="кактус", published_at=datetime(2026, 8, 1),
            views_today=0, views_total=5000, price=600.0,
        ), REF),
    ]


def _classifications():
    return {
        1: {"b2b": "B2B",
            "clusters": [{"kind": "товарный", "tag": "мох"}],
            "queries": ["мох купить", "живой мох"],
            "usp": "доставка за 1 день", "scenario": "оформление террариума"},
        2: {"b2b": "B2C", "clusters": [], "queries": [], "usp": None, "scenario": None},
    }


def _digest(classifications):
    listings = _listings()
    return build_digest(listings, build_summary(listings), "| карта спроса |",
                        "мох", "Москва", REF, classifications=classifications)


def test_digest_shows_stage_a_fields():
    text = _digest(_classifications())
    assert "- Запросы: мох купить, живой мох" in text
    assert "- УТП: доставка за 1 день" in text
    assert "- Сценарий: оформление террариума" in text
    assert "- Тип: B2B" in text


def test_digest_stage_a_empty_fields_are_no_data():
    text = _digest(_classifications())
    assert "- Запросы: [НЕТ ДАННЫХ]" in text
    assert "- УТП: [НЕТ ДАННЫХ]" in text
    assert "- Сценарий: [НЕТ ДАННЫХ]" in text
    assert "- Тип: B2C" in text


def test_digest_without_classifications_is_no_data():
    text = _digest(None)
    assert text.count("- Запросы: [НЕТ ДАННЫХ]") == 2
    assert text.count("- УТП: [НЕТ ДАННЫХ]") == 2
    assert text.count("- Сценарий: [НЕТ ДАННЫХ]") == 2
    assert text.count("- Тип: [НЕТ ДАННЫХ]") == 2


def test_digest_has_key_facts_section():
    text = _digest(_classifications())
    assert "## Ключевые факты (посчитаны кодом)" in text
    for name in FACT_NAMES:
        assert f"[{name}] " in text
    assert "[объём] всего 2" in text
    # секция фактов идёт после правил и до сводных метрик
    assert text.index("НАПОМИНАНИЯ ПРАВИЛ") < text.index("## Ключевые факты") < \
        text.index("## Сводные метрики")


def test_digest_key_facts_reference_stage_a_names_not_numbers():
    text = _digest(None)
    for name in ("кластеры без свежих", "B2B-доля", "топ-5 запросов"):
        assert f"[{name}] [НЕТ ДАННЫХ]" in text
