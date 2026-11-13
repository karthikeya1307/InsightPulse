# Requirements traceability

This matrix combines three sources: the build specification (prompt 1, sections 1–22), the official
problem statement (included in prompt 1), and the **Submission Guidelines** and **FAQ** PDFs. Each
requirement is mapped to the code that implements it and the evidence that it works. Where the
sources overlap or differ, the solution satisfies both readings (see "Resolved differences" at the
end).

Legend for the status column: ✅ done and verified by a test or a run; ⚠️ done, with a documented
limitation.

## Problem statement

| Requirement | Implementation | Evidence | Status |
|---|---|---|---|
| Combine survey responses, reviews and support text | `feedback_items` unifies all three sources (exactly one source FK, enforced by a CHECK) | `tests/test_database.py`, `check_reconciliation` | ✅ |
| Identify recurring themes, sentiment shifts and service priorities | Theme model, `trend_service.detect_shifts`, `action_service` rules and priorities | `docs/evaluation.md` (BI section), `tests/test_api.py` | ✅ |
| Expose key decisions and exceptions; preserve traceability; measurable outputs | `data_quality_issues`, `state_change_events`, `action_evidence`, evidence IDs on alerts, evaluation report | `GET /api/validation/issues`, action detail evidence, `evaluation_report.json` | ✅ |
| Actors: customer, marketing/sales ops, support agent, analyst, system administrator | Customer: `POST /api/feedback`. Analyst with their own data: CSV upload (`POST /api/feedback/upload`, Upload Feedback page). Ops and analyst: dashboard and analytics. Support: ticket data and support-theme actions. Admin: `POST /api/pipeline/run`, `/health`, validation | README "API" and "Dashboard" | ✅ |
| Workflow 1–2: master data first, then dependents with valid parent IDs | `generator/generate_data.py::build_dataset` order; `EntityPicker` | `tests/test_reproducibility.py::test_children_reference_existing_parents`, reproducibility report FK check | ✅ |
| Workflow 3: product/channel taxonomy, balanced sentiment/theme labels, responses linked to dates and products, neutral/mixed/multilingual examples, theme taxonomy, actions from recurring themes | `generator/config.py` distributions, `text_templates.py`, `taxonomy.py`, `RECURRING_COMPLAINT` rule | Label distributions in `docs/evaluation.md` | ✅ |
| Workflow 4: state changes stored as events, history never overwritten | `state_change_events`, `action_status_events`, append-only predictions | `tests/test_database.py::test_history_tables_are_append_only_by_design`, `tests/test_sentiment.py::test_prediction_history_is_append_only` | ✅ |
| Workflow 5: outcomes and labels from documented rules | `derive_sentiment`, clause tags | `tests/test_sentiment.py::test_label_derivation_rule` | ✅ |
| Entities with PKs and FKs, cardinality, optional relationships, effective dates, lifecycle states, bridge tables, event history | `database/schema.sql` (20 tables) | `docs/data_dictionary.md` (generated from the catalog) | ✅ |
| Anomalies keep an issue_type / ground-truth label rather than silently corrupting rows | `anomaly_injector.py` and `ground_truth.csv` | `tests/test_validation.py` | ✅ |
| IDs `SUR-2026-000001`, `RESP-<survey-id>-001`, `REV-2026-000001`, `MSG-<ticket-id>-001`, `THEME-000001` | `app/utils/id_generator.py`, DB CHECKs | `tests/test_ids.py` | ✅ |
| Step 1: data dictionary (type, required, allowed values, units, uniqueness, validation rule) | `scripts/build_data_dictionary.py` | `docs/data_dictionary.md` | ✅ |
| Step 4: 50,000 reviews/responses and 20,000 support messages, label balance, language variation, sarcasm and ambiguity, fixed seed, same generator at both scales | `SCALES` in `generator/config.py` | `data/exports/full/manifest.json`, `tests/test_reproducibility.py::test_configured_volumes` | ✅ |
| Step 5: valid baseline first, then the 5 labelled edge cases | `AnomalyInjector` runs after the baseline is built | `tests/test_validation.py` (all injected anomalies detected, none unexplained) | ✅ |
| Step 6: derived values computed from source records | `message_count`, ticket dates, feedback fields, `evidence_count` | `ARITHMETIC_RECONCILIATION` passes | ✅ |
| Step 7: separate `ground_truth.csv/json`, never in input features | `data/ground_truth/`, `ml/common.py` guards | `tests/test_api.py::test_ground_truth_only_read_by_evaluation_service`, `tests/test_sentiment.py::test_leakage_guards_reject_label_columns` | ✅ |
| Step 8: validation of PK, FK, enums, date order, arithmetic, lifecycle, duplicates | 17 rules (`docs/business_rules.md` §3) | `validation_summary.json`, `tests/test_validation.py` | ✅ |
| Step 9: CSV/JSON/SQL seed, README, schema and dictionary, generator command, seed, source manifest, validation summary | `scripts/export_data.py`, `database/seed.sql`, docs | `data/exports/<scale>/`, `docs/source_manifest.md` | ✅ |
| Step 10: regenerate twice and compare counts and checksums | `scripts/check_reproducibility.py` | `reproducibility_report.json` (PASS) | ✅ |
| Acceptance: evaluation set has human-reviewed labels | TEST split `evaluation_set` with reviewer and status fields | `docs/evaluation.md` | ⚠️ The reviewers are a synthetic panel (see Assumptions) |
| Acceptance: sentiment and theme metrics are measurable | `evaluation_service` | `GET /api/evaluation/metrics`, `docs/evaluation.md` | ✅ |
| Acceptance: privacy-safe synthetic text | `example.com` e-mails, fake numbers, PII quarantine | `docs/source_manifest.md`, `tests/test_validation.py::test_pii_detection` | ✅ |
| Acceptance: trends traceable to source records | `evidence_feedback_ids` on every alert; `action_evidence` | `tests/test_api.py::test_action_detail_evidence_traceable` | ✅ |
| Users can analyse their own feedback (added on the candidate's request, 2026-11-12) | `app/services/upload_service.py`, `dashboard/pages/7_Upload_Feedback.py`: per-row validation, privacy quarantine, duplicates, scoring, actions refresh | `tests/test_upload.py`, README "Try it with your own data" | ✅ |
| Public data guidance | No external data used | `docs/source_manifest.md` | ✅ |

## Build specification

| § | Requirement | Implementation | Evidence | Status |
|---|---|---|---|---|
| 1 | Python 3.11, FastAPI, Streamlit, PostgreSQL 16, SQLAlchemy 2.x, Pandas/NumPy, scikit-learn, NLTK, Plotly, Pydantic, Pytest, Docker Compose, Git | `requirements.txt`, `docker-compose.yml`, `Dockerfile` | pinned versions; `git log` | ✅ |
| 2.1–2.12 | End-to-end workflow (generate → combine → validate → sentiment → themes → shifts → recurring complaints → actions → DQ tracking → traceability → evaluation) | `pipeline_service.run_pipeline` + services | `scripts/run_pipeline.py` output, `tests/conftest.py` runs it end to end | ✅ |
| 3 | Project structure | Matches the spec. Additions: `app/api/routes/pipeline.py` and `evaluation.py` (required endpoints), `dashboard/pages/3_Trend_Detection.py` (required page), `training_service`, `analytics_service`, `feedback_service`, `pipeline_service`, `model_registry`, extra scripts | repository tree | ✅ |
| 4 | 18 required entities plus normalisation, keys, constraints, indexes, effective dates, lifecycle, timestamps, bridges, required cardinalities, history preserved, source-type match, valid parents, ticket/customer/product/channel consistency, SQL and ORM, transactional ingestion | `schema.sql`, `app/models/` (plus `feedback_annotations` and `pipeline_runs`) | `tests/test_database.py` (ORM matches DB, constraints reject bad rows) | ✅ |
| 5 | Centralised deterministic IDs, zero-padded, immutable, consistent FKs | `id_generator.py` | `tests/test_ids.py`, reproducibility report | ✅ |
| 6 | Parameterised generator, seed 20261022, STARTER 500/500/200, FULL 50k/50k/20k, master first, varied text (pos/neg/neu/mixed, sarcasm, ambiguity, en/te/hi, styles, product terms, complaints, praise, suggestions), rule-derived labels, source- and theme-specific distributions, CLI commands, CSV and JSON | `generator/` | manifests, `tests/test_reproducibility.py` | ✅ |
| 7 | 10-theme taxonomy, multi-theme, validation mechanism, out-of-taxonomy flagged | `taxonomy.py`, `THEME_TAXONOMY` rule, `classify_themes` guard | `tests/test_themes.py` | ✅ |
| 8 | TF-IDF + LogReg sentiment, 4 labels, preprocessing, train/test split, stratified evaluation, confidence, model name and version, timestamp, uncertainty threshold, review flag, no ground truth used, splits separated, honest multilingual handling | `ml/sentiment/model.py`, `sentiment_service.py` | `tests/test_sentiment.py`, `docs/evaluation.md` | ⚠️ multilingual is a baseline (see Limitations) |
| 9 | Multi-label themes, TF-IDF + OneVsRest LogReg, confidences, version, micro/macro F1, precision and recall, taxonomy label space | `ml/themes/model.py` | `docs/evaluation.md` | ✅ |
| 10A | Totals, % for each sentiment, by product, by channel | `analytics_service.sentiment_breakdown` | `tests/test_api.py::test_analytics_endpoints` | ✅ |
| 10B | Top themes, negatives by theme, theme by product, theme trends | `analytics_service.theme_breakdown` | same | ✅ |
| 10C | Daily and weekly trends, MoM, unusual increases, configurable thresholds | `trend_service` | `/api/analytics/trends`; planted scenarios recalled in `docs/evaluation.md` | ✅ |
| 10D | Deterministic action rules with all 13 fields and no duplicate active action without a documented reason | `action_service`, `ux_action_active_issue`, `duplicate_reason` | `tests/test_api.py` (actions tests), `tests/test_database.py::test_no_duplicate_active_action_for_same_issue` | ✅ |
| 11 | 5 injected edge cases, detection and handling, preserved history, `ground_truth.csv/json` with the required fields, separate evaluation permissions | `anomaly_injector.py`, validation, `evaluation_service` token | `tests/test_validation.py`, `tests/test_api.py::test_evaluation_requires_token` | ✅ |
| 12 | 14 validation categories, machine-readable summary, injected vs unexplained | `validation_service.RULES`, `validate_data.py` | `validation_summary.json`, `validation_reconciliation.json` | ✅ |
| 13 | 14 endpoints (plus 2 for CSV upload, added on request) with request validation, schemas, pagination, filtering, errors, sessions, status codes, OpenAPI, real operations, protected evaluation | `app/api/routes/` | `tests/test_api.py`, `/docs` | ✅ |
| 14 | 8-page professional dashboard; Overview contents; Explorer filters, search and pagination; actions with evidence and status updates; DQ exceptions; evaluation matrices and reports; API-connected | `dashboard/` | screenshots checked during development; pages only call the API | ✅ |
| 15 | Separate evaluation set; sentiment, theme, DQ, reproducibility and BI metrics computed, not invented | `evaluation_service.compute_metrics` | `evaluation_report.json`, `docs/evaluation.md` | ✅ |
| 16 | Regenerate twice and compare; CSV, JSON, SQL seed, validation summary, ground truth, dictionary, manifest, README; provenance | `check_reproducibility.py`, `export_data.py`, docs | `data/exports/<scale>/` | ✅ |
| 17 | Compose for PostgreSQL; local commands; 10-step workflow; PowerShell and Linux/macOS | `docker-compose.yml`, README | README quick starts | ✅ |
| 18 | Tests for all 14 listed areas | `tests/` (107 tests) | `pytest -q` | ✅ |
| 19 | Phases 1–10 in order, MVP early | one commit per phase | `git log` | ✅ |
| 20 | Engineering rules: no fake responses or hard-coded analytics, no GT features, nothing silently discarded or overwritten, consistent IDs, parents first, no unverified claims or invented metrics, no TODOs, logging, type hints, transactions, no secrets, bulk ops, configurable thresholds, 16 GB laptop, batching and pagination | across the code | full-scale timings and memory in `docs/evaluation.md`; `.env` git-ignored | ✅ |
| 21 | 20 final deliverables | see README and this file | | ✅ |
| 22 | Work in verified milestones | commit history and test runs | | ✅ |

## Submission Guidelines and FAQ

| Requirement | Where | Status |
|---|---|---|
| Make it run: working code plus clear install, configure, run and verify steps | README quick starts, `pytest`, verification scripts | ✅ |
| Public dedicated GitHub repo with meaningful commits | one commit per phase; pushed to the candidate's new public repository | ✅ once pushed |
| Prompt history of **all** prompts | `prompts/` | ✅ (see the note in `prompts/README.md` about prompts to other tools) |
| Explain where AI sped things up, where reliance was heavy, and what was personally reviewed or validated | `AI_USAGE.md` | ⚠️ the candidate's personal review section must be filled in by the candidate |
| Make reasoning visible: problem, assumptions, decisions, trade-offs, testing, limitations, how we know it works | README, `docs/architecture.md` (decisions and trade-offs), `docs/evaluation.md`, `AI_USAGE.md` | ✅ |
| Free or local services only; avoid heavy dependencies | scikit-learn baselines, no paid APIs, no GPU | ✅ |
| Partial work: state what works, what is incomplete, limitations, next steps, deviations | README "Limitations and next steps", this file | ✅ |
| Nothing secret, personal or proprietary in the repo | `.env` git-ignored, synthetic data, no personal details beyond the commit author e-mail | ✅ |
| Reuse only standard dependencies and small snippets; respect licences | only pinned open-source dependencies | ✅ |
| Be ready to explain decisions and own the submission | `docs/architecture.md` and this matrix are the walkthrough guide | candidate |

## Resolved differences between sources

| Topic | Source A | Source B | Resolution |
|---|---|---|---|
| Volume | Spec: 50k reviews **and** 50k responses | Problem statement: "50,000 reviews/responses" | 50k + 50k (+ 20k messages) satisfies both |
| Entities | Problem statement: 10 core entities | Spec: 18 entities | All 18 are implemented, plus annotations and pipeline runs |
| Dashboard pages | Spec structure lists 6 page files | Spec §14 requires 8 pages | Home is the Overview; the 6 named files are kept and Trend Detection is added (8 pages). Upload Feedback was added later on request (9 pages) |
| Response ID | Problem statement: `RESP-<survey-id>-001` | Spec: `RESP-SUR-2026-000001-001` | Identical format; enforced by a CHECK |
| Tests | FAQ: test suite optional | Spec: tests for all critical functionality | 107 automated tests |
| Live demo | FAQ: not required | n/a | Local run instructions plus Docker profile |
