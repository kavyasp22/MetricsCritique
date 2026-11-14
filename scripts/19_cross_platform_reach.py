#!/usr/bin/env python3
"""Compute Improvised Cross-Platform Reach & Narrative Diffusion.

Implements Section 9 of the Virality & Reach Implementation Brief:
1. Cross-Platform Story Reach: OBSERVED views only (counter values summed over view-bearing platforms).
   The earlier view imputation (clamp 5-80) and breadth multiplier were removed: imputed views were 42% of reported
   reach and rested on a fixed clamp the data contradict; every story has exactly 2 platforms, so the multiplier was constant.
   `viewless_engagement` is reported as a measured number of interactions on posts that have no view counter.
4. Updates metrics.duckdb (cross_platform_stories, cross_platform_posts)
5. Updates UI/data/metrics.json and UI/data/metrics.json.gz
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
UI_DATA = ROOT / "UI" / "data"

ANALYTICS_DB = DATA / "analytics.duckdb"
METRICS_DB = DATA / "metrics.duckdb"
METRICS_JSON = UI_DATA / "metrics.json"
METRICS_GZ = UI_DATA / "metrics.json.gz"


VIEW_COVERAGE = 0.90  # share of posts with views>0 for a platform to count as a full view counter


def compute_cross_platform_reach(limit_stories: int = 150):
    print("=" * 72)
    print("Computing Improvised Cross-Platform Reach & Narrative Clusters")
    print(f"Reading from: {ANALYTICS_DB}")
    print("=" * 72)

    con = duckdb.connect(str(ANALYTICS_DB), read_only=True)

    # Which platforms report a view counter is derived from the data, never hard-coded
    # (platform names must match post_facts.platform, e.g. 'youtube_video', not 'youtube').
    #   view_plats : any post has views > 0            -> views>0 posts are OBSERVED reach
    #   full_plats : >= VIEW_COVERAGE of posts have views>0 (a real counter on every post);
    #                a 0/NULL views post there is a genuine zero, not "no counter".
    # Viewless = no counter at all, or partial-coverage counter (e.g. Twitter) with 0/NULL.
    cov = con.execute(
        "SELECT platform, avg((coalesce(views, 0) > 0)::int) AS cov FROM post_facts GROUP BY 1"
    ).fetchall()
    view_plats = sorted(p for p, c in cov if c and c > 0)
    full_plats = sorted(p for p, c in cov if c is not None and c >= VIEW_COVERAGE)
    q_list = lambda xs: ", ".join("'" + x.replace("'", "''") + "'" for x in xs) or "''"
    VP, FP = q_list(view_plats), q_list(full_plats)
    print(f"-> view-bearing platforms: {view_plats}")
    print(f"-> full view-counter platforms (no imputation for 0 views): {full_plats}")

    query = f"""
    WITH base AS (
        SELECT 
            post_uid,
            platform,
            author_uid,
            author_name,
            posted_at,
            scrape_lag_days,
            url,
            coalesce(likes, 0) AS likes,
            coalesce(comments, 0) AS comments,
            coalesce(shares, 0) AS shares,
            coalesce(views, 0) AS views,
            (coalesce(likes, 0) + coalesce(comments, 0) + coalesce(shares, 0)) AS engagement,
            trim(regexp_replace(lower(text), '\\s+', ' ', 'g')) AS clean_text,
            left(text, 220) AS raw_snippet
        FROM post_facts
        WHERE text IS NOT NULL AND length(trim(text)) >= 35
    ),
    cleaned AS (
        -- rep = 1 marks ONE representative post per (story text, platform, author): the highest-
        -- engagement one. Imputation uses only these so an account re-posting the same text 100+
        -- times is not counted as 100+ independent view-less posts.
        SELECT *,
            row_number() OVER (PARTITION BY clean_text, platform, author_uid
                               ORDER BY engagement DESC, posted_at) AS rep
        FROM base
    ),
    clusters AS (
        SELECT 
            clean_text,
            any_value(raw_snippet) AS snippet,
            count(DISTINCT platform) AS n_platforms,
            count(*) AS n_posts,
            count(*) FILTER (WHERE rep = 1) AS n_unique_posts,
            count(DISTINCT author_uid) AS n_authors,
            -- 1 when every post in the story has the same account name (ignoring case/punctuation):
            -- an account re-publishing its own text on several platforms, i.e. cross-posting, not spread
            count(DISTINCT regexp_replace(lower(coalesce(author_name, '')), '[^a-z0-9]', '', 'g')) AS n_account_names,
            min(posted_at) AS origin_time,
            max(posted_at) AS latest_time,
            arg_min(platform, posted_at) AS origin_platform,
            arg_min(scrape_lag_days, posted_at) AS origin_scrape_lag_days,
            list(DISTINCT platform) AS platforms,
            sum(engagement) AS total_engagement,
            sum(views) AS total_views,
            sum(CASE WHEN platform IN ({VP}) AND views > 0 THEN views ELSE 0 END) AS observed_views,
            sum(CASE WHEN rep = 1 AND platform NOT IN ({FP}) AND (views = 0 OR views IS NULL) THEN engagement ELSE 0 END) AS viewless_engagement
        FROM cleaned
        WHERE clean_text NOT SIMILAR TO '^[0-9\\s\\.\\,\\:\\-\\_\\#\\@]+$'
        GROUP BY clean_text
        HAVING count(DISTINCT platform) >= 2
    ),
    calculated AS (
        SELECT 
            *,
            round(epoch(latest_time - origin_time) / 3600.0, 1) AS spread_duration_hours,
            md5(clean_text) AS story_id
        FROM clusters
    ),
    improvised AS (
        SELECT 
            story_id,
            clean_text,
            snippet,
            n_platforms,
            n_posts,
            n_unique_posts,
            n_authors,
            (n_authors < 2) AS single_source,
            (n_account_names = 1) AS same_account_name,
            origin_platform,
            round(origin_scrape_lag_days, 1) AS origin_scrape_lag_days,
            origin_time::varchar AS origin_time,
            spread_duration_hours,
            platforms,
            total_engagement,
            total_views AS raw_views,
            observed_views,
            viewless_engagement
        FROM calculated
        ORDER BY n_platforms DESC, observed_views DESC, total_engagement DESC, n_posts DESC
        LIMIT {limit_stories}
    )
    SELECT * FROM improvised
    """

    stories_df = con.execute(query).fetchdf()
    print(f"-> Extracted {len(stories_df)} multi-platform stories with improvised reach.")

    # Extract matching posts
    top_story_hashes = [r["clean_text"] for r in stories_df[["clean_text"]].to_dict(orient="records")]
    con.register("top_stories", stories_df[["story_id", "clean_text"]])

    posts_query = """
    WITH cleaned AS (
        SELECT 
            post_uid,
            platform,
            posted_at,
            trim(regexp_replace(lower(text), '\\s+', ' ', 'g')) AS clean_text
        FROM post_facts
        WHERE text IS NOT NULL AND length(trim(text)) >= 35
    )
    SELECT 
        s.story_id,
        c.post_uid,
        c.platform,
        c.posted_at::varchar AS posted_at
    FROM cleaned c
    JOIN top_stories s ON s.clean_text = c.clean_text
    ORDER BY s.story_id, c.posted_at ASC
    """
    posts_df = con.execute(posts_query).fetchdf()
    print(f"-> Extracted {len(posts_df)} individual posts across narrative clusters.")
    con.close()

    # Drop clean_text before saving to public tables
    stories_df = stories_df.drop(columns=["clean_text"])

    # Convert platforms array to list of strings
    stories_df["platforms"] = stories_df["platforms"].apply(lambda p: list(p) if hasattr(p, "__iter__") else [str(p)])

    return stories_df, posts_df


# --------------------------------------------------------------------------- URL tier
# Second, separate matching tier: posts on >= 2 platforms that link to the SAME article URL.
# Kept in its own tables (cross_platform_url_links / cross_platform_url_posts) and NOT merged
# into the exact-text stories, so the exact-text results are unchanged. No view imputation here:
# only observed counters. Precision of this tier is judged on a labelled sample before use.
URL_RE = r'https?://[^\s)\]>"]+'
# params that only track the click source, never the page
_TRACKING = ("utm_", "at_", "fbclid", "gclid", "igshid", "mc_", "ref_", "ref=", "source=", "s=", "t=")
# hosts whose links are boilerplate / profile / payment / form links, not a shared story
URL_DENY_HOSTS = {"creativecommons.org", "t.me", "wa.me", "chat.whatsapp.com", "whatsapp.com",
                  "forms.gle", "rzp.io", "paypal.com", "paypal.me", "facebook.com", "instagram.com",
                  "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be", "tiktok.com",
                  "play.google.com", "apps.apple.com"}
URL_SHORTENERS = {"tinyurl.com", "bit.ly", "lnkd.in", "is.gd", "t.co", "ow.ly", "goo.gl", "buff.ly",
                  "rb.gy", "cutt.ly", "shorturl.at"}
URL_MIN_PATH = 12  # shorter paths are usually a site home page, not an article


def normalise_url(u: str):
    """Lower-case, drop fragment, trailing punctuation and click-tracking params. Returns (url, host, path)."""
    import re
    u = re.sub(r"#.*$", "", u.strip().lower()).rstrip("/.,;:!?'\"")
    m = re.match(r"^https?://(?:www\.)?([^/?]+)([^?]*)(?:\?(.*))?$", u)
    if not m:
        return None, None, None
    host, path, query = m.group(1), m.group(2).rstrip("/"), m.group(3) or ""
    keep = [q for q in query.split("&") if q and not q.startswith(_TRACKING)]
    full = f"{host}{path}" + (("?" + "&".join(sorted(keep))) if keep else "")
    return full, host, path + (("?" + "&".join(keep)) if keep else "")


def compute_url_links():
    """Cross-platform links by shared article URL. Returns (links_df, posts_df)."""
    import re  # noqa: F401
    con = duckdb.connect(str(ANALYTICS_DB), read_only=True)
    raw = con.execute(f"""
        SELECT post_uid, platform, author_uid, posted_at, scrape_lag_days,
               coalesce(views, 0) AS views,
               coalesce(likes, 0) + coalesce(comments, 0) + coalesce(shares, 0) AS engagement,
               unnest(regexp_extract_all(text, '{URL_RE}')) AS url
        FROM post_facts WHERE text IS NOT NULL""").fetchdf()
    vp = [r[0] for r in con.execute(
        "SELECT platform FROM post_facts GROUP BY 1 HAVING max(coalesce(views,0)) > 0").fetchall()]
    con.close()
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()
    norm = raw["url"].map(normalise_url)
    raw["key"] = [n[0] for n in norm]
    raw["host"] = [n[1] for n in norm]
    raw["path"] = [n[2] for n in norm]
    raw = raw.dropna(subset=["key"])
    # keep: article-like links (path long enough) and shortened links; drop deny-listed hosts
    raw = raw[~raw.host.isin(URL_DENY_HOSTS)
              & ((raw.path.str.len() >= URL_MIN_PATH) | raw.host.isin(URL_SHORTENERS))]
    # one row per (post, url) even when a post repeats a link
    raw = raw.drop_duplicates(subset=["post_uid", "key"])
    g = raw.groupby("key")
    keep = g.platform.nunique()
    keep = keep[keep >= 2].index
    raw = raw[raw.key.isin(keep)].copy()
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()
    raw = raw.sort_values("posted_at")
    import hashlib
    rows = []
    for key, x in raw.groupby("key", sort=False):
        first = x.iloc[0]
        obs = x.loc[x.platform.isin(vp) & (x.views > 0), "views"].sum()
        rows.append(dict(
            link_id=hashlib.md5(key.encode()).hexdigest(),
            url=key, host=first.host,
            match_confidence="medium" if first.host in URL_SHORTENERS else "high",
            n_platforms=int(x.platform.nunique()), n_posts=int(len(x)),
            # one post per (platform, author): an account re-posting the same link 100+ times counts once
            n_unique_posts=int(x.groupby(["platform", "author_uid"]).ngroups),
            n_authors=int(x.author_uid.nunique()),
            platforms=sorted(x.platform.unique().tolist()),
            origin_platform=first.platform,
            origin_scrape_lag_days=None if pd.isna(first.scrape_lag_days) else round(float(first.scrape_lag_days), 1),
            origin_time=str(first.posted_at),
            spread_duration_hours=round((x.posted_at.max() - x.posted_at.min()).total_seconds() / 3600.0, 1),
            observed_views=float(obs), total_engagement=float(x.engagement.sum())))
    links = pd.DataFrame(rows).sort_values(["n_platforms", "n_posts"], ascending=False).reset_index(drop=True)
    posts = raw.merge(links[["link_id", "url"]], left_on="key", right_on="url")[
        ["link_id", "post_uid", "platform", "posted_at"]].copy()
    posts["posted_at"] = posts["posted_at"].astype(str)
    print(f"-> URL tier: {len(links)} shared-URL links ({(links.match_confidence=='high').sum()} high, "
          f"{(links.match_confidence=='medium').sum()} medium), {len(posts)} posts.")
    return links, posts


def save_to_duckdb(stories_df: pd.DataFrame, posts_df: pd.DataFrame, url_links=None, url_posts=None):
    print(f"Writing to: {METRICS_DB}")
    mcon = duckdb.connect(str(METRICS_DB), read_only=False)
    mcon.execute("DROP TABLE IF EXISTS cross_platform_stories")
    mcon.execute("CREATE TABLE cross_platform_stories AS SELECT * FROM stories_df")
    mcon.execute("DROP TABLE IF EXISTS cross_platform_posts")
    mcon.execute("CREATE TABLE cross_platform_posts AS SELECT * FROM posts_df")
    mcon.execute("CREATE INDEX IF NOT EXISTS idx_cp_posts_story ON cross_platform_posts(story_id)")
    mcon.execute("CREATE INDEX IF NOT EXISTS idx_cp_posts_uid ON cross_platform_posts(post_uid)")
    if url_links is not None:
        mcon.execute("DROP TABLE IF EXISTS cross_platform_url_links")
        mcon.execute("DROP TABLE IF EXISTS cross_platform_url_posts")
        if len(url_links):
            mcon.execute("CREATE TABLE cross_platform_url_links AS SELECT * FROM url_links")
            mcon.execute("CREATE TABLE cross_platform_url_posts AS SELECT * FROM url_posts")
    n_stories = mcon.execute("SELECT count(*) FROM cross_platform_stories").fetchone()[0]
    n_posts = mcon.execute("SELECT count(*) FROM cross_platform_posts").fetchone()[0]
    mcon.close()
    print(f"-> Successfully saved {n_stories} stories and {n_posts} posts into metrics.duckdb.")


def update_metrics_json(stories_df: pd.DataFrame):
    if not METRICS_JSON.exists():
        print(f"Warning: {METRICS_JSON} not found. Skipping JSON update.")
        return

    print(f"Updating: {METRICS_JSON}")
    with open(METRICS_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # Convert dataframe to columnar format matching metrics.json
    cols = list(stories_df.columns)
    rows = []
    for tup in stories_df.itertuples(index=False, name=None):
        row_vals = []
        for v in tup:
            if hasattr(v, "tolist"):
                row_vals.append(v.tolist())
            elif isinstance(v, (list, tuple)):
                row_vals.append(list(v))
            elif isinstance(v, float) and (pd.isna(v) or not v == v):
                row_vals.append(None)
            elif isinstance(v, int):
                row_vals.append(int(v))
            elif isinstance(v, float):
                row_vals.append(round(v, 2))
            else:
                row_vals.append(v)
        rows.append(row_vals)

    payload["tables"]["cross_platform_stories"] = {
        "columns": cols,
        "rows": rows,
        "n": len(rows),
        "table": "cross_platform_stories"
    }

    raw_json = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        f.write(raw_json)
    print(f"-> Saved updated metrics.json ({len(raw_json) / (1024*1024):.2f} MB)")

    # Build gzip
    print(f"Compressing to: {METRICS_GZ}")
    gz_bytes = gzip.compress(raw_json.encode("utf-8"), compresslevel=6)
    with open(METRICS_GZ, "wb") as f:
        f.write(gz_bytes)
    print(f"-> Saved metrics.json.gz ({len(gz_bytes) / (1024*1024):.2f} MB)")


def main():
    stories_df, posts_df = compute_cross_platform_reach()
    url_links, url_posts = compute_url_links()
    save_to_duckdb(stories_df, posts_df, url_links, url_posts)
    update_metrics_json(stories_df)
    print("=" * 72)
    print("Improvised Reach Computation & Publication Complete!")
    print("=" * 72)


if __name__ == "__main__":
    main()

