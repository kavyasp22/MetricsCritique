#!/usr/bin/env python3
"""Smoke tests for the virality / reach / cross-platform changes.

Three tiers:
  1. FORMULA   - `virality_ratio` per platform group (pure, no data needed).
  2. CODE+DATA - run the current code over `analytics.duckdb` (read-only): no platform with a
                 rule may collapse to one percentile (the old 0.50 bug), and rule-less platforms
                 must stay NULL rather than be guessed.
  3. PUBLISHED - invariants of the shipped `metrics.duckdb` (cross-platform tables, reach columns)
                 and that the published run is not stale versus the code (tier 2 vs tier 3).

Data tiers skip when the DuckDB files are absent. Read-only throughout.

Run:  .venv/bin/python -m pytest scripts/tests/test_virality_reach_smoke.py -v
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

ANALYTICS = ROOT / "data" / "analytics.duckdb"
METRICS = ROOT / "data" / "metrics.duckdb"

# platforms that have a virality rule in 07_metrics_v2.virality_ratio
RULED = ["telegram", "snapchat", "tiktok", "twitter", "truthsocial", "mastodon", "facebook",
         "facebook_profile", "youtube_video", "instagram", "linkedin", "baidu_tieba"]
# no interaction signal -> must be NaN, never a fabricated score
RULELESS = ["baidu_video", "reddit", "whatsapp", "pinterest"]


@pytest.fixture(scope="module")
def m7():
    spec = importlib.util.spec_from_file_location("m07", ROOT / "scripts" / "07_metrics_v2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- 1. formulas
def test_video_group_is_shares_over_views(m7):
    for pl in ("telegram", "tiktok", "snapchat"):
        assert m7.virality_ratio(pl, 10, 5, 4, 99) == pytest.approx(4 / 100)


def test_discussion_group_is_comments_over_likes(m7):
    for pl in ("instagram", "linkedin", "baidu_tieba"):
        assert m7.virality_ratio(pl, 9, 3, 0, 0) == pytest.approx(3 / 10)
    # shares are 0 on these platforms; the ratio must still vary with comments
    assert m7.virality_ratio("instagram", 9, 1, 0, 0) != m7.virality_ratio("instagram", 9, 8, 0, 0)


def test_microblog_group_is_shares_over_interactions(m7):
    for pl in ("facebook", "truthsocial", "mastodon", "facebook_profile"):
        assert m7.virality_ratio(pl, 6, 3, 1, 0) == pytest.approx(1 / 11)


def test_twitter_two_pools(m7):
    assert m7.virality_ratio("twitter", 6, 3, 1, 99) == pytest.approx(1 / 100)  # views pool
    assert m7.virality_ratio("twitter", 6, 3, 1, 0) == pytest.approx(1 / 11)    # no-views pool


def test_youtube_video_and_comment(m7):
    assert m7.virality_ratio("youtube_video", 8, 2, 0, 99) == pytest.approx(10 / 100)
    # youtube_comment: `comments` slot carries sibling-comment count
    assert m7.virality_ratio("youtube_comment", 5, 4, 0, 0) == pytest.approx(5 / 5)


@pytest.mark.parametrize("pl", RULELESS)
def test_ruleless_platforms_are_nan(m7, pl):
    assert np.isnan(m7.virality_ratio(pl, 5, 5, 5, 5))


def test_no_signal_is_nan_not_zero(m7):
    for pl in ("telegram", "instagram", "twitter", "facebook"):
        assert np.isnan(m7.virality_ratio(pl, 0, 0, 0, 0)), pl


# --------------------------------------------------------------------------- 2. code over data
needs_analytics = pytest.mark.skipif(not ANALYTICS.exists(), reason="analytics.duckdb absent")
needs_metrics = pytest.mark.skipif(not METRICS.exists(), reason="metrics.duckdb absent")


@pytest.fixture(scope="module")
def scored(m7):
    import duckdb
    import lib.stats_v2 as S
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        p = con.execute("SELECT platform, likes, comments, shares, views, scrape_lag_days "
                        "FROM post_facts").fetchdf()
    finally:
        con.close()
    p["r"] = [m7.virality_ratio(*x) for x in zip(p.platform, p.likes, p.comments, p.shares, p.views)]
    tv = (p.platform == "twitter") & (p.views.fillna(0) > 0)
    tn = (p.platform == "twitter") & ~tv
    g = p.platform.copy()
    g[tv], g[tn] = "twitter_views", "twitter_noviews"
    p["pct"], _ = S.age_conditional_percentile(
        p.r.to_numpy(float), p.scrape_lag_days.to_numpy(float), g.to_numpy(object),
        bands=m7.PARAMS["age_bands"], min_band_n=m7.PARAMS["virality_min_band_n"])
    return p


@needs_analytics
@pytest.mark.parametrize("pl", RULED)
def test_ruled_platform_percentiles_do_not_collapse(scored, pl):
    s = scored.loc[scored.platform == pl, "pct"].dropna()
    assert len(s) > 0, f"{pl}: rule exists but zero percentiles produced"
    assert s.nunique() > 1, f"{pl}: collapsed to one value {s.iloc[0]:.2f} (the 0.50 bug)"
    assert s.between(0, 1).all()


@needs_analytics
@pytest.mark.parametrize("pl", RULELESS)
def test_ruleless_platforms_stay_null_in_data(scored, pl):
    assert scored.loc[scored.platform == pl, "pct"].notna().sum() == 0


@needs_analytics
def test_instagram_not_half_collapsed(scored):
    s = scored.loc[scored.platform == "instagram", "pct"].dropna()
    assert (s.round(2) == 0.50).mean() < 0.5


# --------------------------------------------------------------------------- 3. published tables
@pytest.fixture(scope="module")
def mdb():
    import duckdb
    con = duckdb.connect(str(METRICS), read_only=True)
    yield con
    con.close()


@needs_metrics
def test_cross_platform_tables_present_and_consistent(mdb):
    n_s = mdb.execute("SELECT count(*) FROM cross_platform_stories").fetchone()[0]
    n_p = mdb.execute("SELECT count(*) FROM cross_platform_posts").fetchone()[0]
    assert n_s > 0 and n_p >= 2 * n_s
    bad = mdb.execute("""
        SELECT
          count(*) FILTER (WHERE n_platforms < 2),
          count(*) FILTER (WHERE length(snippet) < 35),
          count(*) FILTER (WHERE story_ratio IS NOT NULL AND (story_ratio < 5 OR story_ratio > 80)),
          count(*) FILTER (WHERE abs(breadth_multiplier - (1 + 0.5*(n_platforms-1))) > 0.01),
          count(*) FILTER (WHERE abs(unified_reach - (observed_views + imputed_views)) > 1),
          count(*) FILTER (WHERE abs(effective_reach - unified_reach*breadth_multiplier) > 1),
          count(*) FILTER (WHERE spread_duration_hours < 0)
        FROM cross_platform_stories""").fetchone()
    assert bad == (0,) * 7, f"invariant violations (<2 plat, short, ratio, breadth, unified, effective, lag): {bad}"


@needs_metrics
def test_cross_platform_posts_join_cleanly(mdb):
    orphans = mdb.execute("SELECT count(*) FROM cross_platform_posts p "
                          "LEFT JOIN cross_platform_stories s USING(story_id) "
                          "WHERE s.story_id IS NULL").fetchone()[0]
    assert orphans == 0
    mism = mdb.execute("""SELECT count(*) FROM (SELECT story_id, count(DISTINCT platform) n
                          FROM cross_platform_posts GROUP BY 1)
                          JOIN cross_platform_stories USING(story_id) WHERE n <> n_platforms""").fetchone()[0]
    assert mism == 0


@needs_metrics
def test_origin_platform_is_earliest_post(mdb):
    wrong = mdb.execute("""
        SELECT count(*) FROM cross_platform_stories s
        JOIN (SELECT story_id, arg_min(platform, posted_at) o FROM cross_platform_posts GROUP BY 1) p
        USING(story_id) WHERE p.o <> s.origin_platform""").fetchone()[0]
    assert wrong == 0


@needs_metrics
def test_reach_proxy_populated_and_audience_norm_positive(mdb):
    assert mdb.execute("SELECT count(reach_proxy) FROM metrics_daily").fetchone()[0] > 0
    n, mn = mdb.execute("SELECT count(reach_audience_norm), min(reach_audience_norm) "
                        "FROM metrics_daily").fetchone()
    assert n > 0 and mn > 0


@needs_metrics
@needs_analytics
@pytest.mark.parametrize("pl", ["twitter", "mastodon", "truthsocial", "tiktok", "facebook_profile", "baidu_tieba"])
def test_published_metrics_not_stale_vs_code(mdb, scored, pl):
    """Code yields percentiles for `pl`; the shipped metrics must too. FAILS until 07/08 are re-run."""
    assert scored.loc[scored.platform == pl, "pct"].notna().sum() > 0
    n = mdb.execute("SELECT coalesce(sum(virality_n),0) FROM metrics_daily WHERE platform=?",
                    [pl]).fetchone()[0]
    assert n > 0, f"{pl}: code produces virality but published metrics.duckdb has none -> rebuild"


@needs_analytics
@needs_metrics
def test_youtube_comment_virality_published(mdb, m7):
    """Code can score YouTube comments (likes / sibling comments on the video); the shipped metrics must too.
    FAILS until 07/08 are re-run (published youtube_comment virality_n is 0)."""
    import duckdb
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        c = con.execute("SELECT video_id, likes FROM comment_facts").fetchdf()
    finally:
        con.close()
    sib = c.groupby("video_id").video_id.transform("count")
    r = np.array([m7.virality_ratio("youtube_comment", lk, sb, 0, 0) for lk, sb in zip(c.likes, sib)])
    assert np.isfinite(r).sum() > 0 and len(np.unique(r[np.isfinite(r)])) > 100   # rule produces a spread
    n = mdb.execute("SELECT coalesce(sum(virality_n),0) FROM metrics_daily WHERE platform='youtube_comment'").fetchone()[0]
    assert n > 0, "youtube_comment: code scores comments but published metrics have none -> rebuild"


@needs_metrics
@needs_analytics
def test_doc_claim_total_records():
    """Doc: 769,983 records / 17 platforms = post_facts + comment_facts."""
    import duckdb
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        n = con.execute("SELECT (SELECT count(*) FROM post_facts)+(SELECT count(*) FROM comment_facts)").fetchone()[0]
    finally:
        con.close()
    assert n == 769_983


# --------------------------------------------------------------------------- 4. script 19 (Phase 1)
@pytest.fixture(scope="module")
def stories19():
    """Run script 19's read-only computation (no DuckDB/JSON write) and return (stories, posts, stdout)."""
    import contextlib
    import io
    spec = importlib.util.spec_from_file_location("m19", ROOT / "scripts" / "19_cross_platform_reach.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        st, po = mod.compute_cross_platform_reach()
    return st, po, buf.getvalue()


@needs_analytics
def test_view_platforms_derived_from_data_not_hardcoded(stories19):
    """youtube_video and snapchat carry views in post_facts, so they must be treated as view-bearing."""
    import duckdb
    out = stories19[2]
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT platform FROM post_facts GROUP BY 1 HAVING max(coalesce(views,0)) > 0").fetchall()}
    finally:
        con.close()
    assert {"youtube_video", "snapchat", "telegram", "twitter", "tiktok"} <= names
    line = next(x for x in out.splitlines() if x.startswith("-> view-bearing platforms"))
    for pl in names:
        assert f"'{pl}'" in line, f"{pl} missing from view-bearing list"
    assert "'youtube'" not in line  # the platform name that does not exist in the data


@needs_analytics
def test_observed_views_counts_youtube_and_snapchat_posts(stories19):
    """Every story's observed_views must equal the views of its posts on view-bearing platforms."""
    import duckdb
    st, po, _ = stories19
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        con.register("po", po[["story_id", "post_uid"]])
        got = con.execute("""
            SELECT po.story_id, sum(CASE WHEN f.views > 0 THEN f.views ELSE 0 END) v
            FROM po JOIN post_facts f USING (post_uid)
            WHERE f.platform IN (SELECT platform FROM post_facts GROUP BY 1 HAVING max(coalesce(views,0)) > 0)
            GROUP BY 1""").fetchdf().set_index("story_id")["v"]
    finally:
        con.close()
    m = st.set_index("story_id")["observed_views"]
    diff = (m - got.reindex(m.index).fillna(0)).abs()
    assert (diff < 1).all(), f"{int((diff >= 1).sum())} stories: observed_views != views on view-bearing platforms"


@needs_analytics
def test_repeat_posts_by_one_author_not_counted_twice(stories19):
    st, po, _ = stories19
    # one representative post per (platform, author) -> unique posts can never exceed authors x platforms
    assert (st.n_unique_posts <= st.n_posts).all()
    assert (st.n_unique_posts >= st.n_platforms).all()
    assert (st.n_unique_posts <= st.n_authors * st.n_platforms).all()
    heavy = st[st.n_posts >= 20]
    assert len(heavy) > 0 and (heavy.n_unique_posts <= 4).all(), "heavy re-posters still counted individually"


@needs_analytics
def test_same_account_name_flag_matches_independent_check(stories19):
    import duckdb
    st, po, _ = stories19
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        nm = con.execute("SELECT post_uid, coalesce(author_name,'') n FROM post_facts").fetchdf().set_index("post_uid").n
    finally:
        con.close()
    key = nm.str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    per = po.assign(k=key.reindex(po.post_uid).to_numpy()).groupby("story_id").k.nunique()
    want = (per == 1)
    got = st.set_index("story_id").same_account_name
    assert (got.reindex(want.index) == want).all()
    assert got.any() and (~got).any()          # both kinds of story exist in the data


@needs_analytics
def test_story_columns_consistent(stories19):
    st, _, _ = stories19
    assert (st.single_source == (st.n_authors < 2)).all()
    assert (st.n_authors >= 1).all()
    assert (st.imputed_views >= 0).all()
    assert (st.origin_scrape_lag_days.dropna() >= 0).all()


# --------------------------------------------------------------------------- 5. reach split / estimate basis (R2, R5)
def test_reach_split_observed_vs_engagement_proxy(m7):
    import pandas as pd
    base = pd.DataFrame({
        "tone_sum": [0, 0, 0], "tone_n": [1, 1, 1], "hate_sum": [0, 0, 0], "hate_n": [1, 1, 1],
        "hate_hits": [0, 0, 0], "read_sum": [0, 0, 0], "read_n": [1, 1, 1], "read_cov": [1, 1, 1],
        "vir_sum": [0.5] * 3, "vir_n": [1] * 3, "reach_norm_sum": [0.0] * 3, "reach_norm_n": [0] * 3,
        "views_sum": [100.0, 0.0, np.nan], "E_sum": [110.0, 10.0, 0.0],   # E_sum INCLUDES views
        "eng_sum": [10.0, 10.0, 0.0]})                                      # interactions only
    out = m7._finish_cells(base)
    assert out.loc[0, "observed_views"] == 100.0                       # counter value kept
    assert np.isnan(out.loc[1, "observed_views"]) and np.isnan(out.loc[2, "observed_views"])
    # engagement_proxy = ln(1 + likes+comments+shares): the 100 views in cell 0 must NOT leak in
    assert out["engagement_proxy"].tolist() == pytest.approx([np.log1p(10), np.log1p(10), 0.0])
    # legacy column unchanged: views when present, else ln(1 + E_sum)
    assert out["reach_proxy"].tolist() == pytest.approx([100.0, np.log1p(10), 0.0])


@needs_analytics
def test_ratio_source_consistent_with_story_ratio(stories19):
    st, _, _ = stories19
    assert set(st.ratio_source) <= {"story_paired", "clamped_low", "clamped_high", "default"}
    assert (st.loc[st.ratio_source == "default", "story_ratio"] == 20.0).all()
    assert (st.loc[st.ratio_source == "clamped_low", "story_ratio"] == 5.0).all()
    assert (st.loc[st.ratio_source == "clamped_high", "story_ratio"] == 80.0).all()
    paired = st[st.ratio_source == "story_paired"]
    assert paired.story_ratio.between(5.0, 80.0).all()


# --------------------------------------------------------------------------- 6. URL tier (R4)
@pytest.fixture(scope="module")
def m19():
    spec = importlib.util.spec_from_file_location("m19u", ROOT / "scripts" / "19_cross_platform_reach.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_normalise_url_strips_tracking_fragment_and_case(m19):
    a = m19.normalise_url("https://www.BBC.co.uk/news/articles/abc123?at_medium=RSS&at_campaign=rss#x")
    b = m19.normalise_url("http://bbc.co.uk/news/articles/abc123/")
    assert a[0] == b[0] == "bbc.co.uk/news/articles/abc123"
    assert a[1] == "bbc.co.uk"
    # a real query parameter is kept
    assert m19.normalise_url("https://x.org/a/b?id=7&utm_source=z")[0] == "x.org/a/b?id=7"


@pytest.fixture(scope="module")
def url_tier(m19):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return m19.compute_url_links()


@needs_analytics
def test_url_links_invariants(m19, url_tier):
    links, posts = url_tier
    assert len(links) > 0
    assert links.link_id.is_unique
    assert (links.n_platforms >= 2).all() and (links.n_authors >= 2).all()
    assert not links.host.isin(m19.URL_DENY_HOSTS).any()
    assert set(links.match_confidence) <= {"high", "medium"}
    assert (links.loc[links.match_confidence == "medium", "host"].isin(m19.URL_SHORTENERS)).all()
    assert (links.loc[links.match_confidence == "high", "host"].isin(m19.URL_SHORTENERS) == False).all()  # noqa: E712
    assert (links.spread_duration_hours >= 0).all() and (links.observed_views >= 0).all()
    # repeat posts by one author count once
    assert (links.n_unique_posts <= links.n_posts).all()
    assert (links.n_unique_posts >= links.n_platforms).all()
    assert (links.n_unique_posts <= links.n_authors * links.n_platforms).all()
    assert (links.n_posts > links.n_unique_posts).any(), "expected at least one re-posted link in the data"


@needs_analytics
def test_url_posts_join_and_platform_counts(url_tier):
    links, posts = url_tier
    assert set(posts.link_id) == set(links.link_id)
    n = posts.groupby("link_id").platform.nunique()
    assert (links.set_index("link_id").n_platforms == n.reindex(links.link_id).to_numpy()).all()
    assert (posts.groupby("link_id").size().reindex(links.link_id).to_numpy() == links.n_posts.to_numpy()).all()


@needs_analytics
def test_url_tier_does_not_alter_exact_text_stories(stories19, url_tier):
    st, _, _ = stories19
    assert (st.n_platforms >= 2).all() and len(st) > 0   # exact-text tier computed independently


# --------------------------------------------------------------------------- 7. author baseline (Phase 3)
def test_loo_median_excludes_own_post_and_matches_bruteforce():
    import lib.stats_v2 as S
    rng = np.random.default_rng(3)
    for n in (6, 7, 12, 31):
        v = rng.lognormal(5, 1.5, n)
        got = S.leave_one_out_median(v, min_others=5)
        want = [np.median(np.delete(v, i)) for i in range(n)]
        assert np.allclose(got, want)
        w = v.copy(); w[0] = 1e12                       # blow up one post: its own baseline must not move
        assert S.leave_one_out_median(w, 5)[0] == pytest.approx(got[0])


def test_loo_median_needs_enough_other_posts():
    import lib.stats_v2 as S
    assert np.isnan(S.leave_one_out_median([5, 6, 7, 8, 9], min_others=5)).all()   # only 4 others each
    assert not np.isnan(S.leave_one_out_median([5, 6, 7, 8, 9, 10], min_others=5)).any()
    assert np.isnan(S.leave_one_out_median([np.nan, 1, 2], 1)[0])                   # NaN stays NaN


@needs_analytics
def test_author_baseline_coverage_on_data(m7):
    """Baseline needs >=5 OTHER view-bearing posts: high-volume authors (Telegram, Twitter) keep it,
    the single-post authors that dominate Snapchat/TikTok/YouTube do not get a fake baseline."""
    import duckdb
    import lib.stats_v2 as S
    assert m7.PARAMS["channel_median_min_others"] == 5
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        d = con.execute("SELECT platform, author_uid, views FROM post_facts WHERE views > 0").fetchdf()
    finally:
        con.close()
    sc = {}
    for (pl, au), g in d.groupby(["platform", "author_uid"]):
        if len(g) > 5:
            sc[pl] = sc.get(pl, 0) + int(np.isfinite(S.leave_one_out_median(g.views.to_numpy(), 5)).sum())
    tot = d.groupby("platform").size()
    assert sc["telegram"] / tot["telegram"] > 0.95 and sc["twitter"] / tot["twitter"] > 0.95
    assert sc.get("tiktok", 0) / tot["tiktok"] < 0.5        # most TikTok authors have too little history


# --------------------------------------------------------------------------- 8. independent recomputation
def _independent_ratio(p):
    """Virality ratio re-implemented from the documented formulas (does not call 07's function)."""
    f = lambda c: p[c].fillna(0).astype(float)  # noqa: E731
    lk, cm, sh, vw, pl = f("likes"), f("comments"), f("shares"), f("views"), p.platform
    d = lk + cm + sh
    r = pd.Series(np.nan, index=p.index)

    def put(mask, val):
        r[mask] = val[mask]

    put(pl.isin(["telegram", "snapchat", "tiktok"]) & ((vw > 0) | (sh > 0)), sh / (vw + 1))
    t = pl == "twitter"
    put(t & (vw > 0), sh / (vw + 1))
    put(t & (vw <= 0) & (d > 0), sh / (d + 1))
    put(pl.isin(["truthsocial", "mastodon", "facebook", "facebook_profile"]) & (d > 0), sh / (d + 1))
    put((pl == "youtube_video") & ((vw > 0) | (lk > 0) | (cm > 0)), (lk + cm) / (vw + 1))
    put(pl.isin(["instagram", "linkedin", "baidu_tieba"]) & ((lk > 0) | (cm > 0)), cm / (lk + 1))
    return r


@needs_analytics
def test_virality_ratio_and_percentile_match_independent_recompute(m7, scored):
    from scipy import stats
    p = scored
    r = _independent_ratio(p)
    assert (r.isna() == p.r.isna()).all()
    both = r.notna() & p.r.notna()
    assert np.allclose(r[both], p.r[both], atol=1e-12)
    # independent percentile: (rank-0.5)/n inside platform(+twitter pools) x age band, band n>=20
    g = p.platform.copy()
    tv = (p.platform == "twitter") & (p.views.fillna(0) > 0)
    g[tv], g[(p.platform == "twitter") & ~tv] = "twitter_views", "twitter_noviews"
    band = np.digitize(np.where(np.isfinite(p.scrape_lag_days), p.scrape_lag_days, 0.0),
                       np.asarray(m7.PARAMS["age_bands"], float))
    ind = pd.Series(np.nan, index=p.index)
    for _, gi in p.groupby(g).groups.items():
        gi = np.asarray(list(gi))
        done = np.zeros(len(gi), bool)
        for b in np.unique(band[gi]):
            sub = gi[band[gi] == b]
            v = r.loc[sub].dropna()
            if len(v) >= m7.PARAMS["virality_min_band_n"]:
                ind.loc[v.index] = (stats.rankdata(v) - 0.5) / len(v)
                done[np.isin(gi, sub)] = True
        rest = gi[~done]
        v = r.loc[rest].dropna()
        if len(v) >= m7.PARAMS["virality_min_band_n"]:
            ind.loc[v.index] = (stats.rankdata(v) - 0.5) / len(v)
    assert (ind.isna() == p.pct.isna()).all()
    assert np.allclose(ind.dropna(), p.pct.dropna(), atol=1e-12)


@needs_analytics
def test_percentile_tracks_ratio_within_platform(scored):
    """Higher ratio must mean higher percentile (Spearman > 0.5) on every scored platform."""
    from scipy import stats
    for pl, x in scored.groupby("platform"):
        x = x.dropna(subset=["pct"])
        if len(x) > 50 and x.r.nunique() > 1:
            assert stats.spearmanr(x.r, x.pct)[0] > 0.5, pl


@needs_analytics
def test_cross_platform_stories_match_independent_recompute(stories19):
    """Re-derive the exact-text stories and their reach fields from post_facts with separate code."""
    import duckdb
    import hashlib
    st, po, _ = stories19
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        p = con.execute("SELECT post_uid, platform, author_uid, posted_at, text, coalesce(likes,0) l, "
                        "coalesce(comments,0) c, coalesce(shares,0) s, coalesce(views,0) v "
                        "FROM post_facts WHERE text IS NOT NULL").fetchdf()
        cov = con.execute("SELECT platform, avg((coalesce(views,0)>0)::int) c FROM post_facts GROUP BY 1"
                          ).fetchdf().set_index("platform").c
    finally:
        con.close()
    vp = set(cov[cov > 0].index)
    full = set(cov[cov >= 0.90].index)
    p = p[p.text.str.strip().str.len() >= 35].copy()
    p["ct"] = p.text.str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
    p = p[~p.ct.str.fullmatch(r"[0-9\s\.\,\:\-\_\#\@]+")]
    p["eng"] = p.l + p.c + p.s
    keep = p.groupby("ct").platform.nunique()
    q = p[p.ct.isin(keep[keep >= 2].index)].copy()
    q["sid"] = q.ct.map(lambda t: hashlib.md5(t.encode()).hexdigest())
    assert set(q.sid) == set(st.story_id)
    assert set(zip(q.sid, q.post_uid)) == set(zip(po.story_id, po.post_uid))
    s = st.set_index("story_id")
    for sid, x in q.groupby("sid"):
        obs = x.loc[x.platform.isin(vp) & (x.v > 0), "v"].sum()
        paired = x.loc[x.platform.isin(vp) & (x.v > 0), "eng"].sum()
        rep = x.sort_values(["eng", "posted_at"], ascending=[False, True]).drop_duplicates(["platform", "author_uid"])
        vless = rep.loc[~rep.platform.isin(full) & (rep.v == 0), "eng"].sum()
        ratio = min(max(round(obs / paired, 2), 5.0), 80.0) if (paired > 0 and obs > 0) else 20.0
        uni = round(obs + vless * ratio)
        br = round(1 + 0.5 * (x.platform.nunique() - 1), 2)
        row = s.loc[sid]
        assert row.n_posts == len(x) and row.n_unique_posts == len(rep)
        assert abs(row.observed_views - obs) <= 1 and abs(row.story_ratio - ratio) <= 0.01
        assert abs(row.unified_reach - uni) <= 1 and abs(row.effective_reach - round(uni * br)) <= 1


@needs_analytics
def test_url_links_really_contain_the_link(url_tier):
    import duckdb
    links, posts = url_tier
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        txt = con.execute("SELECT post_uid, lower(text) t FROM post_facts").fetchdf().set_index("post_uid").t
    finally:
        con.close()
    for r in links.sample(min(40, len(links)), random_state=0).itertuples():
        key = r.url.split("?")[0]
        for uid in posts.loc[posts.link_id == r.link_id, "post_uid"]:
            assert key in txt.loc[uid].replace("://www.", "://"), (r.url, uid)


def test_reach_formulas_documented_in_code(m7):
    """observed_views / engagement_proxy / reach_proxy keep their documented definitions."""
    base = pd.DataFrame({
        "tone_sum": [0.0], "tone_n": [1], "hate_sum": [0.0], "hate_n": [1], "hate_hits": [0.0],
        "read_sum": [0.0], "read_n": [1], "read_cov": [1.0], "vir_sum": [0.5], "vir_n": [1],
        "reach_norm_sum": [2.0], "reach_norm_n": [1],
        "views_sum": [50.0], "E_sum": [58.0], "eng_sum": [8.0]})
    o = m7._finish_cells(base)
    assert o.observed_views[0] == 50.0
    assert o.engagement_proxy[0] == pytest.approx(np.log1p(8.0))   # likes+comments+shares only
    assert o.reach_proxy[0] == 50.0                                  # legacy: views when present
    assert o.reach_audience_norm[0] == pytest.approx(2.0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
