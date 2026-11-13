"""Sentiment classifier: TF-IDF (word + char) -> multinomial Logistic Regression."""
from __future__ import annotations

from dataclasses import dataclass, field

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.pipeline import Pipeline

from app.core.taxonomy import SENTIMENT_CODES
from ml.common import build_text_features

MODEL_NAME = "tfidf-logreg-sentiment"
MODEL_MAJOR = "1.0"
C_GRID = (0.5, 1.0, 2.0, 4.0, 8.0)


@dataclass
class SentimentModel:
    pipeline: Pipeline
    classes: list[str]
    model_version: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def train(cls, x_train: list[str], y_train: list[str], x_val: list[str], y_val: list[str],
              version_hash: str, seed: int = 20261022) -> "SentimentModel":
        """Select C on the VALIDATION split (macro-F1); the model is fit on TRAIN only."""
        # Features depend only on TRAIN text, so they are fitted once and reused for every C.
        features = build_text_features().fit(x_train)
        xt, xv = features.transform(x_train), features.transform(x_val)
        search = []
        best = None
        for c in C_GRID:
            clf = LogisticRegression(C=c, max_iter=3000, class_weight="balanced", random_state=seed).fit(xt, y_train)
            pred = clf.predict(xv)
            score = f1_score(y_val, pred, average="macro")
            search.append({"C": c, "val_macro_f1": round(float(score), 4)})
            if best is None or score > best[0]:
                best = (score, c, clf, pred)
        _, c, clf, pred = best
        pipe = Pipeline([("features", features), ("clf", clf)])
        meta = {
            "model_name": MODEL_NAME, "algorithm": "TfidfVectorizer(word 1-2, char_wb 2-5) + LogisticRegression",
            "hyperparameters": {"C": c, "class_weight": "balanced"}, "c_search": search,
            "train_size": len(x_train), "validation_size": len(x_val),
            "train_label_distribution": {k: int(v) for k, v in zip(*np.unique(y_train, return_counts=True))},
            "validation_metrics": {
                "accuracy": round(float(accuracy_score(y_val, pred)), 4),
                "macro_f1": round(float(f1_score(y_val, pred, average="macro")), 4),
                "report": classification_report(y_val, pred, output_dict=True, zero_division=0),
            },
        }
        version = f"{MODEL_MAJOR}.0+{version_hash}"
        meta["model_version"] = version
        return cls(pipe, list(pipe.classes_), version, meta)

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        """Probabilities with columns ordered as SENTIMENT_CODES."""
        proba = self.pipeline.predict_proba(texts)
        idx = [self.classes.index(c) if c in self.classes else None for c in SENTIMENT_CODES]
        out = np.zeros((len(texts), len(SENTIMENT_CODES)))
        for j, i in enumerate(idx):
            if i is not None:
                out[:, j] = proba[:, i]
        return out

    def save(self, path) -> None:
        joblib.dump({"pipeline": self.pipeline, "classes": self.classes, "model_version": self.model_version,
                     "metadata": self.metadata}, path)

    @classmethod
    def load(cls, path) -> "SentimentModel":
        d = joblib.load(path)
        return cls(d["pipeline"], d["classes"], d["model_version"], d["metadata"])
