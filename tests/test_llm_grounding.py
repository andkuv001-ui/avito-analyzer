import pytest

from src import llm
from src.llm import (
    DECISION_MARKER,
    GROUNDING_WARNING_PREFIX,
    HYPOTHESIS_MARKER,
    normalize_numbers,
    validate_grounding,
)

DIGEST = "всего 12; свежее 3 / старое 8; топ-10 держат 70.0%; медиана цены 1 200"


def _hypo(n: int, basis: str = "всего 12 объявлений", check: str = "тест",
          criterion: str = "≥20 просмотров/день за 14 дней") -> str:
    return (f"**Гипотеза {n}: утверждение** — Основание: {basis}; "
            f"Проверка: {check}; Критерий успеха: {criterion}.")


def _verdict(text: str = "**Вердикт:** ВХОДИТЬ ПРИ УСЛОВИИ — сегмент: дешёвые, "
                         "медиана 12, всего 12 объявлений.") -> str:
    return text


_ECON_CAVEAT = ("Юнит-экономика (себестоимость, маржа, CAC) нашими данными не "
                "измеряется — нет данных; вердикт — о трафике, а не о прибыли.")


def _conclusion(point7: str = "сделать X, основание 12, эффект 70.0%",
                points_format: str = ")") -> str:
    points = [f"{i}{points_format} пункт с цифрой 12, второй этап воронки."
              for i in range(1, 7)]
    points.append(f"7{points_format} {point7}.")
    return "\n".join(points)


def _report(hypotheses: list[str] | None = None, verdict: str | None = None,
            conclusion: str | None = None, section_8: bool = True,
            section_9: bool = True, econ_caveat: bool = True) -> str:
    hypotheses = hypotheses if hypotheses is not None else [_hypo(i) for i in range(1, 6)]
    parts = ["# Аналитический отчёт по выдаче Avito", "", "## 1. Обзор выдачи", "", "текст."]
    if section_8:
        parts += ["", "## 8. Гипотезы", "", "\n".join(hypotheses)]
    if section_9:
        caveat = f"\n\n{_ECON_CAVEAT}" if econ_caveat else ""
        parts += ["", "## 9. Решение о входе", "",
                  (verdict if verdict is not None else _verdict()) + caveat, "",
                  conclusion if conclusion is not None else _conclusion()]
    return "\n".join(parts)


# --- нормализация чисел ----------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("1 200", {1200.0}),
    ("1\xa0200", {1200.0}),
    ("12 345 678", {12345678.0}),
    ("15,5", {15.5}),
    ("15%", {15.0}),
    ("≈1200", {1200.0}),
    ("~1200 и около 45", {1200.0, 45.0}),
    ("10–15", {10.0, 15.0}),
    ("10-15", {10.0, 15.0}),
    ("2/3", {2.0, 3.0}),
    ("5 тыс.", {5.0}),
    ("10 15", {10.0, 15.0}),
])
def test_normalize_numbers(text, expected):
    assert normalize_numbers(text) == expected


def test_normalize_numbers_from_digest_format():
    assert normalize_numbers(DIGEST) >= {12.0, 3.0, 8.0, 10.0, 70.0, 1200.0}


# --- секция 8 (гипотезы) ---------------------------------------------------


def test_missing_sections_are_violations():
    violations = validate_grounding("## 1. Обзор\n\nтекст", DIGEST)
    assert any("нет секции 8" in v for v in violations)
    assert any("нет секции 9" in v for v in violations)


def test_hypothesis_count_below_5():
    report = _report([_hypo(i) for i in range(1, 4)])
    assert any("гипотез: 3" in v for v in validate_grounding(report, DIGEST))


def test_hypothesis_count_above_10():
    report = _report([_hypo(i) for i in range(1, 12)])
    assert any("гипотез: 11" in v for v in validate_grounding(report, DIGEST))


def test_valid_report_passes():
    assert validate_grounding(_report(), DIGEST) == []


