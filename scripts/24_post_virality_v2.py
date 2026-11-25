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
  * reposts ('RT @user', 'retweet @user': counters are the original's) and recurring templates (same author, same normalised opening, >= 5 posts on >= 3 days) and short-message feeds (an author with >= 20 posts a day, median text <= 60 characters; param exclude_feeds) get rule_original = False and are never candidates. Trade-off measured on the judged sample: the feed rule removed 3 of 3 artefact candidates but also 1 of 68 breakouts
  * YouTube videos with views = 0 mean 'counter unavailable' (as in stage 24) -> NULL
  * unknown scrape lag: compared with the whole platform, not with a fresh age band (as in stage 24)
Switch: the output is NOT read by the dashboard; nothing consumes it until the judged-sample gate (spec section 8) passes.

Usage:  python scripts/24_post_virality_v2.py [--source prod | --input path/to/post_facts_like.parquet]
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
    template_min_posts=5, template_min_days=3, template_key_chars=50, template_min_key_len=12,
    feed_min_posts=300, feed_min_per_day=20.0, feed_max_median_len=60, exclude_feeds=True,
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
    if "scraped_at" in d.columns:                                   # same post_uid seen twice (e.g. a tweet in two source tables): keep the latest scrape, as stage 01
        d = d.sort_values("scraped_at", kind="stable", na_position="first").drop_duplicates("post_uid", keep="last")
    else:
        d = d.drop_duplicates("post_uid", keep="last")
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


_REPOST_RE = r"(?i)^\s*(rt|retweet)\s+@"
_URL_RE = r"https?://\S+"


def repost_flag(head: pd.Series) -> pd.Series:
    """Posts that start 'RT @user' / 'retweet @user': their counters are the ORIGINAL post's, so they are never ranked as independent posts."""
    return head.fillna("").astype(str).str.match(_REPOST_RE)


def template_flag(d: pd.DataFrame, P=PARAMS) -> tuple[pd.Series, pd.Series]:
    """Recurring bulletin / template feed: the same author posts, on >= template_min_days different days and >= template_min_posts times, texts whose
    normalised opening is identical (lower case, URLs removed, every digit -> 0, punctuation dropped, first template_key_chars characters).
    Alert bulletins ('Threat of ballistic missiles from ... 25 October') and hiring templates repeat their wording while only numbers and places change."""
    out = pd.Series(False, index=d.index); feed_out = pd.Series(False, index=d.index)
    if "text_head" not in d.columns or "posted_at" not in d.columns:
        return out, feed_out
    key = (d["text_head"].fillna("").astype(str).str.lower().str.replace(_URL_RE, " ", regex=True).str.replace(r"\d", "0", regex=True)
           .str.replace(r"[^\w\s]", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip().str.slice(0, P["template_key_chars"]))
    ok = (key.str.len() >= P["template_min_key_len"]) & d["author_uid"].notna()
    day = pd.to_datetime(d["posted_at"], utc=True, errors="coerce").dt.date
    t = pd.DataFrame({"pl": d["platform"], "au": d["author_uid"], "k": key, "day": day})[ok]
    g = t.groupby(["pl", "au", "k"])
    n = g["day"].transform("size"); nd = g["day"].transform("nunique")
    out.loc[t.index[(n >= P["template_min_posts"]) & (nd >= P["template_min_days"])]] = True
    # short-message feed: an author that posts >= feed_min_per_day messages a day (>= feed_min_posts in all) with a median length <= feed_max_median_len
    # characters is an alert feed whose wording varies too much for the opening-text rule (e.g. one-line air-raid alerts)
    a = pd.DataFrame({"pl": d["platform"], "au": d["author_uid"], "day": day, "len": d["text_head"].fillna("").astype(str).str.len()})[d["author_uid"].notna()]
    ag = a.groupby(["pl", "au"]).agg(n=("len", "size"), nd=("day", "nunique"), med=("len", "median"))
    feed = ag[(ag["n"] >= P["feed_min_posts"]) & (ag["n"] / ag["nd"].clip(lower=1) >= P["feed_min_per_day"]) & (ag["med"] <= P["feed_max_median_len"])].index
    if len(feed):
        m = pd.MultiIndex.from_arrays([d["platform"], d["author_uid"]]).isin(feed)
        feed_out.loc[m] = True
    return out, feed_out


def compute(df: pd.DataFrame, P=PARAMS):
    d = collapse_copies(df.reset_index(drop=True).copy())
    assert d["post_uid"].is_unique, "duplicate post_uid"
    for c in COUNTERS:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float64")
    d.loc[(d["platform"] == "youtube_video") & (d["views"] == 0), "views"] = np.nan       # counter unavailable, not "nobody watched"
    use = usable_counters(d, P); tier = {pl: tier_of(u) for pl, u in use.items()}
    d["tier"] = d["platform"].map(tier)
    d["is_repost"] = repost_flag(d["text_head"]) if "text_head" in d.columns else False
    d["is_template"], d["is_feed"] = template_flag(d, P)
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
    bad = d["is_repost"].to_numpy(bool) | d["is_template"].to_numpy(bool) | (d["is_feed"].to_numpy(bool) if P["exclude_feeds"] else False)
    d["rule_original"] = pd.array(~bad, dtype="boolean")          # not a repost, not a recurring bulletin, not a short-message feed (when exclude_feeds)
    rules = ["rule_tail", "rule_min_support", "rule_baseline", "rule_spread", "rule_original"]
    appl = d[rules].notna().to_numpy(); val = d[rules].fillna(True).to_numpy(bool)
    d["is_candidate"] = d["score_pct"].notna() & (~appl | val).all(axis=1) & d["rule_tail"].fillna(False).astype(bool)
    d["qualified_rules"] = [",".join(n.replace("rule_", "") for n, a, v in zip(rules, ap, vv) if a and v) for ap, vv in zip(appl, val)]
    cols = ["post_uid", "platform", *[c for c in ("native_id", "url") if c in d.columns], "n_copies", "tier", "age_band", "ref_n", "reach_pct", "spread_pct", "comment_pct", "like_pct", "score_s", "score_pct",
            "baseline_kind", "author_overperf", "is_repost", "is_template", "is_feed", *rules, "qualified_rules", "is_candidate"]
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


def load_prod():
    """READ-ONLY extract of the counters from the production tables, using the stage-01 per-platform SQL (same column mapping and the same
    post_uid = sha1(platform|native_id)). Only counters/keys are selected (no text, no embedding). Returns (frame, snapshot_ts)."""
    import importlib.util
    from lib.pg import q, read_only_snapshot
    spec = importlib.util.spec_from_file_location("extract01", ROOT / "scripts" / "01_extract_base.py"); m = importlib.util.module_from_spec(spec)
    sys.modules["extract01"] = m; spec.loader.exec_module(m)
    frames = []
    with read_only_snapshot() as (conn, cur, ts):
        for plat, sql in m.PLATFORM_SQL.items():
            wrapped = ("SELECT platform, native_id, url, author_id, posted_at, scraped_at, likes, comments, shares, views, left(coalesce(text, ''), 80) AS text_head, "
                       "CASE WHEN native_id IS NULL OR btrim(native_id::text) = '' THEN (CASE WHEN text IS NULL OR text = '' THEN '' ELSE md5(text) END) END AS text_md5 "
                       f"FROM ({sql}) q")
            cols, rows = q(cur, wrapped); print(f"  {plat}: {len(rows):,} rows", flush=True)
            if rows:
                frames.append(pd.DataFrame(rows, columns=cols))
    d = pd.concat(frames, ignore_index=True)
    d["posted_at"] = pd.to_datetime(d["posted_at"], utc=True, errors="coerce"); d["scraped_at"] = pd.to_datetime(d["scraped_at"], utc=True, errors="coerce")
    d["scrape_lag_days"] = (d["scraped_at"] - d["posted_at"]).dt.total_seconds() / 86400.0
    d.loc[(d["scrape_lag_days"] < 0) | (d["scrape_lag_days"] > 5000), "scrape_lag_days"] = np.nan          # clock skew / epoch errors, as stage 07
    has = d["native_id"].notna() & (d["native_id"].astype(str).str.strip() != "")
    d["post_uid"] = [m._uid(pl, str(n)) if h else m._uid(pl, t or "", str(a or "")) for pl, n, h, t, a in zip(d["platform"], d["native_id"], has, d["text_md5"], d["author_id"])]
    d = d.rename(columns={"author_id": "author_uid"})
    return d[["post_uid", "platform", "native_id", "author_uid", "url", "posted_at", "scraped_at", "scrape_lag_days", "likes", "comments", "shares", "views", "text_head"]], ts


def load(path: str | None) -> pd.DataFrame:
    cols = "post_uid, platform, native_id, author_uid, url, posted_at, scraped_at, scrape_lag_days, likes, comments, shares, views"
    if path:
        import pyarrow.parquet as pq
        have = set(pq.ParquetFile(path).schema_arrow.names)
        return pd.read_parquet(path, columns=[c.strip() for c in (cols + ", text_head").split(",") if c.strip() in have])
    import duckdb
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    try:
        return con.execute(f"SELECT {cols}, left(coalesce(text, ''), 80) AS text_head FROM post_facts").df()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--input", default=None, help="parquet with the post_facts-like columns")
    ap.add_argument("--source", choices=["local", "prod"], default="local", help="prod = READ-ONLY extract from production (snapshot cached under data/)")
    a = ap.parse_args(); snap = None
    if a.source == "prod":
        df, snap = load_prod(); cache = ROOT / "data" / f"prod_counters_{snap:%Y%m%d_%H%M}.parquet"; df.to_parquet(cache, index=False); print(f"prod snapshot cached: {cache}")
    else:
        df = load(a.input)
        import re
        mm = re.search(r"prod_counters_(\d{8})_(\d{4})", a.input or "")
        snap = pd.Timestamp(f"{mm.group(1)} {mm.group(2)[:2]}:{mm.group(2)[2:]}", tz="UTC") if mm else None
    print(f"posts in: {len(df):,}")
    out, base = compute(df); self_checks(out, len(out)); print(f"copies collapsed: {len(df) - len(out):,} rows")
    out["snapshot_ts"] = snap; base["snapshot_ts"] = snap
    out.to_parquet(OUT, index=False); base.to_parquet(OUT_BASE, index=False)
    print(f"wrote {OUT} ({len(out):,}) and {OUT_BASE} ({len(base):,})")
    g = out.groupby(["tier", "platform"]).agg(n=("post_uid", "size"), scored=("score_s", lambda s: int(s.notna().sum())), candidates=("is_candidate", "sum"))
    g["cand_pct"] = (100 * g["candidates"] / g["n"]).round(2); print(g.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
