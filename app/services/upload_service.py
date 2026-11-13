"""Bulk feedback upload from a CSV file.

Each row goes through exactly the same path as ``POST /api/feedback``: schema validation, relationship
checks, the privacy check, duplicate detection and scoring with the active models. Rows are isolated
with savepoints, so one bad row is reported and skipped without losing the others. All rows of one
upload share a single INFERENCE pipeline run, so the upload is traceable as a unit.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.api import FeedbackCreate
from app.services import feedback_service as fs

MAX_ROWS = 2000
MAX_BYTES = 2_000_000
TEMPLATE_COLUMNS = ["text", "source_type", "product_id", "product_name", "channel_id", "rating", "nps_score",
                    "feedback_date", "review_title", "customer_id", "survey_id", "ticket_id", "language"]
REQUIRED_COLUMNS = {"text"}
GUEST_CUSTOMER_ID = "CUS-999999"


class UploadFileError(ValueError):
    """The file as a whole cannot be processed (HTTP 422)."""


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def ensure_guest_customer(db: Session) -> str:
    """Rows without a customer_id are attributed to one synthetic guest customer (no personal data)."""
    db.execute(text("""INSERT INTO customers (customer_id, first_name, last_name, email, phone, city, state, country,
                       preferred_language, segment, signup_date, lifecycle_status, effective_from, created_at, updated_at)
                       VALUES (:id, 'Upload', 'Guest', 'upload.guest@example.com', '+910000000000', 'Unknown',
                               'Unknown', 'India', 'en', 'CONSUMER', DATE '2020-01-01', 'ACTIVE', DATE '2020-01-01',
                               now(), now())
                       ON CONFLICT (customer_id) DO NOTHING"""), {"id": GUEST_CUSTOMER_ID})
    return GUEST_CUSTOMER_ID


def _lookups(db: Session) -> tuple[dict[str, str], dict[str, str]]:
    products = {name.lower(): pid for pid, name in db.execute(text("SELECT product_id, product_name FROM products"))}
    default_channel = {}
    for src, col in (("REVIEW", "supports_reviews"), ("SURVEY_RESPONSE", "supports_surveys")):
        cid = db.execute(text(f"SELECT channel_id FROM channels WHERE {col} ORDER BY channel_id LIMIT 1")).scalar()
        if cid:
            default_channel[src] = cid
    return products, default_channel


def parse_csv(raw: bytes) -> list[dict[str, str]]:
    if len(raw) > MAX_BYTES:
        raise UploadFileError(f"File is larger than {MAX_BYTES // 1_000_000} MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadFileError("File must be UTF-8 encoded CSV") from exc
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise UploadFileError("File is empty or has no header row")
    headers = {h.strip().lower() for h in reader.fieldnames if h}
    missing = REQUIRED_COLUMNS - headers
    if missing:
        raise UploadFileError(f"Missing required column(s): {', '.join(sorted(missing))}. "
                              f"Allowed columns: {', '.join(TEMPLATE_COLUMNS)}")
    rows = [{(k or "").strip().lower(): (v if isinstance(v, str) else "") for k, v in r.items()} for r in reader]
    rows = [r for r in rows if any((v or "").strip() for v in r.values())]
    if not rows:
        raise UploadFileError("File has a header but no data rows")
    if len(rows) > MAX_ROWS:
        raise UploadFileError(f"File has {len(rows)} rows; the limit is {MAX_ROWS} per upload")
    return rows


def _to_request(row: dict[str, str], products: dict[str, str], default_channel: dict[str, str],
                guest: str) -> FeedbackCreate:
    source_type = (_clean(row.get("source_type")) or "REVIEW").upper()
    product_id = _clean(row.get("product_id"))
    name = _clean(row.get("product_name"))
    if not product_id and name:
        product_id = products.get(name.lower())
        if product_id is None:
            raise fs.FeedbackValidationError(f"Unknown product_name '{name}'")
    fd = _clean(row.get("feedback_date"))
    if fd:
        try:
            parsed = datetime.fromisoformat(fd)
        except ValueError:
            raise fs.FeedbackValidationError(f"feedback_date '{fd}' is not an ISO date (YYYY-MM-DD)") from None
        if len(fd) == 10:  # a plain date: noon India time, so it stays on that calendar day in every report
            parsed = datetime.combine(date.fromisoformat(fd), datetime.min.time()).replace(hour=6, minute=30,
                                                                                          tzinfo=timezone.utc)
        fd = parsed
    payload = {
        "source_type": source_type, "customer_id": _clean(row.get("customer_id")) or guest,
        "product_id": product_id,
        "channel_id": _clean(row.get("channel_id")) or (default_channel.get(source_type)
                                                       if source_type != "SUPPORT_MESSAGE" else None),
        "text": row.get("text") or "", "rating": _clean(row.get("rating")), "nps_score": _clean(row.get("nps_score")),
        "review_title": _clean(row.get("review_title")), "survey_id": _clean(row.get("survey_id")),
        "ticket_id": _clean(row.get("ticket_id")), "language": _clean(row.get("language")), "feedback_date": fd,
    }
    return FeedbackCreate(**{k: v for k, v in payload.items() if v is not None})


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "__root__")
        msg = e.get("msg", "").removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def process_upload(db: Session, raw: bytes, filename: str) -> dict:
    """Validate, store and score every row. Commits once at the end."""
    rows = parse_csv(raw)
    guest = ensure_guest_customer(db)
    products, default_channel = _lookups(db)
    run_id = fs.create_inference_run(db, "api-upload", {"upload": filename, "rows": len(rows)})
    results: list[dict] = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header, so numbers match a spreadsheet
        base = {"row": i, "text": (row.get("text") or "")[:200]}
        sp = db.begin_nested()
        try:
            req = _to_request(row, products, default_channel, guest)
            fid = fs.create_feedback(db, req, triggered_by="api-upload", run_id=run_id)
            sp.commit()
            results.append({**base, "feedback_id": fid})
        except ValidationError as exc:
            sp.rollback()
            results.append({**base, "status": "REJECTED", "error": _validation_message(exc)})
        except fs.FeedbackConflictError as exc:
            sp.rollback()
            dup = "already submitted" in str(exc)
            results.append({**base, "status": "DUPLICATE" if dup else "REJECTED", "error": str(exc)})
        except fs.FeedbackValidationError as exc:
            sp.rollback()
            results.append({**base, "status": "REJECTED", "error": str(exc)})

    created = [r["feedback_id"] for r in results if "feedback_id" in r]
    if created:
        details = {r["feedback_id"]: dict(r) for r in db.execute(
            text(f"{fs.BASE_SELECT} WHERE fi.feedback_id = ANY(:ids)"), {"ids": created}).mappings()}
        themes = fs._themes_for(db, created)
        for r in results:
            d = details.get(r.get("feedback_id"))
            if d is None:
                continue
            status = {"QUARANTINED": "QUARANTINED", "DUPLICATE": "DUPLICATE"}.get(d["processing_status"], "CREATED")
            r.update({"status": status, "source_type": d["source_type"], "product_id": d["product_id"],
                      "product_name": d["product_name"], "text": d["text"][:200], "language": d["language"],
                      "sentiment": d["sentiment_label"],
                      "confidence": float(d["confidence"]) if d["confidence"] is not None else None,
                      "needs_human_review": d["needs_human_review"],
                      "themes": [t["theme_name"] for t in themes.get(r["feedback_id"], [])],
                      "error": "Personal data found; text redacted and record quarantined" if status == "QUARANTINED"
                      else ("Duplicate of an existing response" if status == "DUPLICATE" else None)})
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("CREATED", "QUARANTINED", "DUPLICATE", "REJECTED")}
    db.execute(text("UPDATE pipeline_runs SET summary = summary || CAST(:s AS jsonb) WHERE run_id = :r"),
               {"s": json.dumps({"upload_counts": counts}), "r": run_id})
    db.commit()
    return {"run_id": run_id, "filename": filename, "rows_total": len(rows),
            "created": counts["CREATED"], "quarantined": counts["QUARANTINED"], "duplicates": counts["DUPLICATE"],
            "rejected": counts["REJECTED"], "results": results}


def template_csv(db: Session) -> str:
    """A ready-to-edit example: real product names from the catalogue, one row per typical case."""
    names = [n for (n,) in db.execute(text("SELECT product_name FROM products WHERE lifecycle_status = 'ACTIVE' "
                                           "ORDER BY product_id LIMIT 3"))] or ["<product name>"]
    names = (names * 3)[:3]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_COLUMNS)
    examples = [
        ("Battery drains within a day and the charger got hot. Very disappointed.", 2, ""),
        ("Delivery was quick and the packaging was neat. Happy with the purchase.", 5, ""),
        ("Product works fine but customer support took a week to reply.", 3, "2026-10-15"),
    ]
    for (body, rating, fd), name in zip(examples, names):
        w.writerow([body, "REVIEW", "", name, "", rating, "", fd, "", "", "", "", ""])
    return buf.getvalue()
