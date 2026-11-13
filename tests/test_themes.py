"""Theme taxonomy and multi-label theme model."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.taxonomy import ALLOWED_THEME_IDS, THEMES, TaxonomyError, require_valid_themes, validate_theme_ids


def test_taxonomy_has_ten_unique_themes():
    assert len(THEMES) == 10
    assert len({t.theme_id for t in THEMES}) == 10
    assert all(t.theme_id.startswith("THEME-") for t in THEMES)


def test_out_of_taxonomy_theme_is_rejected():
    valid, invalid = validate_theme_ids(["THEME-000001", "THEME-000099", "SHIPPING_SPEED"])
    assert valid == ["THEME-000001"] and set(invalid) == {"THEME-000099", "SHIPPING_SPEED"}
    with pytest.raises(TaxonomyError):
        require_valid_themes(["THEME-000001", "THEME-000099"])


def test_classify_themes_returns_taxonomy_only(engine):
    from app.services.theme_service import classify_themes

    preds, version = classify_themes([
        "Delivery was late by a week and the courier was rude.",
        "The battery drains in two hours and the price is too high for this quality.",
        "ok",
    ])
    assert version
    assert len(preds) == 3
    for themes in preds:
        for theme_id, p in themes:
            assert theme_id in ALLOWED_THEME_IDS and 0.0 <= p <= 1.0
    assert len(preds[1]) >= 1


def test_stored_themes_are_in_taxonomy(engine):
    with engine.connect() as c:
        bad = c.execute(text("SELECT count(*) FROM feedback_themes WHERE theme_id <> ALL(:t)"),
                        {"t": sorted(ALLOWED_THEME_IDS)}).scalar()
        multi = c.execute(text("""SELECT count(*) FROM (SELECT feedback_id FROM v_current_themes
                                  GROUP BY 1 HAVING count(*) > 1) x""")).scalar()
    assert bad == 0
    assert multi > 0  # multi-label output is actually produced


def test_theme_model_validation_metrics_recorded(engine):
    from app.services.model_registry import load_active

    info = load_active("themes").metadata
    vm = info["validation_metrics"]
    for k in ("micro_f1", "macro_f1"):
        assert 0.0 <= vm[k] <= 1.0
    assert set(info["thresholds"]) == set(ALLOWED_THEME_IDS)
