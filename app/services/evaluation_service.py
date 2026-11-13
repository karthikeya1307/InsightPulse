"""Model, data-quality and BI evaluation against the separate ground truth.

Access control
--------------
Ground truth lives in ``data/ground_truth/<scale>/`` and is read ONLY by this
module, and only through :func:`open_ground_truth`, which requires a valid
evaluation token (``EVALUATION_ACCESS_TOKEN``). Training and inference code
never import this module (enforced by tests/test_ground_truth_isolation).

All numbers are computed from stored predictions and the human-reviewed
labels; nothing is hard-coded.
"""
from __future__ import annotations

import csv
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.metrics import (
    accuracy_score, classification_report, confusion_matrix, f1_score, hamming_loss, precision_recall_fscore_support,
)
from sklearn.preprocessing import MultiLabelBinarizer
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.core.taxonomy import SENTIMENT_CODES, THEME_BY_CODE, THEME_BY_ID, THEME_IDS_ORDERED
from app.services.trend_service import detect_shifts
from app.services.validation_service import build_validation_summary
from app.utils.text_preprocessing import lexicon_sentiment


class EvaluationAccessError(PermissionError):
    pass


class EvaluationNotConfiguredError(RuntimeError):
    pass


class EvaluationScaleMismatchError(RuntimeError):
    """The requested ground truth does not belong to the dataset loaded in the database."""


@dataclass(frozen=True)
class GroundTruth:
    scale: str
    evaluation_set: list[dict]
    anomalies: list[dict]
    scenario_events: list[dict]


def verify_token(token: str | None) -> None:
    expected = get_settings().evaluation_access_token
    if not expected:
        raise EvaluationNotConfiguredError("EVALUATION_ACCESS_TOKEN is not configured")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise EvaluationAccessError("Invalid or missing evaluation token")


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def open_ground_truth(token: str | None, scale: str | None = None, data_root: Path | None = None) -> GroundTruth:
    """The only entry point to ground-truth files; requires the evaluation token."""
    verify_token(token)
    scale = scale or get_settings().data_scale
    d = Path(data_root or get_settings().data_dir) / "ground_truth" / scale
    if not (d / "evaluation_set.csv").exists():
        raise FileNotFoundError(f"Ground truth for scale '{scale}' not found in {d}")
    ev = _read_csv(d / "evaluation_set.csv")
    for r in ev:
        r["theme_ids"] = [t for t in (r["theme_ids"] or "").split("|") if t]
        r["is_sarcasm"] = r["is_sarcasm"] == "true"
        r["is_ambiguous"] = r["is_ambiguous"] == "true"
    return GroundTruth(scale, ev, _read_csv(d / "ground_truth.csv"),
                       json.loads((d / "scenario_events.json").read_text(encoding="utf-8")))


# --------------------------------------------------------------- sentiment --
def _sentiment_metrics(y_true: list[str], y_pred: list[str], all_labels: bool = True) -> dict:
    """Accuracy and macro P/R/F1. Slices (all_labels=False) average over labels present in y_true."""
    if not y_true:
        return {"n": 0}
    labels = list(SENTIMENT_CODES) if all_labels else sorted(set(y_true))
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    return {"n": len(y_true), "accuracy": round(accuracy_score(y_true, y_pred), 4),
            "macro_precision": round(float(p), 4), "macro_recall": round(float(r), 4), "macro_f1": round(float(f), 4)}


def evaluate_sentiment(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["pred_sentiment"]]
    y_true = [r["sentiment_label"] for r in scored]
    y_pred = [r["pred_sentiment"] for r in scored]
    labels = list(SENTIMENT_CODES)
    out = _sentiment_metrics(y_true, y_pred)
    out.update({
        "evaluation_records": len(rows), "scored_records": len(scored), "coverage": round(len(scored) / max(1, len(rows)), 4),
        "unscored_reasons": _count([r["processing_status"] for r in rows if not r["pred_sentiment"]]),
        "labels": labels,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist() if scored else [],
        "classification_report": classification_report(y_true, y_pred, labels=labels, output_dict=True,
                                                       zero_division=0) if scored else {},
        "weighted_f1": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4) if scored else None,
    })
    slices = {}
    for name, key in (("language", "language"), ("language_variant", "language_variant"),
                      ("source_type", "source_type"), ("writing_style", "writing_style")):
        slices[name] = {v: _sentiment_metrics([r["sentiment_label"] for r in scored if r[key] == v],
                                              [r["pred_sentiment"] for r in scored if r[key] == v], False)
                        for v in sorted({r[key] for r in scored})}
    # Template-generated text repeats: an identical (normalised) text may occur in TRAIN and TEST.
    # The "false" slice is the honest estimate on text the model has never seen verbatim.
    for flag in ("is_sarcasm", "is_ambiguous", "needs_human_review", "text_seen_in_train"):
        slices[flag] = {str(v).lower(): _sentiment_metrics([r["sentiment_label"] for r in scored if r[flag] == v],
                                                           [r["pred_sentiment"] for r in scored if r[flag] == v], False)
                        for v in (True, False)}
    out["slices"] = slices
    # Baselines (same records) - makes the model numbers interpretable
    majority = max(set(y_true), key=y_true.count) if y_true else None
    lex = [(lexicon_sentiment(r["text"])[0] or "NEUTRAL") for r in scored]
    out["baselines"] = {"majority_class": {"label": majority, **_sentiment_metrics(y_true, [majority] * len(y_true))},
                        "lexicon_only": _sentiment_metrics(y_true, lex)}
    return out


