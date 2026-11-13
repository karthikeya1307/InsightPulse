-- =============================================================================
-- InsightPulse - PostgreSQL 16 schema
-- Source of truth for the relational model. SQLAlchemy models in app/models
-- mirror this file (tests/test_database.py checks they stay in sync).
--
-- Design principles
--   * Every entity has a primary key; every relationship has a foreign key.
--   * Source records (responses, reviews, messages) are immutable inputs.
--   * FeedbackItem unifies the three feedback sources (exactly one source FK).
--   * Predictions, theme assignments and status changes are append-only
--     history tables; "current" values are exposed through views.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
CREATE TABLE sentiment_labels (
    label_code     VARCHAR(10)  PRIMARY KEY
                   CHECK (label_code IN ('POSITIVE','NEGATIVE','NEUTRAL','MIXED')),
    display_name   VARCHAR(30)  NOT NULL,
    polarity       SMALLINT     NOT NULL CHECK (polarity BETWEEN -1 AND 1),
    description    TEXT         NOT NULL,
    display_order  SMALLINT     NOT NULL UNIQUE
);

CREATE TABLE themes (
    theme_id        VARCHAR(12)  PRIMARY KEY CHECK (theme_id ~ '^THEME-[0-9]{6}$'),
    theme_code      VARCHAR(40)  NOT NULL UNIQUE,
    theme_name      VARCHAR(80)  NOT NULL UNIQUE,
    description     TEXT         NOT NULL,
    keywords        TEXT[]       NOT NULL DEFAULT '{}',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    effective_from  DATE         NOT NULL,
    effective_to    DATE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

-- ---------------------------------------------------------------------------
-- Master data
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    customer_id         VARCHAR(10)  PRIMARY KEY CHECK (customer_id ~ '^CUS-[0-9]{6}$'),
    first_name          VARCHAR(60)  NOT NULL,
    last_name           VARCHAR(60)  NOT NULL,
    email               VARCHAR(120) NOT NULL UNIQUE CHECK (email ~ '^[^@\s]+@[^@\s]+\.[a-z]{2,}$'),
    phone               VARCHAR(20)  NOT NULL,
    city                VARCHAR(60)  NOT NULL,
    state               VARCHAR(60)  NOT NULL,
    country             VARCHAR(60)  NOT NULL DEFAULT 'India',
    preferred_language  VARCHAR(2)   NOT NULL CHECK (preferred_language IN ('en','hi','te')),
    segment             VARCHAR(12)  NOT NULL CHECK (segment IN ('CONSUMER','SMB','ENTERPRISE')),
    signup_date         DATE         NOT NULL,
    lifecycle_status    VARCHAR(10)  NOT NULL CHECK (lifecycle_status IN ('ACTIVE','INACTIVE','CHURNED')),
    effective_from      DATE         NOT NULL,
    created_at          TIMESTAMPTZ  NOT NULL,
    updated_at          TIMESTAMPTZ  NOT NULL,
    CHECK (effective_from >= signup_date)
);
CREATE INDEX ix_customers_segment ON customers (segment);

CREATE TABLE products (
    product_id         VARCHAR(10)   PRIMARY KEY CHECK (product_id ~ '^PRD-[0-9]{6}$'),
    sku                VARCHAR(30)   NOT NULL UNIQUE,
    product_name       VARCHAR(120)  NOT NULL UNIQUE,
    category           VARCHAR(40)   NOT NULL,
    brand              VARCHAR(60)   NOT NULL,
    unit_price         NUMERIC(10,2) NOT NULL CHECK (unit_price > 0),
    currency           CHAR(3)       NOT NULL DEFAULT 'INR',
    launch_date        DATE          NOT NULL,
    discontinued_date  DATE,
    lifecycle_status   VARCHAR(12)   NOT NULL CHECK (lifecycle_status IN ('ACTIVE','DISCONTINUED')),
    created_at         TIMESTAMPTZ   NOT NULL,
    updated_at         TIMESTAMPTZ   NOT NULL,
    CHECK (discontinued_date IS NULL OR discontinued_date >= launch_date),
    CHECK ((lifecycle_status = 'DISCONTINUED') = (discontinued_date IS NOT NULL))
);
CREATE INDEX ix_products_category ON products (category);

