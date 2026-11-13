"""Business intelligence aggregates computed live from current predictions."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.taxonomy import THEME_BY_ID
from app.services.model_registry import available_models
from app.services.trend_service import TZ, _filters, detect_shifts, sentiment_series

LABELS = ("POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED")


def _dist_sql(dim: str | None, dim_name_sql: str = "", join: str = "", where: str = "") -> str:
    sel = f"{dim} AS key, {dim_name_sql or dim} AS name," if dim else ""
    grp = "GROUP BY 1, 2 ORDER BY count(*) DESC" if dim else ""
    return f"""SELECT {sel} count(*) AS total,
                      count(*) FILTER (WHERE fa.sentiment_label = 'POSITIVE') AS positive,
                      count(*) FILTER (WHERE fa.sentiment_label = 'NEGATIVE') AS negative,
                      count(*) FILTER (WHERE fa.sentiment_label = 'NEUTRAL') AS neutral,
                      count(*) FILTER (WHERE fa.sentiment_label = 'MIXED') AS mixed
               FROM v_feedback_analytics fa {join} WHERE TRUE {where} {grp}"""


def _with_pct(row: dict) -> dict:
    total = row["total"] or 0
    out = dict(row)
    for lab in LABELS:
        out[f"{lab.lower()}_pct"] = round(100 * row[lab.lower()] / total, 2) if total else 0.0
    return out


def sentiment_breakdown(conn: Connection, **filters) -> dict:
    where, params = _filters(filters.get("product_id"), filters.get("channel_id"), filters.get("source_type"),
                             filters.get("date_from"), filters.get("date_to"))
    overall = _with_pct(dict(conn.execute(text(_dist_sql(None, where=where)), params).mappings().one()))
    by = {}
    for name, dim, label, join in (
        ("by_product", "fa.product_id", "p.product_name", "JOIN products p ON p.product_id = fa.product_id"),
        ("by_channel", "fa.channel_id", "c.channel_name", "JOIN channels c ON c.channel_id = fa.channel_id"),
        ("by_source", "fa.source_type", "fa.source_type", ""),
        ("by_language", "fa.language", "fa.language", ""),
    ):
        by[name] = [_with_pct(dict(r)) for r in conn.execute(text(_dist_sql(dim, label, join, where)), params).mappings()]
    review = conn.execute(text(f"""SELECT count(*) FILTER (WHERE fa.needs_human_review) AS flagged, count(*) AS total
                                   FROM v_feedback_analytics fa WHERE TRUE {where}"""), params).mappings().one()
    return {"overall": overall, **by, "human_review": dict(review)}


def theme_breakdown(conn: Connection, **filters) -> dict:
    where, params = _filters(filters.get("product_id"), filters.get("channel_id"), filters.get("source_type"),
                             filters.get("date_from"), filters.get("date_to"))
    join = "JOIN v_current_themes ct ON ct.feedback_id = fa.feedback_id"
    freq = []
    for r in conn.execute(text(f"""
            SELECT ct.theme_id, count(*) AS mentions,
                   count(*) FILTER (WHERE fa.sentiment_label = 'NEGATIVE') AS negative,
                   count(*) FILTER (WHERE fa.sentiment_label = 'POSITIVE') AS positive,
                   count(*) FILTER (WHERE fa.sentiment_label = 'MIXED') AS mixed,
                   round(avg(ct.confidence)::numeric, 4) AS avg_confidence
            FROM v_feedback_analytics fa {join} WHERE TRUE {where} GROUP BY 1 ORDER BY 2 DESC"""), params).mappings():
        d = dict(r)
        d["theme_name"] = THEME_BY_ID[d["theme_id"]].name
        d["negative_share"] = round(d["negative"] / d["mentions"], 4) if d["mentions"] else 0.0
        d["avg_confidence"] = float(d["avg_confidence"])
        freq.append(d)
    by_product = []
    for r in conn.execute(text(f"""
            SELECT fa.product_id, p.product_name, ct.theme_id, count(*) AS mentions,
                   count(*) FILTER (WHERE fa.sentiment_label = 'NEGATIVE') AS negative
            FROM v_feedback_analytics fa {join} JOIN products p ON p.product_id = fa.product_id
            WHERE TRUE {where} GROUP BY 1, 2, 3 ORDER BY 1, 3"""), params).mappings():
        d = dict(r)
        d["theme_name"] = THEME_BY_ID[d["theme_id"]].name
        d["negative_share"] = round(d["negative"] / d["mentions"], 4)
        by_product.append(d)
    trends = sentiment_series(conn, "month", by_theme=True, **filters)
    trend_rows = [] if trends.empty else [
        {"month": r.period.strftime("%Y-%m"), "theme_id": r.theme_id, "theme_name": THEME_BY_ID[r.theme_id].name,
         "mentions": int(r.total), "negative": int(r.negative), "negative_rate": float(r.negative_rate)}
        for r in trends.itertuples()]
    no_theme = conn.execute(text(f"""SELECT count(*) FROM v_feedback_analytics fa WHERE TRUE {where}
            AND NOT EXISTS (SELECT 1 FROM v_current_themes ct WHERE ct.feedback_id = fa.feedback_id)"""), params).scalar()
    return {"frequency": freq, "negative_by_theme": sorted(freq, key=lambda d: -d["negative"]),
            "by_product": by_product, "monthly_trends": trend_rows, "feedback_without_theme": no_theme}


def overview(conn: Connection) -> dict:
    counts = dict(conn.execute(text("""
        SELECT count(*) AS total_feedback,
               count(*) FILTER (WHERE processing_status NOT IN ('DUPLICATE','QUARANTINED')) AS analysable,
               count(*) FILTER (WHERE processing_status = 'DUPLICATE') AS duplicates,
               count(*) FILTER (WHERE processing_status = 'QUARANTINED') AS quarantined,
               count(*) FILTER (WHERE source_type = 'SURVEY_RESPONSE') AS survey_responses,
               count(*) FILTER (WHERE source_type = 'REVIEW') AS reviews,
               count(*) FILTER (WHERE source_type = 'SUPPORT_MESSAGE') AS support_messages,
               min(feedback_date) AS first_feedback, max(feedback_date) AS last_feedback
        FROM feedback_items""")).mappings().one())
    sentiment = sentiment_breakdown(conn)["overall"]
    themes = theme_breakdown(conn)["frequency"][:5]
    weekly = sentiment_series(conn, "week")
    neg_trend = [] if weekly.empty else [
        {"week": r.period.isoformat(), "total": int(r.total), "negative": int(r.negative),
         "negative_rate": float(r.negative_rate)} for r in weekly.itertuples()]
    actions = [dict(r) for r in conn.execute(text("""
        SELECT a.action_id, a.title, a.priority, a.status, a.evidence_count, a.owner, a.due_date,
               a.product_id, a.theme_id
        FROM action_items a WHERE a.status IN ('OPEN','IN_PROGRESS','BLOCKED') AND a.priority IN ('CRITICAL','HIGH')
        ORDER BY CASE a.priority WHEN 'CRITICAL' THEN 0 ELSE 1 END, a.evidence_count DESC LIMIT 10""")).mappings()]
    action_counts = dict(conn.execute(text("""
        SELECT count(*) AS total, count(*) FILTER (WHERE status IN ('OPEN','IN_PROGRESS','BLOCKED')) AS active,
               count(*) FILTER (WHERE status IN ('OPEN','IN_PROGRESS','BLOCKED') AND priority IN ('CRITICAL','HIGH')) AS high_priority_active
        FROM action_items""")).mappings().one())
    dq = [dict(r) for r in conn.execute(text("""SELECT rule_code, severity, count(*) AS issues
                                               FROM data_quality_issues GROUP BY 1, 2 ORDER BY 3 DESC""")).mappings()]
    alerts = detect_shifts(conn, "week", evidence_limit=5)
    last_run = conn.execute(text("""SELECT run_id, run_type, status, started_at, finished_at FROM pipeline_runs
                                    WHERE run_type IN ('FULL','INFERENCE') ORDER BY run_id DESC LIMIT 1""")).mappings().first()
    return {"counts": counts, "sentiment": sentiment, "top_themes": themes, "negative_trend_weekly": neg_trend,
            "high_priority_actions": actions, "action_counts": action_counts,
            "data_quality": {"by_rule": dq, "total_issues": sum(d["issues"] for d in dq),
                             "quarantined": counts["quarantined"], "duplicates": counts["duplicates"]},
            "recent_alerts": alerts[-5:], "models": available_models(),
            "last_run": dict(last_run) if last_run else None}


def date_bounds(conn: Connection) -> tuple[date | None, date | None]:
    r = conn.execute(text(f"""SELECT min((feedback_date AT TIME ZONE '{TZ}')::date),
                                     max((feedback_date AT TIME ZONE '{TZ}')::date) FROM feedback_items""")).one()
    return r[0], r[1]
