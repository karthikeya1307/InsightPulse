"""Sentiment distribution by product, channel, source and language."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Sentiment Analysis", "How customers feel, broken down by product, channel, source and language.", "💬")
f = st.columns([1.4, 1, 3])
dates = f[0].date_input("Date range", value=(date(2026, 1, 1), date(2026, 12, 31)))
source = f[1].selectbox("Source", ["All", "REVIEW", "SURVEY_RESPONSE", "SUPPORT_MESSAGE"])
params = {"source_type": None if source == "All" else source}
if isinstance(dates, tuple) and len(dates) == 2:
    params.update(date_from=dates[0].isoformat(), date_to=dates[1].isoformat())
d = ui.get("/api/analytics/sentiment", params)
if d is None:
    st.stop()
o = d["overall"]
if not o["total"]:
    ui.empty("No scored feedback for these filters.")
    st.stop()
k = st.columns(5)
ui.kpi(k[0], "Scored feedback", ui.fmt_int(o["total"]))
for col, lab in zip(k[1:], ui.SENTIMENT_ORDER):
    ui.kpi(col, lab.title(), f"{o[f'{lab.lower()}_pct']:.1f}%", f"{ui.fmt_int(o[lab.lower()])} records")
hr = d["human_review"]
st.caption(f"⚑ {hr['flagged']:,} of {hr['total']:,} predictions ({hr['flagged'] / max(hr['total'], 1):.1%}) "
           "are flagged for human review (low confidence, non-English fallback or rating disagreement).")

prod = pd.DataFrame(d["by_product"]).sort_values("negative_pct", ascending=False)
st.plotly_chart(ui.sentiment_stack(prod, "name", "Sentiment by product (sorted by negative share)",
                                   height=max(360, 26 * len(prod) + 80)), use_container_width=True)
a, b = st.columns(2)
with a:
    st.plotly_chart(ui.sentiment_stack(pd.DataFrame(d["by_channel"]), "name", "Sentiment by channel"),
                    use_container_width=True)
with b:
    src = pd.DataFrame(d["by_source"])
    src["name"] = src["name"].str.replace("_", " ").str.title()
    st.plotly_chart(ui.sentiment_stack(src, "name", "Sentiment by feedback source", height=220), use_container_width=True)
    lang = pd.DataFrame(d["by_language"])
    lang["name"] = lang["name"].map({"en": "English", "hi": "Hindi", "te": "Telugu"})
    st.plotly_chart(ui.sentiment_stack(lang, "name", "Sentiment by language", height=220), use_container_width=True)
with st.expander("Table view"):
    st.dataframe(prod, hide_index=True, use_container_width=True)
