"""Deterministic, explainable business action generation and lifecycle.

Rules (thresholds configurable via settings / .env), evaluated over the
lookback window ending at the latest feedback date:

R1 NEGATIVE_THEME_CONCENTRATION  (product x theme)
    negative_count >= ACTION_MIN_NEGATIVE_COUNT
    AND negative_share >= ACTION_MIN_NEGATIVE_SHARE
    AND lift = negative_share / theme-wide negative_share >= ACTION_MIN_LIFT
R2 RECURRING_COMPLAINT            (product x theme)
    negative_count >= ACTION_MIN_NEGATIVE_COUNT in each of >= 3 consecutive months
    AND negative_share >= theme-wide share
R3 SENTIMENT_SHIFT                (theme, all products)
    a weekly trend alert (see trend_service) inside the lookback window

De-duplication: at most one *active* (OPEN / IN_PROGRESS / BLOCKED) action per
(product or ALL, theme) - enforced by a partial unique index. When a later
run finds the same issue, new evidence is appended to the existing action
(documented in its metrics history) instead of creating a duplicate.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.config import get_settings
from app.core.enums import ACTION_TRANSITIONS, ACTIVE_ACTION_STATUSES, is_valid_transition
from app.core.logging_config import get_logger
from app.core.taxonomy import THEME_BY_ID
from app.services.trend_service import TZ, TrendThresholds, detect_shifts
from app.utils import id_generator as ids

log = get_logger("actions")

OWNERS = {
    "PRODUCT_QUALITY": "Quality Engineering", "PRICING": "Pricing & Revenue", "DELIVERY": "Logistics Operations",
    "CUSTOMER_SUPPORT": "Support Operations", "USER_EXPERIENCE": "Product Design", "PAYMENT": "Payments Team",
    "PRODUCT_AVAILABILITY": "Supply Chain Planning", "PACKAGING": "Fulfilment & Packaging",
    "REFUNDS_RETURNS": "Returns & Refunds Operations", "TECHNICAL_ISSUES": "App Engineering",
}
RECOMMENDATIONS = {
    "PRODUCT_QUALITY": "Quarantine affected batches, run root-cause analysis with the manufacturer and offer proactive replacements.",
    "PRICING": "Review recent price changes against competitors and consider targeted discounts or price communication.",
    "DELIVERY": "Escalate with courier partners, add SLA monitoring per region and notify affected customers proactively.",
    "CUSTOMER_SUPPORT": "Audit recent support interactions, refresh agent scripts and add a callback/escalation path.",
    "USER_EXPERIENCE": "Run usability review of the affected flows and prioritise fixes in the next sprint.",
    "PAYMENT": "Reconcile failed/double charges with the payment gateway and auto-reverse affected transactions.",
    "PRODUCT_AVAILABILITY": "Revisit demand forecast and safety stock; enable back-in-stock notifications.",
    "PACKAGING": "Inspect packaging standards at fulfilment centres and switch to protective packaging for fragile SKUs.",
    "REFUNDS_RETURNS": "Clear the refund backlog, publish refund status in-app and tighten pickup scheduling.",
    "TECHNICAL_ISSUES": "Triage crash/login reports, consider rolling back the latest release and add release monitoring.",
}


def _due(priority: str, today: date) -> date:
    s = get_settings()
    days = {"CRITICAL": s.action_due_days_critical, "HIGH": s.action_due_days_high,
            "MEDIUM": s.action_due_days_medium, "LOW": s.action_due_days_low}[priority]
    return today + timedelta(days=days)


def _window(conn: Connection) -> tuple[date, date]:
    end = conn.execute(text(f"SELECT max((feedback_date AT TIME ZONE '{TZ}')::date) FROM v_feedback_analytics")).scalar()
    if end is None:
        raise RuntimeError("No scored feedback available - run the predict step first.")
    return end - timedelta(days=get_settings().action_lookback_days - 1), end


def _theme_frame(conn: Connection, start: date, end: date) -> pd.DataFrame:
    sql = f"""SELECT fa.feedback_id, fa.product_id, ct.theme_id, fa.sentiment_label,
                     date_trunc('month', fa.feedback_date AT TIME ZONE '{TZ}')::date AS month
              FROM v_feedback_analytics fa JOIN v_current_themes ct ON ct.feedback_id = fa.feedback_id
              WHERE (fa.feedback_date AT TIME ZONE '{TZ}')::date BETWEEN :s AND :e"""
    return pd.DataFrame(conn.execute(text(sql), {"s": start, "e": end}).mappings().all())


def evaluate_rules(conn: Connection) -> list[dict]:
    """Return candidate actions (not yet persisted) with metrics and evidence."""
    s = get_settings()
    start, end = _window(conn)
    df = _theme_frame(conn, start, end)
    if df.empty:
        return []
    df["neg"] = (df.sentiment_label == "NEGATIVE").astype(int)
    theme_share = df.groupby("theme_id")["neg"].mean().to_dict()
    products = {r[0]: r[1] for r in conn.execute(text("SELECT product_id, product_name FROM products"))}
    candidates: list[dict] = []

    grp = df.groupby(["product_id", "theme_id"]).agg(total=("neg", "size"), negative=("neg", "sum")).reset_index()
    grp["share"] = grp.negative / grp.total
    grp["baseline"] = grp.theme_id.map(theme_share)
    grp["lift"] = grp.share / grp.baseline.clip(lower=1e-6)
    for r in grp.itertuples():
        if r.negative >= s.action_min_negative_count and r.share >= s.action_min_negative_share and r.lift >= s.action_min_lift:
            if r.share >= 0.75 and r.negative >= 3 * s.action_min_negative_count:
                prio = "CRITICAL"
            elif r.share >= 0.6 or r.negative >= 3 * s.action_min_negative_count:
                prio = "HIGH"
            else:
                prio = "MEDIUM"
            theme = THEME_BY_ID[r.theme_id]
            ev = df[(df.product_id == r.product_id) & (df.theme_id == r.theme_id) & (df.neg == 1)].feedback_id
            candidates.append({
                "rule_code": "NEGATIVE_THEME_CONCENTRATION", "product_id": r.product_id, "theme_id": r.theme_id,
                "priority": prio, "negative_count": int(r.negative), "total_count": int(r.total),
                "negative_share": round(float(r.share), 5),
                "title": f"{theme.name} complaints concentrated on {products[r.product_id]}",
                "description": (f"{int(r.negative)} of {int(r.total)} {theme.name.lower()} mentions for "
                                f"{products[r.product_id]} were negative ({r.share:.0%}) between {start} and {end}, "
                                f"{r.lift:.1f}x the {theme.name.lower()} negative share across all products "
                                f"({r.baseline:.0%})."),
                "metrics": {"lift": round(float(r.lift), 3), "theme_baseline_share": round(float(r.baseline), 4),
                            "thresholds": {"min_negative_count": s.action_min_negative_count,
                                           "min_negative_share": s.action_min_negative_share,
                                           "min_lift": s.action_min_lift}},
                "evidence": sorted(ev.tolist()), "window_start": start, "window_end": end,
            })

    monthly = df.groupby(["product_id", "theme_id", "month"]).agg(negative=("neg", "sum"), total=("neg", "size")).reset_index()
    for (pid, tid), g in monthly.groupby(["product_id", "theme_id"]):
        g = g.sort_values("month")
        hot = (g.negative >= s.action_min_negative_count) & (g.negative / g.total >= theme_share[tid])
        streak = best = 0
        for v in hot:
            streak = streak + 1 if v else 0
            best = max(best, streak)
        if best >= 3:
            theme = THEME_BY_ID[tid]
            sub = df[(df.product_id == pid) & (df.theme_id == tid)]
            ev = sub[sub.neg == 1].feedback_id
            candidates.append({
                "rule_code": "RECURRING_COMPLAINT", "product_id": pid, "theme_id": tid, "priority": "MEDIUM",
                "negative_count": int(sub.neg.sum()), "total_count": int(len(sub)),
                "negative_share": round(float(sub.neg.mean()), 5),
                "title": f"Recurring {theme.name.lower()} complaints for {products[pid]}",
                "description": (f"{theme.name} complaints for {products[pid]} reached at least "
                                f"{s.action_min_negative_count} negatives per month for {best} consecutive months "
                                f"({start} to {end})."),
                "metrics": {"consecutive_months": best, "monthly": [
                    {"month": m.isoformat(), "negative": int(n), "total": int(t)}
                    for m, n, t in zip(g.month, g.negative, g.total)]},
                "evidence": sorted(ev.tolist()), "window_start": start, "window_end": end,
            })

    th = TrendThresholds.from_settings()
    shifts = [a for a in detect_shifts(conn, "week", th, include_overall=False, evidence_limit=None)
              if a["period_start"] >= start.isoformat()]
    for tid in sorted({a["theme_id"] for a in shifts}):
        alerts = [a for a in shifts if a["theme_id"] == tid]
        peak = max(alerts, key=lambda a: a["z_score"])
        theme = THEME_BY_ID[tid]
        evidence = sorted({fid for a in alerts for fid in a["evidence_feedback_ids"]})
        candidates.append({
            "rule_code": "SENTIMENT_SHIFT", "product_id": None, "theme_id": tid,
            "priority": "CRITICAL" if peak["z_score"] >= 2 * th.z_threshold else "HIGH",
            "negative_count": sum(a["negative_count"] for a in alerts), "total_count": sum(a["volume"] for a in alerts),
            "negative_share": round(sum(a["negative_count"] for a in alerts) / max(1, sum(a["volume"] for a in alerts)), 5),
            "title": f"Spike in negative {theme.name.lower()} feedback",
            "description": (f"{len(alerts)} week(s) with unusual negative {theme.name.lower()} feedback; peak week "
                            f"{peak['period_start']} at {peak['negative_rate']:.0%} vs baseline "
                            f"{peak['baseline_rate']:.0%} (z={peak['z_score']})."),
            "metrics": {"alerts": [{k: a[k] for k in ("period_start", "negative_rate", "baseline_rate", "z_score",
                                                      "volume")} for a in alerts],
                        "thresholds": {"z": th.z_threshold, "min_increase": th.min_increase,
                                       "min_volume": th.min_volume}},
            "evidence": evidence, "window_start": date.fromisoformat(alerts[0]["period_start"]),
            "window_end": date.fromisoformat(alerts[-1]["period_end"]),
        })
    order = {"NEGATIVE_THEME_CONCENTRATION": 0, "RECURRING_COMPLAINT": 1, "SENTIMENT_SHIFT": 2}
    candidates.sort(key=lambda c: (order[c["rule_code"]], c["product_id"] or "", c["theme_id"]))
    return candidates


def _insert_evidence(conn: Connection, action_id: str, feedback_ids: list[str], run_id: str | None) -> int:
    if not feedback_ids:
        return 0
    res = conn.execute(text("""INSERT INTO action_evidence (action_id, feedback_id, run_id)
                               SELECT :a, unnest(CAST(:f AS text[])), :r ON CONFLICT DO NOTHING"""),
                       {"a": action_id, "f": feedback_ids, "r": run_id})
    return res.rowcount


def _refresh_evidence_count(conn: Connection, action_id: str) -> int:
    return conn.execute(text("""UPDATE action_items SET evidence_count =
                                   (SELECT count(*) FROM action_evidence WHERE action_id = :a), updated_at = now()
                                WHERE action_id = :a RETURNING evidence_count"""), {"a": action_id}).scalar_one()


def create_action(conn: Connection, c: dict, origin: str, run_id: str | None, changed_by: str) -> str:
    seq = conn.execute(text("SELECT nextval('action_item_seq')")).scalar_one()
    action_id = ids.action_id(int(seq))
    today = conn.execute(text("SELECT current_date")).scalar_one()
    theme_code = THEME_BY_ID[c["theme_id"]].code
    conn.execute(text("""
        INSERT INTO action_items (action_id, title, description, product_id, theme_id, rule_code, priority,
            evidence_count, negative_count, total_count, negative_share, metrics, recommended_action, owner, status,
            origin, run_id, window_start, window_end, due_date, duplicate_reason)
        VALUES (:id, :title, :desc, :pid, :tid, :rule, :prio, 0, :neg, :tot, :share, CAST(:metrics AS jsonb),
                :rec, :owner, 'OPEN', :origin, :run, :ws, :we, :due, :dup_reason)"""), {
        "dup_reason": c.get("duplicate_reason"),
        "id": action_id, "title": c["title"][:200], "desc": c["description"], "pid": c.get("product_id"),
        "tid": c["theme_id"], "rule": c["rule_code"], "prio": c["priority"], "neg": c.get("negative_count", 0),
        "tot": c.get("total_count", 0), "share": c.get("negative_share"),
        "metrics": json.dumps(c.get("metrics", {}), default=str),
        "rec": c.get("recommended_action") or RECOMMENDATIONS[theme_code],
        "owner": c.get("owner") or OWNERS[theme_code], "origin": origin, "run": run_id,
        "ws": c.get("window_start"), "we": c.get("window_end"), "due": c.get("due_date") or _due(c["priority"], today)})
    conn.execute(text("""INSERT INTO action_status_events (action_id, from_status, to_status, changed_by, note)
                         VALUES (:a, NULL, 'OPEN', :by, :note)"""),
                 {"a": action_id, "by": changed_by, "note": f"Created by {c['rule_code']}"})
    _insert_evidence(conn, action_id, c.get("evidence", []), run_id)
    _refresh_evidence_count(conn, action_id)
    return action_id


def generate_actions(engine: Engine, run_id: str | None = None) -> dict:
    """Evaluate rules and persist new actions / refresh existing active ones (one transaction)."""
    created, refreshed, merged = [], [], []
    with engine.begin() as conn:
        candidates = evaluate_rules(conn)
        for c in candidates:
            existing = conn.execute(text("""
                SELECT action_id, rule_code, metrics FROM action_items
                WHERE COALESCE(product_id, 'ALL') = COALESCE(CAST(:pid AS varchar), 'ALL') AND theme_id = :tid
                  AND status IN ('OPEN','IN_PROGRESS','BLOCKED') FOR UPDATE"""),
                {"pid": c["product_id"], "tid": c["theme_id"]}).mappings().first()
            if existing is None:
                created.append(create_action(conn, c, "RULE", run_id, "action-engine"))
                continue
            added = _insert_evidence(conn, existing["action_id"], c["evidence"], run_id)
            history = (existing["metrics"] or {}).get("history", [])
            history.append({"run_id": run_id, "rule_code": c["rule_code"], "negative_count": c["negative_count"],
                            "negative_share": c["negative_share"], "new_evidence": added})
            conn.execute(text("""UPDATE action_items SET metrics = jsonb_set(metrics, '{history}', CAST(:h AS jsonb))
                                 WHERE action_id = :a"""), {"h": json.dumps(history), "a": existing["action_id"]})
            _refresh_evidence_count(conn, existing["action_id"])
            (merged if existing["rule_code"] != c["rule_code"] else refreshed).append(existing["action_id"])
    summary = {"candidates": len(candidates), "created": len(created), "refreshed_existing": len(refreshed),
               "merged_into_existing_issue": len(merged), "created_ids": created}
    log.info("Action generation: %s", {k: v for k, v in summary.items() if k != "created_ids"})
    return summary


class ActionTransitionError(ValueError):
    pass


def change_status(conn: Connection, action_id: str, to_status: str, changed_by: str, note: str | None) -> dict:
    """Validate and apply a lifecycle transition; history is appended, never overwritten."""
    row = conn.execute(text("SELECT status FROM action_items WHERE action_id = :a FOR UPDATE"),
                       {"a": action_id}).first()
    if row is None:
        raise LookupError(action_id)
    current = row[0]
    if not is_valid_transition(ACTION_TRANSITIONS, current, to_status):
        raise ActionTransitionError(
            f"Transition {current} -> {to_status} not allowed; allowed: {sorted(ACTION_TRANSITIONS[current])}")
    conn.execute(text("UPDATE action_items SET status = :s, updated_at = now() WHERE action_id = :a"),
                 {"s": to_status, "a": action_id})
    conn.execute(text("""INSERT INTO action_status_events (action_id, from_status, to_status, changed_by, note)
                         VALUES (:a, :f, :t, :by, :n)"""),
                 {"a": action_id, "f": current, "t": to_status, "by": changed_by, "n": note})
    return {"action_id": action_id, "from_status": current, "to_status": to_status}


def find_active_duplicate(conn: Connection, product_id: str | None, theme_id: str) -> str | None:
    return conn.execute(text("""SELECT action_id FROM action_items
                                WHERE COALESCE(product_id, 'ALL') = COALESCE(CAST(:p AS varchar), 'ALL')
                                  AND theme_id = :t AND status = ANY(:active)"""),
                        {"p": product_id, "t": theme_id, "active": [s.value for s in ACTIVE_ACTION_STATUSES]}).scalar()
