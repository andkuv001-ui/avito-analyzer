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


def _verdict(text: str = "**Вердикт:** ВХОДИТЬ — всего 12 объявлений.") -> str:
    return text


def _conclusion(point7: str = "сделать X, основание 12, эффект 70.0%",
                points_format: str = ")") -> str:
    points = [f"{i}{points_format} пункт с цифрой 12." for i in range(1, 7)]
    points.append(f"7{points_format} {point7}.")
    return "\n".join(points)


def _report(hypotheses: list[str] | None = None, verdict: str | None = None,
            conclusion: str | None = None, section_8: bool = True,
            section_9: bool = True) -> str:
    hypotheses = hypotheses if hypotheses is not None else [_hypo(i) for i in range(1, 6)]
    parts = ["# Аналитический отчёт по выдаче Avito", "", "## 1. Обзор выдачи", "", "текст."]
    if section_8:
        parts += ["", "## 8. Гипотезы", "", "\n".join(hypotheses)]
    if section_9:
        parts += ["", "## 9. Решение о входе", "",
                  verdict if verdict is not None else _verdict(), "",
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
    assert sum("единичное объявление" in v for v in violations) == 5


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
