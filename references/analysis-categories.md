# Rule-based analysis categories

| Category | Intended meaning |
|---|---|
| `Product Praise` | Positive statements about product features or experience |
| `Product Concern` | Complaints, risks, doubts, discomfort, reliability, price, or performance concerns |
| `Product Question` | Questions about product functions, compatibility, performance, fit, or operation |
| `Purchase Question` | Questions about value, price, availability, shipping, or where to buy |
| `Usage Scenario` | Mentions of contexts such as running, commuting, office, gym, or travel |
| `Competitor Comparison` | Direct comparisons with another brand or model |
| `Purchase Intent` | Statements indicating an order, planned purchase, or purchase consideration |
| `Feature Request` | Requests for a missing or improved feature |
| `After-sales Issue` | Warranty, return, refund, replacement, or support matters |
| `Other` | No deterministic rule matched |

## Interpretation rules

- Classification is a first-pass triage, not ground truth.
- A comment can contain several themes, but the MVP assigns one primary category.
- A brand mention alone is not a competitor comparison. An explicit relation
  such as `vs`, `better than`, `compare`, or `which is better` is required.
- A question mark alone does not make a comment a purchase question.
- Every result retains `comment_id`, rule evidence, and confidence.
- Results below the confidence threshold set `manual_review=true`.
- Category totals should be described as counts within the collected sample only.
