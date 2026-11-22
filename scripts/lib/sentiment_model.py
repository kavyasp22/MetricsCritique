"""The post-sentiment model setup, importable WITHOUT duckdb / dotenv / Postgres settings.

`scripts/04_nlp_scores.py` imports duckdb and `lib.config` at module level, so it cannot be imported in the
torch-only environment that scores text. These definitions are verbatim copies of the ones in 04 (the same model,
the same truncation, the same pos - neg tone formula) so comment and reply sentiment stay on exactly the same
scale as post sentiment. `scripts/tests/test_audience_sentiment.py` compares them with 04's source and fails if
the two ever drift apart. 04 itself is not modified.
"""
from __future__ import annotations

SENTIMENT_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
BATCH = 64
MAX_CHARS = 2000      # 04 scores texts[i][:2000]


def load_sentiment_pipeline(device: int | str):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

    tok = AutoTokenizer.from_pretrained(SENTIMENT_MODEL, use_fast=False)
    model = AutoModelForSequenceClassification.from_pretrained(SENTIMENT_MODEL)
    return pipeline(
        "sentiment-analysis",
        model=model,
        tokenizer=tok,
        device=device,
        truncation=True,
        max_length=256,
        top_k=None,
    )


def sentiment_to_continuous(results) -> float:
    """Map multi-label sentiment outputs to [-1, 1]."""
    # results: list of {label, score}
    if not results:
        return 0.0
    # normalize labels
    scores = {}
    for r in results:
        lab = str(r["label"]).lower()
        scores[lab] = float(r["score"])
    # xlm-roberta-base-sentiment: positive/neutral/negative
    pos = scores.get("positive", scores.get("label_2", 0.0))
    neg = scores.get("negative", scores.get("label_0", 0.0))
    neu = scores.get("neutral", scores.get("label_1", 0.0))
    # continuous: pos - neg
    if pos or neg:
        return float(pos - neg)
    # single best label fallback
    best = max(results, key=lambda x: x["score"])
    lab = str(best["label"]).lower()
    if "pos" in lab:
        return float(best["score"])
    if "neg" in lab:
        return float(-best["score"])
    return 0.0


def label_probs(results) -> dict | None:
    """All three class probabilities and the argmax label from one pipeline output (top_k=None).

    Uses the same label normalisation as `sentiment_to_continuous` (positive/negative/neutral or LABEL_2/0/1).
    Returns None when the output is unusable, so the caller can mark the row as a failed prediction.
    """
    if not results or not isinstance(results, list) or not isinstance(results[0], dict):
        return None
    s = {str(r["label"]).lower(): float(r["score"]) for r in results}
    pos = s.get("positive", s.get("label_2"))
    neg = s.get("negative", s.get("label_0"))
    neu = s.get("neutral", s.get("label_1"))
    if pos is None or neg is None or neu is None:
        return None
    label = max((("negative", neg), ("neutral", neu), ("positive", pos)), key=lambda kv: kv[1])[0]
    return {"p_negative": neg, "p_neutral": neu, "p_positive": pos, "label": label}
