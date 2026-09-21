from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .classification import CATEGORY_CN, CATEGORY_LABELS, classify_text
from .errors import ClassificationWorkflowError
from .profiles import (
    DEFAULT_PROFILE_ID,
    ResearchProfile,
    load_profile,
    profile_sha256,
    validate_profile_value,
)
from .reporting import load_rows
from .review_sampling import REVIEW_CONTROL_FIELDS, apply_review_sampling, review_queue

RUBRIC_VERSION = "phase2a3-rubric-v1"
WORKFLOW_VERSION = "phase2a3-workflow-v1"
DEFAULT_CLASSIFIER_VERSION = "codex-semantic-v2"

CATEGORIES = tuple(category for category, _ in CATEGORY_LABELS)
SUBJECTS = (
    "product",
    "brand",
    "video_or_creator",
    "filming_or_editing",
    "another_commenter",
    "unrelated",
    "unclear",
)
LIFECYCLE_STAGES = (
    "Unknown",
    "Considering",
    "Waiting for Launch",
    "Purchased",
    "Using",
    "Returned",
    "Replacing",
    "Repurchasing",
)
REVIEW_STATUSES = ("pending", "confirmed", "corrected", "rejected")
PRODUCT_SUBJECT_CATEGORIES = {
    "Product Praise",
    "Product Concern",
    "Product Question",
    "Feature Request",
    "After-sales Issue",
}

SKILL_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = SKILL_ROOT / "schemas" / "comment_classification.schema.json"
RUBRIC_PATH = SKILL_ROOT / "references" / "classification_rubric.md"

BATCH_INPUT_FIELDS = (
    "comment_id",
    "comment_text",
    "evidence_id",
    "source_type",
    "source_name",
    "language",
    "market",
    "region",
    "video_id",
    "is_reply",
    "parent_comment_id",
    "parent_comment_text",
    "video_title",
    "channel_title",
)

RULE_CANDIDATE_FIELDS = (
    "rule_classification_label",
    "rule_candidate_categories",
    "rule_candidate_subtopics",
    "detected_entities",
    "detected_comparison_phrases",
    "detected_lifecycle_signals",
    "rule_evidence",
)

CODEX_FIELDS = (
    "codex_subject",
    "codex_primary_category",
    "codex_secondary_categories",
    "codex_subtopics",
    "codex_lifecycle_stage",
    "codex_mentioned_brands",
    "codex_mentioned_products",
    "codex_confidence",
    "codex_evidence",
    "codex_review_risk",
    "codex_review_triggers",
    "codex_manual_review",
    "codex_review_reason",
    "classification_source",
    "classifier_version",
    "rubric_version",
    "classified_at",
    "research_profile",
)

HUMAN_REVIEW_FIELDS = (
    "human_confirmed_primary_category",
    "human_confirmed_secondary_categories",
    "human_confirmed_subtopics",
    "human_confirmed_lifecycle_stage",
    "review_status",
    "reviewed_at",
    "reviewer_note",
)


class CommentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    comment_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    primary_category: str = Field(min_length=1)
    secondary_categories: list[str]
    subtopics: list[str]
    lifecycle_stage: str = Field(min_length=1)
    mentioned_brands: list[str]
    mentioned_products: list[str]
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
    review_risk: Literal["low", "medium", "high"]
    review_triggers: list[str]
    manual_review: bool
    review_reason: str
    classification_source: Literal["codex-assisted"]
    classifier_version: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)

    @field_validator(
        "secondary_categories",
        "subtopics",
        "mentioned_brands",
        "mentioned_products",
        "review_triggers",
    )
    @classmethod
    def values_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("list values must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("list values must be non-empty strings")
        return values

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_short_and_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence values must be non-empty strings")
        if any(len(value) > 180 for value in values):
            raise ValueError("evidence excerpts must be short")
        return values

    @model_validator(mode="after")
    def validate_semantic_contract(self) -> CommentClassification:
        if self.primary_category in self.secondary_categories:
            raise ValueError("primary_category cannot be repeated in secondary_categories")
        if self.manual_review and not self.review_reason.strip():
            raise ValueError("review_reason is required when manual_review=true")
        if self.review_risk in {"medium", "high"} and not self.review_triggers:
            raise ValueError(
                "review_triggers are required when review_risk is medium or high"
            )
        if any(len(value) > 120 for value in self.review_triggers):
            raise ValueError("review_triggers must be concise")
        if self.review_risk == "high" and not self.manual_review:
            raise ValueError("review_risk=high requires manual_review=true")
        return self


@dataclass(frozen=True)
class PreparationSummary:
    input_file: Path
    output_dir: Path
    total_records: int
    batch_count: int
    batch_files: tuple[Path, ...]
    manifest_path: Path
    checksums_path: Path


@dataclass(frozen=True)
class MergeSummary:
    input_file: Path
    output_file: Path
    total_records: int
    classified_records: int
    manual_review_count: int
    category_counts: dict[str, int]
    failed_batches: int = 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ClassificationWorkflowError(f"Input file not found: {path}")
    if path.suffix.lower() not in {".csv", ".json"}:
        raise ClassificationWorkflowError(
            "Classification input must be a comment CSV or Generic Evidence JSON."
        )
    try:
        return load_rows(path)
    except (ValueError, ClassificationWorkflowError):
        raise
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Unable to load classification input: {exc}"
        ) from exc


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip()
        for item in str(value or "").split(";")
        if item.strip()
    ]