# ------------------------------------------------------------------ themes --
def evaluate_themes(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["pred_sentiment"]]
    if not scored:
        return {"n": 0}
    mlb = MultiLabelBinarizer(classes=list(THEME_IDS_ORDERED)).fit([list(THEME_IDS_ORDERED)])
    yt, yp = mlb.transform([r["theme_ids"] for r in scored]), mlb.transform([r["pred_themes"] for r in scored])
    res = {"n": len(scored)}
    for avg in ("micro", "macro"):
        p, r, f, _ = precision_recall_fscore_support(yt, yp, average=avg, zero_division=0)
        res[f"{avg}_precision"], res[f"{avg}_recall"], res[f"{avg}_f1"] = round(float(p), 4), round(float(r), 4), round(float(f), 4)
    res["subset_accuracy"] = round(float(accuracy_score(yt, yp)), 4)
    res["hamming_loss"] = round(float(hamming_loss(yt, yp)), 4)
    p, r, f, s = precision_recall_fscore_support(yt, yp, average=None, zero_division=0)
    res["per_theme"] = [{"theme_id": t, "theme_name": THEME_BY_ID[t].name, "precision": round(float(p[i]), 4),
                         "recall": round(float(r[i]), 4), "f1": round(float(f[i]), 4), "support": int(s[i])}
                        for i, t in enumerate(THEME_IDS_ORDERED)]
    res["records_without_true_theme"] = int((yt.sum(axis=1) == 0).sum())
    seen = [i for i, r in enumerate(scored) if r["text_seen_in_train"]]
    unseen = [i for i, r in enumerate(scored) if not r["text_seen_in_train"]]
    res["text_seen_in_train_slices"] = {}
    for name, idx in (("true", seen), ("false", unseen)):
        if idx:
            p, r, f, _ = precision_recall_fscore_support(yt[idx], yp[idx], average="micro", zero_division=0)
            _, _, fm, _ = precision_recall_fscore_support(yt[idx], yp[idx], average="macro", zero_division=0)
            res["text_seen_in_train_slices"][name] = {"n": len(idx), "micro_f1": round(float(f), 4),
                                                      "macro_f1": round(float(fm), 4)}
    res["language_slices"] = {}
    for lang in sorted({r["language"] for r in scored}):
        idx = [i for i, r in enumerate(scored) if r["language"] == lang]
        p, r, f, _ = precision_recall_fscore_support(yt[idx], yp[idx], average="micro", zero_division=0)
        res["language_slices"][lang] = {"n": len(idx), "micro_f1": round(float(f), 4)}
    return res


# ------------------------------------------------------------ data quality --
def evaluate_data_quality(engine: Engine, gt: GroundTruth) -> dict:
    summary = build_validation_summary(engine)
    with engine.connect() as conn:
        issues = [dict(r) for r in conn.execute(text(
            "SELECT rule_code, record_id, entity_type, severity, resolution_action FROM data_quality_issues")).mappings()]
        # Records submitted through the API after ingestion are not part of the generated dataset, so the
        # ground truth cannot explain them; their issues are reported separately, not as "unexplained".
        live = {r[0] for r in conn.execute(text(
            "SELECT source_record_id FROM feedback_items WHERE dataset_split = 'UNASSIGNED'"))}
    norm = lambda rid: rid[3:] if rid.startswith("FB-") else rid  # noqa: E731
    detected = {(i["rule_code"], norm(i["record_id"])) for i in issues}
    by_type: dict[str, dict] = {}
    matched: set[tuple[str, str]] = set()
    for a in gt.anomalies:
        key = (a["expected_rule"], a["record_id"])
        d = by_type.setdefault(a["issue_type"], {"injected": 0, "detected": 0, "missed_record_ids": []})
        d["injected"] += 1
        if key in detected:
            d["detected"] += 1
            matched.add(key)
        else:
            d["missed_record_ids"].append(a["record_id"])
    for d in by_type.values():
        d["recall"] = round(d["detected"] / d["injected"], 4) if d["injected"] else None
    live_issues = [i for i in issues if norm(i["record_id"]) in live]
    unexplained = [i for i in issues if (i["rule_code"], norm(i["record_id"])) not in matched
                   and norm(i["record_id"]) not in live]
    injected_total = sum(d["injected"] for d in by_type.values())
    return {
        **summary["totals"],
        "issues_by_rule": {k: v["issues"] for k, v in summary["checks"].items()},
        "injected_anomaly_detection": by_type,
        "injected_total": injected_total, "injected_detected": len(matched),
        "detection_recall": round(len(matched) / injected_total, 4) if injected_total else None,
        "detection_precision": (round(len(matched) / (len(issues) - len(live_issues)), 4)
                                if len(issues) > len(live_issues) else None),
        "live_submission_issues": len(live_issues),
        "live_submission_issues_by_rule": _count([i["rule_code"] for i in live_issues]),
        "unexplained_issues": len(unexplained),
        "unexplained_by_rule": _count([i["rule_code"] for i in unexplained]),
        "unexplained_examples": unexplained[:20],
    }


