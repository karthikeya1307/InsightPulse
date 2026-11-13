# Prompt 1: initial build request

- Tool: Claude (Claude Code cloud session, via a Claude project thread)
- Sent: 2026-11-11 17:12 UTC
- Attachments: `Submission_Guidelines.pdf`, `FAQ.pdf` (YottaFlex AI recruitment drive)
- Content: verbatim. It combines the candidate's detailed build specification with the official
  problem statement copied from the challenge portal (from "My Applications" onwards).

---

# ROLE AND OBJECTIVE

Act as a Senior AI Forward Deployed Engineer, Software Architect, Data Engineer, Machine Learning Engineer, Backend Developer and Technical Lead.

Your task is to design and implement a complete, production-minded MVP for the following 72-hour technical challenge:

**Project Name: InsightPulse – AI-Powered Customer Feedback Intelligence Platform**

The application must combine customer survey responses, product reviews and customer support messages to identify recurring themes, sentiment shifts, service priorities and actionable business recommendations.

This is an individual submission for an Associate AI Forward Deployed Engineer position.

Do not merely provide explanations, pseudocode or isolated code snippets. Build a complete, executable, integrated application with proper project structure, database schema, synthetic data generation, AI/ML pipelines, REST APIs, dashboard, testing and documentation.

# 1. TECHNOLOGY STACK

Use the following technologies:

- Programming language: Python 3.11
- Backend: FastAPI
- Frontend/dashboard: Streamlit
- Database: PostgreSQL 16
- ORM: SQLAlchemy 2.x
- Data processing: Pandas and NumPy
- Machine learning: scikit-learn
- NLP: NLTK and multilingual text processing
- Visualization: Plotly
- Validation: Pydantic and custom validation functions
- Testing: Pytest
- Containerization: Docker and Docker Compose
- Version control: Git

Use clean, modular, maintainable code.

Avoid unnecessary technologies, paid APIs and external dependencies that could prevent the application from running locally.

# 2. BUSINESS REQUIREMENTS

Build a complete end-to-end workflow that:

1. Generates customer, product, channel, survey and survey response master data.
2. Generates dependent reviews and support messages using valid parent IDs.
3. Combines feedback from all three sources.
4. Validates records and relationships.
5. Performs sentiment analysis.
6. Identifies multiple themes in customer feedback.
7. Detects sentiment shifts over time.
8. Identifies recurring customer complaints.
9. Generates prioritized business action items.
10. Tracks data quality issues and exceptions.
11. Provides traceability from recommendations to original feedback.
12. Evaluates model performance against separate human-reviewed ground truth.

# 3. PROJECT STRUCTURE

Create the following modular project structure:

insightpulse/
    app/
        main.py
        api/
            routes/
                health.py
                feedback.py
                analytics.py
                actions.py
                validation.py
        core/
            config.py
            database.py
            logging_config.py
        models/
        schemas/
        services/
            ingestion_service.py
            validation_service.py
            sentiment_service.py
            theme_service.py
            trend_service.py
            action_service.py
            evaluation_service.py
        utils/
            id_generator.py
            text_preprocessing.py

    dashboard/
        Home.py
        pages/
            1_Feedback_Explorer.py
            2_Sentiment_Analysis.py
            3_Theme_Analysis.py
            4_Business_Actions.py
            5_Data_Quality.py
            6_Model_Evaluation.py

    generator/
        generate_data.py
        master_generator.py
        feedback_generator.py
        text_templates.py
        anomaly_injector.py
        config.py

    ml/
        sentiment/
        themes/
        saved_models/

    data/
        raw/
        processed/
        master/
        ground_truth/
        exceptions/
        exports/

    database/
        schema.sql
        seed.sql
        migrations/

    tests/
        test_ids.py
        test_database.py
        test_validation.py
        test_sentiment.py
        test_themes.py
        test_reproducibility.py
        test_api.py

    scripts/
        setup_db.py
        run_pipeline.py
        validate_data.py
        export_data.py

    docs/
        architecture.md
        data_dictionary.md
        business_rules.md
        evaluation.md
        source_manifest.md

    requirements.txt
    .env.example
    .gitignore
    docker-compose.yml
    README.md

