"""Data quality endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, get_engine
from app.schemas.api import IssuePage
from app.schemas.common import page_meta
from app.services.validation_service import RULES, build_validation_summary

router = APIRouter(prefix="/api/validation", tags=["data quality"])


@router.get("/issues", response_model=IssuePage, summary="List detected data quality issues / exceptions")
def list_issues(db: Session = Depends(get_db), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                rule_code: str | None = None,
                severity: str | None = Query(None, pattern="^(INFO|LOW|MEDIUM|HIGH|CRITICAL)$"),
                entity_type: str | None = None, record_id: str | None = None,
                resolution_action: str | None = None) -> IssuePage:
    where, params = ["TRUE"], {}
    for col, val in (("rule_code", rule_code), ("severity", severity), ("entity_type", entity_type),
                     ("record_id", record_id), ("resolution_action", resolution_action)):
        if val:
            where.append(f"{col} = :{col}")
            params[col] = val
    w = " AND ".join(where)
    total = db.execute(text(f"SELECT count(*) FROM data_quality_issues WHERE {w}"), params).scalar_one()
    rows = db.execute(text(f"""SELECT * FROM data_quality_issues WHERE {w}
        ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END,
                 issue_id LIMIT :lim OFFSET :off"""), {**params, "lim": page_size, "off": (page - 1) * page_size}
                      ).mappings().all()
    return IssuePage(items=[dict(r) for r in rows], meta=page_meta(page, page_size, total))


@router.get("/summary", summary="Machine-readable validation summary")
def summary() -> dict[str, Any]:
    return build_validation_summary(get_engine())


@router.get("/rules", summary="Catalogue of validation rules and default resolutions")
def rules() -> dict[str, Any]:
    return RULES
