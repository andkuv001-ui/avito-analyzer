from datetime import datetime

from src.dates import NO_FRESHNESS
from src.report import (
    DEMAND_MAP_PLACEHOLDER,
    assemble_report,
    build_demand_map_table,
    report_filename,
)

REF = datetime(2026, 10, 1, 18, 0)
FRESH, STALE = "свежее", "старое"


def _listing(n, klass):
    return {"n": n, "freshness_class": klass}


def _classifications():
    return {
        1: {"clusters": [{"kind": "товарный", "tag": "кактусы"}], "b2b": "B2C",
            "queries": [], "usp": None, "scenario": None},
        2: {"clusters": [{"kind": "сценарный", "tag": "подарок"},
                         {"kind": "проблемный", "tag": "не прижился"}],
            "b2b": "B2C", "queries": [], "usp": None, "scenario": None},
        3: {"clusters": [{"kind": "товарный", "tag": "кактусы"}],
            "b2b": "B2B", "queries": [], "usp": None, "scenario": None},
    }


def test_demand_map_counts_from_code():
    listings = [
        _listing(1, FRESH),
        _listing(2, STALE),
        _listing(3, STALE),
    ]
    table = build_demand_map_table(_classifications(), listings)

    lines = table.splitlines()
    assert lines[0].startswith("| Кластер | Тип | свежее | старое |")
    rows = {line.split("|")[1].strip(): line for line in lines[2:]}

    cactus = rows["кактусы"].split("|")
    assert cactus[3].strip() == "1"   # свежее
    assert cactus[4].strip() == "1"   # старое
    gift = rows["подарок"].split("|")
    assert gift[4].strip() == "1"     # старое
    assert "**ИТОГО**" in table
    # Объявление 2 содержит два тега → появляется в двух строках карты
    assert "| **4** |" in table


def test_demand_map_fallback_no_classifications():
    listings = [_listing(1, FRESH), _listing(2, NO_FRESHNESS), _listing(3, FRESH)]
    table = build_demand_map_table(None, listings)
    assert "[НЕТ ДАННЫХ]" in table
    assert table.splitlines()[2].split("|")[1].strip() == "[НЕТ ДАННЫХ]"
    assert "| **3** |" in table


def test_demand_map_row_without_tags():
    listings = [_listing(1, FRESH)]
    table = build_demand_map_table(
        {1: {"clusters": [], "b2b": "B2C", "queries": [], "usp": None, "scenario": None}},
        listings,
    )
    assert "(без тегов)" in table


def _stage_b_text():
    sections = "\n".join(f"## {i}. Раздел {i}\n\nтекст раздела {i}." for i in range(1, 10))
    sections = sections.replace("## 7. Раздел 7",
                                "## 7. Карта спроса\n\nВводный абзац.\n\n" + DEMAND_MAP_PLACEHOLDER)
    return "# Аналитический отчёт по выдаче Avito\n\n" + sections


def test_assemble_report_has_sections_1_9_and_table():
    listings = [_listing(1, FRESH), _listing(2, STALE)]
    table = build_demand_map_table(_classifications(), listings)
    report = assemble_report(_stage_b_text(), table,
                             niche="кактусы", region="Москва",
                             total=3, model="openai/gpt-4o-mini", generated_at=REF,
                             parsed_at=datetime(2026, 10, 1, 18, 5))

    for i in range(1, 10):
        assert f"## {i}." in report
    assert DEMAND_MAP_PLACEHOLDER not in report
    assert "| Кластер | Тип | свежее | старое |" in report
    assert "**ИТОГО**" in report
    assert "кактусы" in report and "Москва" in report
    assert "Время парсинга выгрузки" in report
    assert "01.10.2026 18:05" in report


def test_assemble_report_inserts_after_section_7_without_placeholder():
    stage_b = "# Заголовок\n\n" + "\n\n".join(
        f"## {i}. Раздел {i}\n\nтекст" for i in range(1, 10)
    )
    table = "| Кластер | Тип | свежее | старое | ИТОГО |\n|---|---|---:|---:|---:|"
    report = assemble_report(stage_b, table, niche="товар", region="СПб",
                             total=1, model="m", generated_at=REF)
    assert "| Кластер | Тип |" in report
    section8 = report.index("## 8.")
    assert report.index("| Кластер | Тип |") < section8


def test_assemble_report_appends_when_no_section_7():
    stage_b = "# Заголовок\n\n## 1. Раздел 1\n\nтекст"
    table = "| Кластер | Тип | ИТОГО |\n|---|---|---:|"
    report = assemble_report(stage_b, table, niche="товар", region="СПб",
                             total=1, model="m", generated_at=REF)
    assert "## 7. Карта спроса" in report
    assert report.index("## 7. Карта спроса") > report.index("## 1.")


def test_report_filename():
    name = report_filename("Кактусы в горшках!", datetime(2026, 10, 1, 18, 0))
    assert name.startswith("avito_report_кактусы-в-горшках_20261001-1800")
    assert name.endswith(".md")
    assert report_filename("   ") .startswith("avito_report_report_")


# --- ценовые столбцы карты спроса ------------------------------------------

def _price_listing(n, klass, price):
    return {"n": n, "freshness_class": klass, "price": price}


def test_demand_map_price_columns_header():
    listings = [_price_listing(1, FRESH, 100.0), _price_listing(2, STALE, 200.0)]
    table = build_demand_map_table(_classifications(), listings)
    header = table.splitlines()[0]
    assert "| цена медиана | дешёвые | средние | дорогие | ИТОГО |" in header


def test_demand_map_price_cells_no_data_below_min_base():
    listings = [_price_listing(i, FRESH if i % 2 else STALE, float(i * 100))
                for i in range(1, 9)]  # 8 с ценой <9
    table = build_demand_map_table(_classifications(), listings)
    rows = table.splitlines()
    row = next(line for line in rows if line.startswith("| (без тегов)"))
    cells = [c.strip() for c in row.split("|")]
    assert cells[6] == "[НЕТ ДАННЫХ]"          # цена медиана
    assert cells[7:10] == ["[НЕТ ДАННЫХ]"] * 3  # сегменты
    total = rows[-1]
    assert "[НЕТ ДАННЫХ]" in total


def test_demand_map_segment_counters_and_total():
    listings = [_price_listing(i, FRESH if i % 2 else STALE, float(i * 100))
                for i in range(1, 10)]  # 100…900 → 4/3/2
    classifications = {
        i: {"clusters": [{"kind": "товарный", "tag": "товар"}], "b2b": "B2C",
            "queries": [], "usp": None, "scenario": None}
        for i in range(1, 10)
    }
    table = build_demand_map_table(classifications, listings)
    lines = table.splitlines()
    row = next(line for line in lines if line.startswith("| товар"))
    cells = [c.strip() for c in row.split("|")]
    assert cells[6] == "500"     # медиана цены
    assert cells[7:10] == ["4", "3", "2"]
    assert cells[10] == "9"
    total = [c.strip() for c in lines[-1].split("|")]
    assert total[6] == "**500**"
    assert total[7:10] == ["**4**", "**3**", "**2**"]
    assert total[10] == "**9**"


def test_demand_map_fallback_with_prices_has_segment_counters():
    listings = [_price_listing(i, FRESH, float(i * 100)) for i in range(1, 10)]
    table = build_demand_map_table(None, listings)
    lines = table.splitlines()
    row = next(line for line in lines if line.startswith("| [НЕТ ДАННЫХ]"))
    cells = [c.strip() for c in row.split("|")]
    assert cells[6] == "500"
    assert cells[7:10] == ["4", "3", "2"]
