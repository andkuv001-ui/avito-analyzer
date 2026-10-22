"""Промпт — основной носитель правил анализа: фиксируем ключевые требования."""

from pathlib import Path

PROMPT = (Path(__file__).parent.parent / "prompts" / "analyst.md").read_text(encoding="utf-8")


def test_prompt_placeholders_present():
    assert "[НИША / ТОВАР]" in PROMPT
    assert "[РЕГИОН]" in PROMPT


def test_prompt_seasonality_rules():
    assert "СЕЗОННОСТЬ ОБЯЗАТЕЛЬНА" in PROMPT
    assert "закрытый сезон" in PROMPT
    # сезонность обязана упоминаться в §1, §4 и в гипотезах
    assert "проверь сезонность" in PROMPT
    # сезонность — внешняя календарная оценка, а не цифра данных
    assert "вне данных" in PROMPT


def test_prompt_single_day_slice_rule():
    # выгрузка — один срез: цифры = уровень на момент парсинга, динамики нет
    assert "СРЕЗ — ОДНА ДАТА" in PROMPT
    assert "динамик" in PROMPT
    assert "тренд" in PROMPT


def test_prompt_product_fit_rule():
    assert "ТОВАР КЛИЕНТА" in PROMPT
    assert "нецелевой трафик" in PROMPT


def test_prompt_small_base_rule():
    assert "МАЛО ДАННЫХ ≠ СПРОС" in PROMPT
    assert "search volume" in PROMPT


def test_prompt_service_not_hypothesis_rule():
    assert "СЕРВИС — НЕ ГИПОТЕЗА" in PROMPT


def test_prompt_realistic_kpi_rule():
    assert "РЕАЛИСТИЧНЫЕ КРИТЕРИИ" in PROMPT
    assert "реалистичные пороги" in PROMPT


def test_prompt_price_segments_without_gaps():
    assert "ТЕРТИЛИ БЕЗ ПРОБЕЛОВ" in PROMPT


def test_prompt_stale_conclusion_caveat():
    assert "ВЫВОД «СТАРЫЕ ЭФФЕКТИВНЕЕ» — С ОГОВОРКОЙ" in PROMPT


def test_prompt_unit_economics_rule():
    assert "ЮНИТ-ЭКОНОМИКА В ДАННЫХ ОТСУТСТВУЕТ" in PROMPT


def test_prompt_verdict_for_specific_product():
    assert "ВЕРДИКТ — ПО КОНКРЕТНОЙ МОДЕЛИ/ТОВАРУ" in PROMPT
    assert "ВХОДИТЬ ПРИ УСЛОВИИ" in PROMPT
    assert "безусловный «ВХОДИТЬ» запрещён" in PROMPT


def test_prompt_demand_map_cleaning_mentioned():
    assert "КАРТА СПРОСА очищена кодом" in PROMPT


def test_prompt_required_caveats_in_section_9():
    assert "второй этап воронки" in PROMPT
    assert "юнит-экономику" in PROMPT


def test_prompt_hypothesis_format_preserved():
    assert "**Гипотеза N:" in PROMPT
    assert "Основание" in PROMPT and "Проверка" in PROMPT
    assert "Критерий успеха" in PROMPT
