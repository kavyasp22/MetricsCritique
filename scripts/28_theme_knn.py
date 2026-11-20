#!/usr/bin/env python3
"""Stage 28 -- spread the hybrid theme (stage 27) to posts that have none, by nearest-neighbour voting on the pretrained bge-m3 embeddings.

No clustering, no training, no LLM. Reference posts (distinct messages only):
    * theme references : stage-27 HIGH tier (74% right on a fresh read)   -> label D01..D09
    * "none" references: clearly not security (stage 26 skip_llm)           -> label none
Candidates: distinct messages with no stage-27 theme, not clearly none, at least 40 characters.
Each candidate takes its K nearest references (cosine); the winning class and its share of the K votes (similarity-weighted) are kept.
A theme is given only if the winner is a theme (never "none"), the share >= MIN_SHARE and the mean similarity of the winning neighbours >= MIN_SIM.
Exact copies inherit the result of their first post (dup_group).

Reads  data/analytics.duckdb (READ-ONLY), data/post_theme_v1.parquet, data/post_domain_zs.parquet, data/post_dedup.parquet
Writes data/post_theme_knn_v1.parquet: post_uid, knn_vote, knn_share, knn_sim, knn_top1, knn_theme ('D01'..'D09' or 'unassigned')
Usage: python scripts/28_theme_knn.py [--k 15] [--min-share 0.9] [--min-sim 0.0] [--torch-python /usr/bin/python3]
The neighbour search runs on the GPU through scripts/lib/knn_gpu.py (the 4 GB left next to vLLM is enough); CUDA_VISIBLE_DEVICES= forces CPU.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "post_theme_knn_v1.parquet"
BATCH = 20000
MIN_CHARS = 40


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--min-share", type=float, default=0.9)
    ap.add_argument("--min-sim", type=float, default=0.0)
    ap.add_argument("--torch-python", default="/usr/bin/python3", help="python with torch (the project .venv has none)")
    a = ap.parse_args()
    th = pd.read_parquet(ROOT / "data" / "post_theme_v1.parquet", columns=["post_uid", "theme", "tier"])
    zs = pd.read_parquet(ROOT / "data" / "post_domain_zs.parquet", columns=["post_uid", "skip_llm", "chars", "is_first"])
    dd = pd.read_parquet(ROOT / "data" / "post_dedup.parquet", columns=["post_uid", "dup_group", "is_first"])
    m = zs.merge(th, on="post_uid").merge(dd[["post_uid", "dup_group"]], on="post_uid")
    m = m.drop_duplicates("post_uid").reset_index(drop=True)
    lab = np.where((m.theme != "unassigned") & (m.tier == "high"), m.theme, np.where(m.skip_llm, "none", ""))
    ref_mask = m.is_first.to_numpy() & (lab != "")
    cand_mask = m.is_first.to_numpy() & (m.theme == "unassigned").to_numpy() & ~m.skip_llm.to_numpy() & (m.chars >= MIN_CHARS).to_numpy()
    print(f"references {ref_mask.sum():,} (themes {int((ref_mask & (lab != 'none')).sum()):,}, none {int((ref_mask & (lab == 'none')).sum()):,}); candidates {cand_mask.sum():,}", flush=True)
    idx = {u: i for i, u in enumerate(m.post_uid)}
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    need = ref_mask | cand_mask
    R, Rl, Q, Qi = [], [], [], []
    rdr = con.execute("SELECT post_uid, embedding FROM post_embeddings").to_arrow_reader(BATCH)
    for rb in rdr:
        uid = rb.column(0).to_pylist()
        X = rb.column(1).flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(uid), -1)
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
        for j, u in enumerate(uid):
            i = idx.get(u)
            if i is None or not need[i]:
                continue
            if ref_mask[i]:
                R.append(X[j]); Rl.append(lab[i])
            if cand_mask[i]:
                Q.append(X[j]); Qi.append(i)
    con.close()
    R = np.vstack(R); Q = np.vstack(Q); Rl = np.array(Rl); Qi = np.array(Qi)
    classes = sorted(set(Rl)); cid = {c: n for n, c in enumerate(classes)}; Rc = np.array([cid[x] for x in Rl])
    print(f"matrix R {R.shape} Q {Q.shape}; classes {classes}", flush=True)
    k = a.k
    import subprocess, tempfile
    with tempfile.TemporaryDirectory(dir=str(ROOT / "data")) as td:
        np.save(f"{td}/R.npy", R); np.save(f"{td}/Q.npy", Q)
        print("neighbour search on the GPU (system python with torch) ...", flush=True)
        subprocess.run([a.torch_python, str(ROOT / "scripts" / "lib" / "knn_gpu.py"), f"{td}/R.npy", f"{td}/Q.npy", f"{td}/nn.npz", str(k)], check=True)
        z = np.load(f"{td}/nn.npz"); NB, NS = z["idx"], z["sim"]
    nc = Rc[NB]
    w = np.zeros((len(NB), len(classes)), np.float32)
    for c in range(len(classes)):
        w[:, c] = (NS * (nc == c)).sum(1)
    win = w.argmax(1)
    vote = np.array(classes, dtype=object)[win]
    share = w[np.arange(len(NB)), win] / np.maximum(w.sum(1), 1e-9)
    wm = (nc == win[:, None])
    sim = (NS * wm).sum(1) / np.maximum(wm.sum(1), 1)
    top1 = NS.max(1)
    c = pd.DataFrame({"post_uid": m.post_uid.to_numpy()[Qi], "dup_group": m.dup_group.to_numpy()[Qi], "knn_vote": vote, "knn_share": share, "knn_sim": sim, "knn_top1": top1})
    c["knn_theme"] = np.where((c.knn_vote != "none") & (c.knn_share >= a.min_share) & (c.knn_sim >= a.min_sim), c.knn_vote, "unassigned")
    # exact copies inherit the first post's result
    allp = dd.merge(c.drop(columns=["post_uid"]).drop_duplicates("dup_group"), on="dup_group", how="inner")
    out = allp[["post_uid", "knn_vote", "knn_share", "knn_sim", "knn_top1", "knn_theme"]]
    out.to_parquet(OUT, index=False)
    print(f"wrote {OUT}: {len(out):,} posts ({len(c):,} distinct candidates)")
    print("vote:", c.knn_vote.value_counts().to_dict())
    print("knn_theme:", c.knn_theme.value_counts().to_dict())
    print(c[["knn_share", "knn_sim", "knn_top1"]].describe().round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
