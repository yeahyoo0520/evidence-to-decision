# Codex-assisted Evidence Classification Rubric

Rubric version: `phase2a3-rubric-v1`

This rubric governs semantic first-pass classification of source evidence by Codex. The output is
an initial annotation for human review, not a confirmed finding. Rule-based
candidates may be supplied to researchers separately, but must never be shown
to Codex as input and must not determine the semantic label.

## Research profile contract

Load the profile recorded in the classification manifest before classifying.
Use only that profile's `primary_categories`, `subject_options`, and
`stage_options`. The fixed consumer-product examples below apply only when the
profile is `consumer_product`; for other profiles, use their category
definitions and research questions. Never translate a consumer-only label into
another profile by keyword substitution.

All evidence records in one prepared workflow must use the same profile, profile hash,
rubric version, and classifier version.

## Required decision order

1. Read the complete evidence record and determine its main subject using the
   selected profile's `subject_options`. For `consumer_product`, these are
   `product`, `brand`, `video_or_creator`, `filming_or_editing`,
   `another_commenter`, `unrelated`, and `unclear`; other profiles define their
   own valid subjects.
2. Identify every intent explicitly expressed in the evidence.
3. Select one primary category that best represents the main intent.
4. Preserve genuine additional intents as secondary categories.
5. Identify subtopics independently from the primary category.
6. Determine lifecycle stage independently; lifecycle must not decide the
   primary category.
7. Calibrate confidence and decide whether human review is required.

## Consumer-product intent ordering

Determine the evidence record's overall communicative purpose before scoring local
phrases or the final sentence when the profile is `consumer_product`. Use the
following ordering rules:

1. Long-term use, overall satisfaction, repeated endorsement, or an explicit
   recommendation normally supports `Product Praise`.
2. If a comment mainly describes the current product experience and only later
   mentions a future purchase, upgrade, or next generation, keep
   `Product Praise` primary and preserve `Purchase Intent` as secondary.
3. Mentioning that a product replaced another product does not automatically
   make `Competitor Comparison` primary. When replacement supports an overall
   endorsement, use `Product Praise` as primary and comparison as secondary.
4. Use `Product Concern` as primary only when the overall purpose is to report
   a problem, express dissatisfaction, warn others, or seek a remedy.
5. When overall praise and a local problem coexist, compare the amount of text,
   emotional force, opening claim, and concluding claim. Do not let one defect
   keyword automatically override the overall evaluation.
6. Use `Competitor Comparison` as primary only when comparing alternatives,
   differences, or relative performance is the main communicative purpose.
   When comparison merely supports praise or concern, keep it secondary.
7. Do not choose the primary category solely from the final sentence.
8. When two interpretations remain similarly strong after applying these
   rules, lower confidence and require human review.

## Subject constraints

Only comments whose main subject is `product` or `brand` may use these primary
categories:

- `Product Praise`
- `Product Concern`
- `Product Question`
- `Feature Request`
- `After-sales Issue`

Praise for the creator, video, narration, voice, editing, camera work, location,
or demonstration is normally `Other`, with subject `video_or_creator` or
`filming_or_editing`.

Comments about another commenter must use subject `another_commenter`. Off-topic
comments use `unrelated`. Use `unclear` when the subject cannot be determined
without guessing.

## Allowed primary and secondary categories

- `Product Praise`
- `Product Concern`
- `Product Question`
- `Purchase Question`
- `Usage Scenario`
- `Competitor Comparison`
- `Purchase Intent`
- `Feature Request`
- `After-sales Issue`
- `Other`

The primary category must represent the comment's central communicative intent,
not the first positive, negative, or interrogative phrase. Secondary categories
must be explicitly supported by the text and must not repeat the primary
category.

## Questions

Distinguish among:

- Product-function questions: `Product Question`
- Price, selection, purchase channel, or availability questions:
  `Purchase Question`
