# AI usage disclosure

The Submission Guidelines ask candidates to explain where AI sped up the work, where they relied
heavily on it, and what they personally reviewed, changed or validated. This file answers those three
questions.

## Tools used

| Tool | Used for |
|---|---|
| Claude (Anthropic), working as a coding agent in a Claude Code cloud session inside a Claude project | Design, implementation, test writing, running every command and verification step, and documentation for this whole repository |

The prompts are in [`prompts/`](prompts/README.md), copied verbatim.

## How the work was split

**Candidate (Siraj):** wrote the detailed build specification and the phase plan, and supplied the
official problem statement plus the Submission Guidelines and FAQ PDFs. Directed the agent to treat
the specification, problem statement and PDFs as a single set of requirements. Owns the
submission and its technical choices: Python 3.11, FastAPI, Streamlit, PostgreSQL 16 and
scikit-learn baselines (all set in the specification).

**AI agent (heavy reliance):** wrote the code, SQL, tests, scripts and docs in this repository. The
agent worked phase by phase, as the specification requires. Before each commit it ran the code against a
real PostgreSQL 16 database, the test suite, and the API and dashboard (dashboard pages were checked
with headless-browser screenshots). Every metric in `docs/` was produced by running the pipeline.
None were written by hand.

**Where AI sped things up most:** the 20-table schema and ORM mirror, the template-based text
generator, the 17 validation rules, about 100 tests, the dashboard, and the documentation. Producing
these by hand in 72 hours would not have been realistic.

## Verification performed, and defects it caught

Running the system, not just writing it, caught real defects. All of the following were found by
execution and then fixed:

| Found by | Defect | Fix |
|---|---|---|
| API smoke test | `/health` counted views as schema tables | Count only `BASE TABLE`s |
| Ingestion run | Valid surveys with a future `end_date` and annotations were rejected as "future dates" | Planned-date fields exempted; annotation dates capped at the data end |
| Pipeline run | The actions step inside a still-running run saw no predictions | `v_current_sentiment` ignores only *failed* runs |
| Rule review | The duplicate-action guard was keyed per rule, which allowed two active actions for one issue | Unique index on (product or ALL, theme), with a documented `duplicate_reason` exception |
| Lexicon test | "not worth it" was scored as positive | Negated positive words are subtracted |
| Evaluation review | Macro-F1 on language slices averaged labels that were absent from the slice | Average only over labels present |
| Screenshots | Chart legends overlapped titles; text was clipped | Margins and title anchoring adjusted |
| `tests/test_api.py` | A manual action with `duplicate_reason` still violated the unique index (the reason was set after the INSERT) | The reason is written in the INSERT |
| `tests/test_api.py` | An exact repeat review via the API was accepted | Returns 409 |
| `tests/test_sentiment.py` | "Absolutely love this product..." was predicted NEGATIVE, because "love" appeared almost only in sarcastic negative templates | The generator now adds theme-free overall opinions, so the vocabulary is balanced |
| `tests/test_validation.py` | Issues on records submitted live through the API were counted as "unexplained" | Reported separately as live-submission issues |
| Full-scale run | 155 survey responses fell after their product's discontinuation date (a survey window ran past it) | Product surveys now close at discontinuation |
| Full-scale run | 2 accidental duplicate reviews (short ambiguous texts colliding) | The generator guarantees no duplicates in the baseline; duplicates are only injected |
| Full-scale timing | Training refit TF-IDF features once per C value | Features are fitted once and reused |
| Full-scale timing | The actions step took 31 minutes. The `DISTINCT ON` "current prediction" view was badly estimated, so PostgreSQL chose nested loops that re-evaluated the view for every row. Once the driver prepared the statement, the generic plan made it worse. | View rewritten as an anti-join (migration 001); alert evidence fetched in one query; server-side prepared statements disabled. One query went from more than 5 minutes to 0.6 s; shift detection from 239 s to 1.2 s |
| Full-scale API check | The evaluation endpoint scored the starter ground truth against the full database and returned meaningless metrics without any error | Returns 409 when the ground truth does not match the loaded dataset |

## What the candidate personally reviewed, changed or validated

> **To be completed by the candidate before submission.** Record what you read line by line, what
> you ran yourself (for example the README quick start, `pytest`, the dashboard), anything you changed
> or rejected, and the decisions you can defend in the walkthrough. The FAQ requires you to
> understand the core logic, key design choices, dependencies and limitations.

## Limits of AI involvement to be aware of

* The metrics are honest but optimistic. The text is template-generated, so the test set shares
  phrasing patterns with the training set. `docs/evaluation.md` quantifies this and reports a
  free-text probe.
* The Hindi and Telugu handling is a baseline (character n-grams plus a small lexicon, and human
  review when confidence is low). It is not real multilingual understanding.
