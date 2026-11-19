#!/usr/bin/env python3
"""
Stage 26 -- zero-shot domain scoring of every embedded post with the pretrained bge-m3 (no training).

Reads  data/analytics.duckdb (READ-ONLY): post_embeddings, post_facts; data/post_dedup.parquet (stage 25, optional);
       data/domain_prototype_emb_v1.npz (stage 26a).
Writes data/post_domain_zs.parquet (new file, nothing existing changes):
    post_uid, platform, zs_domain (D01..D09 or none; provisional, about 60% right among security posts),
    zs_best, zs_best_score, zs_second, zs_margin, none_score, none_gap, confident_none, is_first

`confident_none` = a "not security" description beats every domain by at least NONE_GAP (lib/domain_zeroshot.py). On 273
hand-reviewed posts those were 97% truly non-security (2 of 149 security posts dropped). They can skip the LLM; everything
else still needs the LLM (Gemma prompt v2) because the zero-shot domain choice is not reliable enough on its own.

    python scripts/26_domain_zeroshot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import domain_zeroshot as Z  # noqa: E402

SRC = ROOT / "data" / "analytics.duckdb"
PROTO = ROOT / "data" / "domain_prototype_emb_v1.npz"
DEDUP = ROOT / "data" / "post_dedup.parquet"
OUT = ROOT / "data" / "post_domain_zs.parquet"
BATCH = 40000


def main() -> int:
    if not PROTO.exists():
        print(f"ERROR: run scripts/26a_embed_domain_prototypes.py first ({PROTO.name} missing)")
        return 1
    pz = np.load(PROTO, allow_pickle=True)
    PX, pdom = pz["X"], pz["dom"]
    con = duckdb.connect(str(SRC), read_only=True)
    n_emb = con.execute("SELECT count(*) FROM post_embeddings").fetchall()[0][0]
    print(f"embedded posts: {n_emb:,}", flush=True)
    rdr = con.execute("SELECT e.post_uid, p.platform, e.embedding FROM post_embeddings e "
                      "JOIN post_facts p ON p.post_uid = e.post_uid").to_arrow_reader(BATCH)
    parts = []
    done = 0
    for rb in rdr:
        uid = rb.column(0).to_pylist()
        plat = rb.column(1).to_pylist()
        col = rb.column(2)
        X = col.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(uid), -1)
        s = Z.score(X, PX, pdom)
        parts.append(pd.DataFrame({"post_uid": uid, "platform": plat, **s}))
        done += len(uid)
        print(f"  scored {done:,}", flush=True)
    out = pd.concat(parts, ignore_index=True)
    if DEDUP.exists():
        d = pd.read_parquet(DEDUP, columns=["post_uid", "is_first"])
        out = out.merge(d, on="post_uid", how="left")
        out["is_first"] = out["is_first"].fillna(True)
    else:
        out["is_first"] = True
    assert out["post_uid"].is_unique
    out["zs_best_score"] = out["zs_best_score"].astype("float32")
    for c in ("zs_margin", "none_score", "none_gap"):
        out[c] = out[c].astype("float32")
    out.to_parquet(OUT, index=False)
    n = len(out)
    print(f"\nwrote {OUT}: {n:,} posts")
    print("provisional domain (zero-shot):", out["zs_domain"].value_counts().to_dict())
    cn = out["confident_none"]
    dist = out[out["is_first"]]
    print(f"confident none: {cn.mean():.1%} of all posts | {dist['confident_none'].mean():.1%} of the {len(dist):,} distinct messages "
          f"-> the LLM queue shrinks from {len(dist):,} to {int((~dist['confident_none']).sum()):,} distinct messages")
    t = out.groupby("platform").agg(posts=("post_uid", "size"), confident_none=("confident_none", "mean"))
    print(t.sort_values("posts", ascending=False).round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
