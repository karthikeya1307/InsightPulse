"""Sentiment shift detection: daily/weekly/monthly trends, MoM and alerts."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Trend Detection", "Unusual increases in negative feedback, with configurable alert thresholds.", "📉")
themes = ui.get("/api/analytics/themes") or {}
theme_opts = {r["theme_name"]: r["theme_id"] for r in themes.get("frequency", [])}
with st.container(border=True):
    c = st.columns([1, 1.4, 1, 1, 1, 1])
    gran = c[0].selectbox("Granularity", ["week", "day", "month"])
    theme = c[1].selectbox("Theme", ["All themes"] + sorted(theme_opts))
    z = c[2].number_input("z-score ≥", 0.5, 10.0, 2.0, 0.25)
    inc = c[3].number_input("Increase ≥ (pp)", 0.0, 100.0, 10.0, 1.0)
    vol = c[4].number_input("Min volume", 1, 10000, 8)
    base = c[5].number_input("Baseline periods", 2, 52, 6)
d = ui.get("/api/analytics/trends", {"granularity": gran, "theme_id": theme_opts.get(theme), "z_threshold": z,
                                     "min_increase": inc / 100, "min_volume": vol, "baseline_periods": base})
if d is None:
    st.stop()
series = pd.DataFrame(d["series"])
if series.empty:
    ui.empty("No scored feedback yet.")
    st.stop()
alerts = pd.DataFrame(d["alerts"])
fig = go.Figure()
fig.add_scatter(x=series.period, y=series.negative_rate * 100, name="Negative share", mode="lines+markers",
                line=dict(color=ui.SENTIMENT_COLORS["NEGATIVE"], width=2), marker=dict(size=8 if gran != "day" else 4),
                customdata=series[["negative", "total"]],
                hovertemplate="%{x}<br>Negative: %{y:.1f}% (%{customdata[0]} of %{customdata[1]})<extra></extra>")
if not alerts.empty:
    scope = alerts[alerts.theme_id.isna()] if theme == "All themes" else alerts[alerts.theme_id == theme_opts[theme]]
    if not scope.empty:
        fig.add_scatter(x=scope.period_start, y=scope.negative_rate * 100, name="Alert", mode="markers",
                        marker=dict(symbol="diamond-open", size=15, color="#0b0b0b", line=dict(width=2)),
                        customdata=scope[["baseline_rate", "z_score"]],
                        hovertemplate="Alert %{x}<br>baseline %{customdata[0]:.0%} · z=%{customdata[1]}<extra></extra>")
fig.update_layout(title=f"Negative share per {gran} — {theme}")
fig.update_yaxes(ticksuffix="%", rangemode="tozero")
st.plotly_chart(ui.style(fig, 360), use_container_width=True)
vol_fig = go.Figure(go.Bar(x=series.period, y=series.total, marker_color=ui.CATEGORICAL[0],
                           hovertemplate="%{x}: %{y:,} records<extra></extra>"))
vol_fig.update_layout(title=f"Feedback volume per {gran}")
st.plotly_chart(ui.style(vol_fig, 220, legend=False), use_container_width=True)

st.markdown("##### Detected sentiment shifts")
if alerts.empty:
    st.success("No unusual increases with the current thresholds.", icon="✅")
else:
    show = alerts.assign(scope=alerts.theme_name.fillna("Overall"))[
        ["period_start", "period_end", "scope", "negative_rate", "baseline_rate", "increase", "z_score", "volume",
         "negative_count", "evidence_feedback_ids"]]
    st.dataframe(show, hide_index=True, use_container_width=True,
                 column_config={"negative_rate": st.column_config.NumberColumn(format="%.2f"),
                                "evidence_feedback_ids": st.column_config.ListColumn("Evidence (feedback ids)")})
st.markdown("##### Month-over-month")
mom = pd.DataFrame(d["month_over_month"])
st.dataframe(mom, hide_index=True, use_container_width=True)
st.caption(f"Rule: alert when volume ≥ {vol}, negative share rises ≥ {inc:.0f} pp above the trailing "
           f"{base}-period baseline and z ≥ {z}. Thresholds are also configurable in .env.")
