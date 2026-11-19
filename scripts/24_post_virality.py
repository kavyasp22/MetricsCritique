#!/usr/bin/env python3
"""
Stage 24 -- save the per-post virality percentile.

Script 07 already computes, for every post, a virality percentile (how the post's amplification
ratio ranks against posts of the same platform and age), averages it into the topic x platform x
day cells and then drops it. This stage runs the SAME two functions of script 07
(load_source, enrich_posts, same default parameters) and keeps the per-post value:

    data/post_virality.parquet
        post_uid          sha1(platform|native_id), same key as analytics.post_facts
        platform
        virality_pct      0..1, 0.5 = typical for that platform and age band; NaN when the post has no
                          usable ratio (no interaction signal, platform not rankable) -- never 0
        virality_ratio    the amplification ratio the percentile ranks
        virality_ref_n    posts in the reference set the percentile was ranked against
        scrape_lag_days   age at scrape, used to pick the age band

Why a separate stage: re-running 07 takes 45-60 min and rewrites every metric; this reads only
(READ-ONLY open of data/analytics.duckdb) and writes one new file, so nothing existing changes and
the dashboard can keep running.

Posts of platforms that script 07 leaves out of the time-series scope (TS_PLATFORMS_EXCLUDE) are
not in the file. Reddit has no counters, so its percentile is NaN.

Usage:  python scripts/24_post_virality.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "data" / "post_virality.parquet"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    m = _load(ROOT / "scripts" / "07_metrics_v2.py", "metrics07")
    P = m.PARAMS
    np.random.seed(P["seed"])
    m.validate_params(P)
    src = m.load_source()
    posts_all = src["posts"]
    ts_posts = posts_all[~posts_all["platform"].isin(m.TS_PLATFORMS_EXCLUDE)].copy()
    m.log(f"posts total={len(posts_all):,} in scope={len(ts_posts):,}")
    p = m.enrich_posts(ts_posts, P)
    out = pd.DataFrame({
        "post_uid": p["post_uid"], "platform": p["platform"],
        "virality_pct": p["virality_pct_post"].astype("float32"),
        "virality_ratio": p["virality_ratio"].astype("float64"),
        "virality_ref_n": p["virality_ref_n"],
        "scrape_lag_days": p["scrape_lag_days"].astype("float32"),
    })
    assert out["post_uid"].is_unique, "duplicate post_uid"
    ok = out["virality_pct"].between(0, 1) | out["virality_pct"].isna()
    assert ok.all(), "percentile outside 0..1"
    out.to_parquet(OUT, index=False)
    print(f"wrote {OUT}: {len(out):,} posts")
    g = out.groupby("platform")["virality_pct"].agg(n="size", rated=lambda s: int(s.notna().sum()),
                                                      median="median")
    print(g.sort_values("n", ascending=False).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
