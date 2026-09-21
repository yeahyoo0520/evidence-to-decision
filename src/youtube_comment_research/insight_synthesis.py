from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ClassificationWorkflowError
from .profiles import DEFAULT_PROFILE_ID, ResearchProfile, load_profile, profile_sha256
from .review_sampling import as_bool, review_risk

INSIGHT_WORKFLOW_VERSION = "phase2b-lite-insight-v1"


class InsightRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    insight_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    theme_description: str = Field(min_length=1)
    evidence_count: int = Field(ge=1)
    evidence_comment_ids: list[str] = Field(min_length=1)
    representative_comment_ids: list[str]
    source_video_ids: list[str] = Field(min_length=1)
    source_count: int = Field(ge=1)
    insight: str = Field(min_length=1)
    action_opportunity: str = Field(min_length=1)
    priority: Literal["P0", "P1", "P2"]
    priority_reason: str = Field(min_length=1)
    confidence_level: Literal["low", "medium", "high"]
    status: Literal["preliminary", "confirmed", "rejected"]
    evidence_review_status: Literal[
        "not_required", "pending", "confirmed", "rejected"
    ]
    needs_human_confirmation: bool
    created_at: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)

    @field_validator(
        "evidence_comment_ids", "representative_comment_ids", "source_video_ids"
    )
    @classmethod
    def unique_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("insight ID arrays must contain non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError("insight ID arrays must not contain duplicates")
        return values

    @model_validator(mode="after")
    def counts_and_representatives_match(self) -> "InsightRecord":
        if self.evidence_count != len(self.evidence_comment_ids):
            raise ValueError("evidence_count must equal evidence_comment_ids length")
        if self.source_count != len(self.source_video_ids):
            raise ValueError("source_count must equal source_video_ids length")
        if not set(self.representative_comment_ids).issubset(
            self.evidence_comment_ids
        ):
            raise ValueError("representative comments must be evidence comments")
        return self


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.suffix.lower() != ".csv":
        raise ClassificationWorkflowError(f"Insight source must be an existing CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ClassificationWorkflowError("Insight source contains no comments.")
    ids = [str(row.get("comment_id") or "") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ClassificationWorkflowError(
            "Insight source requires unique, non-empty comment_id values."
        )
    return rows


def _verified_evidence(row: dict[str, Any]) -> bool:
    return (
        str(row.get("qa_status") or "").casefold() in {"confirmed", "corrected"}
        or str(row.get("evidence_review_status") or "").casefold() == "confirmed"
        or str(row.get("review_status") or "").casefold()
        in {"confirmed", "corrected"}
    )


def validate_insight_payload(
    payload: object,
    source_rows: list[dict[str, Any]],
    *,
    profile: ResearchProfile | str = DEFAULT_PROFILE_ID,
) -> InsightRecord:
    current_profile = load_profile(profile) if isinstance(profile, str) else profile
    try:
        result = InsightRecord.model_validate(payload)
    except Exception as exc:
        raise ClassificationWorkflowError(f"Insight schema validation failed: {exc}") from exc
    if result.profile_id != current_profile.profile_id:
        raise ClassificationWorkflowError(
            f"Insight profile {result.profile_id} does not match {current_profile.profile_id}."
        )
    if result.rubric_version != current_profile.rubric_version:
        raise ClassificationWorkflowError("Insight rubric_version does not match profile.")

    source_by_id = {str(row.get("comment_id") or ""): row for row in source_rows}
    unknown = sorted(set(result.evidence_comment_ids) - set(source_by_id))
    if unknown:
        raise ClassificationWorkflowError(
            "Insight references unknown comment_id: " + ", ".join(unknown)
        )
    actual_video_ids = sorted(
        {
            str(source_by_id[comment_id].get("video_id") or "")
            for comment_id in result.evidence_comment_ids
            if source_by_id[comment_id].get("video_id")
        }
    )
    if sorted(result.source_video_ids) != actual_video_ids:
        raise ClassificationWorkflowError(
            "source_video_ids must exactly match the insight evidence comments."
        )
    if result.status == "confirmed":
        unverified = [
            comment_id
            for comment_id in result.evidence_comment_ids
            if not _verified_evidence(source_by_id[comment_id])
        ]
        if unverified:
            raise ClassificationWorkflowError(
                "Confirmed insight contains unverified evidence: "
                + ", ".join(unverified)
            )
        if result.evidence_review_status != "confirmed":
            raise ClassificationWorkflowError(
                "Confirmed insight requires evidence_review_status=confirmed."
            )
    return result


@dataclass(frozen=True)
class InsightPreparationSummary:
    source_file: Path
    output_dir: Path
    context_file: Path
    manifest_file: Path
    total_records: int


def prepare_insight_context(
    source_file: Path,
    output_dir: Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> InsightPreparationSummary:
    profile = load_profile(profile_id)
    rows = _read_csv(source_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    context_file = output_dir / "insight_context.jsonl"
    manifest_file = output_dir / "insight_manifest.json"
    if context_file.exists() or manifest_file.exists():
        raise ClassificationWorkflowError(
            "Insight output directory already contains workflow files."
        )
    context_rows = []
    for row in rows:
        context_rows.append(
            {
                "comment_id": str(row.get("comment_id") or ""),
                "comment_text": str(row.get("comment_text") or ""),
                "video_id": str(row.get("video_id") or ""),
                "video_title": str(row.get("video_title") or ""),
                "channel_title": str(row.get("channel_title") or ""),
                "primary_category": str(
                    row.get("human_confirmed_primary_category")
                    or row.get("codex_primary_category")
                    or ""
                ),
                "secondary_categories": str(
                    row.get("human_confirmed_secondary_categories")
                    or row.get("codex_secondary_categories")
                    or ""
                ),
                "subtopics": str(
                    row.get("human_confirmed_subtopics")
                    or row.get("codex_subtopics")
                    or ""
                ),
                "stage": str(
                    row.get("human_confirmed_lifecycle_stage")
                    or row.get("codex_lifecycle_stage")
                    or "Unknown"
                ),
                "review_risk": review_risk(row),
                "mandatory_review": as_bool(row.get("codex_manual_review")),
                "qa_status": str(row.get("qa_status") or "not_selected"),
                "evidence_review_status": str(
                    row.get("evidence_review_status") or "not_required"
                ),
                "review_status": str(row.get("review_status") or "pending"),
            }
        )
    context_file.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in context_rows),
        encoding="utf-8",
    )
    manifest = {
        "workflow_version": INSIGHT_WORKFLOW_VERSION,
        "profile_id": profile.profile_id,
        "profile_sha256": profile_sha256(profile.profile_id),
        "rubric_version": profile.rubric_version,
        "source_file": str(source_file.resolve()),
        "source_sha256": _sha256(source_file),
        "context_sha256": _sha256(context_file),
        "comment_ids": [row["comment_id"] for row in context_rows],
        "research_goal": profile.research_goal,
        "insight_questions": profile.insight_questions,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return InsightPreparationSummary(
        source_file=source_file,
        output_dir=output_dir,
        context_file=context_file,
        manifest_file=manifest_file,
        total_records=len(rows),
    )


def merge_insights(
    source_file: Path,
    insight_dir: Path,
    output_file: Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> list[InsightRecord]:
    profile = load_profile(profile_id)
    rows = _read_csv(source_file)
    manifest_path = insight_dir / "insight_manifest.json"
    context_path = insight_dir / "insight_context.jsonl"
    result_path = insight_dir / "insights.jsonl"
    if not all(path.exists() for path in (manifest_path, context_path, result_path)):
        raise ClassificationWorkflowError(
            "Insight directory must contain insight_manifest.json, insight_context.jsonl, and insights.jsonl."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("profile_id") != profile.profile_id:
        raise ClassificationWorkflowError("Insight manifest profile mismatch.")
    if manifest.get("profile_sha256") != profile_sha256(profile.profile_id):
        raise ClassificationWorkflowError("Insight profile checksum mismatch.")
    if manifest.get("source_sha256") != _sha256(source_file):
        raise ClassificationWorkflowError("Insight source checksum mismatch.")
    if manifest.get("context_sha256") != _sha256(context_path):
        raise ClassificationWorkflowError("Insight context checksum mismatch.")

    results: list[InsightRecord] = []
    ids: set[str] = set()
    for line_number, line in enumerate(
        result_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClassificationWorkflowError(
                f"insights.jsonl:{line_number}: invalid JSON: {exc}"
            ) from exc
        result = validate_insight_payload(payload, rows, profile=profile)
        if result.insight_id in ids:
            raise ClassificationWorkflowError(
                f"Duplicate insight_id: {result.insight_id}"
            )
        ids.add(result.insight_id)
        results.append(result)
    if not results:
        raise ClassificationWorkflowError("insights.jsonl contains no insights.")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        "".join(result.model_dump_json() + "\n" for result in results),
        encoding="utf-8",
    )
    return results


def load_insights_file(
    path: Path,
    source_rows: list[dict[str, Any]],
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> list[InsightRecord]:
    if not path.exists() or path.suffix.lower() != ".jsonl":
        raise ClassificationWorkflowError(
            f"Insights must be an existing JSONL file: {path}"
        )
    profile = load_profile(profile_id)
    results: list[InsightRecord] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClassificationWorkflowError(
                f"{path.name}:{line_number}: invalid JSON: {exc}"
            ) from exc
        results.append(validate_insight_payload(payload, source_rows, profile=profile))
    return results


def mark_insight_evidence(
    rows: list[dict[str, Any]], insights: list[InsightRecord | dict[str, Any]]
) -> list[dict[str, Any]]:
    evidence_ids: set[str] = set()
    for insight in insights:
        payload = insight.model_dump() if isinstance(insight, InsightRecord) else insight
        evidence_ids.update(str(value) for value in payload.get("evidence_comment_ids", []))
    output = []
    for source in rows:
        row = dict(source)
        if str(row.get("comment_id") or "") in evidence_ids:
            row["evidence_review_required"] = True
            current = str(row.get("evidence_review_status") or "").casefold()
            if current not in {"confirmed", "rejected"}:
                row["evidence_review_status"] = "pending"
        output.append(row)
    return output


def insight_section(insight: InsightRecord | dict[str, Any]) -> str:
    payload = insight.model_dump() if isinstance(insight, InsightRecord) else insight
    if payload.get("status") == "confirmed" and payload.get("evidence_review_status") == "confirmed":
        return "Confirmed Findings"
    if payload.get("status") == "preliminary" and not payload.get("needs_human_confirmation"):
        return "Preliminary Insights"
    return "Insight Review Queue"