def _validate_source_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        raise ClassificationWorkflowError("Input CSV contains no comments.")
    ids = [str(row.get("comment_id") or "").strip() for row in rows]
    if any(not comment_id for comment_id in ids):
        raise ClassificationWorkflowError("Every input row must contain comment_id.")
    duplicates = sorted(
        comment_id for comment_id, count in Counter(ids).items() if count > 1
    )
    if duplicates:
        raise ClassificationWorkflowError(
            f"Duplicate input comment_id: {', '.join(duplicates)}"
        )
    if any(row.get("comment_text") is None for row in rows):
        raise ClassificationWorkflowError("Every input row must contain comment_text.")
    return ids


def _batch_payloads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top_by_id = {
        str(row.get("comment_id") or ""): row
        for row in rows
        if not _as_bool(row.get("is_reply"))
    }
    payloads: list[dict[str, Any]] = []
    for row in rows:
        parent = top_by_id.get(str(row.get("parent_comment_id") or ""))
        payloads.append(
            {
                "comment_id": str(row.get("comment_id") or ""),
                "comment_text": str(row.get("comment_text") or ""),
                "evidence_id": str(
                    row.get("evidence_id") or row.get("comment_id") or ""
                ),
                "source_type": str(row.get("source_type") or "youtube_comment"),
                "source_name": str(row.get("source_name") or ""),
                "language": str(row.get("language") or ""),
                "market": str(row.get("market") or ""),
                "region": str(row.get("region") or ""),
                "video_id": str(row.get("video_id") or ""),
                "is_reply": _as_bool(row.get("is_reply")),
                "parent_comment_id": str(row.get("parent_comment_id") or ""),
                "parent_comment_text": (
                    str(parent.get("comment_text") or "") if parent else ""
                ),
                "video_title": str(row.get("video_title") or ""),
                "channel_title": str(row.get("channel_title") or ""),
            }
        )
    return payloads


