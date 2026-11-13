"""Shared dashboard UI: API client, theme, KPI cards, chart helpers.

The dashboard holds NO business logic: every number comes from the FastAPI
backend (``API_BASE_URL``). This module only formats and renders.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
EVAL_TOKEN = os.getenv("EVALUATION_ACCESS_TOKEN", "")
if not EVAL_TOKEN:  # read local .env for the analyst workstation (never committed)
    _env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(_env):
        for line in open(_env, encoding="utf-8"):
            if line.startswith("EVALUATION_ACCESS_TOKEN="):
                EVAL_TOKEN = line.split("=", 1)[1].strip()

# Validated palette (dataviz reference instance). Stack order keeps POSITIVE and
# NEGATIVE non-adjacent; every chart also carries a legend / labels (secondary encoding).
SENTIMENT_ORDER = ["POSITIVE", "NEUTRAL", "MIXED", "NEGATIVE"]
SENTIMENT_COLORS = {"POSITIVE": "#1baf7a", "NEUTRAL": "#2a78d6", "MIXED": "#eda100", "NEGATIVE": "#e34948"}
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
PRIORITY_ICON = {"CRITICAL": "🔴 Critical", "HIGH": "🟠 High", "MEDIUM": "🟡 Medium", "LOW": "⚪ Low"}
TEXT_PRIMARY, TEXT_SECONDARY, GRID = "#0b0b0b", "#52514e", "#e6e5e0"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, select, textarea { font-family: 'Inter', system-ui, sans-serif; }
.block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1400px; }
h1 { font-weight: 700; letter-spacing: -0.02em; font-size: 1.75rem !important; }
h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
.ip-sub { color: #52514e; margin-top: -0.6rem; margin-bottom: 1.2rem; font-size: 0.95rem; }
.ip-card { background: #ffffff; border: 1px solid #e6e5e0; border-radius: 12px; padding: 14px 16px; height: 100%; }
.ip-card .lbl { color: #52514e; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
.ip-card .val { color: #0b0b0b; font-size: 1.65rem; font-weight: 700; margin-top: 4px; line-height: 1.2; }
.ip-card .note { color: #6b6a64; font-size: 0.8rem; margin-top: 2px; }
.ip-pill { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
           background:#f0efec; color:#0b0b0b; margin-right:6px; }
.ip-quote { border-left: 3px solid #e6e5e0; padding: 6px 12px; margin: 6px 0; color:#0b0b0b; background:#fafaf8; border-radius: 0 8px 8px 0; }
section[data-testid="stSidebar"] { background: #f3f3f1; }
@media (max-width: 640px) { .ip-card .val { font-size: 1.3rem; } }
</style>
"""


def page(title: str, subtitle: str, icon: str = "📊") -> None:
    st.set_page_config(page_title=f"{title} · InsightPulse", page_icon=icon, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("### InsightPulse")
        st.caption("Customer feedback intelligence")
        st.caption(f"API: `{API_BASE_URL}`")
        if st.button("↻ Refresh data", use_container_width=True):
            st.cache_data.clear()
    st.title(title)
    st.markdown(f"<div class='ip-sub'>{subtitle}</div>", unsafe_allow_html=True)


class ApiError(RuntimeError):
    pass


@st.cache_data(ttl=60, show_spinner=False)
def _get(path: str, params: tuple, headers: tuple) -> Any:
    r = requests.get(f"{API_BASE_URL}{path}", params=dict(params), headers=dict(headers), timeout=120)
    if r.status_code >= 400:
        raise ApiError(f"{r.status_code}: {r.json().get('detail', r.text) if r.headers.get('content-type','').startswith('application/json') else r.text}")
    return r.json()


def get(path: str, params: dict | None = None, headers: dict | None = None) -> Any | None:
    """GET from the API; renders a friendly error and returns None on failure."""
    clean = tuple(sorted((k, v) for k, v in (params or {}).items() if v not in (None, "", [])))
    try:
        return _get(path, clean, tuple(sorted((headers or {}).items())))
    except requests.ConnectionError:
        st.error(f"Cannot reach the InsightPulse API at {API_BASE_URL}. Start it with "
                 "`uvicorn app.main:app --port 8000`.", icon="🔌")
    except ApiError as exc:
        st.error(f"API error {exc}", icon="⚠️")
    return None


def send(method: str, path: str, payload: dict) -> tuple[bool, Any]:
    try:
        r = requests.request(method, f"{API_BASE_URL}{path}", json=payload, timeout=600)
    except requests.ConnectionError:
        return False, f"Cannot reach API at {API_BASE_URL}"
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code >= 400:
        return False, body.get("detail", body) if isinstance(body, dict) else body
    st.cache_data.clear()
    return True, body


def kpi(col, label: str, value: str, note: str = "") -> None:
    col.markdown(f"<div class='ip-card'><div class='lbl'>{label}</div><div class='val'>{value}</div>"
                 f"<div class='note'>{note}</div></div>", unsafe_allow_html=True)


def empty(msg: str) -> None:
    st.info(msg, icon="ℹ️")


def style(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=78 if legend else 44, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, sans-serif", size=12, color=TEXT_SECONDARY),
        title_font=dict(size=14, color=TEXT_PRIMARY), hoverlabel=dict(font_family="Inter"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, traceorder="normal") if legend else None,
        showlegend=legend, bargap=0.25,
    )
    if fig.layout.title.text:
        fig.update_layout(title=dict(text=fig.layout.title.text, x=0, xanchor="left", yref="container", y=0.985,
                                     yanchor="top"))
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickfont=dict(color=TEXT_SECONDARY))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=TEXT_SECONDARY))
    return fig


def sentiment_stack(df: pd.DataFrame, name_col: str, title: str, pct: bool = True, height: int = 360) -> go.Figure:
    """Horizontal 100% stacked bars of sentiment per category (POSITIVE/NEGATIVE non-adjacent)."""
    fig = go.Figure()
    for lab in SENTIMENT_ORDER:
        col = f"{lab.lower()}_pct" if pct else lab.lower()
        fig.add_bar(y=df[name_col], x=df[col], name=lab.title(), orientation="h",
                    marker=dict(color=SENTIMENT_COLORS[lab], line=dict(color="#fcfcfb", width=2)),
                    hovertemplate=f"%{{y}}<br>{lab.title()}: %{{x:.1f}}{'%' if pct else ''}<extra></extra>")
    fig.update_layout(barmode="stack", title=title, yaxis=dict(autorange="reversed"))
    fig.update_xaxes(ticksuffix="%" if pct else "", range=[0, 100] if pct else None)
    return style(fig, height)


def fmt_int(n: Any) -> str:
    return f"{int(n):,}" if n is not None else "–"