def test_marker_frees_hypothesis_from_digest_token():
    hypotheses = [
        f"**Гипотеза {i}: утверждение** — Основание: {HYPOTHESIS_MARKER}; "
        f"Проверка: проверить тестом; Критерий успеха: ≥20 просмотров за 14 дней."
        for i in range(1, 6)
    ]
    assert validate_grounding(_report(hypotheses), DIGEST) == []


def test_hypothesis_without_digest_token_is_violation():
    report = _report([_hypo(i, basis="цифра 987654") for i in range(1, 6)])
    assert any("не встречаются в дайджесте" in v for v in validate_grounding(report, DIGEST))


def test_hypothesis_without_any_digit_is_violation():
    report = _report([
        _hypo(i, basis="нет цифр совсем", criterion="стабильный спрос без порога")
        for i in range(1, 6)
    ])
    assert any("нет ни одной цифры" in v for v in validate_grounding(report, DIGEST))


def test_derived_number_alongside_real_quote_passes():
    report = _report([_hypo(i, basis="всего 12 объявлений, в 4.7 раза выше") for i in range(1, 6)])
    assert validate_grounding(report, DIGEST) == []


def test_missing_basis_and_check_are_violations():
    hypotheses = [
        "**Гипотеза 1: утверждение** — Проверка: тест; Критерий успеха: ≥20 за 14 дней.",
        "**Гипотеза 2: утверждение 2** — Основание: всего 12; Критерий успеха: ≥20 за 14 дней.",
        *[_hypo(i) for i in range(3, 6)],
    ]
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert any("гипотеза 1: нет подстроки «Основание»" in v for v in violations)
    assert any("гипотеза 2: нет подстроки «Проверка»" in v for v in violations)


def test_missing_success_criterion_is_violation():
    hypotheses = [
        f"**Гипотеза {i}: утверждение** — Основание: всего 12; Проверка: тест."
        for i in range(1, 6)
    ]
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert sum("нет подстроки «Критерий успеха»" in v for v in violations) == 5


