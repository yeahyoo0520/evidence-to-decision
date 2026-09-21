from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ClassificationWorkflowError
from .human_review import HumanReviewBundle, load_human_review_bundle
from .theme_discovery import ThemeInsightBundle, load_theme_insight_bundle
from .verification import EffectiveInsightRecord, resolve_effective_insights


class PossibleDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    direction: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_basis: str = Field(min_length=1)
    tradeoff_or_risk: str = Field(min_length=1)


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    gap: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    related_insight_ids: list[str] = Field(min_length=1)
    related_evidence_ids: list[str] = Field(default_factory=list)


class ValidationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    validation_question: str = Field(min_length=1)
    method: str = Field(min_length=1)
    required_evidence: str = Field(min_length=1)
    evaluation_criterion: str = Field(min_length=1)
    limitation: str = Field(min_length=1)


class DecisionBriefProposal(BaseModel):
    """Provider output before source-bound provenance is added."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_effective_insight_ids: list[str] = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_question: str = Field(min_length=1)
    possible_directions: list[PossibleDirection] = Field(min_length=1)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    validation_plan: list[ValidationItem] = Field(min_length=1)
    uncertainty_note: str = Field(min_length=1)

    @field_validator("source_effective_insight_ids")
    @classmethod
    def source_ids_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("source_effective_insight_ids must be unique")
        return values

    @field_validator("decision_question")
    @classmethod
    def decision_question_is_a_question(cls, value: str) -> str:
        if not value.strip().endswith("?"):
            raise ValueError("decision_question must be an explicit question")
        return value.strip()


class DecisionBriefProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    briefs: list[DecisionBriefProposal]


class DecisionBriefRecord(BaseModel):
    """Evidence-bound decision support; it does not make the final decision."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    decision_brief_id: str = Field(min_length=1)
    source_effective_insight_ids: list[str] = Field(min_length=1)
    source_theme_ids: list[str] = Field(min_length=1)
    supporting_evidence_ids: list[str] = Field(min_length=1)
    research_question: str | None = None
    decision_context: str | None = None
    analysis_mode: Literal["exploratory", "decision_focused"]
    why_it_matters: str = Field(min_length=1)
    decision_question: str = Field(min_length=1)
    possible_directions: list[PossibleDirection] = Field(min_length=1)
    evidence_gaps: list[EvidenceGap]
    validation_plan: list[ValidationItem] = Field(min_length=1)
    verification_statuses: list[Literal["unreviewed", "reviewed", "edited"]]
    review_recommended: bool
    review_reasons: list[str]
    uncertainty_note: str = Field(min_length=1)
    status: Literal["AI-generated decision support"] = "AI-generated decision support"
    generated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def provenance_contract(self) -> "DecisionBriefRecord":
        for values in (
            self.source_effective_insight_ids,
            self.source_theme_ids,
            self.supporting_evidence_ids,
            self.verification_statuses,
            self.review_reasons,
        ):
            if len(values) != len(set(values)):
                raise ValueError("Decision Brief provenance arrays must be unique")
        if self.review_recommended != bool(self.review_reasons):
            raise ValueError("review_recommended must match review_reasons")
        return self


class DecisionSupportProvider(Protocol):
    provider_id: str

    def generate_decision_briefs(
        self, effective_insights: list[dict[str, object]]
    ) -> object: ...