You may introduce additional files when technically necessary, but preserve separation of concerns.

# 4. DATABASE DESIGN

Use PostgreSQL with properly normalized relational tables.

Required entities:

1. Customer
2. Product
3. Channel
4. Survey
5. SurveyResponse
6. Review
7. SupportTicket
8. SupportMessage
9. Theme
10. SentimentLabel
11. FeedbackItem
12. SentimentPrediction
13. FeedbackTheme
14. ActionItem
15. ActionEvidence
16. ActionStatusEvent
17. DataQualityIssue
18. StateChangeEvent

Use a common FeedbackItem entity to unify survey responses, reviews and support messages.

Define:
- Primary keys for every entity.
- Foreign keys for every relationship.
- Appropriate unique constraints.
- Check constraints.
- Indexes for frequently queried fields.
- Effective dates where relevant.
- Lifecycle states.
- Created and updated timestamps where appropriate.

Use bridge tables for many-to-many relationships.

Required relationships include:

- Customer to Review: 1:N
- Customer to SurveyResponse: 1:N
- Customer to SupportTicket: 1:N
- Product to Review: 1:N
- Survey to SurveyResponse: 1:N
- SupportTicket to SupportMessage: 1:N
- FeedbackItem to SentimentPrediction: 1:N
- FeedbackItem to Theme: N:M
- ActionItem to FeedbackItem: N:M

Important:
- Preserve historical predictions.
- Preserve action status changes as separate events.
- Do not overwrite historical records.
- Ensure feedback source types match their respective source records.
- Ensure child transaction records have valid parent IDs.
- Validate ticket/customer/product/channel consistency.

Create a complete SQL schema and corresponding SQLAlchemy models.

Use transactions for multi-table ingestion.

# 5. ID CONVENTIONS

Implement centralized deterministic ID generation.

Use these conventions:

Customer: CUS-000001
Product: PRD-000001
Channel: CHN-000001
Survey: SUR-2026-000001
SurveyResponse: RESP-SUR-2026-000001-001
Review: REV-2026-000001
SupportTicket: TKT-2026-000001
SupportMessage: MSG-TKT-2026-000001-001
Theme: THEME-000001
ActionItem: ACT-2026-000001

Requirements:
- Zero-padded counters.
- Immutable identifiers.
- No inconsistent foreign key references.
- Deterministic generation.
- Same IDs and data when regenerated with identical configuration and seed.

# 6. SYNTHETIC DATA GENERATION

Implement a parameterized synthetic data generator.

Use fixed seed:

20261022

Support two modes:

STARTER:
- 500 reviews
- 500 survey responses
- 200 support messages

FULL:
- 50,000 reviews
- 50,000 survey responses
- 20,000 support messages

The full interpretation is 50,000 reviews AND 50,000 survey responses.

Generate master data before dependent transaction records.

Generate realistic synthetic:
- Customer profiles
- Product categories
- Channels
- Surveys
- Survey responses
- Reviews
- Support tickets
- Support messages

Include varied feedback text with:
- Positive sentiment
- Negative sentiment
- Neutral sentiment
- Mixed sentiment
- Sarcasm
- Ambiguous expressions
- English, Telugu and Hindi examples
- Different writing styles
- Product-specific terminology
- Support complaints
- Praise and suggestions

Use synthetic templates and controlled variations. Do not use private customer data.

Generate sentiment and theme ground-truth labels from documented rules or controlled generation metadata. Do not independently randomize labels.

Maintain source-specific and theme-specific distributions.

Use realistic distributions and configurable parameters.

Support commands:

python -m generator.generate_data --scale starter --seed 20261022

python -m generator.generate_data --scale full --seed 20261022

Export CSV and JSON files.

# 7. THEME TAXONOMY

Create a predefined theme taxonomy:

