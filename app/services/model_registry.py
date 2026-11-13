"""Model artefact registry on disk.

Layout::

    ml/saved_models/<kind>/<model_version>.joblib   (git-ignored binary)
    ml/saved_models/<kind>/<model_version>.json     (metadata, committed)
    ml/saved_models/<kind>/ACTIVE.json              (pointer to the active version)
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.config import get_settings

KINDS = ("sentiment", "themes")
_CACHE: dict[tuple[str, str], object] = {}
_LOCK = threading.Lock()


class ModelNotTrainedError(RuntimeError):
    pass


def _dir(kind: str, model_dir: Path | None = None) -> Path:
    d = Path(model_dir or get_settings().model_dir) / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(version: str) -> str:
    return version.replace("+", "_")


def active_model_info(kind: str, model_dir: Path | None = None) -> dict | None:
    pointer = _dir(kind, model_dir) / "ACTIVE.json"
    if not pointer.exists():
        return None
    return json.loads(pointer.read_text(encoding="utf-8"))


def available_models(model_dir: Path | None = None) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for kind in KINDS:
        info = active_model_info(kind, model_dir)
        out[kind] = info["model_version"] if info else None
    return out


def save_model(kind: str, model, model_dir: Path | None = None) -> dict:
    d = _dir(kind, model_dir)
    stem = _safe(model.model_version)
    model.save(d / f"{stem}.joblib")
    (d / f"{stem}.json").write_text(json.dumps(model.metadata, indent=2, default=str) + "\n", encoding="utf-8")
    pointer = {"model_version": model.model_version, "file": f"{stem}.joblib",
               "model_name": model.metadata.get("model_name")}
    (d / "ACTIVE.json").write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    return pointer


def load_active(kind: str, model_dir: Path | None = None):
    """Load (and cache) the active model of ``kind``."""
    info = active_model_info(kind, model_dir)
    if info is None:
        raise ModelNotTrainedError(f"No trained {kind} model. Run the pipeline 'train' step first.")
    key = (kind, info["model_version"])
    with _LOCK:
        if key not in _CACHE:
            path = _dir(kind, model_dir) / info["file"]
            if not path.exists():
                raise ModelNotTrainedError(f"Model file {path} missing; retrain with the pipeline.")
            if kind == "sentiment":
                from ml.sentiment.model import SentimentModel
                _CACHE[key] = SentimentModel.load(path)
            else:
                from ml.themes.model import ThemeModel
                _CACHE[key] = ThemeModel.load(path)
        return _CACHE[key]
