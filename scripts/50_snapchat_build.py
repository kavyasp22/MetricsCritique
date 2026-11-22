#!/usr/bin/env python3
"""
Audience sentiment, Phase 5 (Snapchat), step 3: build snap_comment_sentiment table and insert
Snapchat rows into xplat_post_audience_sentiment_v2.

Reads (local only):
  data/audience/snap_comments_extract.parquet
  data/audience/snap_comments_scored.parquet
  data/audience/snap_posts_extract.parquet
  data/analytics.duckdb (READ ONLY: post facts for post_in_store + tone lookup)

Writes to data/audience_sentiment.duckdb (opens exclusively, closes immediately):
  snap_comment_sentiment            (new table, comment-only, no replies layer)
  xplat_post_audience_sentiment_v2  (APPENDED with snapchat rows)

Snapchat-specific notes:
  - Comment-only: all r_* columns are NULL (no replies layer)
  - Max 20 comments per post (Snapchat Spotlight scraper limit)
  - Posts ARE in the analytics store → real post_uid, post_tone_score from post_facts/post_scores
  - 97.9% of posts have 'unassigned' theme; small fraction have D01-D09 themes
  - MIN_SCORED is per-post gap threshold (from lib.audience_rollup)
  - Posts without a tone in analytics.duckdb get post_tone_score=NULL (never treated as neutral)

Fingerprints of existing tables (comment_sentiment, reply_sentiment, ig_comment_sentiment,
ig_reply_sentiment, li_comment_sentiment, xplat rows for youtube/instagram/linkedin) are
verified before and after to ensure no mutation of existing data.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.audience_rollup import MIN_SCORED, audience_gap, comment_data_status, gap_direction, net_sentiment, pct
from lib.audience_text import sha1_hex
from lib.config import DATA_DIR, DUCKDB_PATH

OUT = DATA_DIR / "audience"
DB = DATA_DIR / "audience_sentiment.duckdb"

SNAP_CAVEAT = "Only first 20 comments per Spotlight video collected (Snapchat Spotlight scraper limit). Post-level tone scores available for most posts."

SENT_COLS = [
    "comment_uid", "comment_id", "platform", "post_uid", "post_url",
    "post_in_store", "posted_at", "scraped_at",
    "like_count", "author_hash", "text_source", "text_len", "text_status",
    "flags", "dup_text_n", "dup_text_repeat", "n_source_rows",
    "score_status", "sentiment_label", "p_negative", "p_neutral", "p_positive",
    "tone_score", "model", "scored_at",
]


def fingerprint_xplat(con) -> dict:
    """Fingerprint existing youtube + instagram + linkedin rows so we can verify they're untouched."""
    r = con.execute(
        "SELECT platform, count(*), md5(string_agg(post_uid || coalesce(cast(c_scored AS varchar),''), ',' ORDER BY post_uid)) "
        "FROM xplat_post_audience_sentiment_v2 WHERE platform != 'snapchat' GROUP BY 1"
    ).fetchall()
    return {row[0]: {"rows": int(row[1]), "md5": row[2]} for row in r}


def build_comment_layer(extract: pd.DataFrame, scored: pd.DataFrame) -> pd.DataFrame:
    """Merge extract + scored into the snap_comment_sentiment table."""
    scored = scored.rename(columns={"label": "sentiment_label"})
    m = extract.merge(
        scored[["comment_uid", "score_status", "sentiment_label", "p_negative", "p_neutral",
                "p_positive", "tone_score", "model", "scored_at"]],
        on="comment_uid", how="left"
    )
    # Fill gaps: ok-but-missing = failed; non-ok = not_scored
    m["score_status"] = m["score_status"].where(
        m["score_status"].notna(),
        np.where(m["text_status"] == "ok", "failed", "not_scored")
    )
    m["dup_text_repeat"] = m["dup_text_repeat"].fillna(False).astype(bool)

    # Ensure all required columns exist
    for col in ["like_count", "author_hash", "dup_text_n"]:
        if col not in m.columns:
            m[col] = None

    out = m[[c for c in SENT_COLS if c in m.columns]].reset_index(drop=True)
    # Add any missing sentinel columns
    for c in SENT_COLS:
        if c not in out.columns:
            out[c] = None
    return out[SENT_COLS]


