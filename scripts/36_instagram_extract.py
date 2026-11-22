#!/usr/bin/env python3
"""
Audience sentiment, Phase 3 (Instagram), step 1: fresh READ-ONLY extraction of comments and nested replies.

Reads production Postgres through lib.pg.read_only_snapshot (REPEATABLE READ, read-only, SELECT only, always ROLLBACK).
No Instagram requests, no scraping: only the `comments_data` already in production. Writes under data/audience/
(git-ignored, holds comment text):

    ig_comments_extract.parquet   one row per distinct (post_url, comment_id), comments and replies, every element kept
    ig_posts_extract.parquet      one row per distinct post_url (15,861 in production), canonical post_uid, copy counts
    extract_meta_ig.json          production counts taken in the SAME transaction and the reconciliation

Key fact: `instagram_posts` holds the same post many times (up to 501 identical copies per post_url, 98,389 rows with
comments but ~1,400 distinct posts). The unit here is the distinct post (post_url); a comment that appears in several
copies is kept once, with `n_source_rows` recording how many production rows carried it. Nothing is dropped silently:
`reconciliation` shows production elements (with copies) = extracted elements x copies.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.audience_instagram import MALFORMED, flatten_payload
from lib.audience_text import dup_key, mention_only, pick_text, sha1_hex, triage
from lib.config import DATA_DIR, DUCKDB_PATH
from lib.pg import q, q1, read_only_snapshot

OUT_DIR = DATA_DIR / "audience"
NE = "CASE WHEN jsonb_typeof(comments_data)='array' THEN jsonb_array_length(comments_data) > 0 ELSE FALSE END"
NE_P = NE.replace("comments_data", "p.comments_data")


def fetch():
    with read_only_snapshot() as (conn, cur, snap_ts):
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone()[0] != "on":
            raise RuntimeError("production connection is not read-only; refusing to continue")
        prod = {}
        c = q1(cur, f"SELECT count(*), count(DISTINCT post_url), count(*) FILTER (WHERE {NE}), count(DISTINCT post_url) FILTER (WHERE {NE}), "
                    f"count(DISTINCT (post_url, md5(comments_data::text))) FILTER (WHERE {NE}) FROM instagram_posts")
        prod.update(rows=int(c[0]), distinct_post_urls=int(c[1]), rows_with_comments=int(c[2]), distinct_urls_with_comments=int(c[3]),
                    distinct_payload_variants=int(c[4]))
        print(f"snapshot {snap_ts.isoformat()}: {prod}", flush=True)
        c = q1(cur, f"SELECT count(*), count(DISTINCT (post_url, e->>'comment_id')) FROM instagram_posts p, jsonb_array_elements(p.comments_data) e WHERE {NE_P}")
        prod.update(top_level_elements_with_copies=int(c[0]), top_level_distinct_post_comment_ids=int(c[1]))
        c = q1(cur, f"SELECT count(*), count(DISTINCT (post_url, r->>'comment_id')) FROM instagram_posts p, jsonb_array_elements(p.comments_data) e, "
                    f"jsonb_array_elements(CASE WHEN jsonb_typeof(e->'replies')='array' THEN e->'replies' ELSE '[]'::jsonb END) r WHERE {NE_P}")
        prod.update(nested_reply_elements_with_copies=int(c[0]), nested_reply_distinct_post_comment_ids=int(c[1]))
        print(f"  elements: {prod}", flush=True)
        # one row per distinct payload variant (latest scrape wins), plus how many rows carry each variant
        _, vrows = q(cur, f"""SELECT DISTINCT ON (post_url, md5(comments_data::text)) post_url, md5(comments_data::text) AS h, id, scraped_at, comments_data
                              FROM instagram_posts WHERE {NE} ORDER BY post_url, md5(comments_data::text), scraped_at DESC NULLS LAST, id""")
        _, mrows = q(cur, f"SELECT post_url, md5(comments_data::text) AS h, count(*) FROM instagram_posts WHERE {NE} GROUP BY 1, 2")
        _, urows = q(cur, f"""SELECT post_url, min(id), count(*), count(*) FILTER (WHERE {NE}), max(comment_count), min(comment_count),
                              max(likes_count), min(posted_at), max(scraped_at) FROM instagram_posts GROUP BY post_url""")
        meta = {"snapshot_ts": snap_ts.isoformat(), "production": prod, "read_only_session": "on"}
    variants = pd.DataFrame(vrows, columns=["post_url", "h", "src_id", "scraped_at", "payload"])
    mult = pd.DataFrame(mrows, columns=["post_url", "h", "n_rows"])
    posts = pd.DataFrame(urows, columns=["post_url", "min_id", "n_rows", "n_rows_with_comments", "reported_comment_count_max",
                                         "reported_comment_count_min", "likes_count_max", "posted_at", "last_scraped_at"])
    return variants, mult, posts, meta


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    variants, mult, posts, meta = fetch()
    variants = variants.merge(mult, on=["post_url", "h"], how="left")
    # ---- flatten every distinct payload variant; keep multiplicity for the reconciliation
    flat, elem_with_copies = [], defaultdict(int)
    for r in variants.itertuples():
        for e in flatten_payload(r.payload):
            e.update(post_url=r.post_url, variant_hash=r.h, src_id=r.src_id, scraped_at=r.scraped_at, variant_rows=int(r.n_rows))
            flat.append(e)
            elem_with_copies[("nested" if e["depth"] >= 1 else "top")] += int(r.n_rows)
    df = pd.DataFrame(flat)
    stats = {"variants": int(len(variants)), "elements_in_variants": int(len(df)), "elements_with_copies": dict(elem_with_copies)}
    p = meta["production"]
    stats["reconciliation"] = {
        "top_level_elements_with_copies: production vs variants x copies": [p["top_level_elements_with_copies"], elem_with_copies["top"]],
        "nested_reply_elements_with_copies: production vs variants x copies": [p["nested_reply_elements_with_copies"], elem_with_copies["nested"]],
    }

    # ---- canonical post per post_url: the store's post_uid when the post is in the analytics store, else the recipe on min(id)
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    st = a.execute("""SELECT f.post_uid, f.url AS post_url, coalesce(d.is_first, false) AS is_first FROM post_facts f
                      LEFT JOIN read_parquet('data/post_dedup.parquet') d USING (post_uid) WHERE f.platform = 'instagram'""").df()
    a.close()
    st = st.sort_values(["post_url", "is_first", "post_uid"], ascending=[True, False, True]).drop_duplicates("post_url")
    posts = posts.merge(st[["post_url", "post_uid"]].rename(columns={"post_uid": "store_post_uid"}), on="post_url", how="left")
    posts["post_in_store"] = posts["store_post_uid"].notna()
    posts["post_uid"] = np.where(posts["post_in_store"], posts["store_post_uid"], posts["min_id"].map(lambda x: sha1_hex("instagram", str(x))))
    posts["platform"] = "instagram"
    posts["post_native_id"] = posts["min_id"].astype("Int64").astype(str)
    posts["post_url"] = posts["post_url"].astype("object")

    # ---- one row per (post_url, comment_id): the latest-scraped variant wins; items without an id are kept as they are
    df["has_id"] = df["comment_id"].notna()
    df = df.sort_values(["post_url", "scraped_at"], na_position="first")
    df["_k"] = np.where(df["has_id"], df["post_url"].astype(str) + "|" + df["comment_id"].astype(str),
                        df["post_url"].astype(str) + "|malformed|" + df["variant_hash"] + "|" + df.groupby(["post_url", "variant_hash"]).cumcount().astype(str))
    stats["duplicate_elements_across_variants"] = int(df["_k"].duplicated().sum())
    grp = df.groupby("_k")
    first_seen = grp["scraped_at"].min()
    n_rows = grp["variant_rows"].sum()                   # production rows that carried this element (variants x copies, by key)
    n_variants = grp.size()
    out = df.drop_duplicates("_k", keep="last").copy()
    out["n_source_rows"] = out["_k"].map(n_rows).astype(int)
    out["n_variants_seen"] = out["_k"].map(n_variants).astype(int)
    out["first_scraped_at"] = out["_k"].map(first_seen)
    out["comment_uid"] = [sha1_hex("ig_comment", c) if c else sha1_hex("ig_malformed", k) for c, k in zip(out["comment_id"], out["_k"])]
    out["parent_comment_uid"] = out["parent_comment_id"].map(lambda x: sha1_hex("ig_comment", x) if pd.notna(x) else None)
    out["author_hash"] = out["username"].map(lambda x: sha1_hex("ig_author", x) if pd.notna(x) else None)
    out = out.merge(posts[["post_url", "post_uid", "post_in_store"]], on="post_url", how="left")
    out["platform"] = "instagram"
    picked = [pick_text(t, None) for t in out["text"]]
    out["text_used"], out["text_source"] = [p_[0] for p_ in picked], [p_[1] for p_ in picked]
    tri = [triage(t) for t in out["text_used"]]
    out["text_status"] = np.where(out["item_status"] == MALFORMED, MALFORMED, [t[0] for t in tri])
    out["flags"] = [";".join(t[1]) if s != MALFORMED else "" for t, s in zip(tri, out["item_status"])]
    ment = np.array([mention_only(t) for t in out["text_used"]])
    out["flags"] = np.where(ment & (out["text_status"] == "ok"), out["flags"].where(out["flags"] != "", "") .astype(str) + np.where(out["flags"] != "", ";", "") + "mention_only", out["flags"])
    out["text_len"] = out["text_used"].str.len()

    # ---- relationships: is the parent comment present on the same post
    ids = out[out["has_id"]].set_index(["post_url", "comment_id"])
    keyset = set(ids.index)
    is_rep = out["layer"] == "reply"
    out["parent_in_extract"] = [(pu, pc) in keyset if (rep and pd.notna(pc)) else None for pu, pc, rep in zip(out["post_url"], out["parent_comment_id"], is_rep)]
    out["declared_parent_differs_from_nesting"] = (out["declared_parent_id"].notna() & out["nest_parent_id"].notna() & (out["declared_parent_id"] != out["nest_parent_id"]))
    out["parent_is_reply"] = [bool(ids.loc[(pu, pc), "layer"] == "reply") if (rep and (pu, pc) in keyset) else None for pu, pc, rep in zip(out["post_url"], out["parent_comment_id"], is_rep)]
    stats["replies_parent_missing"] = int((is_rep & ~out["parent_in_extract"].fillna(False).astype(bool)).sum())

    # repeated text inside one post and layer (spam, "first!"): flagged, still scored
    okm = out["text_status"] == "ok"
    key = out["text_used"].map(dup_key)
    g = out[okm].assign(_dk=key[okm]).sort_values(["created_at", "comment_id"], na_position="last")
    g["dup_text_n"] = g.groupby(["post_url", "layer", "_dk"])["comment_uid"].transform("size")
    g["dup_text_rank"] = g.groupby(["post_url", "layer", "_dk"]).cumcount()
    out["dup_text_n"] = g["dup_text_n"].reindex(out.index)
    out["dup_text_repeat"] = (g["dup_text_rank"] > 0).reindex(out.index).fillna(False)
    out["posted_at"] = pd.to_datetime(out["created_at"], utc=True, errors="coerce")      # the comment's own timestamp
    out["like_count"] = out["likes"]
    out["is_edited"] = None

    keep = ["comment_uid", "comment_id", "layer", "shape", "depth", "platform", "post_url", "post_uid", "post_in_store", "parent_comment_id",
            "parent_comment_uid", "parent_in_extract", "parent_is_reply", "declared_parent_differs_from_nesting", "author_hash", "is_verified",
            "like_count", "posted_at", "scraped_at", "first_scraped_at", "n_source_rows", "n_variants_seen", "text_source", "text_len",
            "text_status", "flags", "dup_text_n", "dup_text_repeat", "item_status", "raw", "text_used"]
    out = out[keep].sort_values(["post_url", "layer", "posted_at", "comment_id"], na_position="last").reset_index(drop=True)
    # Items without a comment_id (malformed) still need unique, repeatable ids: post + content hash + occurrence number.
    occ = out.groupby(["post_url", "raw"], dropna=False).cumcount()
    mal = out["comment_id"].isna()
    out.loc[mal, "comment_uid"] = [sha1_hex("ig_malformed", pu, hashlib.md5(str(rw).encode()).hexdigest(), int(o))
                                   for pu, rw, o in zip(out.loc[mal, "post_url"], out.loc[mal, "raw"], occ[mal])]
    cnt = out.groupby("post_url").agg(n_comments_extracted=("layer", lambda s: int((s == "comment").sum())),
                                      n_replies_extracted=("layer", lambda s: int((s == "reply").sum()))).reset_index()
    posts = posts.merge(cnt, on="post_url", how="left")
    posts[["n_comments_extracted", "n_replies_extracted"]] = posts[["n_comments_extracted", "n_replies_extracted"]].fillna(0).astype(int)

    stats.update(rows_out=int(len(out)), layer_counts=out["layer"].value_counts().to_dict(), shape_counts=out["shape"].value_counts().to_dict(),
                 status_counts=out["text_status"].value_counts().to_dict(), n_posts_with_rows=int((posts["n_comments_extracted"] + posts["n_replies_extracted"] > 0).sum()))
    out.to_parquet(OUT_DIR / "ig_comments_extract.parquet", index=False)
    posts.to_parquet(OUT_DIR / "ig_posts_extract.parquet", index=False)
    meta["extract"] = stats
    meta["written_at"] = datetime.now(timezone.utc).isoformat()
    (OUT_DIR / "extract_meta_ig.json").write_text(json.dumps(meta, indent=2, default=str))
    print(json.dumps(stats, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
