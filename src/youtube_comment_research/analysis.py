from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .reporting import CATEGORY_LABELS, classify_rows, classify_text, load_rows as load_report_rows, top_terms


def classify_comment(text: str) -> tuple[str, float, str]:
    result = classify_text(text)
    return result.primary_category, result.category_confidence, result.category_evidence


def load_rows(path: Path) -> list[dict[str, Any]]:
    return load_report_rows(path)


def analyze_file(path: Path, output_dir: Path) -> tuple[Path, Path]:
    rows = load_rows(path)
    classified = classify_rows(rows)
    category_counts: Counter[str] = Counter(
        str(row.get("primary_category") or "Other") for row in classified
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    classified_path = output_dir / "classified_comments.csv"
    fieldnames = (
        list(classified[0].keys())
        if classified
        else [
            "comment_id",
            "comment_text",
            "primary_category",
            "primary_category_cn",
            "category_cn",
            "secondary_categories",
            "secondary_categories_cn",
            "subtopics",
            "subtopic",
            "lifecycle_stage",
            "mentioned_brands",
            "mentioned_products",
            "category_confidence",
            "category_evidence",
            "matched_rules",
            "manual_review",
            "review_reason",
        ]
    )
    with classified_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(classified)

    top_liked = sorted(
        classified,
        key=lambda row: int(row.get("comment_like_count") or 0),
        reverse=True,
    )[:10]
    total = len(classified)
    summary = {
        "source_file": str(path),
        "total_comments": total,
        "category_counts": dict(category_counts),
        "category_percentages": {
            key: round(value * 100 / total, 2) if total else 0 for key, value in category_counts.items()
        },
        "top_terms": top_terms(classified),
        "top_liked_comments": [
            {
                "comment_id": row.get("comment_id"),
                "comment_text": row.get("comment_text"),
                "comment_like_count": row.get("comment_like_count"),
                "primary_category": row.get("primary_category"),
                "category_cn": row.get("category_cn"),
                "subtopic": row.get("subtopic"),
                "category_evidence": row.get("category_evidence"),
                "manual_review": row.get("manual_review"),
            }
            for row in top_liked
        ],
        "categories": [
            {"primary_category": category, "category_cn": category_cn}
            for category, category_cn in CATEGORY_LABELS
        ],
        "limitations": [
            "This is a sample of publicly available comments, not a representative consumer survey.",
            "Rule-based categories are approximate and should be manually reviewed.",
            "Deleted, private, moderated, or otherwise unavailable comments are not included.",
        ],
    }
    summary_path = output_dir / "analysis_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return classified_path, summary_path