# ---------------------------------------------------- business intelligence --
def evaluate_bi(engine: Engine, gt: GroundTruth) -> dict:
    with engine.connect() as conn:
        alerts = detect_shifts(conn, "week", evidence_limit=0)
        actions = [dict(r) for r in conn.execute(text("""
            SELECT a.action_id, a.rule_code, a.priority, a.product_id, a.theme_id, a.evidence_count,
                   count(e.feedback_id) AS evidence_rows,
                   count(e.feedback_id) FILTER (WHERE fa.feedback_id IS NOT NULL) AS evidence_analysable,
                   count(e.feedback_id) FILTER (WHERE fa.sentiment_label = 'NEGATIVE') AS evidence_negative,
                   count(e.feedback_id) FILTER (WHERE ct.theme_id IS NOT NULL) AS evidence_theme_match
            FROM action_items a
            LEFT JOIN action_evidence e ON e.action_id = a.action_id
            LEFT JOIN v_feedback_analytics fa ON fa.feedback_id = e.feedback_id
            LEFT JOIN v_current_themes ct ON ct.feedback_id = e.feedback_id AND ct.theme_id = a.theme_id
            GROUP BY a.action_id ORDER BY a.action_id""")).mappings()]
        recurring = conn.execute(text("""
            SELECT count(*) FROM (SELECT ct.theme_id FROM v_feedback_analytics fa
                JOIN v_current_themes ct ON ct.feedback_id = fa.feedback_id WHERE fa.sentiment_label = 'NEGATIVE'
                GROUP BY ct.theme_id
                HAVING count(DISTINCT date_trunc('month', fa.feedback_date)) >= 3) x""")).scalar()
    events = []
    for ev in gt.scenario_events:
        theme_id = THEME_BY_CODE[ev["theme_code"]].theme_id
        hits = [a for a in alerts if a["theme_id"] in (theme_id, None)
                and a["period_end"] >= ev["start"] and a["period_start"] <= ev["end"]]
        theme_hits = [a for a in hits if a["theme_id"] == theme_id]
        scoped = bool(ev.get("product_id") or ev.get("category"))
        act = [a["action_id"] for a in actions if a["theme_id"] == theme_id
               and (not ev.get("product_id") or a["product_id"] in (ev["product_id"], None))]
        events.append({"event_code": ev["event_code"], "theme": ev["theme_code"], "window": [ev["start"], ev["end"]],
                       "scope": "product/category" if scoped else "all products",
                       "detected_by_theme_alert": bool(theme_hits), "detected_by_any_alert": bool(hits),
                       "alert_weeks": [a["period_start"] for a in theme_hits], "related_actions": act})
    explained_alerts = 0
    for a in alerts:
        for ev in gt.scenario_events:
            tid = THEME_BY_CODE[ev["theme_code"]].theme_id
            if a["theme_id"] in (tid, None) and a["period_end"] >= ev["start"] and a["period_start"] <= ev["end"]:
                explained_alerts += 1
                break
    traced = [a for a in actions if a["evidence_rows"] > 0 and a["evidence_rows"] == a["evidence_count"]]
    total_ev = sum(a["evidence_rows"] for a in actions)
    return {
        "recurring_negative_themes_detected": recurring,
        "sentiment_shift_alerts": len(alerts),
        "alerts_overlapping_known_scenarios": explained_alerts,
        "alert_precision_vs_scenarios": round(explained_alerts / len(alerts), 4) if alerts else None,
        "scenario_events": events,
        "scenario_recall_theme_alerts": round(sum(e["detected_by_theme_alert"] for e in events) / max(1, len(events)), 4),
        "scenario_recall_with_actions": round(sum(bool(e["related_actions"]) for e in events) / max(1, len(events)), 4),
        "actions_generated": len(actions),
        "actions_by_rule": _count([a["rule_code"] for a in actions]),
        "actions_by_priority": _count([a["priority"] for a in actions]),
        "evidence_traceability": {
            "actions_with_complete_evidence": len(traced), "actions_total": len(actions),
            "traceability_rate": round(len(traced) / len(actions), 4) if actions else None,
            "evidence_links": total_ev,
            "evidence_analysable_rate": round(sum(a["evidence_analysable"] for a in actions) / total_ev, 4) if total_ev else None,
            "evidence_negative_rate": round(sum(a["evidence_negative"] for a in actions) / total_ev, 4) if total_ev else None,
            "evidence_theme_match_rate": round(sum(a["evidence_theme_match"] for a in actions) / total_ev, 4) if total_ev else None,
        },
    }


