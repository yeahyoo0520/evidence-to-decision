from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ClassificationWorkflowError
from .theme_discovery import InsightDraft, ThemeInsightBundle, ThemeRecord, load_theme_insight_bundle

REVIEW_BUNDLE_VERSION = "phase3-lite-2-v1"
ReviewAction = Literal["confirm", "edit", "reject"]
AppliedReviewStatus = Literal["human_confirmed", "human_edited", "rejected"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_nonempty(values: list[str], *, field_name: str) -> list[str]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class RepresentativeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    comment_id: str = Field(min_length=1)
    comment_text: str = Field(min_length=1)


class ReviewItem(BaseModel):
    """Immutable AI research unit presented to a human reviewer."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    review_id: str = Field(min_length=1)
    source_theme_id: str = Field(min_length=1)
    source_insight_id: str = Field(min_length=1)
    theme: ThemeRecord
    insight_draft: InsightDraft
    representative_evidence: list[RepresentativeEvidence]
    review_status: Literal["pending_review"] = "pending_review"

    @model_validator(mode="after")
    def source_chain_matches(self) -> "ReviewItem":
        if self.source_theme_id != self.theme.theme_id:
            raise ValueError("source_theme_id does not match the embedded Theme")
        if self.source_insight_id != self.insight_draft.insight_id:
            raise ValueError("source_insight_id does not match the embedded Insight Draft")
        if self.review_id != _review_id(
            self.source_theme_id, self.source_insight_id
        ):
            raise ValueError("review_id does not match its source Theme and Insight")
        if self.insight_draft.theme_id != self.theme.theme_id:
            raise ValueError("Insight Draft does not reference the embedded Theme")
        if not set(self.insight_draft.evidence_ids).issubset(
            self.theme.evidence_comment_ids
        ):
            raise ValueError("Insight Draft evidence must belong to Theme evidence")
        expected = list(
            zip(
                self.theme.representative_evidence_ids,
                self.theme.representative_comments,
                strict=True,
            )
        )
        actual = [
            (item.comment_id, item.comment_text)
            for item in self.representative_evidence
        ]
        if actual != expected:
            raise ValueError(
                "representative evidence text must match the Theme comment_id mapping"
            )
        return self


class HumanReviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    packet_version: Literal["phase3-lite-2-v1"] = REVIEW_BUNDLE_VERSION
    profile_id: str = Field(min_length=1)
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_at: str = Field(min_length=1)
    items: list[ReviewItem]

    @model_validator(mode="after")
    def unique_review_ids(self) -> "HumanReviewPacket":
        review_ids = [item.review_id for item in self.items]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("Review Packet contains duplicate review_id values")
        return self


class HumanReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    review_id: str = Field(min_length=1)
    action: ReviewAction
    reviewer_note: str = ""
    rejection_reason: str = ""
    reviewed_at: str | None = None
    theme_name_override: str | None = Field(default=None, min_length=1)
    theme_description_override: str | None = Field(default=None, min_length=1)
    observation_override: str | None = Field(default=None, min_length=1)
    interpretation_override: str | None = Field(default=None, min_length=1)
    selected_evidence_ids: list[str] | None = Field(default=None, min_length=1)
    representative_evidence_ids: list[str] | None = Field(
        default=None, min_length=1, max_length=3
    )

    @field_validator(
        "reviewer_note",
        "rejection_reason",
        "reviewed_at",
        "theme_name_override",
        "theme_description_override",
        "observation_override",
        "interpretation_override",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("selected_evidence_ids", "representative_evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: list[str] | None) -> list[str] | None:
        return (
            _unique_nonempty(values, field_name="review evidence IDs")
            if values is not None
            else None
        )

    @model_validator(mode="after")
    def action_contract(self) -> "HumanReviewDecision":
        override_values = (
            self.theme_name_override,
            self.theme_description_override,
            self.observation_override,
            self.interpretation_override,
            self.selected_evidence_ids,
            self.representative_evidence_ids,
        )
        if self.action == "confirm" and any(value is not None for value in override_values):
            raise ValueError("confirm must not include edit override fields")
        if self.action == "edit" and not any(value is not None for value in override_values):
            raise ValueError("edit requires at least one modification field")
        if self.action in {"confirm", "edit"} and self.rejection_reason:
            raise ValueError("rejection_reason is only valid for reject")
        if self.action == "reject" and not (
            self.rejection_reason or self.reviewer_note
        ):
            raise ValueError("reject requires rejection_reason or reviewer_note")
        if self.action == "reject" and any(value is not None for value in override_values):
            raise ValueError("reject must not include edit override fields")
        return self


class HumanReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    response_version: Literal["phase3-lite-2-v1"] = REVIEW_BUNDLE_VERSION
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: list[HumanReviewDecision]

    @model_validator(mode="after")
    def decisions_are_unique(self) -> "HumanReviewResponse":
        review_ids = [decision.review_id for decision in self.decisions]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError(
                "A Review Item cannot contain duplicate or conflicting final decisions"
            )
        return self


class AppliedReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    review_id: str = Field(min_length=1)
    source_theme_id: str = Field(min_length=1)
    source_insight_id: str = Field(min_length=1)
    action: ReviewAction
    review_status: AppliedReviewStatus
    reviewer_note: str = ""
    rejection_reason: str = ""
    reviewed_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def status_matches_action(self) -> "AppliedReviewDecision":
        expected: AppliedReviewStatus = {
            "confirm": "human_confirmed",
            "edit": "human_edited",
            "reject": "rejected",
        }[self.action]
        if self.review_status != expected:
            raise ValueError("applied review status does not match action")
        return self


class ConfirmedInsightRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    confirmed_insight_id: str = Field(min_length=1)
    source_theme_id: str = Field(min_length=1)
    source_insight_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    review_action: Literal["confirm", "edit"]
    theme_name: str = Field(min_length=1)
    theme_description: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    representative_evidence_ids: list[str] = Field(max_length=3)
    discovery_method: Literal["taxonomy_based", "llm_inductive"]
    taxonomy_coverage: Literal["covered", "partially_covered", "emergent"]
    coverage_reason: str = Field(min_length=1)
    reviewer_note: str = ""
    reviewed_at: str = Field(min_length=1)
    status: Literal["human_confirmed", "human_edited"]

    @field_validator("evidence_ids", "representative_evidence_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values, field_name="confirmed evidence IDs")

    @model_validator(mode="after")
    def confirmed_chain_is_valid(self) -> "ConfirmedInsightRecord":
        if not set(self.representative_evidence_ids).issubset(self.evidence_ids):
            raise ValueError(
                "representative evidence IDs must belong to confirmed evidence IDs"
            )
        expected_status = (
            "human_confirmed" if self.review_action == "confirm" else "human_edited"
        )
        if self.status != expected_status:
            raise ValueError("confirmed insight status does not match review_action")
        return self


class HumanReviewBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bundle_version: Literal["phase3-lite-2-v1"] = REVIEW_BUNDLE_VERSION
    profile_id: str = Field(min_length=1)
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: str = Field(min_length=1)
    source_bundle: ThemeInsightBundle
    review_items: list[ReviewItem]
    review_decisions: list[AppliedReviewDecision]
    confirmed_insights: list[ConfirmedInsightRecord]
    rejected_review_ids: list[str]
    pending_review_ids: list[str]

    @field_validator("rejected_review_ids", "pending_review_ids")
    @classmethod
    def unique_review_id_lists(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values, field_name="review ID lists")

    @model_validator(mode="after")
    def audit_chain_is_consistent(self) -> "HumanReviewBundle":
        if self.profile_id != self.source_bundle.profile_id:
            raise ValueError("Human Review profile does not match source bundle")
        items_by_id = {item.review_id: item for item in self.review_items}
        if len(items_by_id) != len(self.review_items):
            raise ValueError("Human Review bundle contains duplicate review_id values")
        decisions_by_id = {
            decision.review_id: decision for decision in self.review_decisions
        }
        if len(decisions_by_id) != len(self.review_decisions):
            raise ValueError("Human Review bundle contains conflicting review decisions")
        unknown_decisions = sorted(set(decisions_by_id) - set(items_by_id))
        if unknown_decisions:
            raise ValueError(
                "Review decisions reference unknown review_id values: "
                + ", ".join(unknown_decisions)
            )
        source_themes = {
            theme.theme_id: theme for theme in self.source_bundle.themes
        }
        source_insights = {
            insight.insight_id: insight for insight in self.source_bundle.insight_drafts
        }
        if set(items_by_id) != {
            _review_id(insight.theme_id, insight.insight_id)
            for insight in self.source_bundle.insight_drafts
        }:
            raise ValueError("Review Items do not match the source Insight Draft set")
        for item in self.review_items:
            if source_themes.get(item.source_theme_id) != item.theme:
                raise ValueError("Review Item Theme snapshot does not match source bundle")
            if source_insights.get(item.source_insight_id) != item.insight_draft:
                raise ValueError(
                    "Review Item Insight Draft snapshot does not match source bundle"
                )
        for review_id, decision in decisions_by_id.items():
            item = items_by_id[review_id]
            if decision.source_theme_id != item.source_theme_id:
                raise ValueError("Review decision source_theme_id is inconsistent")
            if decision.source_insight_id != item.source_insight_id:
                raise ValueError("Review decision source_insight_id is inconsistent")
        expected_pending = set(items_by_id) - set(decisions_by_id)
        if set(self.pending_review_ids) != expected_pending:
            raise ValueError("pending_review_ids do not match unapplied Review Items")
        expected_rejected = {
            item.review_id
            for item in self.review_decisions
            if item.action == "reject"
        }
        if set(self.rejected_review_ids) != expected_rejected:
            raise ValueError("rejected_review_ids do not match rejected decisions")
        confirmed_by_review = {
            item.review_id: item for item in self.confirmed_insights
        }
        if len(confirmed_by_review) != len(self.confirmed_insights):
            raise ValueError("A Review Item cannot create multiple Confirmed Insights")
        expected_confirmed = {
            item.review_id
            for item in self.review_decisions
            if item.action in {"confirm", "edit"}
        }
        if set(confirmed_by_review) != expected_confirmed:
            raise ValueError(
                "Confirmed Insight collection does not match confirm/edit decisions"
            )
        for review_id, confirmed in confirmed_by_review.items():
            item = items_by_id[review_id]
            decision = decisions_by_id[review_id]
            if confirmed.confirmed_insight_id != _confirmed_id(
                review_id, decision.action
            ):
                raise ValueError("confirmed_insight_id does not match its review decision")
            if confirmed.source_theme_id != item.source_theme_id:
                raise ValueError("Confirmed Insight source_theme_id is inconsistent")
            if confirmed.source_insight_id != item.source_insight_id:
                raise ValueError("Confirmed Insight source_insight_id is inconsistent")
            if not set(confirmed.evidence_ids).issubset(
                item.theme.evidence_comment_ids
            ):
                raise ValueError("Confirmed evidence must be a subset of Theme evidence")
            if confirmed.review_action != decision.action:
                raise ValueError("Confirmed Insight action is inconsistent")
            if confirmed.discovery_method != item.theme.discovery_method:
                raise ValueError("Confirmed Insight discovery_method is inconsistent")
            if confirmed.taxonomy_coverage != item.theme.taxonomy_coverage:
                raise ValueError("Confirmed Insight taxonomy_coverage is inconsistent")
            if confirmed.coverage_reason != item.theme.coverage_reason:
                raise ValueError("Confirmed Insight coverage_reason is inconsistent")
            original_values = (
                item.theme.theme_name,
                item.theme.theme_description,
                item.insight_draft.observation,
                item.insight_draft.interpretation,
                item.theme.evidence_comment_ids,
                item.theme.representative_evidence_ids,
            )
            final_values = (
                confirmed.theme_name,
                confirmed.theme_description,
                confirmed.observation,
                confirmed.interpretation,
                confirmed.evidence_ids,
                confirmed.representative_evidence_ids,
            )
            if decision.action == "confirm" and final_values != original_values:
                raise ValueError("Confirm must preserve the original AI Theme and Draft")
            if decision.action == "edit" and final_values == original_values:
                raise ValueError("Edit must preserve at least one actual human change")
        return self


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _review_id(theme_id: str, insight_id: str) -> str:
    digest = hashlib.sha256(f"{theme_id}\x1f{insight_id}".encode("utf-8")).hexdigest()
    return f"review-{digest[:12]}"


def _confirmed_id(review_id: str, action: str) -> str:
    digest = hashlib.sha256(f"{review_id}\x1f{action}".encode("utf-8")).hexdigest()
    return f"confirmed-{digest[:12]}"


def build_review_items(bundle: ThemeInsightBundle) -> list[ReviewItem]:
    themes_by_id = {theme.theme_id: theme for theme in bundle.themes}
    items: list[ReviewItem] = []
    for insight in bundle.insight_drafts:
        theme = themes_by_id.get(insight.theme_id)
        if theme is None:
            raise ClassificationWorkflowError(
                f"Insight Draft references nonexistent source_theme_id: {insight.theme_id}"
            )
        if len(theme.representative_evidence_ids) != len(
            theme.representative_comments
        ):
            raise ClassificationWorkflowError(
                f"Theme {theme.theme_id} has mismatched representative evidence text."
            )
        items.append(
            ReviewItem(
                review_id=_review_id(theme.theme_id, insight.insight_id),
                source_theme_id=theme.theme_id,
                source_insight_id=insight.insight_id,
                theme=theme.model_copy(deep=True),
                insight_draft=insight.model_copy(deep=True),
                representative_evidence=[
                    RepresentativeEvidence(comment_id=comment_id, comment_text=text)
                    for comment_id, text in zip(
                        theme.representative_evidence_ids,
                        theme.representative_comments,
                        strict=True,
                    )
                ],
                review_status="pending_review",
            )
        )
    missing_theme_ids = sorted(
        set(themes_by_id) - {insight.theme_id for insight in bundle.insight_drafts}
    )
    if missing_theme_ids:
        raise ClassificationWorkflowError(
            "Preliminary Themes are missing bound Insight Drafts: "
            + ", ".join(missing_theme_ids)
        )
    return items


def build_review_packet(
    bundle: ThemeInsightBundle,
    *,
    source_bundle_sha256: str,
    prepared_at: str | None = None,
) -> HumanReviewPacket:
    return HumanReviewPacket(
        profile_id=bundle.profile_id,
        source_bundle_sha256=source_bundle_sha256,
        prepared_at=prepared_at or _utc_now(),
        items=build_review_items(bundle),
    )


def prepare_review_packet(input_file: Path, output_file: Path) -> HumanReviewPacket:
    _validate_json_output(input_file, output_file)
    bundle = load_theme_insight_bundle(input_file)
    packet = build_review_packet(
        bundle,
        source_bundle_sha256=_source_sha256(input_file),
    )
    _write_json(output_file, packet)
    return packet


def _effective_edit_exists(
    decision: HumanReviewDecision,
    item: ReviewItem,
    selected_evidence_ids: list[str],
    representative_evidence_ids: list[str],
) -> bool:
    return any(
        (
            decision.theme_name_override is not None
            and decision.theme_name_override != item.theme.theme_name,
            decision.theme_description_override is not None
            and decision.theme_description_override != item.theme.theme_description,
            decision.observation_override is not None
            and decision.observation_override != item.insight_draft.observation,
            decision.interpretation_override is not None
            and decision.interpretation_override != item.insight_draft.interpretation,
            decision.selected_evidence_ids is not None
            and selected_evidence_ids != item.theme.evidence_comment_ids,
            decision.representative_evidence_ids is not None
            and representative_evidence_ids != item.theme.representative_evidence_ids,
        )
    )


def apply_review_response(
    bundle: ThemeInsightBundle,
    response: HumanReviewResponse,
    *,
    source_bundle_sha256: str,
    generated_at: str | None = None,
) -> HumanReviewBundle:
    if response.source_bundle_sha256 != source_bundle_sha256:
        raise ClassificationWorkflowError(
            "Human Review Response checksum does not match the source Theme bundle."
        )
    review_items = build_review_items(bundle)
    items_by_id = {item.review_id: item for item in review_items}
    decisions_by_id = {decision.review_id: decision for decision in response.decisions}
    unknown = sorted(set(decisions_by_id) - set(items_by_id))
    if unknown:
        raise ClassificationWorkflowError(
            "Human Review Response references unknown review_id values: "
            + ", ".join(unknown)
        )

    applied: list[AppliedReviewDecision] = []
    confirmed: list[ConfirmedInsightRecord] = []
    rejected_ids: list[str] = []
    for item in review_items:
        decision = decisions_by_id.get(item.review_id)
        if decision is None:
            continue
        reviewed_at = decision.reviewed_at or _utc_now()
        if decision.action == "reject":
            rejected_ids.append(item.review_id)
            applied.append(
                AppliedReviewDecision(
                    review_id=item.review_id,
                    source_theme_id=item.source_theme_id,
                    source_insight_id=item.source_insight_id,
                    action="reject",
                    review_status="rejected",
                    reviewer_note=decision.reviewer_note,
                    rejection_reason=decision.rejection_reason,
                    reviewed_at=reviewed_at,
                )
            )
            continue

        selected_evidence_ids = list(
            decision.selected_evidence_ids
            if decision.selected_evidence_ids is not None
            else item.theme.evidence_comment_ids
        )
        outside_theme = sorted(
            set(selected_evidence_ids) - set(item.theme.evidence_comment_ids)
        )
        if outside_theme:
            raise ClassificationWorkflowError(
                f"Review {item.review_id} selects evidence outside the source Theme: "
                + ", ".join(outside_theme)
            )
        representative_ids = list(
            decision.representative_evidence_ids
            if decision.representative_evidence_ids is not None
            else item.theme.representative_evidence_ids
        )
        outside_selected = sorted(
            set(representative_ids) - set(selected_evidence_ids)
        )
        if outside_selected:
            raise ClassificationWorkflowError(
                f"Review {item.review_id} representative evidence is outside selected evidence: "
                + ", ".join(outside_selected)
            )
        if decision.action == "edit" and not _effective_edit_exists(
            decision, item, selected_evidence_ids, representative_ids
        ):
            raise ClassificationWorkflowError(
                f"Review {item.review_id} edit does not make an actual change."
            )

        status: AppliedReviewStatus = (
            "human_confirmed" if decision.action == "confirm" else "human_edited"
        )
        applied.append(
            AppliedReviewDecision(
                review_id=item.review_id,
                source_theme_id=item.source_theme_id,
                source_insight_id=item.source_insight_id,
                action=decision.action,
                review_status=status,
                reviewer_note=decision.reviewer_note,
                rejection_reason="",
                reviewed_at=reviewed_at,
            )
        )
        confirmed.append(
            ConfirmedInsightRecord(
                confirmed_insight_id=_confirmed_id(item.review_id, decision.action),
                source_theme_id=item.source_theme_id,
                source_insight_id=item.source_insight_id,
                review_id=item.review_id,
                review_action=decision.action,
                theme_name=decision.theme_name_override or item.theme.theme_name,
                theme_description=(
                    decision.theme_description_override
                    or item.theme.theme_description
                ),
                observation=(
                    decision.observation_override or item.insight_draft.observation
                ),
                interpretation=(
                    decision.interpretation_override
                    or item.insight_draft.interpretation
                ),
                evidence_ids=selected_evidence_ids,
                representative_evidence_ids=representative_ids,
                discovery_method=item.theme.discovery_method,
                taxonomy_coverage=item.theme.taxonomy_coverage,
                coverage_reason=item.theme.coverage_reason,
                reviewer_note=decision.reviewer_note,
                reviewed_at=reviewed_at,
                status=status,
            )
        )

    pending_ids = [
        item.review_id for item in review_items if item.review_id not in decisions_by_id
    ]
    try:
        return HumanReviewBundle(
            profile_id=bundle.profile_id,
            source_bundle_sha256=source_bundle_sha256,
            generated_at=generated_at or _utc_now(),
            source_bundle=bundle.model_copy(deep=True),
            review_items=review_items,
            review_decisions=applied,
            confirmed_insights=confirmed,
            rejected_review_ids=rejected_ids,
            pending_review_ids=pending_ids,
        )
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Human Review bundle validation failed: {exc}"
        ) from exc


def apply_review_file(
    input_file: Path,
    review_file: Path,
    output_file: Path,
) -> HumanReviewBundle:
    _validate_json_output(input_file, output_file)
    if review_file.resolve() == output_file.resolve():
        raise ClassificationWorkflowError(
            "--output must not overwrite the Human Review Response."
        )
    bundle = load_theme_insight_bundle(input_file)
    response = load_human_review_response(review_file)
    result = apply_review_response(
        bundle,
        response,
        source_bundle_sha256=_source_sha256(input_file),
    )
    _write_json(output_file, result)
    return result


def load_human_review_response(path: Path) -> HumanReviewResponse:
    return _load_model(path, HumanReviewResponse, "Human Review Response")


def load_human_review_bundle(path: Path) -> HumanReviewBundle:
    return _load_model(path, HumanReviewBundle, "Human Review bundle")


def _load_model(path: Path, model: type[BaseModel], label: str):
    if not path.exists() or path.suffix.lower() != ".json":
        raise ClassificationWorkflowError(
            f"{label} must be an existing JSON file: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClassificationWorkflowError(f"{label} contains invalid JSON: {exc}") from exc
    try:
        return model.model_validate(payload)
    except Exception as exc:
        raise ClassificationWorkflowError(f"{label} validation failed: {exc}") from exc


def _validate_json_output(input_file: Path, output_file: Path) -> None:
    if output_file.suffix.lower() != ".json":
        raise ClassificationWorkflowError("--output must use the .json extension.")
    if input_file.resolve() == output_file.resolve():
        raise ClassificationWorkflowError(
            "--output must not overwrite the preliminary Theme/Insight bundle."
        )


def _write_json(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
