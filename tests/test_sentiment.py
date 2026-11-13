"""Sentiment model: labels, confidence, uncertainty/review flags, multilingual fallback, leakage guards."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.taxonomy import SENTIMENT_CODES
from app.utils.text_preprocessing import detect_language, lexicon_sentiment
from generator.feedback_generator import derive_sentiment
from ml.common import LeakageError, assert_feature_columns, assert_no_test_records


@pytest.mark.parametrize("polarities,expected", [
    ({"pos"}, "POSITIVE"), ({"neg"}, "NEGATIVE"), ({"pos", "neg"}, "MIXED"),
    ({"neu"}, "NEUTRAL"), (set(), "NEUTRAL"), ({"pos", "neg", "neu"}, "MIXED"),
])
def test_label_derivation_rule(polarities, expected):
    assert derive_sentiment(polarities) == expected


def test_leakage_guards_reject_label_columns():
    assert_feature_columns(["text"])
    for bad in (["text", "sentiment_label"], ["text", "rating"], ["text", "theme_ids"], ["feedback_id"]):
        with pytest.raises(LeakageError):
            assert_feature_columns(bad)
    assert_no_test_records(["TRAIN", "VALIDATION"])
    with pytest.raises(LeakageError):
        assert_no_test_records(["TRAIN", "TEST"])


def test_language_detection():
    assert detect_language("The delivery was quick and the packaging was neat.").language == "en"
    assert detect_language("डिलीवरी बहुत देर से आई").language == "hi"
    assert detect_language("డెలివరీ చాలా ఆలస్యంగా వచ్చింది").language == "te"
    rom = detect_language("delivery bahut late aayi, bilkul bekar service hai")
    assert rom.language == "hi" and rom.code_mixed


def test_lexicon_handles_negation():
    label, _ = lexicon_sentiment("not worth the money, really bad")
    assert label == "NEGATIVE"


def test_classify_sentiment_contract(engine):
    from app.services.sentiment_service import classify_sentiment

    texts = ["Absolutely love this product, battery lasts for days and it works perfectly.",
             "Terrible experience. The app keeps crashing and support never replied.",
             "Great sound quality but the battery dies within two hours."]
    res = classify_sentiment(texts, [5, 1, 3])
    assert [r.label for r in res][:2] == ["POSITIVE", "NEGATIVE"]
    for r in res:
        assert r.label in SENTIMENT_CODES
        assert 0.0 <= r.confidence <= 1.0
        assert abs(sum(r.probabilities.values()) - 1.0) < 1e-6
        assert r.model_version and r.model_name
        assert r.needs_human_review == (r.review_reason is not None)


def test_rating_disagreement_is_flagged(engine):
    from app.services.sentiment_service import classify_sentiment

    r = classify_sentiment(["Absolutely love this product, it works perfectly and arrived early."], [1])[0]
    assert r.label == "POSITIVE" and r.needs_human_review and "RATING_DISAGREES" in r.review_reason


def test_low_confidence_non_english_is_flagged(engine):
    from app.services.sentiment_service import classify_sentiment
    from app.core.config import get_settings

    for r in classify_sentiment(["डिलीवरी बहुत देर से आई, पैसा बर्बाद", "చాలా బాగుంది, ధర కూడా తక్కువ"]):
        assert r.detected_language in ("hi", "te")
        if r.method == "MODEL+LEXICON_FALLBACK" or r.confidence < get_settings().multilingual_review_threshold:
            assert r.needs_human_review and "NON_ENGLISH_LOW_CONFIDENCE" in r.review_reason


def test_predictions_stored_with_full_metadata(engine):
    with engine.connect() as c:
        unscored = c.execute(text("""SELECT count(*) FROM feedback_items fi WHERE processing_status = 'PROCESSED'
                                     AND NOT EXISTS (SELECT 1 FROM v_current_sentiment s
                                                     WHERE s.feedback_id = fi.feedback_id)""")).scalar()
        excluded_scored = c.execute(text("""SELECT count(*) FROM v_feedback_analytics
                                            WHERE processing_status IN ('DUPLICATE','QUARANTINED')""")).scalar()
        row = c.execute(text("""
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE model_version IS NULL OR model_name IS NULL OR predicted_at IS NULL) AS missing,
                   count(*) FILTER (WHERE confidence < 0 OR confidence > 1) AS bad_conf,
                   count(DISTINCT sentiment_label) AS labels
            FROM v_current_sentiment""")).mappings().one()
        analysable = c.execute(text("SELECT count(*) FROM feedback_items WHERE processing_status = 'PROCESSED'")).scalar()
    assert analysable > 0 and row["n"] >= analysable
    assert unscored == 0 and excluded_scored == 0
    assert row["missing"] == 0 and row["bad_conf"] == 0 and row["labels"] == 4


def test_prediction_history_is_append_only(engine):
    """Re-running inference appends a new run's rows; the previous run's rows are preserved."""
    from app.services.pipeline_service import run_pipeline

    with engine.connect() as c:
        before_runs = c.execute(text("SELECT count(DISTINCT run_id) FROM sentiment_predictions")).scalar()
        before_rows = c.execute(text("SELECT count(*) FROM sentiment_predictions")).scalar()
        # items stay PROCESSED; re-score them by temporarily looking at PROCESSED (predict_all includes PROCESSED)
    summary = run_pipeline(engine, steps=["predict"], triggered_by="pytest")
    assert summary["status"] == "SUCCEEDED"
    with engine.connect() as c:
        after_runs = c.execute(text("SELECT count(DISTINCT run_id) FROM sentiment_predictions")).scalar()
        after_rows = c.execute(text("SELECT count(*) FROM sentiment_predictions")).scalar()
        current = c.execute(text("SELECT count(*), count(DISTINCT feedback_id) FROM v_current_sentiment")).one()
    assert after_runs == before_runs + 1
    assert after_rows > before_rows
    assert current[0] == current[1]  # exactly one current prediction per item
