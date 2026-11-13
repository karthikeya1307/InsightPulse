-- Migration 001: rewrite v_current_sentiment as an anti-join (performance at full scale).
-- Columns are unchanged, so dependent views (v_current_themes, v_feedback_analytics) keep working.
-- Apply: psql "$DATABASE_URL" -f database/migrations/001_current_sentiment_antijoin.sql

-- Written as an anti-join ("no newer prediction from a non-failed run") rather than DISTINCT ON:
-- PostgreSQL estimates the anti-join well, while DISTINCT ON output estimates led to nested loops
-- that re-evaluated the view per row (minutes instead of < 1 s at 111k items).
CREATE OR REPLACE VIEW v_current_sentiment AS
SELECT sp.*
FROM sentiment_predictions sp
JOIN pipeline_runs pr ON pr.run_id = sp.run_id AND pr.status <> 'FAILED'  -- predictions of failed runs are ignored
WHERE NOT EXISTS (
    SELECT 1 FROM sentiment_predictions newer
    JOIN pipeline_runs npr ON npr.run_id = newer.run_id AND npr.status <> 'FAILED'
    WHERE newer.feedback_id = sp.feedback_id AND newer.run_id > sp.run_id);

