#!/usr/bin/env python3
"""
Audience sentiment, Phase 1 (YouTube), step 2: score comments and replies with the SAME model and settings the
post pipeline uses (cardiffnlp/twitter-xlm-roberta-base-sentiment, 256 tokens, texts capped at 2000 chars,
tone = P(positive) - P(negative)). Comments and replies are scored in one pass but kept as separate layers by the
`layer` column of the extract; nothing is mixed into post sentiment.

Runs in a torch-capable environment (the project .venv has no torch):
    CUDA_VISIBLE_DEVICES= PYTHONPATH=... /usr/bin/python3 scripts/33_audience_score.py   # CPU
    /usr/bin/python3 scripts/33_audience_score.py                                         # GPU if free

Reads data/audience/yt_comments_extract.parquet, writes checkpointed parts under data/audience/score_parts/ and the
merged data/audience/yt_comments_scored.parquet. Resumable: rerun to continue after an interruption. Only rows with
text_status == 'ok' are sent to the model; every other row keeps its status and gets no score.
No database access of any kind (no Postgres, no DuckDB).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.sentiment_model import BATCH, MAX_CHARS, SENTIMENT_MODEL, label_probs, load_sentiment_pipeline, sentiment_to_continuous

OUT = HERE.parent / "data" / "audience"
CHUNK = 20000
# --platform youtube (default) keeps the Phase 1 file names exactly; instagram uses its own files, nothing is shared.
FILES = {"youtube": ("yt_comments_extract.parquet", "score_parts", "yt_comments_scored.parquet", "score_meta.json"),
         "instagram": ("ig_comments_extract.parquet", "score_parts_ig", "ig_comments_scored.parquet", "score_meta_ig.json")}


def score_batch(pipe, texts: list[str]) -> list[dict | None]:
    """One pipeline call; any failure falls back to one-by-one so a single bad text cannot fail the batch."""
    def to_row(out):
        lp = label_probs(out)
        if lp is None:
            return None
        lp["tone_score"] = sentiment_to_continuous(out)
        return lp
    try:
        outs = pipe(texts)
        res = [to_row(o) for o in outs]
        if len(res) == len(texts):
            return res
    except Exception as e:                                   # noqa: BLE001  (OOM, tokenizer errors, ...)
        print(f"  batch error ({type(e).__name__}); retrying one by one", flush=True)
    res = []
    for t in texts:
        try:
            res.append(to_row(pipe([t])[0]))
        except Exception:                                    # noqa: BLE001
            res.append(None)
    return res


def main() -> int:
    import torch

    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=sorted(FILES), default="youtube")
    args = ap.parse_args()
    extract_name, parts_name, scored_name, meta_name = FILES[args.platform]
    PARTS = OUT / parts_name

    device = 0 if torch.cuda.is_available() else -1
    print(f"device={'cuda' if device == 0 else 'cpu'}  model={SENTIMENT_MODEL}", flush=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(OUT / extract_name, columns=["comment_uid", "text_used", "text_status"])
    todo = df[df["text_status"] == "ok"].copy()
    todo["text_used"] = todo["text_used"].str.slice(0, MAX_CHARS)
    done: set[str] = set()
    for p in sorted(PARTS.glob("part-*.parquet")):
        done |= set(pd.read_parquet(p, columns=["comment_uid"])["comment_uid"])
    todo = todo[~todo["comment_uid"].isin(done)]
    # Length-sorted batching, as in 04 (same scores, less padding).
    todo = todo.assign(_n=todo["text_used"].str.len()).sort_values("_n", kind="stable")
    print(f"to score {len(todo):,} (already done {len(done):,})", flush=True)
    if len(todo):
        pipe = load_sentiment_pipeline(device)
        print("model labels:", pipe.model.config.id2label, flush=True)
    part_idx = len(list(PARTS.glob("part-*.parquet")))
    t0 = time.time()
    for c0 in range(0, len(todo), CHUNK):
        chunk = todo.iloc[c0:c0 + CHUNK]
        uids, texts = chunk["comment_uid"].tolist(), chunk["text_used"].tolist()
        rows = []
        for b in range(0, len(texts), BATCH):
            res = score_batch(pipe, texts[b:b + BATCH])
            for uid, r in zip(uids[b:b + BATCH], res):
                if r is None:
                    rows.append({"comment_uid": uid, "score_status": "failed", "label": None, "p_negative": None,
                                 "p_neutral": None, "p_positive": None, "tone_score": None})
                else:
                    rows.append({"comment_uid": uid, "score_status": "scored", **r})
        part = pd.DataFrame(rows)
        part["model"] = SENTIMENT_MODEL
        part["scored_at"] = datetime.now(timezone.utc)
        part.to_parquet(PARTS / f"part-{part_idx:05d}.parquet", index=False)
        part_idx += 1
        el = time.time() - t0
        print(f"  {c0 + len(chunk):,}/{len(todo):,}  {el/60:.1f} min  ({(c0 + len(chunk))/max(el,1):.0f}/s)", flush=True)

    scored = pd.concat([pd.read_parquet(p) for p in sorted(PARTS.glob("part-*.parquet"))], ignore_index=True)
    scored = scored.drop_duplicates("comment_uid", keep="last")
    scored.to_parquet(OUT / scored_name, index=False)

    # Determinism check: rescore 200 random rows and compare with the stored scores.
    ok = scored[scored["score_status"] == "scored"]
    chk = {}
    if len(ok) >= 200:
        s = ok.sample(200, random_state=1)
        txt = df.set_index("comment_uid").loc[s["comment_uid"], "text_used"].str.slice(0, MAX_CHARS).tolist()
        pipe = locals().get("pipe") or load_sentiment_pipeline(device)
        again = score_batch(pipe, txt)
        d = [abs(a["tone_score"] - t) for a, t in zip(again, s["tone_score"]) if a]
        chk = {"n": len(d), "max_abs_tone_diff": float(max(d)), "labels_equal": int(sum(a["label"] == l for a, l in zip(again, s["label"]) if a))}
        print("determinism:", chk, flush=True)
    meta = {"platform": args.platform, "model": SENTIMENT_MODEL, "device": "cuda" if device == 0 else "cpu", "rows_scored": int((scored["score_status"] == "scored").sum()),
            "rows_failed": int((scored["score_status"] == "failed").sum()), "determinism_check": chk,
            "finished_at": datetime.now(timezone.utc).isoformat()}
    (OUT / meta_name).write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