def test_criterion_without_number_is_violation():
    report = _report([
        _hypo(i, criterion="устойчивый спрос без порога") for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("в «Критерии успеха» нет числового порога" in v for v in violations) == 5


def test_criterion_number_without_digest_quote_is_violation():
    # цифра есть, но она в «Критерии», а не в «Основании» — digest-цитаты нет
    report = _report([
        _hypo(i, basis="цифра 987654", criterion="≥999 просмотров/день") for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("не встречаются в дайджесте" in v for v in violations) == 5


def test_marker_in_criterion_is_violation():
    report = _report([
        _hypo(i, criterion=HYPOTHESIS_MARKER) for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("в «Критерии успеха»" in v for v in violations) == 5


def test_hypothesis_bad_formatting_is_violation():
    hypotheses = [_hypo(i) for i in range(1, 6)]
    hypotheses.append("Гипотеза 6: без звёздочек — Основание: всего 12; Проверка: тест.")
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert any("оформлена не по шаблону" in v for v in violations)


# --- секция 9 (решение о входе) --------------------------------------------


def test_verdict_missing_is_violation():
    violations = validate_grounding(_report(verdict="Абзац без вердикта."), DIGEST)
    assert any("нет блока вердикта" in v for v in violations)


def test_verdict_without_digest_digit_is_violation():
    verdict = "**Вердикт:** ВХОДИТЬ — цифра 987654 в основании."
    violations = validate_grounding(_report(verdict=verdict), DIGEST)
    assert any("вердикт не подкреплён цифрой" in v for v in violations)


def test_verdict_with_decision_marker_passes():
    verdict = f"**Вердикт:** {DECISION_MARKER}"
    assert validate_grounding(_report(verdict=verdict), DIGEST) == []


def test_verdict_with_digest_digit_passes():
    assert validate_grounding(_report(verdict=_verdict()), DIGEST) == []


def test_conclusion_without_seventh_point():
    conclusion = "\n".join(f"{i}) пункт с цифрой 12." for i in range(1, 7))
    violations = validate_grounding(_report(conclusion=conclusion), DIGEST)
    assert any("требуется ровно 7" in v for v in violations)


def test_conclusion_points_numbered_with_dot():
    violations = validate_grounding(_report(conclusion=_conclusion(points_format=".")), DIGEST)
    assert any("точкой вместо скобки" in v for v in violations)


def test_conclusion_wrong_point_count():
    points = [f"{i}) пункт с цифрой 12." for i in range(1, 9)]
    violations = validate_grounding(_report(conclusion="\n".join(points)), DIGEST)
    assert any("требуется ровно 7" in v for v in violations)


def test_point_1_to_6_without_digit_or_marker():
    points = [f"{i}) без цифр и маркера." for i in range(1, 7)]
    points.append("7) сделать, основание 12, эффект 70.0%.")
    violations = validate_grounding(_report(conclusion="\n".join(points)), DIGEST)
    assert sum("нет цифры и нет маркера" in v for v in violations) == 6


def test_point_7_without_digest_digit():
    violations = validate_grounding(
        _report(conclusion=_conclusion(point7="сделать X, основание 987654")), DIGEST
    )
    assert any("п.9.7" in v for v in violations)


def test_point_7_with_marker_passes():
    conclusion = _conclusion(point7=f"сделать X, основание {HYPOTHESIS_MARKER}")
    assert validate_grounding(_report(conclusion=conclusion), DIGEST) == []


# --- retry в generate_report ----------------------------------------------


def _valid_text():
    return _report()


def _invalid_text():
    return _report(
        hypotheses=["Гипотеза 1: утверждение без формата — Основание: 987654; Проверка: тест."],
        conclusion="1. пункт\n2) ещё",
    )


def _patch_chat(monkeypatch, answers):
    calls = []

    def fake_chat(client, model, system, user, temperature=0.0):
        calls.append(user)
        return next(answers)

    monkeypatch.setattr(llm, "_client", lambda: object())
    monkeypatch.setattr(llm, "_chat", fake_chat)
    return calls


def test_generate_report_first_answer_clean(monkeypatch):
    calls = _patch_chat(monkeypatch, iter([_valid_text()]))
    text, warning = llm.generate_report("sys", DIGEST)
    assert warning is None
    assert text == _valid_text()
    assert len(calls) == 1


def test_generate_report_retry_succeeds(monkeypatch):
    calls = _patch_chat(monkeypatch, iter([_invalid_text(), _valid_text()]))
    text, warning = llm.generate_report("sys", DIGEST)
    assert warning is None
    assert text == _valid_text()
    assert len(calls) == 2
    assert "НЕ ПРОШЁЛ ПРОВЕРКУ" in calls[1]


def test_generate_report_retry_fails_returns_warning(monkeypatch):
    _patch_chat(monkeypatch, iter([_invalid_text(), _invalid_text()]))
    text, warning = llm.generate_report("sys", DIGEST)
    assert text == _invalid_text()
    assert isinstance(warning, str)
    assert warning.startswith(GROUNDING_WARNING_PREFIX)


def test_generate_report_requires_section_one(monkeypatch):
    _patch_chat(monkeypatch, iter(["## 2. Без первой секции\n\nтекст"]))
    with pytest.raises(llm.LLMError):
        llm.generate_report("sys", DIGEST)


def test_generate_report_broken_retry_keeps_first_text(monkeypatch):
    _patch_chat(monkeypatch, iter([_invalid_text(), "сломанный ответ без секции 1"]))
    text, warning = llm.generate_report("sys", DIGEST)
    assert text == _invalid_text()
    assert warning and warning.startswith(GROUNDING_WARNING_PREFIX)


# --- запрет надуманных гипотез (основание из единичного объявления) --------


def test_singleton_basis_is_violation():
    report = _report([
        _hypo(i, basis="1 старое объявление с упоминанием дефектов")
        for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("1–2 объявления" in v for v in violations) == 5


def test_two_listing_basis_is_violation():
    report = _report([
        _hypo(i, basis="2 объявления кластера")
        for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("1–2 объявления" in v for v in violations) == 5


def test_singleton_as_part_of_aggregate_basis_passes():
    report = _report([
        _hypo(i, basis="12 объявлений, из них 1 объявление свежее")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, DIGEST) == []


def test_singleton_pattern_not_triggered_by_double_digits():
    report = _report([
        _hypo(i, basis="21 объявление держит 12 продавцов")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, DIGEST) == []


# --- запрет темы дефектов/б/у в гипотезах ----------------------------------


def test_demand_for_defects_title_is_violation():
    hypotheses = [
        _hypo(i, basis="всего 12 объявлений").replace(
            "утверждение",
            f"существует спрос на рюкзаки с дефектами {i}",
        )
        for i in range(1, 6)
    ]
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert sum("дефектов/б/у" in v for v in violations) == 5


def test_defects_attractive_title_is_violation():
    hypotheses = [
        _hypo(i, basis="всего 12 объявлений").replace(
            "утверждение",
            f"дефекты ткани привлекательны для покупателей {i}",
        )
        for i in range(1, 6)
    ]
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert sum("дефектов/б/у" in v for v in violations) == 5


def test_used_goods_demand_title_is_violation():
    hypotheses = [
        _hypo(i, basis="всего 12 объявлений").replace(
            "утверждение",
            f"спрос на б/у модели растёт {i}",
        )
        for i in range(1, 6)
    ]
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert sum("дефектов/б/у" in v for v in violations) == 5


def test_sane_hypothesis_titles_pass():
    assert validate_grounding(_report(), DIGEST) == []


# --- обязательная оговорка «второй этап воронки» в §9 -----------------------


def test_missing_funnel_caveat_is_violation():
    conclusion = "\n".join(f"{i}) пункт с цифрой 12." for i in range(1, 8))
    violations = validate_grounding(_report(conclusion=conclusion), DIGEST)
    assert any("второй этап воронки" in v for v in violations)


def test_funnel_caveat_in_verdict_block_passes():
    # оговорка стоит сразу после вердикта, до пунктов — это норма
    conclusion = "\n".join(f"{i}) пункт с цифрой 12." for i in range(1, 8))
    report = _report(
        verdict="**Вердикт:** ВХОДИТЬ ПРИ УСЛОВИИ — сегмент: дешёвые, "
                "медиана 12 просмотров/день.\n\n"
                "Просмотры — второй этап воронки: конверсию в контакты "
                "предполагаем только гипотетически.",
        conclusion=conclusion,
    )
    assert validate_grounding(report, DIGEST) == []


# --- сезонность: «0 свежих» без проверки сезона -------------------------------

def test_no_fresh_basis_without_season_is_violation():
    report = _report([
        _hypo(i, basis="0 свежих объявлений в кластере, всего 12")
        for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("сезонност" in v for v in violations) == 5


def test_no_fresh_basis_with_season_passes():
    report = _report([
        _hypo(i, basis="0 свежих объявлений, сезон: октябрь — межсезонье, всего 12")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, DIGEST) == []


# --- срез «одна дата»: динамика/тренды из данных запрещены ---------------------


def test_dynamics_in_basis_is_violation():
    report = _report([
        _hypo(i, basis="спрос растёт, всего 12 объявлений")
        for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("вывод о динамике" in v for v in violations) == 5


def test_dynamics_in_title_is_violation():
    hypotheses = [
        _hypo(i).replace("утверждение", f"тренд на бюджетные модели {i}")
        for i in range(1, 6)
    ]
    violations = validate_grounding(_report(hypotheses), DIGEST)
    assert sum("вывод о динамике" in v for v in violations) == 5


def test_growth_claims_in_section_9_are_violations():
    points = [
        "1) пункт с цифрой 12, второй этап воронки.",
        "2) пункт с цифрой 12, второй этап воронки.",
        "3) спрос растёт, цифра 12, второй этап воронки.",
        "4) пункт с цифрой 12, второй этап воронки.",
        "5) пункт с цифрой 12, второй этап воронки.",
        "6) пункт с цифрой 12, второй этап воронки.",
        "7) сделать X, основание 12, эффект 70.0%.",
    ]
    violations = validate_grounding(_report(conclusion="\n".join(points)), DIGEST)
    assert sum("§9:" in v and "динамик" in v for v in violations) == 1


def test_dynamics_with_no_data_caveat_passes():
    report = _report([
        _hypo(i, basis="динамика [НЕТ ДАННЫХ] — один срез, всего 12 объявлений")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, DIGEST) == []


def test_dynamics_word_in_verification_passes():
    report = _report([
        _hypo(i, check="мониторинг динамики спроса 14 дней")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, DIGEST) == []


def test_growth_target_in_point_7_passes():
    # п.7 — цели теста на будущее, а не утверждения о данных среза
    points = [f"{i}) пункт с цифрой 12, второй этап воронки." for i in range(1, 7)]
    points.append("7) тест, основание 12, эффект: рост продаж до 3 за 14 дней.")
    assert validate_grounding(_report(conclusion="\n".join(points)), DIGEST) == []


# --- сервис (доставка/оплата) не может быть гипотезой входа ------------------

def test_delivery_hypothesis_title_is_violation():
    report = _report([
        _hypo(i).replace("утверждение", f"предложение рюкзаков с доставкой {i}")
        for i in range(1, 6)
    ])
    violations = validate_grounding(report, DIGEST)
    assert sum("сервис (доставка/оплата)" in v for v in violations) == 5


def test_delivery_niche_exempts_service_ban():
    digest = "Ниша/товар: доставка грузов\n" + DIGEST
    report = _report([
        _hypo(i).replace("утверждение", f"спрос на доставку по городу {i}")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, digest) == []


# --- KPI-реализм: порог просмотров/день ≤ потолка из дайджеста ---------------

KPI_DIGEST = (
    DIGEST + "\n[реалистичные пороги] по сегментам: дешёвые — 7 (медиана ×2), "
    "средние — 4 (медиана ×2); порог успеха выше потолка"
)


def test_views_per_day_above_cap_is_violation():
    report = _report([
        _hypo(i, criterion="≥10 просмотров/день за 14 дней")
        for i in range(1, 6)
    ])
    violations = validate_grounding(report, KPI_DIGEST)
    assert sum("превышает реалистичный потолок" in v for v in violations) == 5


def test_views_per_day_at_cap_passes():
    report = _report([
        _hypo(i, criterion="≥7 просмотров/день за 14 дней")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, KPI_DIGEST) == []


def test_views_per_day_above_cap_with_paid_budget_passes():
    report = _report([
        _hypo(i, check="тест с бюджетом на платные услуги XL",
              criterion="≥10 просмотров/день за 14 дней")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, KPI_DIGEST) == []


def test_no_kpi_fact_in_digest_skips_cap_check():
    report = _report([
        _hypo(i, criterion="≥10 просмотров/день за 14 дней")
        for i in range(1, 6)
    ])
    assert validate_grounding(report, DIGEST) == []


# --- юнит-экономика: обязательная оговорка в §9 ------------------------------

def test_missing_unit_economics_caveat_is_violation():
    report = _report(econ_caveat=False)
    violations = validate_grounding(report, DIGEST)
    assert any("юнит-экономику" in v for v in violations)


# --- вердикт: сегмент обязателен, безусловный «ВХОДИТЬ» запрещён -------------

def test_verdict_without_segment_is_violation():
    verdict = "**Вердикт:** ВХОДИТЬ ПРИ УСЛОВИИ — всего 12 объявлений."
    violations = validate_grounding(_report(verdict=verdict), DIGEST)
    assert any("не назван целевой ценовой сегмент" in v for v in violations)


def test_unconditional_enter_verdict_is_violation():
    verdict = "**Вердикт:** ВХОДИТЬ — сегмент: дешёвые, всего 12 объявлений."
    violations = validate_grounding(_report(verdict=verdict), DIGEST)
    assert any("безусловный «ВХОДИТЬ»" in v for v in violations)


def test_conditional_verdict_with_segment_passes():
    assert validate_grounding(_report(), DIGEST) == []
