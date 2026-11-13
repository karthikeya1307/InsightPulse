# Business rules

Every rule in this document is deterministic and implemented in code. Each section names the module,
and every threshold can be changed in `.env` (see `.env.example`) without editing code.

## 1. Data generation rules (`generator/`)

| Rule | Implementation |
|---|---|
| Master data is generated before dependent records: channels, products, customers, surveys, then responses, reviews, tickets and messages. | `generator/generate_data.py::build_dataset` |
| A child record only references a parent that exists on the record date. A customer must have signed up, a product must be launched and not discontinued, and a survey must be open. | `EntityPicker`, `generate_survey_responses` |
| A product-specific survey closes no later than its product's discontinuation date. | `master_generator.generate_surveys` |
| **Sentiment labels are derived, not drawn at random.** Text is assembled from clauses, and each clause carries a (theme, polarity) tag. The label comes from `derive_sentiment` over those polarities: positive and negative together give MIXED, positive only gives POSITIVE, negative only gives NEGATIVE, and neutral only or no clause gives NEUTRAL. | `feedback_generator.derive_sentiment` |
| **Theme labels are the themes of the clauses used.** Theme-free "overall opinion" clauses carry polarity only, never a theme. | `TextComposer` |
| Ratings and NPS are sampled *conditionally on the derived label*, using `RATING_DIST` and `NPS_RANGE`. A documented 0.4% of records get an explained mismatch (for example, a 5-star rating with a complaint), and these carry an annotator note. | `rating_for`, `_maybe_explained_mismatch` |
| Sarcasm is negative clauses written with positive words (10% of English NEGATIVE texts). Ambiguous texts are theme-free statements labelled NEUTRAL (20% of NEUTRAL). | `ScaleConfig` |
| Language: customers with a Hindi or Telugu preference write in that language 40% of the time. Half of those texts are romanised (Latin script). | `choose_variant` |
| Planted sentiment shifts: while a scenario event is active, matching records are forced to NEGATIVE feedback about the event's theme with probability `intensity`. All events are listed in `scenario_events.json`. | `config.SCENARIO_EVENTS`, `active_event` |
| Derived values are computed from source records, never generated on their own: ticket `message_count`, `resolved_at` and `closed_at` come from messages and events; FeedbackItem fields are copied from the source record. | `generate_support`, `ingestion_service.build_feedback_items` |
| The baseline contains no accidental duplicates. Duplicates exist only where they are injected on purpose. | `generate_reviews`, `generate_survey_responses` |
| Split: stratified 70/15/15 TRAIN/VALIDATION/TEST by source × sentiment. Annotations exist only for TRAIN and VALIDATION. TEST is the human-reviewed evaluation set. | `stratified_splits` |

## 2. Injected edge cases (`generator/anomaly_injector.py`)

Anomalies are injected **after** valid baseline records are generated. Each one is written to
`data/ground_truth/<scale>/ground_truth.csv/json` with these fields: issue ID, record ID, issue type,
expected detection, expected rule, expected resolution, severity, and ground-truth label.

| Edge case | How it is injected | Expected detection (rule) | Resolution |
|---|---|---|---|
| Label contradicts the text without explanation | An annotation's label is flipped (for example, POSITIVE on a 1-star complaint), with no annotator note | `LABEL_CONTRADICTION` | The annotation is `EXCLUDED` from training |
| Duplicate survey response | A response is copied under a new response ID: same survey, same customer, same text | `DUPLICATE_RESPONSE` | The copy becomes `DUPLICATE`; the original is kept |
| Customer identity leaked | The customer's own e-mail, phone number or full name is added to the text | `PRIVACY_LEAK` | The item becomes `QUARANTINED` with `redacted_text`; the raw source is kept |
| Theme outside the taxonomy | The annotation gets a label such as `THEME-000099` or `SHIPPING_SPEED` | `THEME_TAXONOMY` | The label is rejected and the annotation excluded |
| Test labels in model input | A TEST-split record gets a TRAIN annotation | `TARGET_LEAKAGE` (CRITICAL) | The annotation is excluded; the training guard raises an error if one ever reaches the model |

Original source records are never modified or deleted. Every resolution is a status change, and each
change is recorded in `state_change_events` together with the run that caused it.

## 3. Validation rules (`app/services/validation_service.py`)

There are two layers. **File level** checks run before loading: rejected rows are written to
`data/exceptions/<scale>/rejected_records.csv`, and when a parent is rejected its children are rejected
too, so nothing is silently dropped. **Database level** checks run after loading, and every issue is
stored in `data_quality_issues`.

