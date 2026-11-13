"""Theme classification service (multi-label, taxonomy-constrained)."""
from __future__ import annotations

from app.core.taxonomy import ALLOWED_THEME_IDS, TaxonomyError
from app.services.model_registry import load_active


def classify_themes(texts: list[str]) -> tuple[list[list[tuple[str, float]]], str]:
    """Return per-text [(theme_id, confidence)] and the theme model version."""
    model = load_active("themes")
    preds = model.predict(texts)
    for themes in preds:  # defence in depth: never emit a theme outside the taxonomy
        for theme_id, _ in themes:
            if theme_id not in ALLOWED_THEME_IDS:
                raise TaxonomyError(f"Model produced theme outside taxonomy: {theme_id}")
    return preds, model.model_version