CREATE TABLE channels (
    channel_id        VARCHAR(10)  PRIMARY KEY CHECK (channel_id ~ '^CHN-[0-9]{6}$'),
    channel_code      VARCHAR(30)  NOT NULL UNIQUE,
    channel_name      VARCHAR(60)  NOT NULL UNIQUE,
    channel_type      VARCHAR(12)  NOT NULL
                      CHECK (channel_type IN ('WEB','MOBILE_APP','MARKETPLACE','EMAIL','SMS','PHONE','CHAT','SOCIAL')),
    supports_reviews  BOOLEAN      NOT NULL,
    supports_surveys  BOOLEAN      NOT NULL,
    supports_support  BOOLEAN      NOT NULL,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ  NOT NULL,
    CHECK (supports_reviews OR supports_surveys OR supports_support)
);

CREATE TABLE surveys (
    survey_id      VARCHAR(16)  PRIMARY KEY CHECK (survey_id ~ '^SUR-[0-9]{4}-[0-9]{6}$'),
    title          VARCHAR(160) NOT NULL,
    survey_type    VARCHAR(20)  NOT NULL CHECK (survey_type IN ('CSAT','NPS','PRODUCT_FEEDBACK','POST_SUPPORT')),
    product_id     VARCHAR(10)  REFERENCES products (product_id),   -- optional: product-specific survey
    channel_id     VARCHAR(10)  NOT NULL REFERENCES channels (channel_id),
    question_text  TEXT         NOT NULL,
    start_date     DATE         NOT NULL,
    end_date       DATE         NOT NULL,
    status         VARCHAR(8)   NOT NULL CHECK (status IN ('DRAFT','ACTIVE','CLOSED')),
    created_at     TIMESTAMPTZ  NOT NULL,
    updated_at     TIMESTAMPTZ  NOT NULL,
    CHECK (end_date >= start_date)
);
CREATE INDEX ix_surveys_product ON surveys (product_id);

-- ---------------------------------------------------------------------------
-- Source (transaction) records - immutable inputs
-- ---------------------------------------------------------------------------
CREATE TABLE survey_responses (
    response_id    VARCHAR(25)  PRIMARY KEY CHECK (response_id ~ '^RESP-SUR-[0-9]{4}-[0-9]{6}-[0-9]{3}$'),
    survey_id      VARCHAR(16)  NOT NULL REFERENCES surveys (survey_id),
    customer_id    VARCHAR(10)  NOT NULL REFERENCES customers (customer_id),
    product_id     VARCHAR(10)  NOT NULL REFERENCES products (product_id),
    channel_id     VARCHAR(10)  NOT NULL REFERENCES channels (channel_id),
    response_date  TIMESTAMPTZ  NOT NULL,
    rating         SMALLINT     NOT NULL CHECK (rating BETWEEN 1 AND 5),
    nps_score      SMALLINT     CHECK (nps_score BETWEEN 0 AND 10),
    response_text  TEXT         NOT NULL CHECK (length(btrim(response_text)) > 0),
    language       VARCHAR(2)   NOT NULL CHECK (language IN ('en','hi','te')),
    created_at     TIMESTAMPTZ  NOT NULL,
    CHECK (response_id LIKE 'RESP-' || survey_id || '-%')
);
CREATE INDEX ix_responses_survey   ON survey_responses (survey_id);
CREATE INDEX ix_responses_customer ON survey_responses (customer_id);
CREATE INDEX ix_responses_date     ON survey_responses (response_date);

