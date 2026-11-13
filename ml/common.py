"""Shared ML utilities: feature extraction and leakage guards.

The ONLY model input feature is the feedback text. Ground-truth / label
columns are explicitly forbidden and checked at runtime, so a label can never
be passed to a model as a feature by mistake.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion

from app.utils.text_preprocessing import preprocess_for_model

ALLOWED_FEATURE_COLUMNS = frozenset({"text"})
FORBIDDEN_FEATURE_COLUMNS = frozenset({
    "sentiment_label", "theme_labels", "theme_ids", "true_sentiment", "true_theme_ids", "ground_truth_label",
    "label", "labels", "rating", "nps_score", "dataset_split", "declared_split", "annotation_note",
    "is_sarcasm", "is_ambiguous", "scenario_event", "review_status",
})


class LeakageError(RuntimeError):
    """Raised when label/ground-truth information would enter model input."""


def assert_feature_columns(columns: Iterable[str]) -> None:
    cols = set(columns)
    forbidden = cols & FORBIDDEN_FEATURE_COLUMNS
    if forbidden:
        raise LeakageError(f"Forbidden label/ground-truth columns in model input: {sorted(forbidden)}")
    unknown = cols - ALLOWED_FEATURE_COLUMNS
    if unknown:
        raise LeakageError(f"Unexpected model input columns: {sorted(unknown)}")


def assert_no_test_records(splits: Iterable[str]) -> None:
    bad = [s for s in splits if s not in ("TRAIN", "VALIDATION")]
    if bad:
        raise LeakageError(f"{len(bad)} records from splits {sorted(set(bad))} found in training input")


def build_text_features(max_word_features: int = 40000, max_char_features: int = 60000) -> FeatureUnion:
    """Word uni/bi-grams (English stems + negation) and char n-grams (script-agnostic)."""
    word = TfidfVectorizer(preprocessor=preprocess_for_model, tokenizer=str.split, token_pattern=None,
                           lowercase=False, ngram_range=(1, 2), min_df=2, max_features=max_word_features,
                           sublinear_tf=True)
    char = TfidfVectorizer(preprocessor=preprocess_for_model, analyzer="char_wb", ngram_range=(2, 5),
                           min_df=2, max_features=max_char_features, sublinear_tf=True)
    return FeatureUnion([("word", word), ("char", char)])


def fingerprint(*parts: object) -> str:
    """Short deterministic hash used in model versions."""
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:10]
