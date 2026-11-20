#!/usr/bin/env python3
"""Stage 29 -- the A01-A10 event topic (41 sub-codes) of every post that already has a D01-D09 theme (stages 27 and 28).

Constrained zero-shot: a post is compared (cosine, pretrained bge-m3 vectors, no training, no LLM) only with the sub-codes placed under ITS
security theme (CODE_TO_DOMAIN in scripts/lib/taxonomy.py), so the choice is among 1-12 sub-codes, not 41. Needs data/event_topic_emb_v1.npz
(scripts/29a). Posts without a theme get no event topic; neither do posts of a theme with a single sub-code (D01, D09: forced, 0 of 20 right).

Writes data/post_event_topic_v1.parquet: post_uid, theme, theme_tier ('high'|'medium'|'neighbour'), subcode ('05.1'), parent ('05'),
score (cosine to the chosen sub-code), margin (to the runner-up; NULL when the theme has a single sub-code), n_cand.
Raw scores are stored; the cut-offs that make a label usable are measured separately (see collaboration/04_CURRENT_STATE.md).
Usage: python scripts/29_post_event_topic.py
"""
from __future__ import annotations
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "post_event_topic_v1.parquet"
BATCH = 20000


def main() -> int:
    z = np.load(ROOT / "data" / "event_topic_emb_v1.npz", allow_pickle=True)
    X, code, dom = z["X"], z["code"], z["domain"]
    sub = np.array(["." in c for c in code])
    th = pd.read_parquet(ROOT / "data" / "post_theme_v1.parquet", columns=["post_uid", "theme", "tier"])
    th = th[th.theme != "unassigned"].rename(columns={"tier": "theme_tier"})
    kn = pd.read_parquet(ROOT / "data" / "post_theme_knn_v1.parquet", columns=["post_uid", "knn_theme"])
    kn = kn[kn.knn_theme != "unassigned"].rename(columns={"knn_theme": "theme"}).assign(theme_tier="neighbour")
    t = pd.concat([th, kn], ignore_index=True).drop_duplicates("post_uid")
    tmap = dict(zip(t.post_uid, t.theme))
    print(f"posts with a theme: {len(t):,}", flush=True)
    cand = {d: np.flatnonzero(sub & (dom == d)) for d in sorted(set(dom))}
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    rdr = con.execute("SELECT post_uid, embedding FROM post_embeddings").to_arrow_reader(BATCH)
    rows = []
    for rb in rdr:
        uid = rb.column(0).to_pylist()
        keep = [i for i, u in enumerate(uid) if u in tmap]
        if not keep:
            continue
        E = rb.column(1).flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(uid), -1)[keep]
        E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
        ids = [uid[i] for i in keep]
        d_of = np.array([tmap[u] for u in ids])
        for d in np.unique(d_of):
            idx = np.flatnonzero(d_of == d)
            c = cand.get(d, np.array([], dtype=int))
            if len(c) < 2:        # D01 (only 08.2) and D09 (only 08.3): one candidate would be assigned to every post; 0 of 20 were right, so no event topic
                continue
            S = E[idx] @ X[c].T
            o = np.argsort(-S, axis=1)
            best = S[np.arange(len(idx)), o[:, 0]]
            sec = S[np.arange(len(idx)), o[:, 1]] if len(c) > 1 else np.full(len(idx), np.nan, np.float32)
            for j, i in enumerate(idx):
                rows.append((ids[i], d, code[c[o[j, 0]]], float(best[j]), float(best[j] - sec[j]) if len(c) > 1 else np.nan, len(c)))
    con.close()
    o = pd.DataFrame(rows, columns=["post_uid", "theme", "subcode", "score", "margin", "n_cand"])
    o["parent"] = o.subcode.str.split(".").str[0]
    o = o.merge(t[["post_uid", "theme_tier"]], on="post_uid")
    o[["score", "margin"]] = o[["score", "margin"]].astype("float32")
    assert o.post_uid.is_unique
    o.to_parquet(OUT, index=False)
    print(f"wrote {OUT}: {len(o):,} posts")
    print(o.groupby("theme").agg(n=("post_uid", "size"), score=("score", "median"), margin=("margin", "median")).round(3).to_string())
    print(o.subcode.value_counts().to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
