"""Multi-label theme classifier: TF-IDF -> OneVsRest(LogisticRegression).

The label space is exactly the controlled taxonomy (THEME_IDS_ORDERED); a
per-theme decision threshold is tuned on the VALIDATION split.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MultiLabelBinarizer

from app.core.taxonomy import THEME_IDS_ORDERED, require_valid_themes
from ml.common import build_text_features

MODEL_NAME = "tfidf-ovr-logreg-themes"
MODEL_MAJOR = "1.0"
THRESHOLD_GRID = tuple(round(x, 2) for x in np.arange(0.15, 0.75, 0.05))


@dataclass
class ThemeModel:
    pipeline: Pipeline
    thresholds: dict[str, float]
    model_version: str
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def binarizer() -> MultiLabelBinarizer:
        return MultiLabelBinarizer(classes=list(THEME_IDS_ORDERED)).fit([list(THEME_IDS_ORDERED)])

    @classmethod
    def train(cls, x_train: list[str], y_train: list[list[str]], x_val: list[str], y_val: list[list[str]],
              version_hash: str, seed: int = 20261022, c: float = 4.0) -> "ThemeModel":
        for labels in y_train + y_val:  # label space = taxonomy only
            require_valid_themes(labels)
        mlb = cls.binarizer()
        yt, yv = mlb.transform(y_train), mlb.transform(y_val)
        pipe = Pipeline([
            ("features", build_text_features()),
            ("clf", OneVsRestClassifier(LogisticRegression(C=c, max_iter=3000, class_weight="balanced",
                                                           random_state=seed))),
        ]).fit(x_train, yt)
        proba = pipe.predict_proba(x_val)
        thresholds = {}
        for j, theme in enumerate(THEME_IDS_ORDERED):
            scores = [(f1_score(yv[:, j], proba[:, j] >= t, zero_division=0), t) for t in THRESHOLD_GRID]
            thresholds[theme] = float(max(scores)[1]) if yv[:, j].sum() else 0.5
        pred = cls._apply(proba, thresholds)
        meta = {
            "model_name": MODEL_NAME,
            "algorithm": "TfidfVectorizer(word 1-2, char_wb 2-5) + OneVsRestClassifier(LogisticRegression)",
            "hyperparameters": {"C": c, "class_weight": "balanced"}, "thresholds": thresholds,
            "label_space": list(THEME_IDS_ORDERED), "train_size": len(x_train), "validation_size": len(x_val),
            "validation_metrics": {
                "micro_f1": round(float(f1_score(yv, pred, average="micro", zero_division=0)), 4),
                "macro_f1": round(float(f1_score(yv, pred, average="macro", zero_division=0)), 4),
                "micro_precision": round(float(precision_score(yv, pred, average="micro", zero_division=0)), 4),
                "micro_recall": round(float(recall_score(yv, pred, average="micro", zero_division=0)), 4),
            },
        }
        version = f"{MODEL_MAJOR}.0+{version_hash}"
        meta["model_version"] = version
        return cls(pipe, thresholds, version, meta)

    @staticmethod
    def _apply(proba: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
        t = np.array([thresholds[th] for th in THEME_IDS_ORDERED])
        return (proba >= t).astype(int)

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        return self.pipeline.predict_proba(texts)

    def predict(self, texts: list[str]) -> list[list[tuple[str, float]]]:
        """For each text: [(theme_id, probability), ...] above the tuned thresholds."""
        proba = self.predict_proba(texts)
        mask = self._apply(proba, self.thresholds)
        return [[(THEME_IDS_ORDERED[j], float(proba[i, j])) for j in np.flatnonzero(mask[i])]
                for i in range(len(texts))]

    def save(self, path) -> None:
        joblib.dump({"pipeline": self.pipeline, "thresholds": self.thresholds,
                     "model_version": self.model_version, "metadata": self.metadata}, path)

    @classmethod
    def load(cls, path) -> "ThemeModel":
        d = joblib.load(path)
        return cls(d["pipeline"], d["thresholds"], d["model_version"], d["metadata"])