THEME-000001: Product Quality
THEME-000002: Pricing
THEME-000003: Delivery
THEME-000004: Customer Support
THEME-000005: User Experience
THEME-000006: Payment
THEME-000007: Product Availability
THEME-000008: Packaging
THEME-000009: Refunds and Returns
THEME-000010: Technical Issues

Support multiple themes per feedback record.

Include a theme taxonomy validation mechanism.

A theme outside the taxonomy must be flagged as an exception, not silently accepted.

# 8. SENTIMENT ANALYSIS

Implement a working sentiment classification pipeline.

Support:

- POSITIVE
- NEGATIVE
- NEUTRAL
- MIXED

MVP model:
- TF-IDF vectorization
- Logistic Regression
- Appropriate text preprocessing
- Train/test split
- Stratified evaluation

Include:
- Prediction confidence
- Model name
- Model version
- Prediction timestamp
- Uncertainty threshold
- Human review flag

Do not assign predictions using the ground-truth dataset.

Keep training, validation and test data separated.

For multilingual inputs, document limitations of the baseline model and implement a sensible multilingual fallback or preprocessing strategy.

Do not pretend that an English-only classifier understands Telugu or Hindi.

# 9. THEME CLASSIFICATION

Implement multilabel theme classification.

Use:
- TF-IDF features
- OneVsRestClassifier
- Logistic Regression

Support multiple themes per feedback record.

Provide:
- Predicted themes
- Confidence scores
- Model version
- Evaluation metrics

Evaluate using micro F1, macro F1, precision and recall.

Use the predefined theme taxonomy as the allowed label space.

# 10. BUSINESS INTELLIGENCE

Implement the following analytics.

A. Sentiment Analytics:
- Total feedback
- Positive percentage
- Negative percentage
- Neutral percentage
- Mixed percentage
- Sentiment distribution by product
- Sentiment distribution by channel

B. Theme Analytics:
- Most frequently occurring themes
- Negative feedback by theme
- Theme distribution by product
- Theme trends over time

C. Sentiment Shift Detection:
- Daily and weekly trends
- Month-over-month comparisons
- Detect unusual increases in negative feedback
- Configurable alert thresholds

D. Business Action Generation:

Generate action items using explainable, deterministic business rules.

Example:

If negative feedback count for a theme exceeds a configurable threshold and its share of feedback is sufficiently high, create a high-priority action item.

Each action item must contain:
- Action ID
- Title
- Description
- Related product
- Related theme
- Priority
- Evidence count
- Supporting feedback IDs
- Recommended action
- Creation timestamp
- Due date
- Owner
- Lifecycle status

Avoid duplicate active action items for the same issue and product unless there is a documented reason.

# 11. REQUIRED EDGE CASES

Intentionally inject labelled anomalies after generating valid baseline records.

Include:

1. Sentiment label contradicts the text without explanation.
2. Duplicate survey response.
3. Customer identity leaked in feedback text.
4. Theme assigned outside the allowed taxonomy.
5. Test labels accidentally included in model input.

Expected handling:

- Detect contradictory labels.
- Flag duplicate responses.
- Quarantine privacy violations.
- Reject or flag invalid themes.
- Detect target leakage.
- Preserve original source records and anomaly history.

Create a separate ground_truth.csv and ground_truth.json.

Ground truth must never be included in model input features.

Use separate evaluation permissions or data access controls.

The anomaly generator must record:
- Issue ID
- Record ID
- Issue type
- Expected detection
- Severity
- Ground-truth label

# 12. DATA VALIDATION

Implement validation for:

- Primary-key uniqueness
- Foreign-key integrity
- Allowed enum values
- Missing required fields
- Invalid dates
- Date ordering
- Duplicate detection
- Rating boundaries
- Lifecycle transitions
- Arithmetic reconciliation
- Source-specific relationship consistency
- Theme taxonomy
- Privacy leaks
- Ground-truth leakage

Generate a machine-readable validation summary.

Distinguish deliberately injected exceptions from unexplained data quality failures.

