#!/usr/bin/env python3
"""Stage 30b -- does labelling several posts per Gemma call (batching) keep the single-call answers? Test on the posts already labelled
by 30_gemma_event_topic.py. Only writes a parquet of the batch answers.

    python scripts/30b_gemma_event_batch_test.py --pilot PILOT.parquet --sizes 5 10 --out OUT.parquet
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import event_topic_llm as E  # noqa: E402
from lib import gemma_client as G  # noqa: E402


def run_size(df: pd.DataFrame, size: int) -> pd.DataFrame:
    chunks = [df.iloc[i:i + size] for i in range(0, len(df), size)]

    def one(ch):
        n = len(ch)
        r = G.chat_json(E.batch_system_prompt(), E.batch_user_prompt(list(ch.text)), max_tokens=min(4000, 400 + 150 * n),
                        validate=lambda o: E.validate_batch(o, n))
        rows = []
        for j, (uid, text) in enumerate(zip(ch.post_uid, ch.text)):
            a = r["obj"]["answers"][j] if r["ok"] else {}
            ev = a.get("evidence") or ""
            rows.append({"post_uid": uid, f"b{size}_subcode": a.get("subcode"), f"b{size}_conf": a.get("confidence"),
                         f"b{size}_ev_ok": E.evidence_in_text(ev, text) if ev else None, f"b{size}_ok": r["ok"],
                         f"b{size}_call_tokens": r["tokens"], f"b{size}_call_s": r["latency_s"], f"b{size}_n": n})
        return rows

    out = [x for rows in G.run_batch(chunks, one, concurrency=8) for x in rows]
    return pd.DataFrame(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", required=True)
    ap.add_argument("--sizes", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    df = pd.read_parquet(a.pilot)
    res = df[["post_uid"]].copy()
    for s in a.sizes:
        r = run_size(df, s)
        res = res.merge(r, on="post_uid")
        calls = len(range(0, len(df), s))
        print(f"[30b] size {s}: {calls} calls, valid posts {int(r[f'b{s}_ok'].sum())}/{len(df)}", flush=True)
    res.to_parquet(a.out, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
