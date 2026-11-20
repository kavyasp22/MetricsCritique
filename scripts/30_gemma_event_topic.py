#!/usr/bin/env python3
"""Stage 30 -- A-code (event topic) per post with the local Gemma (prompt in scripts/lib/event_topic_llm.py).

PILOT ONLY for now: labels a stratified fresh sample of posts that already have a D theme and writes a parquet for hand review; nothing is
written to a database. Reads data/analytics.duckdb read-only. Needs GEMMA_URL / GEMMA_MODEL / GEMMA_API_KEY in .env.

    python scripts/30_gemma_event_topic.py --pilot 150 --out PATH.parquet [--exclude PATH.parquet ...]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import event_topic_llm as E  # noqa: E402
from lib import gemma_client as G  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def one(item):
    uid, text = item
    r = G.chat_json(E.system_prompt(), E.user_prompt(text), max_tokens=500, validate=E.validate)
    o = r["obj"] or {}
    ev = o.get("evidence") or ""
    return {"post_uid": uid, "g_subcode": o.get("subcode"), "g_conf": float(o["confidence"]) if r["ok"] else None,
            "g_evidence": ev, "g_evidence_ok": E.evidence_in_text(ev, text) if ev else None,
            "g_ok": r["ok"], "g_error": r["error"], "g_tokens": r["tokens"], "g_latency_s": r["latency_s"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", type=int, default=150)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    z = pd.read_parquet(ROOT / "data" / "post_event_topic_v1.parquet")
    th = pd.read_parquet(ROOT / "data" / "post_theme_v1.parquet", columns=["post_uid", "theme", "tier"])
    kn = pd.read_parquet(ROOT / "data" / "post_theme_knn_v1.parquet", columns=["post_uid", "knn_theme"])
    kn = kn[kn.knn_theme != "unassigned"].rename(columns={"knn_theme": "theme"}).assign(tier="neighbour")
    allp = pd.concat([th[th.theme != "unassigned"], kn]).drop_duplicates("post_uid")
    dd = pd.read_parquet(ROOT / "data" / "post_dedup.parquet", columns=["post_uid", "is_first"])
    allp = allp.merge(dd, on="post_uid"); allp = allp[allp.is_first]
    used = set()
    for p in a.exclude:
        used |= set(pd.read_parquet(p).post_uid)
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    t = con.execute("select post_uid, text from post_facts").fetchdf(); con.close()
    allp = allp.merge(t, on="post_uid"); allp = allp[(allp.text.str.len() >= 40) & ~allp.post_uid.isin(used)]
    rng = np.random.default_rng(a.seed)
    per = max(1, a.pilot // allp.theme.nunique())
    S = pd.concat([g.sample(min(per, len(g)), random_state=int(rng.integers(1e6))) for _, g in allp.groupby("theme")])
    S = S.sample(frac=1, random_state=a.seed).reset_index(drop=True)
    print(f"[30] sample {len(S)} posts ({per} per theme); endpoint host {G.endpoint_host().split(':')[0][:0]}configured", flush=True)
    rows = G.run_batch(list(zip(S.post_uid, S.text)), one, concurrency=16)
    R = pd.DataFrame(rows)
    S = S.merge(R, on="post_uid").merge(z[["post_uid", "subcode", "score"]].rename(columns={"subcode": "zs_subcode", "score": "zs_score"}), on="post_uid", how="left")
    S["id"] = range(len(S))
    S.to_parquet(a.out, index=False)
    print(f"[30] ok {int(S.g_ok.sum())}/{len(S)}; median latency {S.g_latency_s.median():.1f}s; tokens/post {S.g_tokens.mean():.0f}")
    print(S.g_subcode.value_counts().head(20).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
