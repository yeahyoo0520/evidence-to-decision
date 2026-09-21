# Output schema

This document summarizes public artifact contracts. The JSON Schema files in
`schemas/` and the Pydantic models in `src/` remain authoritative.

## EvidenceRecord

`schemas/evidence.schema.json` defines source-neutral input.

| Field | Contract |
|---|---|
| `evidence_id` | Required stable identifier; unique within an input file |
| `text` | Required original qualitative evidence text |
| `source_type` | Required supported source type |
| `source_name` | Optional human-readable source label |
| `created_at` | Optional timestamp; `timestamp` is accepted as an input alias |
| `language`, `market`, `region` | Optional source context |
| `metadata` | Optional source-specific object retained without reinterpretation |

Generic input does not claim that a live connector exists for every supported
source type. YouTube collection is a separate source adapter and retains its own
video, comment, reply, publication, engagement, and anonymization fields.

## ResearchContext

`schemas/research_context.schema.json` contains:

- `research_question`: optional question guiding discovery and interpretation;
- `decision_context`: optional description of the decision being framed;
- `analysis_mode`: `exploratory` or `decision_focused`.

Decision-focused mode does not filter contrary evidence or authorize unsupported
recommendations.

## Classification

`schemas/comment_classification.schema.json` validates the common semantic shape.
Each result retains its Evidence ID, subject, one primary category, optional
secondary categories, subtopics, stage, confidence, quoted evidence, review risk,
review triggers, optional verification recommendation, and version metadata.

Categories, subjects, and stages are validated a second time against the selected
Research Profile. Evidence excerpts must occur in the original source text. Rule
candidates remain auxiliary and are not confirmed findings.

## Theme

`schemas/theme.schema.json` defines a preliminary Theme with:

- stable Theme ID, name, and description;
- related profile categories;
- exact Evidence IDs and evidence count;
- representative Evidence IDs and excerpts;
- `taxonomy_based` or `llm_inductive` discovery method;
- `covered`, `partially_covered`, or `emergent` taxonomy coverage;
- a non-empty `coverage_reason`, confidence, and preliminary status.

Representative IDs must be a subset of Theme Evidence IDs. Evidence counts must
equal the number of unique IDs. Coverage is an explainable research judgment, not
a deterministic accuracy score.

## Insight Draft

`schemas/insight_draft.schema.json` links one cautious AI-generated draft to one
validated Theme. Observation describes the evidence pattern; interpretation adds
a bounded explanation; the compatibility `potential_opportunity` field carries a
validation or review cue in current public presentation. Insight Evidence IDs may
not exceed the source Theme scope.

`schemas/theme_insight_bundle.schema.json` combines Research Context, Themes, and
Insight Drafts while enforcing unique IDs and valid links.

## EffectiveInsightRecord

`schemas/effective_insight.schema.json` is the downstream interface after Optional
Verification is resolved. It preserves source Theme/Insight IDs, final observation
and interpretation, Evidence IDs, representative IDs, confidence, coverage,
Research Context, provenance, and review signals.

`verification_status` is one of:

- `unreviewed`: original AI output; downstream eligible by default;
- `reviewed`: explicitly confirmed;
- `edited`: human-edited content with `human_edited` provenance;
- `rejected`: excluded from downstream use.

`review_recommended` must match non-empty `review_reasons`. It is advisory and is
not a default approval gate. Explicit strict mode can require reviewed or edited
status for a particular workflow.

## DecisionBriefRecord

`schemas/decision_brief.schema.json` defines evidence-bound decision support:

- source Effective Insight IDs and Theme IDs;
- computed supporting Evidence IDs;
- Research Context and verification statuses;
- Why It Matters and an explicit Decision Question;
- structured Possible Directions with rationale, evidence basis, and tradeoff;
- Evidence Gaps;
- Validation Plan items;
- review recommendation, uncertainty note, and generated status.

The fixed status is `AI-generated decision support`. A Decision Brief is not a
final decision and does not execute a Validation Plan.

## Evidence Gap

Each gap records what is missing, why it matters, related Effective Insight IDs,
and any related in-scope Evidence IDs. Related IDs may not introduce evidence
outside the source Effective Insights.

## Validation Plan

Each item keeps a validation question, proposed method, required evidence,
evaluation criterion, and limitation separate. The plan is a candidate next step,
not an automatically executed action.

## Provenance invariants

- IDs are unique within each artifact and references must resolve.
- Representative Evidence is always a subset of the parent Evidence scope.
- Theme consolidation may merge valid candidate scopes but may not invent IDs.
- Insight Evidence remains within its Theme.
- Decision Brief supporting Evidence is computed from source Effective Insights;
  provider output cannot attach the entire dataset or unrelated records.
- Rejected Insights are downstream ineligible; unreviewed Insights remain eligible
  unless strict mode is explicitly enabled.

## Workbook presentation

Generic Workbook raw data preserves `evidence_id`, `text`, `source_type`,
`source_name`, `created_at`, `language`, `market`, `region`, and `metadata`.
YouTube Workbooks retain source-specific video, comment, and reply fields. Both
presentations preserve source rows and provenance without claiming qualitative
samples are representative.
