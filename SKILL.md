---
name: youtube-comment-research
description: Turn generic qualitative evidence or public YouTube comments into profile-aware classifications, evidence-linked themes, cautious insights, optional verification, decision briefs, and readable workbooks.
---

# Evidence to Decision

Use this skill for source-neutral qualitative research that must preserve a
traceable path from original Evidence to Insight and Decision Support. YouTube is
one source-specific workflow; the research core accepts Generic Evidence JSON.

## Product principles

- AI-first: valid AI-generated output may continue without row-by-row approval.
- Evidence-grounded: every important Theme, Insight, and Decision Brief retains
  source Evidence IDs.
- Human-on-demand: verification is available when requested or recommended.
- Decision-oriented: surface a question, options, gaps, and validation needs.
- Explicit about uncertainty: do not turn qualitative evidence into unsupported
  prevalence, causality, or certainty claims.

Never describe a collected sample as representative without an appropriate study
design. Do not infer sensitive traits. Do not expose personal identifiers in a
public example or report.

## Set the research context first

Before a formal analysis, establish:

- the research goal and questions;
- the selected Research Profile;
- the evidence scope and sampling strategy;
- an optional decision context;
- whether analysis is `exploratory` or `decision_focused`.

`ResearchContext` frames discovery and interpretation. It never authorizes
discarding contradictory evidence.

## Research Profiles

Load one profile from `profiles/` and use it throughout a prepared workflow:

- `general_feedback`: source-neutral needs, praise, pain points, questions,
  barriers, trust, comparisons, usage, and service research;
- `consumer_product`: backward-compatible product taxonomy;
- `game_publishing`: player, publishing, localization, monetization, and version
  research;
- `gtm_research`: value, use case, adoption, pricing, and message-market fit;
- `overseas_content`: audience reaction, information needs, content requests,
  and cultural feedback.

The common semantic schema validates structure. Python additionally validates
subjects, categories, and stages against the selected profile. Do not mix profiles
within one batch, merge, or Theme bundle.

## Installation

From the project root:

```bash
python -m venv .venv
python -m pip install -e ".[test]"
```

Use either the installed `youtube-comment-research` command or
`python scripts/youtube_comments.py`.

## Generic Evidence

A Generic Evidence JSON file is an array of `EvidenceRecord` objects. Each item
requires `evidence_id`, `text`, and `source_type`. Optional fields are
`source_name`, `created_at` (or input alias `timestamp`), `language`, `market`,
`region`, and `metadata`.

Supported source types describe the data model; they do not imply that v0.1 ships
a live connector for each source. Generic JSON and YouTube collection are the
current ingestion paths.

## Semantic classification

Prepare sanitized, checksummed batches of at most 20 records:

```bash
youtube-comment-research prepare-classification INPUT.json \
  --output-dir output/classification \
  --batch-size 20 \
  --profile general_feedback
```

Follow `references/classification_rubric.md` and the selected profile. Write one
strict JSON object per line to each requested `classified_batch_NNN.jsonl`.
Do not include rule predictions, human answers, expected labels, markdown, or
prose outside JSONL. Evidence excerpts must be short exact substrings of the
original text.

Validate and merge:

```bash
youtube-comment-research merge-classification INPUT.json \
  output/classification \
  --output output/classification_result.csv \
  --profile general_feedback
```

Merge fails on missing or duplicate IDs, modified batch checksums, schema errors,
profile-invalid values, and evidence excerpts absent from the source record. It
does not silently skip records or overwrite protected human fields.

Deterministic `analyze` output is a rule-based candidate layer. It is auxiliary,
not a confirmed finding and not a substitute for semantic judgment.

## Theme Discovery and Insight Drafts

Classification asks what an individual Evidence record expresses. Theme Discovery
asks what patterns connect records.

Taxonomy-based discovery is deterministic:

```bash
youtube-comment-research generate-insights output/classification_result.csv \
  --output output/theme_insights.json \
  --profile general_feedback \
  --theme-method taxonomy
```

For inductive discovery, add `--theme-method llm` and `--llm-work-dir`. The built-in
file provider performs a local exchange:

1. Python writes candidate request files.
2. A compatible agent reads raw Evidence and writes candidate responses.
3. Python validates them and writes a consolidation request.
4. The agent writes the consolidation response.
5. Python validates schema, Evidence IDs, profile mappings, and provenance before
   producing the final Theme and Insight Draft bundle.

