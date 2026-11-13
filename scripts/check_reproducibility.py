"""Regenerate a dataset twice with the same seed and configuration and compare the runs.

Compared: record counts, file checksums (SHA-256), IDs, labels and relationships
(foreign-key columns and their validity). The canonical dataset under ``data/``
is compared too when it exists. The report is written to
``data/exports/<scale>/reproducibility_report.json``; regenerated copies go to
``data/repro_runs/<scale>/run_{1,2}`` (git-ignored).

Usage::

    python -m scripts.check_reproducibility --scale starter
    python -m scripts.check_reproducibility --scale full
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging_config import configure_logging
from generator.config import DEFAULT_SEED, GENERATOR_VERSION, SCALES
from generator.generate_data import generate

# table -> (group, primary key, {fk column: (parent table, parent pk)})
TABLES: dict[str, tuple[str, str, dict[str, tuple[str, str]]]] = {
    "channels": ("master", "channel_id", {}),
    "products": ("master", "product_id", {}),
    "customers": ("master", "customer_id", {}),
    "themes": ("master", "theme_id", {}),
    "surveys": ("master", "survey_id", {"product_id": ("products", "product_id"),
                                         "channel_id": ("channels", "channel_id")}),
    "survey_responses": ("raw", "response_id", {"survey_id": ("surveys", "survey_id"),
                                                 "customer_id": ("customers", "customer_id"),
                                                 "product_id": ("products", "product_id"),
                                                 "channel_id": ("channels", "channel_id")}),
    "reviews": ("raw", "review_id", {"customer_id": ("customers", "customer_id"),
                                     "product_id": ("products", "product_id"),
                                     "channel_id": ("channels", "channel_id")}),
    "support_tickets": ("raw", "ticket_id", {"customer_id": ("customers", "customer_id"),
                                             "product_id": ("products", "product_id"),
                                             "channel_id": ("channels", "channel_id"),
                                             "category_theme_id": ("themes", "theme_id")}),
    "support_messages": ("raw", "message_id", {"ticket_id": ("support_tickets", "ticket_id")}),
    "annotations": ("raw", "annotation_id", {}),
    "evaluation_set": ("ground_truth", "feedback_id", {}),
    "ground_truth": ("ground_truth", "issue_id", {}),
}
LABEL_COLUMNS = {"annotations": ["feedback_id", "sentiment_label", "theme_labels", "declared_split"],
                 "evaluation_set": ["sentiment_label", "theme_ids", "is_sarcasm", "is_ambiguous"],
                 "ground_truth": ["record_id", "issue_type", "expected_detection", "ground_truth_label"]}


def _read(root: Path, scale: str, table: str) -> list[dict]:
    group = TABLES[table][0]
    with (root / group / scale / f"{table}.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _fk_check(root: Path, scale: str) -> dict:
    keys = {t: {r[pk] for r in _read(root, scale, t)} for t, (_, pk, _) in TABLES.items() if t in
            ("channels", "products", "customers", "themes", "surveys", "support_tickets")}
    broken = {}
    for t, (_, _, fks) in TABLES.items():
        rows = _read(root, scale, t) if fks else []
        for col, (parent, _) in fks.items():
            bad = sum(1 for r in rows if r[col] and r[col] not in keys[parent])
            if bad:
                broken[f"{t}.{col}"] = bad
    return broken


def compare(a: Path, b: Path, scale: str, ma: dict, mb: dict) -> dict:
    files_a, files_b = ma["file_checksums_sha256"], mb["file_checksums_sha256"]
    differing = sorted(k for k in set(files_a) | set(files_b) if files_a.get(k) != files_b.get(k))
    ids, labels, rels = {}, {}, {}
    for t, (_, pk, fks) in TABLES.items():
        ra, rb = _read(a, scale, t), _read(b, scale, t)
        ids[t] = {"count": len(ra), "identical": [r[pk] for r in ra] == [r[pk] for r in rb]}
        if t in LABEL_COLUMNS:
            cols = [c for c in LABEL_COLUMNS[t] if ra and c in ra[0]]
            labels[t] = [tuple(r[c] for c in cols) for r in ra] == [tuple(r[c] for c in cols) for r in rb]
        if fks:
            rels[t] = all([r[c] for c in fks] == [s[c] for c in fks] for r, s in zip(ra, rb)) and len(ra) == len(rb)
    return {
        "row_counts_identical": ma["row_counts"] == mb["row_counts"],
        "files_compared": len(files_a), "files_with_different_checksum": differing,
        "ids_identical": all(v["identical"] for v in ids.values()), "ids": ids,
        "labels_identical": all(labels.values()), "labels": labels,
        "relationships_identical": all(rels.values()), "relationships": rels,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scale", choices=sorted(SCALES), default="starter")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--keep", action="store_true", help="Keep the regenerated copies")
    args = p.parse_args(argv)
    configure_logging()
    data_root = get_settings().data_dir
    base = data_root / "repro_runs" / args.scale
    runs, timings = [], []
    for i in (1, 2):
        d = base / f"run_{i}"
        shutil.rmtree(d, ignore_errors=True)
        t0 = time.perf_counter()
        runs.append((d, generate(args.scale, args.seed, d)))
        timings.append(round(time.perf_counter() - t0, 2))
    (a, ma), (b, mb) = runs
    result = compare(a, b, args.scale, ma, mb)
    result["foreign_keys_valid"] = {"run_1": not _fk_check(a, args.scale), "run_2": not _fk_check(b, args.scale),
                                    "broken_run_1": _fk_check(a, args.scale)}
    canonical = data_root / "exports" / args.scale / "manifest.json"
    if canonical.exists():
        mc = json.loads(canonical.read_text(encoding="utf-8"))
        same = mc.get("seed") == args.seed and mc["file_checksums_sha256"] == ma["file_checksums_sha256"]
        result["matches_committed_dataset"] = same
    passed = (result["row_counts_identical"] and not result["files_with_different_checksum"]
              and result["ids_identical"] and result["labels_identical"] and result["relationships_identical"]
              and result["foreign_keys_valid"]["run_1"] and result["foreign_keys_valid"]["run_2"])
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scale": args.scale, "seed": args.seed, "generator_version": GENERATOR_VERSION,
        "configuration": SCALES[args.scale].to_dict(),
        "command": f"python -m generator.generate_data --scale {args.scale} --seed {args.seed}",
        "runs": [{"directory": str(d.relative_to(data_root.parent) if d.is_relative_to(data_root.parent) else d),
                  "seconds": s} for (d, _), s in zip(runs, timings)],
        "row_counts": ma["row_counts"],
        "file_checksums_sha256": ma["file_checksums_sha256"],
        "comparison": result, "status": "PASS" if passed else "FAIL",
    }
    out = data_root / "exports" / args.scale / "reproducibility_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    print(json.dumps({"status": report["status"], **{k: v for k, v in result.items()
                                                     if k not in ("ids", "labels", "relationships")},
                      "report": str(out)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
