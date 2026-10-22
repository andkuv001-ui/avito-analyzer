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


# --- ценовая сегментация в таблицах дайджеста -------------------------------

def _segment_listings(count: int = 9):
    out = []
    for i in range(count):
        views = (i + 1) * 10
        fresh = i % 2 == 0
        out.append(compute_listing_metrics(_listing(
            n=i + 1, title=f"объявление {i + 1}", price=float((i + 1) * 100),
            views_today=views,
            published_at=datetime(2026, 10, 1) if fresh else datetime(2026, 8, 1),
            views_total=views * 2 if fresh else 5000), REF))
    return out


def _segment_classifications():
    return {
        i + 1: {"b2b": "B2C",
                "clusters": [{"kind": "товарный", "tag": "мох" if i < 6 else "кактус"}],
                "queries": [], "usp": None, "scenario": None}
        for i in range(9)
    }


def _segment_digest(classifications=None):
    listings = _segment_listings()
    return build_digest(listings, build_summary(listings), "| карта спроса |",
                        "мох", "Москва", REF,
                        classifications=classifications or _segment_classifications())


def test_digest_has_price_segment_tables():
    text = _segment_digest()
    assert "### Сегменты цены: дешёвые / средние / дорогие" in text
    assert "Тертили по числовой цене: дешёвые ≤ 400 < средние ≤ 700 < дорогие" in text
    assert "### Свежие vs старые внутри ценовых сегментов" in text
    assert "### Цены по кластерам" in text
    assert "| Сегмент | Объявлений | Цена min–med–max |" in text
    assert "| Сегмент | N свежих |" in text
    assert "| Тег | Тип | N | Медиана цены | «Просмотров сегодня» медиана |" in text
    assert "| дешёвые | 4 | 100–250–400 | 25.0 | 0 |" in text
    assert "| мох | товарный | 6 | 350 |" in text


def test_digest_effectiveness_renamed_with_confound_note():
    text = _segment_digest()
    assert "### Эффективность свежих vs старых (без учёта цены)" in text
    assert "конфаундится ценой — см. разрезы по сегментам" in text
    assert "### Эффективность: что эффективнее" not in text


def test_digest_segment_tables_no_data_for_small_base():
    text = _digest(_classifications())
    assert "Сегменты не считаются: с числовой ценой 2 (<9)" in text
    assert "| дешёвые | [НЕТ ДАННЫХ] | [НЕТ ДАННЫХ] | [НЕТ ДАННЫХ] | [НЕТ ДАННЫХ] |" in text
    assert "| дешёвые | [НЕТ ДАННЫХ] | [НЕТ ДАННЫХ] | [НЕТ ДАННЫХ] | [НЕТ ДАННЫХ] | " \
           "[НЕТ ДАННЫХ] |" in text


def test_digest_cluster_prices_without_classifications():
    text = _digest(None)
    assert "### Цены по кластерам" in text
    assert "| [НЕТ ДАННЫХ] | — | — | — | — |" in text


def test_digest_rules_mention_price_confounder():
    text = _digest(_classifications())
    assert "ЦЕНА — ГЛАВНЫЙ КОНФАУНДЕР" in text
    assert "только ВНУТРИ ценовых сегментов" in text


# --- новые правила интерпретации в rules() -----------------------------------

def test_digest_rules_mention_seasonality_with_month():
    text = _digest(_classifications())
    assert "СЕЗОННОСТЬ" in text
    assert "месяц: октябрь" in text  # REF = 01.10.2026
    assert "ЗАКРЫТЫЙ СЕЗОН" in text
    # сезонность — внешняя календарная оценка, а не факт из таблицы
    assert "вне данных" in text


def test_digest_rules_single_day_slice():
    text = _digest(_classifications())
    assert "СРЕЗ — ОДНА ДАТА" in text
    assert "динамик" in text


def test_digest_rules_mention_product_fit_and_small_base():
    text = _digest(_classifications())
    assert "ТОВАР КЛИЕНТА" in text
    assert "1–2 объявления в кластере — это не доказательство спроса" in text


def test_digest_rules_mention_service_and_kpi():
    text = _digest(_classifications())
    assert "СЕРВИС — НЕ СЦЕНАРИЙ" in text
    assert "реалистичные пороги" in text


def test_digest_rules_mention_unit_economics_and_map_cleaning():
    text = _digest(_classifications())
    assert "ЮНИТ-ЭКОНОМИКА" in text
    assert "Карта спроса уже очищена кодом" in text


def test_digest_has_realistic_thresholds_fact():
    text = _digest(_classifications())
    assert "[реалистичные пороги] " in text
