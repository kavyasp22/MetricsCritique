#!/usr/bin/env python3
"""
Audience sentiment, Phase 1 (YouTube), step 1: fresh READ-ONLY extraction of comments and replies.

Reads production Postgres through lib.pg.read_only_snapshot (REPEATABLE READ, read-only, always ROLLBACK, SELECT
only). Nothing is written to production. Writes under data/audience/ (gitignored, holds comment text):

    yt_comments_extract.parquet   one row per production comment/reply, EVERY row kept with a text_status
    yt_videos_extract.parquet     parent videos with the comment count YouTube reported
    extract_meta.json             snapshot time and production counts taken in the SAME transaction

Production counts and the extract come from one snapshot, so the coverage report compares like with like even
though youtube_comments keeps growing between runs.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.audience_text import NOT_SCORED, dup_key, pick_text, sha1_hex, triage
from lib.config import DATA_DIR
from lib.pg import q, q1, read_only_snapshot

OUT_DIR = DATA_DIR / "audience"
COMMENT_COLS = [
    "comment_id", "video_id", "parent_comment_id", "is_reply", "author_channel_id", "comment_text",
    "translated_comment_text", "like_count", "published_at", "updated_at", "is_edited", "scraped_at",
]
COMMENT_SQL = f"SELECT {', '.join(COMMENT_COLS)} FROM youtube_comments"
COUNT_SQL = """
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE COALESCE(comment_text, '') <> ''),
           COUNT(*) FILTER (WHERE COALESCE(translated_comment_text, '') <> ''),
           COUNT(*) FILTER (WHERE is_reply),
           COUNT(DISTINCT video_id),
           COUNT(DISTINCT comment_id),
           MIN(published_at), MAX(published_at), MAX(scraped_at)
    FROM youtube_comments