def _reproducibility(scale: str) -> dict:
    root = get_settings().data_dir / "exports" / scale
    out: dict = {}
    if (root / "manifest.json").exists():
        m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        out["manifest"] = {k: m[k] for k in ("generator_version", "seed", "scale", "command", "row_counts")}
        out["manifest"]["files"] = len(m["file_checksums_sha256"])
    if (root / "reproducibility_report.json").exists():
        out["report"] = json.loads((root / "reproducibility_report.json").read_text(encoding="utf-8"))
    return out


def _count(values: list) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


def evaluation_rows(engine: Engine, gt: GroundTruth) -> list[dict]:
    with engine.connect() as conn:
        preds = {r["feedback_id"]: dict(r) for r in conn.execute(text("""
            SELECT fi.feedback_id, fi.processing_status, fi.feedback_text, cs.sentiment_label, cs.needs_human_review,
                   cs.run_id,
                   EXISTS (SELECT 1 FROM feedback_items tr WHERE tr.dataset_split = 'TRAIN'
                           AND tr.text_hash = fi.text_hash) AS text_seen_in_train
            FROM feedback_items fi LEFT JOIN v_current_sentiment cs USING (feedback_id)
            WHERE fi.dataset_split = 'TEST'""")).mappings()}
        themes: dict[str, list[str]] = {}
        for fid, tid in conn.execute(text("""SELECT ct.feedback_id, ct.theme_id FROM v_current_themes ct
                                             JOIN feedback_items fi USING (feedback_id) WHERE fi.dataset_split = 'TEST'""")):
            themes.setdefault(fid, []).append(tid)
    rows = []
    for g in gt.evaluation_set:
        p = preds.get(g["feedback_id"], {})
        rows.append({**g, "pred_sentiment": p.get("sentiment_label"), "pred_themes": themes.get(g["feedback_id"], []),
                     "needs_human_review": bool(p.get("needs_human_review")), "text": p.get("feedback_text", ""),
                     "processing_status": p.get("processing_status", "MISSING"),
                     "text_seen_in_train": bool(p.get("text_seen_in_train"))})
    return rows


def compute_metrics(engine: Engine, token: str | None, scale: str | None = None, write_report: bool = True) -> dict:
    gt = open_ground_truth(token, scale)
    rows = evaluation_rows(engine, gt)
    present = sum(1 for r in rows if r["processing_status"] != "MISSING")
    if rows and present / len(rows) < 0.5:
        raise EvaluationScaleMismatchError(
            f"Only {present} of {len(rows)} '{gt.scale}' evaluation records exist in the database; "
            "the loaded dataset is a different scale (pass ?scale=... or set DATA_SCALE).")
    with engine.connect() as conn:
        run = conn.execute(text("""SELECT run_id, sentiment_model_version, theme_model_version FROM pipeline_runs
                                   WHERE status = 'SUCCEEDED' AND sentiment_model_version IS NOT NULL
                                   AND run_type = 'FULL' ORDER BY run_id DESC LIMIT 1""")).mappings().first()
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "scale": gt.scale,
        "evaluated_run": dict(run) if run else None,
        "ground_truth": {"evaluation_records": len(gt.evaluation_set), "reviewer": "HUMAN-REVIEW-PANEL-A (synthetic)",
                         "split": "TEST (never used for training or tuning)"},
        "sentiment": evaluate_sentiment(rows),
        "themes": evaluate_themes(rows),
        "data_quality": evaluate_data_quality(engine, gt),
        "business_intelligence": evaluate_bi(engine, gt),
        "reproducibility": _reproducibility(gt.scale),
    }
    if write_report:
        out = get_settings().data_dir / "exports" / gt.scale / "evaluation_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=_json_default) + "\n", encoding="utf-8")
    return json.loads(json.dumps(report, default=_json_default))


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)
