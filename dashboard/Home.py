"""InsightPulse - Overview dashboard. Run: streamlit run dashboard/Home.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Overview", "Unified view of survey responses, product reviews and support messages.", "📈")
data = ui.get("/api/analytics/overview")
if data is None:
    st.stop()
c, s = data["counts"], data["sentiment"]
if not c["total_feedback"]:
    ui.empty("No feedback loaded yet. Generate data and run the pipeline (see README).")
    st.stop()

k = st.columns(6)
ui.kpi(k[0], "Total feedback", ui.fmt_int(c["total_feedback"]),
       f"{ui.fmt_int(c['reviews'])} reviews · {ui.fmt_int(c['survey_responses'])} surveys · {ui.fmt_int(c['support_messages'])} support")
ui.kpi(k[1], "Analysable", ui.fmt_int(c["analysable"]), f"{c['duplicates']} duplicates · {c['quarantined']} quarantined")
ui.kpi(k[2], "Negative", f"{s['negative_pct']:.1f}%", f"{ui.fmt_int(s['negative'])} records")
ui.kpi(k[3], "Positive", f"{s['positive_pct']:.1f}%", f"{ui.fmt_int(s['positive'])} records")
ui.kpi(k[4], "High-priority actions", ui.fmt_int(data["action_counts"]["high_priority_active"]),
       f"{data['action_counts']['active']} active of {data['action_counts']['total']}")
ui.kpi(k[5], "Data quality issues", ui.fmt_int(data["data_quality"]["total_issues"]), "detected & handled")
st.write("")

left, right = st.columns([1, 1.4])
with left:
    dist = pd.DataFrame({"label": ui.SENTIMENT_ORDER, "pct": [s[f"{l.lower()}_pct"] for l in ui.SENTIMENT_ORDER],
                         "n": [s[l.lower()] for l in ui.SENTIMENT_ORDER]})
    fig = go.Figure(go.Bar(x=dist.pct, y=[l.title() for l in dist.label], orientation="h",
                           marker=dict(color=[ui.SENTIMENT_COLORS[l] for l in dist.label]),
                           text=[f"{p:.1f}%  ({n:,})" for p, n in zip(dist.pct, dist.n)], textposition="outside",
                           cliponaxis=False, hovertemplate="%{y}: %{x:.1f}%<extra></extra>"))
    fig.update_layout(title="Sentiment distribution", yaxis=dict(autorange="reversed"))
    fig.update_xaxes(ticksuffix="%", range=[0, max(dist.pct) * 1.35])
    st.plotly_chart(ui.style(fig, 300, legend=False), use_container_width=True)
with right:
    trend = pd.DataFrame(data["negative_trend_weekly"])
    if trend.empty:
        ui.empty("No trend data yet.")
    else:
        fig = go.Figure(go.Scatter(x=trend.week, y=trend.negative_rate * 100, mode="lines+markers", name="Negative share",
                                   line=dict(color=ui.SENTIMENT_COLORS["NEGATIVE"], width=2),
                                   marker=dict(size=8, line=dict(color="#fcfcfb", width=2)),
                                   customdata=trend[["negative", "total"]],
                                   hovertemplate="Week of %{x}<br>Negative: %{y:.1f}% (%{customdata[0]} of %{customdata[1]})<extra></extra>"))
        alerts = [a for a in data["recent_alerts"] if a["scope"] == "OVERALL"]
        if alerts:
            fig.add_scatter(x=[a["period_start"] for a in alerts], y=[a["negative_rate"] * 100 for a in alerts],
                            mode="markers", name="Shift alert", marker=dict(symbol="diamond-open", size=14,
                                                                            color="#0b0b0b", line=dict(width=2)),
                            hovertemplate="Alert: %{x}<extra></extra>")
        fig.update_layout(title="Negative sentiment trend (weekly share)")
        fig.update_yaxes(ticksuffix="%", rangemode="tozero")
        st.plotly_chart(ui.style(fig, 300, legend=bool(alerts)), use_container_width=True)

left, right = st.columns([1, 1.4])
with left:
    themes = pd.DataFrame(data["top_themes"])
    if themes.empty:
        ui.empty("No themes predicted yet.")
    else:
        fig = go.Figure(go.Bar(x=themes.mentions, y=themes.theme_name, orientation="h", marker_color=ui.CATEGORICAL[0],
                               text=[f"{m:,} · {ns:.0%} negative" for m, ns in zip(themes.mentions, themes.negative_share)],
                               textposition="outside", cliponaxis=False,
                               hovertemplate="%{y}: %{x:,} mentions<extra></extra>"))
        fig.update_layout(title="Top recurring themes", yaxis=dict(autorange="reversed"))
        fig.update_xaxes(range=[0, themes.mentions.max() * 1.9])
        st.plotly_chart(ui.style(fig, 300, legend=False), use_container_width=True)
with right:
    st.markdown("##### High-priority action items")
    acts = pd.DataFrame(data["high_priority_actions"])
    if acts.empty:
        ui.empty("No active high-priority actions.")
    else:
        acts["priority"] = acts.priority.map(ui.PRIORITY_ICON)
        st.dataframe(acts[["action_id", "priority", "title", "evidence_count", "owner", "due_date", "status"]],
                     hide_index=True, use_container_width=True, height=280,
                     column_config={"evidence_count": st.column_config.NumberColumn("Evidence")})

st.markdown("##### Data quality summary")
dq = pd.DataFrame(data["data_quality"]["by_rule"])
if dq.empty:
    st.success("No data quality issues detected.", icon="✅")
else:
    st.dataframe(dq, hide_index=True, use_container_width=True)
m = data["models"]
st.caption(f"Models — sentiment `{m.get('sentiment')}` · themes `{m.get('themes')}` · last run "
           f"`{(data.get('last_run') or {}).get('run_id', '–')}`")
