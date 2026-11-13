"""Score a small hand-written probe set that no generator template produced.

The template-generated TEST split shares phrasing with TRAIN, so TEST metrics
overstate how well the baseline handles real customer language. The probe
(``data/probe/free_text_probe.csv``: 30 sentences in English, Hindi and Telugu,
native and romanised, including sarcasm, mixed, suggestions and ambiguity) gives
an honest out-of-distribution check. Probe labels were written by the AI agent
that built this project, not by a human panel; see docs/evaluation.md.

Usage::

    python -m scripts.free_text_probe --scale starter
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support
from sklearn.preprocessing import MultiLabelBinarizer

from app.core.config import PROJECT_ROOT, get_settings
from app.core.logging_config import configure_logging
from app.core.taxonomy import SENTIMENT_CODES, THEME_BY_CODE, THEME_BY_ID, THEME_IDS_ORDERED
from app.services.model_registry import active_model_info
from app.services.sentiment_service import classify_sentiment
from app.services.theme_service import classify_themes

PROBE = PROJECT_ROOT / "data" / "probe" / "free_text_probe.csv"


def run_probe() -> dict:
    with PROBE.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    texts = [r["text"] for r in rows]
    sents = classify_sentiment(texts)
    themes, _ = classify_themes(texts)
    y_true = [r["sentiment_label"] for r in rows]
    y_pred = [s.label for s in sents]
    t_true = [[THEME_BY_CODE[c].theme_id for c in r["theme_codes"].split("|") if c] for r in rows]
    t_pred = [[t for t, _ in ths] for ths in themes]
    mlb = MultiLabelBinarizer(classes=list(THEME_IDS_ORDERED)).fit([list(THEME_IDS_ORDERED)])
    yt, yp = mlb.transform(t_true), mlb.transform(t_pred)
    p, r, f, _ = precision_recall_fscore_support(yt, yp, average="micro", zero_division=0)

    def acc(idx: list[int]) -> dict:
        return {"n": len(idx), "accuracy": round(sum(y_true[i] == y_pred[i] for i in idx) / len(idx), 4) if idx else None}

    langs = sorted({r["language"] for r in rows})
    phen = sorted({r["phenomenon"] for r in rows})
    return {
        "probe_file": str(PROBE.relative_to(PROJECT_ROOT)), "n": len(rows),
        "models": {k: (active_model_info(k) or {}).get("model_version") for k in ("sentiment", "themes")},
        "sentiment": {"accuracy": round(accuracy_score(y_true, y_pred), 4),
                      "macro_f1": round(float(f1_score(y_true, y_pred, labels=list(SENTIMENT_CODES),
                                                       average="macro", zero_division=0)), 4),
                      "flagged_for_review": sum(s.needs_human_review for s in sents),
                      "errors_flagged_for_review": sum(1 for s, t in zip(sents, y_true)
                                                       if s.label != t and s.needs_human_review),
                      "errors": sum(1 for s, t in zip(sents, y_true) if s.label != t),
                      "by_language": {lg: acc([i for i, r in enumerate(rows) if r["language"] == lg]) for lg in langs},
                      "by_phenomenon": {ph: acc([i for i, r in enumerate(rows) if r["phenomenon"] == ph]) for ph in phen}},
        "themes": {"micro_precision": round(float(p), 4), "micro_recall": round(float(r), 4),
                   "micro_f1": round(float(f), 4)},
        "rows": [{"probe_id": row["probe_id"], "text": row["text"], "true": row["sentiment_label"], "pred": s.label,
                  "confidence": s.confidence, "needs_human_review": s.needs_human_review,
                  "review_reason": s.review_reason, "method": s.method,
                  "true_themes": [THEME_BY_ID[t].name for t in tt], "pred_themes": [THEME_BY_ID[t].name for t in tp]}
                 for row, s, tt, tp in zip(rows, sents, t_true, t_pred)],
    }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scale", choices=["starter", "full"], default=settings.data_scale,
                   help="Only selects where the report is written; the ACTIVE models are used")
    args = p.parse_args(argv)
    configure_logging()
    report = run_probe()
    out = settings.data_dir / "exports" / args.scale / "free_text_probe_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
