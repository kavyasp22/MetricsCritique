#!/usr/bin/env python3
"""Stage 41 -- label a sample of posts with security relevance + D-theme + T-topic by nearest-neighbour vote on the analyst gold set.

No training, no LLM, no clustering. Reference = the 198 analyst-reviewed rows (Analyst columns ONLY; the AI and "Final" columns are ignored):
    * security vote : every reference row with a Yes / No label (Unsure rows are not references)
    * D-theme / T-topic vote : reference rows labelled Yes only
Each candidate takes its K nearest references by cosine on the stored bge-m3 embeddings (the same embeddings for references and candidates).
Votes are similarity-weighted. A D-theme / T-topic is only given when the post is predicted relevant.

The gold set is tiny (about 4 posts per T-topic), so the honest accuracy is measured by leave-one-out on the 198 rows and written next to the labels.
Nothing here writes to production Postgres, and posts are read only from the local foundation store (READ-ONLY).

Reads   data/analytics.duckdb (read-only), data/post_dedup.parquet, data/gold/gold200_key_v1.parquet, the analyst workbook (--xlsx)
Writes  data/gold/analyst_gold_v1.parquet                (versioned reference: rid, post_uid, sec, D, T, conf, text)
        data/gold/topic_knn_v1.duckdb  tables knn_label_v1, knn_loo_v1, knn_meta   (new versioned file; nothing overwritten except this file)
Usage   PYTHONPATH=<dir with openpyxl> python scripts/41_gold_knn_label.py --xlsx <workbook> [--n 5000] [--k 7] [--seed 41]
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold"
MIN_CHARS = 40
VERSION = "v1"


def code(s, pat):
    m = re.match(pat, str(s).strip().upper().replace("DO", "D0").replace(".TO", ".T0")) if pd.notna(s) else None
    return m.group(1) if m else None


def load_gold(xlsx: Path) -> pd.DataFrame:
    rv = pd.read_excel(xlsx, sheet_name="Review")
    rv.columns = ["rid", "plat", "lang", "text", "ai_sec", "ai_d", "ai_t", "fin_sec", "fin_d", "fin_t", "notes", "check", "agree", "a_sec", "a_d", "a_t", "a_rat", "a_conf"]
    key = pd.read_parquet(GOLD / "gold200_key_v1.parquet")[["rid", "post_uid"]]
    g = rv.merge(key, on="rid", how="left")
    g["sec"] = g.a_sec.astype(str).str.strip().str.title()
    g["D"] = g.a_d.map(lambda s: code(s, r"(D\d\d)"))
    g["T"] = g.a_t.map(lambda s: code(s, r"(D\d\d\.T\d\d)"))
    g = g.rename(columns={"a_conf": "conf"})[["rid", "post_uid", "plat", "lang", "sec", "D", "T", "conf", "text"]]
    assert g.post_uid.notna().all(), "every analyst row must map to a post_uid"
    yes = g[g.sec == "Yes"]
    assert yes.D.notna().all() and yes["T"].notna().all(), "every relevant analyst row must have a D-theme and a T-topic"
    assert (yes.D == yes["T"].str[:3]).all(), "D-theme and T-topic prefix must agree"
    return g


def vote(sim, labels, k):
    """similarity-weighted vote of the k nearest references; returns (winner, share, mean sim of the winning neighbours)"""
    idx = np.argsort(-sim)[:k]
    w = {}
    for i in idx:
        w[labels[i]] = w.get(labels[i], 0.0) + max(float(sim[i]), 0.0)
    tot = sum(w.values()) or 1.0
    win = max(w, key=w.get)
    ms = float(np.mean([sim[i] for i in idx if labels[i] == win]))
    return win, w[win] / tot, ms


def label_all(E, ref_E, ref_sec, ref_D, ref_T, ref_yes_mask, k, skip=None):
    """label every row of E; skip[i] = index of a reference to leave out for row i (leave-one-out)"""
    S = E @ ref_E.T
    rows = []
    ky = np.where(ref_yes_mask)[0]
    for i in range(len(E)):
        s = S[i].copy()
        if skip is not None and skip[i] is not None:
            s[skip[i]] = -9
        sec, sh, ms = vote(s, ref_sec, k)
        d = t = None; dsh = tsh = dms = tms = np.nan
        if sec == "Yes":
            sy = s[ky]
            d, dsh, dms = vote(sy, ref_D[ky], k)
            t, tsh, tms = vote(sy, ref_T[ky], k)
        rows.append((sec, sh, ms, d, dsh, dms, t, tsh, tms, float(np.sort(s)[::-1][:k].mean())))
    return pd.DataFrame(rows, columns=["knn_sec", "sec_share", "sec_sim", "knn_D", "D_share", "D_sim", "knn_T", "T_share", "T_sim", "top_sim"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--k", type=int, default=7)
    ap.add_argument("--seed", type=int, default=41)
    a = ap.parse_args()
    g = load_gold(Path(a.xlsx))
    GOLD.mkdir(parents=True, exist_ok=True)
    g.to_parquet(GOLD / f"analyst_gold_{VERSION}.parquet", index=False)
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)

    def emb(uids):
        df = con.execute("SELECT post_uid, embedding FROM post_embeddings WHERE post_uid IN (SELECT unnest(?))", [list(uids)]).df()
        M = np.vstack(df.embedding.map(lambda v: np.asarray(v, dtype=np.float32)).values)
        M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
        return df.post_uid.tolist(), M

    uids, E = emb(g.post_uid)
    pos = {u: i for i, u in enumerate(uids)}
    g = g[g.post_uid.isin(pos)].reset_index(drop=True)
    ref_E = E[[pos[u] for u in g.post_uid]]
    refs = g[g.sec.isin(["Yes", "No"])].reset_index(drop=True)          # Unsure rows are not references
    ref_E = E[[pos[u] for u in refs.post_uid]]
    ref_sec = refs.sec.values; ref_D = refs.D.values; ref_T = refs["T"].values; yes_mask = (refs.sec == "Yes").values

    # ---- honest accuracy: leave-one-out on the reference rows
    loo = label_all(ref_E, ref_E, ref_sec, ref_D, ref_T, yes_mask, a.k, skip=list(range(len(refs))))
    loo = pd.concat([refs[["rid", "post_uid", "plat", "sec", "D", "T"]].reset_index(drop=True), loo], axis=1)

    # ---- candidates: distinct messages with a stored embedding and enough text, not a reference row
    ded = pd.read_parquet(ROOT / "data" / "post_dedup.parquet", columns=["post_uid", "is_first"])
    first = set(ded[ded.is_first.astype(bool)].post_uid)
    cand = con.execute("SELECT post_uid, platform, length(trim(text)) AS chars FROM post_facts WHERE text IS NOT NULL AND length(trim(text)) >= ? AND post_uid IN (SELECT post_uid FROM post_embeddings)", [MIN_CHARS]).df()
    cand = cand[cand.post_uid.isin(first) & ~cand.post_uid.isin(set(g.post_uid))]
    pool = len(cand)
    s = cand.sample(n=min(a.n, pool), random_state=a.seed).reset_index(drop=True)
    cu, CE = emb(s.post_uid)
    order = {u: i for i, u in enumerate(cu)}
    CE = CE[[order[u] for u in s.post_uid]]
    res = label_all(CE, ref_E, ref_sec, ref_D, ref_T, yes_mask, a.k)
    out = pd.concat([s[["post_uid", "platform"]], res], axis=1)
    out["version"] = VERSION
    # confidence tiers, picked from the leave-one-out sweep (so optimistic: chosen on the same 191 rows). The relevance vote alone is weak because the
    # gold set is 70% relevant; only P(yes) >= 0.9 is treated as "likely relevant" (LOO precision 91%, recall 36%).
    for df in (out, loo):
        df["p_yes"] = np.where(df.knn_sec == "Yes", df.sec_share, 1 - df.sec_share)
        df["relevance_tier"] = np.where(df.p_yes >= 0.9, "likely", np.where(df.p_yes >= 0.8, "possible", "unlikely"))
        df["D_confident"] = (df.relevance_tier != "unlikely") & (df.D_share >= 0.6)
        df["T_confident"] = (df.relevance_tier != "unlikely") & (df.T_share >= 0.5)

    meta = pd.DataFrame([{"version": VERSION, "built_at": datetime.now(timezone.utc).isoformat(), "k": a.k, "seed": a.seed, "n_candidates_pool": pool, "n_labelled": len(out),
                          "n_refs": len(refs), "n_refs_yes": int(yes_mask.sum()), "reference": "Analyst columns only (198 rows, Unsure excluded from references)",
                          "method": "similarity-weighted kNN on stored bge-m3 embeddings; no training; production Postgres not touched"}])
    dbp = GOLD / f"topic_knn_{VERSION}.duckdb"
    w = duckdb.connect(str(dbp))
    for name, df in (("knn_label_v1", out), ("knn_loo_v1", loo), ("knn_meta", meta)):
        w.register("df_", df); w.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df_"); w.unregister("df_")
    w.close()
    print(f"references {len(refs)} (yes {int(yes_mask.sum())}); candidate pool {pool}; labelled {len(out)} -> {dbp.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
