"""Validate the dataset and reconcile detected issues with the injected anomalies.

Two layers are run:

1. **File-level** checks on the generated CSVs (required fields, ID formats, PK/FK,
   ranges, dates, enumerations). This needs no database.
2. **Database-level** checks (duplicates, lifecycle, reconciliation, taxonomy,
   privacy, label contradictions, target leakage), run against the loaded data.

With an evaluation token, every detected issue is classified as an *injected*
anomaly (listed in the protected ground truth) or *unexplained*. Outputs:

* ``data/exceptions/<scale>/file_validation.json``
* ``data/exceptions/<scale>/validation_summary.json``
* ``data/exceptions/<scale>/validation_reconciliation.json`` (with token)

Usage::

    python -m scripts.validate_data --scale starter            # files + database
    python -m scripts.validate_data --scale starter --files-only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from app.core.config import get_settings
from app.core.logging_config import configure_logging, get_logger

log = get_logger("validate_data")


def file_validation(scale: str, data_root: Path) -> dict:
    from app.services.ingestion_service import load_dataset_files
    from app.services.validation_service import validate_files

    ds = load_dataset_files(scale, data_root)
    rep = validate_files(ds.tables, ds.annotations, ds.splits, ds.master_themes, ds.ticket_events)
    return {
        "scale": scale, "records_checked": rep.checked,
        "records_rejected": {t: len(v) for t, v in rep.rejected.items() if v},
        "issues_by_rule": dict(Counter(i.rule_code for i in rep.issues)),
        "issues": [{"rule_code": i.rule_code, "severity": i.severity, "entity_type": i.entity_type,
                    "record_id": i.record_id, "field": i.field_name, "message": i.message} for i in rep.issues],
    }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scale", choices=["starter", "full"], default=settings.data_scale)
    p.add_argument("--files-only", action="store_true", help="Skip database checks")
    p.add_argument("--fail-on-unexplained", action="store_true", help="Exit 1 if any issue is unexplained")
    args = p.parse_args(argv)
    configure_logging()
    root = settings.data_dir
    out = root / "exceptions" / args.scale
    out.mkdir(parents=True, exist_ok=True)

    fv = file_validation(args.scale, root)
    (out / "file_validation.json").write_text(json.dumps(fv, indent=2) + "\n", encoding="utf-8")
    report = {"file_level": {k: fv[k] for k in ("records_checked", "records_rejected", "issues_by_rule")}}
    exit_code = 0
    if not args.files_only:
        from app.core.database import get_engine
        from app.services.pipeline_service import run_pipeline

        engine = get_engine()
        summary = run_pipeline(engine, steps=["validate"], scale=args.scale, triggered_by="validate_data")
        db = summary["steps"]["validate"]
        vs = json.loads((out / "validation_summary.json").read_text(encoding="utf-8"))
        report["database_level"] = {"run_id": summary["run_id"], "totals": vs["totals"],
                                    "failed_checks": [k for k, v in vs["checks"].items() if v["status"] == "FAIL"],
                                    "issues_detected": db.get("issues_detected"), "summary_file": str(out / "validation_summary.json")}
        if settings.evaluation_access_token:
            from app.services.evaluation_service import evaluate_data_quality, open_ground_truth

            gt = open_ground_truth(settings.evaluation_access_token, args.scale, root)
            dq = evaluate_data_quality(engine, gt)
            rec = {k: dq[k] for k in ("injected_total", "injected_detected", "detection_recall", "detection_precision",
                                      "injected_anomaly_detection", "unexplained_issues", "unexplained_by_rule",
                                      "unexplained_examples", "live_submission_issues",
                                      "live_submission_issues_by_rule")}
            (out / "validation_reconciliation.json").write_text(json.dumps(rec, indent=2, default=str) + "\n",
                                                                encoding="utf-8")
            report["reconciliation"] = {k: rec[k] for k in ("injected_total", "injected_detected", "detection_recall",
                                                            "unexplained_issues", "unexplained_by_rule")}
            if args.fail_on_unexplained and rec["unexplained_issues"]:
                exit_code = 1
        else:
            report["reconciliation"] = "skipped: EVALUATION_ACCESS_TOKEN not set"
    print(json.dumps(report, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
