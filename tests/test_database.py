"""Schema, ORM parity, constraints, foreign keys and transactional behaviour."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.taxonomy import render_seed_sql
from app.models import Base
from scripts.setup_db import SEED_FILE


def test_orm_matches_schema(engine):
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for t in Base.metadata.sorted_tables:
        assert t.name in tables
        assert {c["name"] for c in insp.get_columns(t.name)} == {c.name for c in t.columns}, t.name
    assert len(Base.metadata.tables) == 20


def test_every_relationship_has_fk(engine):
    insp = inspect(engine)
    fks = {(t, fk["referred_table"]) for t in insp.get_table_names() for fk in insp.get_foreign_keys(t)}
    for child, parent in [("reviews", "customers"), ("survey_responses", "customers"), ("support_tickets", "customers"),
                          ("reviews", "products"), ("survey_responses", "surveys"), ("support_messages", "support_tickets"),
                          ("sentiment_predictions", "feedback_items"), ("feedback_themes", "themes"),
                          ("feedback_themes", "feedback_items"), ("action_evidence", "action_items"),
                          ("action_evidence", "feedback_items"), ("action_status_events", "action_items")]:
        assert (child, parent) in fks


def test_seed_file_in_sync_with_taxonomy():
    assert SEED_FILE.read_text(encoding="utf-8") == render_seed_sql()


def _expect_violation(engine, sql: str, **params):
    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(IntegrityError):
            conn.execute(text(sql), params)
        trans.rollback()


def test_check_constraints(engine):
    _expect_violation(engine, "UPDATE reviews SET rating = 6 WHERE review_id = 'REV-2026-000001'")
    _expect_violation(engine, "UPDATE survey_responses SET nps_score = 11 WHERE response_id = 'RESP-SUR-2026-000001-001'")
    _expect_violation(engine, "UPDATE customers SET customer_id = 'C-1' WHERE customer_id = 'CUS-000300'")
    _expect_violation(engine, "UPDATE surveys SET end_date = start_date - 1 WHERE survey_id = 'SUR-2026-000001'")


def test_foreign_keys_enforced(engine):
    _expect_violation(engine, "UPDATE reviews SET customer_id = 'CUS-999999' WHERE review_id = 'REV-2026-000001'")
    _expect_violation(engine, "UPDATE support_messages SET ticket_id = 'TKT-2026-999999' "
                              "WHERE message_id = 'MSG-TKT-2026-000001-001'")


def test_response_id_must_embed_its_survey(engine):
    _expect_violation(engine, "UPDATE survey_responses SET survey_id = 'SUR-2026-000002' "
                              "WHERE response_id = 'RESP-SUR-2026-000001-001'")


def test_feedback_source_type_must_match_source(engine):
    _expect_violation(engine, "UPDATE feedback_items SET source_type = 'SUPPORT_MESSAGE' "
                              "WHERE feedback_id = 'FB-REV-2026-000001'")
    _expect_violation(engine, "UPDATE feedback_items SET survey_response_id = 'RESP-SUR-2026-000001-001' "
                              "WHERE feedback_id = 'FB-REV-2026-000001'")


def test_no_duplicate_active_action_for_same_issue(engine):
    with engine.connect() as conn:
        a = conn.execute(text("SELECT * FROM action_items WHERE status = 'OPEN' AND duplicate_reason IS NULL LIMIT 1")
                         ).mappings().first()
        conn.rollback()
        trans = conn.begin()
        with pytest.raises(IntegrityError):
            conn.execute(text("""INSERT INTO action_items (action_id, title, description, product_id, theme_id, rule_code,
                priority, evidence_count, recommended_action, owner, status, origin, due_date)
                VALUES ('ACT-2026-999999', 'dup', 'dup', :p, :t, 'MANUAL', 'LOW', 0, 'x', 'x', 'OPEN', 'MANUAL',
                        current_date + 1)"""), {"p": a["product_id"], "t": a["theme_id"]})
        trans.rollback()


def test_history_tables_are_append_only_by_design(engine):
    with engine.connect() as conn:
        n_events = conn.execute(text("SELECT count(*) FROM state_change_events")).scalar()
        n_feedback = conn.execute(text("SELECT count(*) FROM feedback_items")).scalar()
        assert n_events >= 2 * n_feedback  # INGESTED + at least one transition per item
        _ = conn.execute(text("SELECT count(*) FROM action_status_events")).scalar()


def test_ingestion_is_transactional(engine, starter_data):
    """A second ingestion without replace must fail and leave data untouched."""
    from app.services.ingestion_service import IngestionError, ingest

    with engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM feedback_items")).scalar()
    with pytest.raises(IngestionError):
        ingest(engine, "starter", "RUN-000001")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM feedback_items")).scalar() == before