The built-in provider never calls a network model API. Tests use deterministic
fake providers. An explicitly authorized custom provider may implement the same
interface; v0.1 includes no built-in OpenAI, Anthropic, or Gemini adapter.

Theme rules:

- form a Theme only when its Evidence is supported by related Evidence;
- bind only the Evidence IDs that support that Theme;
- keep related but meaningfully different patterns separate;
- map categories after inductive discovery rather than forcing the Theme from a
  taxonomy label;
- use `covered`, `partially_covered`, or `emergent` with a non-empty,
  evidence-aware `coverage_reason`;
- keep Observation, Interpretation, and the validation cue separate;
- use cautious language and never invent prevalence, causality, or user intent.

## Effective Insights and Optional Verification

Resolve Theme and Insight Draft output into the downstream interface:

```bash
youtube-comment-research resolve-insights output/theme_insights.json \
  --output output/effective_insights.jsonl
```

Verification status is workflow state, not truth level:

- no decision → `unreviewed`, original AI version, downstream eligible;
- confirm → `reviewed`, original AI version;
- edit → `edited`, explicitly revised version;
- reject → `rejected`, excluded downstream.

`review_recommended=true` is advisory and requires concrete `review_reasons`. It
does not block default processing. Only an explicitly requested
`--require-review` strict workflow excludes unreviewed Insights.

When a user asks to inspect or revise an Insight:

```bash
youtube-comment-research prepare-review output/theme_insights.json \
  --output output/review_packet.json
```

Show the Theme, Insight, and supporting Evidence. Never choose confirm, edit, or
reject for the user. Apply only the user's explicit response with `apply-review`.
Edits must retain in-scope Evidence; rejection requires a reason or reviewer note.
The original AI bundle remains unchanged.

## Decision Support

Decision Support consumes only eligible `EffectiveInsightRecord` objects. It
keeps Why It Matters, a neutral Decision Question, Possible Directions and their
tradeoffs, Evidence Gaps, a Validation Plan, and uncertainty separate.

```bash
youtube-comment-research generate-decision-brief \
  output/effective_insights.jsonl \
  --output output/decision_briefs.json \
  --work-dir output/decision_work
```

The built-in command writes `decision_request.json` and consumes a separately
completed `decision_response.json`; it never calls a network model API. Provider
responses cite source Effective Insight IDs. Python computes supporting Evidence
IDs from those sources and rejects out-of-scope IDs.

Decision Briefs are AI-generated decision support, not final recommendations.
Possible Directions are candidates, Evidence Gaps identify what is not known, and
Validation Plans are not automatically executed.

## Readable Workbook

Generate a workbook from existing data without calling YouTube:

```bash
youtube-comment-research report output/classification_result.csv \
  --output output/research_workbook.xlsx \
  --profile general_feedback \
  --theme-insights output/theme_insights.json \
  --decision-briefs output/decision_briefs.json
```

The generator detects Generic Evidence versus YouTube input:

- Generic presentation uses an Evidence-first overview, `04_分类概览`,
  `05_来源对比`, source-neutral raw fields, `Validation Cue`, and
  `10_可选核验`.
- YouTube presentation retains comment/reply grouping, yellow top-level rows,
  blue reply rows, `05_视频对比`, YouTube raw fields, and its compatibility
  labels.

Both modes retain complete original records, evidence traceability, wrapped long
text, Theme Discovery, Insight Drafts, Optional Verification state, and Decision
Support.

## YouTube source workflow

Live YouTube collection requires `YOUTUBE_API_KEY` and uses only the official
YouTube Data API v3:

```bash
youtube-comment-research collect \
  --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --max-comments 100 \
  --order relevance \
  --include-replies \
  --anonymize-authors \
  --formats csv,xlsx,jsonl \
  --profile consumer_product \
  --output-dir output
```

Only collect public, accessible comments. Do not bypass login, CAPTCHA, regional
restrictions, or platform controls. Default to anonymized authors. Report invalid
video, comments-disabled, quota, permission, and network errors explicitly. Never
publish author channel IDs or display names without a legitimate, user-authorized
need.

## Boundaries and references

- The system does not make or execute a final business decision.
- Validation Plans are proposed next checks, not automatic actions.
- Qualitative evidence is not automatically representative.
- Semantic quality depends on the agent, model, or authorized provider used.
- High-stakes use requires appropriate domain expertise and review.

See `references/output-schema.md` for artifact contracts,
`references/classification_rubric.md` for classification standards, and
`docs/ARCHITECTURE.md` for system boundaries.
