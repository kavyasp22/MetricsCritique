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
          count(*) FILTER (WHERE observed_views < 0 OR viewless_engagement < 0),
          count(*) FILTER (WHERE spread_duration_hours < 0)
        FROM cross_platform_stories""").fetchone()
    assert bad == (0,) * 4, f"invariant violations (<2 plat, short, negative counts, lag): {bad}"
    cols = {r[0] for r in mdb.execute("DESCRIBE cross_platform_stories").fetchall()}
    # imputed views and the breadth multiplier were removed (V1/V2): they must not come back unnoticed
    assert not cols & {"imputed_views", "story_ratio", "ratio_source", "unified_reach", "breadth_multiplier", "effective_reach"}


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
    assert (st.viewless_engagement >= 0).all()
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
    assert set(links.match_confidence) <= {"high", "medium", "low"}
    assert (links.loc[links.match_confidence == "medium", "host"].isin(m19.URL_SHORTENERS)).all()
    assert (links.loc[links.match_confidence != "medium", "host"].isin(m19.URL_SHORTENERS) == False).all()  # noqa: E712
    # 'low' = a non-shortened link whose posts span more than URL_MAX_SPREAD_DAYS; 'high' must be within it
    assert (links.loc[links.match_confidence == "high", "spread_duration_hours"] <= m19.URL_MAX_SPREAD_DAYS * 24).all()
    assert (links.loc[links.match_confidence == "low", "spread_duration_hours"] > m19.URL_MAX_SPREAD_DAYS * 24).all()
    assert not links.host.isin(["timesofindia.sng.link", "sng.link", "onelink.me"]).any()   # app deep links are denied
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
        rep = x.sort_values(["eng", "posted_at"], ascending=[False, True]).drop_duplicates(["platform", "author_uid"])
        vless = rep.loc[~rep.platform.isin(full) & (rep.v == 0), "eng"].sum()
        row = s.loc[sid]
        assert row.n_posts == len(x) and row.n_unique_posts == len(rep)
        assert abs(row.observed_views - obs) <= 1 and abs(row.viewless_engagement - vless) <= 1


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


# --------------------------------------------------------------------------- 9. semantic tier (script 20)
@pytest.fixture(scope="module")
def m20():
    spec = importlib.util.spec_from_file_location("m20", ROOT / "scripts" / "20_semantic_cross_platform.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_metrics
def test_semantic_tables_invariants(mdb, m20):
    ev = mdb.execute("SELECT * FROM cross_platform_semantic_events").fetchdf()
    po = mdb.execute("SELECT * FROM cross_platform_semantic_posts").fetchdf()
    assert len(ev) > 0 and ev.event_id.is_unique
    assert (ev.n_platforms >= 2).all() and (ev.n_posts >= 2).all()
    assert (ev.n_unique_posts <= ev.n_posts).all() and (ev.n_unique_posts >= ev.n_platforms).all()
    assert (ev.min_sim >= m20.SIM_THRESHOLD - 1e-6).all() and (ev.min_sim <= ev.mean_sim + 1e-9).all()
    assert (ev.loc[ev.match_confidence == "high", "min_sim"] >= m20.SIM_HIGH - 1e-6).all()
    # min_sim is stored rounded to 3 decimals; confidence was decided on the unrounded value
    assert (ev.loc[ev.match_confidence == "medium", "min_sim"] <= m20.SIM_HIGH + 0.0006).all()
    assert set(ev.match_confidence) <= {"high", "medium"}
    assert (ev.spread_duration_hours >= 0).all() and (ev.observed_views >= 0).all()
    assert set(po.event_id) == set(ev.event_id)
    n = po.groupby("event_id").size().reindex(ev.event_id).to_numpy()
    assert (n == ev.n_posts.to_numpy()).all()
    npl = po.groupby("event_id").platform.nunique().reindex(ev.event_id).to_numpy()
    assert (npl == ev.n_platforms.to_numpy()).all()


@needs_metrics
@needs_analytics
def test_semantic_events_really_connected_by_embedding_similarity(mdb, m20):
    """Recompute cosine similarity from the stored embeddings for a random sample of events: every
    event must be connected by cross-platform, non-identical, in-window pairs >= the threshold."""
    import duckdb
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ev = mdb.execute("SELECT event_id, min_sim FROM cross_platform_semantic_events").fetchdf().sample(25, random_state=0)
    po = mdb.execute("SELECT * FROM cross_platform_semantic_posts WHERE event_id IN (SELECT unnest(?))",
                     [ev.event_id.tolist()]).fetchdf()
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        con.register("want", po[["post_uid"]])
        txt = con.execute("SELECT p.post_uid, p.platform, p.posted_at, p.text FROM post_facts p "
                          "JOIN want USING (post_uid)").fetchdf().set_index("post_uid")
        emb = con.execute("SELECT e.post_uid, e.embedding FROM post_embeddings e JOIN want USING (post_uid)").fetchdf()
    finally:
        con.close()
    vec = {r.post_uid: np.asarray(r.embedding, dtype=np.float32) for r in emb.itertuples()}
    for eid, x in po.groupby("event_id"):
        uids = [u for u in x.post_uid if u in vec]
        assert len(uids) >= 2
        ct = {u: " ".join(str(txt.loc[u, "text"]).lower().split()) for u in uids}
        M = np.stack([vec[u] / np.linalg.norm(vec[u]) for u in uids])
        S = M @ M.T
        pl = np.array([txt.loc[u, "platform"] for u in uids]); ts = np.array([txt.loc[u, "posted_at"] for u in uids])
        n = len(uids)
        ok = np.zeros((n, n), bool)
        for i in range(n):
            for j in range(i + 1, n):
                if (S[i, j] >= m20.SIM_THRESHOLD - 1e-4 and pl[i] != pl[j] and ct[uids[i]] != ct[uids[j]]
                        and abs((ts[i] - ts[j]).total_seconds()) <= (m20.WINDOW_DAYS + 1) * 86400):
                    ok[i, j] = True
        # posts sharing (platform, text) belong together: join them to the same node
        key = {u: (pl[i], ct[u]) for i, u in enumerate(uids)}
        for i in range(n):
            for j in range(i + 1, n):
                if key[uids[i]] == key[uids[j]]:
                    ok[i, j] = True
        k, _ = connected_components(coo_matrix(ok | ok.T), directed=False)
        assert k == 1, f"event {eid} is not connected by embedding similarity >= {m20.SIM_THRESHOLD}"


@needs_analytics
def test_semantic_tier_excludes_identical_text_and_same_platform(m20):
    """The tier must add evidence beyond the exact-text tier: no edge joins identical cleaned texts or
    two posts of the same platform."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        ev, po = m20.compute_semantic_events(verbose=False)
    assert len(ev) > 0 and (ev.n_platforms >= 2).all()
    assert ev.same_account_name.mean() < 0.2        # mostly different accounts (unlike the exact-text tier)
    assert ev.large_event.sum() < 0.05 * len(ev)


