"""Sentiment trend analytics and shift (anomaly) detection.

All figures are computed from the current predictions of analysable feedback
(duplicates and quarantined items excluded) - nothing is hard-coded.

Shift rule (documented in docs/business_rules.md)
-------------------------------------------------
For each group (overall, or a theme) and period (day/week/month):

    baseline = mean negative rate over the previous ``baseline_periods`` periods
    z        = (rate - baseline) / max(stdev, 0.03)
    ALERT if volume >= min_volume AND rate - baseline >= min_increase AND z >= z_threshold

Each alert carries the feedback ids behind it for traceability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.taxonomy import THEME_BY_ID

TZ = "Asia/Kolkata"
GRAIN = {"day": "day", "week": "week", "month": "month"}
STD_FLOOR = 0.03


@dataclass
class TrendThresholds:
    z_threshold: float
    min_increase: float
    min_volume: int
    baseline_periods: int

    @classmethod
    def from_settings(cls, **overrides) -> "TrendThresholds":
        s = get_settings()
        base = dict(z_threshold=s.trend_z_threshold, min_increase=s.trend_min_increase,
                    min_volume=s.trend_min_volume, baseline_periods=s.trend_baseline_periods)
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)


def _filters(product_id: str | None, channel_id: str | None, source_type: str | None,
             date_from: date | None, date_to: date | None, alias: str = "fa") -> tuple[str, dict]:
    clauses, params = [], {}
    for col, val in (("product_id", product_id), ("channel_id", channel_id), ("source_type", source_type)):
        if val:
            clauses.append(f"{alias}.{col} = :{col}")
            params[col] = val
    if date_from:
        clauses.append(f"({alias}.feedback_date AT TIME ZONE '{TZ}')::date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append(f"({alias}.feedback_date AT TIME ZONE '{TZ}')::date <= :date_to")
        params["date_to"] = date_to
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def sentiment_series(conn: Connection, granularity: str = "week", theme_id: str | None = None,
                     product_id: str | None = None, channel_id: str | None = None, source_type: str | None = None,
                     date_from: date | None = None, date_to: date | None = None, by_theme: bool = False
                     ) -> pd.DataFrame:
    """Counts per period (and optionally per theme) with negative rate."""
    grain = GRAIN[granularity]
    where, params = _filters(product_id, channel_id, source_type, date_from, date_to)
    theme_join = ""
    group_theme = ""
    if theme_id or by_theme:
        theme_join = "JOIN v_current_themes ct ON ct.feedback_id = fa.feedback_id"
        group_theme = ", ct.theme_id"
        if theme_id:
            where += " AND ct.theme_id = :theme_id"
            params["theme_id"] = theme_id
    sql = f"""
        SELECT date_trunc('{grain}', fa.feedback_date AT TIME ZONE '{TZ}')::date AS period {group_theme},
               count(*) AS total,
               count(*) FILTER (WHERE fa.sentiment_label = 'POSITIVE') AS positive,
               count(*) FILTER (WHERE fa.sentiment_label = 'NEGATIVE') AS negative,
               count(*) FILTER (WHERE fa.sentiment_label = 'NEUTRAL') AS neutral,
               count(*) FILTER (WHERE fa.sentiment_label = 'MIXED') AS mixed
        FROM v_feedback_analytics fa {theme_join}
        WHERE TRUE {where}
        GROUP BY 1 {group_theme} ORDER BY 1 {group_theme}"""
    df = pd.DataFrame(conn.execute(text(sql), params).mappings().all())
    if df.empty:
        return pd.DataFrame(columns=["period", "total", "positive", "negative", "neutral", "mixed", "negative_rate"])
    df["negative_rate"] = (df["negative"] / df["total"]).round(4)
    return df


def _alerts_for_series(series: pd.DataFrame, th: TrendThresholds) -> list[dict]:
    series = series.sort_values("period").reset_index(drop=True)
    alerts = []
    rates, vols = series["negative_rate"].to_numpy(float), series["total"].to_numpy(int)
    for i in range(th.baseline_periods, len(series)):
        window = slice(i - th.baseline_periods, i)
        w_rates, w_vols = rates[window], vols[window]
        mask = w_vols > 0
        if mask.sum() < max(2, th.baseline_periods // 2):
            continue
        base = float(np.average(w_rates[mask], weights=w_vols[mask]))
        std = max(float(np.std(w_rates[mask])), STD_FLOOR)
        rate, vol = float(rates[i]), int(vols[i])
        z = (rate - base) / std
        if vol >= th.min_volume and rate - base >= th.min_increase and z >= th.z_threshold:
            alerts.append({"period": series.loc[i, "period"], "negative_rate": round(rate, 4),
                           "baseline_rate": round(base, 4), "increase": round(rate - base, 4),
                           "z_score": round(z, 2), "volume": vol, "negative_count": int(series.loc[i, "negative"])})
    return alerts


def _period_end(start: date, granularity: str) -> date:
    p = pd.Timestamp(start)
    end = {"day": p, "week": p + pd.Timedelta(days=6), "month": p + pd.offsets.MonthEnd(0)}[granularity]
    return end.date()


def _attach_evidence(conn: Connection, alerts: list[dict], granularity: str, evidence_limit: int | None,
                     filters: dict) -> None:
    """Traceability: the negative feedback behind each alert.

    One query fetches every negative (feedback, date, theme) row that falls in any alert period; rows are
    then assigned to alerts in memory. (A query per alert used a slow generic plan once the driver
    prepared it - minutes at full scale.)
    """
    if not alerts:
        return
    for a in alerts:
        a["period_start"], a["period_end"] = a["period"].isoformat(), _period_end(a["period"], granularity).isoformat()
        a["granularity"] = granularity
    lo = min(a["period_start"] for a in alerts)
    hi = max(a["period_end"] for a in alerts)
    where, params = _filters(filters.get("product_id"), filters.get("channel_id"), filters.get("source_type"),
                             date.fromisoformat(lo), date.fromisoformat(hi))
    rows = conn.execute(text(f"""
        SELECT fa.feedback_id, (fa.feedback_date AT TIME ZONE '{TZ}')::date AS d, ct.theme_id
        FROM v_feedback_analytics fa LEFT JOIN v_current_themes ct ON ct.feedback_id = fa.feedback_id
        WHERE fa.sentiment_label = 'NEGATIVE' {where}
        ORDER BY fa.feedback_date, fa.feedback_id"""), params).all()
    for a in alerts:
        s, e, tid = a["period_start"], a["period_end"], a["theme_id"]
        ids: list[str] = []
        seen: set[str] = set()
        for fid, d, theme in rows:
            if s <= d.isoformat() <= e and (tid is None or theme == tid) and fid not in seen:
                seen.add(fid)
                ids.append(fid)
                if evidence_limit and len(ids) >= evidence_limit:
                    break
        a["evidence_feedback_ids"] = ids
        del a["period"]


def detect_shifts(conn: Connection, granularity: str = "week", thresholds: TrendThresholds | None = None,
                  include_overall: bool = True, evidence_limit: int | None = 50, **filters) -> list[dict]:
    """Detect unusual increases in negative feedback overall and per theme."""
    th = thresholds or TrendThresholds.from_settings()
    alerts: list[dict] = []
    if include_overall:
        for a in _alerts_for_series(sentiment_series(conn, granularity, **filters), th):
            alerts.append({"scope": "OVERALL", "theme_id": None, "theme_name": None, **a})
    by_theme = sentiment_series(conn, granularity, by_theme=True, **filters)
    if not by_theme.empty:
        for theme_id, grp in by_theme.groupby("theme_id"):
            for a in _alerts_for_series(grp, th):
                alerts.append({"scope": "THEME", "theme_id": theme_id, "theme_name": THEME_BY_ID[theme_id].name, **a})
    _attach_evidence(conn, alerts, granularity, evidence_limit, filters)
    alerts.sort(key=lambda x: (x["period_start"], x["scope"], x["theme_id"] or ""))
    return alerts


def month_over_month(conn: Connection, **filters) -> list[dict]:
    """Monthly totals and negative rate with change versus the previous month."""
    df = sentiment_series(conn, "month", **filters)
    out, prev = [], None
    for r in df.sort_values("period").itertuples():
        row = {"month": r.period.strftime("%Y-%m"), "total": int(r.total), "negative": int(r.negative),
               "positive": int(r.positive), "negative_rate": float(r.negative_rate),
               "positive_rate": round(r.positive / r.total, 4) if r.total else 0.0}
        if prev:
            row["negative_rate_change_pp"] = round((row["negative_rate"] - prev["negative_rate"]) * 100, 2)
            row["volume_change_pct"] = round((row["total"] - prev["total"]) / prev["total"] * 100, 2) if prev["total"] else None
        else:
            row["negative_rate_change_pp"] = row["volume_change_pct"] = None
        out.append(row)
        prev = row
    return out


def thresholds_dict(th: TrendThresholds) -> dict:
    return asdict(th)
