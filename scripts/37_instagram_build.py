#!/usr/bin/env python3
"""
Audience sentiment, Phase 3 (Instagram), step 3: build the Instagram layers, coverage, validation, per-post roll-ups
and the common cross-platform tables.

Inputs (local): data/audience/ig_comments_extract.parquet, ig_posts_extract.parquet, ig_comments_scored.parquet,
extract_meta_ig.json, score_meta_ig.json, and data/analytics.duckdb (READ ONLY, existing post tone). No production access.
Adds NEW tables to data/audience_sentiment.duckdb; every YouTube table is left alone (fingerprinted before and after):

    ig_post_sentiment            post layer: one row per distinct Instagram post_url (existing post tone, copies, reported count)
    ig_comment_sentiment         top-level comments   (label, 3 probabilities, tone_score, parents, status)
    ig_reply_sentiment           replies              (same columns + parent links)
    ig_comment_text              the text that was scored, LOCAL ONLY
    ig_sentiment_summary, ig_coverage_report, ig_validation_checks, ig_run_meta
    ig_post_audience_sentiment, ig_audience_rollup, ig_audience_rollup_validation   (same logic as YouTube, scripts/35)
    xplat_post_audience_sentiment, xplat_audience_rollup   YouTube + Instagram in one common schema (platform column)

The unit of an Instagram post is the distinct post_url: production repeats each post up to 501 times, so rows are never
counted as posts. Comments and replies stay in separate tables; post, comment and reply sentiment are never merged.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.config import DATA_DIR, DUCKDB_PATH

OUT = DATA_DIR / "audience"
DB = DATA_DIR / "audience_sentiment.duckdb"
YT_TABLES = ("comment_sentiment", "reply_sentiment", "post_sentiment", "post_audience_sentiment", "audience_rollup")

spec35 = importlib.util.spec_from_file_location("audience_rollup_35", Path(__file__).resolve().parent / "35_audience_rollup.py")
r35 = importlib.util.module_from_spec(spec35)
sys.modules["audience_rollup_35"] = r35          # dataclasses need the module registered
spec35.loader.exec_module(r35)

SENT_COLS = ["comment_uid", "comment_id", "platform", "post_native_id", "post_url", "post_uid", "post_in_store", "posted_at", "scraped_at",
             "like_count", "author_hash", "text_source", "text_len", "text_status", "flags", "dup_text_n", "dup_text_repeat", "n_source_rows",
             "shape", "depth", "score_status", "sentiment_label", "p_negative", "p_neutral", "p_positive", "tone_score", "model", "scored_at"]
REPLY_EXTRA = ["parent_comment_id", "parent_comment_uid", "parent_in_extract", "parent_same_post", "declared_parent_differs_from_nesting"]
SPEC_IG = r35.Spec(platform="instagram", comment_table="ig_comment_sentiment", reply_table="ig_reply_sentiment", post_table="ig_post_sentiment",
                   out_table="ig_post_audience_sentiment", rollup_table="ig_audience_rollup", validation_table="ig_audience_rollup_validation",
                   id_col="post_native_id", reported_col="post_comment_count_reported", universe_from_store=False,
                   extra_cols=("post_url", "n_source_rows"), date_col="post_posted_at",
                   protected=("ig_comment_sentiment", "ig_reply_sentiment", "ig_post_sentiment"))


def put(con, name: str, df: pd.DataFrame) -> None:
    con.register("_t", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
    con.unregister("_t")


def chk(rows, name, expected, observed, ok, level="FAIL", note=""):
    rows.append({"check": name, "expected": str(expected), "observed": str(observed), "status": "PASS" if ok else level, "note": note})


def main() -> int:
    t0 = time.time()
    ex = pd.read_parquet(OUT / "ig_comments_extract.parquet")
    sc = pd.read_parquet(OUT / "ig_comments_scored.parquet").rename(columns={"label": "sentiment_label"})
    posts = pd.read_parquet(OUT / "ig_posts_extract.parquet")
    meta = json.loads((OUT / "extract_meta_ig.json").read_text())
    smeta = json.loads((OUT / "score_meta_ig.json").read_text())
    prod, xst = meta["production"], meta["extract"]

    con = duckdb.connect(str(DB))
    yt_before = r35.fingerprint(con, ("comment_sentiment", "reply_sentiment", "post_sentiment"))
    yt_counts_before = {t: con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0] for t in YT_TABLES}

    # ---- merge scores; every extracted row lands in exactly one layer table with a score_status
    pn = posts.set_index("post_url")["post_native_id"]
    ex["post_native_id"] = ex["post_url"].map(pn)
    ex["parent_same_post"] = np.where(ex["layer"] == "reply", ex["parent_in_extract"], None)
    m = ex.merge(sc, on="comment_uid", how="left")
    ok_mask = m["text_status"] == "ok"
    m["score_status"] = np.where(m["score_status"].notna(), m["score_status"], np.where(ok_mask, "failed", "not_scored"))
    m["dup_text_repeat"] = m["dup_text_repeat"].fillna(False).astype(bool)
    comments = m[m["layer"] == "comment"][SENT_COLS].reset_index(drop=True)
    replies = m[m["layer"] == "reply"][SENT_COLS + REPLY_EXTRA].reset_index(drop=True)
    text = m[["comment_uid", "layer", "text_used", "text_source", "item_status", "raw"]]

    # ---- post layer for Instagram: the existing post tone for each distinct post (NULL where the post pipeline has none)
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    st = a.execute("SELECT post_uid, tone_score, tone_valid, tone_model, scored_at AS post_scored_at FROM post_scores WHERE platform='instagram'").df()
    a.close()
    pl = posts.merge(st, on="post_uid", how="left")
    ok_t = pl["tone_valid"].fillna(False).astype(bool) & pl["tone_score"].notna()
    ig_post = pd.DataFrame({
        "post_uid": pl["post_uid"], "platform": "instagram", "post_native_id": pl["post_native_id"], "post_url": pl["post_url"],
        "post_in_store": pl["post_in_store"], "n_source_rows": pl["n_rows"].astype(int), "n_rows_with_comments": pl["n_rows_with_comments"].astype(int),
        "post_comment_count_reported": pl["reported_comment_count_max"], "post_likes_max": pl["likes_count_max"], "post_posted_at": pl["posted_at"],
        "tone_score": pl["tone_score"].where(ok_t), "tone_valid": ok_t, "tone_model": pl["tone_model"].where(ok_t), "post_scored_at": pl["post_scored_at"],
        "n_comments_extracted": pl["n_comments_extracted"], "n_replies_extracted": pl["n_replies_extracted"]})

    # ---- summary
    def dist(df, layer):
        out, s_ = [], df[df["score_status"] == "scored"]
        for lab in ("negative", "neutral", "positive"):
            n = int((s_["sentiment_label"] == lab).sum())
            out.append({"layer": layer, "group": "all", "label": lab, "n": n, "share_of_scored": n / max(len(s_), 1)})
        out.append({"layer": layer, "group": "all", "label": "(mean tone_score)", "n": len(s_), "share_of_scored": float(s_["tone_score"].mean())})
        for fl in ("emoji_only", "very_short", "truncated", "has_url", "mention_only"):
            s = s_[s_["flags"].str.contains(fl, na=False)]
            for lab in ("negative", "neutral", "positive"):
                out.append({"layer": layer, "group": f"flag={fl}", "label": lab, "n": int((s["sentiment_label"] == lab).sum()),
                            "share_of_scored": float((s["sentiment_label"] == lab).mean()) if len(s) else None})
        s = s_[~s_["dup_text_repeat"]]
        for lab in ("negative", "neutral", "positive"):
            out.append({"layer": layer, "group": "excluding_repeated_text", "label": lab, "n": int((s["sentiment_label"] == lab).sum()),
                        "share_of_scored": float((s["sentiment_label"] == lab).mean())})
        for shp, s in s_.groupby("shape"):
            for lab in ("negative", "neutral", "positive"):
                out.append({"layer": layer, "group": f"shape={shp}", "label": lab, "n": int((s["sentiment_label"] == lab).sum()), "share_of_scored": float((s["sentiment_label"] == lab).mean())})
        return out
    summary = pd.DataFrame(dist(comments, "comment") + dist(replies, "reply"))

    # ---- coverage against production (one snapshot)
    def cov(item, p, got, note=""):
        return {"item": item, "production_snapshot": p, "extracted_or_stored": got, "difference": None if p is None else int(got - p), "note": note}
    n_ok = int(ok_mask.sum())
    coverage = pd.DataFrame([
        cov("instagram_posts rows (all, with copies)", prod["rows"], int(posts["n_rows"].sum()), "every production row maps to a post_url"),
        cov("distinct post_url", prod["distinct_post_urls"], int(posts["post_url"].nunique()), "unit of analysis: 959k rows are copies of ~15.9k posts"),
        cov("rows with a non-empty comments_data", prod["rows_with_comments"], int(posts["n_rows_with_comments"].sum())),
        cov("distinct post_url with comments", prod["distinct_urls_with_comments"], int((ex.groupby("post_url").size() > 0).sum())),
        cov("top-level elements (counted with copies)", prod["top_level_elements_with_copies"], xst["elements_with_copies"]["top"], "distinct payload variants x rows carrying them"),
        cov("nested reply elements (counted with copies)", prod["nested_reply_elements_with_copies"], xst["elements_with_copies"]["nested"]),
        cov("distinct (post, comment_id) top-level + nested", prod["top_level_distinct_post_comment_ids"] + prod["nested_reply_distinct_post_comment_ids"], len(ex), "each distinct comment or reply kept once"),
        cov("  of which comments (incl. top-level items with a parent id counted as replies)", prod["top_level_distinct_post_comment_ids"] - xst["shape_counts"].get("top_level_with_parent", 0), len(comments)),
        cov("  of which replies (nested + top-level with a parent id)", prod["nested_reply_distinct_post_comment_ids"] + xst["shape_counts"].get("top_level_with_parent", 0), len(replies)),
        cov("rows sent to the model (text_status=ok)", None, n_ok), cov("rows scored", None, int((m["score_status"] == "scored").sum())),
        cov("rows not scored (empty / URL-only / symbols-only / malformed)", None, int((m["score_status"] == "not_scored").sum())),
        cov("rows failed", None, int((m["score_status"] == "failed").sum())),
        cov("posts whose post row is in the analytics store", None, int(posts["post_in_store"].sum()), "store snapshot is older than production"),
        cov("posts with comments whose post row is NOT in the store", None, int(((~posts["post_in_store"]) & ((posts["n_comments_extracted"] + posts["n_replies_extracted"]) > 0)).sum())),
    ])

    # ---- validation of the Instagram layers
    ch: list[dict] = []
    for k, (a_, b_) in xst["reconciliation"].items():
        chk(ch, k, a_, b_, a_ == b_)
    chk(ch, "distinct (post, comment_id) in production == extracted rows", prod["top_level_distinct_post_comment_ids"] + prod["nested_reply_distinct_post_comment_ids"], len(ex),
        prod["top_level_distinct_post_comment_ids"] + prod["nested_reply_distinct_post_comment_ids"] == len(ex), note="malformed items without an id are counted in the 'top-level' distinct figure as NULL ids")
    chk(ch, "rows with a non-empty comments_data == production", prod["rows_with_comments"], int(posts["n_rows_with_comments"].sum()), prod["rows_with_comments"] == int(posts["n_rows_with_comments"].sum()))
    chk(ch, "posts with comments == production", prod["distinct_urls_with_comments"], int((ex.groupby("post_url").size() > 0).sum()), prod["distinct_urls_with_comments"] == int((ex.groupby("post_url").size() > 0).sum()))
    chk(ch, "comment + reply layers == all rows", len(ex), len(comments) + len(replies), len(comments) + len(replies) == len(ex))
    chk(ch, "no comment_uid in both layers", 0, len(set(comments["comment_uid"]) & set(replies["comment_uid"])), not (set(comments["comment_uid"]) & set(replies["comment_uid"])))
    n_mal = int((ex["item_status"] == "malformed_non_comment").sum())
    chk(ch, "null comment_id only on malformed items (kept, not scored)", n_mal, int(ex["comment_id"].isna().sum()), int(ex["comment_id"].isna().sum()) == n_mal, note=f"{n_mal} malformed items")
    for c in ("comment_uid", "post_uid", "post_url", "post_native_id"):
        chk(ch, f"null {c}", 0, int(ex[c].isna().sum()), int(ex[c].isna().sum()) == 0)
    chk(ch, "duplicate comment_uid", 0, int(ex["comment_uid"].duplicated().sum()), not ex["comment_uid"].duplicated().any())
    d2 = ex[ex["comment_id"].notna()].duplicated(["post_url", "comment_id"]).sum()
    chk(ch, "duplicate (post_url, comment_id)", 0, int(d2), int(d2) == 0)
    xp = ex[ex["comment_id"].notna()].groupby("comment_id")["post_url"].nunique()
    chk(ch, "comment_id used on more than one post", 0, int((xp > 1).sum()), int((xp > 1).sum()) == 0, "WARN")
    chk(ch, "posts: every comment's post_uid is in ig_post_sentiment", 0, int((~ex["post_uid"].isin(ig_post["post_uid"])).sum()), ex["post_uid"].isin(ig_post["post_uid"]).all())
    chk(ch, "ig_post_sentiment: duplicate post_uid / post_url", 0, int(ig_post["post_uid"].duplicated().sum() + ig_post["post_url"].duplicated().sum()), not (ig_post["post_uid"].duplicated().any() or ig_post["post_url"].duplicated().any()))
    n_null_url = int(posts["post_url"].isna().sum())            # production has one row without a post_url: kept as its own post, never dropped
    chk(ch, "ig_post_sentiment covers every production post_url (+ rows without a url)", prod["distinct_post_urls"] + n_null_url, len(ig_post), len(ig_post) == prod["distinct_post_urls"] + n_null_url, note=f"{n_null_url} production row(s) have a NULL post_url")
    rp = replies
    chk(ch, "replies without a parent id", 0, int(rp["parent_comment_id"].isna().sum()), int(rp["parent_comment_id"].isna().sum()) == 0)
    orphans = int((~rp["parent_in_extract"].astype(bool)).sum())
    chk(ch, "replies whose parent comment is not in the post's extract (kept, flagged)", 0, orphans, orphans == 0, "WARN", "these are top-level objects that name a parent that Instagram did not return; layer = reply, parent_in_extract = false")
    chk(ch, "replies whose parent is itself a reply", 0, int((rp["parent_comment_id"].isin(replies["comment_id"])).sum()), not rp["parent_comment_id"].isin(replies["comment_id"]).any())
    mism = int(rp["declared_parent_differs_from_nesting"].fillna(False).sum())
    chk(ch, "nested replies whose declared parent differs from the comment they sit under", 0, mism, mism == 0, "WARN")
    chk(ch, "top-level comments carrying a parent id", 0, int(m[(m["layer"] == "comment")]["parent_comment_id"].notna().sum()), int(m[(m["layer"] == "comment")]["parent_comment_id"].notna().sum()) == 0)
    stc = ex["text_status"].value_counts().to_dict()
    chk(ch, "every row has exactly one text_status", len(ex), sum(stc.values()), sum(stc.values()) == len(ex), note=str(stc))
    chk(ch, "scored + failed == rows with text_status ok", n_ok, int(m["score_status"].isin(["scored", "failed"]).sum()), int(m["score_status"].isin(["scored", "failed"]).sum()) == n_ok)
    chk(ch, "not_scored == rows with a non-ok status", int(len(ex) - n_ok), int((m["score_status"] == "not_scored").sum()), int((m["score_status"] == "not_scored").sum()) == len(ex) - n_ok)
    chk(ch, "failed predictions", 0, int((m["score_status"] == "failed").sum()), int((m["score_status"] == "failed").sum()) == 0, "WARN")
    s_ = m[m["score_status"] == "scored"]
    ps_ = (s_["p_negative"] + s_["p_neutral"] + s_["p_positive"] - 1).abs().max()
    chk(ch, "probabilities sum to 1 (max abs error)", "< 1e-3", f"{ps_:.2e}", ps_ < 1e-3)
    lab = np.array(["negative", "neutral", "positive"])[s_[["p_negative", "p_neutral", "p_positive"]].to_numpy().argmax(axis=1)]
    chk(ch, "label is the argmax of the probabilities (mismatches)", 0, int((lab != s_["sentiment_label"].to_numpy()).sum()), int((lab != s_["sentiment_label"].to_numpy()).sum()) == 0)
    td = (s_["tone_score"] - (s_["p_positive"] - s_["p_negative"])).abs().max()
    chk(ch, "tone_score == p_positive - p_negative (same formula as posts and YouTube)", "< 1e-4", f"{td:.2e}", td < 1e-4)
    d = smeta.get("determinism_check", {})
    chk(ch, "re-scoring 200 rows reproduces the stored scores", "max diff < 1e-3, labels equal", f"{d}", bool(d) and d["max_abs_tone_diff"] < 1e-3 and d["labels_equal"] == d["n"], "WARN")
    chk(ch, "same model as YouTube and posts", "cardiffnlp/twitter-xlm-roberta-base-sentiment", smeta["model"], smeta["model"] == "cardiffnlp/twitter-xlm-roberta-base-sentiment")
    for layer, df in (("comment", comments), ("reply", replies)):
        sh = df[df["score_status"] == "scored"]["sentiment_label"].value_counts(normalize=True)
        chk(ch, f"{layer}: all three labels present, none above 90%", "3 labels, max < 0.90", f"{sh.round(3).to_dict()}", len(sh) == 3 and sh.max() < 0.90, "WARN")
    chk(ch, "scored rows have a model name and timestamp", 0, int(s_["model"].isna().sum() + s_["scored_at"].isna().sum()), int(s_["model"].isna().sum() + s_["scored_at"].isna().sum()) == 0)
    chk(ch, "comment timestamp (posted_at) missing", 0, int(ex["posted_at"].isna().sum()), int(ex["posted_at"].isna().sum()) == 0, "WARN")
    chk(ch, "posts with comments but no post row in the analytics store", 0, int(((~posts["post_in_store"]) & ((posts["n_comments_extracted"] + posts["n_replies_extracted"]) > 0)).sum()),
        int(((~posts["post_in_store"]) & ((posts["n_comments_extracted"] + posts["n_replies_extracted"]) > 0)).sum()) == 0, "WARN", "post tone, theme and date stay NULL for them (never neutral)")
    chk(ch, "text is original (no translation exists in comments_data)", "all original", str(ex["text_source"].value_counts().to_dict()), set(ex["text_source"]) <= {"original", "none"}, "WARN")
    validation = pd.DataFrame(ch)

    run_meta = pd.DataFrame([{"built_at": datetime.now(timezone.utc).isoformat(), "snapshot_ts": meta["snapshot_ts"], "model": smeta["model"], "device": smeta["device"],
                              "phase": 3, "platform": "instagram", "rows": len(ex), "unit_of_post": "distinct post_url (copies in instagram_posts are not posts)",
                              "text_used": "comments_data text as scraped (no translation field exists), capped at 2000 chars (same as posts and YouTube)",
                              "tone": "P(positive) - P(negative), same formula as post_scores.tone_score"}])
    for name, df in (("ig_post_sentiment", ig_post), ("ig_comment_sentiment", comments), ("ig_reply_sentiment", replies), ("ig_comment_text", text),
                     ("ig_sentiment_summary", summary), ("ig_coverage_report", coverage), ("ig_validation_checks", validation), ("ig_run_meta", run_meta)):
        put(con, name, df)

    # ---- per-post roll-ups, same code as YouTube Phase 2
    p_ig, r_ig, v_ig = r35.run(con, SPEC_IG)

    # ---- common cross-platform tables
    yt = con.execute("SELECT * FROM post_audience_sentiment").df().rename(columns={"video_id": "post_native_id"})
    yt["post_url"] = None
    ig = p_ig.copy()
    common = [c for c in ig.columns if c in yt.columns]
    x = pd.concat([yt[common], ig[common]], ignore_index=True)
    put(con, "xplat_post_audience_sentiment", x)
    frames = []
    for plat, g in (("youtube_video", yt), ("instagram", ig), ("all", pd.concat([yt[common], ig[common]], ignore_index=True))):
        rr = r35.rollup(g)
        rr.insert(0, "platform", plat)
        frames.append(rr.rename(columns={c: c.replace("n_videos", "n_posts") for c in rr.columns if c.startswith("n_videos")}))
    put(con, "xplat_audience_rollup", pd.concat(frames, ignore_index=True))

    # ---- YouTube untouched?
    yt_after = r35.fingerprint(con, ("comment_sentiment", "reply_sentiment", "post_sentiment"))
    yt_counts_after = {t: con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0] for t in YT_TABLES}
    extra = []
    chk(extra, "YouTube Phase 1 tables unchanged by the Instagram build (row counts, ids, md5)", "identical", "identical" if yt_before == yt_after else "CHANGED", yt_before == yt_after)
    chk(extra, "YouTube Phase 2 tables unchanged (row counts)", yt_counts_before, yt_counts_after, yt_counts_before == yt_counts_after)
    xs = con.execute("SELECT platform, count(*), sum(c_rows), sum(r_rows) FROM xplat_post_audience_sentiment GROUP BY 1").fetchall()
    chk(extra, "cross-platform table keeps each platform's counts", "youtube 270,262 / 121,506; instagram " + f"{len(comments):,} / {len(replies):,}", str(xs),
        {r[0]: (r[2], r[3]) for r in xs} == {"youtube_video": (270262, 121506), "instagram": (len(comments), len(replies))})
    validation = pd.concat([validation, pd.DataFrame(extra)], ignore_index=True)
    put(con, "ig_validation_checks", validation)
    con.close()

    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 80)
    print(validation[["check", "status", "observed"]].to_string())
    print("\nstatus counts:", validation["status"].value_counts().to_dict(), "| roll-up validation:", v_ig["status"].value_counts().to_dict())
    print("\nCoverage:\n", coverage.to_string())
    print("\nSummary (all):\n", summary[summary["group"] == "all"].to_string())
    print(f"\nbuild seconds: {time.time() - t0:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
