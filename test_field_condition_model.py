from utils.field_condition_model import ABSENCE_LEVELS, assess_field_conditions


def test_field_model_is_deterministic_and_bounded():
    first = assess_field_conditions(35, 60, 13, "中等缺勤")
    assert first == assess_field_conditions(35, 60, 13, "中等缺勤")
    assert first["level"] in {"LOW", "MEDIUM", "HIGH"}
    assert 0 <= first["score"] <= 100
    assert len(first["evidence"]) == 8
    assert sum(first["distribution"].values()) == 8


def test_all_absence_levels_are_supported():
    for absence in ABSENCE_LEVELS:
        result = assess_field_conditions(30, 10, 5, absence)
        assert result["query"]["absence"] == absence
