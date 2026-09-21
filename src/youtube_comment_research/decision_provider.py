from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from .decision_support import DecisionBriefProposalBatch
from .errors import ClassificationWorkflowError


DECISION_SUPPORT_INSTRUCTIONS = """Create AI-generated decision support from the supplied
Effective Insights. Preserve the overall Research Context. Explain why each supported pattern
may matter, frame a neutral and answerable Decision Question, give candidate directions with
tradeoffs, identify only evidence gaps relevant to that question, and propose validation steps.
Do not make a final decision, claim that a direction is proven, invent evidence, or reference an
Insight or evidence ID outside the supplied records. Keep unrelated Insights in separate Briefs.
Use edited Effective Insight wording when provenance is human_edited. Reflect review warnings
and qualitative limitations in cautious language and the uncertainty note."""


def load_decision_support_provider(spec: str) -> object:
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name.strip() or not attribute_name.strip():
        raise ClassificationWorkflowError(
            "--decision-provider must use the module:attribute form."
        )
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
        provider = factory() if callable(factory) else factory
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Decision Support provider could not be loaded from {spec!r}: {exc}"
        ) from exc
    if not callable(getattr(provider, "generate_decision_briefs", None)):
        raise ClassificationWorkflowError(
            "Decision Support provider is missing generate_decision_briefs."
        )
    return provider


class CodexFileDecisionSupportProvider:
    """Reviewable local file exchange; it never calls a network or LLM API."""

    provider_id = "codex-file-exchange"

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def generate_decision_briefs(
        self, effective_insights: list[dict[str, object]]
    ) -> object:
        response_path = self.work_dir / "decision_response.json"
        if response_path.exists():
            try:
                return json.loads(response_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ClassificationWorkflowError(
                    f"Decision response contains invalid JSON: {response_path}: {exc}"
                ) from exc
        request_path = self.work_dir / "decision_request.json"
        payload: dict[str, Any] = {
            "task": "evidence_to_decision_support",
            "instructions": DECISION_SUPPORT_INSTRUCTIONS,
            "effective_insights": effective_insights,
            "response_schema": DecisionBriefProposalBatch.model_json_schema(),
            "response_file": response_path.name,
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        if not request_path.exists() or request_path.read_text(encoding="utf-8") != serialized:
            request_path.write_text(serialized, encoding="utf-8")
        raise ClassificationWorkflowError(
            "Decision Support response is pending. Complete the local request file and "
            f"rerun generate-decision-brief: {request_path}"
        )
