"""Theme frequency, negative feedback by theme, theme x product and theme trends."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Theme Analysis", "Recurring topics across all feedback sources (multi-label: a record can carry several).", "🏷️")
source = st.selectbox("Source", ["All", "REVIEW", "SURVEY_RESPONSE", "SUPPORT_MESSAGE"])
d = ui.get("/api/analytics/themes", {"source_type": None if source == "All" else source})
if d is None:
    st.stop()
freq = pd.DataFrame(d["frequency"])
if freq.empty:
    ui.empty("No theme predictions yet - run the pipeline.")
    st.stop()
a, b = st.columns(2)
with a:
    fig = go.Figure(go.Bar(x=freq.mentions, y=freq.theme_name, orientation="h", marker_color=ui.CATEGORICAL[0],
                           text=[f"{m:,}" for m in freq.mentions], textposition="outside", cliponaxis=False,
                           hovertemplate="%{y}: %{x:,} mentions<extra></extra>"))
    fig.update_layout(title="Most frequent themes", yaxis=dict(autorange="reversed"))
    fig.update_xaxes(range=[0, freq.mentions.max() * 1.2])
    st.plotly_chart(ui.style(fig, 400, legend=False), use_container_width=True)
with b:
    neg = freq.sort_values("negative", ascending=False)
    fig = go.Figure()
    fig.add_bar(x=neg.negative, y=neg.theme_name, orientation="h", name="Negative",
                marker=dict(color=ui.SENTIMENT_COLORS["NEGATIVE"], line=dict(color="#fcfcfb", width=2)),
                hovertemplate="%{y}: %{x:,} negative<extra></extra>")
    fig.add_bar(x=neg.mentions - neg.negative, y=neg.theme_name, orientation="h", name="Other sentiment",
                marker=dict(color="#c3c2b7", line=dict(color="#fcfcfb", width=2)),
                text=[f"{s:.0%} neg" for s in neg.negative_share], textposition="outside", cliponaxis=False,
                hovertemplate="%{y}: %{x:,} other<extra></extra>")
    fig.update_layout(barmode="stack", title="Negative feedback by theme", yaxis=dict(autorange="reversed"))
    fig.update_xaxes(range=[0, neg.mentions.max() * 1.25])
    st.plotly_chart(ui.style(fig, 400), use_container_width=True)

bp = pd.DataFrame(d["by_product"])
metric = st.radio("Theme x product heatmap shows", ["Negative share", "Mentions"], horizontal=True)
col = "negative_share" if metric == "Negative share" else "mentions"
pivot = bp.pivot_table(index="product_name", columns="theme_name", values=col, fill_value=0)
counts = bp.pivot_table(index="product_name", columns="theme_name", values="mentions", fill_value=0)
fig = go.Figure(go.Heatmap(z=pivot.values, x=pivot.columns, y=pivot.index, colorscale=[[i / 6, c] for i, c in enumerate(ui.SEQ_BLUE)],
                           xgap=2, ygap=2, customdata=counts.reindex_like(pivot).values,
                           hovertemplate="%{y}<br>%{x}<br>" + ("Negative share: %{z:.0%}" if col == "negative_share" else "Mentions: %{z}")
                           + "<br>Mentions: %{customdata}<extra></extra>",
                           colorbar=dict(tickformat=".0%" if col == "negative_share" else ",d", thickness=10)))
fig.update_layout(title=f"Theme distribution by product ({metric.lower()})", yaxis=dict(autorange="reversed"))
st.plotly_chart(ui.style(fig, max(420, 24 * len(pivot) + 120), legend=False), use_container_width=True)

tr = pd.DataFrame(d["monthly_trends"])
top = freq.theme_name.head(5).tolist()
picked = st.multiselect("Themes to trend (max 5 for readability)", freq.theme_name.tolist(), default=top, max_selections=5)
fig = go.Figure()
for i, name in enumerate(picked):
    t = tr[tr.theme_name == name]
    fig.add_scatter(x=t.month, y=t.mentions, name=name, mode="lines+markers",
                    line=dict(color=ui.CATEGORICAL[i], width=2), marker=dict(size=8, line=dict(color="#fcfcfb", width=2)),
                    customdata=t.negative_rate, hovertemplate=f"{name}<br>%{{x}}: %{{y}} mentions, %{{customdata:.0%}} negative<extra></extra>")
fig.update_layout(title="Theme mentions over time (monthly)", hovermode="x unified")
st.plotly_chart(ui.style(fig, 360), use_container_width=True)
st.caption(f"{d['feedback_without_theme']:,} analysable records have no theme above threshold (e.g. ambiguous comments).")
with st.expander("Table view"):
    st.dataframe(freq, hide_index=True, use_container_width=True)