# 13. FASTAPI BACKEND

Build functional REST APIs.

Required endpoints:

GET /health

GET /api/feedback

GET /api/feedback/{feedback_id}

POST /api/feedback

GET /api/analytics/overview

GET /api/analytics/sentiment

GET /api/analytics/themes

GET /api/analytics/trends

GET /api/actions

POST /api/actions

PATCH /api/actions/{action_id}/status

GET /api/validation/issues

POST /api/pipeline/run

GET /api/evaluation/metrics

Include:
- Request validation
- Response schemas
- Pagination
- Filtering
- Error handling
- Database session management
- Proper HTTP status codes
- OpenAPI documentation

All endpoints must perform real operations rather than returning hardcoded example data.

Protect the evaluation endpoint so ground truth is not exposed through normal inference APIs.

# 14. STREAMLIT DASHBOARD

Build a professional, modern, responsive dashboard.

Design direction:
- Clean enterprise SaaS interface.
- Modern typography.
- Consistent color palette.
- Clear KPI cards.
- Interactive Plotly charts.
- Intuitive navigation.
- Appropriate empty states and error messages.
- Avoid excessive decorative elements.

Required pages:

1. Overview Dashboard
2. Feedback Explorer
3. Sentiment Analysis
4. Theme Analysis
5. Trend Detection
6. Business Action Items
7. Data Quality and Exceptions
8. Model Evaluation

Overview must show:
- Total feedback records
- Sentiment distribution
- Top recurring themes
- Negative sentiment trend
- High-priority action items
- Data quality summary

Feedback Explorer must support:
- Product filters
- Channel filters
- Date filters
- Sentiment filters
- Theme filters
- Search
- Pagination

Business Action page must show supporting evidence and allow status updates.

Data Quality page must expose detected exceptions.

Evaluation page must show actual model metrics, confusion matrices and classification reports.

Connect the dashboard to the FastAPI backend. Do not duplicate business logic inside Streamlit.

# 15. EVALUATION

Create a separate human-reviewed evaluation dataset.

Implement:

Sentiment:
- Accuracy
- Precision
- Recall
- Macro F1
- Confusion matrix

Themes:
- Micro F1
- Macro F1
- Precision
- Recall
- Per-theme classification metrics

Data quality:
- Primary-key violation count
- Foreign-key violation count
- Duplicate count
- Invalid taxonomy count
- Privacy violation count
- Validation pass rate

Reproducibility:
- Row counts
- File checksums
- Configuration
- Seed
- Generator version

Business intelligence:
- Recurring themes detected
- Sentiment shifts detected
- Actions generated
- Evidence traceability

Do not fabricate evaluation results. Calculate them using actual predictions and human-reviewed labels.

# 16. REPRODUCIBILITY AND EXPORTS

Regenerate the dataset twice using the same seed and configuration.

Compare:
- Record counts
- File checksums
- IDs
- Labels
- Relationships

Produce:
- CSV exports
- JSON exports
- PostgreSQL SQL seed data
- Validation summary
- Ground-truth files
- Data dictionary
- Source manifest
- README

Document all synthetic data provenance.

If public datasets are used, record their source URLs, versions, retrieval dates, licenses, attribution and transformations.

Prefer synthetic data to avoid licensing and privacy problems.

# 17. DOCKER AND LOCAL EXECUTION

Create a working Docker Compose configuration for PostgreSQL.

Document local execution commands.

Expected workflow:

1. Create virtual environment.
2. Install requirements.
3. Configure .env.
4. Start PostgreSQL.
5. Initialize schema.
6. Generate starter dataset.
7. Run validation.
8. Run ML pipeline.
9. Start FastAPI.
10. Start Streamlit.

Provide Windows PowerShell commands in the README.

Also document the corresponding Linux/macOS commands.

# 18. TESTING REQUIREMENTS

Write meaningful automated tests for:

