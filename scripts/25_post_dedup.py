#!/usr/bin/env python3
"""
Stage 25 -- group exact copies of the same post (same author, same normalised text).

Reads  data/analytics.duckdb (READ-ONLY): post_facts.
Writes data/post_dedup.parquet (new file; nothing existing is changed):
    post_uid         key of analytics.post_facts
    platform
    author_uid
    dup_group        sha1 of author + normalised text, NULL when the post is not groupable
                     (text under 20 characters after normalisation, or no author)
    group_size       posts in the group (1 when not groupable)
    is_first         True for the earliest post of the group (ties: smallest post_uid); every ungrouped post is its own first
    first_posted     earliest posted day in the group
    last_posted      latest posted day in the group

Why: 22.7% of posts repeat the same text by the same author and 99% of Instagram's posts are copies (955 distinct
texts in 93,015). Clustering those copies produced clusters that are one message repeated, not a topic or an event.
Event and sub-topic work should read `is_first` posts only and keep `group_size` as a weight; the raw rows are untouched.

Exact copies only (after removing case, whitespace, URLs and Snapchat's generated counter caption). Near-duplicates
(a changed word) are left to the event stage, which works on embeddings.

Usage:  python scripts/25_post_dedup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.text_dedup import MIN_CHARS, dup_key  # noqa: E402

SRC = ROOT / "data" / "analytics.duckdb"
OUT = ROOT / "data" / "post_dedup.parquet"


def main() -> int:
    con = duckdb.connect(str(SRC), read_only=True)
    df = con.execute("SELECT post_uid, platform, author_uid, text, "
                     "CAST(coalesce(posted_at, scraped_at) AS DATE) AS day FROM post_facts").fetchdf()
    con.close()
    n = len(df)
    df["dup_group"] = [dup_key(a, t) for a, t in zip(df["author_uid"], df["text"])]
    g = df.groupby("dup_group", dropna=True)
    df["group_size"] = g["post_uid"].transform("size")
    df["group_size"] = df["group_size"].fillna(1).astype(int)
    df["first_posted"] = g["day"].transform("min")
    df["last_posted"] = g["day"].transform("max")
    df["first_posted"] = df["first_posted"].fillna(df["day"])
    df["last_posted"] = df["last_posted"].fillna(df["day"])
    order = df.sort_values(["dup_group", "day", "post_uid"]).groupby("dup_group", dropna=True).head(1)
    firsts = set(order["post_uid"])
    df["is_first"] = df["dup_group"].isna() | df["post_uid"].isin(firsts)
    out = df[["post_uid", "platform", "author_uid", "dup_group", "group_size", "is_first", "first_posted", "last_posted"]]
    assert out["post_uid"].is_unique and len(out) == n
    out.to_parquet(OUT, index=False)
    kept = int(out["is_first"].sum())
    print(f"wrote {OUT}: {n:,} posts -> {kept:,} after collapsing exact copies ({100*(1-kept/n):.1f}% were copies)")
    print(f"groupable (text >= {MIN_CHARS} chars, author known): {int(out['dup_group'].notna().sum()):,}")
    t = out.groupby("platform").agg(posts=("post_uid", "size"), after=("is_first", "sum"), biggest_group=("group_size", "max"))
    t["copies_share"] = (1 - t["after"] / t["posts"]).round(3)
    print(t.sort_values("posts", ascending=False).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
