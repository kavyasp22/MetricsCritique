#!/usr/bin/env python3
"""
Audience sentiment, targeted post enrichment, step 2: post tone for the target posts, with the SAME model and settings as stage 04
(`lib/sentiment_model.py` is a verbatim, test-guarded copy of 04's loader and tone formula): cardiffnlp XLM-R, 256 tokens, text capped
at 2000 characters, tone = P(positive) - P(negative), length-sorted batches of 64, empty text -> no tone (tone_valid False).

Also scores the reproducibility sample (in-store posts re-read from production) so the targeted run can be checked against the stored
stage-04 results. Runs in a torch environment (the project .venv has none); no database access of any kind.

    PYTHONPATH=<dir with sentencepiece 0.2.0> /usr/bin/python3 scripts/39_audience_enrich_tone.py
Reads data/audience/enrich_posts.parquet, writes data/audience/enrich_tone.parquet.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.sentiment_model import BATCH, MAX_CHARS, SENTIMENT_MODEL, label_probs, load_sentiment_pipeline, sentiment_to_continuous

OUT = HERE.parent / "data" / "audience"


def main() -> int:
    import torch

    device = 0 if torch.cuda.is_available() else -1
    df = pd.read_parquet(OUT / "enrich_posts.parquet", columns=["post_uid", "purpose", "text"])
    texts = df["text"].fillna("").astype(str).tolist()
    idx = sorted((i for i, t in enumerate(texts) if t and t.strip()), key=lambda i: len(texts[i][:MAX_CHARS]))      # as 04: non-empty only
    print(f"device={'cuda' if device == 0 else 'cpu'} rows={len(df)} non-empty={len(idx)}", flush=True)
    pipe = load_sentiment_pipeline(device)
    res = {}
    t0 = time.time()
    for s in range(0, len(idx), BATCH):
        ix = idx[s:s + BATCH]
        batch = [texts[i][:MAX_CHARS] for i in ix]
        try:
            outs = pipe(batch)
        except Exception as e:                                           # noqa: BLE001
            print(f"  batch error {type(e).__name__}; one by one", flush=True)
            outs = []
            for t in batch:
                try:
                    outs.append(pipe([t])[0])
                except Exception:                                        # noqa: BLE001
                    outs.append(None)
        for i, o in zip(ix, outs):
            lp = label_probs(o) if o is not None else None
            if lp is not None:
                lp["tone_score"] = sentiment_to_continuous(o)
            res[i] = lp
        if (s // BATCH) % 10 == 0:
            print(f"  {s + len(ix)}/{len(idx)}  {time.time() - t0:.0f}s", flush=True)
    rows = []
    now = datetime.now(timezone.utc)
    for i, (uid, purpose) in enumerate(zip(df["post_uid"], df["purpose"])):
        lp = res.get(i)
        rows.append({"post_uid": uid, "purpose": purpose, "tone_valid": lp is not None,
                     "tone_score": lp["tone_score"] if lp else None, "label": lp["label"] if lp else None,
                     "p_negative": lp["p_negative"] if lp else None, "p_neutral": lp["p_neutral"] if lp else None,
                     "p_positive": lp["p_positive"] if lp else None, "tone_model": SENTIMENT_MODEL if lp else None, "scored_at": now,
                     "reason": None if lp else ("empty_text" if not texts[i].strip() else "failed")})
    out = pd.DataFrame(rows)
    out.to_parquet(OUT / "enrich_tone.parquet", index=False)
    print(f"done in {time.time() - t0:.0f}s: valid {int(out['tone_valid'].sum())}, not valid {int((~out['tone_valid']).sum())} {out[~out['tone_valid']]['reason'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
