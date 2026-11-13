"""Analytics endpoints - all values computed live from current predictions."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.api import SourceTypeLit
from app.services import analytics_service as an
from app.services.trend_service import TrendThresholds, detect_shifts, month_over_month, sentiment_series, thresholds_dict

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _filters(product_id: str | None = Query(None, pattern=r"^PRD-\d{6}$"),
             channel_id: str | None = Query(None, pattern=r"^CHN-\d{6}$"),
             source_type: SourceTypeLit | None = None,
             date_from: date | None = None, date_to: date | None = None) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    return {"product_id": product_id, "channel_id": channel_id, "source_type": source_type,
            "date_from": date_from, "date_to": date_to}


@router.get("/overview", summary="KPIs, sentiment mix, top themes, negative trend, priority actions, DQ summary")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return an.overview(db.connection())


@router.get("/sentiment", summary="Sentiment distribution overall and by product, channel, source and language")
def sentiment(db: Session = Depends(get_db), f: dict = Depends(_filters)) -> dict[str, Any]:
    return an.sentiment_breakdown(db.connection(), **f)


@router.get("/themes", summary="Theme frequency, negative feedback by theme, theme x product, monthly theme trends")
def themes(db: Session = Depends(get_db), f: dict = Depends(_filters)) -> dict[str, Any]:
    return an.theme_breakdown(db.connection(), **f)


@router.get("/trends", summary="Daily/weekly/monthly sentiment trends, month-over-month and shift alerts")
def trends(db: Session = Depends(get_db), f: dict = Depends(_filters),
           granularity: Literal["day", "week", "month"] = "week",
           theme_id: str | None = Query(None, pattern=r"^THEME-\d{6}$"),
           z_threshold: float | None = Query(None, gt=0), min_increase: float | None = Query(None, ge=0, le=1),
           min_volume: int | None = Query(None, ge=1), baseline_periods: int | None = Query(None, ge=2, le=52)
           ) -> dict[str, Any]:
    conn = db.connection()
    th = TrendThresholds.from_settings(z_threshold=z_threshold, min_increase=min_increase, min_volume=min_volume,
                                       baseline_periods=baseline_periods)
    series = sentiment_series(conn, granularity, theme_id=theme_id, **f)
    return {
        "granularity": granularity, "thresholds": thresholds_dict(th),
        "series": [] if series.empty else [
            {"period": r.period.isoformat(), "total": int(r.total), "positive": int(r.positive),
             "negative": int(r.negative), "neutral": int(r.neutral), "mixed": int(r.mixed),
             "negative_rate": float(r.negative_rate)} for r in series.itertuples()],
        "month_over_month": month_over_month(conn, **f),
        "alerts": detect_shifts(conn, granularity, th, **f),
    }