CREATE TABLE reviews (
    review_id          VARCHAR(16)  PRIMARY KEY CHECK (review_id ~ '^REV-[0-9]{4}-[0-9]{6}$'),
    customer_id        VARCHAR(10)  NOT NULL REFERENCES customers (customer_id),
    product_id         VARCHAR(10)  NOT NULL REFERENCES products (product_id),
    channel_id         VARCHAR(10)  NOT NULL REFERENCES channels (channel_id),
    rating             SMALLINT     NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_title       VARCHAR(160) NOT NULL,
    review_text        TEXT         NOT NULL CHECK (length(btrim(review_text)) > 0),
    review_date        TIMESTAMPTZ  NOT NULL,
    language           VARCHAR(2)   NOT NULL CHECK (language IN ('en','hi','te')),
    verified_purchase  BOOLEAN      NOT NULL,
    helpful_votes      INTEGER      NOT NULL DEFAULT 0 CHECK (helpful_votes >= 0),
    created_at         TIMESTAMPTZ  NOT NULL
);
CREATE INDEX ix_reviews_customer ON reviews (customer_id);
CREATE INDEX ix_reviews_product  ON reviews (product_id);
CREATE INDEX ix_reviews_date     ON reviews (review_date);

CREATE TABLE support_tickets (
    ticket_id       VARCHAR(16)  PRIMARY KEY CHECK (ticket_id ~ '^TKT-[0-9]{4}-[0-9]{6}$'),
    customer_id     VARCHAR(10)  NOT NULL REFERENCES customers (customer_id),
    product_id      VARCHAR(10)  NOT NULL REFERENCES products (product_id),
    channel_id      VARCHAR(10)  NOT NULL REFERENCES channels (channel_id),
    category_theme_id VARCHAR(12) NOT NULL REFERENCES themes (theme_id),
    subject         VARCHAR(200) NOT NULL,
    priority        VARCHAR(8)   NOT NULL CHECK (priority IN ('LOW','MEDIUM','HIGH','URGENT')),
    status          VARCHAR(16)  NOT NULL
                    CHECK (status IN ('OPEN','IN_PROGRESS','PENDING_CUSTOMER','RESOLVED','CLOSED')),
    opened_at       TIMESTAMPTZ  NOT NULL,
    resolved_at     TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    message_count   INTEGER      NOT NULL CHECK (message_count >= 0),  -- derived; reconciled by validation
    created_at      TIMESTAMPTZ  NOT NULL,
    updated_at      TIMESTAMPTZ  NOT NULL,
    CHECK (resolved_at IS NULL OR resolved_at >= opened_at),
    CHECK (closed_at IS NULL OR (resolved_at IS NOT NULL AND closed_at >= resolved_at))
);
CREATE INDEX ix_tickets_customer ON support_tickets (customer_id);
CREATE INDEX ix_tickets_product  ON support_tickets (product_id);
CREATE INDEX ix_tickets_status   ON support_tickets (status);

CREATE TABLE support_messages (
    message_id    VARCHAR(25)  PRIMARY KEY CHECK (message_id ~ '^MSG-TKT-[0-9]{4}-[0-9]{6}-[0-9]{3}$'),
    ticket_id     VARCHAR(16)  NOT NULL REFERENCES support_tickets (ticket_id),
    message_seq   SMALLINT     NOT NULL CHECK (message_seq > 0),
    sender_type   VARCHAR(8)   NOT NULL CHECK (sender_type IN ('CUSTOMER','AGENT')),
    message_text  TEXT         NOT NULL CHECK (length(btrim(message_text)) > 0),
    sent_at       TIMESTAMPTZ  NOT NULL,
    language      VARCHAR(2)   NOT NULL CHECK (language IN ('en','hi','te')),
    created_at    TIMESTAMPTZ  NOT NULL,
    UNIQUE (ticket_id, message_seq),
    CHECK (message_id LIKE 'MSG-' || ticket_id || '-%')
);
CREATE INDEX ix_messages_ticket ON support_messages (ticket_id);

