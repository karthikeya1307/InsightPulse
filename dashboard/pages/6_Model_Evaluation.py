"""Model evaluation against the human-reviewed TEST set (restricted endpoint)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Model Evaluation", "Metrics computed from stored predictions vs. the separate human-reviewed ground truth.", "🎯")
token = st.sidebar.text_input("Evaluation token", value=ui.EVAL_TOKEN, type="password",
                              help="Ground truth is only reachable with the evaluation token (X-Evaluation-Token).")
if not token:
    st.warning("Enter the evaluation token to view metrics.", icon="🔒")
    st.stop()
d = ui.get("/api/evaluation/metrics", headers={"X-Evaluation-Token": token})
if d is None:
    st.stop()
s, t, q, b = d["sentiment"], d["themes"], d["data_quality"], d["business_intelligence"]
run = d.get("evaluated_run") or {}
st.caption(f"Scale `{d['scale']}` · run `{run.get('run_id')}` · sentiment `{run.get('sentiment_model_version')}` · "
           f"themes `{run.get('theme_model_version')}` · {d['ground_truth']['evaluation_records']:,} TEST records")

tab1, tab2, tab3, tab4 = st.tabs(["Sentiment", "Themes", "Data quality", "Business intelligence"])
with tab1:
    k = st.columns(5)
    ui.kpi(k[0], "Accuracy", f"{s['accuracy']:.3f}")
    ui.kpi(k[1], "Macro F1", f"{s['macro_f1']:.3f}")
    ui.kpi(k[2], "Macro precision", f"{s['macro_precision']:.3f}")
    ui.kpi(k[3], "Macro recall", f"{s['macro_recall']:.3f}")
    ui.kpi(k[4], "Coverage", f"{s['coverage']:.1%}", f"{s['scored_records']:,} of {s['evaluation_records']:,} scored")
    a, c = st.columns([1, 1])
    with a:
        cm = s["confusion_matrix"]
        fig = go.Figure(go.Heatmap(z=cm, x=s["labels"], y=s["labels"], colorscale=[[i / 6, x] for i, x in enumerate(ui.SEQ_BLUE)],
                                   text=cm, texttemplate="%{text}", xgap=2, ygap=2, showscale=False,
                                   hovertemplate="True %{y} → predicted %{x}: %{z}<extra></extra>"))
        fig.update_layout(title="Confusion matrix (rows = human label)", yaxis=dict(autorange="reversed"))
        fig.update_xaxes(title="Predicted")
        st.plotly_chart(ui.style(fig, 380, legend=False), use_container_width=True)
    with c:
        rep = pd.DataFrame(s["classification_report"]).T.reset_index().rename(columns={"index": "class"})
        st.markdown("**Classification report**")
        st.dataframe(rep.round(3), hide_index=True, use_container_width=True)
        base = s["baselines"]
        st.markdown("**Baselines on the same records**")
        st.dataframe(pd.DataFrame([{"model": "TF-IDF + LogReg (ours)", "accuracy": s["accuracy"], "macro_f1": s["macro_f1"]},
                                   {"model": f"Majority class ({base['majority_class']['label']})",
                                    "accuracy": base["majority_class"]["accuracy"], "macro_f1": base["majority_class"]["macro_f1"]},
                                   {"model": "Multilingual lexicon only", "accuracy": base["lexicon_only"]["accuracy"],
                                    "macro_f1": base["lexicon_only"]["macro_f1"]}]), hide_index=True, use_container_width=True)
    st.markdown("**Slices** — where the baseline is weaker")
    rows = []
    for sl, vals in s["slices"].items():
        for v, m in vals.items():
            if m.get("n"):
                rows.append({"slice": sl, "value": v, "n": m["n"], "accuracy": m["accuracy"], "macro_f1": m["macro_f1"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=320)
with tab2:
    k = st.columns(5)
    for col, key, lab in zip(k, ["micro_f1", "macro_f1", "micro_precision", "micro_recall", "subset_accuracy"],
                             ["Micro F1", "Macro F1", "Micro precision", "Micro recall", "Exact-match"]):
        ui.kpi(col, lab, f"{t[key]:.3f}")
    pt = pd.DataFrame(t["per_theme"])
    fig = go.Figure()
    for i, m in enumerate(["precision", "recall", "f1"]):
        fig.add_bar(x=pt.theme_name, y=pt[m], name=m.title(), marker=dict(color=ui.CATEGORICAL[i], line=dict(color="#fcfcfb", width=2)),
                    hovertemplate="%{x}<br>" + m + ": %{y:.3f}<extra></extra>")
    fig.update_layout(barmode="group", title="Per-theme metrics")
    fig.update_yaxes(range=[0, 1.05])
    st.plotly_chart(ui.style(fig, 360), use_container_width=True)
    st.dataframe(pt, hide_index=True, use_container_width=True)
with tab3:
    k = st.columns(4)
    ui.kpi(k[0], "Injected anomalies detected", f"{q['injected_detected']}/{q['injected_total']}",
           f"recall {q['detection_recall']:.2%}" if q["detection_recall"] is not None else "")
    ui.kpi(k[1], "Unexplained issues", ui.fmt_int(q["unexplained_issues"]), "not matching an injected anomaly")
    ui.kpi(k[2], "Validation pass rate", f"{q['validation_pass_rate']:.2%}")
    ui.kpi(k[3], "Detection precision", f"{q['detection_precision']:.2%}" if q["detection_precision"] else "–")
    st.dataframe(pd.DataFrame([{"issue_type": k_, **{x: v[x] for x in ("injected", "detected", "recall")},
                                "missed": ", ".join(v["missed_record_ids"][:5])}
                               for k_, v in q["injected_anomaly_detection"].items()]), hide_index=True,
                 use_container_width=True)
    if q["unexplained_by_rule"]:
        st.markdown("**Unexplained issues by rule** (real data-quality findings, not injected)")
        st.json(q["unexplained_by_rule"])
with tab4:
    k = st.columns(4)
    ui.kpi(k[0], "Shift alerts", ui.fmt_int(b["sentiment_shift_alerts"]),
           f"{b['alerts_overlapping_known_scenarios']} overlap a known scenario")
    ui.kpi(k[1], "Scenario recall (theme alerts)", f"{b['scenario_recall_theme_alerts']:.0%}")
    ui.kpi(k[2], "Actions generated", ui.fmt_int(b["actions_generated"]))
    ui.kpi(k[3], "Evidence traceability", f"{b['evidence_traceability']['traceability_rate']:.0%}"
           if b["evidence_traceability"]["traceability_rate"] is not None else "–")
    st.dataframe(pd.DataFrame(b["scenario_events"]), hide_index=True, use_container_width=True)
    st.json(b["evidence_traceability"])
    rep = d.get("reproducibility", {})
    if rep:
        st.markdown("**Reproducibility**")
        st.json(rep, expanded=False)