- Questions that mainly express a negative experience or concern:
  `Product Concern`, with `Product Question` only when genuinely secondary
- Questions unrelated to the product: `Other`
- Rhetorical questions: classify the underlying assertion, not the punctuation

Question marks do not determine a category by themselves.

## Comparison constraint

A brand or product mention alone is not a comparison. Use
`Competitor Comparison` only when the comment expresses a comparison relation,
such as `vs`, `better than`, `worse than`, a difference between two named
products, a preference, or a switch from one product to another.

## Lifecycle stages

- `Unknown`
- `Considering`
- `Waiting for Launch`
- `Purchased`
- `Using`
- `Returned`
- `Replacing`
- `Repurchasing`

Lifecycle describes the commenter or clearly identified product subject.
Experiences belonging only to another person must not be assigned to the
commenter's lifecycle. A past purchase does not automatically mean current
purchase intent.

## Evidence and uncertainty

- Evidence must contain only short exact expressions found in the comment.
- Do not paraphrase inside `evidence`.
- Do not add facts, opinions, products, brands, subtopics, or lifecycle stages
  that the comment does not express.
- Set `manual_review=true` when the subject, primary intent, entity target, or
  lifecycle cannot be determined reliably.
- Complexity alone does not require review when the main intent and entities are
  clear.
- `review_reason` must be non-empty when `manual_review=true`.

## Review risk is independent from confidence

`confidence` answers only: "How confident are we that the selected primary
category is the best primary label?" It does not answer whether the comment is
safe to publish without review.

Every result must separately output:

- `review_risk`: `low`, `medium`, or `high`
- `review_triggers`: a concise array of explainable trigger codes or phrases

Default to at least `medium` risk when any of these conditions is present:

- two or more strong intents;
- `Product Praise` + `Product Concern`;
- `Product Praise` + `Purchase Intent`;
- `Product Concern` + `Competitor Comparison`;
- the subject mixes product and video/creator content;
- an explicit contrast marker such as `but`, `however`, `although`, `despite`,
  or `still`;
- primary and secondary evidence have similar strength;
- a long comment contains multiple lifecycle signals;
- evidence supports opposing sentiment;
- confidence is at least 0.80 and a strong secondary category remains.

Use short triggers such as:

- `multiple_strong_intents`
- `praise_concern_tension`
- `praise_purchase_intent_tension`
- `concern_comparison_tension`
- `mixed_product_creator_subject`
- `contrast_marker:but`
- `close_primary_secondary_evidence`
- `multiple_lifecycle_signals`
- `opposing_sentiment`
- `high_confidence_strong_secondary`
- `formal_marketing_use`

Set `manual_review=true` when:

- `review_risk=high`;
- the primary category and second candidate cannot be stably ordered;
- overall praise coexists with a specific failure or defect;
- comparison, concern, and purchase behaviour coexist;
- the comment will be used as a high-engagement representative quote or in a
  formal marketing conclusion.

A multi-topic comment may still have high confidence when the primary intent is
clear, but high confidence must not cancel review risk or manual review.

Phase 2B-lite interprets `manual_review` as mandatory confirmation before the
comment can support a formal conclusion. Low risk is normally false. Medium
risk is also normally false and is handled through deterministic QA sampling,
unless the comment is selected as core evidence or the primary ordering remains
unstable. High risk is always true. Human review is quality control, not full
manual annotation.

Confidence calibration:

- reduce confidence when primary and secondary evidence are close;
- cap confidence at 0.85 when the primary category depends on implicit meaning;
- cap confidence at 0.80 when product and creator/video subjects are mixed;
- do not lower confidence solely because a clearly subordinate topic exists;
- do not infer `manual_review=false` from confidence.

## Output contract

Return JSONL only: one strict JSON object per input comment, with no Markdown,
code fences, commentary, missing records, duplicate IDs, or invented IDs.
Every object must satisfy `schemas/comment_classification.schema.json`.
