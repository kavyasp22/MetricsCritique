#!/usr/bin/env python3
"""Stage 31 -- a compact, memory-mappable copy of the stored bge-m3 post embeddings for query-time semantic retrieval.

Reads data/analytics.duckdb (READ-ONLY: post_embeddings, post_facts) and data/post_dedup.parquet; writes
    data/post_emb_fp16.npy        (n_posts, 1024) float16, L2-normalised, row i <-> index row i
    data/post_emb_index.parquet   post_uid, platform, day (posted day, else scraped day), is_first (first post of an exact-copy group)
The API server memory-maps the .npy (about 1 GB) and ranks posts against a query vector in a few hundred milliseconds.
Derived data: ignored by git. Re-run after the embeddings are refreshed.   Usage: python scripts/31_post_embedding_index.py
"""
from __future__ import annotations
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BATCH = 20000


def main() -> int:
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    n = con.execute("SELECT count(*) FROM post_embeddings").fetchall()[0][0]
    out = np.lib.format.open_memmap(ROOT / "data" / "post_emb_fp16.npy.tmp.npy", mode="w+", dtype=np.float16, shape=(n, 1024))
    rdr = con.sql("SELECT e.post_uid, p.platform, CAST(coalesce(p.posted_at, p.scraped_at) AS DATE) AS day, e.embedding "
                  "FROM post_embeddings e JOIN post_facts p ON p.post_uid = e.post_uid").to_arrow_reader(BATCH)
    idx, i = [], 0
    for rb in rdr:
        X = rb.column(3).flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(rb.num_rows, -1)
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
        out[i:i + rb.num_rows] = X.astype(np.float16)
        idx.append(pd.DataFrame({"post_uid": rb.column(0).to_pylist(), "platform": rb.column(1).to_pylist(), "day": [str(d) for d in rb.column(2).to_pylist()]}))
        i += rb.num_rows
        print(f"  {i:,}/{n:,}", flush=True)
    out.flush(); del out
    ix = pd.concat(idx, ignore_index=True)
    dd = pd.read_parquet(ROOT / "data" / "post_dedup.parquet", columns=["post_uid", "is_first"])
    ix = ix.merge(dd, on="post_uid", how="left"); ix["is_first"] = ix["is_first"].fillna(True)
    assert len(ix) == i and ix.post_uid.is_unique
    ix.to_parquet(ROOT / "data" / "post_emb_index.parquet", index=False)
    (ROOT / "data" / "post_emb_fp16.npy.tmp.npy").replace(ROOT / "data" / "post_emb_fp16.npy")
    print(f"wrote data/post_emb_fp16.npy and data/post_emb_index.parquet: {i:,} posts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
