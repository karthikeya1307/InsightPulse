"""End-to-end pipeline orchestration with run tracking.

Steps (in order): ingest -> validate -> train -> predict -> actions.
Each invocation creates a ``pipeline_runs`` row; failures mark the run FAILED
with the error message (nothing is half-committed because each step is
transactional).
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.core.logging_config import get_logger

log = get_logger("pipeline")
ALL_STEPS = ("ingest", "validate", "train", "predict", "actions")
_LOCK = threading.Lock()


class PipelineBusyError(RuntimeError):
    pass


def create_run(engine: Engine, run_type: str, triggered_by: str, parameters: dict) -> str:
    with engine.begin() as conn:
        return conn.execute(text("""
            INSERT INTO pipeline_runs (run_id, run_type, status, triggered_by, parameters)
            VALUES ('RUN-' || lpad(nextval('pipeline_run_seq')::text, 6, '0'), :t, 'RUNNING', :by, CAST(:p AS jsonb))
            RETURNING run_id"""), {"t": run_type, "by": triggered_by, "p": json.dumps(parameters)}).scalar_one()


def finish_run(engine: Engine, run_id: str, status: str, summary: dict, error: str | None = None,
               sentiment_version: str | None = None, theme_version: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE pipeline_runs SET status = :s, summary = CAST(:sum AS jsonb), error_message = :e,
                   finished_at = now(),
                   sentiment_model_version = COALESCE(:sv, sentiment_model_version),
                   theme_model_version = COALESCE(:tv, theme_model_version)
            WHERE run_id = :r"""), {"s": status, "sum": json.dumps(summary, default=str), "e": error, "r": run_id,
                                    "sv": sentiment_version, "tv": theme_version})


def write_validation_summary(engine: Engine, scale: str, run_id: str, data_root: Path | None = None) -> Path:
    from app.services.validation_service import build_validation_summary

    summary = build_validation_summary(engine, run_id)
    out = Path(data_root or get_settings().data_dir) / "exceptions" / scale
    out.mkdir(parents=True, exist_ok=True)
    path = out / "validation_summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_pipeline(engine: Engine, steps: tuple[str, ...] | list[str] = ALL_STEPS, scale: str | None = None,
                 replace: bool = False, triggered_by: str = "cli", data_root: Path | None = None,
                 run_id: str | None = None) -> dict:
    """Run the requested steps; returns the run summary (also stored on the run row)."""
    settings = get_settings()
    scale = scale or settings.data_scale
    steps = [s for s in ALL_STEPS if s in steps]
    if not _LOCK.acquire(blocking=False):
        raise PipelineBusyError("A pipeline run is already in progress")
    try:
        run_id = run_id or create_run(engine, "FULL" if len(steps) > 1 else steps[0].upper()
                                      .replace("INGEST", "INGESTION").replace("VALIDATE", "VALIDATION")
                                      .replace("TRAIN", "TRAINING").replace("PREDICT", "INFERENCE"),
                                      triggered_by, {"steps": steps, "scale": scale, "replace": replace})
        summary: dict = {"run_id": run_id, "scale": scale, "steps": {}}
        sv = tv = None
        try:
            for step in steps:
                t0 = time.perf_counter()
                log.info("[%s] step %s started", run_id, step)
                if step == "ingest":
                    from app.services.ingestion_service import ingest
                    result = ingest(engine, scale, run_id, data_root, replace=replace)
                elif step == "validate":
                    from app.services.validation_service import run_db_validation
                    result = run_db_validation(engine, run_id)
                    result["summary_file"] = str(write_validation_summary(engine, scale, run_id, data_root))
                elif step == "train":
                    from app.services.training_service import train_models
                    result = train_models(engine, run_id)
                    sv, tv = result["sentiment"]["model_version"], result["themes"]["model_version"]
                elif step == "predict":
                    from app.services.sentiment_service import predict_all
                    result = predict_all(engine, run_id)
                    sv, tv = result.get("sentiment_model_version"), result.get("theme_model_version")
                elif step == "actions":
                    from app.services.action_service import generate_actions
                    result = generate_actions(engine, run_id)
                else:  # pragma: no cover - guarded above
                    raise ValueError(step)
                result["seconds"] = round(time.perf_counter() - t0, 2)
                summary["steps"][step] = result
                log.info("[%s] step %s finished in %.2fs", run_id, step, result["seconds"])
            finish_run(engine, run_id, "SUCCEEDED", summary, sentiment_version=sv, theme_version=tv)
            summary["status"] = "SUCCEEDED"
        except Exception as exc:
            log.error("[%s] pipeline failed: %s", run_id, exc)
            summary["status"] = "FAILED"
            finish_run(engine, run_id, "FAILED", summary, error=f"{type(exc).__name__}: {exc}\n"
                       + traceback.format_exc()[-1500:])
            raise
        return summary
    finally:
        _LOCK.release()
