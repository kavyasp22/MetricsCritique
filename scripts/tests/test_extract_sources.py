#!/usr/bin/env python3
"""Static checks of scripts/01_extract_base.py source definitions (no database needed).

Guards the multi-source extract: every PLATFORM_SQL block yields the common columns in order, each key has a
SOURCE_TABLE, the Twitter tables share one platform label and the same post_uid scheme (so a tweet present in both
twitter_posts and x_posts is one post), and the effort queries line up.

Run:  .venv/bin/python -m pytest scripts/tests/test_extract_sources.py -v
"""
import importlib.util
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("m01", ROOT / "scripts" / "01_extract_base.py")
m01 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m01)

COMMON = ["platform", "native_id", "url", "author_id", "author_name", "is_verified", "text", "lang", "posted_at",
          "scraped_at", "keyword", "hashtags", "likes", "comments", "shares", "views", "has_engagement", "embedding"]
NEW_KEYS = ["twitter_x", "discord", "chirpwire", "weibo", "kashmirfight", "rocketchat"]


def _select_names(sql: str) -> list[str]:
    body = sql.split("SELECT", 1)[1].rsplit("FROM", 1)[0]
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur.strip()); cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    names = []
    for p in parts:
        m = re.search(r"\bAS\s+\"?(\w+)\"?\s*$", p, re.I | re.S)
        names.append(m.group(1) if m else re.sub(r"\W", "", p.split()[-1]))
    return names


@pytest.mark.parametrize("key", list(m01.PLATFORM_SQL))
def test_every_platform_sql_yields_the_common_columns(key):
    assert _select_names(m01.PLATFORM_SQL[key]) == COMMON, key


def test_every_platform_key_has_a_source_table_and_a_from_clause():
    for key, sql in m01.PLATFORM_SQL.items():
        table = m01.SOURCE_TABLE[key]
        assert re.search(rf"FROM\s+{table}\b", sql), f"{key}: SQL does not read {table}"


def test_new_sources_are_registered():
    for k in NEW_KEYS:
        assert k in m01.PLATFORM_SQL and k in m01.EFFORT_SQLS and k in m01.SOURCE_TABLE, k
    assert m01.SOURCE_TABLE["twitter"] == "twitter_posts" and m01.SOURCE_TABLE["twitter_x"] == "x_posts"


def test_x_posts_is_labelled_twitter_with_the_twitter_mapping():
    sql = m01.PLATFORM_SQL["twitter_x"]
    assert "'twitter'::text AS platform" in sql and "tweet_id::text AS native_id" in sql
    tw = m01.PLATFORM_SQL["twitter"]
    for frag in ("like_count AS likes", "reply_count AS comments", "retweet_count AS shares",
                 "impression_count::double precision AS views"):
        assert frag in sql and frag in tw


def test_effort_queries_return_platform_day_counts():
    for k in NEW_KEYS:
        names = _select_names(m01.EFFORT_SQLS[k])
        assert names == ["platform", "day", "rows_ingested", "active_channels", "active_keywords"], k


def test_same_tweet_in_both_twitter_tables_is_one_post():
    row = dict(platform="twitter", native_id="1850000000000000001", url="u", author_id="a", author_name="n",
               is_verified=None, text="a tweet long enough to count", lang="en",
               posted_at=pd.Timestamp("2026-10-20", tz="UTC"), keyword=None, hashtags=None, likes=1.0, comments=0.0,
               shares=0.0, views=10.0, has_engagement=True)
    a = pd.DataFrame([{**row, "scraped_at": pd.Timestamp("2026-10-21", tz="UTC"), "source_table": "twitter_posts"}])
    b = pd.DataFrame([{**row, "scraped_at": pd.Timestamp("2026-11-01", tz="UTC"), "source_table": "x_posts"}])
    fa, _ = m01.normalize_and_dedup(a)
    fb, _ = m01.normalize_and_dedup(b)
    assert fa.post_uid.iloc[0] == fb.post_uid.iloc[0]                    # same uid -> deduplicated downstream
    both = pd.concat([fa, fb], ignore_index=True)
    kept = (both.sort_values("scraped_at", kind="stable", na_position="first")
            .drop_duplicates(subset=["post_uid"], keep="last"))
    assert len(kept) == 1 and kept.source_table.iloc[0] == "x_posts"     # latest scraped_at wins
    assert list(fa.columns)[-1] == "source_table"
