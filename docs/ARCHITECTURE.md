# Architecture

## 1. Core concept

Evidence to Decision is a source-neutral qualitative research pipeline. Its core
unit is Evidence rather than a platform comment. Source adapters normalize input;
the research engine classifies evidence, discovers patterns, drafts insights, and
frames decision support while preserving provenance.

The main path is:

`ResearchContext → Evidence → Classification → Theme → Insight → Effective Insight → Decision Brief`

## 2. Evidence abstraction

`EvidenceRecord` requires a stable `evidence_id`, text, and source type. Optional
context includes source name, timestamp, language, market, region, and metadata.
These fields let one pipeline represent different qualitative sources without
pretending that all sources share the same collection mechanics.

Internally, the current compatibility adapter maps `evidence_id` to the existing
trace identity used by the original YouTube implementation. It does not create a
second identity chain. Generic Workbook output returns to source-neutral field
names.

## 3. Source adapters

A source adapter is an ingestion boundary, not the research engine:

```text
YouTube API → YouTube adapter ┐
Generic JSON → Evidence loader ├→ EvidenceRecord → research engine
Future adapter → normalizer   ┘
```

v0.1 includes Generic JSON ingestion and a YouTube source workflow. The Evidence
model can represent more source types than the number of live adapters included.

## 4. Profiles and taxonomy

Research Profiles hold domain-specific goals, questions, subjects, categories,
stages, review settings, and Insight questions. The common schema validates the
shape of semantic output; Python then validates profile-specific values.

`general_feedback` is the source-neutral default for mixed qualitative evidence.
`game_publishing` carries player and publishing concepts. Other bundled profiles
support consumer-product, go-to-market, and overseas-content research. Profiles
keep domain taxonomy out of the core models.

## 5. ResearchContext

`ResearchContext` records a research question, decision context, and analysis
mode. `exploratory` mode permits bounded pattern discovery without inventing a
missing decision. `decision_focused` mode frames the same complete evidence set
against an explicit decision context. Neither mode authorizes removing contrary
evidence.

## 6. Semantic analysis

Classification and Theme Discovery are separate:

- Classification applies the selected profile to individual Evidence records.
- `taxonomy_based` Theme Discovery groups stable, known classification patterns.
- `llm_inductive` Theme Discovery reasons from raw text, then maps resulting
  Themes back to related categories.

Inductive Themes retain `taxonomy_coverage` and `coverage_reason`. Coverage is a
provider-assisted research judgment constrained by evidence, taxonomy definitions,
and validation—not a deterministic string match.

## 7. Provider abstraction

Semantic steps use provider interfaces:

- Built-in file providers write structured requests and consume separately
  completed, schema-constrained responses.
- Tests inject deterministic fake providers for offline regression coverage.
- Authorized custom providers can implement the same interfaces.

The built-in provider does not call a network model service. v0.1 includes no
OpenAI, Anthropic, or Gemini API adapter.

## 8. Traceability

Each downstream layer carries stable references:

```text
Decision Brief
  └─ source Effective Insight IDs
       └─ source Insight and Theme IDs
            └─ Evidence IDs
                 └─ original Evidence text and source metadata
```

Validators reject unknown, duplicated, missing, or out-of-scope IDs. Decision
Brief supporting evidence is computed from its source Effective Insights; a
provider cannot attach unrelated dataset records.

## 9. Optional Verification

Verification is optional by default:

- `unreviewed`: original AI output; eligible downstream.
- `reviewed`: explicitly confirmed.
- `edited`: explicitly revised, with the revision used downstream.
- `rejected`: excluded downstream.

`review_recommended` is an advisory signal with concrete reasons. It highlights
low confidence, limited evidence, emergent coverage, or traceability warnings
without turning the workflow into a mandatory approval queue. Explicit strict
mode can require review when a particular use case needs it.

## 10. Effective Insight

`EffectiveInsightRecord` is the stable downstream interface. It resolves the
original AI draft and any optional review decision into one record while keeping
verification state and provenance. Decision Support therefore does not need to
understand the internal Human Review packet structure.

## 11. Decision Support

A `DecisionBriefRecord` separates:

- Why It Matters
- a neutral Decision Question
- Possible Directions with tradeoffs
- Evidence Gaps
- a Validation Plan
- an uncertainty note

The output is labeled AI-generated decision support. Directions are possibilities,
not proven recommendations. Gaps and validation steps remain distinct so readers
can see which claims require more evidence.

## 12. Boundaries

The system does not make a final business decision, execute a Validation Plan, or
automatically act on external systems. It does not guarantee that qualitative
evidence is representative, and semantic quality depends on the agent, model, or
custom provider used. Domain expertise and appropriate professional review remain
necessary for high-stakes decisions.