- ID generation
- Data generation
- Deterministic reproducibility
- Database constraints
- Foreign-key relationships
- Validation rules
- Duplicate detection
- Privacy detection
- Sentiment predictions
- Theme predictions
- Action generation
- API endpoints
- Pagination
- Ground-truth isolation

All critical functionality must have tests.

# 19. IMPLEMENTATION STRATEGY

Follow this exact development strategy:

PHASE 1: Create the project structure, configuration, database schema and SQLAlchemy models.

PHASE 2: Build a complete starter synthetic data generator.

PHASE 3: Implement database ingestion and data validation.

PHASE 4: Implement sentiment and theme classification.

PHASE 5: Implement trend detection and action generation.

PHASE 6: Build FastAPI endpoints.

PHASE 7: Build Streamlit dashboard.

PHASE 8: Implement evaluation and automated testing.

PHASE 9: Scale to the full required dataset.

PHASE 10: Verify reproducibility and prepare final documentation.

Build a functional MVP early. Do not spend excessive time on advanced models before the complete workflow works.

# 20. IMPORTANT ENGINEERING RULES

- Never generate fake API responses to simulate completed functionality.
- Never hardcode analytics that should be calculated from source records.
- Never use ground-truth labels as model input features.
- Never silently discard invalid records.
- Never silently overwrite historical events.
- Never use inconsistent IDs.
- Never generate dependent records before parent records.
- Never claim a test passed without executing it.
- Never invent model performance metrics.
- Never leave critical modules as TODOs.
- Use proper logging and exception handling.
- Use type hints and docstrings.
- Use database transactions where appropriate.
- Keep secrets out of source control.
- Optimize bulk data generation and database insertion.
- Make all important thresholds configurable.
- Keep the application runnable on a normal laptop with 16 GB RAM.
- Avoid loading the entire full dataset into memory unnecessarily.
- Use batch processing and database pagination.

# 21. FINAL DELIVERABLES

The finished project must include:

1. Complete source code.
2. PostgreSQL schema and SQLAlchemy models.
3. Synthetic data generator.
4. Starter and full datasets.
5. Ground-truth dataset.
6. Data validation engine.
7. Sentiment analysis model.
8. Theme classification model.
9. Trend detection engine.
10. Business action generation engine.
11. FastAPI backend.
12. Streamlit dashboard.
13. Automated test suite.
14. CSV/JSON/SQL exports.
15. Data dictionary.
16. Architecture documentation.
17. Business rules documentation.
18. Evaluation report.
19. Reproducibility report.
20. Complete README with execution instructions.

# 22. HOW YOU SHOULD WORK

If you have access to the project filesystem and terminal, create the files directly in the repository.

Work in small, logically complete milestones.

After each milestone:

1. List the files created.
2. Explain important implementation decisions.
3. Install missing dependencies when necessary.
4. Execute the relevant code.
5. Run tests.
6. Fix errors before proceeding.
7. Report the actual verification results.

Do not dump the entire project into one enormous unverified response.

Start by examining the existing workspace.

If it is empty, initialize the project.

Then implement PHASE 1 completely and verify that PostgreSQL connects, the schema initializes and the FastAPI health endpoint works.

Once Phase 1 is verified, continue with the next phases systematically.

Your priority is a complete, correct, demonstrable and submission-ready AI product within the 72-hour deadline.
My Applications
/
Associate AI Forward Deployed Engineers
/
Code Challenge
Round 2
72 Hours Code Challenge
Associate AI Forward Deployed Engineers · AI Forward Deployed Engineer

Problem statement
Read it carefully. Your build should cover every point below.

Business Context / Problem
Combine survey responses, reviews and support text to identify recurring themes, sentiment shifts and service priorities.

Objective and Scope
Build a working solution for this end-to-end workflow: Combine survey responses, reviews and support text to identify recurring themes, sentiment shifts and service priorities. The solution should expose key decisions and exceptions, preserve traceability, and produce measurable outputs. Medium complexity means multiple related entities, conditional rules, at least one exception path and meaningful evaluation metrics.

