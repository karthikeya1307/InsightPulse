"""Database ingestion of a generated dataset.

Flow
----
1. Read master/raw CSV files for a scale.
2. Run file-level validation (PK uniqueness, FK integrity, enums, required
   fields, dates, rating bounds, id formats). Records that would violate a
   hard database constraint are REJECTED: they are written to
   ``data/exceptions/<scale>/rejected_records.csv`` and logged as data quality
   issues - never silently dropped.
3. In ONE transaction: bulk-load (COPY) master -> source -> feedback items ->
   annotations -> state-change events, then record the issues.

The unified ``feedback_items`` rows are *derived* from source records here.
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.services import validation_service as vs
from app.utils import id_generator as ids
from app.utils.text_preprocessing import normalize_text, text_hash

log = get_logger("ingestion")

# table name -> (data group, file stem, DB columns in load order)
LOAD_ORDER: list[tuple[str, str, str, list[str]]] = [
    ("channels", "master", "channels", ["channel_id", "channel_code", "channel_name", "channel_type",
     "supports_reviews", "supports_surveys", "supports_support", "is_active", "created_at"]),
    ("products", "master", "products", ["product_id", "sku", "product_name", "category", "brand", "unit_price",
     "currency", "launch_date", "discontinued_date", "lifecycle_status", "created_at", "updated_at"]),
    ("customers", "master", "customers", ["customer_id", "first_name", "last_name", "email", "phone", "city",
     "state", "country", "preferred_language", "segment", "signup_date", "lifecycle_status", "effective_from",
     "created_at", "updated_at"]),
    ("surveys", "master", "surveys", ["survey_id", "title", "survey_type", "product_id", "channel_id",
     "question_text", "start_date", "end_date", "status", "created_at", "updated_at"]),
    ("survey_responses", "raw", "survey_responses", ["response_id", "survey_id", "customer_id", "product_id",
     "channel_id", "response_date", "rating", "nps_score", "response_text", "language", "created_at"]),
    ("reviews", "raw", "reviews", ["review_id", "customer_id", "product_id", "channel_id", "rating",
     "review_title", "review_text", "review_date", "language", "verified_purchase", "helpful_votes",
     "created_at"]),
    ("support_tickets", "raw", "support_tickets", ["ticket_id", "customer_id", "product_id", "channel_id",
     "category_theme_id", "subject", "priority", "status", "opened_at", "resolved_at", "closed_at",
     "message_count", "created_at", "updated_at"]),
    ("support_messages", "raw", "support_messages", ["message_id", "ticket_id", "message_seq", "sender_type",
     "message_text", "sent_at", "language", "created_at"]),
]
FEEDBACK_COLUMNS = ["feedback_id", "source_type", "source_record_id", "survey_response_id", "review_id",
                    "support_message_id", "customer_id", "product_id", "channel_id", "feedback_date", "rating",
                    "feedback_text", "language", "text_hash", "dataset_split", "processing_status"]
ANNOTATION_COLUMNS = ["annotation_id", "feedback_id", "sentiment_label", "theme_labels", "annotator_id",
                      "annotation_note", "annotated_at", "declared_split", "training_status"]
EVENT_COLUMNS = ["entity_type", "entity_id", "from_state", "to_state", "changed_by", "reason", "run_id",
                 "changed_at"]
DATA_TABLES = ["action_status_events", "action_evidence", "action_items", "feedback_themes",
               "sentiment_predictions", "data_quality_issues", "state_change_events", "feedback_annotations",
               "feedback_items", "support_messages", "support_tickets", "reviews", "survey_responses",
               "surveys", "customers", "products", "channels", "pipeline_runs"]


class IngestionError(RuntimeError):
    pass


@dataclass
class Dataset:
    scale: str
    tables: dict[str, list[dict]]
    annotations: list[dict]
    splits: dict[str, str]
    ticket_events: list[dict]
    master_themes: list[dict]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise IngestionError(f"Missing input file {path}. Run the generator first.")
    with path.open(newline="", encoding="utf-8") as fh:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(fh)]


def load_dataset_files(scale: str, data_root: Path | None = None) -> Dataset:
    root = Path(data_root or get_settings().data_dir)
    tables = {name: read_csv(root / group / scale / f"{stem}.csv") for name, group, stem, _ in LOAD_ORDER}
    splits = {r["source_record_id"]: r["dataset_split"] for r in read_csv(root / "raw" / scale / "dataset_splits.csv")}
    return Dataset(scale, tables, read_csv(root / "raw" / scale / "annotations.csv"), splits,
                   read_csv(root / "raw" / scale / "ticket_status_events.csv"),
                   read_csv(root / "master" / scale / "themes.csv"))


def build_feedback_items(tables: dict[str, list[dict]], splits: dict[str, str]) -> list[dict]:
    """Derive unified FeedbackItem rows from the three source tables."""
    tickets = {t["ticket_id"]: t for t in tables["support_tickets"]}
    out: list[dict] = []

    def item(st: str, sid: str, cust: str, prod: str, chan: str, when: str, rating, body: str, lang: str) -> dict:
        return {
            "feedback_id": ids.feedback_id(sid), "source_type": st, "source_record_id": sid,
            "survey_response_id": sid if st == "SURVEY_RESPONSE" else None,
            "review_id": sid if st == "REVIEW" else None,
            "support_message_id": sid if st == "SUPPORT_MESSAGE" else None,
            "customer_id": cust, "product_id": prod, "channel_id": chan, "feedback_date": when,
            "rating": rating, "feedback_text": normalize_text(body), "language": lang,
            "text_hash": text_hash(body), "dataset_split": splits.get(sid, "UNASSIGNED"),
            "processing_status": "INGESTED",
        }

    for r in tables["survey_responses"]:
        out.append(item("SURVEY_RESPONSE", r["response_id"], r["customer_id"], r["product_id"], r["channel_id"],
                        r["response_date"], r["rating"], r["response_text"], r["language"]))
    for r in tables["reviews"]:
        out.append(item("REVIEW", r["review_id"], r["customer_id"], r["product_id"], r["channel_id"],
                        r["review_date"], r["rating"], r["review_text"], r["language"]))
    for m in tables["support_messages"]:
        if m["sender_type"] != "CUSTOMER":
            continue  # agent replies are kept as source records but are not customer feedback
        t = tickets[m["ticket_id"]]
        out.append(item("SUPPORT_MESSAGE", m["message_id"], t["customer_id"], t["product_id"], t["channel_id"],
                        m["sent_at"], None, m["message_text"], m["language"]))
    return out


def _copy(cur, table: str, columns: list[str], rows: list[dict], transform=None) -> int:
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for r in rows:
            r = transform(r) if transform else r
            cp.write_row([r.get(c) for c in columns])
    return len(rows)


def is_loaded(engine: Engine) -> bool:
    with engine.connect() as conn:
        return bool(conn.execute(text("select exists(select 1 from customers)")).scalar_one())


def reset_data(engine: Engine) -> None:
    """Remove all non-reference data (keeps themes and sentiment labels)."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE"))
        for seq in ("pipeline_run_seq", "action_item_seq", "dq_issue_seq"):
            conn.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH 1"))


