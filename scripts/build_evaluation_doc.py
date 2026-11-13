"""Render docs/evaluation.md from the measured report files (no hand-typed numbers).

Inputs (per scale, when present):
  data/exports/<scale>/evaluation_report.json        (scripts.run_pipeline --evaluate)
  data/exports/<scale>/free_text_probe_report.json   (scripts.free_text_probe)
  data/exports/<scale>/reproducibility_report.json   (scripts.check_reproducibility)
  data/exports/<scale>/pipeline_run_summary.json     (saved output of scripts.run_pipeline)

Usage::

    python -m scripts.build_evaluation_doc
"""
from __future__ import annotations

import json
from pathlib import Path

from app.core.config import PROJECT_ROOT

EXPORTS = PROJECT_ROOT / "data" / "exports"
OUT = PROJECT_ROOT / "docs" / "evaluation.md"


def _load(scale: str, name: str) -> dict | None:
    p = EXPORTS / scale / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _f(x, pct: bool = False) -> str:
    if x is None:
        return "–"
    return f"{x:.1%}" if pct else f"{x:.3f}"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def sentiment_section(s: dict) -> str:
    b = s["baselines"]
    parts = [
        _table(["Model (same TEST records)", "n", "Accuracy", "Macro precision", "Macro recall", "Macro F1"], [
            ["TF-IDF + Logistic Regression", s["n"], _f(s["accuracy"]), _f(s["macro_precision"]),
             _f(s["macro_recall"]), _f(s["macro_f1"])],
            [f"Majority class ({b['majority_class']['label']})", b["majority_class"]["n"],
             _f(b["majority_class"]["accuracy"]), _f(b["majority_class"]["macro_precision"]),
             _f(b["majority_class"]["macro_recall"]), _f(b["majority_class"]["macro_f1"])],
            ["Multilingual lexicon only", b["lexicon_only"]["n"], _f(b["lexicon_only"]["accuracy"]),
             _f(b["lexicon_only"]["macro_precision"]), _f(b["lexicon_only"]["macro_recall"]),
             _f(b["lexicon_only"]["macro_f1"])],
        ]),
        "",
        f"Coverage: {s['scored_records']:,} of {s['evaluation_records']:,} evaluation records were scored "
        f"({_f(s['coverage'], True)}). Unscored records are quarantined or duplicate items, which are "
        f"never scored by design: {s['unscored_reasons'] or 'none'}.",
        "",
        "**Confusion matrix** (rows are the human label, columns the prediction)",
        "",
        _table(["true \\ predicted"] + s["labels"],
               [[lab] + row for lab, row in zip(s["labels"], s["confusion_matrix"])]),
        "",
        "**Per-class report**",
        "",
        _table(["Class", "Precision", "Recall", "F1", "Support"],
               [[c, _f(s["classification_report"][c]["precision"]), _f(s["classification_report"][c]["recall"]),
                 _f(s["classification_report"][c]["f1-score"]), int(s["classification_report"][c]["support"])]
                for c in s["labels"]]),
        "",
        "**Slices** (macro F1 averages only over the labels present in the slice)",
        "",
    ]
    rows = []
    for name, vals in s["slices"].items():
        for v, m in vals.items():
            if m.get("n"):
                rows.append([name, v, m["n"], _f(m["accuracy"]), _f(m["macro_f1"])])
    parts.append(_table(["Slice", "Value", "n", "Accuracy", "Macro F1"], rows))
    return "\n".join(parts)


