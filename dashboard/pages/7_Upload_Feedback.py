"""Upload your own feedback (CSV) and see how InsightPulse analyses it, row by row."""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import requests  # noqa: E402
import streamlit as st  # noqa: E402

import ui_kit as ui  # noqa: E402

STATUS_ICON = {"CREATED": "✅ Analysed", "QUARANTINED": "🔒 Quarantined", "DUPLICATE": "♻️ Duplicate",
               "REJECTED": "⛔ Rejected"}

ui.page("Upload Feedback", "Upload a CSV of your own customer feedback. Every row is validated, checked for "
        "personal data and duplicates, and scored with the active models.", "📤")

with st.expander("CSV format", expanded=False):
    st.markdown(
        "Only **`text`** is required. Everything else is optional:\n\n"
        "| Column | Meaning |\n|---|---|\n"
        "| `text` | The feedback itself (English, Hindi or Telugu) |\n"
        "| `source_type` | `REVIEW` (default), `SURVEY_RESPONSE` or `SUPPORT_MESSAGE` |\n"
        "| `product_name` or `product_id` | Must match the product catalogue (the template uses real names) |\n"
        "| `rating` | 1 to 5. Required for reviews and survey responses |\n"
        "| `feedback_date` | `YYYY-MM-DD`, reviews only. Defaults to today; not in the future or before launch |\n"
        "| `channel_id`, `customer_id`, `survey_id`, `ticket_id`, `nps_score`, `review_title`, `language` "
        "| Optional. Without `customer_id` rows are attributed to a guest customer |\n\n"
        "Limits: UTF-8, up to 2,000 rows and 2 MB per file. Rows are processed independently: "
        "a bad row is reported and skipped, the rest are saved.")

c1, c2 = st.columns([1, 3])
try:
    tpl = requests.get(f"{ui.API_BASE_URL}/api/feedback/upload/template", timeout=30)
    tpl.raise_for_status()
    c1.download_button("⬇ Download template", tpl.content, "insightpulse_upload_template.csv", "text/csv",
                       use_container_width=True)
except requests.RequestException:
    c1.button("⬇ Download template", disabled=True, use_container_width=True)
    st.error(f"Cannot reach the InsightPulse API at {ui.API_BASE_URL}.", icon="🔌")
    st.stop()

up = st.file_uploader("CSV file", type=["csv"], accept_multiple_files=False)
refresh = st.checkbox("Refresh business actions afterwards", value=True,
                      help="Re-runs the action rules so that complaint clusters in your upload can raise actions.")

if up is not None:
    try:
        preview = pd.read_csv(io.BytesIO(up.getvalue()), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        st.caption(f"Preview: {len(preview):,} rows, columns: {', '.join(preview.columns)}")
        st.dataframe(preview.head(10), use_container_width=True, hide_index=True)
    except Exception as exc:  # the API gives the authoritative error on upload
        st.warning(f"Could not preview this file: {exc}")

    if st.button("Upload and analyse", type="primary"):
        with st.spinner("Validating and scoring…"):
            try:
                r = requests.post(f"{ui.API_BASE_URL}/api/feedback/upload",
                                  files={"file": (up.name, up.getvalue(), "text/csv")}, timeout=600)
            except requests.ConnectionError:
                st.error(f"Cannot reach the InsightPulse API at {ui.API_BASE_URL}.", icon="🔌")
                st.stop()
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            st.error(f"Upload rejected ({r.status_code}): {detail}", icon="⚠️")
            st.stop()
        st.session_state["upload_result"] = r.json()
        st.session_state.pop("upload_actions", None)
        st.cache_data.clear()
        if refresh and r.json()["created"]:
            with st.spinner("Refreshing business actions…"):
                ok, body = ui.send("POST", "/api/pipeline/run", {"steps": ["actions"], "wait": True})
            st.session_state["upload_actions"] = (ok, body)

res = st.session_state.get("upload_result")
if not res:
    ui.empty("Download the template, add your feedback rows, then upload the file here.")
    st.stop()

st.subheader(f"Results for {res['filename']}")
st.caption(f"Pipeline run `{res['run_id']}` · {res['rows_total']:,} rows")
k = st.columns(4)
ui.kpi(k[0], "Analysed", ui.fmt_int(res["created"]), "validated and scored")
ui.kpi(k[1], "Quarantined", ui.fmt_int(res["quarantined"]), "personal data found, text redacted")
ui.kpi(k[2], "Duplicates", ui.fmt_int(res["duplicates"]), "already in the system")
ui.kpi(k[3], "Rejected", ui.fmt_int(res["rejected"]), "see the error column")

acts = st.session_state.get("upload_actions")
if acts:
    ok, body = acts
    if ok:
        a = (body.get("steps") or {}).get("actions", {}) if isinstance(body, dict) else {}
        st.success(f"Business actions refreshed: {a.get('created', 0)} new, {a.get('refreshed_existing', 0)} "
                   "existing updated. See the Business Actions page.", icon="🧭")
    else:
        st.warning(f"Upload saved, but refreshing actions failed: {body}")

df = pd.DataFrame(res["results"])
scored = df[df["status"] == "CREATED"] if not df.empty else df
if not scored.empty and scored["sentiment"].notna().any():
    left, right = st.columns(2)
    counts = scored["sentiment"].value_counts()
    fig = go.Figure()
    labels = [s for s in ui.SENTIMENT_ORDER if s in counts.index]
    fig.add_bar(x=[s.title() for s in labels], y=[int(counts[s]) for s in labels], text=[int(counts[s]) for s in labels],
                textposition="outside", marker=dict(color=[ui.SENTIMENT_COLORS[s] for s in labels]),
                hovertemplate="%{x}: %{y}<extra></extra>")
    fig.update_layout(title="Sentiment of your feedback")
    fig = ui.style(fig, 320, legend=False)
    fig.update_layout(bargap=0.55)
    fig.update_yaxes(tickformat=",d", rangemode="tozero")
    left.plotly_chart(fig, use_container_width=True)

    themes = scored.explode("themes")["themes"].dropna().value_counts().sort_values()
    if not themes.empty:
        fig = go.Figure()
        fig.add_bar(y=themes.index, x=themes.values, orientation="h", text=themes.values, textposition="outside",
                    marker=dict(color=ui.SEQ_BLUE[4]), hovertemplate="%{y}: %{x}<extra></extra>")
        fig.update_layout(title="Themes mentioned")
        fig = ui.style(fig, 320, legend=False)
        fig.update_layout(bargap=0.45)
        fig.update_xaxes(tickformat=",d", rangemode="tozero")
        right.plotly_chart(fig, use_container_width=True)
    else:
        right.info("No themes detected with enough confidence.")

view = df.copy()
view["status"] = view["status"].map(STATUS_ICON)
view["themes"] = view["themes"].apply(lambda t: ", ".join(t) if isinstance(t, list) else "")
cols = [c for c in ["row", "status", "sentiment", "confidence", "needs_human_review", "themes", "product_name",
                    "language", "text", "feedback_id", "error"] if c in view.columns]
st.dataframe(view[cols], use_container_width=True, hide_index=True,
             column_config={"confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1,
                                                                          format="%.2f"),
                            "needs_human_review": st.column_config.CheckboxColumn("Needs review")})
st.download_button("⬇ Download results (CSV)", view[cols].to_csv(index=False).encode("utf-8"),
                   f"insightpulse_results_{res['run_id']}.csv", "text/csv")
st.caption("Analysed rows also appear in the Feedback Explorer (search for their text) and in every analytics page.")
