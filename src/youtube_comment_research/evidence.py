from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .errors import ClassificationWorkflowError

EvidenceSourceType = Literal[
    "youtube_comment",
    "product_review",
    "customer_inquiry",
    "crm_note",
    "sales_note",
    "survey_response",
    "interview_note",
    "social_feedback",
]
AnalysisMode = Literal["exploratory", "decision_focused"]


class EvidenceRecord(BaseModel):
    """Source-neutral evidence accepted by the shared research engine."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_type: EvidenceSourceType
    source_name: str | None = None
    created_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("created_at", "timestamp"),
    )
    language: str | None = None
    market: str | None = None
    region: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_id", "text")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("source_name", "language", "market", "region")
    @classmethod
    def optional_text_is_normalized(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ResearchContext(BaseModel):
    """Question and decision context supplied to discovery and draft synthesis."""

    model_config = ConfigDict(extra="forbid")

    research_question: str | None = None
    decision_context: str | None = None
    analysis_mode: AnalysisMode = "exploratory"

    @field_validator("research_question", "decision_context")
    @classmethod
    def normalize_optional_context(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


def load_evidence_json(path: Path) -> list[EvidenceRecord]:
    """Load a JSON array of generic evidence records with unique IDs."""

    if not path.exists():
        raise ClassificationWorkflowError(f"Evidence input not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClassificationWorkflowError(
            f"Evidence input contains invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise ClassificationWorkflowError(
            "Generic Evidence JSON must contain a top-level array."
        )
    try:
        records = [EvidenceRecord.model_validate(item) for item in payload]
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Generic Evidence validation failed: {exc}"
        ) from exc
    ids = [record.evidence_id for record in records]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ClassificationWorkflowError(
            "Duplicate evidence_id: " + ", ".join(duplicates)
        )
    return records


def evidence_to_research_rows(
    records: list[EvidenceRecord],
) -> list[dict[str, Any]]:
    """Adapt EvidenceRecord to the existing traceability contract.

    ``comment_id`` and ``comment_text`` are compatibility aliases, not a second
    identity chain. Their values always equal ``evidence_id`` and ``text``.
    """

    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        payload = record.model_dump(mode="json")
        metadata = dict(record.metadata)
        source_key = str(
            metadata.get("source_id") or record.source_name or record.source_type
        )
        rows.append(
            {
                **payload,
                "metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                "comment_id": record.evidence_id,
                "comment_text": record.text,
                "project_id": str(metadata.get("project_id") or ""),
                "video_id": source_key,
                "video_title": record.source_name or source_key,
                "channel_title": str(metadata.get("organization") or ""),
                "is_reply": False,
                "parent_comment_id": "",
                "comment_like_count": int(metadata.get("engagement_count") or 0),
                "reply_count": 0,
                "comment_published_at": (
                    record.created_at.isoformat() if record.created_at else ""
                ),
                "collected_at": (
                    record.created_at.isoformat() if record.created_at else ""
                ),
                "collection_order": index,
            }
        )
    return rows
