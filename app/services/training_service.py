"""Model training from validated annotations (TRAIN split) with VALIDATION tuning.

Guarantees
----------
* Only annotations with training_status = ELIGIBLE are used (contradictory,
  leaked, privacy-quarantined and duplicate records were excluded by validation).
* Only the text column is passed to the models (``assert_feature_columns``).
* No TEST-split record can enter training (``assert_no_test_records``).
* Theme labels outside the taxonomy are dropped from targets (they were
  reported as THEME_TAXONOMY issues during validation).
* The human-reviewed ground-truth files are never read here.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.taxonomy import ALLOWED_THEME_IDS
from app.services.model_registry import save_model
from ml.common import assert_feature_columns, assert_no_test_records, fingerprint
from ml.sentiment.model import SentimentModel
from ml.themes.model import ThemeModel

log = get_logger("training")

TRAINING_SQL = """
SELECT a.annotation_id, a.feedback_id, fi.feedback_text AS text, fi.dataset_split,
       a.sentiment_label, a.theme_labels
FROM feedback_annotations a
JOIN feedback_items fi USING (feedback_id)
WHERE a.training_status = 'ELIGIBLE'
  AND fi.processing_status NOT IN ('QUARANTINED', 'DUPLICATE')
ORDER BY a.annotation_id
"""


def load_training_frame(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(text(TRAINING_SQL)).mappings().all())
    if df.empty:
        raise RuntimeError("No ELIGIBLE annotations - run ingestion and validation first.")
    assert_no_test_records(df["dataset_split"])
    df["theme_labels"] = df["theme_labels"].apply(lambda xs: sorted(t for t in xs if t in ALLOWED_THEME_IDS))
    return df


def train_models(engine: Engine, run_id: str | None = None) -> dict:
    seed = get_settings().generator_seed
    df = load_training_frame(engine)
    features = df[["text"]]
    assert_feature_columns(features.columns)
    train, val = df[df.dataset_split == "TRAIN"], df[df.dataset_split == "VALIDATION"]
    log.info("Training on %d TRAIN / %d VALIDATION annotations", len(train), len(val))
    data_fp = fingerprint(train.annotation_id.tolist(), train.sentiment_label.tolist(),
                          train.theme_labels.tolist(), val.annotation_id.tolist())

    sent = SentimentModel.train(train.text.tolist(), train.sentiment_label.tolist(), val.text.tolist(),
                                val.sentiment_label.tolist(), version_hash=fingerprint(data_fp, "sentiment"),
                                seed=seed)
    sent.metadata.update(training_run_id=run_id, training_data_fingerprint=data_fp)
    themes = ThemeModel.train(train.text.tolist(), train.theme_labels.tolist(), val.text.tolist(),
                              val.theme_labels.tolist(), version_hash=fingerprint(data_fp, "themes"), seed=seed)
    themes.metadata.update(training_run_id=run_id, training_data_fingerprint=data_fp)
    save_model("sentiment", sent)
    save_model("themes", themes)
    log.info("Sentiment %s val macro-F1 %.3f | Themes %s val micro-F1 %.3f", sent.model_version,
             sent.metadata["validation_metrics"]["macro_f1"], themes.model_version,
             themes.metadata["validation_metrics"]["micro_f1"])
    return {
        "train_records": len(train), "validation_records": len(val),
        "sentiment": {"model_version": sent.model_version, "validation": {
            k: sent.metadata["validation_metrics"][k] for k in ("accuracy", "macro_f1")},
            "selected_C": sent.metadata["hyperparameters"]["C"]},
        "themes": {"model_version": themes.model_version, "validation": themes.metadata["validation_metrics"]},
    }
