"""Scoring rules, kept free of database and web code so they're easy to test."""

from collections import defaultdict
from collections.abc import Iterable

from app.schemas import MasteryLevel, StandardScore

MASTERED_AT = 80.0
APPROACHING_AT = 60.0


def percent(correct: int, total: int) -> float | None:
    """Percent correct, rounded to 1 decimal place; None if there's nothing to score."""
    if total == 0:
        return None
    return round(100 * correct / total, 1)


def mastery_level(percent_correct: float | None) -> MasteryLevel | None:
    # Uses the same rounded percent that's shown, so "80.0" can never say "approaching".
    if percent_correct is None:
        return None
    if percent_correct >= MASTERED_AT:
        return "mastered"
    if percent_correct >= APPROACHING_AT:
        return "approaching"
    return "needs support"


def summarize_by_standard(results: Iterable[tuple[str, bool]]) -> list[StandardScore]:
    """Turn (standard_code, is_correct) pairs into one score per standard."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # code -> [correct, total]
    for standard_code, is_correct in results:
        totals[standard_code][0] += int(is_correct)
        totals[standard_code][1] += 1
    return [
        StandardScore(standard_code=code, correct=correct, total=total, percent=percent(correct, total))
        for code, (correct, total) in sorted(totals.items())
    ]
