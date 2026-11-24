"""Tests for scripts/24_post_virality_v2.py on synthetic posts (mechanics only; no corpus data)."""
import importlib.util, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("v2", ROOT / "scripts" / "24_post_virality_v2.py"); v2 = importlib.util.module_from_spec(spec); sys.modules["v2"] = v2; spec.loader.exec_module(v2)

def frame(n=600, seed=0):
    r = np.random.default_rng(seed); rows = []
    def add(pl, cols, k):
        for i in range(k):
            d = dict(post_uid=f"{pl}{i}", platform=pl, author_uid=f"{pl}a{i % 40}", scrape_lag_days=float(r.choice([0.2, 0.7, 1.5, 3.0])), likes=np.nan, comments=np.nan, shares=np.nan, views=np.nan)
            for c in cols: d[c] = float(np.floor(r.lognormal(2, 1.5)))
            rows.append(d)
    add("tw", ["views", "shares", "likes", "comments"], n); add("fb", ["shares", "likes", "comments"], n); add("ig", ["likes", "comments"], n); add("rd", [], n)
    return pd.DataFrame(rows)

def test_tiers_follow_counters():
    d = frame(); use = v2.usable_counters(d); assert {p: v2.tier_of(u) for p, u in use.items()} == {"tw": "A", "fb": "B", "ig": "D", "rd": "E"}

def test_tier_e_is_null_not_zero():
    o, _ = v2.compute(frame()); e = o[o.tier == "E"]; assert len(e) and e.score_s.isna().all() and not e.is_candidate.any()

def test_zero_activity_is_never_a_candidate():
    d = frame(); d.loc[d.platform == "ig", ["likes", "comments"]] = 0.0           # an all-zero platform: p90 of non-zero does not exist
    o, _ = v2.compute(d); assert not o.loc[o.platform == "ig", "is_candidate"].any()

def test_percentiles_in_range_and_candidates_rare():
    d = frame(); o, _ = v2.compute(d); v2.self_checks(o, len(d)); assert o.is_candidate.mean() < 0.05

def test_missing_counter_is_excluded_not_zero():
    d = frame(); o, _ = v2.compute(d); assert o.loc[o.platform == "fb", "reach_pct"].isna().all()        # no views on this platform -> no reach percentile

def test_youtube_zero_views_is_unavailable():
    d = frame(); d["platform"] = np.where(d.platform == "tw", "youtube_video", d.platform); d.loc[d.platform == "youtube_video", "views"] = 0.0
    o, _ = v2.compute(d); assert o.loc[o.platform == "youtube_video", "reach_pct"].isna().all()

def test_baseline_never_uses_the_post_itself():
    d = frame(); o1, _ = v2.compute(d); d2 = d.copy(); i = d2.index[(d2.platform == "tw") & d2.shares.notna()][0]; d2.loc[i, "shares"] = 1e6
    o2, _ = v2.compute(d2)
    implied = lambda o, dd: (dd.loc[i, "shares"] + 1.0) / o.loc[i, "author_overperf"]            # implied baseline = (x+1) / overperf
    b1, b2 = implied(o1, d), implied(o2, d2)
    assert np.isfinite(b1) and abs(b1 - b2) / b1 < 1e-9          # changing the post's own value leaves its baseline unchanged

def test_deterministic():
    d = frame(); a, _ = v2.compute(d); b, _ = v2.compute(d); pd.testing.assert_frame_equal(a, b)

def test_copies_of_one_post_are_collapsed():
    d = frame(); d["url"] = [f"u{i}" for i in range(len(d))]; ig = d.platform == "ig"
    d.loc[ig, "url"] = [f"igpost{i % 7}" for i in range(int(ig.sum()))]; d["scraped_at"] = pd.Timestamp("2026-01-01")
    o, _ = v2.compute(d); g = o[o.platform == "ig"]
    assert len(g) == 7 and (g.n_copies > 1).all() and g.n_copies.sum() == int(ig.sum())
