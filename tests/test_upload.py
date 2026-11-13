"""Bulk CSV upload: per-row validation, privacy, duplicates, scoring, file-level errors."""
from __future__ import annotations

import csv
import io

from sqlalchemy import text


def _csv(rows: list[dict], columns: list[str] | None = None) -> bytes:
    columns = columns or sorted({k for r in rows for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _post(client, data: bytes, name: str = "test.csv"):
    return client.post("/api/feedback/upload", files={"file": (name, data, "text/csv")})


def _product(engine) -> tuple[str, str]:
    with engine.connect() as c:
        return tuple(c.execute(text("SELECT product_id, product_name FROM products WHERE lifecycle_status = 'ACTIVE' "
                                    "ORDER BY product_id LIMIT 1")).one())


def test_template_downloads_and_round_trips(client):
    r = client.get("/api/feedback/upload/template")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 3 and all(row["text"] and row["product_name"] for row in rows)
    up = _post(client, r.content, "template.csv")
    assert up.status_code == 200
    body = up.json()
    assert body["rows_total"] == 3
    assert body["created"] + body["duplicates"] == 3  # duplicates only if another test uploaded it first


def test_mixed_upload_reports_every_row(client, engine):
    pid, name = _product(engine)
    rows = [
        {"text": "Upload test: the battery died within hours, very disappointed.", "product_name": name, "rating": "1"},
        {"text": "Upload test: wonderful product, works perfectly and arrived early.", "product_id": pid,
         "rating": "5", "feedback_date": "2026-10-20"},
        {"text": "Upload test: contact me at someone.real@example.com about the refund", "product_id": pid, "rating": "2"},
        {"text": "Upload test: no rating given", "product_id": pid, "rating": ""},
        {"text": "Upload test: unknown product", "product_name": "No Such Product", "rating": "3"},
        {"text": "Upload test: dated in the future", "product_id": pid, "rating": "3", "feedback_date": "2099-01-01"},
        {"text": "Upload test: bad date", "product_id": pid, "rating": "3", "feedback_date": "20/10/2026"},
        {"text": "Upload test: the battery died within hours, very disappointed.", "product_name": name, "rating": "1"},
    ]
    r = _post(client, _csv(rows, ["text", "product_id", "product_name", "rating", "feedback_date"]))
    assert r.status_code == 200, r.text
    body = r.json()
    by_row = {x["row"]: x for x in body["results"]}
    assert [by_row[i]["status"] for i in range(2, 10)] == [
        "CREATED", "CREATED", "QUARANTINED", "REJECTED", "REJECTED", "REJECTED", "REJECTED", "DUPLICATE"]
    assert (body["created"], body["quarantined"], body["rejected"], body["duplicates"]) == (2, 1, 4, 1)
    # scored with the active models
    for i in (2, 3):
        assert by_row[i]["sentiment"] in {"POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"}
        assert 0 < by_row[i]["confidence"] <= 1
        assert by_row[i]["feedback_id"].startswith("FB-REV-")
    assert by_row[2]["sentiment"] == "NEGATIVE"
    assert by_row[3]["sentiment"] == "POSITIVE"
    # privacy: quarantined and redacted, never echoed back
    assert "someone.real@example.com" not in by_row[4]["text"] and "[EMAIL]" in by_row[4]["text"]
    assert "rating is required" in by_row[5]["error"]
    assert "Unknown product_name" in by_row[6]["error"]
    assert "future" in by_row[7]["error"]
    assert "ISO date" in by_row[8]["error"]

    # stored, dated as given, attributed to the guest customer, and all under one run
    with engine.connect() as c:
        got = c.execute(text("""SELECT customer_id, (feedback_date AT TIME ZONE 'Asia/Kolkata')::date::text
                                FROM feedback_items WHERE feedback_id = :f"""),
                        {"f": by_row[3]["feedback_id"]}).one()
        assert got == ("CUS-999999", "2026-10-20")
        runs = c.execute(text("""SELECT DISTINCT run_id FROM sentiment_predictions
                                 WHERE feedback_id = ANY(:ids)"""),
                         {"ids": [by_row[2]["feedback_id"], by_row[3]["feedback_id"]]}).scalars().all()
        assert runs == [body["run_id"]]
    # visible through the normal API
    detail = client.get(f"/api/feedback/{by_row[2]['feedback_id']}")
    assert detail.status_code == 200 and detail.json()["sentiment"]["label"] == "NEGATIVE"


def test_file_level_errors(client):
    assert _post(client, b"").status_code == 422
    r = _post(client, b"comment,rating\nhello,3\n")
    assert r.status_code == 422 and "text" in r.json()["detail"]
    assert _post(client, b"text\n").status_code == 422
    assert _post(client, "text\nbonjour\n".encode("utf-16")).status_code == 422
    many = "text,rating\n" + "x,3\n" * 2001
    r = _post(client, many.encode())
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_review_before_launch_rejected(client, engine):
    with engine.connect() as c:
        pid, launch = c.execute(text("SELECT product_id, launch_date FROM products ORDER BY launch_date DESC LIMIT 1")).one()
    r = _post(client, _csv([{"text": "Upload test: reviewed before launch", "product_id": pid, "rating": "3",
                             "feedback_date": "2020-01-01"}]))
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["status"] == "REJECTED" and "before" in res["error"] and str(launch) in res["error"]


def test_feedback_date_only_for_reviews(client):
    r = client.post("/api/feedback", json={"source_type": "SURVEY_RESPONSE", "customer_id": "CUS-000001",
                                           "product_id": "PRD-000001", "channel_id": "CHN-000001", "rating": 3,
                                           "survey_id": "SUR-2026-000001", "text": "fine",
                                           "feedback_date": "2026-10-01"})
    assert r.status_code == 422
