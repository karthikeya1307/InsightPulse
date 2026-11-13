"""Business action endpoints with evidence traceability and lifecycle events."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.taxonomy import THEME_BY_ID
from app.schemas.api import (
    ActionCreate, ActionDetail, ActionPage, ActionStatusLit, ActionStatusUpdate, PriorityLit,
)
from app.schemas.common import page_meta
from app.services import action_service as acts

router = APIRouter(prefix="/api/actions", tags=["actions"])

SELECT = """SELECT a.*, p.product_name FROM action_items a LEFT JOIN products p ON p.product_id = a.product_id"""


def _summary(r: dict) -> dict:
    d = dict(r)
    d["theme_name"] = THEME_BY_ID[d["theme_id"]].name
    d["negative_share"] = float(d["negative_share"]) if d["negative_share"] is not None else None
    return d


def _detail(db: Session, action_id: str, evidence_limit: int = 200) -> dict | None:
    r = db.execute(text(f"{SELECT} WHERE a.action_id = :a"), {"a": action_id}).mappings().first()
    if r is None:
        return None
    d = _summary(r)
    d["status_history"] = [dict(e) for e in db.execute(text(
        """SELECT event_id, from_status, to_status, changed_by, note, changed_at FROM action_status_events
           WHERE action_id = :a ORDER BY changed_at, event_id"""), {"a": action_id}).mappings()]
    d["evidence_total"] = db.execute(text("SELECT count(*) FROM action_evidence WHERE action_id = :a"),
                                     {"a": action_id}).scalar_one()
    d["evidence"] = [dict(e) for e in db.execute(text("""
        SELECT e.feedback_id, fi.source_type, fi.source_record_id, fi.feedback_date, fi.product_id,
               COALESCE(fi.redacted_text, fi.feedback_text) AS text, cs.sentiment_label, e.run_id
        FROM action_evidence e JOIN feedback_items fi USING (feedback_id)
        LEFT JOIN v_current_sentiment cs ON cs.feedback_id = e.feedback_id
        WHERE e.action_id = :a ORDER BY fi.feedback_date DESC LIMIT :lim"""),
        {"a": action_id, "lim": evidence_limit}).mappings()]
    return d


@router.get("", response_model=ActionPage, summary="List action items")
def list_actions(db: Session = Depends(get_db), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                 status_: ActionStatusLit | None = Query(None, alias="status"), priority: PriorityLit | None = None,
                 product_id: str | None = Query(None, pattern=r"^PRD-\d{6}$"),
                 theme_id: str | None = Query(None, pattern=r"^THEME-\d{6}$"),
                 rule_code: str | None = None, active_only: bool = False) -> ActionPage:
    where, params = ["TRUE"], {}
    for col, val in (("a.status", status_), ("a.priority", priority), ("a.product_id", product_id),
                     ("a.theme_id", theme_id), ("a.rule_code", rule_code)):
        if val:
            key = col.split(".")[1]
            where.append(f"{col} = :{key}")
            params[key] = val
    if active_only:
        where.append("a.status IN ('OPEN','IN_PROGRESS','BLOCKED')")
    w = " AND ".join(where)
    total = db.execute(text(f"SELECT count(*) FROM action_items a WHERE {w}"), params).scalar_one()
    rows = db.execute(text(f"""{SELECT} WHERE {w}
        ORDER BY CASE a.priority WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                 a.created_at DESC, a.action_id LIMIT :lim OFFSET :off"""),
                      {**params, "lim": page_size, "off": (page - 1) * page_size}).mappings().all()
    return ActionPage(items=[_summary(r) for r in rows], meta=page_meta(page, page_size, total))


@router.get("/{action_id}", response_model=ActionDetail, summary="Action detail with evidence and status history")
def get_action(action_id: str, db: Session = Depends(get_db),
               evidence_limit: int = Query(200, ge=1, le=5000)) -> ActionDetail:
    d = _detail(db, action_id, evidence_limit)
    if d is None:
        raise HTTPException(404, f"Action {action_id} not found")
    return ActionDetail(**d)


@router.post("", response_model=ActionDetail, status_code=status.HTTP_201_CREATED, summary="Create a manual action")
def create_action(req: ActionCreate, db: Session = Depends(get_db)) -> ActionDetail:
    if req.product_id and db.execute(text("SELECT 1 FROM products WHERE product_id = :p"),
                                     {"p": req.product_id}).first() is None:
        raise HTTPException(422, f"product_id {req.product_id} does not exist")
    fids = sorted(set(req.feedback_ids))
    found = {r[0] for r in db.execute(text("SELECT feedback_id FROM feedback_items WHERE feedback_id = ANY(:f)"),
                                      {"f": fids})}
    missing = [f for f in fids if f not in found]
    if missing:
        raise HTTPException(422, f"Unknown feedback ids: {missing[:20]}")
    conn = db.connection()
    dup = acts.find_active_duplicate(conn, req.product_id, req.theme_id)
    if dup and not req.duplicate_reason:
        raise HTTPException(409, f"Active action {dup} already covers this product/theme; "
                                 "supply duplicate_reason to open another one")
    today = date.today()
    if req.due_date and req.due_date < today:
        raise HTTPException(422, "due_date cannot be in the past")
    candidate = {"rule_code": "MANUAL", "product_id": req.product_id, "theme_id": req.theme_id,
                 "priority": req.priority, "title": req.title, "description": req.description,
                 "recommended_action": req.recommended_action, "owner": req.owner,
                 "due_date": req.due_date or today + timedelta(days=30), "evidence": fids,
                 "metrics": {"created_by": req.created_by},
                 "duplicate_reason": req.duplicate_reason if dup else None}
    try:
        action_id = acts.create_action(conn, candidate, "MANUAL", None, req.created_by)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ActionDetail(**_detail(db, action_id))


@router.patch("/{action_id}/status", response_model=ActionDetail, summary="Change action lifecycle status")
def update_status(action_id: str, req: ActionStatusUpdate, db: Session = Depends(get_db)) -> ActionDetail:
    try:
        acts.change_status(db.connection(), action_id, req.status, req.changed_by, req.note)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, f"Action {action_id} not found") from exc
    except acts.ActionTransitionError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return ActionDetail(**_detail(db, action_id))