# --------------------------------------------------------------------------- 10. tone mix, weighted tone, drift
def _tone_posts():
    return pd.DataFrame({
        "post_uid": list("abcdef"), "tone_v": [0.5, -0.4, 0.05, np.nan, -0.9, 0.3],
        "views": [100.0, 0.0, np.nan, 5.0, 0.0, np.nan], "likes": [5.0, 1.0, np.nan, 0.0, 0.0, np.nan],
        "comments": [1.0, 0.0, np.nan, 0.0, 0.0, np.nan], "shares": [0.0, 0.0, np.nan, 0.0, 0.0, np.nan],
        "has_engagement": [True, True, True, True, True, False]})


def test_tone_weight_floor_and_monotone(m7):
    w = m7.tone_weight(pd.Series([0.0, np.nan, 100.0]), pd.Series([0.0, np.nan, 5.0]),
                       pd.Series([0.0, np.nan, 1.0]), pd.Series([0.0, np.nan, 0.0]))
    assert w.iloc[0] == 1.0 and w.iloc[1] == 1.0                      # never 0, missing counters -> 1
    assert w.iloc[2] == pytest.approx(1 + np.log1p(106)) and w.iloc[2] > w.iloc[0]


def test_tone_extras_classes_and_nan_for_unscored(m7):
    p = m7.add_tone_extras(_tone_posts(), 0.15)
    assert p.tone_pos.tolist()[:3] == [1.0, 0.0, 0.0] and p.tone_neg.tolist()[:3] == [0.0, 1.0, 0.0]
    assert np.isnan(p.tone_pos[3]) and np.isnan(p.tone_neg[3]) and np.isnan(p.tone_wd[3])   # unscored post
    # tone_wk = scored AND has a counter: post c has no counter values, post f is on a platform without engagement
    assert p.tone_wk.tolist()[:3] == [1.0, 1.0, 0.0] and p.tone_wk[4] == 1.0 and p.tone_wk[5] == 0.0