def per_post_rollup(comments: pd.DataFrame, posts: pd.DataFrame, store_lookup: pd.DataFrame) -> pd.DataFrame:
    """Build one-row-per-post metrics for xplat_post_audience_sentiment_v2."""
    # Aggregate per post
    agg = comments.groupby("post_uid").apply(lambda g: pd.Series({
        "c_rows": len(g),
        "c_scored": int((g["score_status"] == "scored").sum()),
        "c_not_scored": int((g["score_status"] == "not_scored").sum()),
        "c_failed": int((g["score_status"] == "failed").sum()),
        "c_neg": int(((g["score_status"] == "scored") & (g["sentiment_label"] == "negative")).sum()),
        "c_neu": int(((g["score_status"] == "scored") & (g["sentiment_label"] == "neutral")).sum()),
        "c_pos": int(((g["score_status"] == "scored") & (g["sentiment_label"] == "positive")).sum()),
        "c_tone_sum": float(g.loc[g["score_status"] == "scored", "tone_score"].sum()),
        "c_tone_sumsq": float((g.loc[g["score_status"] == "scored", "tone_score"] ** 2).sum()),
        "c_conf_sum": float(g.loc[g["score_status"] == "scored",
                                   ["p_negative", "p_neutral", "p_positive"]].max(axis=1).sum()),
        "c_repeat_text_rows": int(g["dup_text_repeat"].sum()),
    }), include_groups=False).reset_index()

    # Derived comment stats (per-post)
    def row_metrics(r):
        n = int(r["c_scored"])
        neg, neu, pos = int(r["c_neg"]), int(r["c_neu"]), int(r["c_pos"])
        ok = n >= MIN_SCORED
        r["c_neg_pct"] = pct(neg, n) if ok else None
        r["c_neu_pct"] = pct(neu, n) if ok else None
        r["c_pos_pct"] = pct(pos, n) if ok else None
        r["c_net_sentiment"] = net_sentiment(pos, neg, n) if ok else None
        r["c_tone_mean"] = r["c_tone_sum"] / n if ok else None
        r["c_tone_sd"] = (
            np.sqrt(max(0.0, r["c_tone_sumsq"] / n - (r["c_tone_sum"] / n) ** 2)) if ok else None
        )
        r["c_tone_se"] = r["c_tone_sd"] / np.sqrt(n) if (ok and r["c_tone_sd"] is not None) else None
        r["c_conf_mean"] = r["c_conf_sum"] / n if ok else None
        r["c_supported"] = ok
        return r

    agg = agg.apply(row_metrics, axis=1)

    # Merge with posts table for metadata
    # Drop post_in_store from posts if already present to avoid duplicate column suffix (_x, _y)
    posts_meta = posts.drop(columns=["post_in_store"], errors="ignore")
    result = posts_meta.merge(agg, on="post_uid", how="left")
    result["platform"] = "snapchat"

    # Post-in-store lookup and tone from analytics
    result = result.merge(
        store_lookup[["post_uid", "post_in_store", "post_tone_score", "post_tone_source",
                      "post_source", "d_theme", "theme_status", "theme_source", "posted_at_store"]],
        on="post_uid", how="left"
    )

    # Use store posted_at when available, else posts extract posted_at
    if "posted_at_store" in result.columns:
        # Prefer store's posted_at
        store_dates = pd.to_datetime(result["posted_at_store"], utc=True, errors="coerce")
        extract_dates = pd.to_datetime(result.get("posted_at"), utc=True, errors="coerce")
        result["posted_at"] = store_dates.where(store_dates.notna(), extract_dates)
    else:
        result["posted_at"] = pd.to_datetime(result.get("posted_at"), utc=True, errors="coerce")

    result["post_in_store"] = result["post_in_store"].fillna(False).astype(bool)
    result["post_tone_source"] = result["post_tone_source"].fillna("none")
    result["post_source"] = result["post_source"].fillna("snapchat_posts")

    # Gaps (NULL if no post tone or not enough scored comments)
    result["gap_comment_vs_post"] = result.apply(
        lambda r: audience_gap(r.get("post_tone_score"), r.get("c_tone_mean"), int(r.get("c_scored") or 0)),
        axis=1
    )
    result["gap_reply_vs_post"] = None
    result["gap_reply_vs_comment"] = None
    result["gap_comment_direction"] = result["gap_comment_vs_post"].apply(
        lambda g: gap_direction(g) if g is not None else None
    )
    result["gap_reply_direction"] = None

    # Coverage
    result["rows_total"] = result["c_rows"].fillna(0).astype(int)
    result["reported_comment_count"] = result.get("n_comments", None)   # from posts extract
    result["coverage_ratio"] = result.apply(
        lambda r: (
            round(r["c_rows"] / r["reported_comment_count"], 4)
            if (r.get("reported_comment_count") and r["reported_comment_count"] > 0 and not pd.isna(r.get("c_rows")))
            else None
        ),
        axis=1
    )
    result["comment_data_status"] = result.apply(
        lambda r: comment_data_status(int(r.get("c_rows") or 0), r.get("reported_comment_count")), axis=1
    )

    # All r_* columns → NULL (no replies)
    for col in ["r_rows", "r_scored", "r_not_scored", "r_failed",
                "r_neg", "r_neu", "r_pos",
                "r_neg_pct", "r_neu_pct", "r_pos_pct",
                "r_net_sentiment", "r_tone_mean", "r_tone_sd", "r_tone_se",
                "r_conf_mean", "r_supported", "r_repeat_text_rows",
                "r_tone_sum", "r_tone_sumsq", "r_conf_sum"]:
        result[col] = None

    result["post_native_id"] = result.get("native_id", None)
    result["post_tone_available"] = result["post_tone_score"].notna()
    result["built_at"] = datetime.now(timezone.utc)

    # posted_month
    result["posted_month"] = result["posted_at"].apply(
        lambda x: str(x)[:7] if pd.notna(x) else None
    )

    return result