Primary Actors / Systems
Customer/prospect, marketing/sales operations, support agent, analyst and system administrator

Business Workflow and Rules
1. Generate/reference master data first: Customer, Product, Channel, Survey, SurveyResponse.
2. Generate dependent transaction/event records using valid parent IDs.
3. Apply business logic: Create product/channel taxonomy; generate synthetic review and ticket text with balanced sentiment/theme labels; link responses to dates/products; include neutral, mixed and multilingual examples; define theme taxonomy; create action items from recurring themes.
4. Persist state changes as explicit records/events where relevant; do not overwrite history.
5. Generate outcomes and ground-truth labels from documented rules, not independent random assignment.

Entities and Relationships
Core entities: Customer, Product, Channel, Survey, SurveyResponse, Review, SupportMessage, Theme, SentimentLabel, ActionItem. Define a primary key for every entity and foreign keys for every relationship. Document cardinality (1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states. Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins. For deliberate anomaly cases, keep an issue_type/ground_truth label rather than silently corrupting unrelated rows.

ID / Nomenclature Conventions
SUR-2026-000001; RESP-<survey-id>-001; REV-2026-000001; MSG-<ticket-id>-001; THEME-000001.

Synthetic Data Generation Instructions (Granular)
Step 1 — Write a data dictionary for: Customer, Product, Channel, Survey, SurveyResponse, Review, SupportMessage, Theme, SentimentLabel, ActionItem. For each field specify type, required/optional, allowed values, units, uniqueness and validation rule.
Step 2 — Create parent/master tables first, then child and event tables. Apply this domain-specific generation logic: Create product/channel taxonomy; generate synthetic review and ticket text with balanced sentiment/theme labels; link responses to dates/products; include neutral, mixed and multilingual examples; define theme taxonomy; create action items from recurring themes.
Step 3 — Apply deterministic IDs and naming: SUR-2026-000001; RESP-<survey-id>-001; REV-2026-000001; MSG-<ticket-id>-001; THEME-000001. Keep counters zero-padded, IDs immutable, and referenced IDs exactly consistent across files.
Step 4 — Generate the baseline volume: Generate 50,000 reviews/responses and 20,000 support messages. Ensure label balance, realistic language variation, sarcasm and ambiguous examples; keep text synthetic or properly licensed. Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full datasets use the same generator.
Step 5 — Generate valid baseline records first, then inject labelled edge cases: Label contradicts text without explanation; duplicate response; customer identity leaked; theme assigned outside taxonomy; test labels included in model input.
Step 6 — Calculate derived values (totals, balances, scores, statuses and dates) from source records rather than generating them independently.
Step 7 — Produce a separate ground_truth.csv/json for known anomalies, expected decisions or labels; never leak it into input features.
Step 8 — Run validation: primary-key uniqueness, foreign-key integrity, allowed enums, date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks.
Step 9 — Export CSV/JSON/SQL seed data plus README, schema/data dictionary, generator command, fixed seed, source manifest and validation summary.
Step 10 — Regenerate twice with the same seed and compare row counts/checksums to demonstrate reproducibility.

Suggested Volume and Coverage
Generate 50,000 reviews/responses and 20,000 support messages. Ensure label balance, realistic language variation, sarcasm and ambiguous examples; keep text synthetic or properly licensed.

Required Edge Cases / Negative Scenarios
Label contradicts text without explanation; duplicate response; customer identity leaked; theme assigned outside taxonomy; test labels included in model input.

Validation Rules / Acceptance Criteria
Evaluation set has human-reviewed labels; sentiment/theme metrics are measurable; privacy-safe synthetic text is used; trends can be traced to source records.

Evaluation Focus for Generated Data
Review: (a) realistic distributions and meaningful variation; (b) consistent IDs and valid joins; (c) dependency/lifecycle consistency across entities; (d) correct derived values and reconciliation; (e) normal, boundary, failure and exception coverage; (f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful provenance.

Public Data Guidance / Lawful Use
External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration. DO THE NECESSARY STEPS
