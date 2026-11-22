#!/usr/bin/env python3
"""
Audience sentiment, Phase 5 (Snapchat), step 1: READ-ONLY extraction of comments from snapchat_posts.

Reads production Postgres through lib.pg.read_only_snapshot (REPEATABLE READ, read-only, SELECT only, always ROLLBACK).
No scraping. Comments are stored as a JSONB array in the `top_comments` column of snapchat_posts; each element has
at minimum `text`, `author`, `likes`, `published_at` keys. The Snapchat Spotlight scraper collects at most 20 comments
per post.

Writes under data/audience/ (git-ignored):
    snap_comments_extract.parquet  one row per distinct (spotlight_url, comment_id); comment_id is sha1 of content
    snap_posts_extract.parquet     one row per distinct post: post_uid, post_url, comment count, scraped_at
    extract_meta_snap.json         production counts and reconciliation

Key facts:
  - snapchat_posts has ~44 duplicate spotlight_url rows; we keep the row with the most comments for each URL.
  - Snapchat comments have no native ID → sha1_hex(spotlight_url, author, text, published_at) for stability.
  - Snapchat is comment-only: reply_count field present in comment JSON but replies array is never collected.
  - Only the first 20 comments per Spotlight video are collected (Snapchat Spotlight scraper limit).
  - Posts ARE in the analytics store (post_facts table, platform='snapchat'); post_uid from post_facts.
  - For posts not in post_facts, generate synthetic: 'snap_' + sha1(spotlight_url)[:16].
  - Filter for posts with comments: jsonb_typeof(top_comments) = 'array' AND jsonb_array_length(top_comments) > 0
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.audience_text import sha1_hex, triage
from lib.config import DATA_DIR, DUCKDB_PATH
from lib.pg import q1, read_only_snapshot

OUT_DIR = DATA_DIR / "audience"
OUT_DIR.mkdir(parents=True, exist_ok=True)

NE = "jsonb_typeof(top_comments) = 'array' AND jsonb_array_length(top_comments) > 0"


def _to_ts(v):
    """Convert a value to UTC Timestamp safely."""
    if v is None:
        return pd.NaT
    try:
        ts = pd.Timestamp(v)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    except Exception:
        return pd.NaT


def fetch_post_facts_lookup() -> dict:
    """Read native_id → (post_uid, posted_at) from DuckDB analytics store for Snapchat posts."""
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        df = a.execute(
            "SELECT native_id, post_uid, posted_at FROM post_facts WHERE platform = 'snapchat'"
        ).df()
        print(f"  post_facts snapchat rows (DuckDB): {len(df)}", flush=True)
        return df.set_index("native_id")[["post_uid", "posted_at"]].to_dict("index")
    finally:
        a.close()


def fetch():
    with read_only_snapshot() as (conn, cur, snap_ts):
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone()[0] != "on":
            raise RuntimeError("production connection is not read-only; refusing to continue")

        prod = {}
        c = q1(cur,
            f"SELECT count(*), count(DISTINCT spotlight_url), "
            f"count(*) FILTER (WHERE {NE}), count(DISTINCT spotlight_url) FILTER (WHERE {NE}) "
            f"FROM snapchat_posts")
        prod.update(rows=int(c[0]), distinct_urls=int(c[1]),
                    rows_with_comments=int(c[2]), distinct_urls_with_comments=int(c[3]))
        print(f"snapshot {snap_ts.isoformat()}: {prod}", flush=True)

        # Fetch posts with comments; deduplicate below by keeping row with most comments per URL
        cur.execute(
            f"SELECT id::text AS native_id, spotlight_url, top_comments, "
            f"scraped_at_utc, posted_at "
            f"FROM snapchat_posts "
            f"WHERE {NE} "
            f"ORDER BY spotlight_url, jsonb_array_length(top_comments) DESC"
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        print(f"  fetched {len(rows)} rows with comments", flush=True)

        return snap_ts, prod, rows, colnames


def extract(snap_ts, prod, rows, colnames, pf_lookup):
    df_raw = pd.DataFrame(rows, columns=colnames)

    # Deduplicate: keep one row per spotlight_url (most comments first due to ORDER BY)
    df_posts = df_raw.drop_duplicates(subset=["spotlight_url"], keep="first").copy()
    df_posts = df_posts.reset_index(drop=True)
    n_source_map = df_raw.groupby("spotlight_url").size().to_dict()
    df_posts["n_source_rows"] = df_posts["spotlight_url"].map(n_source_map).fillna(1).astype(int)

    print(f"  post_facts lookup entries: {len(pf_lookup)}", flush=True)

    # Assign post_uid: from post_facts where available, else synthetic
    def get_post_uid(native_id, spotlight_url):
        if native_id in pf_lookup:
            return pf_lookup[native_id]["post_uid"], True
        return "snap_" + sha1_hex(str(spotlight_url))[:16], False

    post_uids = []
    post_in_stores = []
    for _, row in df_posts.iterrows():
        uid, in_store = get_post_uid(row["native_id"], row["spotlight_url"])
        post_uids.append(uid)
        post_in_stores.append(in_store)

    df_posts["post_uid"] = post_uids
    df_posts["post_in_store"] = post_in_stores

    # Fill posted_at from post_facts where available
    def get_pf_posted_at(native_id):
        if native_id in pf_lookup:
            return pf_lookup[native_id].get("posted_at")
        return None

    df_posts["posted_at_pf"] = df_posts["native_id"].apply(get_pf_posted_at)

    comment_rows = []
    for _, row in df_posts.iterrows():
        post_uid = row["post_uid"]
        spotlight_url = row["spotlight_url"]
        native_id = row["native_id"]
        scraped_at = row.get("scraped_at_utc")
        post_posted_at = row.get("posted_at_pf") or row.get("posted_at")
        comments = row["top_comments"]

        if not isinstance(comments, list):
            continue

        for elem in comments:
            if not isinstance(elem, dict):
                continue
            raw_text = elem.get("text") or ""
            raw_author = elem.get("author") or ""
            raw_published_at = elem.get("published_at") or ""
            raw_likes = elem.get("likes")

            # Stable comment_id from (spotlight_url, author, text, published_at)
            comment_id = sha1_hex(spotlight_url, raw_author, raw_text, raw_published_at)
            comment_uid = sha1_hex("snap", comment_id)

            # Text triage — returns (text_status, flags_list)
            text_status, flags = triage(raw_text)
            text_used = raw_text if text_status == "ok" else ""

            # Comment posted_at: use the comment's published_at; fall back to post's posted_at
            c_posted_at = _to_ts(raw_published_at) if raw_published_at else _to_ts(post_posted_at)

            # Like count: may be integer or None
            try:
                like_count = int(raw_likes) if raw_likes is not None else None
            except (ValueError, TypeError):
                like_count = None

            comment_rows.append({
                "comment_uid": comment_uid,
                "comment_id": comment_id,
                "platform": "snapchat",
                "post_uid": post_uid,
                "post_url": spotlight_url,
                "post_native_id": native_id,
                "posted_at": c_posted_at,
                "scraped_at": _to_ts(scraped_at),
                "text_source": "comment",
                "comment_text": raw_text,
                "text_used": text_used,
                "text_len": len(raw_text),
                "text_status": text_status,
                "flags": "|".join(flags) if flags else None,
                "layer": "comment",
                "n_source_rows": int(row["n_source_rows"]),
                "like_count": like_count,
                "author_hash": sha1_hex(raw_author)[:12] if raw_author else None,
                "dup_text_n": None,
                "dup_text_repeat": False,
            })

    comments_df = pd.DataFrame(comment_rows)

    if len(comments_df):
        comments_df = comments_df.drop_duplicates(subset=["post_url", "comment_id"]).reset_index(drop=True)
        # Mark duplicate texts within each post
        dup_counts = comments_df.groupby(["post_uid", "text_used"])["comment_uid"].transform("count")
        comments_df["dup_text_n"] = dup_counts
        comments_df["dup_text_repeat"] = (
            comments_df.duplicated(subset=["post_uid", "text_used"], keep="first")
            | (comments_df["text_used"] == "")
        )

    # Posts extract: one row per distinct spotlight_url
    # spotlight_url is the canonical post URL; alias as post_url for consistency with other platforms
    df_posts["post_url"] = df_posts["spotlight_url"]
    posts_extract = df_posts[["post_uid", "post_url", "spotlight_url", "native_id",
                               "post_in_store", "scraped_at_utc", "n_source_rows"]].copy()
    posts_extract = posts_extract.rename(columns={"scraped_at_utc": "scraped_at"})
    posts_extract["n_comments"] = df_posts["top_comments"].apply(
        lambda x: len(x) if isinstance(x, list) else 0
    )
    posts_extract["scraped_at"] = pd.to_datetime(posts_extract["scraped_at"], utc=True, errors="coerce")

    # posted_at for posts: prefer post_facts, then snapchat_posts.posted_at
    def get_posted_at(row):
        pf_ts = row.get("posted_at_pf")
        raw_ts = row.get("posted_at")
        v = pf_ts if pf_ts is not None else raw_ts
        return _to_ts(v)

    posts_extract["posted_at"] = df_posts.apply(get_posted_at, axis=1)

    n_in_store = int(df_posts["post_in_store"].sum())
    reconciliation = {
        "source_rows_with_comments": prod["rows_with_comments"],
        "distinct_urls_with_comments": prod["distinct_urls_with_comments"],
        "deduplicated_posts": int(len(df_posts)),
        "posts_in_analytics_store": n_in_store,
        "posts_not_in_store": int(len(df_posts)) - n_in_store,
        "total_comment_rows_extracted": int(len(comments_df)),
        "ok_to_score": int((comments_df["text_status"] == "ok").sum()) if len(comments_df) else 0,
    }
    meta = {
        "snapshot_ts": snap_ts.isoformat(),
        "production": prod,
        "reconciliation": reconciliation,
        "platform": "snapchat",
        "caveat": "Only first 20 comments per Spotlight video collected (Snapchat Spotlight scraper limit). Post-level tone scores available for posts in the analytics store.",
    }
    return comments_df, posts_extract, meta


def main() -> int:
    print("=== Snapchat comment extraction ===", flush=True)
    # Step 1: Get post_uid mapping from DuckDB analytics store
    pf_lookup = fetch_post_facts_lookup()
    # Step 2: Fetch comments from Postgres (read-only)
    snap_ts, prod, rows, colnames = fetch()
    # Step 3: Extract and structure
    comments_df, posts_df, meta = extract(snap_ts, prod, rows, colnames, pf_lookup)

    out_comments = OUT_DIR / "snap_comments_extract.parquet"
    out_posts = OUT_DIR / "snap_posts_extract.parquet"
    out_meta = OUT_DIR / "extract_meta_snap.json"

    comments_df.to_parquet(out_comments, index=False)
    posts_df.to_parquet(out_posts, index=False)
    out_meta.write_text(json.dumps(meta, indent=2, default=str))

    print(f"\n  Posts extracted:    {len(posts_df)}", flush=True)
    print(f"  Comments extracted: {len(comments_df)}", flush=True)
    if len(comments_df):
        print(f"  text_status counts: {comments_df['text_status'].value_counts().to_dict()}", flush=True)
        print(f"  Posts in analytics store: {meta['reconciliation']['posts_in_analytics_store']}", flush=True)
    print(f"\nWrote:\n  {out_comments}\n  {out_posts}\n  {out_meta}", flush=True)
    print(f"\nCaveat: {meta['caveat']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
