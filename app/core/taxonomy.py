"""Controlled vocabularies: the theme taxonomy and sentiment labels.

This module is the single source of truth for the allowed label space. The
database seed (``database/seed.sql``), the generator, the theme classifier and
the validation engine all import from here, so a theme outside this taxonomy
can never be silently accepted.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeDef:
    theme_id: str
    code: str
    name: str
    description: str
    keywords: tuple[str, ...]


THEMES: tuple[ThemeDef, ...] = (
    ThemeDef("THEME-000001", "PRODUCT_QUALITY", "Product Quality",
             "Build quality, durability, defects and performance of the product itself.",
             ("quality", "defect", "broke", "durable", "battery", "stitching")),
    ThemeDef("THEME-000002", "PRICING", "Pricing",
             "Price level, value for money, discounts and price changes.",
             ("price", "expensive", "value", "discount", "cost")),
    ThemeDef("THEME-000003", "DELIVERY", "Delivery",
             "Shipping speed, courier behaviour, delivery accuracy and tracking.",
             ("delivery", "courier", "late", "shipping", "tracking")),
    ThemeDef("THEME-000004", "CUSTOMER_SUPPORT", "Customer Support",
             "Interactions with support agents: responsiveness, helpfulness, resolution.",
             ("support", "agent", "helpline", "response", "resolved")),
    ThemeDef("THEME-000005", "USER_EXPERIENCE", "User Experience",
             "Ease of use of the app/website/product, navigation and design.",
             ("app", "website", "easy", "navigation", "interface")),
    ThemeDef("THEME-000006", "PAYMENT", "Payment",
             "Checkout, payment methods, failed or double charges, UPI and cards.",
             ("payment", "upi", "card", "charged", "checkout")),
    ThemeDef("THEME-000007", "PRODUCT_AVAILABILITY", "Product Availability",
             "Stock-outs, restocking, variant and size availability.",
             ("stock", "available", "restock", "sold out")),
    ThemeDef("THEME-000008", "PACKAGING", "Packaging",
             "Packaging condition, protection, sustainability and presentation.",
             ("packaging", "box", "damaged", "seal", "plastic")),
    ThemeDef("THEME-000009", "REFUNDS_RETURNS", "Refunds and Returns",
             "Return pickup, refund speed, replacement and return policy.",
             ("refund", "return", "replacement", "pickup")),
    ThemeDef("THEME-000010", "TECHNICAL_ISSUES", "Technical Issues",
             "Crashes, login failures, bugs, connectivity and firmware problems.",
             ("crash", "bug", "login", "error", "firmware")),
)

THEME_BY_ID: dict[str, ThemeDef] = {t.theme_id: t for t in THEMES}
THEME_BY_CODE: dict[str, ThemeDef] = {t.code: t for t in THEMES}
ALLOWED_THEME_IDS: frozenset[str] = frozenset(THEME_BY_ID)
THEME_IDS_ORDERED: tuple[str, ...] = tuple(t.theme_id for t in THEMES)

SENTIMENT_LABELS: tuple[tuple[str, str, int, str], ...] = (
    ("POSITIVE", "Positive", 1, "Feedback expresses satisfaction or praise."),
    ("NEGATIVE", "Negative", -1, "Feedback expresses dissatisfaction or a complaint (incl. sarcasm)."),
    ("NEUTRAL", "Neutral", 0, "Factual, ambiguous or suggestion-only feedback without clear polarity."),
    ("MIXED", "Mixed", 0, "Feedback contains both clearly positive and clearly negative statements."),
)
SENTIMENT_CODES: tuple[str, ...] = tuple(s[0] for s in SENTIMENT_LABELS)


class TaxonomyError(ValueError):
    """Raised when a theme outside the allowed taxonomy is supplied."""


def validate_theme_ids(theme_ids: list[str] | tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split supplied theme identifiers into (valid, invalid) lists, preserving order."""
    valid = [t for t in theme_ids if t in ALLOWED_THEME_IDS]
    invalid = [t for t in theme_ids if t not in ALLOWED_THEME_IDS]
    return valid, invalid


def require_valid_themes(theme_ids: list[str]) -> list[str]:
    """Return the themes if all are valid, otherwise raise :class:`TaxonomyError`."""
    _, invalid = validate_theme_ids(theme_ids)
    if invalid:
        raise TaxonomyError(f"Themes outside the allowed taxonomy: {sorted(set(invalid))}")
    return list(theme_ids)


def render_seed_sql() -> str:
    """Render reference-data SQL (themes + sentiment labels) deterministically."""

    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    lines = [
        "-- InsightPulse reference data. GENERATED from app/core/taxonomy.py; do not edit by hand.",
        "-- Regenerate with: python -m scripts.setup_db --write-seed",
        "BEGIN;",
    ]
    for code, name, polarity, desc in SENTIMENT_LABELS:
        order = SENTIMENT_CODES.index(code) + 1
        lines.append(
            "INSERT INTO sentiment_labels (label_code, display_name, polarity, description, display_order) "
            f"VALUES ({q(code)}, {q(name)}, {polarity}, {q(desc)}, {order}) ON CONFLICT (label_code) DO NOTHING;"
        )
    for t in THEMES:
        kw = "ARRAY[" + ", ".join(q(k) for k in t.keywords) + "]::text[]"
        lines.append(
            "INSERT INTO themes (theme_id, theme_code, theme_name, description, keywords, is_active, effective_from) "
            f"VALUES ({q(t.theme_id)}, {q(t.code)}, {q(t.name)}, {q(t.description)}, {kw}, TRUE, DATE '2026-01-01') "
            "ON CONFLICT (theme_id) DO NOTHING;"
        )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"