def ingest(engine: Engine, scale: str, run_id: str, data_root: Path | None = None, replace: bool = False) -> dict:
    """Validate files and load them in a single transaction. Returns a summary."""
    t0 = time.perf_counter()
    if is_loaded(engine):
        if not replace:
            raise IngestionError("Database already contains data; use replace=True to reload.")
    ds = load_dataset_files(scale, data_root)
    file_report = vs.validate_files(ds.tables, ds.annotations, ds.splits, ds.master_themes, ds.ticket_events)
    rejected = file_report.rejected
    clean = {name: [r for r in rows if vs.pk_of(name, r) not in rejected.get(name, set())]
             for name, rows in ds.tables.items()}
    feedback = build_feedback_items(clean, ds.splits)
    feedback_ids = {f["feedback_id"] for f in feedback}
    annotations = [a for a in ds.annotations if a["annotation_id"] not in rejected.get("annotations", set())
                   and a["feedback_id"] in feedback_ids]
    loaded_tickets = {t["ticket_id"] for t in clean["support_tickets"]}
    events = [e for e in ds.ticket_events if e["entity_id"] in loaded_tickets]

    _write_rejections(scale, ds, rejected, data_root)
    raw_conn = engine.raw_connection()
    counts: dict[str, int] = {}
    try:
        if replace:
            with raw_conn.cursor() as cur:
                cur.execute(f"TRUNCATE {', '.join(t for t in DATA_TABLES if t != 'pipeline_runs')} RESTART IDENTITY CASCADE")
        with raw_conn.cursor() as cur:
            for name, _group, _stem, cols in LOAD_ORDER:
                counts[name] = _copy(cur, name, cols, clean[name])
            counts["feedback_items"] = _copy(cur, "feedback_items", FEEDBACK_COLUMNS, feedback)

            def ann(a: dict) -> dict:
                return dict(a, theme_labels=(a["theme_labels"] or "").split("|") if a["theme_labels"] else [],
                            training_status="PENDING")
            counts["feedback_annotations"] = _copy(cur, "feedback_annotations", ANNOTATION_COLUMNS, annotations, ann)
            ingest_events = [{"entity_type": "FEEDBACK_ITEM", "entity_id": f["feedback_id"], "from_state": None,
                              "to_state": "INGESTED", "changed_by": "ingestion", "reason": f"scale={scale}",
                              "run_id": run_id, "changed_at": "now"} for f in feedback]
            ticket_events = [dict(e, run_id=run_id, reason=e["reason"]) for e in events]
            counts["state_change_events"] = _copy(cur, "state_change_events", EVENT_COLUMNS, ticket_events + ingest_events)
            vs.store_issues(cur, file_report.issues, run_id)
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()
    summary = {
        "scale": scale, "loaded": counts,
        "raw_rows": {name: len(rows) for name, rows in ds.tables.items()} | {"annotations": len(ds.annotations)},
        "rejected": {k: len(v) for k, v in rejected.items()},
        "file_issues": len(file_report.issues), "seconds": round(time.perf_counter() - t0, 2),
    }
    log.info("Ingestion complete: %s", json.dumps(summary["loaded"]))
    return summary


def _write_rejections(scale: str, ds: Dataset, rejected: dict[str, set[str]], data_root: Path | None) -> None:
    out = Path(data_root or get_settings().data_dir) / "exceptions" / scale
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rejected_records.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["table", "record_id", "record_json"])
        for name, keys in sorted(rejected.items()):
            rows = ds.annotations if name == "annotations" else ds.tables.get(name, [])
            for r in rows:
                if vs.pk_of(name, r) in keys:
                    w.writerow([name, vs.pk_of(name, r), json.dumps(r, ensure_ascii=False)])
