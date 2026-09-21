from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import ResearchContext
from .errors import ClassificationWorkflowError
from .profiles import DEFAULT_PROFILE_ID, ResearchProfile, load_profile
from .reporting import load_rows

TAXONOMY_GENERATION_METHOD = "classification-semantic-aggregation-v1"
LLM_GENERATION_METHOD = "llm-inductive-two-stage-v1"
GENERATION_METHOD = TAXONOMY_GENERATION_METHOD
DRAFT_LABEL = "AI generated draft"
ThemeMethod = Literal["taxonomy", "llm"]
DiscoveryMethod = Literal["taxonomy_based", "llm_inductive"]
TaxonomyCoverage = Literal["covered", "partially_covered", "emergent"]
TAXONOMY_BASED_COVERAGE_REASON = (
    "This theme was produced directly from the predefined taxonomy and is "
    "therefore treated as covered."
)


class CandidateTheme(BaseModel):
    """A batch-local semantic pattern proposed from raw evidence text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    theme_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    representative_evidence_ids: list[str] = Field(min_length=1, max_length=3)

    @field_validator("evidence_ids", "representative_evidence_ids")
    @classmethod
    def unique_nonempty_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("candidate evidence IDs must be non-empty")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def representatives_are_evidence(self) -> "CandidateTheme":
        if not set(self.representative_evidence_ids).issubset(self.evidence_ids):
            raise ValueError("representative evidence IDs must belong to evidence IDs")
        return self


class CandidateThemeBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    themes: list[CandidateTheme]


class ConsolidatedThemeProposal(BaseModel):
    """A semantic merge proposal that points back to candidate themes."""

    model_config = ConfigDict(extra="forbid", strict=True)

    theme_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_candidate_ids: list[str] = Field(min_length=1)
    representative_evidence_ids: list[str] = Field(min_length=1, max_length=3)
    taxonomy_coverage: TaxonomyCoverage
    coverage_reason: str = Field(
        min_length=1,
        description=(
            "Preliminary explanation of how the theme's evidence and meaning relate "
            "to the selected profile taxonomy."
        ),
    )

    @field_validator("source_candidate_ids", "representative_evidence_ids")
    @classmethod
    def unique_nonempty_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("consolidation IDs must be non-empty")
        return list(dict.fromkeys(values))

    @field_validator("coverage_reason")
    @classmethod
    def nonempty_coverage_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("coverage_reason must be non-empty")
        return value


class ConsolidatedThemeBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    themes: list[ConsolidatedThemeProposal]


class ThemeDiscoveryProvider(Protocol):
    """Minimal provider boundary for deterministic tests and real LLM adapters."""

    provider_id: str

    def discover_candidate_themes(
        self,
        comments: list[dict[str, Any]],
        *,
        profile: ResearchProfile,
        research_context: ResearchContext | None = None,
    ) -> object: ...

    def consolidate_themes(
        self,
        candidates: list[dict[str, Any]],
        comments: list[dict[str, Any]],
        *,
        profile: ResearchProfile,
        research_context: ResearchContext | None = None,
    ) -> object: ...


class ThemeRecord(BaseModel):
    """A preliminary theme grounded in existing classified evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    theme_id: str = Field(min_length=1)
    theme_name: str = Field(min_length=1)
    theme_description: str = Field(min_length=1)
    related_categories: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=1)
    evidence_comment_ids: list[str] = Field(min_length=1)
    representative_comments: list[str] = Field(default_factory=list, max_length=3)
    representative_evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    discovery_method: DiscoveryMethod = "taxonomy_based"
    taxonomy_coverage: TaxonomyCoverage = "covered"
    coverage_reason: str = Field(
        default="",
        description=(
            "Preliminary explanation of how the theme's evidence and meaning relate "
            "to the selected profile taxonomy."
        ),
    )
    confidence: Literal["low", "medium", "high"]
    review_status: Literal["preliminary"] = "preliminary"

    @field_validator(
        "related_categories",
        "evidence_comment_ids",
        "representative_comments",
        "representative_evidence_ids",
    )
    @classmethod
    def unique_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("theme arrays must contain non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError("theme arrays must not contain duplicates")
        return values

    @model_validator(mode="after")
    def evidence_count_matches(self) -> "ThemeRecord":
        reason = self.coverage_reason.strip()
        if not reason:
            if self.discovery_method == "llm_inductive":
                raise ValueError(
                    "llm_inductive themes must include a non-empty coverage_reason"
                )
            reason = TAXONOMY_BASED_COVERAGE_REASON
        self.coverage_reason = reason
        if self.evidence_count != len(self.evidence_comment_ids):
            raise ValueError("evidence_count must equal evidence_comment_ids length")
        if not set(self.representative_evidence_ids).issubset(
            self.evidence_comment_ids
        ):
            raise ValueError(
                "representative_evidence_ids must belong to evidence_comment_ids"
            )
        return self

    @property
    def evidence_ids(self) -> list[str]:
        """Source-neutral alias for the legacy evidence_comment_ids field."""

        return self.evidence_comment_ids


class InsightDraft(BaseModel):
    """A cautious interpretation attached to one validated theme."""

    model_config = ConfigDict(extra="forbid", strict=True)

    insight_id: str = Field(min_length=1)
    theme_id: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    potential_opportunity: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]
    review_status: Literal["preliminary"] = "preliminary"
    draft_label: Literal["AI generated draft"] = DRAFT_LABEL

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence_ids must contain non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError("evidence_ids must not contain duplicates")
        return values

    @model_validator(mode="after")
    def language_is_cautious(self) -> "InsightDraft":
        combined = f"{self.interpretation} {self.potential_opportunity}".casefold()
        if not any(word in combined for word in ("may", "could", "potential")):
            raise ValueError(
                "insight drafts must use cautious may/could/potential language"
            )
        if re.search(r"\b(definitely|always)\b", combined):
            raise ValueError("insight drafts must not use definitive certainty language")
        return self


class ThemeInsightBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    profile_id: str = Field(min_length=1)
    generation_method: Literal[
        "classification-semantic-aggregation-v1", "llm-inductive-two-stage-v1"
    ] = GENERATION_METHOD
    draft_label: Literal["AI generated draft"] = DRAFT_LABEL
    generated_at: str = Field(min_length=1)
    source_comment_count: int = Field(ge=0)
    research_context: ResearchContext = Field(default_factory=ResearchContext)
    themes: list[ThemeRecord]
    insight_drafts: list[InsightDraft]

    @model_validator(mode="after")
    def unique_links(self) -> "ThemeInsightBundle":
        theme_ids = [theme.theme_id for theme in self.themes]
        if len(theme_ids) != len(set(theme_ids)):
            raise ValueError("theme_id values must be unique")
        insight_ids = [insight.insight_id for insight in self.insight_drafts]
        if len(insight_ids) != len(set(insight_ids)):
            raise ValueError("insight_id values must be unique")
        unknown = sorted(
            {insight.theme_id for insight in self.insight_drafts} - set(theme_ids)
        )
        if unknown:
            raise ValueError("insight drafts reference unknown themes: " + ", ".join(unknown))
        return self


def _evidence_id(row: dict[str, Any]) -> str:
    return str(row.get("evidence_id") or row.get("comment_id") or "").strip()


def _evidence_text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("comment_text") or "").strip()


