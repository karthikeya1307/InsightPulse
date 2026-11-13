"""Feedback queries (list/detail) and transactional creation of new feedback."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.taxonomy import THEME_BY_ID
from app.services.model_registry import ModelNotTrainedError, active_model_info
from app.services.validation_service import Issue, store_issues
from app.utils import id_generator as ids
from app.utils.text_preprocessing import detect_language, find_pii, normalize_text, redact_pii, text_hash

TZ = "Asia/Kolkata"


class FeedbackValidationError(ValueError):
    """Request is well-formed but violates a relationship/business rule (HTTP 422)."""


class FeedbackConflictError(ValueError):
    """Request conflicts with current state (HTTP 409)."""


BASE_SELECT = f"""
SELECT fi.feedback_id, fi.source_type, fi.source_record_id, fi.customer_id, fi.product_id, p.product_name,
       fi.channel_id, c.channel_name, fi.feedback_date, fi.rating, fi.language,
       COALESCE(fi.redacted_text, fi.feedback_text) AS text, fi.processing_status,
       cs.sentiment_label, cs.confidence, cs.needs_human_review, cs.review_reason, cs.model_version,
       cs.prediction_method, cs.run_id
FROM feedback_items fi
JOIN products p ON p.product_id = fi.product_id
JOIN channels c ON c.channel_id = fi.channel_id
LEFT JOIN v_current_sentiment cs ON cs.feedback_id = fi.feedback_id
"""


def _row_to_summary(r: dict, themes: dict[str, list[dict]]) -> dict:
    sentiment = None
    if r["sentiment_label"]:
        sentiment = {"label": r["sentiment_label"], "confidence": float(r["confidence"]),
                     "needs_human_review": r["needs_human_review"], "review_reason": r["review_reason"],
                     "model_version": r["model_version"], "prediction_method": r["prediction_method"],
                     "run_id": r["run_id"]}
    out = {k: r[k] for k in ("feedback_id", "source_type", "source_record_id", "customer_id", "product_id",
                             "product_name", "channel_id", "channel_name", "feedback_date", "rating", "language",
                             "text", "processing_status")}
    out["sentiment"] = sentiment
    out["themes"] = themes.get(r["feedback_id"], [])
    return out


def _themes_for(db: Session, feedback_ids: list[str]) -> dict[str, list[dict]]:
    if not feedback_ids:
        return {}
    rows = db.execute(text("""SELECT feedback_id, theme_id, confidence FROM v_current_themes
                              WHERE feedback_id = ANY(:ids) ORDER BY confidence DESC"""), {"ids": feedback_ids})
    out: dict[str, list[dict]] = {}
    for fid, tid, conf in rows:
        out.setdefault(fid, []).append({"theme_id": tid, "theme_name": THEME_BY_ID[tid].name,
                                        "confidence": float(conf)})
    return out


def list_feedback(db: Session, page: int, page_size: int, product_id: str | None = None,
                  channel_id: str | None = None, source_type: str | None = None, sentiment: str | None = None,
                  theme_id: str | None = None, language: str | None = None, status: str | None = None,
                  date_from: date | None = None, date_to: date | None = None, q: str | None = None,
                  needs_review: bool | None = None, include_excluded: bool = True) -> tuple[list[dict], int]:
    where, params = ["TRUE"], {}
    simple = {"fi.product_id": product_id, "fi.channel_id": channel_id, "fi.source_type": source_type,
              "cs.sentiment_label": sentiment, "fi.language": language, "fi.processing_status": status}
    for i, (col, val) in enumerate(simple.items()):
        if val:
            where.append(f"{col} = :p{i}")
            params[f"p{i}"] = val
    if theme_id:
        where.append("EXISTS (SELECT 1 FROM v_current_themes ct WHERE ct.feedback_id = fi.feedback_id AND ct.theme_id = :theme)")
        params["theme"] = theme_id
    if date_from:
        where.append(f"(fi.feedback_date AT TIME ZONE '{TZ}')::date >= :df")
        params["df"] = date_from
    if date_to:
        where.append(f"(fi.feedback_date AT TIME ZONE '{TZ}')::date <= :dt")
        params["dt"] = date_to
    if q:
        where.append("COALESCE(fi.redacted_text, fi.feedback_text) ILIKE :q")
        params["q"] = f"%{q}%"
    if needs_review is not None:
        where.append("cs.needs_human_review = :nr")
        params["nr"] = needs_review
    if not include_excluded:
        where.append("fi.processing_status NOT IN ('DUPLICATE','QUARANTINED')")
    w = " AND ".join(where)
    total = db.execute(text(f"""SELECT count(*) FROM feedback_items fi
                                LEFT JOIN v_current_sentiment cs ON cs.feedback_id = fi.feedback_id WHERE {w}"""),
                       params).scalar_one()
    rows = db.execute(text(f"{BASE_SELECT} WHERE {w} ORDER BY fi.feedback_date DESC, fi.feedback_id "
                           f"LIMIT :lim OFFSET :off"), {**params, "lim": page_size, "off": (page - 1) * page_size}
                      ).mappings().all()
    themes = _themes_for(db, [r["feedback_id"] for r in rows])
    return [_row_to_summary(dict(r), themes) for r in rows], total


def get_feedback(db: Session, feedback_id: str) -> dict | None:
    r = db.execute(text(f"{BASE_SELECT} WHERE fi.feedback_id = :f"), {"f": feedback_id}).mappings().first()
    if r is None:
        return None
    out = _row_to_summary(dict(r), _themes_for(db, [feedback_id]))
    st, sid = r["source_type"], r["source_record_id"]
    if st == "REVIEW":
        src = db.execute(text("""SELECT review_id, review_title, verified_purchase, helpful_votes FROM reviews
                                 WHERE review_id = :s"""), {"s": sid}).mappings().first()
    elif st == "SURVEY_RESPONSE":
        src = db.execute(text("""SELECT sr.response_id, sr.survey_id, s.title AS survey_title, s.survey_type,
                                        sr.nps_score FROM survey_responses sr JOIN surveys s USING (survey_id)
                                 WHERE sr.response_id = :s"""), {"s": sid}).mappings().first()
    else:
        src = db.execute(text("""SELECT m.message_id, m.ticket_id, m.message_seq, t.subject, t.status AS ticket_status,
                                        t.priority AS ticket_priority FROM support_messages m
                                 JOIN support_tickets t USING (ticket_id) WHERE m.message_id = :s"""),
                         {"s": sid}).mappings().first()
    out["source_details"] = {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in dict(src).items()}
    preds = db.execute(text("""SELECT sp.run_id, sp.sentiment_label, sp.confidence, sp.model_version, sp.predicted_at,
                                      sp.needs_human_review, sp.review_reason, sp.prediction_method
                               FROM sentiment_predictions sp WHERE sp.feedback_id = :f ORDER BY sp.run_id DESC"""),
                       {"f": feedback_id}).mappings().all()
    theme_hist: dict[str, list[dict]] = {}
    for run_id, tid, conf in db.execute(text("""SELECT run_id, theme_id, confidence FROM feedback_themes
                                                WHERE feedback_id = :f ORDER BY confidence DESC"""), {"f": feedback_id}):
        theme_hist.setdefault(run_id, []).append({"theme_id": tid, "theme_name": THEME_BY_ID[tid].name,
                                                  "confidence": float(conf)})
    out["prediction_history"] = [dict(p, confidence=float(p["confidence"]), themes=theme_hist.get(p["run_id"], []))
                                 for p in preds]
    out["data_quality_issues"] = [dict(i) for i in db.execute(text("""
        SELECT issue_id, rule_code, severity, message, resolution_action, detected_at FROM data_quality_issues
        WHERE record_id IN (:f, :s) OR details->>'feedback_id' = :f ORDER BY issue_id"""),
        {"f": feedback_id, "s": sid}).mappings()]
    out["linked_actions"] = [dict(a) for a in db.execute(text("""
        SELECT a.action_id, a.title, a.priority, a.status FROM action_evidence e
        JOIN action_items a USING (action_id) WHERE e.feedback_id = :f ORDER BY a.action_id"""),
        {"f": feedback_id}).mappings()]
    out["status_history"] = [dict(e) for e in db.execute(text("""
        SELECT from_state, to_state, changed_by, reason, run_id, changed_at FROM state_change_events
        WHERE entity_type = 'FEEDBACK_ITEM' AND entity_id = :f ORDER BY changed_at, event_id"""),
        {"f": feedback_id}).mappings()]
    return out


# ------------------------------------------------------------------ create --
def _exists(db: Session, table: str, col: str, value: str) -> dict | None:
    return db.execute(text(f"SELECT * FROM {table} WHERE {col} = :v"), {"v": value}).mappings().first()


def create_feedback(db: Session, req, triggered_by: str = "api", run_id: str | None = None) -> str:
    """Create source record + feedback item, validate, score. Caller commits.

    ``run_id``: attach the item to an existing INFERENCE run (bulk upload) instead of creating one."""
    now = getattr(req, "feedback_date", None) or datetime.now(timezone.utc)
    if not _exists(db, "customers", "customer_id", req.customer_id):
        raise FeedbackValidationError(f"customer_id {req.customer_id} does not exist")
    product_id, channel_id = req.product_id, req.channel_id
    if req.source_type == "SUPPORT_MESSAGE":
        ticket = _exists(db, "support_tickets", "ticket_id", req.ticket_id)
        if ticket is None:
            raise FeedbackValidationError(f"ticket_id {req.ticket_id} does not exist")
        if ticket["customer_id"] != req.customer_id:
            raise FeedbackValidationError("ticket/customer mismatch: ticket belongs to a different customer")
        for name, given, actual in (("product_id", product_id, ticket["product_id"]),
                                    ("channel_id", channel_id, ticket["channel_id"])):
            if given and given != actual:
                raise FeedbackValidationError(f"ticket/{name} mismatch: ticket has {actual}, request has {given}")
        if ticket["status"] == "CLOSED":
            raise FeedbackConflictError(f"ticket {req.ticket_id} is CLOSED")
        product_id, channel_id = ticket["product_id"], ticket["channel_id"]
    product = _exists(db, "products", "product_id", product_id)
    channel = _exists(db, "channels", "channel_id", channel_id)
    if product is None:
        raise FeedbackValidationError(f"product_id {product_id} does not exist")
    if channel is None:
        raise FeedbackValidationError(f"channel_id {channel_id} does not exist")
    supports = {"REVIEW": "supports_reviews", "SURVEY_RESPONSE": "supports_surveys", "SUPPORT_MESSAGE": "supports_support"}
    if not channel[supports[req.source_type]]:
        raise FeedbackValidationError(f"channel {channel_id} does not accept {req.source_type}")
    body = normalize_text(req.text)
    lang = req.language or detect_language(body).language
    lock_key = {"REVIEW": "review", "SURVEY_RESPONSE": req.survey_id, "SUPPORT_MESSAGE": req.ticket_id}[req.source_type]
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"id:{lock_key}"})

    if req.source_type == "REVIEW":
        if now.date() < product["launch_date"]:
            raise FeedbackValidationError(f"feedback_date {now.date()} is before {product_id} launched "
                                          f"({product['launch_date']})")
        dup = db.execute(text("""SELECT review_id FROM reviews WHERE customer_id = :c AND product_id = :p
                                   AND lower(regexp_replace(btrim(review_text), '\\s+', ' ', 'g')) = lower(:x)
                                 ORDER BY review_id LIMIT 1"""),
                         {"c": req.customer_id, "p": product_id, "x": body}).scalar()
        if dup:
            raise FeedbackConflictError(f"Customer already submitted this review text for {product_id} ({dup})")
        n = db.execute(text("SELECT COALESCE(max(substring(review_id from 10)::int), 0) FROM reviews")).scalar_one()
        sid = ids.review_id(n + 1)
        db.execute(text("""INSERT INTO reviews (review_id, customer_id, product_id, channel_id, rating, review_title,
                           review_text, review_date, language, verified_purchase, helpful_votes, created_at)
                           VALUES (:id, :c, :p, :ch, :r, :t, :x, :d, :l, :v, 0, :d)"""),
                   {"id": sid, "c": req.customer_id, "p": product_id, "ch": channel_id, "r": req.rating,
                    "t": req.review_title or body[:60], "x": body, "d": now, "l": lang, "v": req.verified_purchase})
    elif req.source_type == "SURVEY_RESPONSE":
        survey = _exists(db, "surveys", "survey_id", req.survey_id)
        if survey is None:
            raise FeedbackValidationError(f"survey_id {req.survey_id} does not exist")
        if survey["status"] != "ACTIVE":
            raise FeedbackConflictError(f"survey {req.survey_id} is {survey['status']}, not accepting responses")
        if survey["channel_id"] != channel_id:
            raise FeedbackValidationError(f"survey is distributed via {survey['channel_id']}, not {channel_id}")
        if survey["product_id"] and survey["product_id"] != product_id:
            raise FeedbackValidationError(f"survey is about {survey['product_id']}, not {product_id}")
        n = db.execute(text("SELECT count(*) FROM survey_responses WHERE survey_id = :s"), {"s": req.survey_id}).scalar_one()
        if n >= 999:
            raise FeedbackConflictError("survey response id space exhausted (999 per survey)")
        sid = ids.response_id(req.survey_id, n + 1)
        db.execute(text("""INSERT INTO survey_responses (response_id, survey_id, customer_id, product_id, channel_id,
                           response_date, rating, nps_score, response_text, language, created_at)
                           VALUES (:id, :s, :c, :p, :ch, :d, :r, :nps, :x, :l, :d)"""),
                   {"id": sid, "s": req.survey_id, "c": req.customer_id, "p": product_id, "ch": channel_id,
                    "d": now, "r": req.rating, "nps": req.nps_score, "x": body, "l": lang})
    else:
        n = db.execute(text("SELECT COALESCE(max(message_seq), 0) FROM support_messages WHERE ticket_id = :t"),
                       {"t": req.ticket_id}).scalar_one()
        sid = ids.message_id(req.ticket_id, n + 1)
        db.execute(text("""INSERT INTO support_messages (message_id, ticket_id, message_seq, sender_type, message_text,
                           sent_at, language, created_at) VALUES (:id, :t, :n, 'CUSTOMER', :x, :d, :l, :d)"""),
                   {"id": sid, "t": req.ticket_id, "n": n + 1, "x": body, "d": now, "l": lang})
        db.execute(text("UPDATE support_tickets SET message_count = message_count + 1, updated_at = now() "
                        "WHERE ticket_id = :t"), {"t": req.ticket_id})

    fid = ids.feedback_id(sid)
    db.execute(text("""INSERT INTO feedback_items (feedback_id, source_type, source_record_id, survey_response_id,
                       review_id, support_message_id, customer_id, product_id, channel_id, feedback_date, rating,
                       feedback_text, language, text_hash, dataset_split, processing_status)
                       VALUES (:f, :st, :sid, :sr, :rv, :sm, :c, :p, :ch, :d, :r, :x, :l, :h, 'UNASSIGNED', 'INGESTED')"""),
               {"f": fid, "st": req.source_type, "sid": sid,
                "sr": sid if req.source_type == "SURVEY_RESPONSE" else None,
                "rv": sid if req.source_type == "REVIEW" else None,
                "sm": sid if req.source_type == "SUPPORT_MESSAGE" else None, "c": req.customer_id, "p": product_id,
                "ch": channel_id, "d": now, "r": req.rating, "x": body, "l": lang, "h": text_hash(body)})
    if run_id is None:
        run_id = create_inference_run(db, triggered_by, {"feedback_id": fid})
    _event(db, fid, None, "INGESTED", "api create", run_id)

    # Inline validation: privacy and duplicate checks
    cust = _exists(db, "customers", "customer_id", req.customer_id)
    full_name = f"{cust['first_name']} {cust['last_name']}"
    pii = find_pii(body, [full_name])
    cur = db.connection().connection.driver_connection.cursor()
    if pii:
        db.execute(text("UPDATE feedback_items SET redacted_text = :t, processing_status = 'QUARANTINED' "
                        "WHERE feedback_id = :f"), {"t": redact_pii(body, [full_name]), "f": fid})
        _event(db, fid, "INGESTED", "QUARANTINED", "PRIVACY_LEAK", run_id)
        store_issues(cur, [Issue("PRIVACY_LEAK", "HIGH", "FEEDBACK_ITEM", fid,
                                 f"Customer identity found in text ({', '.join(sorted({p.kind for p in pii}))}); "
                                 "record quarantined", "feedback_text", {"pii_types": sorted({p.kind for p in pii})})],
                     run_id)
        return fid
    if req.source_type == "SURVEY_RESPONSE":
        dup = db.execute(text("""SELECT response_id FROM survey_responses
                                 WHERE survey_id = :s AND customer_id = :c AND response_id <> :id
                                   AND lower(regexp_replace(btrim(response_text), '\\s+', ' ', 'g')) = lower(:x)
                                 ORDER BY response_id LIMIT 1"""),
                         {"s": req.survey_id, "c": req.customer_id, "id": sid, "x": body}).scalar()
        if dup:
            db.execute(text("UPDATE feedback_items SET processing_status = 'DUPLICATE' WHERE feedback_id = :f"), {"f": fid})
            _event(db, fid, "INGESTED", "DUPLICATE", "DUPLICATE", run_id)
            store_issues(cur, [Issue("DUPLICATE_RESPONSE", "MEDIUM", "SURVEY_RESPONSE", sid,
                                     f"Duplicate of {dup} (same survey, customer and text)",
                                     details={"duplicate_of": dup})], run_id)
            return fid
    db.execute(text("UPDATE feedback_items SET processing_status = 'VALIDATED' WHERE feedback_id = :f"), {"f": fid})
    _event(db, fid, "INGESTED", "VALIDATED", "api inline validation", run_id)
    if active_model_info("sentiment") and active_model_info("themes"):
        from app.services.sentiment_service import score_and_store

        try:
            _, _, sv, tv = score_and_store(db.connection().connection.driver_connection,
                                           [{"feedback_id": fid, "text": body, "rating": req.rating}], run_id)
        except ModelNotTrainedError:
            return fid
        db.execute(text("UPDATE feedback_items SET processing_status = 'PROCESSED' WHERE feedback_id = :f"), {"f": fid})
        db.execute(text("UPDATE pipeline_runs SET sentiment_model_version = :s, theme_model_version = :t, "
                        "summary = jsonb_set(summary, '{scored}', to_jsonb(COALESCE((summary->>'scored')::int, 0) + 1)) "
                        "WHERE run_id = :r"), {"s": sv, "t": tv, "r": run_id})
        _event(db, fid, "VALIDATED", "PROCESSED", "scored", run_id)
    return fid


def create_inference_run(db: Session, triggered_by: str, parameters: dict) -> str:
    return db.execute(text("""INSERT INTO pipeline_runs (run_id, run_type, status, triggered_by, parameters, finished_at)
                              VALUES ('RUN-' || lpad(nextval('pipeline_run_seq')::text, 6, '0'), 'INFERENCE',
                                      'SUCCEEDED', :by, CAST(:p AS jsonb), now()) RETURNING run_id"""),
                      {"by": triggered_by, "p": json.dumps(parameters)}).scalar_one()


def _event(db: Session, fid: str, frm: str | None, to: str, reason: str, run_id: str | None) -> None:
    db.execute(text("""INSERT INTO state_change_events (entity_type, entity_id, from_state, to_state, changed_by,
                       reason, run_id, changed_at) VALUES ('FEEDBACK_ITEM', :f, :fr, :to, 'api', :r, :run, clock_timestamp())"""),
               {"f": fid, "fr": frm, "to": to, "r": reason, "run": run_id})
