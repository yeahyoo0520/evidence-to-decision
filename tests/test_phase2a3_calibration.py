from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from youtube_comment_research.errors import ClassificationWorkflowError
from youtube_comment_research.readable_workbook import SHEET_NAMES, create_readable_workbook
from youtube_comment_research.semantic_workflow import (
    RUBRIC_VERSION,
    build_review_sections,
    merge_classifications,
    prepare_classification_batches,
    prepare_report_rows,
    validate_classification_payload,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "phase2a3_intent_ranking_diagnostics.jsonl"
)


def _diagnostics() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _case(name: str) -> dict[str, object]:
    return next(item for item in _diagnostics() if item["case_name"] == name)


def _valid_payload(text: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "comment_id": "control-case",
        "subject": "product",
        "primary_category": "Product Praise",
        "secondary_categories": [],
        "subtopics": [],
        "lifecycle_stage": "Unknown",
        "mentioned_brands": [],
        "mentioned_products": [],
        "confidence": 0.9,
        "evidence": [text],
        "review_risk": "low",
        "review_triggers": [],
        "manual_review": False,
        "review_reason": "",
        "classification_source": "codex-assisted",
        "classifier_version": "codex-semantic-v2",
        "rubric_version": RUBRIC_VERSION,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("item", _diagnostics(), ids=lambda item: item["case_name"])
def test_eight_diagnostic_regressions_satisfy_phase2a3_schema(
    item: dict[str, object],
) -> None:
    input_value = item["input"]
    classification = item["suggested_classification"]
    assert isinstance(input_value, dict)
    result = validate_classification_payload(
        classification,
        input_text=str(input_value["comment_text"]),
    )
    assert result.rubric_version == "phase2a3-rubric-v1"


def test_reference_examples_satisfy_phase2a3_schema() -> None:
    examples_path = (
        Path(__file__).parents[1]
        / "references"
        / "classification_examples.jsonl"
    )
    for line in examples_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        result = validate_classification_payload(
            item["classification"],
            input_text=item["input"]["comment_text"],
        )
        assert result.review_risk in {"low", "medium", "high"}


def test_long_term_praise_with_future_upgrade_keeps_praise_primary() -> None:
    classification = _case("long_term_praise_with_upgrade_consideration")[
        "suggested_classification"
    ]
    assert classification["primary_category"] == "Product Praise"
    assert "Purchase Intent" in classification["secondary_categories"]


def test_overall_praise_with_local_damage_keeps_praise_primary() -> None:
    classification = _case(
        "overall_praise_with_warranty_and_hinge_failure"
    )["suggested_classification"]
    assert classification["primary_category"] == "Product Praise"
    assert "Product Concern" in classification["secondary_categories"]
    assert classification["manual_review"] is True


def test_damage_complaint_can_still_be_product_concern_primary() -> None:
    text = "The hinge broke after two weeks and I need a replacement."
    payload = _valid_payload(
        text,
        primary_category="Product Concern",
        secondary_categories=["After-sales Issue"],
        subtopics=["durability"],
        confidence=0.96,
        evidence=["hinge broke after two weeks", "need a replacement"],
        review_risk="high",
        review_triggers=[
            "specific_product_failure",
            "high_confidence_strong_secondary",
        ],
        manual_review=True,
        review_reason="A concrete failure and replacement request require review.",
    )
    result = validate_classification_payload(payload, input_text=text)
    assert result.primary_category == "Product Concern"


def test_comparison_supporting_praise_is_secondary() -> None:
    classification = _case("replacement_supports_overall_praise")[
        "suggested_classification"
    ]
    assert classification["primary_category"] == "Product Praise"
    assert classification["secondary_categories"] == ["Competitor Comparison"]


def test_comparison_as_main_purpose_remains_primary() -> None:
    text = "Which sounds better, OpenRun Pro 2 versus AirPods?"
    payload = _valid_payload(
        text,
        primary_category="Competitor Comparison",
        secondary_categories=["Product Question"],
        subtopics=["sound quality"],
        confidence=0.97,
        evidence=["Which sounds better", "versus"],
        review_risk="medium",
        review_triggers=["high_confidence_strong_secondary"],
    )
    result = validate_classification_payload(payload, input_text=text)
    assert result.primary_category == "Competitor Comparison"


def test_two_strong_intents_raise_review_risk() -> None:
    classification = _case("long_term_praise_with_upgrade_consideration")[
        "suggested_classification"
    ]
    assert classification["review_risk"] in {"medium", "high"}
    assert "multiple_strong_intents" in classification["review_triggers"]


def test_high_confidence_does_not_cancel_manual_review() -> None:
    classification = _case(
        "overall_praise_with_warranty_and_hinge_failure"
    )["suggested_classification"]
    assert classification["confidence"] >= 0.8
    assert classification["review_risk"] == "high"
    assert classification["manual_review"] is True


def test_contrast_marker_is_explained_in_review_triggers() -> None:
    classification = _case("concern_with_older_model_comparison")[
        "suggested_classification"
    ]
    assert "contrast_marker:but" in classification["review_triggers"]


def test_mixed_product_and_video_subject_requires_review() -> None:
    classification = _case("mixed_video_and_product_colour_praise")[
        "suggested_classification"
    ]
    assert classification["confidence"] <= 0.8
    assert classification["review_risk"] == "high"
    assert classification["manual_review"] is True
    assert "mixed_product_creator_subject" in classification["review_triggers"]


def test_high_risk_requires_manual_review() -> None:
    text = "I love these but the hinge broke."
    payload = _valid_payload(
        text,
        secondary_categories=["Product Concern"],
        evidence=["love these", "hinge broke"],
        review_risk="high",
        review_triggers=["praise_concern_tension", "contrast_marker:but"],
        manual_review=False,
    )
    with pytest.raises(ClassificationWorkflowError, match="high requires"):
        validate_classification_payload(payload, input_text=text)


def test_medium_risk_requires_review_triggers() -> None:
    text = "I love these and may upgrade next year."
    payload = _valid_payload(
        text,
        secondary_categories=["Purchase Intent"],
        review_risk="medium",
        review_triggers=[],
    )
    with pytest.raises(ClassificationWorkflowError, match="review_triggers"):
        validate_classification_payload(payload, input_text=text)


def test_merge_keeps_human_confirmation_with_high_risk_codex_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "comment_id",
                "comment_text",
                "video_id",
                "is_reply",
                "human_confirmed_primary_category",
                "review_status",
                "reviewer_note",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "comment_id": "protected-human",
                "comment_text": "Great product, but the hinge broke.",
                "video_id": "video",
                "is_reply": False,
                "human_confirmed_primary_category": "Product Praise",
                "review_status": "corrected",
                "reviewer_note": "Keep this human decision.",
            }
        )
    batch_dir = tmp_path / "batches"
    summary = prepare_classification_batches(source, batch_dir)
    payload = _valid_payload(
        "Great product, but the hinge broke.",
        comment_id="protected-human",
        secondary_categories=["Product Concern"],
        evidence=["Great product", "hinge broke"],
        review_risk="high",
        review_triggers=["praise_concern_tension", "contrast_marker:but"],
        manual_review=True,
        review_reason="Overall praise and a concrete failure coexist.",
    )
    (batch_dir / f"classified_{summary.batch_files[0].name}").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "merged.csv"
    merge_classifications(source, batch_dir, output)
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        merged = next(csv.DictReader(handle))
    assert merged["codex_review_risk"] == "high"
    assert merged["human_confirmed_primary_category"] == "Product Praise"
    assert merged["review_status"] == "corrected"
    assert merged["reviewer_note"] == "Keep this human decision."


