#!/usr/bin/env python3
"""
Audience sentiment, Phase 1 (YouTube), step 3: build the three separate sentiment layers + coverage + validation.

Inputs (all local): data/audience/yt_comments_extract.parquet, yt_videos_extract.parquet, extract_meta.json,
yt_comments_scored.parquet, score_meta.json, and data/analytics.duckdb (READ ONLY, for the existing post scores).
No production access. Writes data/audience_sentiment.duckdb (its own file, so it never needs the lock on the
metrics or geo databases):

    post_sentiment     existing post tone (tone_score, copied unchanged from post_scores) for the parent videos
    comment_sentiment  top-level comments  (label, p_negative/p_neutral/p_positive, tone_score, parents, status)
    reply_sentiment    replies             (same columns + parent_comment_id / parent_comment_uid)
    comment_text       the text that was scored, kept LOCAL ONLY (not delivered)
    sentiment_summary, coverage_report, validation_checks, run_meta

Post, comment and reply sentiment are never combined here. Every extracted row appears in exactly one of
comment_sentiment / reply_sentiment, with score_status 'scored', 'failed' or 'not_scored' (reason in text_status).
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
from lib.audience_text import NOT_SCORED
from lib.config import DATA_DIR, DUCKDB_PATH

OUT = DATA_DIR / "audience"
DB = DATA_DIR / "audience_sentiment.duckdb"
SENT_COLS = ["comment_uid", "comment_id", "platform", "video_id", "post_uid", "post_in_store", "posted_at", "scraped_at",
             "like_count", "author_hash", "text_source", "text_len", "text_status", "flags", "dup_text_n", "dup_text_repeat",
             "score_status", "sentiment_label", "p_negative", "p_neutral", "p_positive", "tone_score", "model", "scored_at"]
REPLY_EXTRA = ["parent_comment_id", "parent_comment_uid", "parent_in_extract", "parent_same_video"]


def check(rows: list, name: str, expected, observed, ok: bool, level: str = "FAIL", note: str = "") -> None:
    rows.append({"check": name, "expected": str(expected), "observed": str(observed),
                 "status": "PASS" if ok else level, "note": note})


def main() -> int:
    ex = pd.read_parquet(OUT / "yt_comments_extract.parquet")
    sc = pd.read_parquet(OUT / "yt_comments_scored.parquet")
    vid = pd.read_parquet(OUT / "yt_videos_extract.parquet")
    meta = json.loads((OUT / "extract_meta.json").read_text())
    smeta = json.loads((OUT / "score_meta.json").read_text())
    prod = meta["production"]
    ex["posted_at"] = ex["published_at"]          # same column name as post_facts.posted_at

    # ---- parent posts: do they exist in the post store, and what is their existing post tone
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    posts = a.execute("""
        SELECT f.post_uid, f.platform, f.native_id AS video_id, s.tone_score, s.tone_valid, s.tone_model, s.scored_at AS post_scored_at
        FROM post_facts f LEFT JOIN post_scores s USING (post_uid) WHERE f.platform = 'youtube_video'
    """).df()
    a.close()
    in_store = set(posts["post_uid"])
    ex["post_in_store"] = ex["post_uid"].isin(in_store)

    # ---- merge scores
    sc = sc.rename(columns={"label": "sentiment_label"})
    m = ex.merge(sc, on="comment_uid", how="left")
    m["score_status"] = np.where(m["score_status"].notna(), m["score_status"],
                                 np.where(m["text_status"] == "ok", "failed", "not_scored"))   # ok-but-missing = failed, never silent
    m["dup_text_repeat"] = m["dup_text_repeat"].fillna(False).astype(bool)

    comments = m[m["layer"] == "comment"][SENT_COLS].reset_index(drop=True)
    replies = m[m["layer"] == "reply"][SENT_COLS + REPLY_EXTRA].reset_index(drop=True)
    text = m[["comment_uid", "layer", "text_used", "text_source", "comment_text", "translated_comment_text"]]

    # ---- post layer (existing scores, untouched) + relationship / coverage columns
    pl = vid.merge(posts[["post_uid", "tone_score", "tone_valid", "tone_model", "post_scored_at"]], on="post_uid", how="left")
    pl["post_in_store"] = pl["post_uid"].isin(in_store)
    pl["platform"] = "youtube_video"
    pl["n_comments_extracted"] = pl["n_comments_extracted"].fillna(0).astype(int)
    pl["n_replies_extracted"] = pl["n_replies_extracted"].fillna(0).astype(int)
    post_sentiment = pl[["post_uid", "platform", "video_id", "post_in_store", "tone_score", "tone_valid", "tone_model",
                         "post_scored_at", "video_comment_count_reported", "n_comments_extracted", "n_replies_extracted"]]

    # ---- summary
    def dist(df: pd.DataFrame, layer: str) -> list[dict]:
        out = []
        sc_ = df[df["score_status"] == "scored"]
        for lab in ("negative", "neutral", "positive"):
            n = int((sc_["sentiment_label"] == lab).sum())
            out.append({"layer": layer, "group": "all", "label": lab, "n": n, "share_of_scored": n / max(len(sc_), 1)})
        out.append({"layer": layer, "group": "all", "label": "(mean tone_score)", "n": len(sc_), "share_of_scored": float(sc_["tone_score"].mean())})
        for g, s in sc_.groupby("text_source"):
            for lab in ("negative", "neutral", "positive"):
                out.append({"layer": layer, "group": f"text_source={g}", "label": lab, "n": int((s["sentiment_label"] == lab).sum()),
                            "share_of_scored": float((s["sentiment_label"] == lab).mean())})
        for fl in ("emoji_only", "very_short", "truncated", "has_url"):
            s = sc_[sc_["flags"].str.contains(fl, na=False)]
            for lab in ("negative", "neutral", "positive"):
                out.append({"layer": layer, "group": f"flag={fl}", "label": lab, "n": int((s["sentiment_label"] == lab).sum()),
                            "share_of_scored": float((s["sentiment_label"] == lab).mean()) if len(s) else None})
        s = sc_[~sc_["dup_text_repeat"]]
        for lab in ("negative", "neutral", "positive"):
            out.append({"layer": layer, "group": "excluding_repeated_text", "label": lab, "n": int((s["sentiment_label"] == lab).sum()),
                        "share_of_scored": float((s["sentiment_label"] == lab).mean())})
        return out
    summary = pd.DataFrame(dist(comments, "comment") + dist(replies, "reply"))

    # ---- coverage vs production (same snapshot)
    def cov(label, prod_n, got_n, note=""):
        return {"item": label, "production_snapshot": prod_n, "extracted_or_stored": got_n,
                "difference": None if prod_n is None else int(got_n - prod_n), "note": note}
    n_ok = int((ex["text_status"] == "ok").sum())
    coverage = pd.DataFrame([
        cov("youtube_comments rows (all)", prod["rows"], len(ex), "comments + replies, one snapshot"),
        cov("  of which replies", prod["replies"], int((ex["layer"] == "reply").sum())),
        cov("  of which top-level comments", prod["rows"] - prod["replies"], int((ex["layer"] == "comment").sum())),
        cov("rows with original text", prod["rows_with_original_text"], int((ex["comment_text"].fillna("") != "").sum())),
        cov("rows with translated text", prod["rows_with_translation"], int((ex["translated_comment_text"].fillna("") != "").sum())),
        cov("distinct videos with comments", prod["distinct_videos"], int(ex["video_id"].nunique())),
        cov("rows sent to the model (text_status=ok)", None, n_ok),
        cov("rows scored", None, int((m["score_status"] == "scored").sum())),
        cov("rows not scored (empty/deleted/url-only/symbols)", None, int((m["score_status"] == "not_scored").sum())),
        cov("rows failed", None, int((m["score_status"] == "failed").sum())),
        cov("comments of videos whose post is in post_scores", None, int(m["post_in_store"].sum()), "parent post present in the analytics store"),
        cov("comments whose parent post is NOT in the post store", None, int((~m["post_in_store"]).sum()), "store snapshot is older than production"),
        cov("analytics.duckdb comment_facts (old store) rows", None, None, "comment_facts predates this extract"),
    ])
    try:
        a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        coverage.loc[coverage["item"].str.contains("comment_facts"), "extracted_or_stored"] = a.execute("select count(*) from comment_facts").fetchone()[0]
        a.close()
    except Exception:
        pass

    # ---- validation checks
    ch: list[dict] = []
    check(ch, "extract rows == production rows (same snapshot)", prod["rows"], len(ex) + meta["extract"]["duplicate_comment_id_removed"], len(ex) + meta["extract"]["duplicate_comment_id_removed"] == prod["rows"])
    check(ch, "rows with original text == production", prod["rows_with_original_text"], int((ex["comment_text"].fillna("") != "").sum()), int((ex["comment_text"].fillna("") != "").sum()) == prod["rows_with_original_text"])
    check(ch, "reply rows == production replies", prod["replies"], len(replies), len(replies) == prod["replies"])
    check(ch, "comment + reply layers == all rows", len(ex), len(comments) + len(replies), len(comments) + len(replies) == len(ex))
    check(ch, "no comment_uid in both layers", 0, len(set(comments["comment_uid"]) & set(replies["comment_uid"])), not (set(comments["comment_uid"]) & set(replies["comment_uid"])))
    for c in ("comment_id", "comment_uid", "video_id", "post_uid"):
        check(ch, f"null {c}", 0, int(ex[c].isna().sum()), int(ex[c].isna().sum()) == 0)
    check(ch, "duplicate comment_id", 0, int(ex["comment_id"].duplicated().sum()), not ex["comment_id"].duplicated().any())
    check(ch, "duplicate comment_uid", 0, int(ex["comment_uid"].duplicated().sum()), not ex["comment_uid"].duplicated().any())
    check(ch, "replies without a parent id", 0, int(replies["parent_comment_id"].isna().sum()), int(replies["parent_comment_id"].isna().sum()) == 0)
    check(ch, "replies whose parent is not in the extract", 0, int((~replies["parent_in_extract"].astype(bool)).sum()), int((~replies["parent_in_extract"].astype(bool)).sum()) == 0, "WARN", "orphan replies keep their row, flagged")
    check(ch, "replies whose parent belongs to another video", 0, int((replies["parent_same_video"] == False).sum()), int((replies["parent_same_video"] == False).sum()) == 0, "WARN")  # noqa: E712
    check(ch, "top-level comments carrying a parent id", 0, int(m[(m["layer"] == "comment")]["parent_comment_id"].notna().sum()), int(m[(m["layer"] == "comment")]["parent_comment_id"].notna().sum()) == 0)
    stc = ex["text_status"].value_counts().to_dict()
    check(ch, "every row has exactly one text_status", len(ex), sum(stc.values()), sum(stc.values()) == len(ex), note=str(stc))
    check(ch, "scored + failed == rows with text_status ok", n_ok, int(((m["score_status"] == "scored") | (m["score_status"] == "failed")).sum()), int(((m["score_status"] == "scored") | (m["score_status"] == "failed")).sum()) == n_ok)
    check(ch, "not_scored == rows with a non-ok status", int(len(ex) - n_ok), int((m["score_status"] == "not_scored").sum()), int((m["score_status"] == "not_scored").sum()) == len(ex) - n_ok)
    check(ch, "failed predictions", 0, int((m["score_status"] == "failed").sum()), int((m["score_status"] == "failed").sum()) == 0, "WARN", "failed rows are kept with score_status='failed'")
    s_ = m[m["score_status"] == "scored"]
    ps = (s_["p_negative"] + s_["p_neutral"] + s_["p_positive"] - 1).abs().max()
    check(ch, "probabilities sum to 1 (max abs error)", "< 1e-3", f"{ps:.2e}", ps < 1e-3)
    am = s_[["p_negative", "p_neutral", "p_positive"]].to_numpy().argmax(axis=1)
    lab = np.array(["negative", "neutral", "positive"])[am]
    check(ch, "label is the argmax of the probabilities (mismatches)", 0, int((lab != s_["sentiment_label"].to_numpy()).sum()), int((lab != s_["sentiment_label"].to_numpy()).sum()) == 0)
    td = (s_["tone_score"] - (s_["p_positive"] - s_["p_negative"])).abs().max()
    check(ch, "tone_score == p_positive - p_negative (post formula)", "< 1e-4", f"{td:.2e}", td < 1e-4)
    d = smeta.get("determinism_check", {})
    check(ch, "re-scoring 200 rows reproduces the stored scores", "max diff < 1e-3, labels equal", f"{d}", bool(d) and d["max_abs_tone_diff"] < 1e-3 and d["labels_equal"] == d["n"], "WARN")
    for layer, df in (("comment", comments), ("reply", replies)):
        s = df[df["score_status"] == "scored"]
        sh = s["sentiment_label"].value_counts(normalize=True)
        check(ch, f"{layer}: all three labels present, none above 90%", "3 labels, max < 0.90", f"{sh.round(3).to_dict()}", len(sh) == 3 and sh.max() < 0.90, "WARN")
    check(ch, "scored rows have a model name and timestamp", 0, int(s_["model"].isna().sum() + s_["scored_at"].isna().sum()), int(s_["model"].isna().sum() + s_["scored_at"].isna().sum()) == 0)
    check(ch, "posted_at missing", 0, int(ex["posted_at"].isna().sum()), int(ex["posted_at"].isna().sum()) == 0, "WARN")
    pm = ex.loc[~ex["post_in_store"], "video_id"].nunique()
    check(ch, "parent videos present in the post store", 0, pm, pm == 0, "WARN", f"{pm} videos have comments in production but no post row in the analytics store (older snapshot)")
    rep = post_sentiment["video_comment_count_reported"]
    r = (post_sentiment["n_comments_extracted"] + post_sentiment["n_replies_extracted"])[rep.notna() & (rep > 0)] / rep[rep.notna() & (rep > 0)]
    check(ch, "scraped comments vs YouTube-reported comment count per video (median ratio, info)", "info", f"median {r.median():.2f}", True, "INFO", "reported counts include comments never scraped; extracted/reported shows how much of each video's discussion we hold")
    validation = pd.DataFrame(ch)

    run_meta = pd.DataFrame([{"built_at": datetime.now(timezone.utc).isoformat(), "snapshot_ts": meta["snapshot_ts"], "model": smeta["model"],
                              "device": smeta["device"], "phase": 1, "platform": "youtube", "rows": len(ex),
                              "text_used": "COALESCE(translated_comment_text, comment_text), capped at 2000 chars (same as posts)",
                              "tone": "P(positive) - P(negative), same formula as post_scores.tone_score"}])

    con = duckdb.connect(str(DB))
    for name, df in (("post_sentiment", post_sentiment), ("comment_sentiment", comments), ("reply_sentiment", replies),
                     ("comment_text", text), ("sentiment_summary", summary), ("coverage_report", coverage),
                     ("validation_checks", validation), ("run_meta", run_meta)):
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
        con.unregister("_t")
    con.close()

    print(validation[["check", "status", "observed"]].to_string(max_colwidth=90))
    print("\nstatus counts:", validation["status"].value_counts().to_dict())
    print("\nCoverage:\n", coverage.to_string())
    print("\nSummary (all):\n", summary[summary["group"] == "all"].to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
