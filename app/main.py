"""FastAPI application entry point.

Run with::

    uvicorn app.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.routes import actions, analytics, evaluation, feedback, health, pipeline, validation
from app.core.config import get_settings
from app.core.logging_config import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger("app")

app = FastAPI(
    title="InsightPulse API",
    version=settings.app_version,
    description=(
        "AI-powered customer feedback intelligence: unified feedback from surveys, reviews and "
        "support messages with sentiment, themes, trends, prioritised actions and data quality."
    ),
)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
    log.warning("Integrity error: %s", exc.orig)
    return JSONResponse(status_code=409, content={"detail": f"Integrity constraint violated: {exc.orig}"[:500]})


@app.exception_handler(OperationalError)
async def operational_error_handler(_: Request, exc: OperationalError) -> JSONResponse:
    log.error("Database unavailable: %s", exc.orig)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


for r in (health, feedback, analytics, actions, validation, pipeline, evaluation):
    app.include_router(r.router)
