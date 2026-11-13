"""Pipeline orchestration endpoints."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, get_engine
from app.core.logging_config import get_logger
from app.schemas.api import PipelineRunOut, PipelineRunRequest
from app.services import pipeline_service as ps

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
log = get_logger("api.pipeline")


@router.post("/run", summary="Run pipeline steps (async by default; wait=true runs synchronously)",
             responses={202: {"description": "Run accepted"}, 409: {"description": "A run is already in progress"}})
def run(req: PipelineRunRequest):
    engine = get_engine()
    steps = list(dict.fromkeys(req.steps))
    if ps._LOCK.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "A pipeline run is already in progress")
    params = {"steps": steps, "scale": req.scale, "replace": req.replace}
    run_id = ps.create_run(engine, "FULL" if len(steps) > 1 else {"ingest": "INGESTION", "validate": "VALIDATION",
                           "train": "TRAINING", "predict": "INFERENCE", "actions": "ACTIONS"}[steps[0]], "api", params)
    kwargs = dict(steps=steps, scale=req.scale, replace=req.replace, triggered_by="api", run_id=run_id)
    if req.wait:
        try:
            return ps.run_pipeline(engine, **kwargs)
        except ps.PipelineBusyError as exc:
            ps.finish_run(engine, run_id, "FAILED", {}, error=str(exc))
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"Pipeline failed: {exc}") from exc

    def _bg() -> None:
        try:
            ps.run_pipeline(engine, **kwargs)
        except Exception as exc:  # already recorded on the run row
            log.error("Background run %s failed: %s", run_id, exc)

    threading.Thread(target=_bg, daemon=True, name=f"pipeline-{run_id}").start()
    return JSONResponse(status_code=202, content={"run_id": run_id, "status": "RUNNING",
                                                  "status_url": f"/api/pipeline/runs/{run_id}"})


@router.get("/runs", response_model=list[PipelineRunOut], summary="Recent pipeline runs")
def runs(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=200)) -> list[PipelineRunOut]:
    rows = db.execute(text("SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT :l"), {"l": limit}).mappings().all()
    return [PipelineRunOut(**r) for r in rows]


@router.get("/runs/{run_id}", response_model=PipelineRunOut, summary="Pipeline run status and summary")
def run_status(run_id: str, db: Session = Depends(get_db)) -> PipelineRunOut:
    r = db.execute(text("SELECT * FROM pipeline_runs WHERE run_id = :r"), {"r": run_id}).mappings().first()
    if r is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return PipelineRunOut(**r)
