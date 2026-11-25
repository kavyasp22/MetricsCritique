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
        activity          the post's raw attention: views when it has any, else likes + comments + shares
        activity_basis    'views' or 'engagement' (the two are never compared with each other)
        activity_floor    the median activity of posts with a counter of the same platform AND basis
        activity_p90      the 90th percentile of activity of posts with a counter of the same platform AND basis
        activity_top10    activity >= activity_p90: the post is in the top 10% of its own platform by raw attention.
                          This is the page's default floor: in a judged sample of 40 posts ranked with the median
                          floor only 8 (20%) were clearly viral (top 10% on their platform) and 29 were merely popular.
        activity_pct      where the post's raw attention sits among posts of the SAME platform, basis and age band (0..1). Posts with
                          an unknown age (Snapchat, most YouTube, some TikTok) are compared with the whole platform and basis, not with a fresh
                          age band. This is what the page's "Biggest" order sorts by: it is comparable across platforms
                          (top 0.4% of Twitter, top 0.4% of Truth Social), unlike a multiple of a platform's line, which a thin
                          platform inflates (Truth Social took 153 of the top 300 under the multiple).
        activity_ref_n    posts in that reference set
        activity_mult     activity / activity_p90: raw attention as a multiple of the platform's top-10% line
                          (what the page's "Biggest" order sorts by; 1.0 = exactly at the line)
        activity_ok       activity >= activity_floor. The percentile ranks a RATIO, so a post with 4 views and
                          1 share can sit at 1.00; at the 99th percentile and above, 88% of Telegram and 98% of
                          Snapchat posts were below their own platform's median activity. The floor is relative
                          to the platform (no fixed number), and the page lets the reader switch it off.

Why a separate stage: re-running 07 takes 45-60 min and rewrites every metric; this reads only
(READ-ONLY open of data/analytics.duckdb) and writes one new file, so nothing existing changes and
the dashboard can keep running.

Copies of one post (same platform + url) are collapsed to the latest-scraped row before any percentile or line is computed (2026-11-24).
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
    # Rows that share (platform, url) are COPIES of one post (local Instagram: 93,029 rows = 982 posts). Percentiles, p90 and medians must be
    # computed over distinct posts, or a copied post is counted ~95 times in its own reference set. Keep the latest-scraped copy: the SAME rule the
    # dashboard API uses (UI/api_server.py, `viral_keep`), so every post the card shows has a row here. Rows not in post_facts (YouTube comments) are kept.
    import duckdb
    _con = duckdb.connect(str(m.DUCKDB_PATH), read_only=True)
    try:
        _copies = set(_con.execute(
            "SELECT post_uid FROM (SELECT post_uid, row_number() OVER (PARTITION BY platform, coalesce(nullif(url, ''), post_uid) "
            "ORDER BY scraped_at DESC NULLS LAST, post_uid) AS rn FROM post_facts) WHERE rn > 1").df()["post_uid"])
    finally:
        _con.close()
    n_before = len(ts_posts); ts_posts = ts_posts[~ts_posts["post_uid"].isin(_copies)].copy()
    m.log(f"posts total={len(posts_all):,} in scope={n_before:,}; copies of one post removed={n_before - len(ts_posts):,}; ranked posts={len(ts_posts):,}")
    p = m.enrich_posts(ts_posts, P)
    views = p["views"].astype("float64")
    # YouTube: views = 0 with other counters means the view counter was NOT AVAILABLE, not that nobody watched. The ratio
    # (likes + comments) / (views + 1) would turn that into ratio = likes and put the video at the top (41 of the 107 YouTube
    # posts at percentile >= 0.99 were of this kind). Such videos get no percentile and no activity.
    yt_zero = (p["platform"] == "youtube_video") & (views == 0)
    eng = p["eng_sum"].astype("float64")
    activity = views.where(views > 0, eng).mask(yt_zero)
    basis = np.where(views > 0, "views", "engagement")
    out = pd.DataFrame({
        "post_uid": p["post_uid"], "platform": p["platform"],
        "virality_pct": p["virality_pct_post"].astype("float32").mask(yt_zero),
        "virality_ratio": p["virality_ratio"].astype("float64").mask(yt_zero),
        "virality_ref_n": p["virality_ref_n"].where(~yt_zero, 0),
        "scrape_lag_days": p["scrape_lag_days"].astype("float32"),
        "activity": activity.astype("float64"), "activity_basis": basis,
    })
    # the lines are the platform's own distribution of raw attention over ALL posts that have a counter (rated or not), so
    # posts without a ratio (Snapchat has no views, some have no usable ratio) are judged against the same line
    has_act = out["activity"].notna()
    grp = out[has_act].groupby(["platform", "activity_basis"])["activity"]
    floor = pd.concat([grp.median().rename("activity_floor"), grp.quantile(0.9).rename("activity_p90")], axis=1)
    out = out.merge(floor, on=["platform", "activity_basis"], how="left")
    out["activity_top10"] = (out["activity"] >= out["activity_p90"]) & out["activity"].notna()
    out["activity_mult"] = (out["activity"] / out["activity_p90"]).where(out["activity_p90"] > 0)
    act = out["activity"].to_numpy(dtype=float)
    lag = out["scrape_lag_days"].to_numpy(dtype=float)
    known = np.isfinite(lag) & np.isfinite(act)
    unknown = ~np.isfinite(lag) & np.isfinite(act)
    grp_key = (out["platform"].astype(str) + "|" + out["activity_basis"].astype(str)).to_numpy(dtype=object)
    apct = np.full(len(out), np.nan)
    aref = np.zeros(len(out))
    # known age: percentile inside platform x basis x age band (falls back to the whole group when a band has < 20 posts)
    ik = np.flatnonzero(known)
    apct[ik], aref[ik] = m.S.age_conditional_percentile(act[ik], lag[ik], grp_key[ik], bands=P["age_bands"],
                                                        min_band_n=P["virality_min_band_n"])
    # unknown age (Snapchat, most YouTube, some TikTok): compared with the WHOLE platform x basis distribution of raw attention, not
    # with a tiny group of its own (that capped the percentile of a 32M-view TikTok video at 0.9993) and not with a "fresh" age band
    for key in np.unique(grp_key[unknown]):
        pool = np.sort(act[(grp_key == key) & np.isfinite(act)])
        iu = np.flatnonzero(unknown & (grp_key == key))
        if pool.size < P["virality_min_band_n"]:
            continue
        lo = np.searchsorted(pool, act[iu], side="left")
        hi = np.searchsorted(pool, act[iu], side="right")
        apct[iu] = (lo + hi) / (2.0 * pool.size)
        aref[iu] = pool.size
    out["activity_pct"] = apct.astype("float32")
    out["activity_ref_n"] = aref
    print(f"YouTube videos with views = 0 treated as unavailable (no percentile, no activity): {int(yt_zero.sum()):,}")
    out["activity_ok"] = (out["activity"] >= out["activity_floor"]) & out["activity"].notna()
    assert out["post_uid"].is_unique, "duplicate post_uid"
    ok = out["virality_pct"].between(0, 1) | out["virality_pct"].isna()
    assert ok.all(), "percentile outside 0..1"
    out.to_parquet(OUT, index=False)
    print(f"wrote {OUT}: {len(out):,} posts")
    g = out.groupby("platform")["virality_pct"].agg(n="size", rated=lambda s: int(s.notna().sum()),
                                                      median="median")
    print(g.sort_values("n", ascending=False).to_string())
    top = out[out["virality_pct"] >= 0.99]
    print("\nposts at percentile >= 0.99 that pass the activity floors, by platform:")
    print(top.groupby("platform").agg(n=("activity_ok", "size"), kept_median=("activity_ok", "sum"), kept_top10=("activity_top10", "sum")).assign(
        share_median=lambda d: (d["kept_median"] / d["n"]).round(2), share_top10=lambda d: (d["kept_top10"] / d["n"]).round(2)).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
