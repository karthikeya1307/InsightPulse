"""REST API: health, feedback, analytics, actions, validation, pipeline, protected evaluation,
and ground-truth isolation."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.conftest import EVAL_TOKEN

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH_KEYS = {"true_sentiment", "true_theme_ids", "is_sarcasm", "is_ambiguous", "anomaly_type",
                     "ground_truth_label", "declared_split", "annotation_note"}


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(_keys(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj)) if obj else set()
    return set()


# ------------------------------------------------------------------ health --
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "connected"
    assert body["schema_tables"] >= 18 and body["feedback_items"] > 0
    assert body["models"]["sentiment"] and body["models"]["themes"]


# ---------------------------------------------------------------- feedback --
def test_feedback_pagination_and_filters(client):
    r = client.get("/api/feedback", params={"page": 1, "page_size": 10})
    assert r.status_code == 200
    page1 = r.json()
    assert len(page1["items"]) == 10 and page1["meta"]["total"] > 10
    assert page1["meta"]["pages"] == -(-page1["meta"]["total"] // 10)
    page2 = client.get("/api/feedback", params={"page": 2, "page_size": 10}).json()
    assert not {i["feedback_id"] for i in page1["items"]} & {i["feedback_id"] for i in page2["items"]}

    neg = client.get("/api/feedback", params={"sentiment": "NEGATIVE", "page_size": 50}).json()
    assert neg["items"] and all(i["sentiment"]["label"] == "NEGATIVE" for i in neg["items"])
    rev = client.get("/api/feedback", params={"source_type": "REVIEW", "page_size": 50}).json()
    assert all(i["source_type"] == "REVIEW" for i in rev["items"])


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 1000}, {"product_id": "BAD"},
                                    {"theme_id": "THEME-000099"}, {"date_from": "2026-05-01", "date_to": "2026-01-01"}])
def test_feedback_invalid_params(client, params):
    assert client.get("/api/feedback", params=params).status_code == 422


def test_feedback_detail_and_404(client):
    fid = client.get("/api/feedback", params={"page_size": 1}).json()["items"][0]["feedback_id"]
    r = client.get(f"/api/feedback/{fid}")
    assert r.status_code == 200
    d = r.json()
    assert d["feedback_id"] == fid and d["prediction_history"] and d["source_details"]
    assert client.get("/api/feedback/FB-REV-2026-999999").status_code == 404


def test_quarantined_feedback_is_redacted(client, engine):
    with engine.connect() as c:
        fid = c.execute(text("SELECT feedback_id FROM feedback_items WHERE processing_status = 'QUARANTINED' LIMIT 1")
                        ).scalar()
    assert fid, "starter data must contain a quarantined (PII) record"
    body = client.get(f"/api/feedback/{fid}").json()
    assert "@example.com" not in body["text"] and any(t in body["text"] for t in ("[EMAIL]", "[PHONE]", "[NAME]"))


def _review_payload(engine, text_: str) -> dict:
    with engine.connect() as c:
        cus = c.execute(text("SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1")).scalar()
        prd = c.execute(text("SELECT product_id FROM products ORDER BY product_id LIMIT 1")).scalar()
        chn = c.execute(text("""SELECT channel_id FROM channels WHERE supports_reviews AND is_active
                                ORDER BY channel_id LIMIT 1""")).scalar()
    return {"source_type": "REVIEW", "customer_id": cus, "product_id": prd, "channel_id": chn,
            "rating": 1, "text": text_}


def test_create_feedback_scores_and_returns_201(client, engine):
    payload = _review_payload(engine, "Pytest review: the battery died after two days and support never replied.")
    r = client.post("/api/feedback", json=payload)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["feedback_id"].startswith("FB-REV-2026-")
    assert d["source_record_id"].startswith("REV-2026-")
    assert d["sentiment"]["label"] == "NEGATIVE"
    assert d["sentiment"]["model_version"]
    assert not _keys(d) & GROUND_TRUTH_KEYS
    # exact duplicate text from the same customer -> 409
    assert client.post("/api/feedback", json=payload).status_code == 409


def test_create_feedback_validation_errors(client, engine):
    p = _review_payload(engine, "fine product overall")
    assert client.post("/api/feedback", json={**p, "rating": None}).status_code == 422          # rating required
    assert client.post("/api/feedback", json={**p, "customer_id": "CUS-999999"}).status_code == 422  # FK
    assert client.post("/api/feedback", json={**p, "text": "   "}).status_code == 422
    assert client.post("/api/feedback", json={**p, "rating": 9}).status_code == 422


def test_create_feedback_with_pii_is_quarantined_and_redacted(client, engine):
    p = _review_payload(engine, "Call me on 9876543210 or mail ravi.k@example.com about the refund please")
    r = client.post("/api/feedback", json=p)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["processing_status"] == "QUARANTINED" and d["sentiment"] is None
    assert "9876543210" not in d["text"] and "ravi.k@example.com" not in d["text"]
    assert any(i["rule_code"] == "PRIVACY_LEAK" for i in d["data_quality_issues"])


# --------------------------------------------------------------- analytics --
def test_analytics_endpoints(client):
    ov = client.get("/api/analytics/overview")
    assert ov.status_code == 200
    o = ov.json()
    s = o["sentiment"]
    assert s["positive"] + s["negative"] + s["neutral"] + s["mixed"] == s["total"] > 0
    assert len(o["top_themes"]) == 5

    sent = client.get("/api/analytics/sentiment").json()
    for dim in ("by_product", "by_channel", "by_source", "by_language"):
        assert sum(r["total"] for r in sent[dim]) == sent["overall"]["total"]

    th = client.get("/api/analytics/themes").json()
    assert th["frequency"] and all(r["theme_id"].startswith("THEME-") for r in th["frequency"])

    tr = client.get("/api/analytics/trends", params={"granularity": "month"})
    assert tr.status_code == 200
    t = tr.json()
    assert t["series"] and "alerts" in t and "month_over_month" in t
    assert client.get("/api/analytics/trends", params={"granularity": "year"}).status_code == 422


def test_analytics_filter_consistency(client, engine):
    with engine.connect() as c:
        prd = c.execute(text("SELECT product_id FROM v_feedback_analytics GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")).scalar()
    all_ = client.get("/api/analytics/sentiment").json()
    one = client.get("/api/analytics/sentiment", params={"product_id": prd}).json()
    expected = next(r for r in all_["by_product"] if r["key"] == prd)
    assert one["overall"]["total"] == expected["total"]


# ----------------------------------------------------------------- actions --
def test_generated_actions_have_required_fields_and_no_active_duplicates(client, engine):
    r = client.get("/api/actions", params={"page_size": 200})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "pipeline must generate at least one action on starter data"
    for a in items:
        for f in ("action_id", "title", "product_id", "theme_id", "priority", "evidence_count", "recommended_action",
                  "owner", "status", "due_date", "created_at"):
            assert f in a
        assert a["action_id"].startswith("ACT-2026-") and a["evidence_count"] > 0
    active = [(a["product_id"], a["theme_id"]) for a in items if a["status"] in ("OPEN", "IN_PROGRESS", "BLOCKED")]
    assert len(active) == len(set(active))


def test_action_generation_is_idempotent(engine):
    from app.services.action_service import generate_actions

    generate_actions(engine)  # bring actions up to date with any feedback submitted by earlier tests
    with engine.connect() as c:
        before = c.execute(text("SELECT count(*) FROM action_items")).scalar()
    res = generate_actions(engine)
    with engine.connect() as c:
        after = c.execute(text("SELECT count(*) FROM action_items")).scalar()
    assert after == before, res


def test_action_detail_evidence_traceable(client):
    a = client.get("/api/actions", params={"page_size": 1}).json()["items"][0]
    d = client.get(f"/api/actions/{a['action_id']}").json()
    assert d["evidence_total"] == d["evidence_count"]
    assert d["evidence"] and all(e["feedback_id"].startswith("FB-") for e in d["evidence"])
    assert d["status_history"][0]["to_status"] == "OPEN"
    assert client.get("/api/actions/ACT-2026-999999").status_code == 404


def test_action_create_duplicate_and_status_lifecycle(client):
    a = client.get("/api/actions", params={"active_only": True, "page_size": 1}).json()["items"][0]
    fids = [e["feedback_id"] for e in client.get(f"/api/actions/{a['action_id']}").json()["evidence"][:3]]
    body = {"title": "Pytest manual action", "description": "Created by the test suite.", "theme_id": a["theme_id"],
            "product_id": a["product_id"], "priority": "LOW", "owner": "QA Team",
            "recommended_action": "Verify the duplicate guard.", "feedback_ids": fids, "created_by": "pytest"}
    assert client.post("/api/actions", json=body).status_code == 409  # duplicate active issue
    assert client.post("/api/actions", json={**body, "theme_id": "THEME-000099"}).status_code == 422
    r = client.post("/api/actions", json={**body, "duplicate_reason": "Separate workstream for QA"})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["status"] == "OPEN" and new["evidence_count"] == len(fids)

    aid = new["action_id"]
    bad = client.patch(f"/api/actions/{aid}/status", json={"status": "CLOSED", "changed_by": "pytest"})
    assert bad.status_code == 409  # OPEN -> CLOSED not allowed
    for st in ("IN_PROGRESS", "RESOLVED", "CLOSED"):
        r = client.patch(f"/api/actions/{aid}/status", json={"status": st, "changed_by": "pytest", "note": st})
        assert r.status_code == 200 and r.json()["status"] == st
    hist = [h["to_status"] for h in client.get(f"/api/actions/{aid}").json()["status_history"]]
    assert hist == ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
    assert client.patch(f"/api/actions/{aid}/status",
                        json={"status": "OPEN", "changed_by": "pytest"}).status_code == 409  # terminal
    assert client.patch("/api/actions/ACT-2026-999999/status",
                        json={"status": "OPEN", "changed_by": "pytest"}).status_code == 404


# -------------------------------------------------------------- validation --
def test_validation_issues_and_summary(client):
    r = client.get("/api/validation/issues", params={"page_size": 5})
    assert r.status_code == 200 and r.json()["meta"]["total"] > 0
    pii = client.get("/api/validation/issues", params={"rule_code": "PRIVACY_LEAK"}).json()
    assert pii["items"] and all(i["rule_code"] == "PRIVACY_LEAK" for i in pii["items"])
    s = client.get("/api/validation/summary")
    assert s.status_code == 200
    assert client.get("/api/validation/rules").status_code == 200


# ---------------------------------------------------------------- pipeline --
def test_pipeline_run_sync_and_status(client):
    r = client.post("/api/pipeline/run", json={"steps": ["validate"], "wait": True})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "SUCCEEDED"
    s = client.get(f"/api/pipeline/runs/{run['run_id']}")
    assert s.status_code == 200 and s.json()["status"] == "SUCCEEDED"
    assert client.post("/api/pipeline/run", json={"steps": ["bogus"]}).status_code == 422


# ------------------------------------------------------ evaluation (auth) --
def test_evaluation_requires_token(client):
    assert client.get("/api/evaluation/metrics").status_code == 401
    assert client.get("/api/evaluation/metrics", headers={"X-Evaluation-Token": "wrong"}).status_code == 403


def test_evaluation_metrics_with_token(client):
    r = client.get("/api/evaluation/metrics", headers={"X-Evaluation-Token": EVAL_TOKEN})
    assert r.status_code == 200, r.text
    m = r.json()
    assert 0 <= m["sentiment"]["macro_f1"] <= 1
    assert 0 <= m["themes"]["micro_f1"] <= 1
    dq = m["data_quality"]
    assert dq["injected_detected"] == dq["injected_total"] > 0


def test_open_ground_truth_requires_token():
    from app.services.evaluation_service import EvaluationAccessError, open_ground_truth

    with pytest.raises(EvaluationAccessError):
        open_ground_truth("not-the-token")
    with pytest.raises(EvaluationAccessError):
        open_ground_truth(None)


# ---------------------------------------------------- ground-truth isolation --
def _imports_and_strings(path: Path) -> tuple[set[str], list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods, strings = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    return mods, strings


def test_ground_truth_only_read_by_evaluation_service():
    """No training/inference/ingestion/API code may import the evaluation module or reference ground-truth files."""
    allowed = {ROOT / "app/services/evaluation_service.py", ROOT / "app/api/routes/evaluation.py",
               ROOT / "app/core/config.py", ROOT / "ml/common.py"}
    offenders = []
    for path in [*ROOT.glob("app/**/*.py"), *ROOT.glob("ml/**/*.py")]:
        if path in allowed:
            continue
        mods, strings = _imports_and_strings(path)
        if any("evaluation_service" in m for m in mods):
            offenders.append(f"{path.relative_to(ROOT)} imports evaluation_service")
        if any("ground_truth" in s or "evaluation_set" in s for s in strings):
            offenders.append(f"{path.relative_to(ROOT)} references ground-truth files")
    assert not offenders, offenders


def test_inference_payloads_contain_no_ground_truth(client):
    fid = client.get("/api/feedback", params={"page_size": 1}).json()["items"][0]["feedback_id"]
    for url in ("/api/feedback?page_size=50", f"/api/feedback/{fid}", "/api/analytics/overview",
                "/api/analytics/themes", "/api/actions?page_size=20"):
        assert not _keys(client.get(url).json()) & GROUND_TRUTH_KEYS, url
