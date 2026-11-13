"""Data quality and exceptions."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Data Quality & Exceptions", "Every validation rule, what it found, and how each record was handled.", "🛡️")
s = ui.get("/api/validation/summary")
if s is None:
    st.stop()
t = s["totals"]
k = st.columns(6)
ui.kpi(k[0], "Validation pass rate", f"{t['validation_pass_rate']:.2%}", "feedback items without issues")
ui.kpi(k[1], "Checks passed", f"{t['checks_passed']}/{t['checks_total']}")
ui.kpi(k[2], "Privacy (quarantined)", ui.fmt_int(t["privacy_violations"]))
ui.kpi(k[3], "Duplicates", ui.fmt_int(t["duplicates"]))
ui.kpi(k[4], "Invalid taxonomy", ui.fmt_int(t["invalid_taxonomy"]))
ui.kpi(k[5], "PK / FK violations", f"{t['primary_key_violations']} / {t['foreign_key_violations']}")
st.write("")
checks = pd.DataFrame([{"rule": k_, **{x: v[x] for x in ("description", "issues", "default_resolution", "status")}}
                       for k_, v in s["checks"].items()])
a, b = st.columns([1, 1.3])
with a:
    hit = checks[checks.issues > 0].sort_values("issues")
    if hit.empty:
        st.success("All checks passed.", icon="✅")
    else:
        fig = go.Figure(go.Bar(x=hit.issues, y=hit.rule, orientation="h", marker_color=ui.CATEGORICAL[1],
                               text=hit.issues, textposition="outside", cliponaxis=False,
                               hovertemplate="%{y}: %{x} issues<extra></extra>"))
        fig.update_layout(title="Issues by rule")
        fig.update_xaxes(range=[0, hit.issues.max() * 1.25])
        st.plotly_chart(ui.style(fig, 320, legend=False), use_container_width=True)
with b:
    st.dataframe(checks.assign(status=checks.status.map({"PASS": "✅ PASS", "FAIL": "⚠️ FOUND"})), hide_index=True,
                 use_container_width=True, height=330)
c1, c2 = st.columns(2)
c1.markdown("**Feedback processing status**")
c1.dataframe(pd.DataFrame(list(s["feedback_status"].items()), columns=["status", "records"]), hide_index=True,
             use_container_width=True)
c2.markdown("**Training annotation status**")
c2.dataframe(pd.DataFrame(list(s["annotation_training_status"].items()), columns=["status", "annotations"]),
             hide_index=True, use_container_width=True)

st.markdown("##### Exceptions")
f = st.columns(3)
rule = f[0].selectbox("Rule", ["All"] + checks[checks.issues > 0].rule.tolist())
sev = f[1].selectbox("Severity", ["All", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"])
page = f[2].number_input("Page", 1, 10000, 1)
res = ui.get("/api/validation/issues", {"rule_code": None if rule == "All" else rule,
                                        "severity": None if sev == "All" else sev, "page": page, "page_size": 100})
if res and res["items"]:
    df = pd.DataFrame(res["items"])
    st.dataframe(df[["issue_id", "rule_code", "severity", "entity_type", "record_id", "message", "resolution_action",
                     "detected_at"]], hide_index=True, use_container_width=True, height=380)
    st.caption(f"{res['meta']['total']:,} issues · page {res['meta']['page']} of {res['meta']['pages']}. "
               "Original records are preserved; resolutions are recorded as state-change events.")
elif res:
    ui.empty("No issues for this filter.")
