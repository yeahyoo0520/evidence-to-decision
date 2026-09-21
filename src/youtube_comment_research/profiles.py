from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ClassificationWorkflowError

DEFAULT_PROFILE_ID = "consumer_product"
SKILL_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = SKILL_ROOT / "profiles"


class QASamplingRates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: float = Field(default=0.10, ge=0, le=1)
    medium: float = Field(default=0.25, ge=0, le=1)


class ResearchProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1)
    profile_name: str = Field(min_length=1)
    research_goal: str = Field(min_length=1)
    research_questions: list[str] = Field(min_length=1)
    primary_categories: list[str] = Field(min_length=1)
    category_definitions: dict[str, str]
    secondary_category_guidance: str = Field(min_length=1)
    subject_options: list[str] = Field(min_length=1)
    subtopic_guidance: list[str] = Field(min_length=1)
    stage_options: list[str] = Field(min_length=1)
    review_guidance: str = Field(min_length=1)
    insight_questions: list[str] = Field(min_length=1)
    report_sections: list[str] = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    qa_sampling: QASamplingRates = Field(default_factory=QASamplingRates)

    @field_validator(
        "research_questions",
        "primary_categories",
        "subject_options",
        "subtopic_guidance",
        "stage_options",
        "insight_questions",
        "report_sections",
    )
    @classmethod
    def unique_nonempty_values(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("profile list values must be non-empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("profile list values must be unique")
        return cleaned

    @model_validator(mode="after")
    def definitions_cover_taxonomy(self) -> "ResearchProfile":
        categories = set(self.primary_categories)
        definitions = set(self.category_definitions)
        if categories != definitions:
            missing = sorted(categories - definitions)
            extra = sorted(definitions - categories)
            raise ValueError(
                f"category_definitions must exactly cover primary_categories; "
                f"missing={missing}, extra={extra}"
            )
        if not any(category.casefold().startswith("other") for category in categories):
            raise ValueError("every profile must contain an Other catch-all category")
        return self


def available_profiles() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in PROFILES_DIR.glob("*.yaml")))


def profile_path(profile_id: str) -> Path:
    normalized = str(profile_id or DEFAULT_PROFILE_ID).strip()
    if not normalized or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in normalized):
        raise ClassificationWorkflowError(f"Invalid research profile ID: {profile_id!r}")
    path = PROFILES_DIR / f"{normalized}.yaml"
    if not path.exists():
        choices = ", ".join(available_profiles()) or "none installed"
        raise ClassificationWorkflowError(
            f"Research profile not found: {normalized}. Available profiles: {choices}"
        )
    return path


def load_profile(profile_id: str = DEFAULT_PROFILE_ID) -> ResearchProfile:
    path = profile_path(profile_id)
    try:
        # JSON is a strict subset of YAML 1.2. Keeping profiles JSON-compatible
        # avoids adding a runtime parser dependency while retaining .yaml files.
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        profile = ResearchProfile.model_validate(payload)
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Invalid research profile {path.name}: {exc}"
        ) from exc
    if profile.profile_id != path.stem:
        raise ClassificationWorkflowError(
            f"Profile ID {profile.profile_id!r} does not match filename {path.stem!r}."
        )
    return profile


def profile_sha256(profile_id: str = DEFAULT_PROFILE_ID) -> str:
    return hashlib.sha256(profile_path(profile_id).read_bytes()).hexdigest()


def validate_profile_value(
    profile: ResearchProfile,
    *,
    primary_category: str,
    secondary_categories: list[str],
    subject: str,
    stage: str,
) -> None:
    allowed_categories = set(profile.primary_categories)
    invalid_categories = sorted(
        {primary_category, *secondary_categories} - allowed_categories
    )
    if invalid_categories:
        raise ClassificationWorkflowError(
            f"Categories are not valid for profile {profile.profile_id}: "
            + ", ".join(invalid_categories)
        )
    if subject not in profile.subject_options:
        raise ClassificationWorkflowError(
            f"Subject {subject!r} is not valid for profile {profile.profile_id}."
        )
    if stage not in profile.stage_options:
        raise ClassificationWorkflowError(
            f"Stage {stage!r} is not valid for profile {profile.profile_id}."
        )
