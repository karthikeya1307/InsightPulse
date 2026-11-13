"""Feedback Explorer: filter, search and page through unified feedback."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

ui.page("Feedback Explorer", "Search and filter every feedback record; open one to trace it end to end.", "🔎")
products = ui.get("/api/analytics/sentiment") or {}
prod_opts = {r["name"]: r["key"] for r in products.get("by_product", [])}
chan_opts = {r["name"]: r["key"] for r in products.get("by_channel", [])}
themes = ui.get("/api/analytics/themes") or {}
theme_opts = {r["theme_name"]: r["theme_id"] for r in themes.get("frequency", [])}

with st.container(border=True):
    r1 = st.columns([2, 1.2, 1.2, 1.2])
    q = r1[0].text_input("Search text", placeholder="e.g. refund, battery, delivery late")
    product = r1[1].selectbox("Product", ["All"] + sorted(prod_opts))
    channel = r1[2].selectbox("Channel", ["All"] + sorted(chan_opts))
    theme = r1[3].selectbox("Theme", ["All"] + sorted(theme_opts))
    r2 = st.columns([1.2, 1.2, 1, 1, 1.2, 1])
    sentiment = r2[0].selectbox("Sentiment", ["All"] + ui.SENTIMENT_ORDER)
    source = r2[1].selectbox("Source", ["All", "REVIEW", "SURVEY_RESPONSE", "SUPPORT_MESSAGE"])
    language = r2[2].selectbox("Language", ["All", "en", "hi", "te"])
    review = r2[3].selectbox("Human review", ["All", "Flagged", "Not flagged"])
    dates = r2[4].date_input("Date range", value=(date(2026, 1, 1), date(2026, 12, 31)))
    page_size = r2[5].selectbox("Page size", [25, 50, 100], index=0)

params = {"q": q if len(q) >= 2 else None, "product_id": prod_opts.get(product), "channel_id": chan_opts.get(channel),
          "theme_id": theme_opts.get(theme), "sentiment": None if sentiment == "All" else sentiment,
          "source_type": None if source == "All" else source, "language": None if language == "All" else language,
          "needs_review": {"All": None, "Flagged": "true", "Not flagged": "false"}[review],
          "page_size": page_size}
if isinstance(dates, tuple) and len(dates) == 2:
    params["date_from"], params["date_to"] = dates[0].isoformat(), dates[1].isoformat()
key = str(sorted((k, str(v)) for k, v in params.items()))
if st.session_state.get("fe_key") != key:
    st.session_state.fe_key, st.session_state.fe_page = key, 1
res = ui.get("/api/feedback", {**params, "page": st.session_state.fe_page})
if res is None:
    st.stop()
meta = res["meta"]
nav = st.columns([1, 1, 4])
if nav[0].button("◀ Previous", disabled=meta["page"] <= 1):
    st.session_state.fe_page -= 1
    st.rerun()
if nav[1].button("Next ▶", disabled=meta["page"] >= meta["pages"]):
    st.session_state.fe_page += 1
    st.rerun()
nav[2].markdown(f"Page **{meta['page']}** of **{max(meta['pages'], 1)}** · **{meta['total']:,}** matching records")
if not res["items"]:
    ui.empty("No feedback matches these filters.")
    st.stop()
rows = []
for it in res["items"]:
    s = it.get("sentiment") or {}
    rows.append({"feedback_id": it["feedback_id"], "date": it["feedback_date"][:10], "source": it["source_type"],
                 "product": it["product_name"], "channel": it["channel_name"], "lang": it["language"],
                 "rating": it["rating"], "sentiment": s.get("label", "—"), "confidence": s.get("confidence"),
                 "review": "⚑" if s.get("needs_human_review") else "",
                 "themes": ", ".join(t["theme_name"] for t in it["themes"]), "text": it["text"],
                 "status": it["processing_status"]})
df = pd.DataFrame(rows)
event = st.dataframe(df, hide_index=True, use_container_width=True, height=460, on_select="rerun",
                     selection_mode="single-row",
                     column_config={"confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1, format="%.2f"),
                                    "text": st.column_config.TextColumn("Text", width="large")})
sel = event.selection.rows if event and event.selection else []
fid = df.iloc[sel[0]].feedback_id if sel else st.selectbox("Open record", df.feedback_id)
detail = ui.get(f"/api/feedback/{fid}")
if detail:
    st.markdown(f"#### {detail['feedback_id']}")
    st.markdown(f"<div class='ip-quote'>{detail['text']}</div>", unsafe_allow_html=True)
    a, b, c = st.columns(3)
    a.markdown("**Source record**")
    a.json(detail["source_details"], expanded=False)
    b.markdown("**Prediction history** (append-only)")
    b.dataframe(pd.DataFrame([{"run": p["run_id"], "label": p["sentiment_label"], "conf": p["confidence"],
                               "themes": ", ".join(t["theme_name"] for t in p["themes"]),
                               "review": p["review_reason"] or ""} for p in detail["prediction_history"]]),
                hide_index=True, use_container_width=True)
    c.markdown("**Lifecycle**")
    c.dataframe(pd.DataFrame(detail["status_history"])[["from_state", "to_state", "reason"]]
                if detail["status_history"] else pd.DataFrame(), hide_index=True, use_container_width=True)
    if detail["data_quality_issues"]:
        st.warning("Data quality: " + "; ".join(f"{i['rule_code']} → {i['resolution_action']}"
                                                for i in detail["data_quality_issues"]), icon="⚠️")
    if detail["linked_actions"]:
        st.info("Evidence for actions: " + ", ".join(f"{a['action_id']} ({a['priority']}, {a['status']})"
                                                     for a in detail["linked_actions"]), icon="🧭")
