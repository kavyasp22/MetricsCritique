#!/usr/bin/env python3
"""
Stage 24 v3 -- v2 (tiers, attention percentile, author baseline, exclusions) + empirical-Bayes spread efficiency + composite ranking.
PROPOSAL; versioned (stage 24 and 24_v2 are untouched). Writes NEW files only:

    data/post_virality_v3.parquet               one row per distinct post (copies collapsed)
    data/platform_virality_priors_v3.parquet     the empirical-Bayes priors (p0, C, rho) per platform x stratum x age band

What v3 adds to v2 (spec: Project-documents-final/Viral_Posts_Virality_Spec_and_Research.md, section 8D):
  * spread_eff_pct  -- percentile (platform x stratum x age band) of the SHRUNK share rate (k + C*p0) / (n + C), tiers A and B only:
        k = shares/forwards; n = views where the post has views, else likes + comments + shares (two strata, never mixed).
        p0 and C are estimated per cohort by the beta-binomial moment estimator (lib/stats_v2.beta_binomial_prior), falling back from the
        age band to the whole platform x stratum when the band has fewer than `cohort_min_posts` posts. 1 share on 1 view therefore cannot win.
  * tier D scale    -- attention_pct = percentile of w_l*ln(1+likes) + w_c*ln(1+comments) (0.6 / 0.4; 0.15 / 0.85 when likes are non-zero on < 20 % of posts,
        a data rule, not a platform name). Labelled "high engagement"; it measures size, not spread.
  * attention_pct   -- tiers A-C: v2 score_pct (percentile of the weighted counter percentiles); tier D: the log-engagement percentile above.
  * composite       -- attention_pct ** 0.75 * spread_eff_pct ** 0.25 (asymmetric, keeps large broadcasts near the top) and, for the benchmark only,
        the symmetric geometric mean; both ranked within platform x age band. Tiers without spread efficiency use attention_pct.
  * gate            -- v2 rules, with the tail taken on attention_pct, plus rule_spread_eff: spread_eff_pct >= 0.5 for tiers A and B.
BENCHMARK RESULT (AI-assisted, section 8D; local snapshot): (1) spread_eff_pct is independent of volume (Spearman with raw activity -0.18..0.17) -> kept as a
DESCRIPTIVE column / separate sort ("most efficient"), not blended; (2) the composite FAILS the guardrail: the 25 most-viewed Twitter posts rank at a median of
55,146 of 121,415 under 0.75/0.25 (sqrt: 91,983), because attention percentiles saturate near 1.0 while spread_eff spans 0..1 -> not used for ordering;
(3) the spread_eff >= 0.5 gate removed 48 posts judged breakout 31/48 (65 %) vs 27/46 (59 %) for kept ones -> gate OFF by default (spread_gate=False);
(4) tier-D log engagement vs the v2 weighted percentiles: no evidence of a gain (log top 6/13 breakouts vs old top 29/48) -> OFF by default (tier_d_log=False).
So by default is_candidate_v3 equals is_candidate_v2; the new value is spread_eff_pct and the benchmark. Nothing in the dashboard reads this file.

Usage:  python scripts/24_post_virality_v3.py [--source prod | --input parquet]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import stats_v2 as S  # noqa: E402

_spec = importlib.util.spec_from_file_location("v2stage", ROOT / "scripts" / "24_post_virality_v2.py")
v2 = importlib.util.module_from_spec(_spec); sys.modules["v2stage"] = v2; _spec.loader.exec_module(v2)

OUT = ROOT / "data" / "post_virality_v3.parquet"
OUT_PRIORS = ROOT / "data" / "platform_virality_priors_v3.parquet"
P3 = dict(
    cohort_min_posts=200, prior_min_posts=30,
    d_weights=(0.6, 0.4), d_weights_sparse=(0.15, 0.85), d_sparse_nonzero=0.20,       # (likes, comments)
    composite_exp=(0.75, 0.25), spread_eff_min=0.50, tail=0.99,
    spread_gate=False, tier_d_log=False,      # both OFF by default: not supported by the section-8D benchmark (see docstring)
)


def band_labels(lag: np.ndarray, P=v2.PARAMS) -> np.ndarray:
    edges = np.asarray(P["age_bands"], float)
    names = np.array([f"{a:g}-{b:g}d" for a, b in zip(edges[:-1], edges[1:])] + [f"{edges[-1]:g}d+"], dtype=object)
    out = np.full(len(lag), "unknown", dtype=object); ok = np.isfinite(lag)
    out[ok] = names[np.digitize(lag[ok], edges, right=False) - 1]
    return out


def ratio_parts(d: pd.DataFrame, use: dict, tier: dict):
    """(k, n, stratum) per post. k = shares; n = views (stratum 'views') or likes + comments + shares (stratum 'engagement'). NaN where not applicable."""
    k = np.full(len(d), np.nan); n = np.full(len(d), np.nan); st = np.full(len(d), "", dtype=object)
    for pl, g in d.groupby("platform"):
        if tier.get(pl) not in ("A", "B"):
            continue
        ix = g.index.to_numpy(); shares = g["shares"].to_numpy(float)
        eng = g[["likes", "comments", "shares"]].fillna(0).sum(axis=1).to_numpy(float)
        if use[pl]["views"]:
            views = g["views"].to_numpy(float); has_v = np.isfinite(views) & (views > 0)
            n_i = np.where(has_v, views, eng); s_i = np.where(has_v, "views", "engagement")
        else:
            n_i = eng; s_i = np.full(len(g), "engagement", dtype=object)
        ok = np.isfinite(shares) & (n_i > 0)
        k[ix[ok]] = shares[ok]; n[ix[ok]] = n_i[ok]; st[ix[ok]] = s_i[ok]
    return k, n, st


def spread_efficiency(d: pd.DataFrame, use: dict, tier: dict, P=P3):
    k, n, st = ratio_parts(d, use, tier)
    lag = d["scrape_lag_days"].to_numpy(float); band = band_labels(lag); plat = d["platform"].to_numpy(object)
    shrunk = np.full(len(d), np.nan); p0v = np.full(len(d), np.nan); Cv = np.full(len(d), np.nan); rows = []
    valid = np.isfinite(k) & np.isfinite(n) & (st != "")
    key_ps = np.char.add(np.char.add(plat.astype(str), "|"), st.astype(str))
    for ps in np.unique(key_ps[valid]):
        m_ps = valid & (key_ps == ps)
        p_all, C_all, r_all = S.beta_binomial_prior(k[m_ps], n[m_ps], min_posts=P["prior_min_posts"])
        rows.append(dict(platform=ps.split("|")[0], stratum=ps.split("|")[1], age_band="all", n_posts=int(m_ps.sum()), p0=p_all, C=C_all, rho=r_all, level="platform"))
        for b in np.unique(band[m_ps]):
            m = m_ps & (band == b); nb = int(m.sum())
            if nb >= P["cohort_min_posts"]:
                p0, C, rho = S.beta_binomial_prior(k[m], n[m], min_posts=P["prior_min_posts"]); lvl = "band"
                if not np.isfinite(p0):
                    p0, C, rho, lvl = p_all, C_all, r_all, "platform"
            else:
                p0, C, rho, lvl = p_all, C_all, r_all, "platform"
            rows.append(dict(platform=ps.split("|")[0], stratum=ps.split("|")[1], age_band=b, n_posts=nb, p0=p0, C=C, rho=rho, level=lvl))
            shrunk[m] = S.eb_ratio(k[m], n[m], p0, C); p0v[m] = p0; Cv[m] = C
    pct, _, _ = v2._group_pct(shrunk, lag, np.where(valid, key_ps, "x").astype(object))
    return pct, shrunk, k / np.where(n > 0, n, np.nan), p0v, Cv, st, pd.DataFrame(rows)


def tier_d_attention(d: pd.DataFrame, tier: dict, P=P3):
    """Percentile of weighted log engagement within platform x age band, tier D only."""
    lag = d["scrape_lag_days"].to_numpy(float); plat = d["platform"].to_numpy(object)
    val = np.full(len(d), np.nan)
    for pl, g in d.groupby("platform"):
        if tier.get(pl) != "D":
            continue
        likes_nz = float((g["likes"] > 0).mean()); wl, wc = P["d_weights_sparse"] if likes_nz < P["d_sparse_nonzero"] else P["d_weights"]
        val[g.index.to_numpy()] = wl * np.log1p(g["likes"].fillna(0).to_numpy(float)) + wc * np.log1p(g["comments"].fillna(0).to_numpy(float))
    pct, _, _ = v2._group_pct(val, lag, plat)
    return pct


def compute(df: pd.DataFrame, P=P3):
    base, _ = v2.compute(df)                                          # v2 output: tiers, score_pct, author baseline, v2 rules, exclusions, v2 candidates
    d = v2.collapse_copies(df.reset_index(drop=True).copy())
    for c in v2.COUNTERS:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float64")
    d.loc[(d["platform"] == "youtube_video") & (d["views"] == 0), "views"] = np.nan
    use = v2.usable_counters(d); tier = {pl: v2.tier_of(u) for pl, u in use.items()}
    d = d.reset_index(drop=True)
    sp_pct, shrunk, raw, p0v, Cv, stratum, priors = spread_efficiency(d, use, tier, P)
    d_att = tier_d_attention(d, tier, P)
    x = d[["post_uid"]].copy(); x["spread_eff_pct"] = sp_pct; x["spread_ratio_eb"] = shrunk; x["spread_ratio_raw"] = raw
    x["spread_prior_p0"] = p0v; x["spread_prior_C"] = Cv; x["spread_stratum"] = stratum; x["d_attention_pct"] = d_att
    o = base.merge(x, on="post_uid", how="left")
    is_d = o["tier"].eq("D").to_numpy()
    use_log = is_d & bool(P["tier_d_log"])
    o["attention_pct"] = np.where(use_log, o["d_attention_pct"], o["score_pct"])
    o["attention_basis"] = np.where(use_log, "log_engagement", "counter_percentiles")
    a = o["attention_pct"].to_numpy(float); s = o["spread_eff_pct"].to_numpy(float); e1, e2 = P["composite_exp"]
    comp = np.where(np.isfinite(s), np.power(np.clip(a, 0, 1), e1) * np.power(np.clip(s, 0, 1), e2), a)
    sym = np.where(np.isfinite(s), np.sqrt(np.clip(a, 0, 1) * np.clip(s, 0, 1)), a)
    lag = d.set_index("post_uid").loc[o["post_uid"], "scrape_lag_days"].to_numpy(float); plat = o["platform"].to_numpy(object)
    o["composite_viral_score"] = comp; o["composite_viral_pct"], _, _ = v2._group_pct(comp, lag, plat)
    o["composite_sqrt_pct"], _, _ = v2._group_pct(sym, lag, plat)
    ab = o["attention_pct"].notna()
    o["rule_tail_v3"] = pd.array(np.where(ab, a >= P["tail"], None), dtype="boolean")
    need = o["tier"].isin(["A", "B"]).to_numpy()
    o["rule_spread_eff"] = pd.array(np.where(need, np.where(np.isfinite(s), s >= P["spread_eff_min"], False), None), dtype="boolean")
    rules = ["rule_tail_v3", "rule_min_support", "rule_baseline", "rule_spread", "rule_original"] + (["rule_spread_eff"] if P["spread_gate"] else [])
    appl = o[rules].notna().to_numpy(); val = o[rules].fillna(True).to_numpy(bool)
    o["is_candidate_v2"] = o["is_candidate"]
    o["is_candidate_v3"] = ab & (~appl | val).all(axis=1) & o["rule_tail_v3"].fillna(False).astype(bool)
    o["qualified_rules_v3"] = [",".join(n.replace("rule_", "") for n, p_, v in zip(rules, ap, vv) if p_ and v) for ap, vv in zip(appl, val)]
    return o, priors


def self_checks(o: pd.DataFrame):
    for c in ("attention_pct", "spread_eff_pct", "composite_viral_pct", "composite_sqrt_pct"):
        v = o[c].dropna(); assert ((v >= 0) & (v <= 1)).all(), f"{c} outside 0..1"
    assert o.loc[o.tier.isin(["C", "D", "E"]), "spread_eff_pct"].isna().all(), "spread efficiency exists only for tiers A and B"
    assert o.loc[o.tier == "E", "attention_pct"].isna().all() and not o.loc[o.tier == "E", "is_candidate_v3"].any()
    assert not o.loc[o.is_candidate_v3, "is_repost"].any() and not o.loc[o.is_candidate_v3, "is_template"].any()


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--input", default=None); ap.add_argument("--source", choices=["local", "prod"], default="local")
    a = ap.parse_args(); snap = None
    if a.source == "prod":
        df, snap = v2.load_prod()
    else:
        df = v2.load(a.input)
    print(f"posts in: {len(df):,}")
    o, priors = compute(df); self_checks(o)
    o["snapshot_ts"] = snap; priors["snapshot_ts"] = snap
    o.to_parquet(OUT, index=False); priors.to_parquet(OUT_PRIORS, index=False)
    print(f"wrote {OUT} ({len(o):,}) and {OUT_PRIORS} ({len(priors):,})")
    g = o.groupby(["tier", "platform"]).agg(n=("post_uid", "size"), cand_v2=("is_candidate_v2", "sum"), cand_v3=("is_candidate_v3", "sum"), spread_scored=("spread_eff_pct", lambda s: int(s.notna().sum())))
    print(g.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
