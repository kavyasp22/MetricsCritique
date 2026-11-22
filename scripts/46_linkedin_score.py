#!/usr/bin/env python3
"""
Audience sentiment, Phase 4 (LinkedIn), step 2: score LinkedIn comments with the same sentiment model
used for YouTube and Instagram (cardiffnlp/twitter-xlm-roberta-base-sentiment).

Reads  data/audience/li_comments_extract.parquet
Writes data/audience/li_comments_scored.parquet  (matches ig_comments_scored.parquet schema)
       data/audience/score_parts_li/              (resumable checkpoints)
       data/audience/score_meta_li.json

Only rows with text_status == 'ok' are scored. Everything else keeps its status and receives no score.
No database access of any kind.
"""
from __future__ import annotations

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
PARTS = OUT / "score_parts_li"
CHUNK = 20000


def score_batch(pipe, texts: list[str]) -> list[dict | None]:
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
    except Exception as e:
        print(f"  batch error ({type(e).__name__}); retrying one by one", flush=True)
    res = []
    for t in texts:
        try:
            res.append(to_row(pipe([t])[0]))
        except Exception:
            res.append(None)
    return res


def main() -> int:
    import torch

    device = 0 if torch.cuda.is_available() else -1
    print(f"device={'cuda' if device == 0 else 'cpu'}  model={SENTIMENT_MODEL}", flush=True)
    PARTS.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(OUT / "li_comments_extract.parquet", columns=["comment_uid", "text_used", "text_status"])
    todo = df[df["text_status"] == "ok"].copy()
    todo["text_used"] = todo["text_used"].str.slice(0, MAX_CHARS)
    done: set[str] = set()
    for p in sorted(PARTS.glob("part-*.parquet")):
        done |= set(pd.read_parquet(p, columns=["comment_uid"])["comment_uid"])
    todo = todo[~todo["comment_uid"].isin(done)].reset_index(drop=True)
    print(f"total ok: {len(df[df['text_status']=='ok'])}  already done: {len(done)}  to score: {len(todo)}", flush=True)
    if todo.empty:
        print("nothing to do", flush=True)
    else:
        pipe = load_sentiment_pipeline(device=device)
        scored_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()
        for i in range(0, len(todo), CHUNK):
            chunk = todo.iloc[i: i + CHUNK]
            texts = chunk["text_used"].tolist()
            results = score_batch(pipe, texts)
            part_rows = []
            for uid, r in zip(chunk["comment_uid"], results):
                if r is None:
                    part_rows.append({"comment_uid": uid, "score_status": "failed",
                                      "label": None, "p_negative": None, "p_neutral": None, "p_positive": None,
                                      "tone_score": None, "model": SENTIMENT_MODEL, "scored_at": scored_at})
                else:
                    part_rows.append({"comment_uid": uid, "score_status": "scored",
                                      "label": r["label"], "p_negative": r["p_negative"],
                                      "p_neutral": r["p_neutral"], "p_positive": r["p_positive"],
                                      "tone_score": r["tone_score"], "model": SENTIMENT_MODEL, "scored_at": scored_at})
            part_df = pd.DataFrame(part_rows)
            pname = PARTS / f"part-{i:08d}.parquet"
            part_df.to_parquet(pname, index=False)
            elapsed = time.time() - t0
            print(f"  chunk {i}–{i + len(chunk) - 1}: {len(part_rows)} rows  ({elapsed:.1f}s)", flush=True)
        print(f"scoring done in {time.time() - t0:.1f}s", flush=True)

    # Merge parts
    parts = sorted(PARTS.glob("part-*.parquet"))
    scored = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True) if parts else pd.DataFrame()

    # Join back all rows (scored + not scored)
    full = df.copy()
    if len(scored):
        full = full.merge(scored[["comment_uid", "score_status", "label", "p_negative", "p_neutral",
                                  "p_positive", "tone_score", "model", "scored_at"]], on="comment_uid", how="left")
    else:
        for c in ["score_status", "label", "p_negative", "p_neutral", "p_positive", "tone_score", "model", "scored_at"]:
            full[c] = None

    # Fill not_scored for rows that weren't ok (and therefore weren't sent to the model)
    full["score_status"] = full["score_status"].where(
        full["score_status"].notna(),
        np.where(full["text_status"] == "ok", "failed", "not_scored")
    )

    out_path = OUT / "li_comments_scored.parquet"
    full.to_parquet(out_path, index=False)

    n_scored = int((full["score_status"] == "scored").sum())
    n_failed = int((full["score_status"] == "failed").sum())
    n_not = int((full["score_status"] == "not_scored").sum())
    meta = {
        "model": SENTIMENT_MODEL,
        "device": "cuda" if device == 0 else "cpu",
        "platform": "linkedin",
        "total_rows": int(len(full)),
        "scored": n_scored,
        "failed": n_failed,
        "not_scored": n_not,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "score_meta_li.json").write_text(json.dumps(meta, indent=2))
    print(f"\nscored={n_scored}  failed={n_failed}  not_scored={n_not}", flush=True)
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
