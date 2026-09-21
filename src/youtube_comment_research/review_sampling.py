from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .profiles import ResearchProfile

QA_STATUSES = ("not_selected", "pending", "confirmed", "corrected")
EVIDENCE_REVIEW_STATUSES = ("not_required", "pending", "confirmed", "rejected")

REVIEW_CONTROL_FIELDS = (
    "qa_sample",
    "qa_sample_reason",
    "qa_status",
    "qa_sample_seed",
    "qa_sampled_at",
    "evidence_review_required",
    "evidence_review_status",
)


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "是"}


def review_risk(row: dict[str, Any]) -> str:
    value = str(
        row.get("codex_review_risk") or row.get("review_risk") or "low"
    ).strip().casefold()
    return value if value in {"low", "medium", "high"} else "high"


def mandatory_review(row: dict[str, Any]) -> bool:
    return review_risk(row) == "high" or as_bool(
        row.get("codex_manual_review") or row.get("manual_review")
    )


def _sample_count(size: int, rate: float) -> int:
    if size <= 0 or rate <= 0:
        return 0
    return min(size, int(size * rate + 0.5))


def _eligible_ids(rows: list[dict[str, Any]], risk: str) -> list[str]:
    return sorted(
        str(row.get("comment_id") or "")
        for row in rows
        if review_risk(row) == risk
        and not mandatory_review(row)
        and row.get("comment_id")
    )


def apply_review_sampling(
    rows: Iterable[dict[str, Any]],
    profile: ResearchProfile,
    *,
    seed: int = 20260816,
    sampled_at: str | None = None,
) -> list[dict[str, Any]]:
    output = [dict(row) for row in rows]
    rng = random.Random(seed)
    selected: set[str] = set()
    rates = {"low": profile.qa_sampling.low, "medium": profile.qa_sampling.medium}
    for risk in ("low", "medium"):
        ids = _eligible_ids(output, risk)
        count = _sample_count(len(ids), rates[risk])
        selected.update(rng.sample(ids, count) if count else [])

    timestamp = sampled_at or datetime.now(timezone.utc).isoformat()
    for row in output:
        comment_id = str(row.get("comment_id") or "")
        risk = review_risk(row)
        is_mandatory = mandatory_review(row)
        if risk == "high":
            row["codex_manual_review"] = True
            row["manual_review"] = True
        elif not as_bool(row.get("codex_manual_review")):
            row["codex_manual_review"] = False

        row["qa_sample"] = comment_id in selected
        row["qa_sample_reason"] = (
            f"fixed-seed {risk}-risk QA sample at {rates[risk]:.0%}"
            if comment_id in selected
            else ("mandatory high-risk review" if is_mandatory else "not selected")
        )
        current_qa = str(row.get("qa_status") or "").strip().casefold()
        if current_qa not in QA_STATUSES:
            current_qa = ""
        row["qa_status"] = (
            current_qa
            if current_qa in {"confirmed", "corrected"}
            else ("pending" if comment_id in selected else "not_selected")
        )
        row["qa_sample_seed"] = seed
        row["qa_sampled_at"] = timestamp

        evidence_required = as_bool(row.get("evidence_review_required"))
        row["evidence_review_required"] = evidence_required
        evidence_status = str(
            row.get("evidence_review_status") or ""
        ).strip().casefold()
        if evidence_status not in EVIDENCE_REVIEW_STATUSES:
            evidence_status = ""
        row["evidence_review_status"] = (
            evidence_status
            if evidence_status in {"confirmed", "rejected"}
            else ("pending" if evidence_required else "not_required")
        )
    return output


@dataclass(frozen=True)
class ReviewWorkload:
    total_comments: int
    low_risk_count: int
    medium_risk_count: int
    high_risk_count: int
    mandatory_review_count: int
    qa_sample_count: int
    evidence_review_count: int
    unique_human_review_workload: int
    human_review_coverage: float


def summarize_review_workload(rows: Iterable[dict[str, Any]]) -> ReviewWorkload:
    materialized = list(rows)
    risk_counts = {
        risk: sum(review_risk(row) == risk for row in materialized)
        for risk in ("low", "medium", "high")
    }
    mandatory_ids = {
        str(row.get("comment_id") or "")
        for row in materialized
        if mandatory_review(row)
    }
    qa_ids = {
        str(row.get("comment_id") or "")
        for row in materialized
        if as_bool(row.get("qa_sample"))
    }
    evidence_ids = {
        str(row.get("comment_id") or "")
        for row in materialized
        if as_bool(row.get("evidence_review_required"))
    }
    unique = {value for value in mandatory_ids | qa_ids | evidence_ids if value}
    total = len(materialized)
    return ReviewWorkload(
        total_comments=total,
        low_risk_count=risk_counts["low"],
        medium_risk_count=risk_counts["medium"],
        high_risk_count=risk_counts["high"],
        mandatory_review_count=len({value for value in mandatory_ids if value}),
        qa_sample_count=len({value for value in qa_ids if value}),
        evidence_review_count=len({value for value in evidence_ids if value}),
        unique_human_review_workload=len(unique),
        human_review_coverage=(len(unique) / total if total else 0),
    )


def review_queue(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if mandatory_review(row)
        or (
            as_bool(row.get("qa_sample"))
            and str(row.get("qa_status") or "").casefold() == "pending"
        )
        or (
            as_bool(row.get("evidence_review_required"))
            and str(row.get("evidence_review_status") or "").casefold() == "pending"
        )
    ]