| # | Required category | Rule code(s) | Layer | Default resolution |
|---|---|---|---|---|
| 1 | Primary-key uniqueness | `PK_UNIQUENESS` | file + DB PK | REJECTED |
| 2 | Foreign-key integrity | `FK_INTEGRITY` | file + DB FK | REJECTED (cascades to children) |
| 3 | Allowed enum values | `ENUM_VALUE` | file + DB CHECK | REJECTED |
| 4 | Missing required fields | `REQUIRED_FIELD` | file + DB NOT NULL | REJECTED |
| 5 | Invalid dates | `INVALID_DATE` | file | REJECTED |
| 6 | Date ordering | `DATE_ORDER`: response inside the survey window, feedback after signup and within the product lifecycle, message after ticket opened, message sequence chronological, annotation after feedback | DB | FLAGGED |
| 7 | Duplicate detection | `DUPLICATE_RESPONSE`, `DUPLICATE_FEEDBACK` (same customer, product and text hash) | DB | FLAGGED, item becomes DUPLICATE |
| 8 | Rating boundaries | `RATING_BOUNDS` (rating 1–5, NPS 0–10, counters ≥ 0) | file + DB CHECK | REJECTED |
| 9 | Lifecycle transitions | `LIFECYCLE_TRANSITION`: ticket and action status events follow their state machines (`app/core/enums.py`) and the latest event equals the current status; the same holds for feedback items | DB | FLAGGED |
| 10 | Arithmetic reconciliation | `ARITHMETIC_RECONCILIATION`: ticket `message_count` equals the number of messages; action `evidence_count` equals the number of evidence links; the number of feedback items equals responses + reviews + customer messages, and every source record has a feedback item | DB | FLAGGED |
| 11 | Source-specific relationship consistency | `SOURCE_CONSISTENCY`: each feedback item's customer, product, channel, date and rating equal its source record (for messages, the ticket); a channel supports the source type; a survey's product and channel match its responses | DB | FLAGGED |
| 12 | Theme taxonomy | `THEME_TAXONOMY` | DB | REJECTED |
| 13 | Privacy leaks | `PRIVACY_LEAK` (regex for e-mail and Indian mobile numbers, plus the customer's own full name) | DB and API inline | QUARANTINED |
| 14 | Ground-truth / target leakage | `TARGET_LEAKAGE`, plus runtime guards `assert_feature_columns` and `assert_no_test_records` in `ml/common.py` | DB + training | EXCLUDED_FROM_TRAINING |
| + | Identifier formats | `ID_FORMAT` | file + DB CHECK | REJECTED |
| + | Label contradiction | `LABEL_CONTRADICTION`: label against star rating for rated sources, label against lexicon for unrated ones; explained if an annotator note exists | DB | EXCLUDED_FROM_TRAINING |

The **validation summary** (`data/exceptions/<scale>/validation_summary.json`, also served at
`GET /api/validation/summary`) gives, for every rule, the issue count, PASS/FAIL, severity breakdown and
resolution, along with the totals. Reconciliation against the injected anomalies classifies each issue
into one of three groups (`scripts/validate_data.py`, `evaluation_service.evaluate_data_quality`):

* **injected**: matches an entry in `ground_truth.csv` (rule and record);
* **live submission**: concerns a record submitted through `POST /api/feedback` after generation, which
  the ground truth cannot cover;
* **unexplained**: everything else. This should be 0, and any non-zero value is a defect to investigate.

## 4. Sentiment decision rules (`app/services/sentiment_service.py`)

1. The model outputs class probabilities, and confidence is the maximum probability.
2. English text with confidence below `SENTIMENT_UNCERTAINTY_THRESHOLD` (0.55) is marked `needs_human_review` (`LOW_CONFIDENCE`).
3. Hindi or Telugu text (native or romanised script, detected by `detect_language`): if confidence is below
   `MULTILINGUAL_REVIEW_THRESHOLD` (0.70), a multilingual lexicon is consulted. If the lexicon has a clear
   signal (≥ 0.6) that disagrees with the model, its label is used (`MODEL+LEXICON_FALLBACK`). The record
   is always flagged `NON_ENGLISH_LOW_CONFIDENCE`.
4. A star rating that strongly disagrees with the predicted label (POSITIVE with ≤ 2 stars, or NEGATIVE
   with ≥ 4 stars) is flagged `RATING_DISAGREES`. The rating is never used as a model feature.
5. Every prediction stores the label, confidence, all four probabilities, model name and version,
   threshold, review flag and reason, detected language, method and timestamp. Predictions are
   **appended** per run, and `v_current_sentiment` selects the latest prediction from runs that did not fail.

Theme predictions use per-theme thresholds tuned on VALIDATION. Any theme outside the taxonomy raises a
`TaxonomyError`, so it can never be stored.

## 5. Trend and shift detection (`app/services/trend_service.py`)

These rules run for the overall series and for each theme, by day, week or month. Only analysable
feedback is included (not DUPLICATE or QUARANTINED).

```
baseline = mean negative rate over the previous TREND_BASELINE_PERIODS periods   (default 6)
z        = (rate - baseline) / max(stdev of those periods, 0.03)
ALERT if volume >= TREND_MIN_VOLUME (8)
     and rate - baseline >= TREND_MIN_INCREASE (0.10)
     and z >= TREND_Z_THRESHOLD (2.0)
```

Each alert lists the negative feedback IDs from its period, which makes it traceable. The API accepts
per-request overrides (`z_threshold`, `min_increase`, `min_volume`, `baseline_periods`). The
month-over-month view reports volume change (%) and negative-rate change (percentage points).

## 6. Action generation (`app/services/action_service.py`)

Rules are evaluated over the last `ACTION_LOOKBACK_DAYS` (90) days, ending at the latest feedback date.

| Rule | Scope | Condition | Priority |
|---|---|---|---|
| `NEGATIVE_THEME_CONCENTRATION` | product × theme | negatives ≥ `ACTION_MIN_NEGATIVE_COUNT` (5) **and** negative share ≥ `ACTION_MIN_NEGATIVE_SHARE` (0.45) **and** lift (share ÷ the theme's share across all products) ≥ `ACTION_MIN_LIFT` (1.3) | CRITICAL if share ≥ 0.75 and negatives ≥ 15; HIGH if share ≥ 0.6 or negatives ≥ 15; otherwise MEDIUM |
| `RECURRING_COMPLAINT` | product × theme | ≥ 5 negatives per month, with a monthly share at or above the theme baseline, for ≥ 3 consecutive months | MEDIUM |
| `SENTIMENT_SHIFT` | theme, all products | ≥ 1 weekly theme alert (section 5) inside the window | CRITICAL if peak z ≥ 2 × threshold, otherwise HIGH |

Every action stores the action ID (`ACT-2026-######`), title, description (with the numbers that
triggered it), product (NULL means all products), theme, rule, priority, evidence count, supporting
feedback IDs (in the `action_evidence` bridge table), recommended action, owner (by theme), due date,
creation timestamp, lifecycle status, analysis window and the metrics behind the decision.

* **Due date:** CRITICAL +7 days, HIGH +14, MEDIUM +30, LOW +45 (`ACTION_DUE_DAYS_*`).
* **No duplicate active actions:** there is at most one OPEN, IN_PROGRESS or BLOCKED action per
  (product or ALL, theme). The database enforces this with the partial unique index `ux_action_active_issue`. When a later
  run finds the same issue again, it appends new evidence to the existing action and does not create
  a new one. A second active action can be opened manually only with a documented `duplicate_reason`;
  otherwise the API returns 409.
* **Lifecycle:** `OPEN → IN_PROGRESS | DISMISSED`, `IN_PROGRESS → BLOCKED | RESOLVED | OPEN`,
  `BLOCKED → IN_PROGRESS | DISMISSED`, `RESOLVED → CLOSED | IN_PROGRESS`; CLOSED and DISMISSED are
  terminal. Invalid transitions return 409. Each change is a new row in `action_status_events`.

## 7. API submission rules (`POST /api/feedback`)

* Pydantic validates the request: ID formats, rating 1–5, NPS 0–10, a non-blank text of at most 5,000
  characters, and the fields each source needs (a rating for reviews and surveys, a survey ID, a ticket
  ID; no rating on support messages).
* The referenced customer, product, channel, survey and ticket must exist and agree with one another
  (ticket owner, survey product and channel, channel supports the source type). Otherwise → 422.
* A closed survey or ticket returns 409. An exact repeat of the same review (same customer, product
  and text) returns 409.
* Text containing personal data is stored as `QUARANTINED`, with the redacted text shown and a
  `PRIVACY_LEAK` issue recorded. It is never scored.
* The source record, the feedback item, the inference run, the predictions and the state events are
  all written in **one transaction**.

## 8. Ground-truth access (`app/services/evaluation_service.py`)

* Ground truth lives only in `data/ground_truth/<scale>/`. It is read **only** by
  `evaluation_service.open_ground_truth(token)`, which requires `EVALUATION_ACCESS_TOKEN` (checked with
  a constant-time comparison).
* `GET /api/evaluation/metrics` requires the `X-Evaluation-Token` header. It returns 401 without the
  header and 403 with a wrong token. Inference endpoints never return ground-truth fields.
* `tests/test_api.py::test_ground_truth_only_read_by_evaluation_service` statically checks that no
  training, inference, ingestion or API module imports the evaluation module or refers to ground-truth files.
