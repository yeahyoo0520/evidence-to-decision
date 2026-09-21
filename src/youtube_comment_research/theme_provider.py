from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from .evidence import ResearchContext
from .errors import ClassificationWorkflowError
from .profiles import ResearchProfile
from .theme_discovery import CandidateThemeBatch, ConsolidatedThemeBatch

_CANDIDATE_INSTRUCTIONS = """Perform inductive qualitative theme discovery from raw
evidence text. Existing classifications are auxiliary context only. Identify recurring,
research-useful patterns grounded in all supplied evidence. Use the Research Context to
frame relevance, but do not discard important counterevidence or out-of-scope patterns.
Do not invent evidence IDs or source views. Do not generate recommendations, decisions, opportunities, experiments, or a
fixed number of themes. One evidence record may support multiple themes. Prefer specific issue names
over broad labels and do not mechanically rename taxonomy categories."""

_CONSOLIDATION_INSTRUCTIONS = """Consolidate only semantically synonymous candidate
themes; keep merely related themes separate. Every source candidate ID must appear exactly
once. Do not invent IDs or evidence. Representative evidence IDs must come from the selected
candidates. Judge taxonomy coverage semantically from the Theme description, supporting
evidence records, classification context, relevant existing categories, and the supplied
taxonomy definitions—not Theme/Category name similarity. Return a non-empty coverage_reason
that explains that relationship for every Theme. Use covered when the existing taxonomy can
substantially express the Theme's core meaning; partially_covered when it expresses only part
of the Theme and material extra meaning remains; and emergent when the Theme's core user issue
is clearly outside the taxonomy. Related categories do not rule out emergent coverage, and
different names do not imply emergent coverage. Coverage is a preliminary research judgment,
not a deterministic score. Do not generate recommendations or decisions."""


def load_theme_discovery_provider(spec: str) -> object:
    """Load an explicitly configured provider factory as ``module:attribute``."""

    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name.strip() or not attribute_name.strip():
        raise ClassificationWorkflowError(
            "--theme-provider must use the module:attribute form."
        )
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
        provider = factory() if callable(factory) else factory
    except Exception as exc:
        raise ClassificationWorkflowError(
            f"Theme provider could not be loaded from {spec!r}: {exc}"
        ) from exc
    missing = [
        name
        for name in ("discover_candidate_themes", "consolidate_themes")
        if not callable(getattr(provider, name, None))
    ]
    if missing:
        raise ClassificationWorkflowError(
            "Theme provider is missing required methods: " + ", ".join(missing)
        )
    return provider


class CodexFileThemeDiscoveryProvider:
    """Local, reviewable exchange boundary for Codex-assisted theme discovery.

    The provider never makes a network request. Missing response files cause it to write
    strict request files that a Codex session can classify. Re-running the same command
    consumes those responses and advances to consolidation.
    """

    provider_id = "codex-file-exchange"

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._candidate_index = 0
        self._pending_candidate_paths: list[Path] = []

    def discover_candidate_themes(
        self,
        comments: list[dict[str, Any]],
        *,
        profile: ResearchProfile,
        research_context: ResearchContext | None = None,
    ) -> object:
        self._candidate_index += 1
        suffix = f"{self._candidate_index:03d}"
        response_path = self.work_dir / f"candidate_response_{suffix}.json"
        if response_path.exists():
            return self._read_json(response_path)
        request_path = self.work_dir / f"candidate_request_{suffix}.json"
        self._write_request(
            request_path,
            {
                "task": "candidate_theme_discovery",
                "instructions": _CANDIDATE_INSTRUCTIONS,
                "profile_id": profile.profile_id,
                "research_context": (research_context or ResearchContext()).model_dump(),
                "comments": comments,
                "response_schema": CandidateThemeBatch.model_json_schema(),
                "response_file": response_path.name,
            },
        )
        self._pending_candidate_paths.append(request_path)
        return {"themes": []}

    def finalize_candidate_stage(self) -> None:
        if self._pending_candidate_paths:
            paths = ", ".join(str(path) for path in self._pending_candidate_paths)
            raise ClassificationWorkflowError(
                "Candidate Theme responses are pending. Complete these local request "
                f"files and rerun generate-insights: {paths}"
            )

    def consolidate_themes(
        self,
        candidates: list[dict[str, Any]],
        comments: list[dict[str, Any]],
        *,
        profile: ResearchProfile,
        research_context: ResearchContext | None = None,
    ) -> object:
        response_path = self.work_dir / "consolidation_response.json"
        if response_path.exists():
            return self._read_json(response_path)
        request_path = self.work_dir / "consolidation_request.json"
        self._write_request(
            request_path,
            {
                "task": "theme_consolidation_and_taxonomy_coverage",
                "instructions": _CONSOLIDATION_INSTRUCTIONS,
                "profile_id": profile.profile_id,
                "research_context": (research_context or ResearchContext()).model_dump(),
                "taxonomy_definitions": profile.category_definitions,
                "candidates": candidates,
                "comments": comments,
                "response_schema": ConsolidatedThemeBatch.model_json_schema(),
                "response_file": response_path.name,
            },
        )
        raise ClassificationWorkflowError(
            "Theme consolidation response is pending. Complete the local request file "
            f"and rerun generate-insights: {request_path}"
        )

    @staticmethod
    def _read_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ClassificationWorkflowError(
                f"Theme provider response contains invalid JSON: {path}: {exc}"
            ) from exc

    @staticmethod
    def _write_request(path: Path, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        if path.exists() and path.read_text(encoding="utf-8") == serialized:
            return
        path.write_text(serialized, encoding="utf-8")
