from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .classification import classify_text


@dataclass(frozen=True)
class GoldEvaluation:
    evaluated_count: int
    primary_category_accuracy: float
    high_confidence_error_count: int
    manual_review_recall: float
    per_category_confusion_summary: dict[str, dict[str, int]]
    mismatched_comment_ids: tuple[str, ...]
    lifecycle_mismatched_comment_ids: tuple[str, ...]
    manual_review_mismatched_comment_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)


def _as_bool(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def load_gold_labels(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def evaluate_gold_labels(
    path: Path,
    *,
    high_confidence_threshold: float = 0.80,
) -> GoldEvaluation:
    rows = load_gold_labels(path)
    correct = 0
    high_confidence_errors = 0
    expected_review_count = 0
    caught_review_count = 0
    mismatched_ids: list[str] = []
    lifecycle_mismatches: list[str] = []
    review_mismatches: list[str] = []
    confusion: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        comment_id = row["comment_id"]
        result = classify_text(row["comment_text"])
        expected_category = row["expected_primary_category"]
        confusion[expected_category][result.primary_category] += 1
        if result.primary_category == expected_category:
            correct += 1
        else:
            mismatched_ids.append(comment_id)
            if result.category_confidence >= high_confidence_threshold:
                high_confidence_errors += 1

        expected_review = _as_bool(row["expected_manual_review"])
        if expected_review:
            expected_review_count += 1
            if result.manual_review:
                caught_review_count += 1
        if result.manual_review != expected_review:
            review_mismatches.append(comment_id)

        if result.lifecycle_stage != row["expected_lifecycle_stage"]:
            lifecycle_mismatches.append(comment_id)

    total = len(rows)
    return GoldEvaluation(
        evaluated_count=total,
        primary_category_accuracy=round(correct / total, 4) if total else 0.0,
        high_confidence_error_count=high_confidence_errors,
        manual_review_recall=(
            round(caught_review_count / expected_review_count, 4)
            if expected_review_count
            else 1.0
        ),
        per_category_confusion_summary={
            expected: dict(sorted(predicted.items()))
            for expected, predicted in sorted(confusion.items())
        },
        mismatched_comment_ids=tuple(mismatched_ids),
        lifecycle_mismatched_comment_ids=tuple(lifecycle_mismatches),
        manual_review_mismatched_comment_ids=tuple(review_mismatches),
    )
