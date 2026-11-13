"""Initialise the PostgreSQL schema and reference data.

Usage::

    python -m scripts.setup_db            # create schema if missing
    python -m scripts.setup_db --reset    # drop & recreate everything (destructive)
    python -m scripts.setup_db --write-seed   # regenerate database/seed.sql from the taxonomy
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.core.config import PROJECT_ROOT, get_settings
from app.core.logging_config import configure_logging, get_logger
from app.core.taxonomy import render_seed_sql

log = get_logger("setup_db")
SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"
SEED_FILE = PROJECT_ROOT / "database" / "seed.sql"


def _exec_sql_file(engine: Engine, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def write_seed_file() -> Path:
    SEED_FILE.write_text(render_seed_sql(), encoding="utf-8")
    return SEED_FILE


def setup_database(url: str | None = None, reset: bool = False) -> dict:
    """Create schema + reference data. Returns a summary with the table list."""
    engine = create_engine(url or get_settings().database_url, future=True)
    try:
        if reset:
            with engine.begin() as conn:
                conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
            log.info("Dropped and recreated schema public")
        if "customers" not in inspect(engine).get_table_names():
            _exec_sql_file(engine, SCHEMA_FILE)
            log.info("Applied %s", SCHEMA_FILE.name)
        _exec_sql_file(engine, SEED_FILE)
        tables = sorted(inspect(engine).get_table_names())
        with engine.connect() as conn:
            themes = conn.execute(text("select count(*) from themes")).scalar_one()
            labels = conn.execute(text("select count(*) from sentiment_labels")).scalar_one()
        return {"tables": tables, "themes": themes, "sentiment_labels": labels}
    finally:
        engine.dispose()


def main() -> None:
    configure_logging(get_settings().log_level)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all objects")
    parser.add_argument("--write-seed", action="store_true", help="regenerate database/seed.sql")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    if args.write_seed:
        log.info("Wrote %s", write_seed_file())
    summary = setup_database(args.database_url, reset=args.reset)
    log.info("Schema ready: %d tables, %d themes, %d sentiment labels",
             len(summary["tables"]), summary["themes"], summary["sentiment_labels"])
    for t in summary["tables"]:
        log.info("  table %s", t)


if __name__ == "__main__":
    main()
