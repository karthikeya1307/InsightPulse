"""Synthetic data generator entry point.

Usage::

    python -m generator.generate_data --scale starter --seed 20261022
    python -m generator.generate_data --scale full --seed 20261022
    python -m generator.generate_data --scale starter --out-root /tmp/run1   # alternate root

Order of generation (parents before children):
    reference -> channels -> products -> customers -> surveys
    -> reviews -> survey responses -> tickets -> messages -> ticket events
    -> dataset splits -> training annotations -> anomaly injection -> exports

Outputs (``<root>`` defaults to ./data):
    <root>/master/<scale>/       customers, products, channels, surveys, themes, sentiment_labels
    <root>/raw/<scale>/          survey_responses, reviews, support_tickets, support_messages,
                                 ticket_status_events, annotations, dataset_splits
    <root>/ground_truth/<scale>/ ground_truth (anomaly registry), evaluation_set, scenario_events
    <root>/exports/<scale>/      manifest.json, checksums.json
Each table is written as CSV and JSON.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.core.logging_config import configure_logging, get_logger
from app.utils import id_generator as ids
from generator.anomaly_injector import AnomalyInjector
from generator.config import DEFAULT_SEED, GENERATOR_VERSION, ID_YEAR, SCALES, SCENARIO_EVENTS, ScaleConfig
from generator.feedback_generator import generate_reviews, generate_support, generate_survey_responses
from generator.master_generator import (
    IST, generate_channels, generate_customers, generate_products, generate_reference, generate_surveys, rng_for,
)
from generator.text_templates import EXPLAINED_MISMATCH_NOTE

log = get_logger("generator")

SOURCE_KEYS = {"SURVEY_RESPONSE": ("response_id", "response_date"), "REVIEW": ("review_id", "review_date"),
               "SUPPORT_MESSAGE": ("message_id", "sent_at")}


def _public(rows: list[dict]) -> list[dict]:
    """Strip private generation metadata (keys starting with '_')."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def _cell(v: object) -> object:
    if isinstance(v, list):
        return "|".join(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def write_table(directory: Path, name: str, rows: list[dict]) -> list[Path]:
    """Write ``rows`` as <name>.csv and <name>.json deterministically."""
    directory.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = directory / f"{name}.csv", directory / f"{name}.json"
    fields = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(v) for k, v in r.items()})
    with json_path.open("w", encoding="utf-8") as fh:
        fh.write("[\n")
        fh.write(",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=False) for r in rows))
        fh.write("\n]\n")
    return [csv_path, json_path]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stratified_splits(records: list[dict], cfg: ScaleConfig, seed: int) -> dict[str, str]:
    """TRAIN/VALIDATION/TEST assignment stratified by (source type, sentiment)."""
    rng = rng_for(seed, "splits")
    groups: dict[tuple[str, str], list[str]] = {}
    for r in records:
        groups.setdefault((r["source_type"], r["meta"]["true_sentiment"]), []).append(r["source_id"])
    out: dict[str, str] = {}
    for key in sorted(groups):
        members = sorted(groups[key])
        rng.shuffle(members)
        n = len(members)
        n_train, n_val = round(n * cfg.train_share), round(n * cfg.validation_share)
        for i, sid in enumerate(members):
            out[sid] = "TRAIN" if i < n_train else ("VALIDATION" if i < n_train + n_val else "TEST")
    return out


def build_dataset(cfg: ScaleConfig, seed: int) -> dict:
    """Generate every table in memory (dicts) - returns tables + ground truth."""
    t0 = time.perf_counter()
    themes, labels = generate_reference()
    channels = generate_channels()
    products = generate_products(cfg, seed)
    customers = generate_customers(cfg, seed)
    surveys = generate_surveys(cfg, seed, products, channels)
    log.info("Master data: %d customers, %d products, %d channels, %d surveys",
             len(customers), len(products), len(channels), len(surveys))

    reviews = generate_reviews(cfg, seed, customers, products, channels)
    responses = generate_survey_responses(cfg, seed, customers, products, surveys)
    tickets, messages, ticket_events = generate_support(cfg, seed, customers, products, channels)
    log.info("Transactions: %d reviews, %d responses, %d tickets, %d messages",
             len(reviews), len(responses), len(tickets), len(messages))

    ticket_customer = {t["ticket_id"]: t["customer_id"] for t in tickets}
    customer_msgs = [m for m in messages if m["sender_type"] == "CUSTOMER"]
    for m in customer_msgs:
        m["_customer_id"] = ticket_customer[m["ticket_id"]]

    def feedback_records() -> list[dict]:
        out = []
        for st, rows in (("SURVEY_RESPONSE", responses), ("REVIEW", reviews), ("SUPPORT_MESSAGE", customer_msgs)):
            key, dkey = SOURCE_KEYS[st]
            for r in rows:
                out.append({"source_type": st, "source_id": r[key], "date": r[dkey], "meta": r["_meta"],
                            "rated": "rating" in r, "row": r})
        return out

    base_records = feedback_records()
    splits = stratified_splits(base_records, cfg, seed)

    # Training annotations for TRAIN/VALIDATION records (labels follow generation metadata).
    rng_ann = rng_for(seed, "annotations")
    ann_seq = ids.IdSequence("annotation", ID_YEAR)
    data_end = datetime.combine(cfg.end_date, datetime.max.time().replace(microsecond=0), tzinfo=IST)
    annotations = []
    for r in sorted(base_records, key=lambda x: (x["date"], x["source_id"])):
        split = splits[r["source_id"]]
        if split == "TEST":
            continue
        fb_when = datetime.fromisoformat(r["date"])
        when = fb_when + timedelta(days=rng_ann.randint(1, 14), hours=rng_ann.randint(0, 8))
        when = max(fb_when, min(when, data_end))  # annotations happen inside the data window
        annotations.append({
            "annotation_id": ann_seq.next(), "feedback_id": ids.feedback_id(r["source_id"]),
            "sentiment_label": r["meta"]["true_sentiment"], "theme_labels": list(r["meta"]["true_theme_ids"]),
            "annotator_id": f"ANNOTATOR-{rng_ann.randint(1, 6):02d}",
            "annotation_note": EXPLAINED_MISMATCH_NOTE if r["meta"]["explained_mismatch"] else "",
            "annotated_at": when.isoformat(), "declared_split": split, "_source_id": r["source_id"],
        })

    # ---- Anomaly injection (after the valid baseline) -------------------------
    inj = AnomalyInjector(cfg, rng_for(seed, "anomalies"))
    for r in base_records:  # explained mismatches are legit boundary cases; keep them untouched
        if r["meta"]["explained_mismatch"]:
            inj.touched.add(r["source_id"])
    responses.extend(inj.duplicate_responses(responses, splits))
    customers_by_id = {c["customer_id"]: c for c in customers}
    inj.privacy_leaks([(st, row) for st, rows in (("SURVEY_RESPONSE", responses), ("REVIEW", reviews),
                                                  ("SUPPORT_MESSAGE", customer_msgs)) for row in rows],
                      customers_by_id)
    rated = {r["source_id"] for r in base_records if r["rated"]}
    inj.label_contradictions(annotations, rated)
    inj.invalid_themes(annotations)
    test_records = [r for r in base_records if splits[r["source_id"]] == "TEST"]
    inj.target_leakage(test_records, annotations, ann_seq)

    # ---- Ground truth --------------------------------------------------------
    evaluation_set = []
    for r in sorted(test_records, key=lambda x: x["source_id"]):
        m = r["meta"]
        evaluation_set.append({
            "feedback_id": ids.feedback_id(r["source_id"]), "source_record_id": r["source_id"],
            "source_type": r["source_type"], "sentiment_label": m["true_sentiment"],
            "theme_ids": list(m["true_theme_ids"]), "language": r["row"]["language"],
            "language_variant": m["language_variant"], "is_sarcasm": m["is_sarcasm"],
            "is_ambiguous": m["is_ambiguous"], "writing_style": m["style"],
            "scenario_event": m["scenario_event"], "reviewer_id": "HUMAN-REVIEW-PANEL-A",
            "review_status": "REVIEWED", "label_rule": "derive_sentiment(clause polarities) v1",
        })
    scenario_events = [{
        "event_code": e.event_code, "theme_code": e.theme_code, "start": e.start.isoformat(),
        "end": e.end.isoformat(), "intensity": e.intensity, "description": e.description,
        "product_id": ids.product_id(e.product_index) if e.product_index and e.product_index <= cfg.n_products else None,
        "category": e.category, "sources": list(e.sources),
    } for e in SCENARIO_EVENTS]

    split_rows = [{"source_record_id": sid, "feedback_id": ids.feedback_id(sid), "dataset_split": sp}
                  for sid, sp in sorted(splits.items())]
    for a in annotations:
        a.pop("_source_id", None)
    log.info("Annotations %d, anomalies %d, evaluation records %d (%.1fs)",
             len(annotations), len(inj.registry), len(evaluation_set), time.perf_counter() - t0)
    return {
        "master": {"channels": channels, "products": products, "customers": customers, "surveys": surveys,
                   "themes": themes, "sentiment_labels": labels},
        "raw": {"reviews": _public(reviews),
                "survey_responses": _public(sorted(responses, key=lambda r: r["response_id"])),
                "support_tickets": tickets, "support_messages": _public(messages),
                "ticket_status_events": ticket_events, "annotations": annotations,
                "dataset_splits": split_rows},
        "ground_truth": {"ground_truth": inj.registry, "evaluation_set": evaluation_set,
                         "scenario_events": scenario_events},
    }


def generate(scale: str, seed: int = DEFAULT_SEED, root: Path | None = None) -> dict:
    """Generate and write a dataset; returns the manifest."""
    cfg = SCALES[scale]
    root = Path(root) if root else PROJECT_ROOT / "data"
    data = build_dataset(cfg, seed)
    written: list[Path] = []
    for group in ("master", "raw", "ground_truth"):
        for name, rows in data[group].items():
            written += write_table(root / group / scale, name, rows)
    row_counts = {f"{g}/{n}": len(rows) for g in ("master", "raw", "ground_truth") for n, rows in data[g].items()}
    checksums = {str(p.relative_to(root)): sha256(p) for p in sorted(written)}
    manifest = {
        "generator": "InsightPulse synthetic generator", "generator_version": GENERATOR_VERSION,
        "scale": scale, "seed": seed, "config": cfg.to_dict(),
        "command": f"python -m generator.generate_data --scale {scale} --seed {seed}",
        "row_counts": row_counts,
        "derived_counts": {
            "feedback_items_expected": len(data["raw"]["reviews"]) + len(data["raw"]["survey_responses"])
            + sum(1 for m in data["raw"]["support_messages"] if m["sender_type"] == "CUSTOMER"),
            "support_messages_customer": sum(1 for m in data["raw"]["support_messages"] if m["sender_type"] == "CUSTOMER"),
            "support_messages_agent": sum(1 for m in data["raw"]["support_messages"] if m["sender_type"] == "AGENT"),
            "anomalies_by_type": _count_by(data["ground_truth"]["ground_truth"], "issue_type"),
        },
        "provenance": "100% synthetic: template text written for this project, synthetic names/e-mails "
                      "(example.com) and phone numbers; no public or private datasets used.",
        "file_checksums_sha256": checksums,
    }
    out_dir = root / "exports" / scale
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    return manifest


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="InsightPulse synthetic data generator")
    parser.add_argument("--scale", choices=sorted(SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out-root", type=Path, default=None, help="alternate data root (default ./data)")
    args = parser.parse_args()
    t0 = time.perf_counter()
    manifest = generate(args.scale, args.seed, args.out_root)
    log.info("Generated %s dataset (seed %s) in %.1fs", args.scale, args.seed, time.perf_counter() - t0)
    for k, v in manifest["row_counts"].items():
        log.info("  %-36s %8d", k, v)
    log.info("  anomalies: %s", manifest["derived_counts"]["anomalies_by_type"])


if __name__ == "__main__":
    main()
