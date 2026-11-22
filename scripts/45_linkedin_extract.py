#!/usr/bin/env python3
"""
Audience sentiment, Phase 4 (LinkedIn), step 1: READ-ONLY extraction of comments from linkedin_posts.

Reads production Postgres through lib.pg.read_only_snapshot (REPEATABLE READ, read-only, SELECT only, always ROLLBACK).
No scraping. Comments are stored as a flat JSONB array in the `comments` column of linkedin_posts; each element has
at minimum `text` and `date` keys. LinkedIn scraper collects at most the first ~10 comments per post.

Writes under data/audience/ (git-ignored):
    li_comments_extract.parquet   one row per distinct (post_url, comment_id); comment_id is a stable sha1 of the content
    li_posts_extract.parquet      one row per distinct post_url: stable post_uid (sha1 of URL), comment count, scraped_at
    extract_meta_li.json          production counts and reconciliation

Key facts:
  - linkedin_posts has duplicate post_url rows; we keep the row with the most comments for each URL.
  - LinkedIn comments have no native ID → sha1_hex(post_url + "|" + text + "|" + date) for stability.
  - LinkedIn is comment-only: there is no replies layer.
  - Only the first ~10 comments per post are collected (LinkedIn scraper limit).
  - Comment collection began around August 2026.
  - LinkedIn posts are not in the analytics store; post_uid is derived: "li_" + sha1(post_url)[:16].
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.audience_text import sha1_hex, triage
from lib.config import DATA_DIR
from lib.pg import q1, read_only_snapshot

OUT_DIR = DATA_DIR / "audience"
OUT_DIR.mkdir(parents=True, exist_ok=True)

NE = "jsonb_typeof(comments) = 'array' AND jsonb_array_length(comments) > 0"


def _to_ts(v):
    """Convert a value to UTC Timestamp safely."""
    if v is None:
        return pd.NaT
    try:
        ts = pd.Timestamp(v)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    except Exception:
        return pd.NaT


def fetch():
    with read_only_snapshot() as (conn, cur, snap_ts):
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone()[0] != "on":
            raise RuntimeError("production connection is not read-only; refusing to continue")

        prod = {}
        c = q1(cur,
            f"SELECT count(*), count(DISTINCT post_url), "
            f"count(*) FILTER (WHERE {NE}), count(DISTINCT post_url) FILTER (WHERE {NE}) "
            f"FROM linkedin_posts")
        prod.update(rows=int(c[0]), distinct_urls=int(c[1]),
                    rows_with_comments=int(c[2]), distinct_urls_with_comments=int(c[3]))
        print(f"snapshot {snap_ts.isoformat()}: {prod}", flush=True)

        # Fetch all posts with comments; deduplicate below by keeping the row with the most comments per URL
        cur.execute(
            f"SELECT post_url, comments, scraped_at, posted_at "
            f"FROM linkedin_posts "
            f"WHERE {NE} "
            f"ORDER BY post_url, jsonb_array_length(comments) DESC"
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        print(f"  fetched {len(rows)} rows with comments", flush=True)
        return snap_ts, prod, rows, colnames


def extract(snap_ts, prod, rows, colnames):
    df_raw = pd.DataFrame(rows, columns=colnames)

    # Deduplicate: keep one row per post_url (most comments first due to ORDER BY)
    df_posts = df_raw.drop_duplicates(subset=["post_url"], keep="first").copy()
    df_posts = df_posts.reset_index(drop=True)
    n_source_map = df_raw.groupby("post_url").size().to_dict()
    df_posts["n_source_rows"] = df_posts["post_url"].map(n_source_map).fillna(1).astype(int)

    # Stable post_uid derived from post_url (LinkedIn posts are not in the analytics store)
    df_posts["post_uid"] = df_posts["post_url"].apply(lambda u: "li_" + sha1_hex(str(u))[:16])

    comment_rows = []
    for _, row in df_posts.iterrows():
        post_uid = row["post_uid"]
        post_url = row["post_url"]
        scraped_at = row["scraped_at"]
        post_posted_at = row.get("posted_at")
        comments = row["comments"]

        if not isinstance(comments, list):
            continue

        for elem in comments:
            if not isinstance(elem, dict):
                continue
            raw_text = elem.get("text") or ""
            raw_date = elem.get("date") or ""

            # Stable comment_id from (post_url, text, date)
            comment_id = sha1_hex(post_url, raw_text, raw_date)
            comment_uid = sha1_hex("li", comment_id)

            # Text triage — returns (text_status, flags_list)
            text_status, flags = triage(raw_text)
            text_used = raw_text if text_status == "ok" else ""

            # Comment posted_at: use the comment's date; fall back to post's posted_at
            c_posted_at = _to_ts(raw_date) if raw_date else _to_ts(post_posted_at)

            comment_rows.append({
                "comment_uid": comment_uid,
                "comment_id": comment_id,
                "platform": "linkedin",
                "post_uid": post_uid,
                "post_url": post_url,
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
                "like_count": None,
                "author_hash": None,
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

    # Posts extract: one row per distinct post_url
    posts_extract = df_posts[["post_uid", "post_url", "scraped_at", "n_source_rows"]].copy()
    posts_extract["n_comments"] = df_posts["comments"].apply(
        lambda x: len(x) if isinstance(x, list) else 0
    )
    posts_extract["scraped_at"] = pd.to_datetime(posts_extract["scraped_at"], utc=True)
    if "posted_at" in df_posts.columns:
        posts_extract["posted_at"] = pd.to_datetime(df_posts["posted_at"], utc=True, errors="coerce")
    else:
        posts_extract["posted_at"] = pd.NaT

    reconciliation = {
        "source_rows_with_comments": prod["rows_with_comments"],
        "distinct_urls_with_comments": prod["distinct_urls_with_comments"],
        "deduplicated_posts": int(len(df_posts)),
        "total_comment_rows_extracted": int(len(comments_df)),
        "ok_to_score": int((comments_df["text_status"] == "ok").sum()) if len(comments_df) else 0,
    }
    meta = {
        "snapshot_ts": snap_ts.isoformat(),
        "production": prod,
        "reconciliation": reconciliation,
        "platform": "linkedin",
        "caveat": "Only first ~10 comments per post collected (LinkedIn scraper limit). Collection began ~August 2026.",
    }
    return comments_df, posts_extract, meta


def main() -> int:
    print("=== LinkedIn comment extraction ===", flush=True)
    snap_ts, prod, rows, colnames = fetch()
    comments_df, posts_df, meta = extract(snap_ts, prod, rows, colnames)

    out_comments = OUT_DIR / "li_comments_extract.parquet"
    out_posts = OUT_DIR / "li_posts_extract.parquet"
    out_meta = OUT_DIR / "extract_meta_li.json"

    comments_df.to_parquet(out_comments, index=False)
    posts_df.to_parquet(out_posts, index=False)
    out_meta.write_text(json.dumps(meta, indent=2, default=str))

    print(f"\n  Posts extracted:    {len(posts_df)}", flush=True)
    print(f"  Comments extracted: {len(comments_df)}", flush=True)
    if len(comments_df):
        print(f"  text_status counts: {comments_df['text_status'].value_counts().to_dict()}", flush=True)
    print(f"\nWrote:\n  {out_comments}\n  {out_posts}\n  {out_meta}", flush=True)
    print(f"\nCaveat: {meta['caveat']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
