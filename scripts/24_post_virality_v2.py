#!/usr/bin/env python3
"""
Stage 24 v2 -- evidence-tiered, per-platform post virality (PROPOSAL; versioned, does not touch stage 24 / post_virality.parquet).

Spec: Project-documents-final/Viral_Posts_Virality_Spec_and_Research.md (sections 6.1-6.5). Read-only on the source; writes two NEW files:

    data/post_virality_v2.parquet            one row per post
        post_uid, platform, native_id, url (match keys back to the source tables), n_copies, tier (A..E), age_band, ref_n,
        reach_pct, spread_pct, comment_pct, like_pct   percentile of the counter within platform x age band (NaN when the counter is not usable/absent)
        score_s          weighted mean of the available usable counter percentiles (weights renormalised); NaN for tier E / no usable counter
        score_pct        rank of score_s within platform x age band (0..1)
        baseline_kind    'author' | 'author_shrunk' | 'none'
        author_overperf  (x+1)/(author baseline+1) on the platform's primary measure; NaN without a baseline
        rule_tail, rule_min_support, rule_baseline, rule_spread   booleans (NaN-safe: False when not applicable is stored as <NA>)
        qualified_rules  text, the rules that qualified the post
        is_candidate     all applicable rules hold
    data/platform_virality_baselines_v2.parquet   one row per platform x counter: tier, usable, coverage, p50/p90/p99 of non-zero values,
        baseline coverage, parameters

Rules (all data-derived per run; platforms are never hard-coded):
  * a counter is USABLE on a platform if non-NULL on >= 50 % and non-zero on >= 5 % of its posts
  * tier: A reach+spread (views and shares), B spread only, C reach only, D engagement only (likes/comments), E none -> score NULL (not 0)
  * score_s = sum(w_c * pct_c) / sum(w_c) over usable, non-NULL counters; weights shares .4, views .3, comments .2, likes .1 (PROPOSED, unvalidated)
  * candidate = score_pct >= 0.99 AND minimum support (primary counter(s) >= platform p90 of non-zero values) AND, where the platform has an
    author baseline for >= 50 % of its posts, author_overperf >= 5 (log-scale leave-one-out median; empirical-Bayes shrinkage for thin authors)
  * rows that share (platform, url) are copies of ONE post (local Instagram: 93,029 rows = 982 distinct posts): collapsed to the latest-scraped row, with n_copies kept
  * YouTube videos with views = 0 mean 'counter unavailable' (as in stage 24) -> NULL
  * unknown scrape lag: compared with the whole platform, not with a fresh age band (as in stage 24)
Switch: the output is NOT read by the dashboard; nothing consumes it until the judged-sample gate (spec section 8) passes.

Usage:  python scripts/24_post_virality_v2.py [--input path/to/post_facts_like.parquet]
        (default: READ-ONLY open of data/analytics.duckdb, table post_facts; the parquet needs the same columns)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import stats_v2 as S  # noqa: E402

OUT = ROOT / "data" / "post_virality_v2.parquet"
OUT_BASE = ROOT / "data" / "platform_virality_baselines_v2.parquet"

COUNTERS = ("shares", "views", "comments", "likes")
PARAMS = dict(
    min_present=0.50, min_nonzero=0.05,
    weights={"shares": 0.40, "views": 0.30, "comments": 0.20, "likes": 0.10},
    age_bands=[0.0, 0.5, 1.0, 2.0, 4.0, 7.0, 14.0, 30.0, 90.0], min_band_n=20,
    tail=0.99, support_q=0.90, overperf_min=5.0, baseline_min_coverage=0.50, baseline_min_others=5,
)
PCT_COL = {"shares": "spread_pct", "views": "reach_pct", "comments": "comment_pct", "likes": "like_pct"}


def usable_counters(df: pd.DataFrame, P=PARAMS) -> dict:
    """{platform: {counter: bool}} from presence (non-NULL) and non-zero rates."""
    out = {}
    for pl, g in df.groupby("platform"):
        out[pl] = {c: bool(g[c].notna().mean() >= P["min_present"] and (g[c] > 0).mean() >= P["min_nonzero"]) for c in COUNTERS}
    return out


def tier_of(use: dict) -> str:
    reach, spread, eng = use["views"], use["shares"], use["likes"] or use["comments"]
    return "A" if (reach and spread) else "B" if spread else "C" if reach else "D" if eng else "E"


def primary_counters(tier: str) -> list:
    return {"A": ["shares", "views"], "B": ["shares"], "C": ["views"], "D": ["comments", "likes"]}.get(tier, [])


def _group_pct(values: np.ndarray, lag: np.ndarray, groups: np.ndarray, P=PARAMS):
    """Percentile within group x age band; unknown lag -> whole group. Returns (pct, ref_n, band_label)."""
    n = len(values); pct = np.full(n, np.nan); ref = np.zeros(n); band = np.full(n, "", dtype=object)
    edges = np.asarray(P["age_bands"], float); names = [f"{a:g}-{b:g}d" for a, b in zip(edges[:-1], edges[1:])] + [f"{edges[-1]:g}d+"]
    fin = np.isfinite(values); known = fin & np.isfinite(lag); unknown = fin & ~np.isfinite(lag)
    ik = np.flatnonzero(known)
    if ik.size:
        pct[ik], ref[ik] = S.age_conditional_percentile(values[ik], lag[ik], groups[ik], bands=P["age_bands"], min_band_n=P["min_band_n"])
        band[ik] = np.array(names, dtype=object)[np.digitize(lag[ik], edges, right=False) - 1]
    for g in np.unique(groups[unknown]):
        pool = np.sort(values[fin & (groups == g)]); iu = np.flatnonzero(unknown & (groups == g))
        if pool.size < P["min_band_n"]:
            continue
        lo, hi = np.searchsorted(pool, values[iu], "left"), np.searchsorted(pool, values[iu], "right")
        pct[iu] = (lo + hi) / (2.0 * pool.size); ref[iu] = pool.size; band[iu] = "unknown"
    return pct, ref, band


def collapse_copies(d: pd.DataFrame) -> pd.DataFrame:
    """One row per (platform, url): the latest-scraped copy (ties: smallest post_uid); n_copies = rows collapsed. Rows without a url are kept as they are."""
    d = d.copy(); d["n_copies"] = 1
    if "url" not in d.columns:
        return d
    has = d["url"].notna() & (d["url"].astype(str) != "")
    keyed = d[has].copy(); rest = d[~has]
    if "scraped_at" in keyed.columns:
        keyed = keyed.sort_values(["platform", "url", "scraped_at", "post_uid"], ascending=[True, True, False, True])
    else:
        keyed = keyed.sort_values(["platform", "url", "post_uid"])
    n = keyed.groupby(["platform", "url"])["post_uid"].transform("size")
    keyed["n_copies"] = n.to_numpy()
    keyed = keyed.drop_duplicates(["platform", "url"], keep="first")
    return pd.concat([keyed, rest], ignore_index=True)


def compute(df: pd.DataFrame, P=PARAMS):
    d = collapse_copies(df.reset_index(drop=True).copy())
    assert d["post_uid"].is_unique, "duplicate post_uid"
    for c in COUNTERS:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float64")
    d.loc[(d["platform"] == "youtube_video") & (d["views"] == 0), "views"] = np.nan       # counter unavailable, not "nobody watched"
    use = usable_counters(d, P); tier = {pl: tier_of(u) for pl, u in use.items()}
    d["tier"] = d["platform"].map(tier)
    lag = d["scrape_lag_days"].to_numpy(float); groups = d["platform"].to_numpy(object)
    num = np.zeros(len(d)); den = np.zeros(len(d)); ref_n = np.zeros(len(d)); band = np.full(len(d), "", dtype=object)
    for c in COUNTERS:
        usable_row = d["platform"].map(lambda p: use[p][c]).to_numpy(bool)
        v = np.where(usable_row, d[c].to_numpy(float), np.nan)
        pct, ref, b = _group_pct(v, lag, groups, P)
        d[PCT_COL[c]] = pct
        w = P["weights"][c]; ok = np.isfinite(pct)
        num[ok] += w * pct[ok]; den[ok] += w
        take = ok & (ref > ref_n); ref_n[take] = ref[take]; band[take] = b[take]
    d["score_s"] = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    d["score_s"] = d["score_s"].where(d["tier"] != "E")
    d["age_band"], d["ref_n"] = band, ref_n
    d.loc[d["score_s"].isna(), ["age_band"]] = ""
    sp, _, _ = _group_pct(d["score_s"].to_numpy(float), lag, groups, P)
    d["score_pct"] = sp
    # platform p90 of non-zero primary counters (platform-level minimum support)
    p90 = {(pl, c): (d.loc[(d.platform == pl) & (d[c] > 0), c].quantile(P["support_q"]) if ((d.platform == pl) & (d[c] > 0)).sum() >= P["min_band_n"] else np.nan)
           for pl in use for c in COUNTERS}
    sup = np.zeros(len(d), bool); applicable = np.zeros(len(d), bool)
    for pl, t in tier.items():
        m = (d["platform"] == pl).to_numpy(); prim = [c for c in primary_counters(t) if use[pl][c]]
        if not prim:
            continue
        cond = [d[c].to_numpy(float) >= p90[(pl, c)] for c in prim]
        ok = np.logical_and.reduce(cond) if t == "A" else np.logical_or.reduce(cond)
        sup[m] = ok[m]; applicable[m] = True
    d["rule_min_support"] = pd.array(np.where(applicable, sup, None), dtype="boolean")
    d["rule_tail"] = pd.array(np.where(d["score_pct"].notna(), d["score_pct"] >= P["tail"], None), dtype="boolean")
    # author baseline on the platform's primary measure: spread where it exists (A/B), reach (C), engagement sum (D)
    meas = np.full(len(d), np.nan)
    for pl, t in tier.items():
        m = (d["platform"] == pl).to_numpy()
        if t in ("A", "B"): meas[m] = d.loc[m, "shares"].to_numpy(float)
        elif t == "C": meas[m] = d.loc[m, "views"].to_numpy(float)
        elif t == "D": meas[m] = (d.loc[m, "likes"].fillna(0) + d.loc[m, "comments"].fillna(0)).to_numpy(float)
    base_log = np.full(len(d), np.nan); kind = np.full(len(d), "none", dtype=object); cov = {}
    auth = d["author_uid"].to_numpy(object)
    for pl in use:
        idx = np.flatnonzero((groups == pl) & np.isfinite(meas) & pd.notna(auth))
        if idx.size < 50:
            cov[pl] = 0.0; continue
        lv = np.log1p(meas[idx]); a = auth[idx]
        loo = np.full(idx.size, np.nan)
        for _, gi in pd.Series(np.arange(idx.size)).groupby(a):
            gi = gi.to_numpy()
            if len(gi) - 1 >= P["baseline_min_others"]:
                loo[gi] = np.log1p(S.leave_one_out_median(meas[idx][gi], P["baseline_min_others"]))
        thin, _, _ = S.thin_author_baseline_log(lv, a, min_others=P["baseline_min_others"])
        use_loo = np.isfinite(loo); use_thin = ~use_loo & np.isfinite(thin)
        bl = np.where(use_loo, loo, np.where(use_thin, thin, np.nan))
        base_log[idx] = bl; kind[idx[use_loo]] = "author"; kind[idx[use_thin]] = "author_shrunk"
        cov[pl] = float(np.isfinite(bl).sum() / max((groups == pl).sum(), 1))
    d["baseline_kind"] = kind
    d["author_overperf"] = np.where(np.isfinite(base_log), np.expm1(np.log1p(meas)) + 1.0, np.nan) / np.where(np.isfinite(base_log), np.expm1(base_log) + 1.0, np.nan)
    feasible = d["platform"].map(lambda p: cov.get(p, 0.0) >= P["baseline_min_coverage"]).to_numpy(bool)
    rb = np.where(feasible & np.isfinite(d["author_overperf"].to_numpy(float)), d["author_overperf"].to_numpy(float) >= P["overperf_min"], None)
    d["rule_baseline"] = pd.array(np.where(feasible, rb, None), dtype="boolean")
    spread_needed = d["tier"].isin(["A", "B"]).to_numpy()
    d["rule_spread"] = pd.array(np.where(spread_needed, (d["shares"].fillna(0) > 0).to_numpy(), None), dtype="boolean")
    rules = ["rule_tail", "rule_min_support", "rule_baseline", "rule_spread"]
    appl = d[rules].notna().to_numpy(); val = d[rules].fillna(True).to_numpy(bool)
    d["is_candidate"] = d["score_pct"].notna() & (~appl | val).all(axis=1) & d["rule_tail"].fillna(False).astype(bool)
    d["qualified_rules"] = [",".join(n.replace("rule_", "") for n, a, v in zip(rules, ap, vv) if a and v) for ap, vv in zip(appl, val)]
    cols = ["post_uid", "platform", *[c for c in ("native_id", "url") if c in d.columns], "n_copies", "tier", "age_band", "ref_n", "reach_pct", "spread_pct", "comment_pct", "like_pct", "score_s", "score_pct",
            "baseline_kind", "author_overperf", *rules, "qualified_rules", "is_candidate"]
    rows = []
    for pl, g in d.groupby("platform"):
        for c in COUNTERS:
            nz = g.loc[g[c] > 0, c]
            rows.append(dict(platform=pl, counter=c, tier=tier[pl], usable=use[pl][c], n_posts=len(g), pct_present=float(g[c].notna().mean()),
                             pct_nonzero=float((g[c] > 0).mean()), n_nonzero=len(nz), p50=nz.median() if len(nz) >= P["min_band_n"] else np.nan,
                             p90=nz.quantile(.9) if len(nz) >= P["min_band_n"] else np.nan, p99=nz.quantile(.99) if len(nz) >= P["min_band_n"] else np.nan,
                             baseline_coverage=cov.get(pl, 0.0), baseline_used=cov.get(pl, 0.0) >= P["baseline_min_coverage"]))
    return d[cols], pd.DataFrame(rows)


def self_checks(out: pd.DataFrame, n_in: int):
    assert len(out) == n_in and out["post_uid"].is_unique
    for c in ("reach_pct", "spread_pct", "comment_pct", "like_pct", "score_s", "score_pct"):
        v = out[c].dropna(); assert ((v >= 0) & (v <= 1)).all(), f"{c} outside 0..1"
    assert out.loc[out.tier == "E", "score_s"].isna().all(), "tier E must be NULL, not 0"
    assert not out.loc[out.score_pct.isna(), "is_candidate"].any(), "candidate without a score"


def load(path: str | None) -> pd.DataFrame:
    cols = "post_uid, platform, native_id, author_uid, url, scraped_at, scrape_lag_days, likes, comments, shares, views"
    if path:
        return pd.read_parquet(path, columns=[c.strip() for c in cols.split(",")])
    import duckdb
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    try:
        return con.execute(f"SELECT {cols} FROM post_facts").df()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--input", default=None); a = ap.parse_args()
    df = load(a.input); print(f"posts in: {len(df):,}")
    out, base = compute(df); self_checks(out, len(out))
    print(f"copies collapsed: {len(df) - len(out):,} rows")
    out.to_parquet(OUT, index=False); base.to_parquet(OUT_BASE, index=False)
    print(f"wrote {OUT} ({len(out):,}) and {OUT_BASE} ({len(base):,})")
    g = out.groupby(["tier", "platform"]).agg(n=("post_uid", "size"), scored=("score_s", lambda s: int(s.notna().sum())), candidates=("is_candidate", "sum"))
    g["cand_pct"] = (100 * g["candidates"] / g["n"]).round(2); print(g.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
