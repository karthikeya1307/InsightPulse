"""Labelled edge-case injection, applied AFTER the valid baseline is generated.

Each injected anomaly is recorded in the anomaly registry (``ground_truth.csv``)
with: issue id, record id, issue type, expected detection, severity and the
ground-truth label. Unrelated rows are never touched.

Injected issue types
--------------------
LABEL_CONTRADICTION  annotation label flipped against the text, no annotator note
DUPLICATE_RESPONSE   survey response re-submitted with a new response id
PRIVACY_LEAK         customer's own e-mail / phone / name written into feedback text
INVALID_THEME        annotation carries a theme label outside the taxonomy
TARGET_LEAKAGE       a TEST-split record's label copied into the training annotations
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.utils import id_generator as ids
from generator import text_templates as T
from generator.config import ScaleConfig

EXPECTED = {
    # issue_type: (validation rule expected to fire, expected resolution, severity)
    "LABEL_CONTRADICTION": ("LABEL_CONTRADICTION", "EXCLUDED_FROM_TRAINING", "HIGH"),
    "DUPLICATE_RESPONSE": ("DUPLICATE_RESPONSE", "FLAGGED", "MEDIUM"),
    "PRIVACY_LEAK": ("PRIVACY_LEAK", "QUARANTINED", "HIGH"),
    "INVALID_THEME": ("THEME_TAXONOMY", "REJECTED", "MEDIUM"),
    "TARGET_LEAKAGE": ("TARGET_LEAKAGE", "EXCLUDED_FROM_TRAINING", "CRITICAL"),
}
TEXT_FIELD = {"SURVEY_RESPONSE": "response_text", "REVIEW": "review_text", "SUPPORT_MESSAGE": "message_text"}
OPPOSITE = {"POSITIVE": "NEGATIVE", "NEGATIVE": "POSITIVE"}


class AnomalyInjector:
    def __init__(self, cfg: ScaleConfig, rng: random.Random):
        self.cfg, self.rng = cfg, rng
        self.registry: list[dict] = []
        self._seq = ids.IdSequence("anomaly")
        self.touched: set[str] = set()  # source/annotation ids already used by an anomaly

    def _count(self, issue: str, population: int) -> int:
        return min(population, max(self.cfg.anomaly_min_count, round(self.cfg.anomaly_rates[issue] * population)))

    def _record(self, issue: str, record_id: str, record_type: str, gt_label: str, related: str, desc: str) -> None:
        rule, resolution, severity = EXPECTED[issue]
        self.registry.append({
            "issue_id": self._seq.next(), "record_id": record_id, "record_type": record_type,
            "issue_type": issue, "expected_detection": f"{rule} -> {resolution}",
            "expected_rule": rule, "expected_resolution": resolution, "severity": severity,
            "ground_truth_label": gt_label, "related_record_id": related, "description": desc,
        })

    # ------------------------------------------------------------------ #
    def duplicate_responses(self, responses: list[dict], splits: dict[str, str]) -> list[dict]:
        """Append duplicates (new ids) of existing responses. Returns the new rows."""
        per_survey: dict[str, int] = {}
        for r in responses:
            per_survey[r["survey_id"]] = per_survey.get(r["survey_id"], 0) + 1
        candidates = [r for r in responses if per_survey[r["survey_id"]] < 999]
        picks = self.rng.sample(candidates, self._count("DUPLICATE_RESPONSE", len(responses)))
        new_rows = []
        for orig in sorted(picks, key=lambda r: r["response_id"]):
            per_survey[orig["survey_id"]] += 1
            new_id = ids.response_id(orig["survey_id"], per_survey[orig["survey_id"]])
            when = datetime.fromisoformat(orig["response_date"]) + timedelta(minutes=self.rng.randint(3, 45))
            dup = {k: v for k, v in orig.items() if k != "_meta"}
            dup.update(response_id=new_id, response_date=when.isoformat(), created_at=when.isoformat())
            dup["_meta"] = dict(orig["_meta"], duplicate_of=orig["response_id"])
            new_rows.append(dup)
            splits[new_id] = splits[orig["response_id"]]
            self.touched.update({orig["response_id"], new_id})
            self._record("DUPLICATE_RESPONSE", new_id, "SURVEY_RESPONSE", f"DUPLICATE_OF:{orig['response_id']}",
                         orig["response_id"], "Same customer re-submitted an identical response to the same survey.")
        return new_rows

    def privacy_leaks(self, feedback_rows: list[tuple[str, dict]], customers: dict[str, dict]) -> None:
        """Write the customer's own (synthetic) identity into the feedback text, in place."""
        pool = [(st, r) for st, r in feedback_rows if _sid(st, r) not in self.touched]
        for st, row in sorted(self.rng.sample(pool, self._count("PRIVACY_LEAK", len(feedback_rows))),
                              key=lambda x: _sid(*x)):
            cust_id = row["customer_id"] if "customer_id" in row else row["_customer_id"]
            c = customers[cust_id]
            tmpl = self.rng.choice(T.PII_SENTENCES)
            row[TEXT_FIELD[st]] += tmpl.format(email=c["email"], phone=c["phone"],
                                                full_name=f"{c['first_name']} {c['last_name']}")
            kinds = [k for k, token in (("EMAIL", "{email}"), ("PHONE", "{phone}"), ("NAME", "{full_name}")) if token in tmpl]
            sid = _sid(st, row)
            self.touched.add(sid)
            self._record("PRIVACY_LEAK", sid, st, "PII:" + "|".join(kinds), cust_id,
                         "Customer identity (synthetic) leaked in free text.")

    def label_contradictions(self, annotations: list[dict], rated: set[str]) -> None:
        pool = [a for a in annotations if a["sentiment_label"] in OPPOSITE and a["_source_id"] in rated
                and a["_source_id"] not in self.touched and not a["annotation_note"]]
        for a in sorted(self.rng.sample(pool, self._count("LABEL_CONTRADICTION", len(annotations))),
                        key=lambda x: x["annotation_id"]):
            truth = a["sentiment_label"]
            a["sentiment_label"] = OPPOSITE[truth]
            self.touched.add(a["annotation_id"])
            self._record("LABEL_CONTRADICTION", a["annotation_id"], "FEEDBACK_ANNOTATION", truth, a["_source_id"],
                         f"Annotation says {a['sentiment_label']} but text is {truth}; no explanation recorded.")

    def invalid_themes(self, annotations: list[dict]) -> None:
        pool = [a for a in annotations if a["annotation_id"] not in self.touched]
        for a in sorted(self.rng.sample(pool, self._count("INVALID_THEME", len(annotations))),
                        key=lambda x: x["annotation_id"]):
            bad = self.rng.choice(T.INVALID_THEME_LABELS)
            a["theme_labels"] = a["theme_labels"] + [bad]
            self.touched.add(a["annotation_id"])
            self._record("INVALID_THEME", a["annotation_id"], "FEEDBACK_ANNOTATION", f"INVALID_THEME:{bad}",
                         a["_source_id"], f"Theme label '{bad}' is not in the allowed taxonomy.")

    def target_leakage(self, test_records: list[dict], annotations: list[dict], next_ann: ids.IdSequence) -> None:
        """Copy TEST-split labels into the training annotations (declared as TRAIN)."""
        pool = [r for r in test_records if r["source_id"] not in self.touched]
        for r in sorted(self.rng.sample(pool, self._count("TARGET_LEAKAGE", len(test_records))),
                        key=lambda x: x["source_id"]):
            ann_id = next_ann.next()
            annotations.append({
                "annotation_id": ann_id, "feedback_id": ids.feedback_id(r["source_id"]),
                "sentiment_label": r["meta"]["true_sentiment"], "theme_labels": list(r["meta"]["true_theme_ids"]),
                "annotator_id": "EXPORT-BATCH-7", "annotation_note": "",
                "annotated_at": r["date"], "declared_split": "TRAIN", "_source_id": r["source_id"],
            })
            self.touched.update({ann_id, r["source_id"]})
            self._record("TARGET_LEAKAGE", ann_id, "FEEDBACK_ANNOTATION", f"TEST_RECORD_IN_TRAINING:{r['source_id']}",
                         r["source_id"], "Human-reviewed TEST label leaked into the training annotations.")


def _sid(source_type: str, row: dict) -> str:
    return row[{"SURVEY_RESPONSE": "response_id", "REVIEW": "review_id", "SUPPORT_MESSAGE": "message_id"}[source_type]]


