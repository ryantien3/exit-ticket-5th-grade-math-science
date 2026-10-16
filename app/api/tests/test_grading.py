import pytest

from app.grading import mastery_level, percent, summarize_by_standard


@pytest.mark.parametrize(
    ("percent_correct", "level"),
    [
        (0.0, "needs support"),
        (59.9, "needs support"),
        (60.0, "approaching"),
        (79.9, "approaching"),
        (80.0, "mastered"),
        (100.0, "mastered"),
        (None, None),
    ],
)
def test_mastery_level_thresholds(percent_correct, level):
    assert mastery_level(percent_correct) == level


def test_percent_rounds_to_one_decimal():
    assert percent(2, 3) == 66.7
    assert percent(4, 5) == 80.0


def test_percent_of_nothing_is_none():
    assert percent(0, 0) is None


def test_rounded_percent_decides_level():
    # 799/1000 = 79.9 -> approaching, but 7999/10000 = 79.99 rounds to 80.0 -> mastered
    assert mastery_level(percent(799, 1000)) == "approaching"
    assert mastery_level(percent(7999, 10000)) == "mastered"


def test_summarize_by_standard():
    summary = summarize_by_standard(
        [("NY-5.NBT.4", True), ("NY-5.NBT.3", True), ("NY-5.NBT.4", False), ("NY-5.NBT.3", True)]
    )
    assert [s.model_dump() for s in summary] == [
        {"standard_code": "NY-5.NBT.3", "correct": 2, "total": 2, "percent": 100.0},
        {"standard_code": "NY-5.NBT.4", "correct": 1, "total": 2, "percent": 50.0},
    ]
