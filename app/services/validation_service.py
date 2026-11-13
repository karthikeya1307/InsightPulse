"""Data validation engine.

Two layers:

* **File-level** (``validate_files``) - runs on the raw CSVs before loading.
  Violations of hard constraints (duplicate PK, broken FK, bad enum, missing
  required field, unparsable date, rating out of bounds, malformed id) cause
  the record to be REJECTED and written to the exceptions folder.
* **Database-level** (``run_db_validation``) - relational/business checks on
  the loaded data: duplicates, date ordering, lifecycle transitions,
  arithmetic reconciliation, source consistency, theme taxonomy, privacy,
  label contradictions and ground-truth/target leakage. Each rule has a
  documented resolution (flag, quarantine, reject label, exclude from training).

Nothing is deleted: originals stay in the source tables, and every status
change is written to ``state_change_events``.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core import enums as E
from app.core.logging_config import get_logger
from app.core.taxonomy import ALLOWED_THEME_IDS, SENTIMENT_CODES, THEME_BY_ID
from app.utils import id_generator as ids
from app.utils.text_preprocessing import find_pii, lexicon_score, redact_pii

log = get_logger("validation")

RULES: dict[str, dict] = {
    "PK_UNIQUENESS": {"desc": "Primary keys are unique within each file/table", "resolution": "REJECTED"},
    "FK_INTEGRITY": {"desc": "Every foreign key references an existing parent record", "resolution": "REJECTED"},
    "ID_FORMAT": {"desc": "Identifiers follow the documented conventions", "resolution": "REJECTED"},
    "ENUM_VALUE": {"desc": "Categorical fields contain allowed values only", "resolution": "REJECTED"},
    "REQUIRED_FIELD": {"desc": "Required fields are present", "resolution": "REJECTED"},
    "INVALID_DATE": {"desc": "Dates parse and are not in the future", "resolution": "REJECTED"},
    "RATING_BOUNDS": {"desc": "Ratings 1-5, NPS 0-10, counters non-negative", "resolution": "REJECTED"},
    "DATE_ORDER": {"desc": "Chronology across related records is consistent", "resolution": "FLAGGED"},
    "DUPLICATE_RESPONSE": {"desc": "Same customer submitted identical text to the same survey", "resolution": "FLAGGED"},
    "DUPLICATE_FEEDBACK": {"desc": "Same customer posted identical review text for the same product", "resolution": "FLAGGED"},
    "LIFECYCLE_TRANSITION": {"desc": "State changes follow the allowed state machine and match current state", "resolution": "FLAGGED"},
    "ARITHMETIC_RECONCILIATION": {"desc": "Derived counts equal counts recomputed from source records", "resolution": "FLAGGED"},
    "SOURCE_CONSISTENCY": {"desc": "Feedback/ticket/survey/channel/product relationships agree", "resolution": "FLAGGED"},
    "THEME_TAXONOMY": {"desc": "Theme labels belong to the controlled taxonomy", "resolution": "REJECTED"},
    "PRIVACY_LEAK": {"desc": "Free text contains customer identity (e-mail, phone, own full name)", "resolution": "QUARANTINED"},
    "LABEL_CONTRADICTION": {"desc": "Annotation label contradicts the text/rating without explanation", "resolution": "EXCLUDED_FROM_TRAINING"},
    "TARGET_LEAKAGE": {"desc": "Evaluation (TEST) records or labels present in model training input", "resolution": "EXCLUDED_FROM_TRAINING"},
}


@dataclass
class Issue:
    rule_code: str
    severity: str
    entity_type: str
    record_id: str
    message: str
    field_name: str | None = None
    details: dict = field(default_factory=dict)
    resolution: str | None = None

    def resolved(self) -> str:
        return self.resolution or RULES[self.rule_code]["resolution"]


@dataclass
class FileReport:
    issues: list[Issue]
    rejected: dict[str, set[str]]
    checked: dict[str, int]


# ---------------------------------------------------------------------------
# File-level validation
# ---------------------------------------------------------------------------
PK = {"channels": "channel_id", "products": "product_id", "customers": "customer_id", "surveys": "survey_id",
      "survey_responses": "response_id", "reviews": "review_id", "support_tickets": "ticket_id",
      "support_messages": "message_id", "annotations": "annotation_id"}
ID_ENTITY = {"channels": "channel", "products": "product", "customers": "customer", "surveys": "survey",
             "survey_responses": "survey_response", "reviews": "review", "support_tickets": "ticket",
             "support_messages": "support_message", "annotations": "annotation"}
ENTITY_TYPE = {"channels": "CHANNEL", "products": "PRODUCT", "customers": "CUSTOMER", "surveys": "SURVEY",
               "survey_responses": "SURVEY_RESPONSE", "reviews": "REVIEW", "support_tickets": "SUPPORT_TICKET",
               "support_messages": "SUPPORT_MESSAGE", "annotations": "FEEDBACK_ANNOTATION"}

_E = lambda enum_cls: {e.value for e in enum_cls}  # noqa: E731
SPECS: dict[str, dict] = {
    "channels": {"required": ["channel_code", "channel_name", "channel_type"],
                 "enums": {"channel_type": _E(E.ChannelType)}},
    "products": {"required": ["sku", "product_name", "category", "brand", "unit_price", "launch_date"],
                 "enums": {"lifecycle_status": _E(E.ProductStatus)},
                 "dates": ["launch_date", "discontinued_date"], "positive": ["unit_price"]},
    "customers": {"required": ["first_name", "last_name", "email", "signup_date"],
                  "enums": {"preferred_language": _E(E.Language), "segment": _E(E.CustomerSegment),
                            "lifecycle_status": _E(E.CustomerStatus)}, "dates": ["signup_date"],
                  "unique": ["email"]},
    "surveys": {"required": ["title", "channel_id", "start_date", "end_date"],
                "enums": {"survey_type": _E(E.SurveyType), "status": _E(E.SurveyStatus)},
                "fks": {"product_id": ("products", True), "channel_id": ("channels", False)},
                "dates": ["start_date", "end_date"], "order": [("start_date", "end_date")],
                "planned": ("end_date",)},  # a planned end date may lie in the future
    "survey_responses": {"required": ["survey_id", "customer_id", "product_id", "channel_id", "response_date",
                                      "rating", "response_text"],
                         "enums": {"language": _E(E.Language)},
                         "fks": {"survey_id": ("surveys", False), "customer_id": ("customers", False),
                                 "product_id": ("products", False), "channel_id": ("channels", False)},
                         "dates": ["response_date"], "ints": {"rating": (1, 5), "nps_score": (0, 10)},
                         "parent_in_id": "survey_id"},
    "reviews": {"required": ["customer_id", "product_id", "channel_id", "rating", "review_text", "review_date"],
                "enums": {"language": _E(E.Language)},
                "fks": {"customer_id": ("customers", False), "product_id": ("products", False),
                        "channel_id": ("channels", False)},
                "dates": ["review_date"], "ints": {"rating": (1, 5), "helpful_votes": (0, 10**9)}},
    "support_tickets": {"required": ["customer_id", "product_id", "channel_id", "category_theme_id", "opened_at"],
                        "enums": {"priority": _E(E.TicketPriority), "status": _E(E.TicketStatus)},
                        "fks": {"customer_id": ("customers", False), "product_id": ("products", False),
                                "channel_id": ("channels", False)},
                        "dates": ["opened_at", "resolved_at", "closed_at"],
                        "order": [("opened_at", "resolved_at"), ("resolved_at", "closed_at")],
                        "ints": {"message_count": (0, 10**6)}},
    "support_messages": {"required": ["ticket_id", "message_seq", "sender_type", "message_text", "sent_at"],
                         "enums": {"sender_type": _E(E.SenderType), "language": _E(E.Language)},
                         "fks": {"ticket_id": ("support_tickets", False)}, "dates": ["sent_at"],
                         "ints": {"message_seq": (1, 999)}, "parent_in_id": "ticket_id"},
    "annotations": {"required": ["feedback_id", "sentiment_label", "annotator_id", "annotated_at", "declared_split"],
                    "enums": {"sentiment_label": set(SENTIMENT_CODES), "declared_split": {"TRAIN", "VALIDATION"}},
                    "dates": ["annotated_at"]},
}


def pk_of(table: str, row: dict) -> str:
    return row.get(PK[table]) or ""


def _parse_dt(value: str) -> datetime | date:
    return datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)


def _as_dt(v: datetime | date) -> datetime:
    return v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time())


def validate_files(tables: dict[str, list[dict]], annotations: list[dict], splits: dict[str, str],
                   master_themes: list[dict], ticket_events: list[dict]) -> FileReport:
    """Validate raw rows. Returns issues and the set of rejected PKs per table."""
    issues: list[Issue] = []
    rejected: dict[str, set[str]] = defaultdict(set)
    checked: dict[str, int] = {}
    all_tables = dict(tables, annotations=annotations)
    today = datetime.now().date()
    id_cache: dict[str, set[str]] = {}

    def reject(table: str, rid: str, rule: str, msg: str, fld: str | None = None, sev: str = "HIGH", **details):
        issues.append(Issue(rule, sev, ENTITY_TYPE[table], rid or "<missing>", msg, fld, details))
        rejected[table].add(rid)

    # Order matters: parents are validated before children so rejection cascades.
    for table in ["channels", "products", "customers", "surveys", "survey_responses", "reviews",
                  "support_tickets", "support_messages", "annotations"]:
        rows, spec = all_tables[table], SPECS[table]
        checked[table] = len(rows)
        seen: set[str] = set()
        uniques: dict[str, set] = {c: set() for c in spec.get("unique", [])}
        for r in rows:
            rid = pk_of(table, r)
            if not rid:
                reject(table, rid, "REQUIRED_FIELD", f"Missing primary key {PK[table]}", PK[table])
                continue
            if rid in seen:
                reject(table, rid, "PK_UNIQUENESS", f"Duplicate primary key {rid}", PK[table], "CRITICAL")
                continue
            seen.add(rid)
            if not ids.is_valid_id(ID_ENTITY[table], rid):
                reject(table, rid, "ID_FORMAT", f"Malformed identifier {rid!r}", PK[table])
            for col in spec.get("required", []):
                if r.get(col) in (None, ""):
                    reject(table, rid, "REQUIRED_FIELD", f"Required field {col} is missing", col)
            for col, allowed in spec.get("enums", {}).items():
                if r.get(col) is not None and r[col] not in allowed:
                    reject(table, rid, "ENUM_VALUE", f"{col}={r[col]!r} not in {sorted(allowed)}", col)
            parsed: dict[str, datetime | date] = {}
            for col in spec.get("dates", []):
                if r.get(col):
                    try:
                        parsed[col] = _parse_dt(r[col])
                        if col not in spec.get("planned", ()) and _as_dt(parsed[col]).date() > today:
                            reject(table, rid, "INVALID_DATE", f"{col} is in the future", col)
                    except ValueError:
                        reject(table, rid, "INVALID_DATE", f"Unparsable date {col}={r[col]!r}", col)
            for a, b in spec.get("order", []):
                if a in parsed and b in parsed and _as_dt(parsed[b]) < _as_dt(parsed[a]):
                    reject(table, rid, "DATE_ORDER", f"{b} precedes {a}", b)
            for col, (lo, hi) in spec.get("ints", {}).items():
                if r.get(col) is not None:
                    try:
                        v = int(r[col])
                        if not lo <= v <= hi:
                            raise ValueError
                    except ValueError:
                        reject(table, rid, "RATING_BOUNDS", f"{col}={r[col]!r} outside [{lo}, {hi}]", col)
            for col in spec.get("positive", []):
                try:
                    if float(r[col]) <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    reject(table, rid, "RATING_BOUNDS", f"{col} must be > 0", col)
            for col in uniques:
                if r.get(col) in uniques[col]:
                    reject(table, rid, "PK_UNIQUENESS", f"Duplicate unique value in {col}", col)
                uniques[col].add(r.get(col))
            for col, (parent, optional) in spec.get("fks", {}).items():
                val = r.get(col)
                if val is None and optional:
                    continue
                if parent not in id_cache:
                    id_cache[parent] = {pk_of(parent, p) for p in all_tables[parent]}
                parent_ids = id_cache[parent]
                if val not in parent_ids or val in rejected[parent]:
                    reject(table, rid, "FK_INTEGRITY", f"{col}={val!r} has no valid parent in {parent}", col,
                           "CRITICAL")
            if "parent_in_id" in spec and r.get(spec["parent_in_id"]):
                try:
                    if ids.parent_of(rid) != r[spec["parent_in_id"]]:
                        reject(table, rid, "SOURCE_CONSISTENCY",
                               f"Id {rid} does not embed parent {r[spec['parent_in_id']]}", PK[table])
                except ids.IdError:
                    pass  # already reported as ID_FORMAT
        if table == "support_tickets":
            for r in rows:
                if r.get("category_theme_id") not in ALLOWED_THEME_IDS:
                    reject(table, pk_of(table, r), "THEME_TAXONOMY",
                           f"Ticket category {r.get('category_theme_id')!r} outside taxonomy", "category_theme_id")

    # Annotation -> feedback FK (feedback ids are derived from loadable source records)
    valid_feedback = {ids.feedback_id(pk_of(t, r)) for t in ("survey_responses", "reviews")
                      for r in tables[t] if pk_of(t, r) not in rejected[t]}
    valid_feedback |= {ids.feedback_id(r["message_id"]) for r in tables["support_messages"]
                       if r["sender_type"] == "CUSTOMER" and r["message_id"] not in rejected["support_messages"]}
    for a in annotations:
        if a["annotation_id"] not in rejected["annotations"] and a["feedback_id"] not in valid_feedback:
            reject("annotations", a["annotation_id"], "FK_INTEGRITY",
                   f"feedback_id {a['feedback_id']!r} does not exist", "feedback_id", "CRITICAL")
    # Dataset split file must cover every feedback record
    for fid in sorted(valid_feedback):
        if fid[3:] not in splits:
            issues.append(Issue("REQUIRED_FIELD", "LOW", "FEEDBACK_ITEM", fid, "No dataset split assigned; "
                                "record will be UNASSIGNED", "dataset_split", resolution="FLAGGED"))
    # Master theme file vs controlled taxonomy
    checked["themes"] = len(master_themes)
    for t in master_themes:
        ref = THEME_BY_ID.get(t["theme_id"])
        if ref is None or ref.name != t["theme_name"]:
            issues.append(Issue("THEME_TAXONOMY", "HIGH", "THEME", t["theme_id"],
                                f"Theme {t['theme_id']} / {t['theme_name']!r} not in controlled taxonomy",
                                resolution="REJECTED"))
    checked["ticket_status_events"] = len(ticket_events)
    return FileReport(issues, dict(rejected), checked)


# ---------------------------------------------------------------------------
# Persistence of issues
# ---------------------------------------------------------------------------
UPSERT_SQL = """
INSERT INTO data_quality_issues (issue_id, run_id, rule_code, severity, entity_type, record_id, field_name,
                                 message, details, resolution_action)
