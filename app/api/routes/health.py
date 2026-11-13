"""Liveness / readiness endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service and database health")
def health(db: Session = Depends(get_db)) -> HealthResponse:
    """Checks the database connection and reports schema/model readiness."""
    settings = get_settings()
    db_ok, detail, tables, feedback_count = True, "ok", 0, 0
    try:
        db.execute(text("select 1"))
        tables = db.execute(
            text("select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
        ).scalar_one()
        if tables:
            feedback_count = db.execute(text("select count(*) from feedback_items")).scalar_one()
    except Exception as exc:  # pragma: no cover - exercised when DB is down
        db_ok, detail = False, f"{type(exc).__name__}: {exc}"[:300]
    from app.services.model_registry import available_models

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        database="connected" if db_ok else "unavailable",
        database_detail=detail,
        schema_tables=tables,
        feedback_items=feedback_count,
        models=available_models(),
    )