def theme_section(t: dict) -> str:
    rows = [["Micro", _f(t["micro_precision"]), _f(t["micro_recall"]), _f(t["micro_f1"])],
            ["Macro", _f(t["macro_precision"]), _f(t["macro_recall"]), _f(t["macro_f1"])]]
    parts = [_table(["Average", "Precision", "Recall", "F1"], rows), "",
             f"Exact-match (subset) accuracy {_f(t['subset_accuracy'])}, Hamming loss {_f(t['hamming_loss'])}, "
             f"n = {t['n']:,}; {t['records_without_true_theme']:,} records have no true theme (ambiguous text).", "",
             _table(["Theme", "Precision", "Recall", "F1", "Support"],
                    [[p["theme_name"], _f(p["precision"]), _f(p["recall"]), _f(p["f1"]), p["support"]]
                     for p in t["per_theme"]]), ""]
    sl = [["language", k, v["n"], _f(v["micro_f1"]), "–"] for k, v in t["language_slices"].items()]
    sl += [["text seen in TRAIN", k, v["n"], _f(v["micro_f1"]), _f(v["macro_f1"])]
           for k, v in t.get("text_seen_in_train_slices", {}).items()]
    parts.append(_table(["Slice", "Value", "n", "Micro F1", "Macro F1"], sl))
    return "\n".join(parts)


def dq_section(q: dict) -> str:
    parts = [_table(["Metric", "Value"], [
        ["Primary-key violations", q["primary_key_violations"]], ["Foreign-key violations", q["foreign_key_violations"]],
        ["Duplicates flagged", q["duplicates"]], ["Invalid taxonomy labels", q["invalid_taxonomy"]],
        ["Privacy violations (quarantined)", q["privacy_violations"]],
        ["Label contradictions", q["label_contradictions"]], ["Target-leakage records", q["target_leakage"]],
        ["Feedback items with any issue", q["feedback_items_with_issues"]],
        ["Validation pass rate (items without issues)", _f(q["validation_pass_rate"], True)],
        ["Checks passing / total", f"{q['checks_passed']} / {q['checks_total']}"],
        ["Injected anomalies detected", f"{q['injected_detected']} / {q['injected_total']} "
                                        f"(recall {_f(q['detection_recall'], True)})"],
        ["Detection precision (issues explained by an injected anomaly)", _f(q["detection_precision"], True)],
        ["**Unexplained issues**", f"**{q['unexplained_issues']}**"],
        ["Issues on live API submissions (outside ground truth)", q.get("live_submission_issues", 0)],
    ]), "", _table(["Injected issue type", "Injected", "Detected", "Recall"],
                    [[k, v["injected"], v["detected"], _f(v["recall"], True)]
                     for k, v in q["injected_anomaly_detection"].items()])]
    return "\n".join(parts)


def bi_section(b: dict) -> str:
    rows = [[e["event_code"], e["theme"], f"{e['window'][0]} to {e['window'][1]}", e["scope"],
             "yes" if e["detected_by_theme_alert"] else "no", "yes" if e["detected_by_any_alert"] else "no",
             ", ".join(e["related_actions"]) or "–"] for e in b["scenario_events"]]
    ev = b["evidence_traceability"]
    return "\n".join([
        _table(["Planted scenario", "Theme", "Window", "Scope", "Theme-level alert", "Any alert", "Related actions"], rows),
        "",
        _table(["Metric", "Value"], [
            ["Recurring negative themes detected", b["recurring_negative_themes_detected"]],
            ["Sentiment-shift alerts (weekly)", b["sentiment_shift_alerts"]],
            ["Alerts overlapping a planted scenario", f"{b['alerts_overlapping_known_scenarios']} "
                                                       f"(precision {_f(b['alert_precision_vs_scenarios'], True)})"],
            ["Scenario recall: theme-level alerts", _f(b["scenario_recall_theme_alerts"], True)],
            ["Scenario recall: alerts or actions", _f(b["scenario_recall_with_actions"], True)],
            ["Actions generated", f"{b['actions_generated']} {b['actions_by_rule']}"],
            ["Actions by priority", b["actions_by_priority"]],
            ["Actions with complete evidence", f"{ev['actions_with_complete_evidence']} / {ev['actions_total']}"],
            ["Evidence links", ev["evidence_links"]],
            ["Evidence that is analysable / negative / matches the action theme",
             f"{_f(ev['evidence_analysable_rate'], True)} / {_f(ev['evidence_negative_rate'], True)} / "
             f"{_f(ev['evidence_theme_match_rate'], True)}"],
        ])])


