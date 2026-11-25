"""Tests for scripts/24_post_virality_v3.py (empirical-Bayes spread efficiency, tier-D log engagement, composite, gate) on synthetic posts."""
import importlib.util, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("v3", ROOT / "scripts" / "24_post_virality_v3.py"); v3 = importlib.util.module_from_spec(spec); sys.modules["v3"] = v3; spec.loader.exec_module(v3)

def frame(n=800, seed=1):
    r = np.random.default_rng(seed); rows = []
    def add(pl, cols, k):
        for i in range(k):
            d = dict(post_uid=f"{pl}{i}", platform=pl, author_uid=f"{pl}a{i % 60}", scrape_lag_days=float(r.choice([0.2, 0.7, 1.5, 3.0])), likes=np.nan, comments=np.nan, shares=np.nan, views=np.nan)
            v = r.lognormal(7, 1.5)
            if "views" in cols: d["views"] = float(np.floor(v))
            if "shares" in cols: d["shares"] = float(r.binomial(int(max(v, 1)), 0.01))
            if "likes" in cols: d["likes"] = float(np.floor(v * r.uniform(0.01, 0.05)))
            if "comments" in cols: d["comments"] = float(np.floor(v * r.uniform(0.001, 0.01)))
            rows.append(d)
    add("tw", ["views", "shares", "likes", "comments"], n); add("fb", ["shares", "likes", "comments"], n); add("ig", ["likes", "comments"], n); add("rd", [], n)
    return pd.DataFrame(rows)

def test_tiny_post_is_not_top_spread_efficiency():
    d = frame(); i = d.index[d.platform == "tw"][0]; d.loc[i, ["views", "shares", "likes", "comments"]] = [1.0, 1.0, 0.0, 0.0]       # raw rate 1.0; "+1" smoothing 0.5
    o, _ = v3.compute(d); r = o[o.post_uid == d.loc[i, "post_uid"]].iloc[0]
    assert r.spread_ratio_raw == 1.0 and r.spread_ratio_eb < 0.2 and r.spread_eff_pct < 0.99

def test_spread_efficiency_only_for_tiers_a_and_b():
    o, _ = v3.compute(frame()); assert o.loc[o.tier.isin(["C", "D", "E"]), "spread_eff_pct"].isna().all() and o.loc[o.tier.isin(["A", "B"]), "spread_eff_pct"].notna().any()

def test_priors_are_estimated_and_in_range():
    o, pri = v3.compute(frame()); p = pri[pri.age_band == "all"]; assert len(p) >= 2 and (p.p0.between(0, 1)).all() and (p.C >= 1).all()

def test_tier_d_attention_follows_weighted_log_engagement():
    d = frame(); ig = d.index[d.platform == "ig"]; a, b = ig[0], ig[1]; d.loc[[a, b], "scrape_lag_days"] = 0.7
    d.loc[a, ["likes", "comments"]] = [5000.0, 0.0]; d.loc[b, ["likes", "comments"]] = [100.0, 100.0]          # high-like zero-comment vs balanced
    o, _ = v3.compute(d, {**v3.P3, 'tier_d_log': True}); sa = 0.6 * np.log1p(5000) + 0.4 * 0; sb = 0.6 * np.log1p(100) + 0.4 * np.log1p(100)
    pa = o.loc[o.post_uid == d.loc[a, "post_uid"], "attention_pct"].iloc[0]; pb = o.loc[o.post_uid == d.loc[b, "post_uid"], "attention_pct"].iloc[0]
    assert (pa > pb) == (sa > sb) and o.loc[o.platform == "ig", "attention_basis"].eq("log_engagement").all()

def test_default_candidates_equal_v2_and_gate_is_optional():
    d = frame(); o, _ = v3.compute(d); v3.self_checks(o)
    assert (o.is_candidate_v3 == o.is_candidate_v2).all()                                  # gate and tier-D log engagement are OFF by default
    og, _ = v3.compute(d, {**v3.P3, "spread_gate": True}); c = og[og.is_candidate_v3 & og.tier.isin(["A", "B"])]
    assert (c.spread_eff_pct >= 0.5).all() and og.is_candidate_v3.sum() <= o.is_candidate_v3.sum() and not og.loc[og.is_candidate_v3 & og.tier.eq("E")].shape[0]

def test_composite_defaults_to_attention_without_spread():
    o, _ = v3.compute(frame()); g = o[o.tier == "D"]; assert np.allclose(g.composite_viral_score, g.attention_pct, equal_nan=True)

def test_composite_is_asymmetric_toward_attention():
    o, _ = v3.compute(frame()); g = o[o.tier == "A"].dropna(subset=["attention_pct", "spread_eff_pct"]).iloc[0]
    assert abs(g.composite_viral_score - g.attention_pct ** 0.75 * g.spread_eff_pct ** 0.25) < 1e-9