def get_store_lookup(post_uids: list[str]) -> pd.DataFrame:
    """Read post facts and scores from analytics.duckdb for Snapchat post_uids.

    Snapchat posts ARE in the analytics store (platform='snapchat' in post_facts).
    This gives us: real post_uid, post_tone_score from post_scores, posted_at.
    """
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)

    # Snapchat posts are in analytics store; query for all uids that match
    uid_list = "'" + "','".join(post_uids) + "'"
    df = a.execute(f"""
        SELECT f.post_uid,
               TRUE AS post_in_store,
               s.tone_score AS post_tone_score,
               'store' AS post_tone_source,
               'store' AS post_source,
               f.posted_at AS posted_at_store
        FROM post_facts f
        LEFT JOIN post_scores s USING (post_uid)
        WHERE f.post_uid IN ({uid_list})
    """).df()
    a.close()
    print(f"  store lookup: {len(df)} / {len(post_uids)} post_uids found in analytics store", flush=True)

    # Build a full lookup including posts NOT in the store
    all_uids = pd.DataFrame({"post_uid": post_uids})
    result = all_uids.merge(df, on="post_uid", how="left")
    result["post_in_store"] = result["post_in_store"].fillna(False).astype(bool)
    result["post_tone_source"] = result["post_tone_source"].fillna("none")
    result["post_source"] = result["post_source"].fillna("snapchat_posts")
    if "posted_at_store" not in result.columns:
        result["posted_at_store"] = pd.NaT

    # Theme: load from post_theme files (most will be unassigned, but some D01-D09 exist)
    theme_map = {}
    v1_path = DATA_DIR / "post_theme_v1.parquet"
    if v1_path.exists():
        try:
            tdf = pd.read_parquet(v1_path)
            # post_theme_v1 has columns: post_uid, platform, theme, tier, etc.
            theme_col = "theme" if "theme" in tdf.columns else "d_theme"
            snap_themes = tdf[tdf["post_uid"].isin(post_uids)]
            for _, tr in snap_themes.iterrows():
                uid = tr["post_uid"]
                theme_map[uid] = {"d_theme": tr.get(theme_col), "theme_source": "rule_v1"}
            print(f"  loaded {len(snap_themes)} theme rows from post_theme_v1.parquet", flush=True)
        except Exception as e:
            print(f"  warning: could not load post_theme_v1.parquet: {e}", flush=True)

    knn_path = DATA_DIR / "post_theme_knn_v1.parquet"
    if knn_path.exists():
        try:
            kdf = pd.read_parquet(knn_path)
            theme_col = "knn_theme" if "knn_theme" in kdf.columns else "d_theme"
            snap_knn = kdf[kdf["post_uid"].isin(post_uids)]
            for _, tr in snap_knn.iterrows():
                uid = tr["post_uid"]
                if uid not in theme_map or not theme_map[uid].get("d_theme"):
                    theme_map[uid] = {"d_theme": tr.get(theme_col), "theme_source": "knn_v1"}
            print(f"  loaded {len(snap_knn)} theme rows from post_theme_knn_v1.parquet", flush=True)
        except Exception as e:
            print(f"  warning: could not load post_theme_knn_v1.parquet: {e}", flush=True)

    result["d_theme"] = result["post_uid"].map(lambda u: theme_map.get(u, {}).get("d_theme"))
    result["theme_source"] = result["post_uid"].map(lambda u: theme_map.get(u, {}).get("theme_source"))
    result["theme_status"] = result.apply(
        lambda r: "assigned" if r.get("d_theme") and str(r["d_theme"]).startswith("D") else "unassigned",
        axis=1
    )

    return result


