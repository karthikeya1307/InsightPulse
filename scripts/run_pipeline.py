"""Run the end-to-end InsightPulse pipeline from the command line.

Examples::

    python -m scripts.run_pipeline --scale starter --generate --reset-db     # fresh start
    python -m scripts.run_pipeline --scale starter --steps validate predict actions
    python -m scripts.run_pipeline --scale full --generate --reset-db --evaluate

Steps run in this fixed order: ingest -> validate -> train -> predict -> actions.
``--evaluate`` reads the protected ground truth and therefore needs
EVALUATION_ACCESS_TOKEN in the environment (or .env).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.logging_config import configure_logging, get_logger
from app.services.pipeline_service import ALL_STEPS, run_pipeline

log = get_logger("run_pipeline")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scale", choices=["starter", "full"], default=settings.data_scale)
    p.add_argument("--seed", type=int, default=settings.generator_seed)
    p.add_argument("--steps", nargs="+", choices=ALL_STEPS, default=list(ALL_STEPS))
    p.add_argument("--generate", action="store_true", help="(Re)generate the synthetic dataset first")
    p.add_argument("--reset-db", action="store_true", help="Drop and recreate the schema first (destructive)")
    p.add_argument("--replace", action="store_true", help="Truncate loaded data before ingesting")
    p.add_argument("--evaluate", action="store_true", help="Compute evaluation metrics afterwards (needs token)")
    args = p.parse_args(argv)
    configure_logging()
    t0 = time.perf_counter()

    if args.generate:
        from generator.generate_data import generate

        manifest = generate(args.scale, args.seed)
        log.info("Generated %s dataset: %s", args.scale, manifest["row_counts"])
    if args.reset_db:
        from scripts.setup_db import setup_database

        setup_database(settings.database_url, reset=True)
    engine = get_engine()
    summary = run_pipeline(engine, steps=args.steps, scale=args.scale, replace=args.replace, triggered_by="cli")
    if args.evaluate:
        from app.services.evaluation_service import compute_metrics

        m = compute_metrics(engine, settings.evaluation_access_token, args.scale)
        summary["evaluation"] = {
            "sentiment_accuracy": m["sentiment"]["accuracy"], "sentiment_macro_f1": m["sentiment"]["macro_f1"],
            "theme_micro_f1": m["themes"]["micro_f1"], "theme_macro_f1": m["themes"]["macro_f1"],
            "dq_detection_recall": m["data_quality"]["detection_recall"],
            "dq_unexplained_issues": m["data_quality"]["unexplained_issues"],
            "report": f"data/exports/{args.scale}/evaluation_report.json"}
    summary["total_seconds"] = round(time.perf_counter() - t0, 2)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary.get("status") == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
