from src.llm import LLMError, validate_stage_a


def _item(n, clusters):
    return {"n": n, "b2b": "B2C", "clusters": clusters,
            "queries": [], "usp": None, "scenario": None}


def test_validate_stage_a_drops_junk_tags():
    parsed = [_item(1, [{"kind": "товарный", "tag": "кактусы"},
                        {"kind": "проблемный", "tag": "следы эксплуатации"}])]
    result = validate_stage_a(parsed, {1})
    assert [c["tag"] for c in result[1]["clusters"]] == ["кактусы"]


def test_validate_stage_a_merges_case_duplicates():
    parsed = [_item(1, [{"kind": "товарный", "tag": "Urban Life Style"},
                        {"kind": "товарный", "tag": "urban life style"}])]
    result = validate_stage_a(parsed, {1})
    tags = [c["tag"] for c in result[1]["clusters"]]
    assert tags == ["Urban Life Style"]


def test_validate_stage_a_merges_size_variants():
    parsed = [_item(1, [{"kind": "характеристический", "tag": "размер 40x30x14"},
                        {"kind": "характеристический", "tag": "размеры 43x28x15"}])]
    result = validate_stage_a(parsed, {1})
    tags = [c["tag"] for c in result[1]["clusters"]]
    assert tags == ["размеры"]


def test_validate_stage_a_invalid_kind_still_raises():
    parsed = [_item(1, [{"kind": "мусорный тип", "tag": "тег"}])]
    try:
        validate_stage_a(parsed, {1})
        raise AssertionError("ожидали LLMError")
    except LLMError:
        pass