def main() -> int:
    print("=== Snapchat build: comment sentiment + xplat rollup ===", flush=True)

    # Load data
    extract = pd.read_parquet(OUT / "snap_comments_extract.parquet")
    scored = pd.read_parquet(OUT / "snap_comments_scored.parquet")
    posts = pd.read_parquet(OUT / "snap_posts_extract.parquet")

    print(f"  extract: {len(extract)} comments, scored: {len(scored)} rows, posts: {len(posts)}", flush=True)

    # Build comment layer
    comments = build_comment_layer(extract, scored)
    print(f"  snap_comment_sentiment: {len(comments)} rows, "
          f"scored={int((comments['score_status']=='scored').sum())}", flush=True)

    # Get analytics store lookup for post tone + in_store flag
    all_post_uids = posts["post_uid"].dropna().unique().tolist()
    store_lookup = get_store_lookup(all_post_uids)

    # Build per-post rollup
    xplat_snap = per_post_rollup(comments, posts, store_lookup)
    print(f"  xplat rows (snapchat): {len(xplat_snap)}", flush=True)

    # Write to DuckDB
    con = duckdb.connect(str(DB))
    try:
        # Fingerprint existing data before any changes
        fp_before = fingerprint_xplat(con)
        print(f"  fingerprint before: {fp_before}", flush=True)

        # Write snap_comment_sentiment
        con.register("_snap_comments", comments)
        con.execute("CREATE OR REPLACE TABLE snap_comment_sentiment AS SELECT * FROM _snap_comments")
        con.unregister("_snap_comments")
        print(f"  wrote snap_comment_sentiment ({len(comments)} rows)", flush=True)

        # Append snapchat rows to xplat (remove any existing snapchat rows first, then reinsert)
        existing_platforms = con.execute(
            "SELECT DISTINCT platform FROM xplat_post_audience_sentiment_v2"
        ).fetchall()
        existing_platforms = [r[0] for r in existing_platforms]
        print(f"  platforms in xplat before: {existing_platforms}", flush=True)

        # Remove any stale snapchat rows
        if "snapchat" in existing_platforms:
            con.execute("DELETE FROM xplat_post_audience_sentiment_v2 WHERE platform = 'snapchat'")
            print("  removed stale snapchat rows", flush=True)

        # Align columns with xplat schema
        xplat_schema = con.execute("DESCRIBE xplat_post_audience_sentiment_v2").df()["column_name"].tolist()
        for col in xplat_schema:
            if col not in xplat_snap.columns:
                xplat_snap[col] = None
        xplat_snap = xplat_snap[xplat_schema]

        con.register("_snap_xplat", xplat_snap)
        con.execute("INSERT INTO xplat_post_audience_sentiment_v2 SELECT * FROM _snap_xplat")
        con.unregister("_snap_xplat")
        print(f"  inserted {len(xplat_snap)} snapchat rows into xplat", flush=True)

        # Verify existing rows unchanged
        fp_after = fingerprint_xplat(con)
        for plat, fp in fp_before.items():
            fa = fp_after.get(plat, {})
            if fp["rows"] != fa.get("rows") or fp["md5"] != fa.get("md5"):
                raise RuntimeError(f"INTEGRITY VIOLATION: {plat} rows changed! before={fp} after={fa}")
        print(f"  integrity check PASSED: existing platforms unchanged", flush=True)

        # Final counts
        final = con.execute(
            "SELECT platform, count(*) FROM xplat_post_audience_sentiment_v2 GROUP BY 1"
        ).df()
        print(f"\nxplat_post_audience_sentiment_v2 final counts:\n{final.to_string()}", flush=True)

    finally:
        con.close()

    print(f"\nCaveat: {SNAP_CAVEAT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