"""


def _ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def fetch() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    with read_only_snapshot() as (conn, cur, snap_ts):
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("SHOW transaction_read_only")
        read_only = cur.fetchone()[0]
        if read_only != "on":
            raise RuntimeError("production connection is not read-only; refusing to continue")
        c = q1(cur, COUNT_SQL)
        prod = {
            "rows": int(c[0]), "rows_with_original_text": int(c[1]), "rows_with_translation": int(c[2]),
            "replies": int(c[3]), "distinct_videos": int(c[4]), "distinct_comment_ids": int(c[5]),
            "min_published_at": str(c[6]), "max_published_at": str(c[7]), "max_scraped_at": str(c[8]),
        }
        print(f"production snapshot {snap_ts.isoformat()}: {prod['rows']:,} rows, {prod['replies']:,} replies, "
              f"{prod['distinct_videos']:,} videos", flush=True)
        rows: list[tuple] = []
        stream = conn.cursor(name="audience_yt_comments")      # server-side cursor, still inside the read-only txn
        stream.itersize = 20000
        stream.execute(COMMENT_SQL)
        while True:
            chunk = stream.fetchmany(20000)
            if not chunk:
                break
            rows.extend(chunk)
            print(f"  fetched {len(rows):,}", flush=True)
        stream.close()
        df = pd.DataFrame(rows, columns=COMMENT_COLS)
        vids = sorted(df["video_id"].dropna().unique().tolist())
        _, vrows = q(cur, "SELECT video_id, video_comment_count FROM youtube_videos WHERE video_id = ANY(%s)", (vids,))
        videos = pd.DataFrame(vrows, columns=["video_id", "video_comment_count_reported"])
        meta = {"snapshot_ts": snap_ts.isoformat(), "production": prod, "read_only_session": read_only}
    return df, videos, meta


def build(df: pd.DataFrame, videos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    stats: dict = {"rows_fetched": int(len(df))}
    for c in ("published_at", "updated_at", "scraped_at"):
        df[c] = _ts(df[c])

    # Exact duplicate comment_id (none expected): keep the latest scrape, count what was removed.
    df = df.sort_values("scraped_at", na_position="first")
    before = len(df)
    df = df.drop_duplicates("comment_id", keep="last").copy()
    stats["duplicate_comment_id_removed"] = int(before - len(df))

    df["comment_uid"] = df["comment_id"].map(lambda x: sha1_hex("yt_comment", str(x)))     # same recipe as 01_extract_base
    df["parent_comment_uid"] = df["parent_comment_id"].map(lambda x: sha1_hex("yt_comment", str(x)) if pd.notna(x) else None)
    df["post_uid"] = df["video_id"].map(lambda x: sha1_hex("youtube_video", str(x)))        # = post_facts.post_uid for the video
    df["platform"] = "youtube"
    df["layer"] = np.where(df["is_reply"].fillna(False), "reply", "comment")
    df["author_hash"] = df["author_channel_id"].map(lambda x: sha1_hex("yt_author", str(x)) if pd.notna(x) else None)

    picked = [pick_text(o, t) for o, t in zip(df["comment_text"], df["translated_comment_text"])]
    df["text_used"] = [p[0] for p in picked]
    df["text_source"] = [p[1] for p in picked]
    tri = [triage(t) for t in df["text_used"]]
    df["text_status"] = [t[0] for t in tri]
    df["flags"] = [";".join(t[1]) for t in tri]
    df["text_len"] = df["text_used"].str.len()

    # Relationships: does a reply's parent exist in the extract, on the same video, and is it a top-level comment?
    top = df.set_index("comment_id")[["video_id", "is_reply"]]
    is_rep = df["layer"] == "reply"
    par = df["parent_comment_id"].map(lambda x: x if pd.notna(x) else None)
    in_ext = par.isin(top.index)
    df["parent_in_extract"] = np.where(is_rep, in_ext, None)
    pv = par.map(top["video_id"]) if len(top) else None
    df["parent_same_video"] = np.where(is_rep & in_ext, pv == df["video_id"], None)
    pr = par.map(top["is_reply"])
    df["parent_is_reply"] = np.where(is_rep & in_ext, pr.fillna(False).astype(bool), None)
    stats["replies_parent_missing"] = int((is_rep & ~in_ext).sum())
    stats["replies_parent_other_video"] = int((is_rep & in_ext & (pv != df["video_id"])).sum())
    stats["replies_parent_is_reply"] = int((is_rep & in_ext & pr.fillna(False).astype(bool)).sum())

    # Repeated text inside one video (spam, "first!"): flagged, still scored, never dropped.
    ok = df["text_status"] == "ok"
    key = df["text_used"].map(dup_key)
    g = df[ok].assign(_k=key[ok]).sort_values(["published_at", "comment_id"])
    g["dup_text_n"] = g.groupby(["video_id", "layer", "_k"])["comment_id"].transform("size")
    g["dup_text_rank"] = g.groupby(["video_id", "layer", "_k"]).cumcount()
    df["dup_text_n"] = g["dup_text_n"].reindex(df.index)
    df["dup_text_repeat"] = (g["dup_text_rank"] > 0).reindex(df.index).fillna(False)
    stats["repeat_text_rows"] = int(df["dup_text_repeat"].sum())

    df = df.sort_values(["video_id", "layer", "published_at", "comment_id"]).reset_index(drop=True)
    keep = ["comment_uid", "comment_id", "layer", "platform", "video_id", "post_uid", "parent_comment_id",
            "parent_comment_uid", "parent_in_extract", "parent_same_video", "parent_is_reply", "author_hash",
            "like_count", "published_at", "updated_at", "is_edited", "scraped_at", "text_source", "text_len",
            "text_status", "flags", "dup_text_n", "dup_text_repeat", "text_used", "comment_text", "translated_comment_text"]
    out = df[keep].copy()

    videos = videos.copy()
    videos["post_uid"] = videos["video_id"].map(lambda x: sha1_hex("youtube_video", str(x)))
    cnt = out.groupby("video_id").agg(n_comments_extracted=("layer", lambda s: int((s == "comment").sum())),
                                      n_replies_extracted=("layer", lambda s: int((s == "reply").sum()))).reset_index()
    videos = videos.merge(cnt, on="video_id", how="outer")
    videos["post_uid"] = videos["video_id"].map(lambda x: sha1_hex("youtube_video", str(x)))
    stats["rows_out"] = int(len(out))
    stats["status_counts"] = out["text_status"].value_counts().to_dict()
    stats["layer_counts"] = out["layer"].value_counts().to_dict()
    return out, videos, stats


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, videos, meta = fetch()
    out, videos_out, stats = build(df, videos)
    out.to_parquet(OUT_DIR / "yt_comments_extract.parquet", index=False)
    videos_out.to_parquet(OUT_DIR / "yt_videos_extract.parquet", index=False)
    meta["extract"] = stats
    meta["written_at"] = datetime.now(timezone.utc).isoformat()
    (OUT_DIR / "extract_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(json.dumps(stats, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