def test_cell_aggregation_matches_independent_calc(m7):
    p = m7.add_tone_extras(_tone_posts(), 0.15)
    for c in ("hate_v", "read_v", "virality_pct_post", "reach_audience_norm_post", "confidence", "read_ok"):
        p[c] = 0.5
    p["E_sum"], p["eng_sum"], p["author_uid"], p["platform"], p["day"] = 1.0, 1.0, "a", "x", 1
    p["text_norm"] = [f"text {i}" for i in range(len(p))]
    base = m7._aggregate_cells_hard(p, ["platform", "day"])
    with_tone = p[p.tone_v.notna()]
    w = m7.tone_weight(with_tone.views, with_tone.likes, with_tone.comments, with_tone.shares)
    r = base.iloc[0]
    assert r.tone_n == 5 and r.tone_pos_n == 2 and r.tone_neg_n == 2
    assert r.tone_pos_share == pytest.approx(2 / 5) and r.tone_neu_share == pytest.approx(1 / 5)
    assert r.net_sentiment_score == pytest.approx(0.0)
    assert r.tone_weighted == pytest.approx((with_tone.tone_v * w).sum() / w.sum())
    assert r.tone_weight_cov == pytest.approx(3 / 5)          # 3 of the 5 scored posts carry a counter
    # below tone_min_n -> NULL, never 0
    small = m7._aggregate_cells_hard(p.iloc[:3], ["platform", "day"]).iloc[0]
    assert np.isnan(small.net_sentiment_score) and np.isnan(small.tone_weighted)
    assert small.tone_n == 3


def _drift_panel(spike=True):
    days = pd.date_range("2026-07-01", periods=30)
    rows = []
    for plat in ("telegram", "twitter"):
        rng = np.random.default_rng(1 if plat == "telegram" else 2)
        for i, d in enumerate(days):
            tone = 0.1 + rng.normal(0, 0.02)
            if spike and plat == "telegram" and i == 25:
                tone = -0.6
            rows.append(dict(topic_id="t001", platform=plat, day=d.date(), in_window=True, tone_mean=tone, tone_n=20.0))
    return pd.DataFrame(rows)


def test_tone_drift_flags_a_sudden_drop_only_in_its_own_series(m7):
    out = m7.add_tone_drift(_drift_panel(), m7.PARAMS)
    flagged = out[out.tone_drift_flag]
    assert len(flagged) == 1 and flagged.iloc[0].platform == "telegram" and str(flagged.iloc[0].day) == "2026-07-26"
    assert flagged.iloc[0].tone_drift_z < -5 and flagged.iloc[0].tone_drift_q < m7.PARAMS["fdr_alpha"]
    assert out.loc[out.platform == "twitter", "tone_drift_flag"].sum() == 0        # platforms are never pooled
    assert out.tone_drift_z.iloc[:7].isna().all()                                  # < min_base prior days
    assert not m7.add_tone_drift(_drift_panel(spike=False), m7.PARAMS).tone_drift_flag.any()


def test_tone_drift_ignores_out_of_window_and_low_n(m7):
    p = _drift_panel()
    p.loc[p.platform == "telegram", "in_window"] = False
    assert not m7.add_tone_drift(p, m7.PARAMS).tone_drift_flag.any()
    q = _drift_panel()
    q.loc[(q.platform == "telegram") & (q.day.astype(str) == "2026-07-26"), "tone_n"] = 2.0   # too few posts that day
    assert not m7.add_tone_drift(q, m7.PARAMS).tone_drift_flag.any()


@needs_analytics
def test_tone_weight_and_drift_calibration_on_real_data(m7):
    """Real data: weights are >= 1 and never missing; the drift p-values are near nominal (p<0.05 on ~5% of days)."""
    import duckdb
    import lib.stats_v2 as S
    con = duckdb.connect(str(ANALYTICS), read_only=True)
    try:
        c = con.execute("SELECT f.views, f.likes, f.comments, f.shares FROM post_facts f").fetchdf()
        d = con.execute("""SELECT pt.topic_id, f.platform, CAST(date_trunc('day', f.posted_at) AS DATE) AS dy, s.tone_score t
                           FROM post_scores s JOIN post_facts f USING (post_uid) JOIN post_topic pt USING (post_uid)
                           WHERE s.tone_valid AND f.posted_at >= '2026-06-14' AND pt.topic_id <> 'unassigned'""").fetchdf()
    finally:
        con.close()
    w = m7.tone_weight(c.views, c.likes, c.comments, c.shares)
    assert w.notna().all() and (w >= 1.0).all()
    g = d.groupby(["topic_id", "platform", "dy"]).t.agg(["mean", "size"]).reset_index()
    ps = []
    for _, x in g.groupby(["topic_id", "platform"]):
        x = x.set_index("dy").sort_index().reindex(pd.date_range(x.dy.min(), x.dy.max()))
        z, p, _b = S.prior_window_drift(x["mean"].to_numpy(), x["size"].fillna(0).to_numpy(),
                                        window=14, min_base=7, min_n=5, scale_floor=0.05)
        ps.append(p[np.isfinite(p)])
    p = np.concatenate(ps)
    assert len(p) > 1000
    assert 0.03 < (p < 0.05).mean() < 0.075 and 0.07 < (p < 0.10).mean() < 0.14


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