def _provider_call(
    method: object,
    *args: object,
    profile: ResearchProfile,
    research_context: ResearchContext,
) -> object:
    """Pass ResearchContext to updated providers while retaining old adapters."""

    signature = inspect.signature(method)  # type: ignore[arg-type]
    parameters = signature.parameters.values()
    supports_context = "research_context" in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    kwargs: dict[str, Any] = {"profile": profile}
    if supports_context:
        kwargs["research_context"] = research_context
    return method(*args, **kwargs)  # type: ignore[operator]


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, list):
                    raw = [str(item) for item in decoded]
                else:
                    raw = [text]
            except json.JSONDecodeError:
                raw = re.split(r"\s*[;|,]\s*", text)
        else:
            raw = re.split(r"\s*[;|,]\s*", text)
    output: list[str] = []
    for item in raw:
        cleaned = item.strip()
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _first_value(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _classification_view(row: dict[str, Any]) -> dict[str, Any]:
    primary = _first_value(
        row,
        (
            "human_confirmed_primary_category",
            "effective_primary_category",
            "codex_primary_category",
            "primary_category",
        ),
    )
    secondary = _split_values(
        _first_value(
            row,
            (
                "human_confirmed_secondary_categories",
                "effective_secondary_categories",
                "codex_secondary_categories",
                "secondary_categories",
            ),
        )
    )
    subtopics = _split_values(
        _first_value(
            row,
            (
                "human_confirmed_subtopics",
                "effective_subtopics",
                "codex_subtopics",
                "subtopics",
                "subtopic",
            ),
        )
    )
    usage = _first_value(row, ("usage_scenario", "usage_scenarios"))
    sentiment = _first_value(row, ("sentiment", "sentiment_label"))
    confidence_text = _first_value(row, ("codex_confidence", "category_confidence"))
    try:
        classification_confidence = float(confidence_text) if confidence_text else 0.7
    except ValueError:
        classification_confidence = 0.7
    return {
        "primary": primary,
        "secondary": secondary,
        "subtopics": subtopics,
        "usage": usage,
        "sentiment": sentiment,
        "classification_confidence": max(0.0, min(1.0, classification_confidence)),
    }


def _theme_id(profile_id: str, primary: str, anchor: str) -> str:
    digest = hashlib.sha256(
        f"{profile_id}\x1f{primary}\x1f{anchor}".encode("utf-8")
    ).hexdigest()[:10]
    return f"theme-{digest}"


def _inductive_theme_id(
    profile_id: str, theme_name: str, evidence_ids: list[str]
) -> str:
    digest = hashlib.sha256(
        (
            f"{profile_id}\x1fllm_inductive\x1f{theme_name.casefold()}\x1f"
            + "\x1f".join(sorted(evidence_ids))
        ).encode("utf-8")
    ).hexdigest()[:10]
    return f"theme-{digest}"


def _insight_id(theme_id: str) -> str:
    return f"insight-{theme_id.removeprefix('theme-')}"


def _title(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split())


def _theme_confidence(rows: list[dict[str, Any]]) -> Literal["low", "medium", "high"]:
    average = sum(row["view"]["classification_confidence"] for row in rows) / len(rows)
    if len(rows) >= 3 and average >= 0.8:
        return "high"
    if len(rows) >= 2 or average >= 0.75:
        return "medium"
    return "low"


def discover_themes(
    source_rows: list[dict[str, Any]],
    *,
    profile: ResearchProfile | str = DEFAULT_PROFILE_ID,
    research_context: ResearchContext | None = None,
) -> list[ThemeRecord]:
    """Group semantic classification outputs without reclassifying comment text."""

    current_profile = load_profile(profile) if isinstance(profile, str) else profile
    seen_ids: set[str] = set()
    prepared: list[dict[str, Any]] = []
    anchor_counts: Counter[tuple[str, str]] = Counter()
    for index, source in enumerate(source_rows):
        comment_id = _evidence_id(source)
        if not comment_id:
            raise ClassificationWorkflowError("Theme input contains an empty evidence ID.")
        if comment_id in seen_ids:
            raise ClassificationWorkflowError(
                f"Theme input contains duplicate evidence ID: {comment_id}"
            )
        seen_ids.add(comment_id)
        view = _classification_view(source)
        primary = view["primary"]
        if not primary:
            continue
        if primary not in current_profile.primary_categories:
            raise ClassificationWorkflowError(
                f"Classification category {primary!r} is not valid for profile "
                f"{current_profile.profile_id}."
            )
        invalid_secondary = sorted(
            set(view["secondary"]) - set(current_profile.primary_categories)
        )
        if invalid_secondary:
            raise ClassificationWorkflowError(
                "Secondary categories are not valid for profile "
                f"{current_profile.profile_id}: {', '.join(invalid_secondary)}"
            )
        semantic_parts = [*view["subtopics"]]
        if view["usage"]:
            semantic_parts.append(view["usage"])
        if view["sentiment"]:
            semantic_parts.append(view["sentiment"])
        anchor = semantic_parts[0] if semantic_parts else ""
        anchor_counts[(primary, anchor.casefold())] += 1
        prepared.append(
            {
                "index": index,
                "source": source,
                "view": view,
                "primary": primary,
                "anchor": anchor,
            }
        )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        primary = item["primary"]
        anchor = item["anchor"]
        recurring_anchor = (
            anchor if anchor and anchor_counts[(primary, anchor.casefold())] >= 2 else ""
        )
        grouped[(primary, recurring_anchor.casefold())].append(item)

    themes: list[ThemeRecord] = []
    for (primary, _), items in grouped.items():
        anchor = next((item["anchor"] for item in items if item["anchor"]), "")
        use_anchor = bool(anchor) and all(
            item["anchor"].casefold() == anchor.casefold() for item in items
        )
        theme_name = _title(anchor) if use_anchor else primary
        evidence_ids = [_evidence_id(item["source"]) for item in items]
        categories: list[str] = []
        for item in items:
            for category in [primary, *item["view"]["secondary"]]:
                if category not in categories:
                    categories.append(category)
        representative_items = sorted(
            items,
            key=lambda item: (
                -_safe_int(item["source"].get("comment_like_count")),
                item["index"],
            ),
        )
        representatives: list[str] = []
        representative_ids: list[str] = []
        for item in representative_items:
            text = _evidence_text(item["source"])
            if text and text not in representatives:
                representatives.append(text)
                representative_ids.append(_evidence_id(item["source"]))
            if len(representatives) == 3:
                break
        focus = anchor if use_anchor else primary
        themes.append(
            ThemeRecord(
                theme_id=_theme_id(current_profile.profile_id, primary, focus),
                theme_name=theme_name,
                theme_description=(
                    f"This preliminary theme groups classified evidence about "
                    f"{focus} within {primary}."
                    + (
                        f" It is considered in relation to the research question: "
                        f"{research_context.research_question}"
                        if research_context and research_context.research_question
                        else ""
                    )
                ),
                related_categories=categories,
                evidence_count=len(evidence_ids),
                evidence_comment_ids=evidence_ids,
                representative_comments=representatives,
                representative_evidence_ids=representative_ids,
                discovery_method="taxonomy_based",
                taxonomy_coverage="covered",
                coverage_reason=TAXONOMY_BASED_COVERAGE_REASON,
                confidence=_theme_confidence(items),
                review_status="preliminary",
            )
        )
    return sorted(
        themes,
        key=lambda theme: (-theme.evidence_count, theme.theme_name.casefold()),
    )


def _provider_comments(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep raw text primary and expose classification only as optional context."""

    comments: list[dict[str, Any]] = []
    for row in source_rows:
        comment_id = _evidence_id(row)
        comment_text = _evidence_text(row)
        if not comment_text:
            continue
        view = _classification_view(row)
        comments.append(
            {
                "comment_id": comment_id,
                "comment_text": comment_text,
                "evidence_id": comment_id,
                "source_type": str(row.get("source_type") or "youtube_comment"),
                "source_name": str(row.get("source_name") or row.get("video_title") or ""),
                "classification_context": {
                    "primary_category": view["primary"],
                    "secondary_categories": view["secondary"],
                    "subtopics": view["subtopics"],
                    "usage_scenario": view["usage"],
                    "sentiment": view["sentiment"],
                },
            }
        )
    return comments


def _validate_source_rows(source_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    source_by_id: dict[str, dict[str, Any]] = {}
    for source in source_rows:
        comment_id = _evidence_id(source)
        if not comment_id:
            raise ClassificationWorkflowError("Theme input contains an empty evidence ID.")
        if comment_id in source_by_id:
            raise ClassificationWorkflowError(
                f"Theme input contains duplicate evidence ID: {comment_id}"
            )
        source_by_id[comment_id] = source
    return source_by_id


def _candidate_id(batch_index: int, item_index: int, candidate: CandidateTheme) -> str:
    digest = hashlib.sha256(
        (
            f"{batch_index}\x1f{item_index}\x1f{candidate.theme_name.casefold()}\x1f"
            + "\x1f".join(sorted(candidate.evidence_ids))
        ).encode("utf-8")
    ).hexdigest()[:8]
    return f"candidate-{batch_index:03d}-{item_index:03d}-{digest}"


def _mapped_categories(
    evidence_ids: list[str],
    source_by_id: dict[str, dict[str, Any]],
    profile: ResearchProfile,
) -> list[str]:
    categories: list[str] = []
    allowed = set(profile.primary_categories)
    for comment_id in evidence_ids:
        view = _classification_view(source_by_id[comment_id])
        for category in [view["primary"], *view["secondary"]]:
            if category and category not in allowed:
                raise ClassificationWorkflowError(
                    f"Classification category {category!r} is not valid for profile "
                    f"{profile.profile_id}."
                )
            if category and category not in categories:
                categories.append(category)
    return categories


def _inductive_confidence(
    evidence_count: int, min_evidence_count: int
) -> Literal["low", "medium", "high"]:
    if evidence_count >= max(5, min_evidence_count * 3):
        return "high"
    if evidence_count >= max(3, min_evidence_count + 1):
        return "medium"
    return "low"


def discover_inductive_themes(
    source_rows: list[dict[str, Any]],
    *,
    provider: ThemeDiscoveryProvider,
    profile: ResearchProfile | str = DEFAULT_PROFILE_ID,
    batch_size: int = 20,
    min_evidence_count: int = 2,
    research_context: ResearchContext | None = None,
) -> list[ThemeRecord]:
    """Discover themes from raw text, then consolidate and map taxonomy context."""

    if not 1 <= batch_size <= 100:
        raise ClassificationWorkflowError("theme batch_size must be between 1 and 100.")
    if min_evidence_count < 1:
        raise ClassificationWorkflowError("min_evidence_count must be at least 1.")
    current_profile = load_profile(profile) if isinstance(profile, str) else profile
    current_context = research_context or ResearchContext()
    source_by_id = _validate_source_rows(source_rows)
    comments = _provider_comments(source_rows)
    if not comments:
        return []

    candidates: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(comments), batch_size), start=1):
        batch = comments[start : start + batch_size]
        allowed_ids = {item["evidence_id"] for item in batch}
        try:
            result = CandidateThemeBatch.model_validate(
                _provider_call(
                    provider.discover_candidate_themes,
                    batch,
                    profile=current_profile,
                    research_context=current_context,
                )
            )
        except Exception as exc:
            raise ClassificationWorkflowError(
                f"LLM candidate theme validation failed for batch {batch_index}: {exc}"
            ) from exc
        for item_index, candidate in enumerate(result.themes, start=1):
            unknown = sorted(set(candidate.evidence_ids) - allowed_ids)
            if unknown:
                raise ClassificationWorkflowError(
                        "LLM candidate theme references unknown evidence ID "
                        "(legacy unknown comment_id): "
                    + ", ".join(unknown)
                )
            candidates.append(
                {
                    "candidate_id": _candidate_id(batch_index, item_index, candidate),
                    **candidate.model_dump(),
                }
            )
    finalize_candidate_stage = getattr(provider, "finalize_candidate_stage", None)
    if callable(finalize_candidate_stage):
        finalize_candidate_stage()
    if not candidates:
        return []

    try:
        consolidation = ConsolidatedThemeBatch.model_validate(
            _provider_call(
                provider.consolidate_themes,
                candidates,
                comments,
                profile=current_profile,
                research_context=current_context,
            )
        )
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"LLM theme consolidation validation failed: {exc}"
        ) from exc

    candidates_by_id = {item["candidate_id"]: item for item in candidates}
    used_candidate_ids: list[str] = []
    themes: list[ThemeRecord] = []
    for proposal in consolidation.themes:
        unknown_candidates = sorted(
            set(proposal.source_candidate_ids) - set(candidates_by_id)
        )
        if unknown_candidates:
            raise ClassificationWorkflowError(
                "Theme consolidation references unknown candidate_id: "
                + ", ".join(unknown_candidates)
            )
        used_candidate_ids.extend(proposal.source_candidate_ids)
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for candidate_id in proposal.source_candidate_ids
                for evidence_id in candidates_by_id[candidate_id]["evidence_ids"]
            )
        )
        if not set(proposal.representative_evidence_ids).issubset(evidence_ids):
            raise ClassificationWorkflowError(
                "Theme consolidation representative IDs must belong to consolidated evidence IDs."
            )
        if len(evidence_ids) < min_evidence_count:
            continue
        representatives = [
            _evidence_text(source_by_id[comment_id])
            for comment_id in proposal.representative_evidence_ids
        ]
        themes.append(
            ThemeRecord(
                theme_id=_inductive_theme_id(
                    current_profile.profile_id, proposal.theme_name, evidence_ids
                ),
                theme_name=proposal.theme_name,
                theme_description=proposal.description,
                related_categories=_mapped_categories(
                    evidence_ids, source_by_id, current_profile
                ),
                evidence_count=len(evidence_ids),
                evidence_comment_ids=evidence_ids,
                representative_comments=representatives,
                representative_evidence_ids=proposal.representative_evidence_ids,
                discovery_method="llm_inductive",
                taxonomy_coverage=proposal.taxonomy_coverage,
                coverage_reason=proposal.coverage_reason,
                confidence=_inductive_confidence(
                    len(evidence_ids), min_evidence_count
                ),
                review_status="preliminary",
            )
        )

    duplicate_candidates = sorted(
        candidate_id
        for candidate_id, count in Counter(used_candidate_ids).items()
        if count > 1
    )
    if duplicate_candidates:
        raise ClassificationWorkflowError(
            "Theme consolidation reuses candidate IDs across final themes: "
            + ", ".join(duplicate_candidates)
        )
    missing_candidates = sorted(set(candidates_by_id) - set(used_candidate_ids))
    if missing_candidates:
        raise ClassificationWorkflowError(
            "Theme consolidation omitted candidate IDs: "
            + ", ".join(missing_candidates)
        )
    return sorted(
        themes,
        key=lambda theme: (-theme.evidence_count, theme.theme_name.casefold()),
    )


def draft_insights(
    themes: list[ThemeRecord],
    *,
    profile: ResearchProfile | str = DEFAULT_PROFILE_ID,
    research_context: ResearchContext | None = None,
) -> list[InsightDraft]:
    current_profile = load_profile(profile) if isinstance(profile, str) else profile
    current_context = research_context or ResearchContext()
    drafts: list[InsightDraft] = []
    for theme in themes:
        categories = ", ".join(theme.related_categories) or "no predefined category"
        noun = "evidence record" if theme.evidence_count == 1 else "evidence records"
        verb = "forms" if theme.evidence_count == 1 else "form"
        relation_verb = "relates" if theme.evidence_count == 1 else "relate"
        if current_context.analysis_mode == "decision_focused":
            context_clause = (
                " In decision-focused mode, this pattern may inform"
                + (
                    f" the question '{current_context.research_question}'"
                    if current_context.research_question
                    else " the stated research question"
                )
                + (
                    f" for the decision context '{current_context.decision_context}'."
                    if current_context.decision_context
                    else "."
                )
            )
        elif current_context.research_question:
            context_clause = (
                " In exploratory mode, it may help investigate the question "
                f"'{current_context.research_question}'."
            )
        else:
            context_clause = ""
        drafts.append(
            InsightDraft(
                insight_id=_insight_id(theme.theme_id),
                theme_id=theme.theme_id,
                observation=(
                    f"{theme.evidence_count} classified {noun} {verb} the "
                    f"{theme.theme_name} theme and {relation_verb} to {categories}."
                ),
                interpretation=(
                    f"This pattern may indicate attention to "
                    f"{theme.theme_name.casefold()} within the "
                    f"{current_profile.profile_name} profile."
                    + context_clause
                ),
                potential_opportunity=(
                    "Review the cited evidence before treating this preliminary "
                    "interpretation as a confirmed insight."
                ),
                evidence_ids=list(theme.evidence_comment_ids),
                confidence=theme.confidence,
                review_status="preliminary",
                draft_label=DRAFT_LABEL,
            )
        )
    return drafts


def validate_theme_insight_bundle(
    payload: object,
    source_rows: list[dict[str, Any]],
    *,
    profile: ResearchProfile | str = DEFAULT_PROFILE_ID,
) -> ThemeInsightBundle:
    current_profile = load_profile(profile) if isinstance(profile, str) else profile
    try:
        bundle = ThemeInsightBundle.model_validate(payload)
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Theme/insight schema validation failed: {exc}"
        ) from exc
    if bundle.profile_id != current_profile.profile_id:
        raise ClassificationWorkflowError(
            f"Theme bundle profile {bundle.profile_id!r} does not match "
            f"{current_profile.profile_id!r}."
        )
    if bundle.source_comment_count != len(source_rows):
        raise ClassificationWorkflowError(
            "source_comment_count does not match the source evidence input."
        )
    expected_discovery_method = (
        "taxonomy_based"
        if bundle.generation_method == TAXONOMY_GENERATION_METHOD
        else "llm_inductive"
    )
    inconsistent_methods = sorted(
        theme.theme_id
        for theme in bundle.themes
        if theme.discovery_method != expected_discovery_method
    )
    if inconsistent_methods:
        raise ClassificationWorkflowError(
            "Theme discovery_method does not match bundle generation_method: "
            + ", ".join(inconsistent_methods)
        )
    source_by_id = {_evidence_id(row): row for row in source_rows}
    if len(source_by_id) != len(source_rows) or "" in source_by_id:
        raise ClassificationWorkflowError(
            "Theme input requires unique, non-empty evidence IDs."
        )
    themes_by_id = {theme.theme_id: theme for theme in bundle.themes}
    for theme in bundle.themes:
        unknown = sorted(set(theme.evidence_comment_ids) - set(source_by_id))
        if unknown:
            raise ClassificationWorkflowError(
                f"Theme {theme.theme_id} references unknown evidence ID: "
                + ", ".join(unknown)
            )
        invalid_categories = sorted(
            set(theme.related_categories) - set(current_profile.primary_categories)
        )
        if invalid_categories:
            raise ClassificationWorkflowError(
                f"Theme {theme.theme_id} contains categories outside profile "
                f"{current_profile.profile_id}: {', '.join(invalid_categories)}"
            )
        evidence_texts = {
            _evidence_text(source_by_id[comment_id])
            for comment_id in theme.evidence_comment_ids
        }
        if not set(theme.representative_comments).issubset(evidence_texts):
            raise ClassificationWorkflowError(
                f"Theme {theme.theme_id} contains a representative comment "
                "outside its evidence IDs."
            )
        if not set(theme.representative_evidence_ids).issubset(
            theme.evidence_comment_ids
        ):
            raise ClassificationWorkflowError(
                f"Theme {theme.theme_id} contains representative evidence IDs "
                "outside its evidence IDs."
            )
        representative_texts = [
            _evidence_text(source_by_id[comment_id])
            for comment_id in theme.representative_evidence_ids
        ]
        if theme.representative_evidence_ids and (
            representative_texts != theme.representative_comments
        ):
            raise ClassificationWorkflowError(
                f"Theme {theme.theme_id} representative comments do not match "
                "representative evidence IDs."
            )
    for insight in bundle.insight_drafts:
        theme = themes_by_id[insight.theme_id]
        if not set(insight.evidence_ids).issubset(theme.evidence_comment_ids):
            raise ClassificationWorkflowError(
                f"Insight {insight.insight_id} contains evidence outside theme "
                f"{insight.theme_id}."
            )
    return bundle


def generate_theme_insights(
    input_file: Path,
    output_file: Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    theme_method: ThemeMethod = "taxonomy",
    provider: ThemeDiscoveryProvider | None = None,
    batch_size: int = 20,
    min_evidence_count: int = 2,
    research_context: ResearchContext | None = None,
) -> ThemeInsightBundle:
    if output_file.suffix.lower() != ".json":
        raise ClassificationWorkflowError("--output must use the .json extension.")
    if not input_file.exists():
        raise ClassificationWorkflowError(f"Classification input not found: {input_file}")
    profile = load_profile(profile_id)
    current_context = research_context or ResearchContext()
    rows = load_rows(input_file)
    if theme_method == "taxonomy":
        themes = discover_themes(
            rows,
            profile=profile,
            research_context=current_context,
        )
        generation_method = TAXONOMY_GENERATION_METHOD
    elif theme_method == "llm":
        if provider is None:
            raise ClassificationWorkflowError(
                "--theme-method llm requires a configured ThemeDiscoveryProvider."
            )
        themes = discover_inductive_themes(
            rows,
            provider=provider,
            profile=profile,
            batch_size=batch_size,
            min_evidence_count=min_evidence_count,
            research_context=current_context,
        )
        generation_method = LLM_GENERATION_METHOD
    else:
        raise ClassificationWorkflowError(
            "theme_method must be either 'taxonomy' or 'llm'."
        )
    drafts = draft_insights(
        themes,
        profile=profile,
        research_context=current_context,
    )
    bundle = ThemeInsightBundle(
        profile_id=profile.profile_id,
        generation_method=generation_method,
        draft_label=DRAFT_LABEL,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_comment_count=len(rows),
        research_context=current_context,
        themes=themes,
        insight_drafts=drafts,
    )
    validated = validate_theme_insight_bundle(bundle, rows, profile=profile)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(validated.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return validated


def load_theme_insight_bundle(
    path: Path,
    *,
    source_rows: list[dict[str, Any]] | None = None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> ThemeInsightBundle:
    if not path.exists() or path.suffix.lower() != ".json":
        raise ClassificationWorkflowError(
            f"Theme/insight bundle must be an existing JSON file: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClassificationWorkflowError(
            f"Theme/insight bundle contains invalid JSON: {exc}"
        ) from exc
    if source_rows is None:
        try:
            return ThemeInsightBundle.model_validate(payload)
        except Exception as exc:
            raise ClassificationWorkflowError(
                f"Theme/insight schema validation failed: {exc}"
            ) from exc
    return validate_theme_insight_bundle(payload, source_rows, profile=profile_id)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