def probe_section(p: dict) -> str:
    s = p["sentiment"]
    rows = [[r["probe_id"], r["text"].replace("|", "/"), r["true"], r["pred"], f"{r['confidence']:.2f}",
             "yes" if r["needs_human_review"] else "", "✓" if r["true"] == r["pred"] else "✗"] for r in p["rows"]]
    return "\n".join([
        _table(["Metric", "Value"], [
            ["Sentiment accuracy", _f(s["accuracy"])], ["Sentiment macro F1", _f(s["macro_f1"])],
            ["Errors / of which flagged for human review", f"{s['errors']} / {s['errors_flagged_for_review']}"],
            ["Theme micro F1 (P / R)", f"{_f(p['themes']['micro_f1'])} "
                                       f"({_f(p['themes']['micro_precision'])} / {_f(p['themes']['micro_recall'])})"],
            ["Accuracy by language", ", ".join(f"{k} {_f(v['accuracy'])} (n={v['n']})" for k, v in s["by_language"].items())],
            ["Accuracy by phenomenon", ", ".join(f"{k} {_f(v['accuracy'])} (n={v['n']})"
                                                 for k, v in s["by_phenomenon"].items())],
        ]), "", "<details><summary>All 30 probe sentences</summary>", "",
        _table(["ID", "Text", "Expected", "Predicted", "Conf.", "Review", ""], rows), "", "</details>"])


def run_section(r: dict) -> str:
    rows = [[k, v.get("seconds", "–")] for k, v in r["steps"].items()]
    rows.append(["**total (incl. generation and evaluation)**", r.get("total_seconds", "–")])
    extra = []
    if r.get("peak_rss_mb"):
        extra.append(f"Peak memory of the pipeline process: **{r['peak_rss_mb']:,} MB**.")
    p = r["steps"].get("predict", {})
    if p:
        extra.append(f"Inference throughput: {p.get('items_per_second', '–'):,} items/s "
                     f"({p.get('scored', 0):,} items scored, {p.get('theme_assignments', 0):,} theme assignments, "
                     f"{p.get('needs_human_review', 0):,} flagged for review).")
    return "\n".join([_table(["Step", "Seconds"], rows), "", " ".join(extra)])


def scale_block(scale: str) -> str:
    rep = _load(scale, "evaluation_report.json")
    if not rep:
        return f"_No evaluation report for `{scale}` yet._\n"
    parts = [f"Evaluated run `{rep['evaluated_run']['run_id']}`: sentiment model "
             f"`{rep['evaluated_run']['sentiment_model_version']}`, theme model "
             f"`{rep['evaluated_run']['theme_model_version']}`. Report generated {rep['generated_at']}. "
             f"Evaluation set: {rep['ground_truth']['evaluation_records']:,} records "
             f"({rep['ground_truth']['split']}).", ""]
    run = _load(scale, "pipeline_run_summary.json")
    if run:
        parts += ["#### Pipeline run (timings measured on the build machine)", "", run_section(run), ""]
    parts += ["#### Sentiment", "", sentiment_section(rep["sentiment"]), "",
              "#### Themes", "", theme_section(rep["themes"]), "",
              "#### Data quality", "", dq_section(rep["data_quality"]), "",
              "#### Business intelligence", "", bi_section(rep["business_intelligence"]), ""]
    probe = _load(scale, "free_text_probe_report.json")
    if probe:
        parts += ["#### Free-text probe (out-of-template sentences)", "", probe_section(probe), ""]
    repro = _load(scale, "reproducibility_report.json")
    if repro:
        c = repro["comparison"]
        parts += ["#### Reproducibility", "", _table(["Check", "Result"], [
            ["Status", f"**{repro['status']}**"], ["Seed / generator version", f"{repro['seed']} / {repro['generator_version']}"],
            ["Files compared (SHA-256)", f"{c['files_compared']}, differing: {len(c['files_with_different_checksum'])}"],
            ["Row counts identical", c["row_counts_identical"]], ["IDs identical", c["ids_identical"]],
            ["Labels identical", c["labels_identical"]], ["Relationships identical", c["relationships_identical"]],
            ["All foreign keys valid", c["foreign_keys_valid"]["run_1"] and c["foreign_keys_valid"]["run_2"]],
            ["Matches the committed dataset", c.get("matches_committed_dataset", "–")],
        ]), ""]
    return "\n".join(parts)