-- ---------------------------------------------------------------------------
-- Unified feedback
-- ---------------------------------------------------------------------------
CREATE TABLE feedback_items (
    feedback_id         VARCHAR(30)  PRIMARY KEY,
    source_type         VARCHAR(16)  NOT NULL CHECK (source_type IN ('SURVEY_RESPONSE','REVIEW','SUPPORT_MESSAGE')),
    source_record_id    VARCHAR(25)  NOT NULL UNIQUE,
    survey_response_id  VARCHAR(25)  UNIQUE REFERENCES survey_responses (response_id),
    review_id           VARCHAR(16)  UNIQUE REFERENCES reviews (review_id),
    support_message_id  VARCHAR(25)  UNIQUE REFERENCES support_messages (message_id),
    customer_id         VARCHAR(10)  NOT NULL REFERENCES customers (customer_id),
    product_id          VARCHAR(10)  NOT NULL REFERENCES products (product_id),
    channel_id          VARCHAR(10)  NOT NULL REFERENCES channels (channel_id),
    feedback_date       TIMESTAMPTZ  NOT NULL,
    rating              SMALLINT     CHECK (rating BETWEEN 1 AND 5),
    feedback_text       TEXT         NOT NULL,
    redacted_text       TEXT,
    language            VARCHAR(2)   NOT NULL CHECK (language IN ('en','hi','te')),
    text_hash           CHAR(64)     NOT NULL,
    dataset_split       VARCHAR(10)  NOT NULL CHECK (dataset_split IN ('TRAIN','VALIDATION','TEST','UNASSIGNED')),  -- UNASSIGNED = live feedback via API
    processing_status   VARCHAR(12)  NOT NULL
                        CHECK (processing_status IN ('INGESTED','VALIDATED','PROCESSED','DUPLICATE','QUARANTINED')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (feedback_id = 'FB-' || source_record_id),
    -- exactly the source FK matching source_type is populated, and it equals source_record_id
    CHECK (
        (source_type = 'SURVEY_RESPONSE' AND survey_response_id = source_record_id
             AND review_id IS NULL AND support_message_id IS NULL)
     OR (source_type = 'REVIEW' AND review_id = source_record_id
             AND survey_response_id IS NULL AND support_message_id IS NULL)
     OR (source_type = 'SUPPORT_MESSAGE' AND support_message_id = source_record_id
             AND survey_response_id IS NULL AND review_id IS NULL)
    )
);
CREATE INDEX ix_feedback_date     ON feedback_items (feedback_date);
CREATE INDEX ix_feedback_product  ON feedback_items (product_id);
CREATE INDEX ix_feedback_channel  ON feedback_items (channel_id);
CREATE INDEX ix_feedback_customer ON feedback_items (customer_id);
CREATE INDEX ix_feedback_source   ON feedback_items (source_type);
CREATE INDEX ix_feedback_status   ON feedback_items (processing_status);
CREATE INDEX ix_feedback_hash     ON feedback_items (text_hash);
CREATE INDEX ix_feedback_text_trgm ON feedback_items USING gin (feedback_text gin_trgm_ops);

-- Labelled training corpus (train/validation splits only). Raw theme labels are
-- kept as text so that out-of-taxonomy labels can be detected, not lost.
CREATE TABLE feedback_annotations (
    annotation_id     VARCHAR(16)  PRIMARY KEY CHECK (annotation_id ~ '^ANN-[0-9]{4}-[0-9]{6}$'),
    feedback_id       VARCHAR(30)  NOT NULL REFERENCES feedback_items (feedback_id),
    sentiment_label   VARCHAR(10)  NOT NULL REFERENCES sentiment_labels (label_code),
    theme_labels      TEXT[]       NOT NULL DEFAULT '{}',
    annotator_id      VARCHAR(20)  NOT NULL,
    annotation_note   TEXT,
    annotated_at      TIMESTAMPTZ  NOT NULL,
    declared_split    VARCHAR(10)  NOT NULL CHECK (declared_split IN ('TRAIN','VALIDATION')),
    training_status   VARCHAR(10)  NOT NULL DEFAULT 'PENDING' CHECK (training_status IN ('PENDING','ELIGIBLE','EXCLUDED')),
    exclusion_reason  TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK ((training_status = 'EXCLUDED') = (exclusion_reason IS NOT NULL))
);
CREATE INDEX ix_annotations_feedback ON feedback_annotations (feedback_id);

-- ---------------------------------------------------------------------------
-- ML runs and append-only prediction history
-- ---------------------------------------------------------------------------
CREATE SEQUENCE pipeline_run_seq START 1;
CREATE TABLE pipeline_runs (
    run_id                   VARCHAR(10)  PRIMARY KEY CHECK (run_id ~ '^RUN-[0-9]{6}$'),
    run_type                 VARCHAR(12)  NOT NULL CHECK (run_type IN ('FULL','INGESTION','VALIDATION','TRAINING','INFERENCE','ACTIONS')),
    status                   VARCHAR(10)  NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    triggered_by             VARCHAR(60)  NOT NULL,
    parameters               JSONB        NOT NULL DEFAULT '{}',
    summary                  JSONB        NOT NULL DEFAULT '{}',
    sentiment_model_version  VARCHAR(80),
    theme_model_version      VARCHAR(80),
    error_message            TEXT,
    started_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at              TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE sentiment_predictions (
    prediction_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feedback_id            VARCHAR(30)   NOT NULL REFERENCES feedback_items (feedback_id),
    run_id                 VARCHAR(10)   NOT NULL REFERENCES pipeline_runs (run_id),
    sentiment_label        VARCHAR(10)   NOT NULL REFERENCES sentiment_labels (label_code),
    confidence             NUMERIC(6,5)  NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    prob_positive          NUMERIC(6,5)  NOT NULL CHECK (prob_positive BETWEEN 0 AND 1),
    prob_negative          NUMERIC(6,5)  NOT NULL CHECK (prob_negative BETWEEN 0 AND 1),
    prob_neutral           NUMERIC(6,5)  NOT NULL CHECK (prob_neutral BETWEEN 0 AND 1),
    prob_mixed             NUMERIC(6,5)  NOT NULL CHECK (prob_mixed BETWEEN 0 AND 1),
    model_name             VARCHAR(60)   NOT NULL,
    model_version          VARCHAR(80)   NOT NULL,
    uncertainty_threshold  NUMERIC(4,3)  NOT NULL,
    needs_human_review     BOOLEAN       NOT NULL,
    review_reason          VARCHAR(120),
    detected_language      VARCHAR(2)    NOT NULL,
    prediction_method      VARCHAR(40)   NOT NULL,
    predicted_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (feedback_id, run_id)
);
CREATE INDEX ix_sentpred_feedback ON sentiment_predictions (feedback_id, run_id DESC);
CREATE INDEX ix_sentpred_run ON sentiment_predictions (run_id);

CREATE TABLE feedback_themes (
    feedback_theme_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feedback_id        VARCHAR(30)   NOT NULL REFERENCES feedback_items (feedback_id),
    theme_id           VARCHAR(12)   NOT NULL REFERENCES themes (theme_id),
    run_id             VARCHAR(10)   NOT NULL REFERENCES pipeline_runs (run_id),
    confidence         NUMERIC(6,5)  NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    model_version      VARCHAR(80)   NOT NULL,
    assignment_source  VARCHAR(10)   NOT NULL CHECK (assignment_source IN ('MODEL','MANUAL')),
    assigned_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (feedback_id, theme_id, run_id)
);
CREATE INDEX ix_fbtheme_feedback ON feedback_themes (feedback_id, run_id);
CREATE INDEX ix_fbtheme_theme ON feedback_themes (theme_id, run_id);

-- ---------------------------------------------------------------------------
-- Business actions (current row + append-only status events + evidence bridge)
-- ---------------------------------------------------------------------------
CREATE SEQUENCE action_item_seq START 1;
CREATE TABLE action_items (
    action_id           VARCHAR(16)   PRIMARY KEY CHECK (action_id ~ '^ACT-[0-9]{4}-[0-9]{6}$'),
    title               VARCHAR(200)  NOT NULL,
    description         TEXT          NOT NULL,
    product_id          VARCHAR(10)   REFERENCES products (product_id),   -- NULL = applies to all products
    theme_id            VARCHAR(12)   NOT NULL REFERENCES themes (theme_id),
    rule_code           VARCHAR(40)   NOT NULL,
    priority            VARCHAR(8)    NOT NULL CHECK (priority IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    evidence_count      INTEGER       NOT NULL CHECK (evidence_count >= 0),  -- derived; reconciled
    negative_count      INTEGER       NOT NULL DEFAULT 0 CHECK (negative_count >= 0),
    total_count         INTEGER       NOT NULL DEFAULT 0 CHECK (total_count >= negative_count),
    negative_share      NUMERIC(6,5)  CHECK (negative_share BETWEEN 0 AND 1),
    metrics             JSONB         NOT NULL DEFAULT '{}',
    recommended_action  TEXT          NOT NULL,
    owner               VARCHAR(80)   NOT NULL,
    status              VARCHAR(12)   NOT NULL
                        CHECK (status IN ('OPEN','IN_PROGRESS','BLOCKED','RESOLVED','CLOSED','DISMISSED')),
    origin              VARCHAR(8)    NOT NULL CHECK (origin IN ('RULE','MANUAL')),
    duplicate_reason    TEXT,          -- documented reason to allow a 2nd active action for the same issue
    run_id              VARCHAR(10)   REFERENCES pipeline_runs (run_id),
    window_start        DATE,
    window_end          DATE,
    due_date            DATE          NOT NULL,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CHECK (due_date >= created_at::date),
    CHECK (window_end IS NULL OR window_start IS NULL OR window_end >= window_start)
);
-- No duplicate *active* action for the same issue (theme) and product ('ALL' = cross-product).
CREATE UNIQUE INDEX ux_action_active_issue
    ON action_items (COALESCE(product_id, 'ALL'), theme_id)
    WHERE status IN ('OPEN','IN_PROGRESS','BLOCKED') AND duplicate_reason IS NULL;
CREATE INDEX ix_actions_status ON action_items (status, priority);

CREATE TABLE action_evidence (
    action_id    VARCHAR(16)  NOT NULL REFERENCES action_items (action_id),
    feedback_id  VARCHAR(30)  NOT NULL REFERENCES feedback_items (feedback_id),
    run_id       VARCHAR(10)  REFERENCES pipeline_runs (run_id),
    linked_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (action_id, feedback_id)
);
CREATE INDEX ix_evidence_feedback ON action_evidence (feedback_id);

CREATE TABLE action_status_events (
    event_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_id    VARCHAR(16)  NOT NULL REFERENCES action_items (action_id),
    from_status  VARCHAR(12),
    to_status    VARCHAR(12)  NOT NULL
                 CHECK (to_status IN ('OPEN','IN_PROGRESS','BLOCKED','RESOLVED','CLOSED','DISMISSED')),
    changed_by   VARCHAR(80)  NOT NULL,
    note         TEXT,
    changed_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (from_status IS DISTINCT FROM to_status)
);
CREATE INDEX ix_action_events_action ON action_status_events (action_id, changed_at);

-- ---------------------------------------------------------------------------
-- Data quality and generic state-change history
-- ---------------------------------------------------------------------------
CREATE SEQUENCE dq_issue_seq START 1;
CREATE TABLE data_quality_issues (
    issue_id           VARCHAR(10)  PRIMARY KEY CHECK (issue_id ~ '^DQ-[0-9]{6}$'),
    run_id             VARCHAR(10)  REFERENCES pipeline_runs (run_id),
    rule_code          VARCHAR(40)  NOT NULL,
    severity           VARCHAR(8)   NOT NULL CHECK (severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
    entity_type        VARCHAR(30)  NOT NULL,
    record_id          VARCHAR(40)  NOT NULL,
    field_name         VARCHAR(60),
    message            TEXT         NOT NULL,
    details            JSONB        NOT NULL DEFAULT '{}',
    resolution_action  VARCHAR(30)  NOT NULL
                       CHECK (resolution_action IN ('FLAGGED','QUARANTINED','REJECTED','EXCLUDED_FROM_TRAINING','NONE')),
    status             VARCHAR(12)  NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
    detected_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (rule_code, entity_type, record_id)
);
CREATE INDEX ix_dq_rule ON data_quality_issues (rule_code, severity);
CREATE INDEX ix_dq_record ON data_quality_issues (record_id);

CREATE TABLE state_change_events (
    event_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type  VARCHAR(30)  NOT NULL
                 CHECK (entity_type IN ('SUPPORT_TICKET','FEEDBACK_ITEM','FEEDBACK_ANNOTATION','SURVEY','PRODUCT','CUSTOMER')),
    entity_id    VARCHAR(40)  NOT NULL,
    from_state   VARCHAR(20),
    to_state     VARCHAR(20)  NOT NULL,
    changed_by   VARCHAR(80)  NOT NULL,
    reason       TEXT,
    run_id       VARCHAR(10)  REFERENCES pipeline_runs (run_id),
    changed_at   TIMESTAMPTZ  NOT NULL,
    CHECK (from_state IS DISTINCT FROM to_state)
);
CREATE INDEX ix_sce_entity ON state_change_events (entity_type, entity_id, changed_at);

-- ---------------------------------------------------------------------------
-- Current-state views (latest prediction per feedback item)
-- ---------------------------------------------------------------------------
-- Written as an anti-join ("no newer prediction from a non-failed run") rather than DISTINCT ON:
-- PostgreSQL estimates the anti-join well, while DISTINCT ON output estimates led to nested loops
-- that re-evaluated the view per row (minutes instead of < 1 s at 111k items).
CREATE VIEW v_current_sentiment AS
SELECT sp.*
FROM sentiment_predictions sp
JOIN pipeline_runs pr ON pr.run_id = sp.run_id AND pr.status <> 'FAILED'  -- predictions of failed runs are ignored
WHERE NOT EXISTS (
    SELECT 1 FROM sentiment_predictions newer
    JOIN pipeline_runs npr ON npr.run_id = newer.run_id AND npr.status <> 'FAILED'
    WHERE newer.feedback_id = sp.feedback_id AND newer.run_id > sp.run_id);

CREATE VIEW v_current_themes AS
SELECT ft.*
FROM feedback_themes ft
JOIN v_current_sentiment cs ON cs.feedback_id = ft.feedback_id AND cs.run_id = ft.run_id;

-- Analysable feedback = not duplicate / quarantined, joined with current predictions.
CREATE VIEW v_feedback_analytics AS
SELECT fi.feedback_id, fi.source_type, fi.source_record_id, fi.customer_id, fi.product_id,
       fi.channel_id, fi.feedback_date, fi.rating, fi.language, fi.processing_status,
       cs.sentiment_label, cs.confidence, cs.needs_human_review, cs.run_id
FROM feedback_items fi
JOIN v_current_sentiment cs ON cs.feedback_id = fi.feedback_id
WHERE fi.processing_status NOT IN ('DUPLICATE','QUARANTINED');