VALUES ('DQ-' || lpad(nextval('dq_issue_seq')::text, 6, '0'), %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (rule_code, entity_type, record_id)
DO UPDATE SET last_seen_at = now(), run_id = EXCLUDED.run_id, details = EXCLUDED.details,
              message = EXCLUDED.message
"""


def store_issues(cur, issues: list[Issue], run_id: str | None) -> int:
    """Upsert issues using a DB-API cursor (psycopg)."""
    rows = [(run_id, i.rule_code, i.severity, i.entity_type, i.record_id[:40], i.field_name, i.message[:2000],
             json.dumps(i.details, default=str), i.resolved()) for i in issues]
    if rows:
        cur.executemany(UPSERT_SQL, rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Database-level validation
# ---------------------------------------------------------------------------
def _q(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def check_duplicates(conn: Connection) -> list[Issue]:
    out = []
    for r in _q(conn, """
        SELECT response_id, original, survey_id, customer_id FROM (
          SELECT response_id, survey_id, customer_id,
                 first_value(response_id) OVER w AS original, row_number() OVER w AS rn
          FROM survey_responses
          WINDOW w AS (PARTITION BY survey_id, customer_id,
                       md5(lower(regexp_replace(btrim(response_text), '\\s+', ' ', 'g')))
                       ORDER BY response_date, response_id)) x
        WHERE rn > 1"""):
        out.append(Issue("DUPLICATE_RESPONSE", "MEDIUM", "SURVEY_RESPONSE", r["response_id"],
                         f"Duplicate of {r['original']} (same survey, customer and text)",
                         details={"duplicate_of": r["original"], "survey_id": r["survey_id"],
                                  "customer_id": r["customer_id"]}))
    for r in _q(conn, """
        SELECT feedback_id, original FROM (
          SELECT feedback_id, first_value(feedback_id) OVER w AS original, row_number() OVER w AS rn
          FROM feedback_items WHERE source_type = 'REVIEW'
          WINDOW w AS (PARTITION BY customer_id, product_id, text_hash ORDER BY feedback_date, feedback_id)) x
        WHERE rn > 1"""):
        out.append(Issue("DUPLICATE_FEEDBACK", "LOW", "FEEDBACK_ITEM", r["feedback_id"],
                         f"Identical review text by the same customer for the same product as {r['original']}",
                         details={"duplicate_of": r["original"]}))
    return out


def check_date_order(conn: Connection) -> list[Issue]:
    checks = [
        ("SURVEY_RESPONSE", "response_id", "response_date", "Response outside survey window",
         """SELECT sr.response_id AS id, sr.response_date::text AS v FROM survey_responses sr
            JOIN surveys s USING (survey_id)
            WHERE (sr.response_date AT TIME ZONE 'Asia/Kolkata')::date NOT BETWEEN s.start_date AND s.end_date"""),
        ("FEEDBACK_ITEM", "feedback_id", "feedback_date", "Feedback before customer signup",
         """SELECT fi.feedback_id AS id, fi.feedback_date::text AS v FROM feedback_items fi
            JOIN customers c USING (customer_id)
            WHERE (fi.feedback_date AT TIME ZONE 'Asia/Kolkata')::date < c.signup_date"""),
        ("FEEDBACK_ITEM", "feedback_id", "feedback_date", "Feedback outside product lifecycle",
         """SELECT fi.feedback_id AS id, fi.feedback_date::text AS v FROM feedback_items fi
            JOIN products p USING (product_id)
            WHERE (fi.feedback_date AT TIME ZONE 'Asia/Kolkata')::date < p.launch_date
               OR (p.discontinued_date IS NOT NULL
                   AND (fi.feedback_date AT TIME ZONE 'Asia/Kolkata')::date > p.discontinued_date)"""),
        ("SUPPORT_MESSAGE", "message_id", "sent_at", "Message sent before ticket was opened",
         """SELECT m.message_id AS id, m.sent_at::text AS v FROM support_messages m
            JOIN support_tickets t USING (ticket_id) WHERE m.sent_at < t.opened_at"""),
        ("SUPPORT_MESSAGE", "message_id", "sent_at", "Message sequence not chronological",
         """SELECT id, v FROM (SELECT message_id AS id, sent_at::text AS v,
                   sent_at < lag(sent_at) OVER (PARTITION BY ticket_id ORDER BY message_seq) AS bad
            FROM support_messages) x WHERE bad"""),
        ("FEEDBACK_ANNOTATION", "annotation_id", "annotated_at", "Annotated before feedback was created",
         """SELECT a.annotation_id AS id, a.annotated_at::text AS v FROM feedback_annotations a
            JOIN feedback_items fi USING (feedback_id) WHERE a.annotated_at < fi.feedback_date"""),
    ]
    out = []
    for entity, _pk, fld, msg, sql in checks:
        for r in _q(conn, sql):
            out.append(Issue("DATE_ORDER", "MEDIUM", entity, r["id"], msg, fld, {"value": r["v"]}))
    return out


def _check_chain(events: list[dict], machine: dict, current: str) -> str | None:
    prev = None
    for e in events:
        frm = e["from_state"] or None
        if frm != prev:
            return f"event from_state {frm} does not continue previous state {prev}"
        if e["to_state"] not in machine.get(prev, frozenset()):
            return f"illegal transition {prev} -> {e['to_state']}"
        prev = e["to_state"]
    if prev != current:
        return f"latest event state {prev} != current status {current}"
    return None


def check_lifecycle(conn: Connection) -> list[Issue]:
    out = []
    events: dict[str, list[dict]] = defaultdict(list)
    for e in _q(conn, """SELECT entity_id, from_state, to_state FROM state_change_events
                         WHERE entity_type = 'SUPPORT_TICKET' ORDER BY entity_id, changed_at, event_id"""):
        events[e["entity_id"]].append(e)
    for t in _q(conn, "SELECT ticket_id, status, resolved_at IS NOT NULL AS has_resolved FROM support_tickets"):
        err = _check_chain(events.get(t["ticket_id"], []), E.TICKET_TRANSITIONS, t["status"])
        if err:
            out.append(Issue("LIFECYCLE_TRANSITION", "MEDIUM", "SUPPORT_TICKET", t["ticket_id"], err))
    action_events: dict[str, list[dict]] = defaultdict(list)
    for e in _q(conn, "SELECT action_id, from_status AS from_state, to_status AS to_state FROM action_status_events "
                      "ORDER BY action_id, changed_at, event_id"):
        action_events[e["action_id"]].append(e)
    machine = {None: frozenset({"OPEN"}), **E.ACTION_TRANSITIONS}
    for a in _q(conn, "SELECT action_id, status FROM action_items"):
        err = _check_chain(action_events.get(a["action_id"], []), machine, a["status"])
        if err:
            out.append(Issue("LIFECYCLE_TRANSITION", "MEDIUM", "ACTION_ITEM", a["action_id"], err))
    for r in _q(conn, """
        SELECT fi.feedback_id, fi.processing_status, ev.to_state FROM feedback_items fi
        LEFT JOIN LATERAL (SELECT to_state FROM state_change_events s
                           WHERE s.entity_type = 'FEEDBACK_ITEM' AND s.entity_id = fi.feedback_id
                           ORDER BY changed_at DESC, event_id DESC LIMIT 1) ev ON TRUE
        WHERE ev.to_state IS DISTINCT FROM fi.processing_status"""):
        out.append(Issue("LIFECYCLE_TRANSITION", "MEDIUM", "FEEDBACK_ITEM", r["feedback_id"],
                         f"latest event state {r['to_state']} != current status {r['processing_status']}"))
    return out


def check_reconciliation(conn: Connection) -> list[Issue]:
    out = []
    for r in _q(conn, """SELECT t.ticket_id, t.message_count, count(m.message_id) AS actual
                         FROM support_tickets t LEFT JOIN support_messages m USING (ticket_id)
                         GROUP BY t.ticket_id HAVING t.message_count <> count(m.message_id)"""):
        out.append(Issue("ARITHMETIC_RECONCILIATION", "MEDIUM", "SUPPORT_TICKET", r["ticket_id"],
                         f"message_count={r['message_count']} but {r['actual']} messages exist", "message_count",
                         {"stored": r["message_count"], "actual": r["actual"]}))
    for r in _q(conn, """SELECT a.action_id, a.evidence_count, count(e.feedback_id) AS actual
                         FROM action_items a LEFT JOIN action_evidence e USING (action_id)
                         GROUP BY a.action_id HAVING a.evidence_count <> count(e.feedback_id)"""):
        out.append(Issue("ARITHMETIC_RECONCILIATION", "MEDIUM", "ACTION_ITEM", r["action_id"],
                         f"evidence_count={r['evidence_count']} but {r['actual']} evidence rows", "evidence_count"))
    row = _q(conn, """SELECT (SELECT count(*) FROM survey_responses) + (SELECT count(*) FROM reviews)
                           + (SELECT count(*) FROM support_messages WHERE sender_type = 'CUSTOMER') AS expected,
                             (SELECT count(*) FROM feedback_items) AS actual""")[0]
    if row["expected"] != row["actual"]:
        out.append(Issue("ARITHMETIC_RECONCILIATION", "HIGH", "DATASET", "feedback_items",
                         f"{row['actual']} feedback items but {row['expected']} source records", details=row))
    for r in _q(conn, """SELECT x.id FROM (
            SELECT response_id AS id FROM survey_responses UNION ALL SELECT review_id FROM reviews
            UNION ALL SELECT message_id FROM support_messages WHERE sender_type = 'CUSTOMER') x
            LEFT JOIN feedback_items fi ON fi.source_record_id = x.id WHERE fi.feedback_id IS NULL"""):
        out.append(Issue("ARITHMETIC_RECONCILIATION", "HIGH", "FEEDBACK_ITEM", r["id"],
                         "Source record has no unified feedback item"))
    return out


def check_source_consistency(conn: Connection) -> list[Issue]:
    sqls = [
        ("FEEDBACK_ITEM", "Feedback attributes differ from survey response source", """
         SELECT fi.feedback_id AS id FROM feedback_items fi JOIN survey_responses s ON s.response_id = fi.survey_response_id
         WHERE (fi.customer_id, fi.product_id, fi.channel_id, fi.feedback_date)
               IS DISTINCT FROM (s.customer_id, s.product_id, s.channel_id, s.response_date)"""),
        ("FEEDBACK_ITEM", "Feedback attributes differ from review source", """
         SELECT fi.feedback_id AS id FROM feedback_items fi JOIN reviews r ON r.review_id = fi.review_id
         WHERE (fi.customer_id, fi.product_id, fi.channel_id, fi.feedback_date, fi.rating)
               IS DISTINCT FROM (r.customer_id, r.product_id, r.channel_id, r.review_date, r.rating)"""),
        ("FEEDBACK_ITEM", "Feedback attributes differ from ticket/message source", """
         SELECT fi.feedback_id AS id FROM feedback_items fi
         JOIN support_messages m ON m.message_id = fi.support_message_id JOIN support_tickets t USING (ticket_id)
         WHERE (fi.customer_id, fi.product_id, fi.channel_id, fi.feedback_date)
               IS DISTINCT FROM (t.customer_id, t.product_id, t.channel_id, m.sent_at) OR m.sender_type <> 'CUSTOMER'"""),
        ("SURVEY_RESPONSE", "Response channel differs from survey distribution channel", """
         SELECT sr.response_id AS id FROM survey_responses sr JOIN surveys s USING (survey_id)
         WHERE sr.channel_id <> s.channel_id"""),
        ("SURVEY_RESPONSE", "Product-specific survey answered for a different product", """
         SELECT sr.response_id AS id FROM survey_responses sr JOIN surveys s USING (survey_id)
         WHERE s.product_id IS NOT NULL AND sr.product_id <> s.product_id"""),
        ("REVIEW", "Review posted on a channel that does not accept reviews", """
         SELECT r.review_id AS id FROM reviews r JOIN channels c USING (channel_id) WHERE NOT c.supports_reviews"""),
        ("SURVEY", "Survey distributed on a channel that does not support surveys", """
         SELECT s.survey_id AS id FROM surveys s JOIN channels c USING (channel_id) WHERE NOT c.supports_surveys"""),
        ("SUPPORT_TICKET", "Ticket raised on a channel without support", """
         SELECT t.ticket_id AS id FROM support_tickets t JOIN channels c USING (channel_id) WHERE NOT c.supports_support"""),
        ("FEEDBACK_ITEM", "Feedback source type does not match its source id pattern", """
         SELECT feedback_id AS id FROM feedback_items WHERE NOT (
            (source_type = 'SURVEY_RESPONSE' AND source_record_id LIKE 'RESP-SUR-%')
         OR (source_type = 'REVIEW' AND source_record_id LIKE 'REV-%')
         OR (source_type = 'SUPPORT_MESSAGE' AND source_record_id LIKE 'MSG-TKT-%'))"""),
    ]
    out = []
    for entity, msg, sql in sqls:
        for r in _q(conn, sql):
            out.append(Issue("SOURCE_CONSISTENCY", "HIGH", entity, r["id"], msg))
    return out


def check_taxonomy(conn: Connection) -> list[Issue]:
    out = []
    for r in _q(conn, """
        SELECT annotation_id, feedback_id, theme_labels,
               ARRAY(SELECT unnest(theme_labels) EXCEPT SELECT theme_id FROM themes) AS invalid
        FROM feedback_annotations
        WHERE cardinality(ARRAY(SELECT unnest(theme_labels) EXCEPT SELECT theme_id FROM themes)) > 0"""):
        out.append(Issue("THEME_TAXONOMY", "MEDIUM", "FEEDBACK_ANNOTATION", r["annotation_id"],
                         f"Theme label(s) outside taxonomy rejected: {r['invalid']}", "theme_labels",
                         {"invalid_labels": r["invalid"], "feedback_id": r["feedback_id"]}))
    return out


def check_privacy(conn: Connection) -> list[tuple[Issue, str]]:
    """Returns (issue, redacted_text) pairs."""
    out = []
    for r in _q(conn, r"""
        SELECT fi.feedback_id, fi.feedback_text, c.first_name || ' ' || c.last_name AS full_name
        FROM feedback_items fi JOIN customers c USING (customer_id)
        WHERE fi.feedback_text ~* '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'
           OR fi.feedback_text ~ '(^|[^0-9])[6-9][0-9]{9}([^0-9]|$)'
           OR position(lower(c.first_name || ' ' || c.last_name) IN lower(fi.feedback_text)) > 0"""):
        findings = find_pii(r["feedback_text"], [r["full_name"]])
        if not findings:
            continue
        kinds = sorted({f.kind for f in findings})
        issue = Issue("PRIVACY_LEAK", "HIGH", "FEEDBACK_ITEM", r["feedback_id"],
                      f"Customer identity found in text ({', '.join(kinds)}); record quarantined",
                      "feedback_text", {"pii_types": kinds})
        out.append((issue, redact_pii(r["feedback_text"], [r["full_name"]])))
    return out


def check_label_contradictions(conn: Connection) -> tuple[list[Issue], int]:
    """Annotation label vs star rating (rated sources) and vs lexicon (unrated)."""
    out, explained = [], 0
    for r in _q(conn, """
        SELECT a.annotation_id, a.sentiment_label, a.annotation_note, fi.rating, fi.feedback_id
        FROM feedback_annotations a JOIN feedback_items fi USING (feedback_id)
        WHERE fi.rating IS NOT NULL
          AND ((a.sentiment_label = 'POSITIVE' AND fi.rating <= 2) OR (a.sentiment_label = 'NEGATIVE' AND fi.rating >= 4))"""):
        if r["annotation_note"]:
            explained += 1
            continue
        out.append(Issue("LABEL_CONTRADICTION", "HIGH", "FEEDBACK_ANNOTATION", r["annotation_id"],
                         f"Label {r['sentiment_label']} contradicts rating {r['rating']} with no annotator note",
                         "sentiment_label", {"rating": r["rating"], "feedback_id": r["feedback_id"]}))
    for r in _q(conn, """
        SELECT a.annotation_id, a.sentiment_label, a.annotation_note, fi.feedback_text, fi.feedback_id
        FROM feedback_annotations a JOIN feedback_items fi USING (feedback_id)
        WHERE fi.rating IS NULL AND a.sentiment_label = 'POSITIVE'"""):
        pos, neg = lexicon_score(r["feedback_text"])
        if neg >= 2 and pos == 0 and not r["annotation_note"]:
            out.append(Issue("LABEL_CONTRADICTION", "MEDIUM", "FEEDBACK_ANNOTATION", r["annotation_id"],
                             "Label POSITIVE but text has only negative cues", "sentiment_label",
                             {"lexicon_neg": neg, "feedback_id": r["feedback_id"]}))
    return out, explained


def check_target_leakage(conn: Connection) -> list[Issue]:
    out = []
    for r in _q(conn, """
        SELECT a.annotation_id, a.declared_split, fi.dataset_split, fi.feedback_id
        FROM feedback_annotations a JOIN feedback_items fi USING (feedback_id)
        WHERE fi.dataset_split NOT IN ('TRAIN', 'VALIDATION') OR fi.dataset_split <> a.declared_split"""):
        critical = r["dataset_split"] == "TEST"
        out.append(Issue("TARGET_LEAKAGE", "CRITICAL" if critical else "MEDIUM", "FEEDBACK_ANNOTATION",
                         r["annotation_id"],
                         f"Annotation declared {r['declared_split']} but record belongs to {r['dataset_split']}"
                         + (" (evaluation set) - label would leak into training" if critical else ""),
                         "declared_split", {"feedback_id": r["feedback_id"], "actual_split": r["dataset_split"]}))
    return out


# ---------------------------------------------------------------------------
# Orchestration and resolution
# ---------------------------------------------------------------------------
def _transition_feedback(conn: Connection, ids_: list[str], to_state: str, reason: str, run_id: str | None) -> int:
    if not ids_:
        return 0
    allowed_from = [s for s, targets in E.FEEDBACK_TRANSITIONS.items() if s and to_state in targets]
    rows = conn.execute(text("""
        WITH upd AS (
          UPDATE feedback_items fi SET processing_status = :to, updated_at = now()
          FROM (SELECT feedback_id, processing_status AS old FROM feedback_items
                WHERE feedback_id = ANY(:ids) AND processing_status = ANY(:allowed) FOR UPDATE) prev
          WHERE fi.feedback_id = prev.feedback_id
          RETURNING fi.feedback_id, prev.old)
        INSERT INTO state_change_events (entity_type, entity_id, from_state, to_state, changed_by, reason, run_id, changed_at)
        SELECT 'FEEDBACK_ITEM', feedback_id, old, :to, 'validation', :reason, :run, now() FROM upd
        RETURNING 1"""), {"to": to_state, "ids": ids_, "allowed": allowed_from, "reason": reason, "run": run_id})
    return len(rows.fetchall())


def _exclude_annotations(conn: Connection, where_sql: str, reason: str, run_id: str | None, **params) -> int:
    rows = conn.execute(text(f"""
        WITH upd AS (
          UPDATE feedback_annotations SET training_status = 'EXCLUDED', exclusion_reason = :reason, updated_at = now()
          WHERE training_status <> 'EXCLUDED' AND ({where_sql})
          RETURNING annotation_id)
        INSERT INTO state_change_events (entity_type, entity_id, from_state, to_state, changed_by, reason, run_id, changed_at)
        SELECT 'FEEDBACK_ANNOTATION', annotation_id, NULL, 'EXCLUDED', 'validation', :reason, :run, now() FROM upd
        RETURNING 1"""), {"reason": reason, "run": run_id, **params})
    return len(rows.fetchall())


def run_db_validation(engine: Engine, run_id: str | None) -> dict:
    """Run all DB-level checks, persist issues, apply resolutions. One transaction."""
    with engine.begin() as conn:
        issues: list[Issue] = []
        issues += check_duplicates(conn)
        issues += check_date_order(conn)
        issues += check_lifecycle(conn)
        issues += check_reconciliation(conn)
        issues += check_source_consistency(conn)
        issues += check_taxonomy(conn)
        privacy = check_privacy(conn)
        issues += [i for i, _ in privacy]
        contradictions, explained = check_label_contradictions(conn)
        issues += contradictions
        issues += check_target_leakage(conn)

        cur = conn.connection.driver_connection.cursor()
        store_issues(cur, issues, run_id)

        # -- resolutions -------------------------------------------------------
        for issue, redacted in privacy:
            conn.execute(text("UPDATE feedback_items SET redacted_text = :t, updated_at = now() WHERE feedback_id = :f"),
                         {"t": redacted, "f": issue.record_id})
        quarantined = _transition_feedback(conn, [i.record_id for i, _ in privacy], "QUARANTINED",
                                           "PRIVACY_LEAK", run_id)
        dup_ids = [ids.feedback_id(i.record_id) for i in issues if i.rule_code == "DUPLICATE_RESPONSE"]
        dup_ids += [i.record_id for i in issues if i.rule_code == "DUPLICATE_FEEDBACK"]
        duplicates = _transition_feedback(conn, dup_ids, "DUPLICATE", "DUPLICATE", run_id)
        excluded = 0
        for rule in ("LABEL_CONTRADICTION", "TARGET_LEAKAGE"):
            ann_ids = [i.record_id for i in issues if i.rule_code == rule]
            if ann_ids:
                excluded += _exclude_annotations(conn, "annotation_id = ANY(:ids)", rule, run_id, ids=ann_ids)
        excluded += _exclude_annotations(
            conn, "feedback_id IN (SELECT feedback_id FROM feedback_items WHERE processing_status IN ('QUARANTINED','DUPLICATE'))",
            "SOURCE_RECORD_QUARANTINED_OR_DUPLICATE", run_id)
        eligible = conn.execute(text("""
            WITH upd AS (UPDATE feedback_annotations SET training_status = 'ELIGIBLE', updated_at = now()
                         WHERE training_status = 'PENDING' RETURNING annotation_id)
            INSERT INTO state_change_events (entity_type, entity_id, from_state, to_state, changed_by, reason, run_id, changed_at)
            SELECT 'FEEDBACK_ANNOTATION', annotation_id, 'PENDING', 'ELIGIBLE', 'validation', 'passed validation', :run, now()
            FROM upd RETURNING 1"""), {"run": run_id}).rowcount
        pending = [r[0] for r in conn.execute(text(
            "SELECT feedback_id FROM feedback_items WHERE processing_status = 'INGESTED'"))]
        validated = _transition_feedback(conn, pending, "VALIDATED", "passed validation", run_id)

        by_rule: dict[str, int] = defaultdict(int)
        for i in issues:
            by_rule[i.rule_code] += 1
        summary = {
            "issues_detected": len(issues), "issues_by_rule": dict(sorted(by_rule.items())),
            "explained_rating_mismatches": explained,
            "resolutions": {"quarantined": quarantined, "flagged_duplicates": duplicates,
                            "annotations_excluded": excluded, "annotations_eligible": eligible,
                            "feedback_validated": validated},
        }
    log.info("DB validation: %s", json.dumps(summary))
    return summary


def build_validation_summary(engine: Engine, run_id: str | None = None) -> dict:
    """Machine-readable validation summary computed from the current database state."""
    with engine.connect() as conn:
        counts = _q(conn, """SELECT
            (SELECT count(*) FROM feedback_items) AS feedback_items,
            (SELECT count(*) FROM feedback_annotations) AS annotations,
            (SELECT count(*) FROM survey_responses) AS survey_responses,
            (SELECT count(*) FROM reviews) AS reviews,
            (SELECT count(*) FROM support_tickets) AS support_tickets,
            (SELECT count(*) FROM support_messages) AS support_messages,
            (SELECT count(*) FROM customers) AS customers,
            (SELECT count(*) FROM products) AS products,
            (SELECT count(*) FROM surveys) AS surveys,
            (SELECT count(*) FROM action_items) AS action_items""")[0]
        rows = _q(conn, """SELECT rule_code, severity, entity_type, resolution_action, count(*) AS n
                           FROM data_quality_issues GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3""")
        affected = _q(conn, """SELECT count(DISTINCT x.fid) AS n FROM (
                SELECT CASE WHEN entity_type = 'FEEDBACK_ITEM' THEN record_id
                            WHEN entity_type = 'FEEDBACK_ANNOTATION' THEN details->>'feedback_id'
                            WHEN entity_type IN ('SURVEY_RESPONSE','REVIEW','SUPPORT_MESSAGE') THEN 'FB-' || record_id
                       END AS fid FROM data_quality_issues WHERE severity <> 'INFO') x
                WHERE x.fid IS NOT NULL""")[0]["n"]
        status = {r["processing_status"]: r["n"] for r in _q(
            conn, "SELECT processing_status, count(*) AS n FROM feedback_items GROUP BY 1")}
        ann_status = {r["training_status"]: r["n"] for r in _q(
            conn, "SELECT training_status, count(*) AS n FROM feedback_annotations GROUP BY 1")}
    by_rule: dict[str, dict] = {}
    for code, meta in RULES.items():
        rr = [r for r in rows if r["rule_code"] == code]
        n = sum(r["n"] for r in rr)
        by_rule[code] = {"description": meta["desc"], "issues": n, "default_resolution": meta["resolution"],
                         "status": "PASS" if n == 0 else "FAIL",
                         "by_severity": {s: sum(r["n"] for r in rr if r["severity"] == s) for s in
                                         sorted({r["severity"] for r in rr})}}
    total = counts["feedback_items"] or 1
    return {
        "run_id": run_id, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "record_counts": counts, "feedback_status": status, "annotation_training_status": ann_status,
        "checks": by_rule,
        "totals": {
            "issues": sum(r["n"] for r in rows),
            "primary_key_violations": by_rule["PK_UNIQUENESS"]["issues"],
            "foreign_key_violations": by_rule["FK_INTEGRITY"]["issues"],
            "duplicates": by_rule["DUPLICATE_RESPONSE"]["issues"] + by_rule["DUPLICATE_FEEDBACK"]["issues"],
            "invalid_taxonomy": by_rule["THEME_TAXONOMY"]["issues"],
            "privacy_violations": by_rule["PRIVACY_LEAK"]["issues"],
            "label_contradictions": by_rule["LABEL_CONTRADICTION"]["issues"],
            "target_leakage": by_rule["TARGET_LEAKAGE"]["issues"],
            "feedback_items_with_issues": affected,
            "validation_pass_rate": round(1 - affected / total, 6),
            "checks_passed": sum(1 for v in by_rule.values() if v["status"] == "PASS"),
            "checks_total": len(by_rule),
        },
        "issue_breakdown": rows,
    }