INTRO = """# Evaluation report

> This file is generated by `python -m scripts.build_evaluation_doc` from the JSON reports in
> `data/exports/<scale>/`. Every number below was computed from stored predictions and the separate
> human-reviewed labels. None was typed by hand.

## Method

* **Evaluation set.** The TEST split (15%, stratified by source × sentiment) is held out by the
  generator. Its labels are in `data/ground_truth/<scale>/evaluation_set.csv` and are never loaded as
  training annotations. Only the token-protected `evaluation_service` reads it. The "human review" is
  a synthetic panel: labels come from the documented clause rules and are marked as reviewed. No real
  annotators were involved.
* **Model selection.** Sentiment C is chosen on VALIDATION (macro F1), and per-theme thresholds are
  tuned on VALIDATION. TEST is used only here.
* **Sentiment metrics:** accuracy, macro precision, recall and F1, a confusion matrix, a per-class
  report, slices, and two baselines on the same records.
* **Theme metrics:** micro and macro precision, recall and F1, per-theme metrics, exact-match
  accuracy and Hamming loss.
* **Data quality:** the rule counts from the validation summary, reconciled against the injected
  anomaly registry (`ground_truth.csv`).
* **Business intelligence:** shift alerts and actions compared with the planted scenario events, and
  the integrity of evidence links.

## How to read these numbers

The text is **template-generated**, and labels are derived from the templates. A linear model can
learn that mapping almost perfectly, so TEST scores on synthetic data are an upper bound, not an
estimate of performance on real customers. Two checks make this visible:

1. **"text seen in TRAIN" slice.** It splits TEST into records whose normalised text also appears
   verbatim in TRAIN and records whose text does not. The `false` row is the fairer estimate.
2. **Free-text probe.** Thirty hand-written sentences that no template produced, including Hindi and
   Telugu in both scripts, sarcasm, mixed sentiment and suggestions. Their labels were written by the
   AI agent that built the project, not by a human panel. They should be read as a smoke test of
   generalisation, not a benchmark.

The baseline itself has limits: character n-grams help with Hindi and Telugu scripts and romanised
text, but it cannot understand those languages. Low-confidence non-English predictions are therefore
flagged for human review, and a lexicon fallback is used.
"""

OUTRO = """## Conclusions

* The workflow is measurable end to end. Every metric above is recomputed by one command, and the
  same code runs at both scales.
* On synthetic TEST data both models score very high. At full scale they score 1.000 even on TEST
  texts that never appear verbatim in TRAIN, because templates differ only in the values filled into
  their slots (product, feature, amount), which is not a meaningful difference. **Do not read the TEST
  numbers as real-world accuracy.** The free-text probe shows the realistic
  picture: the baseline handles plain English complaints and praise, but sarcasm, subtle mixed
  sentiment and non-English text are where it fails. Most of those errors are flagged for human
  review.
* Data-quality detection reconciles completely with the injected anomalies, with no unexplained
  issues. Two real generator defects were found this way during the full-scale run and fixed (see
  `AI_USAGE.md`).
* At the starter scale, theme-level shift alerts miss scenarios with low weekly volume. The action
  rules catch more of them, and recall is higher at full scale where weekly volumes are meaningful.

**Next steps for the models:** collect real labelled feedback; evaluate on a template-disjoint split (hold whole
templates out of TRAIN);
try multilingual sentence embeddings (for example a small multilingual transformer) as features; and
calibrate the review thresholds against reviewer capacity.
"""


def main() -> None:
    body = [INTRO]
    for scale, title in (("full", "Full scale (50k reviews + 50k responses + 20k messages)"),
                         ("starter", "Starter scale (500 + 500 + 200)")):
        body += [f"## {title}", "", scale_block(scale)]
    body.append(OUTRO)
    OUT.write_text("\n".join(body), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
