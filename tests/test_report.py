from datetime import datetime

from src.dates import NO_FRESHNESS
from src.report import (
    DEMAND_MAP_PLACEHOLDER,
    assemble_report,
    build_demand_map_table,
    report_filename,
)

REF = datetime(2026, 10, 1, 18, 0)


def _listing(n, bucket):
    return {"n": n, "freshness_bucket": bucket}


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
        _listing(1, "0–1"),
        _listing(2, "2–3"),
        _listing(3, "31+"),
    ]
    table = build_demand_map_table(_classifications(), listings)

    lines = table.splitlines()
    assert lines[0].startswith("| Кластер | Тип |")
    rows = {line.split("|")[1].strip(): line for line in lines[2:]}

    cactus = rows["кактусы"]
    assert cactus.split("|")[3].strip() == "1"   # 0–1
    assert cactus.split("|")[7].strip() == "1"   # 31+
    gift = rows["подарок"]
    assert gift.split("|")[4].strip() == "1"     # 2–3
    assert "**ИТОГО**" in table
    # Объявление 2 содержит два тега → появляется в двух строках карты
    assert "| **4** |" in table


def test_demand_map_fallback_no_classifications():
    listings = [_listing(1, "0–1"), _listing(2, NO_FRESHNESS), _listing(3, "0–1")]
    table = build_demand_map_table(None, listings)
    assert "[НЕТ ДАННЫХ]" in table
    assert table.splitlines()[2].split("|")[1].strip() == "[НЕТ ДАННЫХ]"
    assert "| **3** |" in table


def test_demand_map_row_without_tags():
    listings = [_listing(1, "0–1")]
    table = build_demand_map_table(
        {1: {"clusters": [], "b2b": "B2C", "queries": [], "usp": None, "scenario": None}},
        listings,
    )
    assert "(без тегов)" in table


def _stage_b_text():
    sections = "\n".join(f"## {i}. Раздел {i}\n\nтекст раздела {i}." for i in range(1, 11))
    sections = sections.replace("## 8. Раздел 8",
                                "## 8. Карта спроса\n\nВводный абзац.\n\n" + DEMAND_MAP_PLACEHOLDER)
    return "# Аналитический отчёт по выдаче Avito\n\n" + sections


def test_assemble_report_has_sections_1_10_and_table():
    listings = [_listing(1, "0–1"), _listing(2, "31+")]
    table = build_demand_map_table(_classifications(), listings)
    report = assemble_report(_stage_b_text(), table,
                             niche="кактусы", region="Москва",
                             total=3, model="openai/gpt-4o-mini", generated_at=REF,
                             parsed_at=datetime(2026, 10, 1, 18, 5))

    for i in range(1, 11):
        assert f"## {i}." in report
    assert DEMAND_MAP_PLACEHOLDER not in report
    assert "| Кластер | Тип |" in report
    assert "**ИТОГО**" in report
    assert "кактусы" in report and "Москва" in report
    assert "Время парсинга выгрузки" in report
    assert "01.10.2026 18:05" in report
    assert "3" in report.split("Объявлений в выдаче")[1][:40]


def test_assemble_report_inserts_after_section_8_without_placeholder():
    stage_b = "# Заголовок\n\n" + "\n\n".join(
        f"## {i}. Раздел {i}\n\nтекст" for i in range(1, 11)
    )
    table = "| Кластер | Тип | 0–1 | 2–3 | 4–7 | 8–30 | 31+ | Нет данных | ИТОГО |\n|---|---|---:|---:|---:|---:|---:|---:|---:|"
    report = assemble_report(stage_b, table, niche="товар", region="СПб",
                             total=1, model="m", generated_at=REF)
    assert "| Кластер | Тип |" in report
    section9 = report.index("## 9.")
    assert report.index("| Кластер | Тип |") < section9


def test_assemble_report_appends_when_no_section_8():
    stage_b = "# Заголовок\n\n## 1. Раздел 1\n\nтекст"
    table = "| Кластер | Тип | ИТОГО |\n|---|---|---:|"
    report = assemble_report(stage_b, table, niche="товар", region="СПб",
                             total=1, model="m", generated_at=REF)
    assert "## 8. Карта спроса" in report
    assert report.index("## 8. Карта спроса") > report.index("## 1.")


def test_report_filename():
    name = report_filename("Кактусы в горшках!", datetime(2026, 10, 1, 18, 0))
    assert name.startswith("avito_report_кактусы-в-горшках_20261001-1800")
    assert name.endswith(".md")
    assert report_filename("   ") .startswith("avito_report_report_")
