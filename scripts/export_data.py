"""Export analysis-ready data from the database.

Writes to ``data/exports/<scale>/``:

* ``feedback_enriched.csv/.json``: every feedback item with its current sentiment
  prediction (label, confidence, review flag, model version) and current themes
* ``action_items.csv/.json`` plus ``action_evidence.csv``
* ``data_quality_issues.csv/.json``
* ``insightpulse_seed.sql[.gz]``: a data-only SQL seed (COPY blocks in FK order),
  loadable with ``psql -f`` into a schema created from ``database/schema.sql``

Ground-truth files are never exported here; they stay in ``data/ground_truth``.

Usage::

    python -m scripts.export_data --scale starter
    python -m scripts.export_data --scale full --gzip-seed
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.logging_config import configure_logging, get_logger

log = get_logger("export_data")

# FK-safe order for the SQL seed. Reference tables first; history tables last.
SEED_TABLES = [
    "sentiment_labels", "themes", "channels", "products", "customers", "surveys", "survey_responses", "reviews",
    "support_tickets", "support_messages", "pipeline_runs", "feedback_items", "feedback_annotations",
    "sentiment_predictions", "feedback_themes", "data_quality_issues", "state_change_events", "action_items",
    "action_evidence", "action_status_events",
]
SEQUENCES = {"pipeline_run_seq": ("pipeline_runs", "run_id"), "action_item_seq": ("action_items", "action_id"),
             "dq_issue_seq": ("data_quality_issues", "issue_id")}
IDENTITY_COLUMNS = [("sentiment_predictions", "prediction_id"), ("feedback_themes", "feedback_theme_id"),
                    ("action_status_events", "event_id"), ("state_change_events", "event_id")]

FEEDBACK_SQL = """
SELECT fi.feedback_id, fi.source_type, fi.source_record_id, fi.customer_id, fi.product_id, p.product_name,
       fi.channel_id, c.channel_name, fi.feedback_date, fi.rating, fi.language,
       COALESCE(fi.redacted_text, fi.feedback_text) AS text, fi.processing_status, fi.dataset_split,
       cs.sentiment_label, cs.confidence AS sentiment_confidence, cs.needs_human_review, cs.review_reason,
       cs.prediction_method, cs.model_version AS sentiment_model_version, cs.run_id AS prediction_run_id,
       (SELECT string_agg(ct.theme_id, '|' ORDER BY ct.confidence DESC) FROM v_current_themes ct
         WHERE ct.feedback_id = fi.feedback_id) AS theme_ids
FROM feedback_items fi
JOIN products p ON p.product_id = fi.product_id
JOIN channels c ON c.channel_id = fi.channel_id
LEFT JOIN v_current_sentiment cs ON cs.feedback_id = fi.feedback_id
ORDER BY fi.feedback_id"""


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _write(out: Path, name: str, rows: list[dict], json_too: bool = True) -> int:
    cols = list(rows[0].keys()) if rows else []
    with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else _jsonable(v)))
                        for k, v in r.items()})
    if json_too:
        (out / f"{name}.json").write_text(json.dumps([{k: _jsonable(v) for k, v in r.items()} for r in rows],
                                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(rows)


def export_tables(engine, out: Path) -> dict:
    counts = {}
    with engine.connect() as conn:
        q = lambda sql: [dict(r) for r in conn.execute(text(sql)).mappings()]  # noqa: E731
        counts["feedback_enriched"] = _write(out, "feedback_enriched", q(FEEDBACK_SQL))
        counts["action_items"] = _write(out, "action_items", q("""
            SELECT a.*, p.product_name FROM action_items a LEFT JOIN products p USING (product_id)
            ORDER BY a.action_id"""))
        counts["action_evidence"] = _write(out, "action_evidence", q(
            "SELECT * FROM action_evidence ORDER BY action_id, feedback_id"), json_too=False)
        counts["data_quality_issues"] = _write(out, "data_quality_issues", q(
            "SELECT * FROM data_quality_issues ORDER BY issue_id"))
    return counts


def export_seed(engine, path: Path, compress: bool) -> dict:
    """Data-only SQL seed using COPY blocks (fast to load, exact round trip)."""
    path = path.with_suffix(".sql.gz") if compress else path
    opener = (lambda: gzip.open(path, "wt", encoding="utf-8", compresslevel=6)) if compress else \
        (lambda: path.open("w", encoding="utf-8"))
    rows_per_table = {}
    raw = engine.raw_connection()
    try:
        conn = raw.driver_connection
        with opener() as fh, conn.cursor() as cur:
            fh.write("-- InsightPulse data-only seed. Load after database/schema.sql into an EMPTY schema:\n"
                     "--   psql \"$DATABASE_URL\" -f database/schema.sql && psql \"$DATABASE_URL\" -f <this file>\n"
                     "SET client_encoding = 'UTF8';\nBEGIN;\n"
                     f"TRUNCATE {', '.join(reversed(SEED_TABLES))} CASCADE;\n")
            for table in SEED_TABLES:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s "
                            "AND table_schema = 'public' AND is_generated = 'NEVER' ORDER BY ordinal_position",
                            (table,))
                cols = ", ".join(r[0] for r in cur.fetchall())
                fh.write(f"\nCOPY {table} ({cols}) FROM stdin;\n")
                n = 0
                with cur.copy(f"COPY {table} ({cols}) TO STDOUT") as cp:
                    buf = bytearray()
                    for chunk in cp:
                        buf += chunk
                    data = buf.decode("utf-8")
                    n = data.count("\n")
                    fh.write(data)
                fh.write("\\.\n")
                rows_per_table[table] = n
            for seq, (table, col) in SEQUENCES.items():
                fh.write(f"SELECT setval('{seq}', COALESCE((SELECT max(substring({col} from '[0-9]+$')::bigint) "
                         f"FROM {table}), 1));\n")
            for table, col in IDENTITY_COLUMNS:
                fh.write(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                         f"COALESCE((SELECT max({col}) FROM {table}), 1));\n")
            fh.write("COMMIT;\n")
    finally:
        raw.close()
    return {"file": str(path), "rows": rows_per_table}


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scale", choices=["starter", "full"], default=settings.data_scale)
    p.add_argument("--gzip-seed", action="store_true", help="Write insightpulse_seed.sql.gz")
    p.add_argument("--no-seed", action="store_true")
    args = p.parse_args(argv)
    configure_logging()
    out = settings.data_dir / "exports" / args.scale
    out.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    result = {"scale": args.scale, "directory": str(out), "tables": export_tables(engine, out)}
    if not args.no_seed:
        result["seed"] = export_seed(engine, out / "insightpulse_seed.sql", args.gzip_seed)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
