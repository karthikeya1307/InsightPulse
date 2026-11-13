"""Sentiment inference with uncertainty handling and a multilingual fallback.

Decision logic per text
-----------------------
1. The TF-IDF/LogReg model gives class probabilities; confidence = max prob.
2. English: if confidence < SENTIMENT_UNCERTAINTY_THRESHOLD -> human review.
3. Hindi/Telugu (native or romanised): the baseline was trained mostly on
   English, so we never assume it "understands" these languages. If the model
   confidence is below MULTILINGUAL_REVIEW_THRESHOLD, the multilingual lexicon
   is consulted; when it has a clear signal its label is used
   (method MODEL+LEXICON_FALLBACK). Low-confidence non-English predictions are
   always flagged for human review.
4. Star rating strongly disagreeing with the predicted polarity -> review flag.

``predict_all`` scores every analysable feedback item in batches (keyset
pagination) and APPENDS rows to the prediction history tables.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.taxonomy import SENTIMENT_CODES
from app.services.model_registry import load_active
from app.services.theme_service import classify_themes
from app.utils.text_preprocessing import detect_language, lexicon_sentiment

log = get_logger("sentiment")


@dataclass
class SentimentResult:
    label: str
    confidence: float
    probabilities: dict[str, float]
    needs_human_review: bool
    review_reason: str | None
    detected_language: str
    method: str
    model_name: str
    model_version: str
    threshold: float


def classify_sentiment(texts: list[str], ratings: list[int | None] | None = None) -> list[SentimentResult]:
    settings = get_settings()
    model = load_active("sentiment")
    proba = model.predict_proba(texts)
    ratings = ratings or [None] * len(texts)
    out = []
    for i, txt in enumerate(texts):
        probs = {c: float(proba[i, j]) for j, c in enumerate(SENTIMENT_CODES)}
        label = max(probs, key=probs.get)
        conf = probs[label]
        lang = detect_language(txt)
        method, reasons = "MODEL", []
        if lang.language != "en":
            if conf < settings.multilingual_review_threshold:
                lex_label, lex_conf = lexicon_sentiment(txt)
                if lex_label is not None and lex_conf >= 0.6 and lex_label != label:
                    # lexicon overrides only a low-confidence model; confidence is the lexicon's own
                    label, conf, method = lex_label, lex_conf, "MODEL+LEXICON_FALLBACK"
                reasons.append(f"NON_ENGLISH_LOW_CONFIDENCE({lang.language}{'-romanised' if lang.code_mixed else ''})")
        elif conf < settings.sentiment_uncertainty_threshold:
            reasons.append("LOW_CONFIDENCE")
        r = ratings[i]
        if r is not None and ((label == "POSITIVE" and r <= 2) or (label == "NEGATIVE" and r >= 4)):
            reasons.append("RATING_DISAGREES")
        out.append(SentimentResult(label, round(conf, 5), probs, bool(reasons), "; ".join(reasons) or None,
                                   lang.language, method, model.metadata.get("model_name", "sentiment"),
                                   model.model_version, settings.sentiment_uncertainty_threshold))
    return out


PRED_COLUMNS = ["feedback_id", "run_id", "sentiment_label", "confidence", "prob_positive", "prob_negative",
                "prob_neutral", "prob_mixed", "model_name", "model_version", "uncertainty_threshold",
                "needs_human_review", "review_reason", "detected_language", "prediction_method"]
THEME_COLUMNS = ["feedback_id", "theme_id", "run_id", "confidence", "model_version", "assignment_source"]


def score_and_store(conn, items: list[dict], run_id: str) -> tuple[int, int, str, str]:
    """Predict sentiment + themes for ``items`` and append them (psycopg connection)."""
    texts = [it["text"] for it in items]
    sents = classify_sentiment(texts, [it.get("rating") for it in items])
    themes, theme_version = classify_themes(texts)
    n_themes = 0
    with conn.cursor() as cur:
        with cur.copy(f"COPY sentiment_predictions ({', '.join(PRED_COLUMNS)}) FROM STDIN") as cp:
            for it, s in zip(items, sents):
                cp.write_row([it["feedback_id"], run_id, s.label, s.confidence, round(s.probabilities["POSITIVE"], 5),
                              round(s.probabilities["NEGATIVE"], 5), round(s.probabilities["NEUTRAL"], 5),
                              round(s.probabilities["MIXED"], 5), s.model_name, s.model_version, s.threshold,
                              s.needs_human_review, s.review_reason, s.detected_language, s.method])
        with cur.copy(f"COPY feedback_themes ({', '.join(THEME_COLUMNS)}) FROM STDIN") as cp:
            for it, ths in zip(items, themes):
                for theme_id, p in ths:
                    cp.write_row([it["feedback_id"], theme_id, run_id, round(p, 5), theme_version, "MODEL"])
                    n_themes += 1
    return len(items), n_themes, sents[0].model_version if sents else "", theme_version


def predict_all(engine: Engine, run_id: str, batch_size: int | None = None) -> dict:
    """Score all analysable feedback items in keyset-paginated batches; one transaction."""
    batch_size = batch_size or get_settings().prediction_batch_size
    t0 = time.perf_counter()
    total = n_themes = 0
    sv = tv = None
    raw = engine.raw_connection()
    try:
        conn = raw.driver_connection
        last = ""
        while True:
            with conn.cursor() as cur:
                cur.execute("""SELECT feedback_id, feedback_text, rating FROM feedback_items
                               WHERE processing_status IN ('VALIDATED','PROCESSED') AND feedback_id > %s
                               ORDER BY feedback_id LIMIT %s""", (last, batch_size))
                rows = cur.fetchall()
            if not rows:
                break
            items = [{"feedback_id": r[0], "text": r[1], "rating": r[2]} for r in rows]
            n, k, sv, tv = score_and_store(conn, items, run_id)
            total, n_themes, last = total + n, n_themes + k, rows[-1][0]
            log.info("Scored %d items (batch of %d)", total, n)
        with conn.cursor() as cur:
            cur.execute("""
                WITH upd AS (UPDATE feedback_items SET processing_status = 'PROCESSED', updated_at = now()
                             WHERE processing_status = 'VALIDATED' RETURNING feedback_id)
                INSERT INTO state_change_events (entity_type, entity_id, from_state, to_state, changed_by,
                                                 reason, run_id, changed_at)
                SELECT 'FEEDBACK_ITEM', feedback_id, 'VALIDATED', 'PROCESSED', 'inference', 'scored', %s, now()
                FROM upd""", (run_id,))
            cur.execute("""SELECT count(*) FILTER (WHERE needs_human_review),
                                  count(*) FILTER (WHERE prediction_method <> 'MODEL')
                           FROM sentiment_predictions WHERE run_id = %s""", (run_id,))
            review, fallback = cur.fetchone()
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
    return {"scored": total, "theme_assignments": n_themes, "needs_human_review": review,
            "lexicon_fallbacks": fallback, "sentiment_model_version": sv, "theme_model_version": tv,
            "items_per_second": round(total / max(time.perf_counter() - t0, 1e-6), 1)}
