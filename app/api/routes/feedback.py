"""Feedback endpoints: list/filter/search, detail with full traceability, create."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.taxonomy import ALLOWED_THEME_IDS
from app.schemas.api import FeedbackCreate, FeedbackDetail, FeedbackPage, SentimentLit, SourceTypeLit, UploadResult
from app.schemas.common import page_meta
from app.services import feedback_service as fs
from app.services import upload_service as us

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.get("", response_model=FeedbackPage, summary="List feedback with filters, search and pagination")
def list_feedback(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    product_id: str | None = Query(None, pattern=r"^PRD-\d{6}$"),
    channel_id: str | None = Query(None, pattern=r"^CHN-\d{6}$"),
    source_type: SourceTypeLit | None = None, sentiment: SentimentLit | None = None,
    theme_id: str | None = Query(None, pattern=r"^THEME-\d{6}$"),
    language: str | None = Query(None, pattern="^(en|hi|te)$"),
    status_: str | None = Query(None, alias="status",
                                pattern="^(INGESTED|VALIDATED|PROCESSED|DUPLICATE|QUARANTINED)$"),
    date_from: date | None = None, date_to: date | None = None,
    q: str | None = Query(None, min_length=2, max_length=100, description="Case-insensitive text search"),
    needs_review: bool | None = None,
) -> FeedbackPage:
    if theme_id and theme_id not in ALLOWED_THEME_IDS:
        raise HTTPException(422, f"theme_id {theme_id} is outside the allowed taxonomy")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    items, total = fs.list_feedback(db, page, page_size, product_id, channel_id, source_type, sentiment, theme_id,
                                    language, status_, date_from, date_to, q, needs_review)
    return FeedbackPage(items=items, meta=page_meta(page, page_size, total))


@router.get("/upload/template", response_class=PlainTextResponse,
            summary="Download a CSV template for bulk upload (columns plus three example rows)")
def upload_template(db: Session = Depends(get_db)) -> PlainTextResponse:
    return PlainTextResponse(us.template_csv(db), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=insightpulse_upload_template.csv"})


@router.post("/upload", response_model=UploadResult,
             summary="Upload a CSV of feedback: every row is validated, privacy-checked, de-duplicated and scored",
             responses={422: {"description": "The file cannot be read (encoding, header, size or row limit)"}})
def upload_feedback(file: UploadFile = File(..., description="UTF-8 CSV; only the `text` column is required"),
                          db: Session = Depends(get_db)) -> UploadResult:
    raw = file.file.read(us.MAX_BYTES + 1)
    try:
        return UploadResult(**us.process_upload(db, raw, file.filename or "upload.csv"))
    except us.UploadFileError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/{feedback_id}", response_model=FeedbackDetail,
            summary="Feedback detail: source record, prediction history, DQ issues, linked actions")
def get_feedback(feedback_id: str, db: Session = Depends(get_db)) -> FeedbackDetail:
    item = fs.get_feedback(db, feedback_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Feedback {feedback_id} not found")
    return FeedbackDetail(**item)


@router.post("", response_model=FeedbackDetail, status_code=status.HTTP_201_CREATED,
             summary="Submit new feedback (validated, privacy-checked and scored in one transaction)")
def create_feedback(req: FeedbackCreate, db: Session = Depends(get_db)) -> FeedbackDetail:
    try:
        fid = fs.create_feedback(db, req)
        db.commit()
    except fs.FeedbackValidationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except fs.FeedbackConflictError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return FeedbackDetail(**fs.get_feedback(db, fid))