def _brief_id(source_ids: list[str], question: str) -> str:
    seed = "\x1f".join(sorted(source_ids)) + "\x1f" + question
    return "decision-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def build_decision_briefs(
    effective_insights: list[EffectiveInsightRecord],
    provider: DecisionSupportProvider,
    *,
    generated_at: str | None = None,
) -> list[DecisionBriefRecord]:
    eligible = [
        item
        for item in effective_insights
        if item.downstream_eligible and item.verification_status != "rejected"
    ]
    by_id = {item.effective_insight_id: item for item in eligible}
    if len(by_id) != len(eligible):
        raise ClassificationWorkflowError("Effective Insight IDs must be unique.")
    if not eligible:
        return []
    try:
        payload = provider.generate_decision_briefs(
            [item.model_dump(mode="json") for item in eligible]
        )
        proposals = DecisionBriefProposalBatch.model_validate(payload).briefs
    except ClassificationWorkflowError:
        raise
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Decision Support provider output is invalid: {exc}"
        ) from exc

    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    records: list[DecisionBriefRecord] = []
    for proposal in proposals:
        unknown = sorted(set(proposal.source_effective_insight_ids) - set(by_id))
        if unknown:
            raise ClassificationWorkflowError(
                "Decision Brief references unavailable Effective Insights: "
                + ", ".join(unknown)
            )
        sources = [by_id[value] for value in proposal.source_effective_insight_ids]
        contexts = {item.research_context.model_dump_json() for item in sources}
        if len(contexts) != 1:
            raise ClassificationWorkflowError(
                "A Decision Brief cannot merge Insights with different Research Contexts."
            )
        context = sources[0].research_context
        source_evidence = _unique(
            [evidence_id for item in sources for evidence_id in item.evidence_ids]
        )
        source_ids = set(proposal.source_effective_insight_ids)
        evidence_ids = set(source_evidence)
        for gap in proposal.evidence_gaps:
            if not set(gap.related_insight_ids).issubset(source_ids):
                raise ClassificationWorkflowError(
                    "Evidence Gap references an Insight outside its Decision Brief."
                )
            if not set(gap.related_evidence_ids).issubset(evidence_ids):
                raise ClassificationWorkflowError(
                    "Evidence Gap references evidence outside its source Insights."
                )
        normalized_question = re.sub(r"\W+", " ", proposal.decision_question).strip().casefold()
        source_statements = {
            re.sub(r"\W+", " ", value).strip().casefold()
            for item in sources
            for value in (
                item.final_observation,
                item.final_interpretation,
                item.theme_description,
            )
        }
        if normalized_question in source_statements:
            raise ClassificationWorkflowError(
                "Decision Question must frame a decision, not repeat the source Insight."
            )
        review_reasons = _unique(
            [reason for item in sources for reason in item.review_reasons]
        )
        records.append(
            DecisionBriefRecord(
                decision_brief_id=_brief_id(
                    proposal.source_effective_insight_ids,
                    proposal.decision_question,
                ),
                source_effective_insight_ids=proposal.source_effective_insight_ids,
                source_theme_ids=_unique([item.source_theme_id for item in sources]),
                supporting_evidence_ids=source_evidence,
                research_question=context.research_question,
                decision_context=context.decision_context,
                analysis_mode=context.analysis_mode,
                why_it_matters=proposal.why_it_matters,
                decision_question=proposal.decision_question,
                possible_directions=proposal.possible_directions,
                evidence_gaps=proposal.evidence_gaps,
                validation_plan=proposal.validation_plan,
                verification_statuses=_unique(
                    [item.verification_status for item in sources]
                ),
                review_recommended=bool(review_reasons),
                review_reasons=review_reasons,
                uncertainty_note=proposal.uncertainty_note,
                generated_at=timestamp,
            )
        )
    return records


def load_effective_insights(path: Path) -> list[EffectiveInsightRecord]:
    if not path.exists():
        raise ClassificationWorkflowError(f"Effective Insight input not found: {path}")
    try:
        if path.suffix.lower() == ".jsonl":
            payload = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "decision_briefs" in payload:
            payload = payload["decision_briefs"]
        if not isinstance(payload, list):
            raise ValueError("expected a JSON array or JSONL records")
        return [EffectiveInsightRecord.model_validate(item) for item in payload]
    except (json.JSONDecodeError, ValueError) as exc:
        raise ClassificationWorkflowError(
            f"Effective Insight input is invalid: {exc}"
        ) from exc


def resolve_decision_input(
    input_file: Path,
    *,
    human_review_file: Path | None = None,
) -> list[EffectiveInsightRecord]:
    if input_file.suffix.lower() == ".jsonl":
        if human_review_file:
            raise ClassificationWorkflowError(
                "--human-review is only valid when INPUT is a Theme + Insight bundle."
            )
        return load_effective_insights(input_file)
    source_bundle: ThemeInsightBundle = load_theme_insight_bundle(input_file)
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
    return resolve_effective_insights(source)


def write_decision_briefs(
    path: Path, records: list[DecisionBriefRecord]
) -> Path:
    if path.suffix.lower() not in {".json", ".jsonl"}:
        raise ClassificationWorkflowError("--output must use .json or .jsonl.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".jsonl":
        text = "".join(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for item in records
        )
    else:
        text = json.dumps(
            [item.model_dump(mode="json") for item in records],
            ensure_ascii=False,
            indent=2,
        )
    path.write_text(text, encoding="utf-8")
    return path


def load_decision_briefs(path: Path) -> list[DecisionBriefRecord]:
    if not path.exists():
        raise ClassificationWorkflowError(f"Decision Brief input not found: {path}")
    try:
        if path.suffix.lower() == ".jsonl":
            payload = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Decision Brief file must contain a JSON array or JSONL")
        return [DecisionBriefRecord.model_validate(item) for item in payload]
    except (json.JSONDecodeError, ValueError) as exc:
        raise ClassificationWorkflowError(f"Decision Brief input is invalid: {exc}") from exc
