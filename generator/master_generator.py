"""Master (parent) data generation: channels, products, customers, surveys.

Master data is always generated before any dependent transaction record so that
every child row references an existing parent id.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

from app.core.taxonomy import THEMES
from app.utils import id_generator as ids
from generator.config import (
    CHANNELS, ID_YEAR, SURVEY_CHANNEL_WEIGHTS, ScaleConfig,
)
from generator.text_templates import CITIES, FIRST_NAMES, LAST_NAMES, PRODUCT_CATALOG, SURVEY_QUESTIONS

IST = timezone(timedelta(hours=5, minutes=30))


def ts(d: date, t: time = time(9, 0)) -> str:
    """ISO-8601 timestamp in IST for a date (+ optional time)."""
    return datetime.combine(d, t, tzinfo=IST).isoformat()


def rng_for(seed: int, stream: str) -> random.Random:
    """Independent deterministic RNG stream per entity (string seeding is SHA-512 based)."""
    return random.Random(f"{seed}:{stream}")


def generate_channels() -> list[dict]:
    rows = []
    for i, (code, name, ctype, rev, sur, sup) in enumerate(CHANNELS, start=1):
        rows.append({
            "channel_id": ids.channel_id(i), "channel_code": code, "channel_name": name,
            "channel_type": ctype, "supports_reviews": rev, "supports_surveys": sur,
            "supports_support": sup, "is_active": True, "created_at": ts(date(2022, 1, 1)),
        })
    return rows


def generate_products(cfg: ScaleConfig, seed: int) -> list[dict]:
    rng = rng_for(seed, "products")
    if cfg.n_products > len(PRODUCT_CATALOG):
        raise ValueError(f"Catalogue holds {len(PRODUCT_CATALOG)} products, {cfg.n_products} requested")
    rows = []
    for i, (name, category, brand, price) in enumerate(PRODUCT_CATALOG[: cfg.n_products], start=1):
        launch = date(2023, 1, 1) + timedelta(days=rng.randint(0, 1000))
        discontinued = None
        # Documented lifecycle cases: two mid-2026 launches and one discontinuation.
        if i == 7:
            launch = date(2026, 3, 1)
        elif i == 15:
            launch = date(2026, 5, 15)
        elif i == 18:
            launch, discontinued = date(2024, 2, 1), date(2026, 9, 30)
        rows.append({
            "product_id": ids.product_id(i),
            "sku": f"SKU-{category[:3].upper()}-{i:05d}",
            "product_name": name, "category": category, "brand": brand,
            "unit_price": f"{price:.2f}", "currency": "INR",
            "launch_date": launch.isoformat(),
            "discontinued_date": discontinued.isoformat() if discontinued else "",
            "lifecycle_status": "DISCONTINUED" if discontinued else "ACTIVE",
            "created_at": ts(launch), "updated_at": ts(discontinued or launch),
        })
    return rows


def generate_customers(cfg: ScaleConfig, seed: int) -> list[dict]:
    rng = rng_for(seed, "customers")
    rows = []
    for i in range(1, cfg.n_customers + 1):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        city, state, lang_p = rng.choice(CITIES)
        lang = rng.choices(["en", "hi", "te"], weights=lang_p)[0]
        # 88% joined before the analysis year; the rest during 2026 (date-ordering checks apply).
        if rng.random() < 0.88:
            signup = date(2021, 1, 1) + timedelta(days=rng.randint(0, 1825))
        else:
            signup = date(2026, 1, 1) + timedelta(days=rng.randint(0, 240))
        status = rng.choices(["ACTIVE", "INACTIVE", "CHURNED"], weights=[0.86, 0.09, 0.05])[0]
        cid = ids.customer_id(i)
        rows.append({
            "customer_id": cid, "first_name": first, "last_name": last,
            "email": f"{first.lower()}.{last.lower()}.{i}@example.com",
            "phone": f"+91-9{rng.randint(100000000, 999999999)}",
            "city": city, "state": state, "country": "India",
            "preferred_language": lang,
            "segment": rng.choices(["CONSUMER", "SMB", "ENTERPRISE"], weights=[0.85, 0.12, 0.03])[0],
            "signup_date": signup.isoformat(), "lifecycle_status": status,
            "effective_from": signup.isoformat(), "created_at": ts(signup), "updated_at": ts(signup),
        })
    return rows


def generate_surveys(cfg: ScaleConfig, seed: int, products: list[dict], channels: list[dict]) -> list[dict]:
    """Surveys staggered across the period so every day is covered by >=1 survey."""
    rng = rng_for(seed, "surveys")
    code_to_id = {c["channel_code"]: c["channel_id"] for c in channels}
    period = (cfg.end_date - cfg.start_date).days
    rows = []
    types = ["CSAT", "NPS", "PRODUCT_FEEDBACK", "POST_SUPPORT"]
    for i in range(1, cfg.n_surveys + 1):
        stype = types[(i - 1) % 4] if i <= 4 else rng.choices(types, weights=[0.35, 0.25, 0.30, 0.10])[0]
        offset = int((i - 1) * period / cfg.n_surveys)
        start = cfg.start_date + timedelta(days=max(0, offset - rng.randint(0, 10)))
        duration = rng.randint(max(60, int(2.5 * period / cfg.n_surveys)), max(90, int(4 * period / cfg.n_surveys)))
        end = start + timedelta(days=duration)
        product = rng.choice(products) if stype == "PRODUCT_FEEDBACK" else None
        if product and date.fromisoformat(product["launch_date"]) > start:
            start = date.fromisoformat(product["launch_date"])
            end = max(end, start + timedelta(days=60))
        if product and product["discontinued_date"]:
            # a product survey cannot run after the product is discontinued
            end = min(end, date.fromisoformat(product["discontinued_date"]))
        chan = rng.choices(list(SURVEY_CHANNEL_WEIGHTS), weights=list(SURVEY_CHANNEL_WEIGHTS.values()))[0]
        title = {
            "CSAT": "Customer Satisfaction Pulse", "NPS": "Net Promoter Score Survey",
            "PRODUCT_FEEDBACK": f"Product Feedback: {product['product_name'] if product else ''}",
            "POST_SUPPORT": "Post-Support Experience Survey",
        }[stype]
        rows.append({
            "survey_id": ids.survey_id(i, ID_YEAR), "title": f"{title} #{i}", "survey_type": stype,
            "product_id": product["product_id"] if product else "",
            "channel_id": code_to_id[chan], "question_text": SURVEY_QUESTIONS[stype],
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "status": "CLOSED" if end < cfg.end_date else "ACTIVE",
            "created_at": ts(start - timedelta(days=7)), "updated_at": ts(min(end, cfg.end_date)),
        })
    return rows


def generate_reference() -> tuple[list[dict], list[dict]]:
    """Theme taxonomy and sentiment labels as exportable rows."""
    from app.core.taxonomy import SENTIMENT_LABELS

    themes = [{"theme_id": t.theme_id, "theme_code": t.code, "theme_name": t.name,
               "description": t.description, "keywords": "|".join(t.keywords)} for t in THEMES]
    labels = [{"label_code": c, "display_name": n, "polarity": p, "description": d}
              for c, n, p, d in SENTIMENT_LABELS]
    return themes, labels
