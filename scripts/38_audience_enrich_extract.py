#!/usr/bin/env python3
"""
Audience sentiment, targeted post enrichment, step 1: READ-ONLY extraction of the posts that carry audience (comment / reply)
data but have no post tone or theme yet, plus a small reproducibility sample of posts that already have them.

Why targeted: a full stage 01 refresh would pull ~1.03 million Instagram rows (copies of ~15.9k posts) into post_facts, and stages
04 / 07 are not dedup-aware. Here ONE canonical row per distinct post is read, only for the target posts. The analytics store
(analytics.duckdb), metrics_v2 and stage 01/04/07 are not touched.

Targets (from the local audience tables):
    instagram  posts with comment data that are (a) not in the analytics post store, or (b) in the store with no valid tone
    youtube    videos with comment data that are not in the analytics post store
Canonical rows (same recipe as the audience extracts):
    instagram  smallest `id` among the copies of a post_url -> post_uid = sha1('instagram|<id>')   (text = caption, as stage 01)
    youtube    the video row                              -> post_uid = sha1('youtube_video|<video_id>')
               text = title + newline + description + newline + first 4000 transcript characters   (exactly as stage 01)
Reads production through lib.pg.read_only_snapshot (REPEATABLE READ, read-only asserted, SELECT only, ROLLBACK).
Writes data/audience/enrich_posts.parquet and enrich_meta.json (git-ignored: they hold post text and vectors).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.audience_text import sha1_hex
from lib.config import DATA_DIR, DUCKDB_PATH
from lib.pg import q, read_only_snapshot

OUT = DATA_DIR / "audience"
DB = DATA_DIR / "audience_sentiment.duckdb"
N_VALID = 150          # in-store posts per platform used to prove the targeted code reproduces the stored results

YT_TEXT = "COALESCE(video_title, '') || E'\\n' || COALESCE(video_description, '') || E'\\n' || COALESCE(LEFT(transcript_text, 4000), '')"


def parse_vec(s) -> np.ndarray | None:
    if not isinstance(s, str) or not s.strip("[] "):
        return None
    return np.array(s.strip("[]").split(","), dtype=np.float32)


def targets() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    c = duckdb.connect(str(DB), read_only=True)
    ig = c.execute("""SELECT a.post_uid, s.post_native_id, s.post_url, a.post_in_store, a.post_tone_available,
                             a.c_scored + a.r_scored AS audience_scored
                      FROM ig_post_audience_sentiment a JOIN ig_post_sentiment s USING (post_uid)
                      WHERE a.comment_data_status = 'has_comments' AND (NOT a.post_in_store OR NOT a.post_tone_available)""").df()
    ig["reason"] = np.where(ig["post_in_store"], "in_store_no_tone", "not_in_store")
    yt = c.execute("""SELECT post_uid, video_id AS post_native_id, post_in_store, post_tone_available, c_scored + r_scored AS audience_scored
                      FROM post_audience_sentiment WHERE comment_data_status = 'has_comments' AND NOT post_in_store""").df()
    yt["reason"] = "not_in_store"
    ctx = {"ig_commented_total": int(c.execute("SELECT count(*) FROM ig_post_audience_sentiment WHERE comment_data_status='has_comments'").fetchall()[0][0]),
           "yt_commented_total": int(c.execute("SELECT count(*) FROM post_audience_sentiment WHERE comment_data_status='has_comments'").fetchall()[0][0]),
           "ig_in_store_with_tone": int(c.execute("SELECT count(*) FROM ig_post_audience_sentiment WHERE comment_data_status='has_comments' AND post_in_store AND post_tone_available").fetchall()[0][0]),
           "yt_in_store": int(c.execute("SELECT count(*) FROM post_audience_sentiment WHERE comment_data_status='has_comments' AND post_in_store").fetchall()[0][0])}
    c.close()
    return ig, yt, ctx


def validation_sample() -> tuple[pd.DataFrame, pd.DataFrame]:
    """In-store posts with a valid stored tone: 150 Instagram (one per distinct post_url) and 150 YouTube videos."""
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    ig = a.execute("""SELECT f.post_uid, f.url AS post_url, f.native_id, s.tone_score AS store_tone FROM post_facts f JOIN post_scores s USING (post_uid)
                      JOIN read_parquet('data/post_dedup.parquet') d USING (post_uid)
                      WHERE f.platform='instagram' AND s.tone_valid AND d.is_first AND f.url IS NOT NULL""").df()
    yt = a.execute("""SELECT f.post_uid, f.native_id, s.tone_score AS store_tone FROM post_facts f JOIN post_scores s USING (post_uid)
                      WHERE f.platform='youtube_video' AND s.tone_valid""").df()
    a.close()
    return ig.sample(N_VALID, random_state=11).reset_index(drop=True), yt.sample(N_VALID, random_state=12).reset_index(drop=True)


def fetch(ig: pd.DataFrame, yt: pd.DataFrame, vig: pd.DataFrame, vyt: pd.DataFrame):
    with read_only_snapshot() as (conn, cur, snap_ts):
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("SHOW transaction_read_only")
        ro = cur.fetchone()[0]
        if ro != "on":
            raise RuntimeError("production connection is not read-only; refusing to continue")
        meta = {"snapshot_ts": snap_ts.isoformat(), "read_only_session": ro}
        urls = sorted(set(ig["post_url"].dropna()) | set(vig["post_url"].dropna()))
        # canonical copy per post_url = smallest id; also check the copies agree on the caption
        _, r = q(cur, """SELECT post_url, min(id), count(*), count(DISTINCT coalesce(caption,'')), max(comment_count)
                         FROM instagram_posts WHERE post_url = ANY(%s) GROUP BY post_url""", (urls,))
        can = pd.DataFrame(r, columns=["post_url", "min_id", "n_copies", "n_distinct_captions", "reported_comment_count"])
        meta["ig_urls_requested"], meta["ig_urls_found"] = len(urls), len(can)
        meta["ig_urls_with_differing_captions_across_copies"] = int((can["n_distinct_captions"] > 1).sum())
        _, r = q(cur, """SELECT id, post_url, caption, posted_at, scraped_at, embedding::text FROM instagram_posts WHERE id = ANY(%s)""", (can["min_id"].astype(int).tolist(),))
        igr = pd.DataFrame(r, columns=["id", "post_url", "text", "posted_at", "scraped_at", "emb"])
        # canonical row lacks an embedding: take one from another copy of the same post (the caption is the same)
        miss = igr[igr["emb"].isna()]["post_url"].tolist()
        meta["ig_canonical_without_embedding"] = len(miss)
        if miss:
            _, r = q(cur, """SELECT DISTINCT ON (post_url) post_url, embedding::text FROM instagram_posts WHERE post_url = ANY(%s) AND embedding IS NOT NULL ORDER BY post_url, id""", (miss,))
            alt = dict(r)
            igr.loc[igr["post_url"].isin(alt), "emb"] = igr.loc[igr["post_url"].isin(alt), "post_url"].map(alt)
            meta["ig_embedding_recovered_from_other_copy"] = int(len(alt))
        igr = igr.merge(can, on="post_url")
        vids = sorted(set(yt["post_native_id"]) | set(vyt["native_id"]))
        _, r = q(cur, f"""SELECT video_id, {YT_TEXT} AS text, video_published_at, scraped_at, video_comment_count, transcript_language, embedding::text
                          FROM youtube_videos WHERE video_id = ANY(%s)""", (vids,))
        ytr = pd.DataFrame(r, columns=["video_id", "text", "posted_at", "scraped_at", "reported_comment_count", "lang", "emb"])
        meta["yt_videos_requested"], meta["yt_videos_found"] = len(vids), len(ytr)
    return igr, ytr, meta


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ig_t, yt_t, ctx = targets()
    vig, vyt = validation_sample()
    print(f"TARGETS  instagram {len(ig_t)} ({ig_t['reason'].value_counts().to_dict()})  youtube {len(yt_t)}  | context {ctx}", flush=True)
    igr, ytr, meta = fetch(ig_t, yt_t, vig, vyt)
    meta["context"] = ctx

    igi = igr.set_index("post_url")
    out = []
    for r in ig_t.itertuples():
        if r.post_url in igi.index:
            x = igi.loc[r.post_url]
            out.append(dict(post_uid=r.post_uid, platform="instagram", native_id=r.post_native_id, post_url=r.post_url, purpose="target", reason=r.reason,
                            text=x.text or "", posted_at=x.posted_at, scraped_at=x.scraped_at, reported_comment_count=x.reported_comment_count,
                            n_copies=int(x.n_copies), emb=x.emb, store_tone=np.nan, lang=None))
    yti = ytr.set_index("video_id")
    for r in yt_t.itertuples():
        if r.post_native_id in yti.index:
            x = yti.loc[r.post_native_id]
            out.append(dict(post_uid=r.post_uid, platform="youtube_video", native_id=r.post_native_id, post_url=None, purpose="target", reason=r.reason,
                            text=x.text or "", posted_at=x.posted_at, scraped_at=x.scraped_at, reported_comment_count=x.reported_comment_count,
                            n_copies=1, emb=x.emb, store_tone=np.nan, lang=x.lang))
    for r in vig.itertuples():
        if r.post_url in igi.index:
            x = igi.loc[r.post_url]
            out.append(dict(post_uid=r.post_uid, platform="instagram", native_id=r.native_id, post_url=r.post_url, purpose="validation", reason="in_store",
                            text=x.text or "", posted_at=x.posted_at, scraped_at=x.scraped_at, reported_comment_count=x.reported_comment_count,
                            n_copies=int(x.n_copies), emb=x.emb, store_tone=r.store_tone, lang=None))
    for r in vyt.itertuples():
        if r.native_id in yti.index:
            x = yti.loc[r.native_id]
            out.append(dict(post_uid=r.post_uid, platform="youtube_video", native_id=r.native_id, post_url=None, purpose="validation", reason="in_store",
                            text=x.text or "", posted_at=x.posted_at, scraped_at=x.scraped_at, reported_comment_count=x.reported_comment_count,
                            n_copies=1, emb=x.emb, store_tone=r.store_tone, lang=x.lang))
    df = pd.DataFrame(out)
    vec = [parse_vec(e) for e in df["emb"]]
    df["has_embedding"] = [v is not None and len(v) == 1024 for v in vec]
    df["embedding"] = [v if (v is not None and len(v) == 1024) else None for v in vec]
    df = df.drop(columns=["emb"])
    df["text_len"] = df["text"].str.strip().str.len()
    t = df[df["purpose"] == "target"]
    meta["target_counts"] = {"instagram": int((t["platform"] == "instagram").sum()), "youtube_video": int((t["platform"] == "youtube_video").sum()),
                             "instagram_by_reason": t[t["platform"] == "instagram"]["reason"].value_counts().to_dict()}
    meta["targets_requested"] = {"instagram": len(ig_t), "youtube_video": len(yt_t)}
    meta["targets_found_in_production"] = meta["target_counts"]
    meta["target_text_empty"] = {p: int(((t["platform"] == p) & (t["text_len"] == 0)).sum()) for p in ("instagram", "youtube_video")}
    meta["target_with_embedding"] = {p: int(((t["platform"] == p) & t["has_embedding"]).sum()) for p in ("instagram", "youtube_video")}
    meta["validation_counts"] = df[df["purpose"] == "validation"]["platform"].value_counts().to_dict()
    meta["written_at"] = datetime.now(timezone.utc).isoformat()
    df.to_parquet(OUT / "enrich_posts.parquet", index=False)
    (OUT / "enrich_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(json.dumps({k: v for k, v in meta.items() if k not in ("context",)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
