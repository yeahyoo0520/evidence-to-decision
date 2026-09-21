from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ClassificationWorkflowError
from .evidence import ResearchContext
from .human_review import HumanReviewBundle, load_human_review_bundle
from .theme_discovery import (
    InsightDraft,
    ThemeInsightBundle,
    ThemeRecord,
    load_theme_insight_bundle,
)

VerificationStatus = Literal["unreviewed", "reviewed", "edited", "rejected"]
InsightProvenance = Literal["ai_generated", "human_edited"]


class EffectiveInsightRecord(BaseModel):
    """One downstream-facing insight after optional verification is resolved."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    effective_insight_id: str = Field(min_length=1)
    source_insight_id: str = Field(min_length=1)
    source_theme_id: str = Field(min_length=1)
    theme_name: str = Field(min_length=1)
    theme_description: str = Field(min_length=1)
    final_observation: str = Field(min_length=1)
    final_interpretation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    representative_evidence_ids: list[str] = Field(max_length=3)
    verification_status: VerificationStatus
    reviewer_note: str = ""
    rejection_reason: str = ""
    review_recommended: bool
    review_reasons: list[str]
    downstream_eligible: bool
    confidence: Literal["low", "medium", "high"]
    taxonomy_coverage: Literal["covered", "partially_covered", "emergent"]
    coverage_reason: str = Field(min_length=1)
    research_context: ResearchContext
    provenance: InsightProvenance

    @field_validator("evidence_ids", "representative_evidence_ids", "review_reasons")
    @classmethod
    def unique_nonempty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("list values must be non-empty")
        if len(values) != len(set(values)):
            raise ValueError("list values must be unique")
        return values

    @model_validator(mode="after")
    def verification_contract(self) -> "EffectiveInsightRecord":
        if not set(self.representative_evidence_ids).issubset(self.evidence_ids):
            raise ValueError("representative evidence must belong to effective evidence")
        if self.review_recommended != bool(self.review_reasons):
            raise ValueError("review_recommended must match review_reasons")
        if self.verification_status == "edited" and self.provenance != "human_edited":
            raise ValueError("edited insights require human_edited provenance")
        if self.verification_status != "edited" and self.provenance != "ai_generated":
            raise ValueError("non-edited insights retain ai_generated provenance")
        if self.verification_status == "rejected" and self.downstream_eligible:
            raise ValueError("rejected insights cannot be downstream eligible")
        return self


class EvidenceInspectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_name: str | None = None
    metadata: Any = None
    representative: bool = False


class InsightEvidenceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_insight_id: str
    evidence_count: int = Field(ge=1)
    representative_evidence_ids: list[str]
    evidence: list[EvidenceInspectionRecord]


def _effective_id(source_insight_id: str) -> str:
    digest = hashlib.sha256(source_insight_id.encode("utf-8")).hexdigest()[:12]
    return f"effective-{digest}"


def _recommendation_reasons(
    theme: ThemeRecord,
    insight: InsightDraft,
    evidence_ids: list[str],
    representative_evidence_ids: list[str],
) -> list[str]:
    reasons: list[str] = []
    if insight.confidence == "low" or theme.confidence == "low":
        reasons.append("Low confidence")
    if len(evidence_ids) <= 2:
        count = len(evidence_ids)
        noun = "item" if count == 1 else "items"
        reasons.append(f"Only {count} supporting evidence {noun}")
    if theme.taxonomy_coverage == "emergent":
        reasons.append("Emergent theme not fully covered by the predefined taxonomy")
    if not representative_evidence_ids:
        reasons.append("No representative evidence is selected")
    return reasons


def resolve_effective_insights(
    source: ThemeInsightBundle | HumanReviewBundle,
    *,
    require_review: bool = False,
    include_rejected: bool = False,
) -> list[EffectiveInsightRecord]:
    """Resolve AI drafts and optional review into one downstream collection.

    By default, unreviewed, reviewed, and edited records are eligible. Setting
    ``require_review=True`` is an explicit strict mode that keeps only reviewed
    and edited records. Rejected records are never eligible.
    """

    if isinstance(source, HumanReviewBundle):
        source_bundle = source.source_bundle
        decisions = {
            item.source_insight_id: item for item in source.review_decisions
        }
        confirmed = {
            item.source_insight_id: item for item in source.confirmed_insights
        }
    else:
        source_bundle = source
        decisions = {}
        confirmed = {}

    themes = {theme.theme_id: theme for theme in source_bundle.themes}
    resolved: list[EffectiveInsightRecord] = []
    for draft in source_bundle.insight_drafts:
        theme = themes[draft.theme_id]
        decision = decisions.get(draft.insight_id)
        final = confirmed.get(draft.insight_id)

        if decision is None:
            verification_status: VerificationStatus = "unreviewed"
            observation = draft.observation
            interpretation = draft.interpretation
            evidence_ids = list(draft.evidence_ids)
            representative_ids = list(theme.representative_evidence_ids)
            theme_name = theme.theme_name
            theme_description = theme.theme_description
            reviewer_note = ""
            rejection_reason = ""
            provenance: InsightProvenance = "ai_generated"
        elif decision.action == "confirm":
            verification_status = "reviewed"
            observation = draft.observation
            interpretation = draft.interpretation
            evidence_ids = list(draft.evidence_ids)
            representative_ids = list(theme.representative_evidence_ids)
            theme_name = theme.theme_name
            theme_description = theme.theme_description
            reviewer_note = decision.reviewer_note
            rejection_reason = ""
            provenance = "ai_generated"
        elif decision.action == "edit":
            if final is None:
                raise ClassificationWorkflowError(
                    f"Edited review is missing its human-edited insight: {draft.insight_id}"
                )
            verification_status = "edited"
            observation = final.observation
            interpretation = final.interpretation
            evidence_ids = list(final.evidence_ids)
            representative_ids = list(final.representative_evidence_ids)
            theme_name = final.theme_name
            theme_description = final.theme_description
            reviewer_note = final.reviewer_note
            rejection_reason = ""
            provenance = "human_edited"
        else:
            verification_status = "rejected"
            observation = draft.observation
            interpretation = draft.interpretation
            evidence_ids = list(draft.evidence_ids)
            representative_ids = list(theme.representative_evidence_ids)
            theme_name = theme.theme_name
            theme_description = theme.theme_description
            reviewer_note = decision.reviewer_note
            rejection_reason = decision.rejection_reason
            provenance = "ai_generated"

        eligible = verification_status != "rejected" and (
            not require_review or verification_status in {"reviewed", "edited"}
        )
        reasons = _recommendation_reasons(
            theme,
            draft,
            evidence_ids,
            representative_ids,
        )
        record = EffectiveInsightRecord(
            effective_insight_id=_effective_id(draft.insight_id),
            source_insight_id=draft.insight_id,
            source_theme_id=theme.theme_id,
            theme_name=theme_name,
            theme_description=theme_description,
            final_observation=observation,
            final_interpretation=interpretation,
            evidence_ids=evidence_ids,
            representative_evidence_ids=representative_ids,
            verification_status=verification_status,
            reviewer_note=reviewer_note,
            rejection_reason=rejection_reason,
            review_recommended=bool(reasons),
            review_reasons=reasons,
            downstream_eligible=eligible,
            confidence=draft.confidence,
            taxonomy_coverage=theme.taxonomy_coverage,
            coverage_reason=theme.coverage_reason,
            research_context=source_bundle.research_context,
            provenance=provenance,
        )
        if include_rejected or record.verification_status != "rejected":
            if record.downstream_eligible:
                resolved.append(record)
            elif include_rejected:
                resolved.append(record)
    return resolved


def inspect_effective_insight_evidence(
    insight: EffectiveInsightRecord,
    source_rows: list[dict[str, Any]],
) -> InsightEvidenceInspection:
    """Resolve an Effective Insight to original source evidence without review."""

    source_by_id: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        evidence_id = str(row.get("evidence_id") or row.get("comment_id") or "").strip()
        if not evidence_id or evidence_id in source_by_id:
            raise ClassificationWorkflowError(
                "Evidence inspection requires unique, non-empty evidence IDs."
            )
        source_by_id[evidence_id] = row
    missing = sorted(set(insight.evidence_ids) - set(source_by_id))
    if missing:
        raise ClassificationWorkflowError(
            "Effective Insight references missing original evidence: " + ", ".join(missing)
        )
    representative = set(insight.representative_evidence_ids)
    records = []
    for evidence_id in insight.evidence_ids:
        row = source_by_id[evidence_id]
        metadata = row.get("metadata")
        if isinstance(metadata, str) and metadata.strip():
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                pass
        records.append(
            EvidenceInspectionRecord(
                evidence_id=evidence_id,
                text=str(row.get("text") or row.get("comment_text") or ""),
                source_type=str(row.get("source_type") or "youtube_comment"),
                source_name=(
                    str(row.get("source_name") or row.get("video_title") or "").strip()
                    or None
                ),
                metadata=metadata,
                representative=evidence_id in representative,
            )
        )
    return InsightEvidenceInspection(
        effective_insight_id=insight.effective_insight_id,
        evidence_count=len(records),
        representative_evidence_ids=list(insight.representative_evidence_ids),
        evidence=records,
    )


def resolve_effective_insight_file(
    input_file: Path,
    output_file: Path,
    *,
    human_review_file: Path | None = None,
    require_review: bool = False,
) -> list[EffectiveInsightRecord]:
    if output_file.suffix.lower() != ".jsonl":
        raise ClassificationWorkflowError("--output must use the .jsonl extension.")
    source_bundle = load_theme_insight_bundle(input_file)
    source: ThemeInsightBundle | HumanReviewBundle = source_bundle
    if human_review_file:
        review_bundle = load_human_review_bundle(human_review_file)
        source_sha256 = hashlib.sha256(input_file.read_bytes()).hexdigest()
        if review_bundle.source_bundle_sha256 != source_sha256:
            raise ClassificationWorkflowError(
                "Human Review bundle checksum does not match the source Theme bundle."
            )
        if review_bundle.source_bundle != source_bundle:
            raise ClassificationWorkflowError(
                "Human Review bundle embeds a different source Theme bundle."
            )
        source = review_bundle
    records = resolve_effective_insights(
        source,
        require_review=require_review,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        "".join(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    return records