def test_classification_sheet_places_review_columns_adjacent(tmp_path: Path) -> None:
    path = create_readable_workbook(
        [
            {
                "comment_id": "one",
                "comment_text": "I love these but the hinge broke.",
                "video_id": "video",
                "is_reply": False,
                "codex_primary_category": "Product Praise",
                "codex_secondary_categories": "Product Concern",
                "codex_confidence": 0.86,
                "codex_review_risk": "high",
                "codex_manual_review": True,
                "codex_review_triggers": '["praise_concern_tension"]',
                "review_status": "pending",
                "reviewer_note": "",
            }
        ],
        tmp_path / "phase2a3.xlsx",
    )
    workbook = load_workbook(path)
    headers = [cell.value for cell in workbook[SHEET_NAMES[2]][1]]
    expected = [
        "Codex 置信度 Confidence",
        "Review Risk",
        "Mandatory Manual Review",
        "Review Triggers",
        "QA Sample",
        "QA Status",
        "Evidence Review Required",
        "Evidence Review Status",
        "Review Status",
        "Reviewer Note",
    ]
    start = headers.index(expected[0])
    assert headers[start : start + len(expected)] == expected


def test_topic_summary_distinguishes_low_risk_and_pending_review() -> None:
    rows = prepare_report_rows(
        [
            {
                "comment_id": "low",
                "comment_text": "Love these.",
                "codex_primary_category": "Product Praise",
                "codex_review_risk": "low",
                "codex_manual_review": False,
                "review_status": "pending",
            },
            {
                "comment_id": "high",
                "comment_text": "Love these but the hinge broke.",
                "codex_primary_category": "Product Praise",
                "codex_review_risk": "high",
                "codex_manual_review": True,
                "codex_review_triggers": '["praise_concern_tension"]',
                "review_status": "pending",
            },
        ]
    )
    preliminary = [
        row
        for row in build_review_sections(rows)
        if row["report_section"] == "Preliminary Codex-assisted Findings"
    ]
    assert {row["finding_status"] for row in preliminary} == {
        "low-risk preliminary",
        "medium-risk preliminary",
    }
    queued_ids = {
        row["representative_comment_id"]
        for row in build_review_sections(rows)
        if row["report_section"] == "Review Queue"
    }
    assert "high" in queued_ids


def test_runtime_contains_no_frozen_comment_id_overrides() -> None:
    runtime = (
        Path(__file__).parents[1]
        / "src"
        / "youtube_comment_research"
        / "semantic_workflow.py"
    ).read_text(encoding="utf-8")
    frozen_ids = {
        "UgwIIB8cYeqWUR93kB94AaABAg",
        "UgyRt34X8JhTaDUzKot4AaABAg",
        "UgwrWYdAb5s25Cq8Pc54AaABAg",
        "Ugx_WB-_rkE2fMIyzd94AaABAg",
        "Ugzaescv7cKgZX-XCH54AaABAg",
        "UgzL8YWFWUIHl_x1Oyp4AaABAg",
        "UgwelzBaQB4uXMjhn5p4AaABAg",
        "UgyH-7v8_rODWvBXLrZ4AaABAg",
    }
    assert not any(comment_id in runtime for comment_id in frozen_ids)
