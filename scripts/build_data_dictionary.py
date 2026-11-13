"""Build docs/data_dictionary.md from the live PostgreSQL catalog.

Types, required/optional, keys, uniqueness, defaults and CHECK rules are read
from the database itself, so the dictionary cannot drift from database/schema.sql.
Entity descriptions, cardinalities and units are curated below.

Usage::

    python -m scripts.build_data_dictionary
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import text

from app.core.config import PROJECT_ROOT
from app.core.database import get_engine

OUT = PROJECT_ROOT / "docs" / "data_dictionary.md"

ENTITIES = [
    ("Reference data", [
        ("sentiment_labels", "SentimentLabel", "The four allowed sentiment classes."),
        ("themes", "Theme", "Controlled 10-theme taxonomy (THEME-000001..THEME-000010). Effective-dated."),
    ]),
    ("Master data (generated first)", [
        ("customers", "Customer", "Synthetic customer profile. E-mails use example.com; phones are fake."),
        ("products", "Product", "Product catalogue with lifecycle (ACTIVE / DISCONTINUED)."),
        ("channels", "Channel", "Where feedback arrives; flags say which source types a channel accepts."),
        ("surveys", "Survey", "Survey campaign with an open window; optionally about one product."),
    ]),
    ("Source records (immutable inputs, generated after their parents)", [
        ("survey_responses", "SurveyResponse", "One customer's answer to a survey."),
        ("reviews", "Review", "Product review with a 1-5 star rating."),
        ("support_tickets", "SupportTicket", "Support case; lifecycle history is in state_change_events."),
        ("support_messages", "SupportMessage", "Ordered messages on a ticket (customer or agent)."),
    ]),
    ("Unified feedback and labels", [
        ("feedback_items", "FeedbackItem", "One row per customer-authored source record; the analysis unit."),
        ("feedback_annotations", "FeedbackAnnotation",
         "Training labels (TRAIN/VALIDATION splits only). Raw theme labels are kept as text so "
         "out-of-taxonomy labels are detected rather than lost."),
    ]),
    ("Model runs and append-only prediction history", [
        ("pipeline_runs", "PipelineRun", "Every pipeline / inference run with parameters, summary and model versions."),
        ("sentiment_predictions", "SentimentPrediction", "Append-only; one row per feedback item per run."),
        ("feedback_themes", "FeedbackTheme", "Bridge FeedbackItem N:M Theme, per run (append-only)."),
    ]),
    ("Business actions", [
        ("action_items", "ActionItem", "Current state of a recommended action."),
        ("action_evidence", "ActionEvidence", "Bridge ActionItem N:M FeedbackItem (traceability)."),
        ("action_status_events", "ActionStatusEvent", "Append-only action lifecycle history."),
    ]),
    ("Data quality and history", [
        ("data_quality_issues", "DataQualityIssue", "One row per (rule, entity, record); re-detection updates last_seen_at."),
        ("state_change_events", "StateChangeEvent",
         "Append-only state history for tickets, feedback items and annotations."),
    ]),
]

UNITS = {
    "unit_price": "INR", "rating": "stars (1-5)", "nps_score": "points (0-10)", "confidence": "probability (0-1)",
    "prob_positive": "probability", "prob_negative": "probability", "prob_neutral": "probability",
    "prob_mixed": "probability", "negative_share": "ratio (0-1)", "uncertainty_threshold": "probability",
    "helpful_votes": "count", "message_count": "count", "evidence_count": "count", "negative_count": "count",
    "total_count": "count",
}
NOTES = {
    ("feedback_items", "feedback_id"): "`FB-` + source_record_id",
    ("feedback_items", "text_hash"): "SHA-256 of normalised, case-folded text (duplicate detection)",
    ("feedback_items", "redacted_text"): "Set when PII is found; the API shows this instead of the raw text",
    ("feedback_items", "dataset_split"): "Stratified 70/15/15 split from the generator; UNASSIGNED = live API feedback",
    ("support_tickets", "message_count"): "Derived from support_messages; reconciled by ARITHMETIC_RECONCILIATION",
    ("action_items", "evidence_count"): "Derived from action_evidence; reconciled",
    ("action_items", "product_id"): "NULL = cross-product action",
    ("action_items", "duplicate_reason"): "Required to open a second active action for the same product + theme",
    ("surveys", "product_id"): "Optional: only PRODUCT_FEEDBACK surveys are product-specific",
    ("sentiment_predictions", "needs_human_review"): "Low confidence, non-English low confidence or rating disagreement",
}

CARDINALITY = """| Relationship | Cardinality | Optional? | Implemented by |
|---|---|---|---|
| Customer to Review | 1:N | a customer may have none | `reviews.customer_id` FK |
| Customer to SurveyResponse | 1:N | yes | `survey_responses.customer_id` FK |
| Customer to SupportTicket | 1:N | yes | `support_tickets.customer_id` FK |
| Product to Review | 1:N | yes | `reviews.product_id` FK |
| Product to Survey | 1:N | survey.product_id optional | `surveys.product_id` FK (nullable) |
| Channel to Survey / Review / Ticket | 1:N | | `channel_id` FKs |
| Survey to SurveyResponse | 1:N | | `survey_responses.survey_id` FK; id embeds survey id |
| SupportTicket to SupportMessage | 1:N | | `support_messages.ticket_id` FK; id embeds ticket id |
| SurveyResponse / Review / SupportMessage to FeedbackItem | 1:0..1 | agent messages have none | unique source FK + CHECK that exactly the matching FK is set |
| FeedbackItem to SentimentPrediction | 1:N | | one row per run (history kept) |
| FeedbackItem to Theme | N:M | | bridge `feedback_themes` (per run) |
| FeedbackItem to FeedbackAnnotation | 1:0..1 | TEST items have none | `feedback_annotations.feedback_id` FK |
| ActionItem to FeedbackItem | N:M | | bridge `action_evidence` |
| ActionItem to ActionStatusEvent | 1:N | | append-only events |
| Theme to ActionItem | 1:N | | `action_items.theme_id` FK |
| PipelineRun to predictions / issues / events | 1:N | | `run_id` FKs |
"""


def _pretty(rule: str) -> str:
    rule = re.sub(r"'(-?\d+)'::\w+", r"\1", rule)
    rule = re.sub(r"\((\w+)\) = ANY \(\[(.*?)\]\[\]\)",
                  lambda m: f"{m.group(1)} in {{{m.group(2).replace(chr(39), '')}}}", rule)
    rule = re.sub(r"\((\w+)\) ~ '(.*?)'", r"\1 matches \2", rule)
    return rule.replace(" ~~ ", " LIKE ")


def build() -> str:
    eng = get_engine()
    with eng.connect() as c:
        cols = c.execute(text("""
            SELECT c.table_name, c.column_name, c.data_type, c.character_maximum_length, c.numeric_precision,
                   c.numeric_scale, c.is_nullable, c.column_default, c.is_identity, c.udt_name, c.ordinal_position
            FROM information_schema.columns c JOIN information_schema.tables t
              ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position""")).mappings().all()
        cons = c.execute(text("""
            SELECT rel.relname AS table_name, con.contype, pg_get_constraintdef(con.oid) AS def,
                   ARRAY(SELECT a.attname FROM unnest(con.conkey) k JOIN pg_attribute a
                         ON a.attrelid = con.conrelid AND a.attnum = k)::text[] AS cols
            FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace WHERE n.nspname = 'public'""")).mappings().all()
        idx = c.execute(text("""SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
                                ORDER BY tablename, indexname""")).mappings().all()
        counts = {}
        for t, _, _ in [e for _, g in ENTITIES for e in g]:
            counts[t] = c.execute(text(f"SELECT count(*) FROM {t}")).scalar()

    by_table = defaultdict(list)
    for r in cols:
        by_table[r["table_name"]].append(r)
    col_rules: dict[tuple[str, str], list[str]] = defaultdict(list)
    table_rules: dict[str, list[str]] = defaultdict(list)
    pk, uniq, fk = defaultdict(set), defaultdict(set), {}
    for k in cons:
        t, cs, d = k["table_name"], list(k["cols"]), k["def"]
        if k["contype"] == "p":
            pk[t] |= set(cs) if len(cs) == 1 else set()
            if len(cs) > 1:
                table_rules[t].append(f"Composite primary key ({', '.join(cs)})")
        elif k["contype"] == "u":
            if len(cs) == 1:
                uniq[t].add(cs[0])
            else:
                table_rules[t].append(f"Unique ({', '.join(cs)})")
        elif k["contype"] == "f":
            m = re.search(r"REFERENCES (\w+)\((\w+)\)", d)
            fk[(t, cs[0])] = f"{m.group(1)}.{m.group(2)}" if m else d
        elif k["contype"] == "c":
            rule = re.sub(r"^CHECK \(\((.*)\)\)$", r"\1", d)
            rule = re.sub(r"^CHECK \((.*)\)$", r"\1", rule)
            rule = rule.replace("::character varying", "").replace("::text", "").replace("::bpchar", "")
            rule = re.sub(r"\(ARRAY\[(.*?)\]\)", r"[\1]", rule)
            rule = _pretty(rule)
            if len(cs) == 1:
                col_rules[(t, cs[0])].append(rule)
            else:
                table_rules[t].append(rule)
    for i in idx:
        if "WHERE" in i["indexdef"] and "UNIQUE" in i["indexdef"]:
            d = i["indexdef"].split(" USING btree ")[1]
            d = re.sub(r"::(character varying|text)(\[\])?", "", d).replace("ARRAY", "")
            table_rules[i["tablename"]].append(f"Partial unique index `{i['indexname']}`: {d}")

    def typ(r) -> str:
        if r["data_type"] == "character varying":
            return f"VARCHAR({r['character_maximum_length']})"
        if r["data_type"] == "character":
            return f"CHAR({r['character_maximum_length']})"
        if r["data_type"] == "numeric":
            return f"NUMERIC({r['numeric_precision']},{r['numeric_scale']})"
        if r["data_type"] == "ARRAY":
            return r["udt_name"].lstrip("_").upper() + "[]"
        return {"timestamp with time zone": "TIMESTAMPTZ", "integer": "INTEGER", "smallint": "SMALLINT",
                "bigint": "BIGINT", "boolean": "BOOLEAN", "text": "TEXT", "date": "DATE",
                "jsonb": "JSONB"}.get(r["data_type"], r["data_type"].upper())

    out = ["# Data dictionary", "",
           "> Generated by `python -m scripts.build_data_dictionary` from the live PostgreSQL catalog "
           "(types, nullability, keys, CHECK rules), so it matches `database/schema.sql` exactly. "
           "Row counts are from the database the script ran against.", "",
           "Conventions: every `*_id` is an immutable, zero-padded, deterministic identifier "
           "(see `app/utils/id_generator.py`); timestamps are `TIMESTAMPTZ` (data is generated in "
           "Asia/Kolkata and stored in UTC); `Req.` = NOT NULL.", "",
           "## Relationships and cardinality", "", CARDINALITY,
           "Views: `v_current_sentiment` (latest prediction per item from non-failed runs), "
           "`v_current_themes` (themes of that same run), `v_feedback_analytics` (analysable items: "
           "not DUPLICATE/QUARANTINED, joined with current sentiment).", ""]
    for group, ents in ENTITIES:
        out += [f"## {group}", ""]
        for t, entity, desc in ents:
            out += [f"### {entity} (`{t}`)", "", f"{desc} Rows: **{counts[t]:,}**.", "",
                    "| Field | Type | Req. | Key / unique | Allowed values / validation rule | Units / notes |",
                    "|---|---|---|---|---|---|"]
            for r in by_table[t]:
                n = r["column_name"]
                keys = []
                if n in pk[t]:
                    keys.append("PK")
                if n in uniq[t]:
                    keys.append("unique")
                if (t, n) in fk:
                    keys.append(f"FK to `{fk[(t, n)]}`")
                rules = "; ".join(f"`{x}`" for x in col_rules.get((t, n), []))
                notes = [UNITS.get(n, "")] if UNITS.get(n) else []
                if NOTES.get((t, n)):
                    notes.append(NOTES[(t, n)])
                if r["is_identity"] == "YES":
                    notes.append("identity (auto)")
                elif r["column_default"]:
                    notes.append(f"default `{r['column_default'].split('::')[0]}`")
                out.append(f"| `{n}` | {typ(r)} | {'yes' if r['is_nullable'] == 'NO' else 'no'} | "
                           f"{', '.join(keys)} | {rules.replace('|', '&#124;')} | {'; '.join(notes)} |")
            if table_rules.get(t):
                out += ["", "Table rules:"] + [f"- `{x}`" for x in table_rules[t]]
            out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