def prepare_classification_batches(
    input_file: Path,
    output_dir: Path,
    *,
    batch_size: int = 20,
    profile_id: str = DEFAULT_PROFILE_ID,
    classifier_version: str = DEFAULT_CLASSIFIER_VERSION,
    review_sample_seed: int = 20260816,
) -> PreparationSummary:
    if not 1 <= batch_size <= 20:
        raise ClassificationWorkflowError("--batch-size must be between 1 and 20.")
    profile = load_profile(profile_id)
    rows = _read_csv(input_file)
    input_ids = _validate_source_rows(rows)
    payloads = _batch_payloads(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    occupied = list(output_dir.glob("batch_*.jsonl")) + list(
        output_dir.glob("classified_batch_*.jsonl")
    )
    occupied += [
        path
        for path in (output_dir / "manifest.json", output_dir / "checksums.json")
        if path.exists()
    ]
    if occupied:
        raise ClassificationWorkflowError(
            "Output directory already contains classification workflow files."
        )

    batch_files: list[Path] = []
    manifest_batches: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(payloads), batch_size), start=1):
        batch = payloads[start : start + batch_size]
        path = output_dir / f"batch_{index:03d}.jsonl"
        path.write_text(
            "".join(_json_dump(item) + "\n" for item in batch),
            encoding="utf-8",
        )
        batch_files.append(path)
        manifest_batches.append(
            {
                "input_file": path.name,
                "classification_file": f"classified_{path.name}",
                "record_count": len(batch),
                "comment_ids": [item["comment_id"] for item in batch],
                "sha256": _sha256(path),
            }
        )

    manifest = {
        "workflow_version": WORKFLOW_VERSION,
        "profile_id": profile.profile_id,
        "profile_sha256": profile_sha256(profile.profile_id),
        "rubric_version": profile.rubric_version,
        "classifier_version": classifier_version,
        "rubric_sha256": _sha256(RUBRIC_PATH),
        "schema_sha256": _sha256(SCHEMA_PATH),
        "classification_timestamp": datetime.now(timezone.utc).isoformat(),
        "review_sample_seed": review_sample_seed,
        "schema_file": str(SCHEMA_PATH.relative_to(SKILL_ROOT)).replace("\\", "/"),
        "input_file": str(input_file.resolve()),
        "input_sha256": _sha256(input_file),
        "total_records": len(rows),
        "batch_size": batch_size,
        "batch_count": len(batch_files),
        "comment_ids": input_ids,
        "batch_fields": list(BATCH_INPUT_FIELDS),
        "batches": manifest_batches,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    checksums = {
        "algorithm": "sha256",
        "source_sha256": manifest["input_sha256"],
        "files": {
            **{path.name: _sha256(path) for path in batch_files},
            manifest_path.name: _sha256(manifest_path),
        },
    }
    checksums_path = output_dir / "checksums.json"
    checksums_path.write_text(
        json.dumps(checksums, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return PreparationSummary(
        input_file=input_file,
        output_dir=output_dir,
        total_records=len(rows),
        batch_count=len(batch_files),
        batch_files=tuple(batch_files),
        manifest_path=manifest_path,
        checksums_path=checksums_path,
    )


def create_blind_input_copy(input_file: Path, output_file: Path) -> int:
    """Create a Codex-safe CSV copy without predictions or expected answers."""
    if output_file.suffix.lower() != ".csv":
        raise ClassificationWorkflowError("--output must use the .csv extension.")
    if input_file.resolve() == output_file.resolve():
        raise ClassificationWorkflowError("Refusing to overwrite the input CSV.")
    rows = _read_csv(input_file)
    _validate_source_rows(rows)
    payloads = _batch_payloads(rows)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(BATCH_INPUT_FIELDS))
        writer.writeheader()
        writer.writerows(payloads)
    return len(payloads)


def load_classification_schema() -> dict[str, Any]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClassificationWorkflowError(
            f"Unable to load classification schema: {exc}"
        ) from exc
    required = set(schema.get("required") or [])
    if required != set(CommentClassification.model_fields):
        raise ClassificationWorkflowError(
            "Classification schema and runtime model fields are inconsistent."
        )
    return schema


def validate_classification_payload(
    payload: object,
    *,
    input_text: str | None = None,
    profile: ResearchProfile | str = DEFAULT_PROFILE_ID,
) -> CommentClassification:
    load_classification_schema()
    current_profile = load_profile(profile) if isinstance(profile, str) else profile
    try:
        result = CommentClassification.model_validate(payload)
        validate_profile_value(
            current_profile,
            primary_category=result.primary_category,
            secondary_categories=result.secondary_categories,
            subject=result.subject,
            stage=result.lifecycle_stage,
        )
        if result.rubric_version != current_profile.rubric_version:
            raise ClassificationWorkflowError(
                f"rubric_version {result.rubric_version!r} does not match profile "
                f"{current_profile.rubric_version!r}."
            )
        if (
            current_profile.profile_id == DEFAULT_PROFILE_ID
            and result.primary_category in PRODUCT_SUBJECT_CATEGORIES
            and result.subject not in {"product", "brand"}
        ):
            raise ClassificationWorkflowError(
                f"{result.primary_category} requires subject product or brand"
            )
    except Exception as exc:
        raise ClassificationWorkflowError(f"Schema validation failed: {exc}") from exc
    if input_text is not None:
        folded = input_text.casefold()
        missing_evidence = [
            evidence for evidence in result.evidence if evidence.casefold() not in folded
        ]
        if missing_evidence:
            raise ClassificationWorkflowError(
                "Evidence is not an exact excerpt from the comment: "
                + ", ".join(repr(value) for value in missing_evidence)
            )
    return result


def build_rule_candidate(row: dict[str, Any]) -> dict[str, Any]:
    result = classify_text(str(row.get("comment_text") or ""))
    categories = [result.primary_category, *result.secondary_categories]
    comparison_phrases = [
        match.matched_phrase
        for match in result.matched_rules
        if match.category == "Competitor Comparison"
    ]
    lifecycle_signals = (
        [result.lifecycle_stage] if result.lifecycle_stage != "Unknown" else []
    )
    return {
        "rule_classification_label": "Rule-based candidate / 规则候选",
        "rule_candidate_categories": "; ".join(categories),
        "rule_candidate_subtopics": "; ".join(result.subtopics),
        "detected_entities": _json_dump(
            {
                "brands": list(result.mentioned_brands),
                "products": list(result.mentioned_products),
            }
        ),
        "detected_comparison_phrases": _json_dump(comparison_phrases),
        "detected_lifecycle_signals": _json_dump(lifecycle_signals),
        "rule_evidence": result.category_evidence,
        # Legacy Phase 2A.1 fields remain available for compatibility only.
        **result.as_dict(),
    }


def _load_manifest(classification_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = classification_dir / "manifest.json"
    checksums_path = classification_dir / "checksums.json"
    if not manifest_path.exists() or not checksums_path.exists():
        raise ClassificationWorkflowError(
            "classification directory must contain manifest.json and checksums.json"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClassificationWorkflowError(f"Invalid manifest JSON: {exc}") from exc
    if checksums.get("files", {}).get("manifest.json") != _sha256(manifest_path):
        raise ClassificationWorkflowError("manifest.json checksum mismatch")
    batches = manifest.get("batches")
    if not isinstance(batches, list) or not batches:
        raise ClassificationWorkflowError("manifest.json contains no batches")
    if manifest.get("batch_count") != len(batches):
        raise ClassificationWorkflowError("Manifest batch count is inconsistent.")
    return manifest, checksums


def _load_classification_results(
    classification_dir: Path,
    manifest: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
    profile: ResearchProfile,
) -> dict[str, CommentClassification]:
    results: dict[str, CommentClassification] = {}
    for batch in manifest.get("batches") or []:
        result_path = classification_dir / str(batch["classification_file"])
        if not result_path.exists():
            raise ClassificationWorkflowError(
                f"Missing classification batch: {result_path.name}"
            )
        for line_number, line in enumerate(
            result_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClassificationWorkflowError(
                    f"{result_path.name}:{line_number}: invalid JSON: {exc}"
                ) from exc
            comment_id = (
                str(payload.get("comment_id") or "")
                if isinstance(payload, dict)
                else ""
            )
            if comment_id not in source_by_id:
                raise ClassificationWorkflowError(
                    f"Unknown comment_id in classification output: {comment_id}"
                )
            if comment_id in results:
                raise ClassificationWorkflowError(
                    f"Duplicate classification comment_id: {comment_id}"
                )
            results[comment_id] = validate_classification_payload(
                payload,
                input_text=str(source_by_id[comment_id].get("comment_text") or ""),
                profile=profile,
            )
    return results


def _load_history(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        history = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ClassificationWorkflowError(
            "classification_history is not valid JSON"
        ) from exc
    if not isinstance(history, list):
        raise ClassificationWorkflowError("classification_history must be a JSON list")
    return [item for item in history if isinstance(item, dict)]


def _old_codex_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in CODEX_FIELDS}


def merge_classifications(
    input_file: Path,
    classification_dir: Path,
    output_file: Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    review_sample_seed: int | None = None,
) -> MergeSummary:
    if output_file.suffix.lower() != ".csv":
        raise ClassificationWorkflowError("--output must use the .csv extension.")
    if input_file.resolve() == output_file.resolve():
        raise ClassificationWorkflowError("Refusing to overwrite the input CSV.")
    profile = load_profile(profile_id)
    rows = _read_csv(input_file)
    input_ids = _validate_source_rows(rows)
    source_by_id = {str(row["comment_id"]): row for row in rows}
    manifest, checksums = _load_manifest(classification_dir)

    if manifest.get("profile_id", DEFAULT_PROFILE_ID) != profile.profile_id:
        raise ClassificationWorkflowError(
            f"Classification profile mismatch: manifest={manifest.get('profile_id')}, "
            f"requested={profile.profile_id}."
        )
    if manifest.get("profile_sha256", profile_sha256(profile.profile_id)) != profile_sha256(profile.profile_id):
        raise ClassificationWorkflowError("Research profile checksum mismatch.")
    if manifest.get("rubric_version", RUBRIC_VERSION) != profile.rubric_version:
        raise ClassificationWorkflowError("Research profile rubric version mismatch.")
    if manifest.get("rubric_sha256", _sha256(RUBRIC_PATH)) != _sha256(RUBRIC_PATH):
        raise ClassificationWorkflowError("Classification rubric checksum mismatch.")
    if manifest.get("schema_sha256", _sha256(SCHEMA_PATH)) != _sha256(SCHEMA_PATH):
        raise ClassificationWorkflowError("Classification schema checksum mismatch.")

    if manifest.get("input_sha256") != _sha256(input_file):
        raise ClassificationWorkflowError("Input CSV does not match manifest checksum.")
    if manifest.get("comment_ids") != input_ids:
        raise ClassificationWorkflowError(
            "Input comment order or IDs do not match manifest."
        )
    if manifest.get("total_records") != len(rows):
        raise ClassificationWorkflowError("Manifest record count does not match input.")
    for batch in manifest.get("batches") or []:
        batch_path = classification_dir / str(batch["input_file"])
        if not batch_path.exists():
            raise ClassificationWorkflowError(
                f"Missing prepared batch: {batch_path.name}"
            )
        expected_hash = checksums.get("files", {}).get(batch_path.name)
        if expected_hash != _sha256(batch_path) or expected_hash != batch.get("sha256"):
            raise ClassificationWorkflowError(
                f"Prepared batch checksum mismatch: {batch_path.name}"
            )

    results = _load_classification_results(
        classification_dir,
        manifest,
        source_by_id,
        profile,
    )
    missing = [comment_id for comment_id in input_ids if comment_id not in results]
    if missing:
        raise ClassificationWorkflowError(
            "Missing classification comment_id: " + ", ".join(missing)
        )
    classifier_versions = {result.classifier_version for result in results.values()}
    if len(classifier_versions) != 1:
        raise ClassificationWorkflowError(
            "Classification results contain mixed classifier versions."
        )

    classified_at = datetime.now(timezone.utc).isoformat()
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        comment_id = str(row["comment_id"])
        classification = results[comment_id]
        rule_candidate = (
            build_rule_candidate(row)
            if profile.profile_id == DEFAULT_PROFILE_ID
            else {
                field: ("not available for this research profile" if field == "rule_classification_label" else "")
                for field in RULE_CANDIDATE_FIELDS
            }
        )
        merged = {**row, **rule_candidate}
        history = _load_history(row.get("classification_history"))
        if row.get("codex_primary_category"):
            history.append(_old_codex_snapshot(row))

        codex_values = {
            "codex_subject": classification.subject,
            "codex_primary_category": classification.primary_category,
            "codex_secondary_categories": "; ".join(
                classification.secondary_categories
            ),
            "codex_subtopics": "; ".join(classification.subtopics),
            "codex_lifecycle_stage": classification.lifecycle_stage,
            "codex_mentioned_brands": "; ".join(classification.mentioned_brands),
            "codex_mentioned_products": "; ".join(classification.mentioned_products),
            "codex_confidence": classification.confidence,
            "codex_evidence": _json_dump(classification.evidence),
            "codex_review_risk": classification.review_risk,
            "codex_review_triggers": _json_dump(classification.review_triggers),
            "codex_manual_review": classification.manual_review,
            "codex_review_reason": classification.review_reason,
            "classification_source": classification.classification_source,
            "classifier_version": classification.classifier_version,
            "rubric_version": classification.rubric_version,
            "classified_at": classified_at,
            "research_profile": profile.profile_id,
            "classification_history": _json_dump(history),
        }
        merged.update(codex_values)

        for field in HUMAN_REVIEW_FIELDS + REVIEW_CONTROL_FIELDS:
            merged[field] = row.get(field, "")
        review_status = str(row.get("review_status") or "").strip().casefold()
        if review_status and review_status not in REVIEW_STATUSES:
            raise ClassificationWorkflowError(
                f"Invalid review_status for {comment_id}: {review_status}"
            )
        merged["review_status"] = review_status or "pending"
        merged_rows.append(merged)

    merged_rows = apply_review_sampling(
        merged_rows,
        profile,
        seed=(
            review_sample_seed
            if review_sample_seed is not None
            else int(manifest.get("review_sample_seed", 20260816))
        ),
        sampled_at=classified_at,
    )

    original_fields = list(rows[0])
    added_fields = [
        *RULE_CANDIDATE_FIELDS,
        *CODEX_FIELDS,
        "classification_history",
        *HUMAN_REVIEW_FIELDS,
        *REVIEW_CONTROL_FIELDS,
    ]
    fieldnames = original_fields + [
        field for field in added_fields if field not in original_fields
    ]
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(merged_rows)

    category_counts = Counter(
        classification.primary_category for classification in results.values()
    )
    return MergeSummary(
        input_file=input_file,
        output_file=output_file,
        total_records=len(rows),
        classified_records=len(results),
        manual_review_count=sum(
            classification.manual_review for classification in results.values()
        ),
        category_counts=dict(sorted(category_counts.items())),
    )


def effective_classification(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("review_status") or "").strip().casefold()
    human_primary = str(row.get("human_confirmed_primary_category") or "").strip()
    if status in {"confirmed", "corrected"} and human_primary:
        return {
            "source": "human-reviewed",
            "primary_category": human_primary,
            "secondary_categories": str(
                row.get("human_confirmed_secondary_categories") or ""
            ),
            "subtopics": str(row.get("human_confirmed_subtopics") or ""),
            "lifecycle_stage": str(
                row.get("human_confirmed_lifecycle_stage") or "Unknown"
            ),
        }
    codex_primary = str(row.get("codex_primary_category") or "").strip()
    if codex_primary and status != "rejected":
        return {
            "source": "codex-assisted",
            "primary_category": codex_primary,
            "secondary_categories": str(row.get("codex_secondary_categories") or ""),
            "subtopics": str(row.get("codex_subtopics") or ""),
            "lifecycle_stage": str(row.get("codex_lifecycle_stage") or "Unknown"),
        }
    rule_categories = _split_values(row.get("rule_candidate_categories"))
    return {
        "source": "rule-candidate",
        "primary_category": rule_categories[0] if rule_categories else "Other",
        "secondary_categories": "; ".join(rule_categories[1:]),
        "subtopics": str(row.get("rule_candidate_subtopics") or ""),
        "lifecycle_stage": "Unknown",
    }


def prepare_report_rows(
    rows: list[dict[str, Any]],
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    review_sample_seed: int = 20260816,
) -> list[dict[str, Any]]:
    profile = load_profile(profile_id)
    prepared: list[dict[str, Any]] = []
    for source in rows:
        rule_values = (
            build_rule_candidate(source)
            if profile.profile_id == DEFAULT_PROFILE_ID
            else {
                field: (
                    "Rule-based candidate unavailable for this profile"
                    if field == "rule_classification_label"
                    else ""
                )
                for field in RULE_CANDIDATE_FIELDS
            }
        )
        row = {**source, **rule_values}
        for field in CODEX_FIELDS + HUMAN_REVIEW_FIELDS + REVIEW_CONTROL_FIELDS:
            row.setdefault(field, "")
        row.setdefault("research_profile", profile.profile_id)
        row.setdefault("classification_history", "[]")
        if row.get("codex_primary_category") and not row.get("review_status"):
            row["review_status"] = "pending"
        effective = effective_classification(row)
        row.update(
            {
                "effective_classification_source": effective["source"],
                "effective_primary_category": effective["primary_category"],
                "effective_secondary_categories": effective[
                    "secondary_categories"
                ],
                "effective_subtopics": effective["subtopics"],
                "effective_lifecycle_stage": effective["lifecycle_stage"],
            }
        )
        prepared.append(row)
    return apply_review_sampling(
        prepared,
        profile,
        seed=review_sample_seed,
        sampled_at=str(prepared[0].get("qa_sampled_at") or "") if prepared else None,
    )


def build_review_sections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    limitation = (
        "Confirmed findings require human status confirmed/corrected; "
        "Codex-assisted findings are preliminary and rule candidates are excluded."
    )
    pending_codex = [
        row
        for row in rows
        if row.get("codex_primary_category")
        and str(row.get("review_status") or "pending").casefold() == "pending"
    ]
    low_risk_preliminary = [
        row
        for row in pending_codex
        if str(row.get("codex_review_risk") or "").casefold() == "low"
        and not _as_bool(row.get("codex_manual_review"))
    ]
    medium_risk_preliminary = [
        row
        for row in pending_codex
        if str(row.get("codex_review_risk") or "").casefold() == "medium"
        and not _as_bool(row.get("codex_manual_review"))
    ]
    section_specs = (
        (
            "Confirmed Findings",
            "human-confirmed",
            [
                row
                for row in rows
                if str(row.get("review_status") or "").casefold()
                in {"confirmed", "corrected"}
                and row.get("human_confirmed_primary_category")
            ],
            "human_confirmed_primary_category",
            "human_confirmed_subtopics",
        ),
        (
            "Preliminary Codex-assisted Findings",
            "low-risk preliminary",
            low_risk_preliminary,
            "codex_primary_category",
            "codex_subtopics",
        ),
        (
            "Preliminary Codex-assisted Findings",
            "medium-risk preliminary",
            medium_risk_preliminary,
            "codex_primary_category",
            "codex_subtopics",
        ),
    )
    for (
        section,
        finding_status,
        scoped,
        category_field,
        subtopic_field,
    ) in section_specs:
        counts = Counter(str(row.get(category_field) or "") for row in scoped)
        if not scoped:
            output.append(
                {
                    "report_section": section,
                    "finding_status": finding_status,
                    "primary_category": "",
                    "category_cn": "",
                    "count": 0,
                    "share": 0,
                    "high_frequency_subtopics": "",
                    "representative_comment": "",
                    "representative_comment_id": "",
                    "review_status": "",
                    "review_risk": "",
                    "review_triggers": "",
                    "review_reason": "",
                    "data_limitation": limitation,
                }
            )
            continue
        for category, count in counts.most_common():
            matching = [row for row in scoped if row.get(category_field) == category]
            subtopics: Counter[str] = Counter()
            for row in matching:
                subtopics.update(_split_values(row.get(subtopic_field)))
            representative = max(
                matching,
                key=lambda row: _safe_int(row.get("comment_like_count")),
            )
            output.append(
                {
                    "report_section": section,
                    "finding_status": finding_status,
                    "primary_category": category,
                    "category_cn": CATEGORY_CN.get(category, ""),
                    "count": count,
                    "share": count / len(scoped),
                    "high_frequency_subtopics": "; ".join(
                        f"{topic} ({topic_count})"
                        for topic, topic_count in subtopics.most_common(3)
                    ),
                    "representative_comment": representative.get("comment_text", ""),
                    "representative_comment_id": representative.get("comment_id", ""),
                    "review_status": representative.get("review_status", ""),
                    "review_risk": representative.get("codex_review_risk", ""),
                    "review_triggers": representative.get(
                        "codex_review_triggers", ""
                    ),
                    "review_reason": representative.get("reviewer_note", ""),
                    "data_limitation": limitation,
                }
            )

    queue = review_queue(rows)
    if not queue:
        output.append(
            {
                "report_section": "Review Queue",
                "finding_status": "pending review",
                "primary_category": "",
                "category_cn": "",
                "count": 0,
                "share": 0,
                "high_frequency_subtopics": "",
                "representative_comment": "",
                "representative_comment_id": "",
                "review_status": "",
                "review_risk": "",
                "review_triggers": "",
                "review_reason": "",
                "data_limitation": limitation,
            }
        )
    else:
        for row in queue:
            category = str(row.get("codex_primary_category") or "")
            output.append(
                {
                    "report_section": "Review Queue",
                    "finding_status": "pending review",
                    "primary_category": category,
                    "category_cn": CATEGORY_CN.get(category, ""),
                    "count": 1,
                    "share": 1 / len(queue),
                    "high_frequency_subtopics": row.get("codex_subtopics", ""),
                    "representative_comment": row.get("comment_text", ""),
                    "representative_comment_id": row.get("comment_id", ""),
                    "review_status": row.get("review_status", ""),
                    "review_risk": row.get("codex_review_risk", ""),
                    "review_triggers": row.get("codex_review_triggers", ""),
                    "review_reason": (
                        row.get("codex_review_reason")
                        or row.get("reviewer_note")
                        or "Pending human review."
                    ),
                    "data_limitation": limitation,
                }
            )
    return output


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
