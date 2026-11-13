# InsightPulse: AI-powered customer feedback intelligence

InsightPulse brings together **survey responses, product reviews and support messages**. From that
combined feedback it finds recurring themes, sentiment shifts and service priorities, and turns them
into **traceable, prioritised business actions**. It also tracks every data-quality exception, and it
measures its models against a separate human-reviewed ground truth.

It is an end-to-end MVP that runs locally. It has a seeded synthetic data generator, PostgreSQL 16
(20 tables), a transactional ingestion and validation engine, scikit-learn sentiment and multi-label
theme models, trend and shift detection, rule-based action generation, a FastAPI backend, an 8-page
Streamlit dashboard, an automated test suite, and exports with reproducibility evidence.

> Everything in this repository is synthetic. No real customer data, no paid APIs, and no secrets
> are included.

## Contents

- [What it does](#what-it-does)
- [Results](#results-measured-not-estimated)
- [Quick start (Windows PowerShell)](#quick-start-windows-powershell)
- [Quick start (Linux / macOS)](#quick-start-linux--macos)
- [Running the full 120k-record dataset](#running-the-full-dataset)
- [API](#api)
- [Dashboard](#dashboard)
- [Tests and verification](#tests-and-verification)
- [Repository layout](#repository-layout)
- [Assumptions](#assumptions)
- [Limitations and next steps](#limitations-and-next-steps)
- [AI usage and prompt history](#ai-usage-and-prompt-history)

## What it does

| Step | What happens | Where |
|---|---|---|
| 1. Generate | Builds master data (customers, products, channels, surveys), then reviews, survey responses, tickets and messages, all with valid parent IDs. Text is assembled from tagged clauses in English, Hindi and Telugu (native and romanised scripts), with sarcasm, ambiguity, mixed sentiment and suggestions. Labels are derived from the clauses, never drawn at random. Five kinds of labelled anomalies are injected afterwards. | `generator/` |
| 2. Ingest | File-level validation, then a bulk `COPY` into PostgreSQL in one transaction. Rejected rows are written to `data/exceptions/`. | `ingestion_service.py` |
| 3. Validate | 17 rules covering all 14 required categories. Duplicates are flagged, personal data is quarantined (with redaction), contradicting or leaking labels are excluded from training, and every state change is recorded as an event. | `validation_service.py` |
| 4. Learn | Sentiment: TF-IDF with Logistic Regression (POSITIVE, NEGATIVE, NEUTRAL, MIXED). Themes: TF-IDF with One-vs-Rest Logistic Regression over the 10-theme taxonomy. Both are trained on TRAIN and tuned on VALIDATION; TEST is never used. | `ml/`, `training_service.py` |
| 5. Score | Batched inference. Each prediction stores confidence, model name and version, timestamp, uncertainty threshold and a human-review flag. A multilingual fallback flags low-confidence Hindi and Telugu predictions. Prediction history is append-only. | `sentiment_service.py` |
| 6. Analyse | Sentiment and theme analytics; daily, weekly and monthly trends; month-over-month comparison; z-score shift alerts with configurable thresholds. Each alert links to its feedback IDs. | `analytics_service.py`, `trend_service.py` |
| 7. Act | Three deterministic rules (negative concentration, recurring complaint, sentiment shift) create prioritised actions with an owner, due date, recommendation and evidence links. There is never a duplicate active action for the same issue. | `action_service.py` |
| 8. Evaluate | A token-protected module compares stored predictions with the human-reviewed TEST labels and the anomaly registry. It reports model, data-quality and BI metrics. | `evaluation_service.py` |

Detailed docs: [architecture](docs/architecture.md) · [business rules](docs/business_rules.md) ·
[data dictionary](docs/data_dictionary.md) · [evaluation report](docs/evaluation.md) ·
[source manifest](docs/source_manifest.md) · [requirements traceability](docs/requirements_traceability.md)

## Results (measured, not estimated)

All figures come from `data/exports/<scale>/evaluation_report.json`, which is produced by
`python -m scripts.run_pipeline --evaluate`, and from the reproducibility and probe reports. See
[docs/evaluation.md](docs/evaluation.md) for confusion matrices, per-theme metrics, slices and how to
read these numbers.

| Metric | Starter (1.1k items) | Full (112k items) |
|---|---|---|
| Sentiment, TEST set: accuracy / macro F1 | 0.970 / 0.968 | 1.000 / 1.000 |
| Sentiment baseline: lexicon only, macro F1 | 0.769 | 0.786 |
| Themes, TEST set: micro F1 / macro F1 | 0.960 / 0.953 | 1.000 / 1.000 |
| **Free-text probe (30 out-of-template sentences): sentiment accuracy** | **0.600** (8 of 12 errors flagged for review) | **0.700** (6 of 9 errors flagged for review) |
| Free-text probe: theme micro F1 | 0.800 | 0.849 |
| Injected anomalies detected | 28 / 28 | 2436 / 2436 |
| Unexplained data-quality issues | 0 | 0 |
| PK / FK violations | 0 / 0 | 0 / 0 |
| Validation pass rate | 97.5% | 97.8% |
| Planted sentiment shifts found (theme alerts / alerts or actions) | 20% / 60% | 100% / 100% |
| Actions generated (all with traceable evidence) | 7 (100% traceable) | 34 (100% traceable) |
| Reproducibility (regenerate twice: counts, SHA-256, IDs, labels, FKs) | PASS | PASS |
| End-to-end pipeline time / peak memory | 11 s / 281 MB | 284 s / 1,178 MB |

**How to read this.** TEST labels come from the same templates that generated the training text, so
TEST scores are an upper bound. The free-text probe is the honest signal: the baseline handles plain
complaints and praise, but struggles with sarcasm, ambiguity and native-script Hindi and Telugu. Most
of its mistakes are routed to human review. Theme-level shift detection needs volume: at the starter
scale it catches 1 of the 5 planted events, and at full scale it catches all 5.

## Quick start (Windows PowerShell)

Prerequisites: Python 3.11, Git, and Docker Desktop (or a local PostgreSQL 16 server).

```powershell
# 1. Clone and create a virtual environment
git clone https://github.com/ChinmayeePrasad327/InsightPulse.git insightpulse
cd insightpulse
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install requirements
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Configure .env (then edit the passwords and the evaluation token)
Copy-Item .env.example .env

# 4. Start PostgreSQL 16 (it also creates the insightpulse_test database used by pytest)
docker compose up -d db

# 5. Initialise the schema and reference data
python -m scripts.setup_db

# 6. Generate the starter dataset (deterministic)
python -m generator.generate_data --scale starter --seed 20261022

# 7. Load and validate (writes data/exceptions/starter/validation_summary.json)
python -m scripts.run_pipeline --scale starter --steps ingest validate
python -m scripts.validate_data --scale starter

# 8. Train, score, generate actions and evaluate
python -m scripts.run_pipeline --scale starter --steps train predict actions --evaluate

# 9. Start the API (http://localhost:8000/docs)
uvicorn app.main:app --port 8000

# 10. In a second terminal (activate .venv first), start the dashboard (http://localhost:8501)
streamlit run dashboard/Home.py
```

Steps 5 to 8 can also be run as a single command:
`python -m scripts.run_pipeline --scale starter --generate --reset-db --evaluate`

If script execution is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

If Docker Desktop reports a **WSL error** ("wsl.exe ... The system cannot find the file specified"), WSL
is not installed. In an administrator terminal run
`dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart` and
`dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart`, restart, then run
`wsl --update`. Alternatively, skip Docker: install PostgreSQL 16 for Windows and follow "Without Docker" below.

## Quick start (Linux / macOS)

```bash
git clone https://github.com/ChinmayeePrasad327/InsightPulse.git insightpulse && cd insightpulse
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
cp .env.example .env                       # then edit the passwords and the evaluation token
docker compose up -d db                    # PostgreSQL 16 + insightpulse_test database
python -m scripts.setup_db
python -m generator.generate_data --scale starter --seed 20261022
python -m scripts.run_pipeline --scale starter --steps ingest validate
python -m scripts.validate_data --scale starter
python -m scripts.run_pipeline --scale starter --steps train predict actions --evaluate
uvicorn app.main:app --port 8000           # API + OpenAPI docs at /docs
streamlit run dashboard/Home.py            # in a second terminal
```

**Without Docker:** create a role and two databases on a local PostgreSQL 16, then point
`DATABASE_URL` and `TEST_DATABASE_URL` in `.env` at them:

```sql
CREATE ROLE insightpulse LOGIN PASSWORD 'change-me-locally';
CREATE DATABASE insightpulse OWNER insightpulse;
CREATE DATABASE insightpulse_test OWNER insightpulse;
```

**Containers for everything:** `docker compose --profile app up -d --build` starts the database, the
API and the dashboard. Run the pipeline once with
`docker compose run --rm api python -m scripts.run_pipeline --generate --reset-db`.

## Running the full dataset

```bash
python -m generator.generate_data --scale full --seed 20261022     # 50k reviews, 50k responses, 20k messages
python -m scripts.run_pipeline --scale full --reset-db --evaluate  # ingest -> validate -> train -> predict -> actions
python -m scripts.check_reproducibility --scale full               # regenerate twice and compare
```

Then set `DATA_SCALE=full` in `.env` so that the API and dashboard evaluate the full dataset.

Measured on the build machine (cloud container, 16 GB RAM): generation about 12 s, ingest 17 s,
validation 15 s, training 140 s, scoring 111k items 89 s,
actions 2 s. Total **4.7 minutes**, with peak memory of
**1.2 GB**.

The full dataset is not committed uncompressed because it can be regenerated byte for byte. A zipped
copy, plus `manifest.json` and `checksums.json`, is in `data/exports/full/`.

## API

Interactive OpenAPI docs are at `http://localhost:8000/docs`. Every endpoint does real work against
the database.

| Method and path | Purpose |
|---|---|
| `GET /health` | Database connectivity, schema table count, feedback count, active model versions |
| `GET /api/feedback` | Paginated list. Filters: product, channel, source, sentiment, theme, language, status, date range, text search `q`, `needs_review` |
| `POST /api/feedback/upload` | Upload a CSV of feedback (only `text` is required, up to 2,000 rows). Each row is validated, privacy-checked, de-duplicated and scored; the response gives a status, sentiment and themes for every row |
| `GET /api/feedback/upload/template` | A CSV template with every column and three example rows that use real product names |
| `GET /api/feedback/{feedback_id}` | Source record, prediction history, data-quality issues, linked actions, status history |
| `POST /api/feedback` | Submit feedback. It is validated, checked for personal data and duplicates, and scored, all in one transaction (201, 409, 422) |
| `GET /api/analytics/overview` | KPIs, sentiment mix, top themes, weekly negative trend, priority actions, data-quality summary |
| `GET /api/analytics/sentiment` | Distribution overall and by product, channel, source and language |
| `GET /api/analytics/themes` | Frequency, negatives by theme, theme by product, monthly theme trends |
| `GET /api/analytics/trends` | Day, week or month series, month over month, shift alerts with evidence (thresholds can be overridden per request) |
| `GET /api/actions` · `GET /api/actions/{id}` | Actions with filters and pagination; detail view with evidence and status history |
| `POST /api/actions` | Create a manual action (409 if an active action already covers the issue and no `duplicate_reason` is given) |
| `PATCH /api/actions/{id}/status` | Lifecycle transition (409 if the transition is invalid; every change is recorded as an event) |
| `GET /api/validation/issues` · `/summary` · `/rules` | Data-quality exceptions, machine-readable summary, rule catalogue |
| `POST /api/pipeline/run` · `GET /api/pipeline/runs[/{id}]` | Run pipeline steps asynchronously (202) or with `wait=true`; check run status |
| `GET /api/evaluation/metrics` | **Protected.** Needs the `X-Evaluation-Token` header (401 without it, 403 if wrong) |

Example:

```bash
curl -s "http://localhost:8000/api/feedback?sentiment=NEGATIVE&theme_id=THEME-000003&page_size=5"
curl -s -H "X-Evaluation-Token: $EVALUATION_ACCESS_TOKEN" http://localhost:8000/api/evaluation/metrics
```

## Dashboard

There are nine pages, and every one gets its data from the API: Overview (Home), Feedback Explorer
(filters, search, pagination, detail view), Sentiment Analysis, Theme Analysis, Trend Detection
(configurable alert thresholds), Business Actions (evidence and status updates), Data Quality and
Exceptions, Model Evaluation (metrics, confusion matrices, classification reports; reads the token
from `.env`), and Upload Feedback (see below). If the API is down, or a page has no data yet, it shows
an explanatory empty state.

## Try it with your own data

1. Open the **Upload Feedback** page in the dashboard and click **Download template**.
2. Replace the example rows with your own feedback. Only `text` is required. Reviews also need a
   `rating` (1 to 5) and a `product_name` from the catalogue (the template shows the format).
   `feedback_date` (`YYYY-MM-DD`) places a review on the trend charts.
3. Upload the file and click **Upload and analyse**. You get a status for every row (analysed,
   quarantined for personal data, duplicate, or rejected with the reason), plus the sentiment,
   confidence and themes, charts of the results and a downloadable results CSV.
4. With **Refresh business actions** ticked, the action rules run again, so a cluster of complaints in
   your upload can raise an action. The new rows also appear in every other page.

Without the dashboard: `curl -F "file=@my_feedback.csv" http://localhost:8000/api/feedback/upload`.
Rows without a `customer_id` are attributed to a single synthetic guest customer (`CUS-999999`).
Uploaded rows have no ground-truth label, so they are scored but never counted in the evaluation metrics.

## Tests and verification

```bash
pytest -q          # uses TEST_DATABASE_URL (insightpulse_test) and a temporary data/model folder
```

The suite has 107 tests in 8 modules. It generates starter data and runs the whole pipeline against
the test database. It covers ID generation, data generation and deterministic reproducibility,
database constraints and foreign keys, every validation rule (duplicates, personal data, taxonomy,
leakage), sentiment and theme predictions, action generation (fields, no duplicates, idempotency,
lifecycle), all API endpoints, pagination, protection of the evaluation endpoint, and **ground-truth
isolation**. That last check is static: no training, inference or API module may read ground truth.

Other verification scripts:

| Command | Output |
|---|---|
| `python -m scripts.validate_data --scale starter` | File and DB validation plus reconciliation of injected vs unexplained issues (`data/exceptions/<scale>/`) |
| `python -m scripts.check_reproducibility --scale starter` | Regenerates twice and compares counts, checksums, IDs, labels and relationships (`reproducibility_report.json`) |
| `python -m scripts.free_text_probe` | Accuracy on 30 hand-written sentences that no template produced (honest generalisation check) |
| `python -m scripts.export_data --scale starter` | Enriched feedback, actions and issues as CSV/JSON, plus a data-only SQL seed (`data/exports/<scale>/`) |
| `python -m scripts.build_data_dictionary` | Regenerates `docs/data_dictionary.md` from the live database catalog |

## Repository layout

```
app/            FastAPI app: api/routes, core (config, database, logging, taxonomy, enums),
                models (SQLAlchemy), schemas (Pydantic), services (all business logic), utils
dashboard/      Streamlit Home.py + 7 pages (API client only)
generator/      Seeded synthetic data generator and anomaly injector
ml/             Feature pipeline + leakage guards, sentiment and theme models, saved_models/ (git-ignored)
data/           raw/ master/ ground_truth/ (separate, protected) exceptions/ exports/ probe/
database/       schema.sql (source of truth), seed.sql (reference data), init/, migrations/
scripts/        setup_db, run_pipeline, validate_data, export_data, check_reproducibility,
                free_text_probe, build_data_dictionary
tests/          pytest suite (7 modules)
docs/           architecture, data dictionary, business rules, evaluation, source manifest,
                requirements traceability
prompts/        verbatim prompt history       AI_USAGE.md   AI usage disclosure
```

## Assumptions

* **Volume:** "50,000 reviews/responses" is read as 50,000 reviews **and** 50,000 survey responses,
  plus 20,000 support messages. This satisfies both readings.
* **Feedback unit:** each customer-written source record is one FeedbackItem. Agent replies on tickets
  are stored, but they are not customer feedback.
* **Human-reviewed evaluation set:** the TEST split (15%, stratified) has labels that come from the
  documented generation rules and are marked as reviewed by a (synthetic) review panel. No real human
  labelling took place. This is stated wherever the metrics appear.
* **Time zone:** the data is generated in Asia/Kolkata (IST) for January to October 2026. Analytics
  group by IST calendar dates.
* **Access control:** a single evaluation token protects ground truth. That is enough for an MVP; a
  production system would use per-user roles.
* **Taxonomy:** the 10 themes are fixed and effective-dated. Changing them requires a data migration;
  the API never changes them.

## Limitations and next steps

| Limitation | Impact | Next step |
|---|---|---|
| Text is template-generated and labels come from the templates | TEST metrics overstate real accuracy (the probe reaches 60–70%) | Label a sample of real feedback; evaluate on a template-disjoint split |
| The baseline TF-IDF model does not understand Hindi or Telugu | Native-script accuracy is lower; low-confidence cases are flagged, and the lexicon fallback is small | Multilingual sentence embeddings (e.g. a small multilingual transformer); a larger lexicon |
| Sarcasm is only learnt from template patterns | Sarcastic praise of a failure is often misread | Add sarcasm-specific training data; use the review queue as a feedback loop |
| "Human-reviewed" evaluation labels come from a synthetic panel | No measure of real inter-annotator agreement | Double-annotate a real sample and report Cohen's kappa |
| A single shared evaluation token | Coarse access control | Per-user roles (analyst vs evaluator) and audit logging |
| Theme alerts need weekly volume | Low-volume shifts (starter scale) are found by action rules rather than alerts | Bayesian or Poisson baselines for small counts; per-product alerting |
| Prediction history grows with every run | Table size at many re-scoring runs | Partition by run and archive superseded runs |
| No authentication on the API and dashboard (besides evaluation) | Fine for a local MVP only | OAuth2/OIDC in front of the API; role-based dashboard |
| The product, channel and customer catalogue is synthetic | Uploaded feedback must name a product from it | Add catalogue upload (products, customers) alongside feedback upload |
| Thresholds were set by inspecting synthetic data | They may not suit a real business | Tune them with stakeholders; the values live in `.env` |

What works: every item in the [requirements traceability matrix](docs/requirements_traceability.md)
is implemented and verified. Nothing is left as a TODO. The one exception is the candidate's personal
review statement in `AI_USAGE.md`, which only the candidate can write.

## AI usage and prompt history

This project was built with an AI coding agent. See [AI_USAGE.md](AI_USAGE.md) for what the AI did,
what was verified and how, and the defects that execution caught. Every prompt is in
[prompts/](prompts/README.md), verbatim.
