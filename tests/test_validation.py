"""Validation rules: file-level rejection, DB-level detection, resolutions, PII handling."""
import copy

import pytest
from sqlalchemy import text

from app.services import validation_service as vs
from app.services.evaluation_service import compute_metrics
from app.services.ingestion_service import load_dataset_files
from app.utils.text_preprocessing import find_pii, redact_pii
from tests.conftest import EVAL_TOKEN


@pytest.fixture(scope="module")
def dataset(starter_data, tmp_root):
    return load_dataset_files("starter", tmp_root / "data")


def _validate(ds, mutate):
    tables, ann = copy.deepcopy(ds.tables), copy.deepcopy(ds.annotations)
    mutate(tables, ann)
    return vs.validate_files(tables, ann, ds.splits, ds.master_themes, ds.ticket_events)


def test_clean_baseline_has_no_rejections(dataset):
    rep = vs.validate_files(dataset.tables, dataset.annotations, dataset.splits, dataset.master_themes,
                            dataset.ticket_events)
    assert not any(rep.rejected.values())


def test_duplicate_primary_key_rejected(dataset):
    rep = _validate(dataset, lambda t, a: t["reviews"].append(dict(t["reviews"][0])))
    assert any(i.rule_code == "PK_UNIQUENESS" for i in rep.issues)


def test_missing_parent_rejected_and_cascades(dataset):
    def mutate(t, a):
        t["support_tickets"][0]["customer_id"] = "CUS-999999"
    rep = _validate(dataset, mutate)
    tid = dataset.tables["support_tickets"][0]["ticket_id"]
    assert tid in rep.rejected["support_tickets"]
    assert any(m["ticket_id"] == tid for m in dataset.tables["support_messages"])
    assert all(m["message_id"] in rep.rejected["support_messages"]
               for m in dataset.tables["support_messages"] if m["ticket_id"] == tid)


@pytest.mark.parametrize("field,value,rule", [
    ("language", "fr", "ENUM_VALUE"), ("rating", "9", "RATING_BOUNDS"), ("review_date", "2026-13-40T00:00:00", "INVALID_DATE"),
    ("review_text", None, "REQUIRED_FIELD"), ("review_id", "REV-1", "ID_FORMAT"),
])
def test_field_rules(dataset, field, value, rule):
    def mutate(t, a):
        t["reviews"][5][field] = value
    rep = _validate(dataset, mutate)
    assert any(i.rule_code == rule for i in rep.issues)


def test_date_order_rule(dataset):
    def mutate(t, a):
        s = t["surveys"][0]
        s["end_date"] = "2025-01-01"
    rep = _validate(dataset, mutate)
    assert any(i.rule_code == "DATE_ORDER" and i.entity_type == "SURVEY" for i in rep.issues)


def test_all_injected_anomalies_detected_and_none_unexplained(engine):
    q = compute_metrics(engine, EVAL_TOKEN, "starter", write_report=False)["data_quality"]
    assert q["detection_recall"] == 1.0
    assert q["unexplained_issues"] == 0
    for issue_type in ("LABEL_CONTRADICTION", "DUPLICATE_RESPONSE", "PRIVACY_LEAK", "INVALID_THEME", "TARGET_LEAKAGE"):
        assert q["injected_anomaly_detection"][issue_type]["recall"] == 1.0


def test_resolutions_applied(engine):
    with engine.connect() as conn:
        dup = conn.execute(text("""SELECT count(*) FROM data_quality_issues d JOIN feedback_items fi
                                   ON fi.source_record_id = d.record_id
                                   WHERE d.rule_code = 'DUPLICATE_RESPONSE' AND fi.processing_status <> 'DUPLICATE'""")).scalar()
        assert dup == 0
        rows = conn.execute(text("""SELECT fi.feedback_text, fi.redacted_text, fi.processing_status
                                    FROM data_quality_issues d JOIN feedback_items fi ON fi.feedback_id = d.record_id
                                    WHERE d.rule_code = 'PRIVACY_LEAK'""")).all()
        assert rows and all(r.processing_status == "QUARANTINED" for r in rows)
        assert all(not find_pii(r.redacted_text) for r in rows)
        assert all(find_pii(r.feedback_text) for r in rows)  # original preserved
        excluded = conn.execute(text("""SELECT count(*) FROM data_quality_issues d JOIN feedback_annotations a
                                        ON a.annotation_id = d.record_id
                                        WHERE d.rule_code IN ('LABEL_CONTRADICTION','TARGET_LEAKAGE')
                                          AND a.training_status <> 'EXCLUDED'""")).scalar()
        assert excluded == 0
        quarantined_annot = conn.execute(text("""SELECT count(*) FROM feedback_annotations a JOIN feedback_items fi
                                                 USING (feedback_id) WHERE fi.processing_status = 'QUARANTINED'
                                                 AND a.training_status = 'ELIGIBLE'""")).scalar()
        assert quarantined_annot == 0


def test_lifecycle_and_reconciliation_rules_detect_tampering(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        tid = conn.execute(text("SELECT ticket_id FROM support_tickets WHERE status = 'CLOSED' LIMIT 1")).scalar()
        conn.execute(text("UPDATE support_tickets SET message_count = message_count + 5 WHERE ticket_id = :t"), {"t": tid})
        conn.execute(text("UPDATE support_tickets SET status = 'OPEN' WHERE ticket_id = :t"), {"t": tid})
        rec = vs.check_reconciliation(conn)
        life = vs.check_lifecycle(conn)
        trans.rollback()
    assert any(i.record_id == tid for i in rec)
    assert any(i.record_id == tid for i in life)


def test_source_consistency_detects_mismatch(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(text("UPDATE feedback_items SET product_id = 'PRD-000002' WHERE feedback_id = 'FB-REV-2026-000001' "
                          "AND product_id <> 'PRD-000002'"))
        conn.execute(text("UPDATE feedback_items SET product_id = 'PRD-000003' WHERE feedback_id = 'FB-REV-2026-000001' "
                          "AND product_id = 'PRD-000002'"))
        issues = vs.check_source_consistency(conn)
        trans.rollback()
    assert any(i.record_id == "FB-REV-2026-000001" for i in issues)


def test_validation_is_idempotent(engine):
    with engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM data_quality_issues")).scalar()
    vs.run_db_validation(engine, None)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM data_quality_issues")).scalar() == before


def test_summary_is_machine_readable(engine):
    s = vs.build_validation_summary(engine)
    assert set(vs.RULES) == set(s["checks"])
    assert 0 < s["totals"]["validation_pass_rate"] <= 1
    assert s["totals"]["primary_key_violations"] == 0 and s["totals"]["foreign_key_violations"] == 0


@pytest.mark.parametrize("txt,kinds", [
    ("mail me at a.b@example.com", {"EMAIL"}), ("call +91-9876543210 now", {"PHONE"}),
    ("this is Ravi Rao here", {"NAME"}), ("order ORD-12345 arrived late", set()),
])
def test_pii_detection(txt, kinds):
    assert {f.kind for f in find_pii(txt, ["Ravi Rao"])} == kinds
    if kinds:
        assert not find_pii(redact_pii(txt, ["Ravi Rao"]), ["Ravi Rao"])
