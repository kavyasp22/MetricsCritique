#!/usr/bin/env python3
"""
Audience sentiment, Phase 2 (YouTube): per-post roll-ups of the Phase 1 comment and reply sentiment, the sentiment
gap, and roll-ups by D-theme and month.

Reads data/audience_sentiment.duckdb (Phase 1 tables, never modified), data/analytics.duckdb (READ ONLY: existing post
tone and post facts) and the stage 27/28 D-theme files. No production access, no model run. Adds NEW tables to
data/audience_sentiment.duckdb:

    post_audience_sentiment   one row per YouTube video: comment and reply metrics kept in separate columns (c_*, r_*),
                              the post's existing tone, the gaps, and coverage / data-status columns
    audience_rollup           pooled roll-ups (counts and sums, so groups can be re-summed) by D-theme, month, both, all
    audience_rollup_validation  the checks below with PASS / WARN / FAIL

Definitions (scales are the same: all tone values are P(positive) - P(negative) in [-1, 1]):
    net_sentiment      100 * (positive share - negative share) over scored rows (metrics v2's net_sentiment_score)
    gap_comment_vs_post  mean comment tone - post tone       (NULL if the post has no tone or < MIN_SCORED scored comments)
    gap_reply_vs_post    mean reply tone   - post tone       (same rule for replies)
    gap_reply_vs_comment mean reply tone - mean comment tone  (NULL unless both have >= MIN_SCORED scored rows)
Post tone that does not exist stays NULL: it is never replaced by neutral. Comments and replies are never merged.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.audience_rollup import (GAP_FLAG, MIN_SCORED, audience_gap, comment_data_status, gap_direction, net_sentiment,
                                 pct, reply_vs_comment)
from lib.config import DATA_DIR, DUCKDB_PATH

DB = DATA_DIR / "audience_sentiment.duckdb"
ROLLUP_MIN = 30            # pooled roll-up statistics are NULL below this many scored rows (counts are kept)
LAYERS = (("c", "comment_sentiment"), ("r", "reply_sentiment"))      # YouTube table names; see Spec for other platforms


@dataclass(frozen=True)
class Spec:
    """Everything platform-specific. The defaults are the YouTube Phase 2 setup, so running this file unchanged reproduces it."""
    platform: str = "youtube_video"            # post_facts.platform of the parent posts
    comment_table: str = "comment_sentiment"
    reply_table: str = "reply_sentiment"
    post_table: str = "post_sentiment"         # Phase 1 post layer: post_uid, native id, reported comment count
    out_table: str = "post_audience_sentiment"
    rollup_table: str = "audience_rollup"
    validation_table: str = "audience_rollup_validation"
    id_col: str = "video_id"                   # name of the platform's own post id column in post_table and in the output
    reported_col: str = "video_comment_count_reported"
    universe_from_store: bool = True           # YouTube: every store video plus videos with comments; others: only post_table's posts
    extra_cols: tuple = ()                     # extra post_table columns carried to the output (e.g. post_url, n_source_rows)
    date_col: str = ""                         # post_table column holding the post's own date, used when the post store has none
    enriched: bool = False                     # also read enriched_post_facts / _scores / _theme (targeted post enrichment, scripts 38-40)
    protected: tuple = ("comment_sentiment", "reply_sentiment", "post_sentiment")   # tables whose fingerprint must not change


YOUTUBE = Spec()


def fingerprint(con, tables=("comment_sentiment", "reply_sentiment", "post_sentiment")) -> dict:
    out = {}
    for t in tables:
        is_post = t.endswith("post_sentiment")
        key = "post_uid" if is_post else "comment_uid"
        extra = "coalesce(sentiment_label,'')" if not is_post else "coalesce(cast(tone_score as varchar),'')"
        r = con.execute(f"SELECT count(*), count(DISTINCT {key}), md5(string_agg({key} || '|' || {extra}, ',' ORDER BY {key})) FROM {t}").fetchone()
        out[t] = {"rows": int(r[0]), "distinct_ids": int(r[1]), "md5": r[2]}
    return out


def layer_agg(con, prefix: str, table: str) -> pd.DataFrame:
    return con.execute(f"""
        SELECT post_uid,
          count(*)                                                    AS {prefix}_rows,
          count(*) FILTER (WHERE score_status='scored')               AS {prefix}_scored,
          count(*) FILTER (WHERE score_status='not_scored')           AS {prefix}_not_scored,
          count(*) FILTER (WHERE score_status='failed')               AS {prefix}_failed,
          count(*) FILTER (WHERE score_status='scored' AND sentiment_label='negative') AS {prefix}_neg,
          count(*) FILTER (WHERE score_status='scored' AND sentiment_label='neutral')  AS {prefix}_neu,
          count(*) FILTER (WHERE score_status='scored' AND sentiment_label='positive') AS {prefix}_pos,
          coalesce(sum(tone_score) FILTER (WHERE score_status='scored'), 0.0)          AS {prefix}_tone_sum,
          coalesce(sum(tone_score * tone_score) FILTER (WHERE score_status='scored'), 0.0) AS {prefix}_tone_sumsq,
          coalesce(sum(greatest(p_negative, p_neutral, p_positive)) FILTER (WHERE score_status='scored'), 0.0) AS {prefix}_conf_sum,
          count(*) FILTER (WHERE dup_text_repeat)                     AS {prefix}_repeat_text_rows
        FROM {table} GROUP BY post_uid""").df()


def theme_table(post_uids: pd.Series, enriched_theme: pd.DataFrame = None) -> pd.DataFrame:
    t = pd.read_parquet(DATA_DIR / "post_theme_v1.parquet", columns=["post_uid", "theme", "tier"]).drop_duplicates("post_uid")
    k = pd.read_parquet(DATA_DIR / "post_theme_knn_v1.parquet", columns=["post_uid", "knn_theme"]).drop_duplicates("post_uid")
    m = pd.DataFrame({"post_uid": post_uids}).merge(t, on="post_uid", how="left").merge(k, on="post_uid", how="left")
    hyb = m["theme"].where(m["theme"].astype(str).str.match(r"D0\d"))
    knn = m["knn_theme"].where(m["knn_theme"].astype(str).str.match(r"D0\d"))
    m["d_theme"] = hyb.fillna(knn)
    m["theme_source"] = np.where(hyb.notna(), "hybrid", np.where(knn.notna(), "neighbour", None))
    m["in_theme_files"] = m["theme"].notna() | m["knn_theme"].notna()
    out = m[["post_uid", "d_theme", "theme_source", "in_theme_files"]]
    if enriched_theme is not None and len(enriched_theme):
        # posts the stage 27/28 files never saw get the targeted replay's result; posts already in the files keep theirs
        e = enriched_theme[~enriched_theme["post_uid"].isin(out.loc[out["in_theme_files"], "post_uid"])]
        e = e.assign(in_theme_files=True)[["post_uid", "d_theme", "theme_source", "in_theme_files"]]
        out = pd.concat([out[~out["post_uid"].isin(e["post_uid"])], e], ignore_index=True)
    return out


def build(con, spec: Spec = None) -> pd.DataFrame:
    spec = spec or Spec()
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    posts = a.execute(f"""
        SELECT f.post_uid, f.native_id AS video_id, f.posted_at, f.comments AS store_reported_comments,
               s.tone_score AS post_tone_score, s.tone_valid AS post_tone_valid
        FROM post_facts f LEFT JOIN post_scores s USING (post_uid) WHERE f.platform = '{spec.platform}'""").df()
    a.close()
    store_uids = set(posts["post_uid"])
    enr_theme = None
    enr_uids: set = set()
    if spec.enriched:
        ef = con.execute("SELECT post_uid, native_id AS video_id, posted_at, reported_comment_count AS store_reported_comments FROM enriched_post_facts WHERE platform = ?", [spec.platform]).df()
        es = con.execute("SELECT post_uid, tone_score AS post_tone_score, tone_valid AS post_tone_valid FROM enriched_post_scores").df()
        e = ef.merge(es, on="post_uid", how="left")
        e["post_tone_valid"] = e["post_tone_valid"].fillna(False).astype(bool)
        enr_theme = con.execute("SELECT post_uid, d_theme, theme_source FROM enriched_post_theme").df()
        enr_uids = set(e["post_uid"])
        has_valid = posts["post_tone_valid"].fillna(False).astype(bool) & posts["post_tone_score"].notna()
        fill = posts[(~has_valid) & posts["post_uid"].isin(e["post_uid"])].index           # in the store but without a valid tone: fill it
        fv = e.set_index("post_uid")
        for col in ("post_tone_score", "post_tone_valid"):
            posts.loc[fill, col] = posts.loc[fill, "post_uid"].map(fv[col])
        if "posted_at" in posts.columns:
            posts.loc[fill, "posted_at"] = posts.loc[fill, "posted_at"].fillna(posts.loc[fill, "post_uid"].map(fv["posted_at"]))
        posts.loc[fill, "store_reported_comments"] = posts.loc[fill, "store_reported_comments"].fillna(posts.loc[fill, "post_uid"].map(fv["store_reported_comments"]))
        tone_filled = set(posts.loc[fill, "post_uid"][posts.loc[fill, "post_tone_valid"].fillna(False).astype(bool)])
        new = e[~e["post_uid"].isin(store_uids)]
        posts = pd.concat([posts, new[["post_uid", "video_id", "posted_at", "store_reported_comments", "post_tone_score", "post_tone_valid"]]], ignore_index=True)
    extra = "".join(f", {c}" for c in spec.extra_cols)
    dsel = f", {spec.date_col} AS posted_at" if spec.date_col else ""
    ps = con.execute(f"SELECT post_uid, {spec.id_col} AS video_id, {spec.reported_col} AS video_comment_count_reported{extra}{dsel} FROM {spec.post_table}").df()
    ps_has_date = bool(spec.date_col)
    uni = posts.merge(ps, on="post_uid", how="outer" if spec.universe_from_store else "right", suffixes=("", "_p1"))
    uni["post_in_store"] = uni["post_uid"].isin(store_uids)
    uni["post_enriched"] = uni["post_uid"].isin(enr_uids)
    uni["video_id"] = uni["video_id"].fillna(uni["video_id_p1"])
    uni["reported_comment_count"] = uni["video_comment_count_reported"].fillna(uni["store_reported_comments"])
    if ps_has_date and "posted_at_p1" in uni.columns:                   # platforms whose Phase 1 post table carries its own date
        uni["posted_at"] = uni["posted_at"].fillna(uni["posted_at_p1"])
    uni["platform"] = spec.platform
    # post tone exists only if the post pipeline scored it; otherwise NULL (never neutral)
    ok = uni["post_tone_valid"].fillna(False).astype(bool) & uni["post_tone_score"].notna()
    uni["post_tone_available"] = ok
    uni["post_tone_score"] = uni["post_tone_score"].where(ok)
    uni = uni.drop(columns=["video_id_p1", "video_comment_count_reported", "store_reported_comments", "post_tone_valid", "posted_at_p1"], errors="ignore")

    for prefix, table in (("c", spec.comment_table), ("r", spec.reply_table)):
        uni = uni.merge(layer_agg(con, prefix, table), on="post_uid", how="left")
    for prefix, _ in LAYERS:
        for col in ("rows", "scored", "not_scored", "failed", "neg", "neu", "pos", "repeat_text_rows"):
            uni[f"{prefix}_{col}"] = uni[f"{prefix}_{col}"].fillna(0).astype(int)
        for col in ("tone_sum", "tone_sumsq", "conf_sum"):
            uni[f"{prefix}_{col}"] = uni[f"{prefix}_{col}"].fillna(0.0)

    # data status: has data / confirmed zero / known-not-collected / cannot tell
    uni["rows_total"] = uni["c_rows"] + uni["r_rows"]          # coverage only; comment and reply metrics stay separate
    uni["comment_data_status"] = [comment_data_status(n, rep) for n, rep in zip(uni["rows_total"], uni["reported_comment_count"])]
    rc = uni["reported_comment_count"]
    uni["coverage_ratio"] = np.where(rc.notna() & (rc > 0), uni["rows_total"] / rc.where(rc > 0), np.nan)

    for prefix, _ in LAYERS:
        n = uni[f"{prefix}_scored"]
        uni[f"{prefix}_neg_pct"] = [pct(x, y) for x, y in zip(uni[f"{prefix}_neg"], n)]
        uni[f"{prefix}_neu_pct"] = [pct(x, y) for x, y in zip(uni[f"{prefix}_neu"], n)]
        uni[f"{prefix}_pos_pct"] = [pct(x, y) for x, y in zip(uni[f"{prefix}_pos"], n)]
        uni[f"{prefix}_net_sentiment"] = [net_sentiment(p, q, y) for p, q, y in zip(uni[f"{prefix}_pos"], uni[f"{prefix}_neg"], n)]
        sup = n >= MIN_SCORED
        uni[f"{prefix}_supported"] = sup
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = uni[f"{prefix}_tone_sum"] / n.where(n > 0)
            var = (uni[f"{prefix}_tone_sumsq"] - uni[f"{prefix}_tone_sum"] ** 2 / n.where(n > 0)) / (n - 1).where(n > 1)
        uni[f"{prefix}_tone_mean"] = mean.where(sup)                       # NULL below minimum support
        uni[f"{prefix}_tone_sd"] = np.sqrt(var.clip(lower=0)).where(sup)
        uni[f"{prefix}_tone_se"] = (uni[f"{prefix}_tone_sd"] / np.sqrt(n.where(n > 0)))
        uni[f"{prefix}_conf_mean"] = (uni[f"{prefix}_conf_sum"] / n.where(n > 0))   # average max-probability of the label

    pt = [None if pd.isna(x) else float(x) for x in uni["post_tone_score"]]
    ct = [None if pd.isna(x) else float(x) for x in uni["c_tone_mean"]]
    rt = [None if pd.isna(x) else float(x) for x in uni["r_tone_mean"]]
    uni["gap_comment_vs_post"] = [audience_gap(p, c, n) for p, c, n in zip(pt, ct, uni["c_scored"])]
    uni["gap_reply_vs_post"] = [audience_gap(p, r, n) for p, r, n in zip(pt, rt, uni["r_scored"])]
    uni["gap_reply_vs_comment"] = [reply_vs_comment(r, nr, c, nc) for r, nr, c, nc in zip(rt, uni["r_scored"], ct, uni["c_scored"])]
    uni["gap_comment_direction"] = [gap_direction(g) for g in uni["gap_comment_vs_post"]]
    uni["gap_reply_direction"] = [gap_direction(g) for g in uni["gap_reply_vs_post"]]

    th = theme_table(uni["post_uid"], enr_theme)
    uni = uni.merge(th, on="post_uid", how="left")
    uni["post_available"] = uni["post_in_store"] | uni["post_enriched"]
    uni["theme_status"] = np.where(~uni["post_available"], "post_not_in_store", np.where(uni["d_theme"].notna(), uni["d_theme"], "unassigned"))
    if spec.enriched:
        uni["post_source"] = np.where(uni["post_in_store"], "store", np.where(uni["post_enriched"], "targeted_enrichment", None))
        uni["post_tone_source"] = np.where(uni["post_tone_available"], np.where(uni["post_uid"].isin(tone_filled) | (~uni["post_in_store"] & uni["post_enriched"]), "targeted_enrichment", "store"), None)
    uni["posted_month"] = uni["posted_at"].dt.strftime("%Y-%m").where(uni["posted_at"].notna(), "unknown")
    uni["built_at"] = datetime.now(timezone.utc)
    cols = ["post_uid", "platform", "video_id", *spec.extra_cols, "post_in_store", "posted_at", "posted_month", "d_theme", "theme_status", "theme_source",
            "post_tone_score", "post_tone_available", "reported_comment_count", "comment_data_status", "rows_total", "coverage_ratio"]
    for prefix, _ in LAYERS:
        cols += [f"{prefix}_{c}" for c in ("rows", "scored", "not_scored", "failed", "neg", "neu", "pos", "neg_pct", "neu_pct", "pos_pct",
                                           "net_sentiment", "tone_mean", "tone_sd", "tone_se", "conf_mean", "supported", "repeat_text_rows",
                                           "tone_sum", "tone_sumsq", "conf_sum")]
    cols += ["gap_comment_vs_post", "gap_reply_vs_post", "gap_reply_vs_comment", "gap_comment_direction", "gap_reply_direction", "built_at"]
    if spec.enriched:
        cols += ["post_source", "post_tone_source"]
    return uni[cols].rename(columns={"video_id": spec.id_col}).sort_values("post_uid").reset_index(drop=True)


def rollup(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    p["d_key"], p["month"] = p["theme_status"], p["posted_month"]
    p["gc_ok"] = p["gap_comment_vs_post"].notna()
    p["gr_ok"] = p["gap_reply_vs_post"].notna()
    p["gc_neg"] = (p["gap_comment_direction"] == "audience_more_negative")
    p["gc_pos"] = (p["gap_comment_direction"] == "audience_more_positive")
    p["gr_neg"] = (p["gap_reply_direction"] == "audience_more_negative")
    p["gr_pos"] = (p["gap_reply_direction"] == "audience_more_positive")
    p["has_rows"] = p["rows_total"] > 0
    p["status_known"] = p["comment_data_status"] == "not_collected_known"
    p["status_unknown"] = p["comment_data_status"] == "unknown_no_comment_data"
    p["status_zero"] = p["comment_data_status"] == "confirmed_zero"
    p["gcv"] = p["gap_comment_vs_post"].fillna(0.0)
    p["grv"] = p["gap_reply_vs_post"].fillna(0.0)
    sums = ["has_rows", "post_tone_available", "status_known", "status_unknown", "status_zero", "gc_ok", "gr_ok", "gc_neg", "gc_pos", "gr_neg", "gr_pos",
            "gcv", "grv"] + [f"{pf}_{c}" for pf in ("c", "r") for c in ("rows", "scored", "not_scored", "failed", "neg", "neu", "pos", "tone_sum", "tone_sumsq", "conf_sum")]
    frames = []
    for dim, keys in (("all", []), ("d_theme", ["d_key"]), ("month", ["month"]), ("d_theme_month", ["d_key", "month"])):
        g = p.groupby(keys, dropna=False) if keys else p.assign(_all=1).groupby("_all")
        a = g[sums].sum()
        a["n_videos"] = g.size()
        a = a.reset_index(drop=not keys)
        a["dimension"] = dim
        a["d_theme"] = a["d_key"] if "d_key" in a else "all"
        a["period"] = a["month"] if "month" in a else "all"
        frames.append(a.drop(columns=[c for c in ("d_key", "month", "_all") if c in a.columns]))
    r = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({"dimension": r["dimension"], "d_theme": r["d_theme"], "period": r["period"], "period_basis": "video_posted_month",
                        "n_videos": r["n_videos"].astype(int), "n_videos_with_comment_rows": r["has_rows"].astype(int),
                        "n_videos_with_post_tone": r["post_tone_available"].astype(int),
                        "n_videos_not_collected_known": r["status_known"].astype(int), "n_videos_unknown_no_comment_data": r["status_unknown"].astype(int),
                        "n_videos_confirmed_zero": r["status_zero"].astype(int)})
    for pf in ("c", "r"):
        for col in ("rows", "scored", "not_scored", "failed", "neg", "neu", "pos"):
            out[f"{pf}_{col}"] = r[f"{pf}_{col}"].astype(int)
        n = r[f"{pf}_scored"].where(r[f"{pf}_scored"] >= ROLLUP_MIN)               # NULL below the pooled minimum, counts kept
        out[f"{pf}_neg_pct"], out[f"{pf}_neu_pct"], out[f"{pf}_pos_pct"] = 100 * r[f"{pf}_neg"] / n, 100 * r[f"{pf}_neu"] / n, 100 * r[f"{pf}_pos"] / n
        out[f"{pf}_net_sentiment"] = 100 * (r[f"{pf}_pos"] - r[f"{pf}_neg"]) / n
        out[f"{pf}_tone_mean"] = r[f"{pf}_tone_sum"] / n
        out[f"{pf}_conf_mean"] = r[f"{pf}_conf_sum"] / n
        out[f"{pf}_tone_sum"], out[f"{pf}_tone_sumsq"], out[f"{pf}_conf_sum"] = r[f"{pf}_tone_sum"], r[f"{pf}_tone_sumsq"], r[f"{pf}_conf_sum"]
    for tag, ok, neg, pos, v in (("comment", "gc_ok", "gc_neg", "gc_pos", "gcv"), ("reply", "gr_ok", "gr_neg", "gr_pos", "grv")):
        n_ok = r[ok].where(r[ok] >= MIN_SCORED)
        out[f"n_videos_gap_{tag}"] = r[ok].astype(int)
        out[f"gap_{tag}_mean"] = r[v] / n_ok                                       # mean of per-video gaps (video-weighted)
        out[f"gap_{tag}_share_audience_more_negative"] = r[neg] / n_ok
        out[f"gap_{tag}_share_audience_more_positive"] = r[pos] / n_ok
    out["rollup_min_scored"] = ROLLUP_MIN
    out["built_at"] = datetime.now(timezone.utc)
    return out


def validate(con, p: pd.DataFrame, r: pd.DataFrame, fp_before: dict, fp_after: dict, spec: Spec = None) -> pd.DataFrame:
    spec = spec or Spec()
    LAYERS = (("c", spec.comment_table), ("r", spec.reply_table))
    idc = spec.id_col
    rows: list[dict] = []

    def chk(name, expected, observed, ok, level="FAIL", note=""):
        rows.append({"check": name, "expected": str(expected), "observed": str(observed), "status": "PASS" if ok else level, "note": note})

    # Phase 1 preserved
    chk("Phase 1 tables unchanged (row counts, ids, md5 of id|label)", fp_before, fp_after, fp_before == fp_after)
    src = {t: con.execute(f"SELECT count(*), count(*) FILTER (WHERE score_status='scored') FROM {t}").fetchone() for t in (spec.comment_table, spec.reply_table)}
    # totals against Phase 1 source records
    for pf, t in LAYERS:
        chk(f"sum of per-video {pf}_rows == Phase 1 {t} rows", src[t][0], int(p[f"{pf}_rows"].sum()), int(p[f"{pf}_rows"].sum()) == src[t][0])
        chk(f"sum of per-video {pf}_scored == Phase 1 scored rows", src[t][1], int(p[f"{pf}_scored"].sum()), int(p[f"{pf}_scored"].sum()) == src[t][1])
        chk(f"{pf}: neg + neu + pos == scored for every video", 0, int((p[f"{pf}_neg"] + p[f"{pf}_neu"] + p[f"{pf}_pos"] != p[f"{pf}_scored"]).sum()), ((p[f"{pf}_neg"] + p[f"{pf}_neu"] + p[f"{pf}_pos"]) == p[f"{pf}_scored"]).all())
        chk(f"{pf}: scored + not_scored + failed == rows for every video", 0, int((p[f"{pf}_scored"] + p[f"{pf}_not_scored"] + p[f"{pf}_failed"] != p[f"{pf}_rows"]).sum()), ((p[f"{pf}_scored"] + p[f"{pf}_not_scored"] + p[f"{pf}_failed"]) == p[f"{pf}_rows"]).all())
        s = p[p[f"{pf}_scored"] > 0]
        tot = (s[f"{pf}_neg_pct"] + s[f"{pf}_neu_pct"] + s[f"{pf}_pos_pct"] - 100).abs().max()
        chk(f"{pf}: percentages sum to 100 (max abs error)", "< 1e-9", f"{tot:.2e}", tot < 1e-9)
        ne = (s[f"{pf}_net_sentiment"] - (s[f"{pf}_pos_pct"] - s[f"{pf}_neg_pct"])).abs().max()
        chk(f"{pf}: net sentiment == positive % - negative % (max abs error)", "< 1e-9", f"{ne:.2e}", ne < 1e-9)
        z = p[p[f"{pf}_scored"] == 0]
        bad = int(z[[f"{pf}_neg_pct", f"{pf}_net_sentiment", f"{pf}_tone_mean"]].notna().any(axis=1).sum())
        chk(f"{pf}: no percentages / net / mean where nothing was scored (NULL, not 0)", 0, bad, bad == 0)
        low = p[(p[f"{pf}_scored"] > 0) & (p[f"{pf}_scored"] < MIN_SCORED)]
        chk(f"{pf}: tone mean NULL below {MIN_SCORED} scored rows, counts kept", 0, int(low[f"{pf}_tone_mean"].notna().sum()), low[f"{pf}_tone_mean"].isna().all())
    # independent recomputation straight from the Phase 1 rows
    ind = con.execute(f"""
        SELECT c.post_uid, count(*) FILTER (WHERE score_status='scored') n, avg(tone_score) FILTER (WHERE score_status='scored') m,
               count(*) FILTER (WHERE sentiment_label='negative') neg, count(*) FILTER (WHERE sentiment_label='positive') pos
        FROM {spec.comment_table} c GROUP BY 1""").df().merge(p[["post_uid", "c_scored", "c_neg", "c_pos", "c_tone_sum", "c_tone_mean", "c_supported"]], on="post_uid")
    chk("independent per-video recount of comment counts == table", 0, int(((ind["n"] != ind["c_scored"]) | (ind["neg"] != ind["c_neg"]) | (ind["pos"] != ind["c_pos"])).sum()),
        ((ind["n"] == ind["c_scored"]) & (ind["neg"] == ind["c_neg"]) & (ind["pos"] == ind["c_pos"])).all())
    mm = ind[ind["c_supported"]]
    chk("independent per-video mean tone == table (max abs error)", "< 1e-6", f"{(mm['m'] - mm['c_tone_mean']).abs().max():.2e}", (mm["m"] - mm["c_tone_mean"]).abs().max() < 1e-6)
    # keys and mappings
    chk("duplicate post_uid in per-video table", 0, int(p["post_uid"].duplicated().sum()), not p["post_uid"].duplicated().any())
    missing = con.execute(f"""SELECT count(DISTINCT post_uid) FROM (SELECT post_uid FROM {spec.comment_table} UNION ALL SELECT post_uid FROM {spec.reply_table})
                             WHERE post_uid NOT IN (SELECT post_uid FROM {spec.out_table})""").fetchone()[0]
    chk("comment / reply post_uid missing from the per-video table", 0, missing, missing == 0)
    chk(f"null {idc}", 0, int(p[idc].isna().sum()), p[idc].isna().sum() == 0)
    # post tone: unchanged and never invented
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    old = a.execute(f"SELECT post_uid, tone_score FROM post_scores WHERE platform='{spec.platform}' AND tone_valid").df(); a.close()
    j = p[p["post_tone_available"]].merge(old, on="post_uid", suffixes=("", "_old"))
    chk("post tone identical to existing post_scores (max abs diff)", 0, f"{(j['post_tone_score'] - j['tone_score']).abs().max():.1e}", (j["post_tone_score"] - j["tone_score"]).abs().max() == 0)
    have_tone = set(old["post_uid"])
    if spec.enriched:                                                  # targeted enrichment adds tone for posts the store never scored
        have_tone |= set(con.execute("SELECT post_uid FROM enriched_post_scores WHERE tone_valid").df()["post_uid"])
    n_expected = len(have_tone & set(p["post_uid"]))
    chk("videos with a post tone == videos with a valid tone in post_scores (+ targeted enrichment)", n_expected, int(p["post_tone_available"].sum()), int(p["post_tone_available"].sum()) == n_expected)
    nt = p[~p["post_tone_available"]]
    chk("no post tone => post tone NULL and every gap NULL (never neutral)", 0, int(nt[["post_tone_score", "gap_comment_vs_post", "gap_reply_vs_post"]].notna().any(axis=1).sum()), not nt[["post_tone_score", "gap_comment_vs_post", "gap_reply_vs_post"]].notna().any().any())
    # gap recomputation
    g = p[p["gap_comment_vs_post"].notna()]
    chk("comment gap == comment mean tone - post tone (max abs error)", "< 1e-9", f"{(g['gap_comment_vs_post'] - (g['c_tone_mean'] - g['post_tone_score'])).abs().max():.2e}", (g["gap_comment_vs_post"] - (g["c_tone_mean"] - g["post_tone_score"])).abs().max() < 1e-9)
    g = p[p["gap_reply_vs_post"].notna()]
    chk("reply gap == reply mean tone - post tone (max abs error)", "< 1e-9", f"{(g['gap_reply_vs_post'] - (g['r_tone_mean'] - g['post_tone_score'])).abs().max():.2e}", (g["gap_reply_vs_post"] - (g["r_tone_mean"] - g["post_tone_score"])).abs().max() < 1e-9)
    n_gap_low = int(((p["gap_comment_vs_post"].notna()) & (p["c_scored"] < MIN_SCORED)).sum() + ((p["gap_reply_vs_post"].notna()) & (p["r_scored"] < MIN_SCORED)).sum())
    chk("gap only where the audience has >= minimum scored rows", 0, n_gap_low, n_gap_low == 0)
    # data-status accounting
    st = p["comment_data_status"].value_counts().to_dict()
    chk("every video has exactly one data status", len(p), sum(st.values()), sum(st.values()) == len(p), note=str(st))
    n_bad = int(((p["rows_total"] > 0) & (p["comment_data_status"] != "has_comments")).sum() + ((p["rows_total"] == 0) & (p["comment_data_status"] == "has_comments")).sum())
    chk("has_comments status iff the video has Phase 1 rows", 0, n_bad, n_bad == 0)
    chk("confirmed_zero videos (reported 0)", "info", st.get("confirmed_zero", 0), True, note="reported comment count is NULL for most videos, so zero cannot be told from 'not collected'; see unknown_no_comment_data")
    # roll-up totals
    allr = r[r["dimension"] == "all"].iloc[0]
    for pf, t in LAYERS:
        chk(f"roll-up 'all': {pf}_rows == Phase 1 rows", src[t][0], int(allr[f"{pf}_rows"]), int(allr[f"{pf}_rows"]) == src[t][0])
        for dim in ("d_theme", "month"):
            tot = int(r[r["dimension"] == dim][f"{pf}_scored"].sum())
            chk(f"roll-up by {dim}: {pf}_scored sums to total (no row lost or double-counted)", src[t][1], tot, tot == src[t][1])
        tot = int(r[r["dimension"] == "d_theme_month"][f"{pf}_scored"].sum())
        chk(f"roll-up by theme x month: {pf}_scored sums to total", src[t][1], tot, tot == src[t][1])
    chk("roll-up videos sum to the video count (theme)", len(p), int(r[r["dimension"] == "d_theme"]["n_videos"].sum()), int(r[r["dimension"] == "d_theme"]["n_videos"].sum()) == len(p))
    chk("roll-up videos sum to the video count (month)", len(p), int(r[r["dimension"] == "month"]["n_videos"].sum()), int(r[r["dimension"] == "month"]["n_videos"].sum()) == len(p))
    # pooled share recomputed from raw rows for each theme
    raw = con.execute(f"""SELECT t.theme_status d, count(*) FILTER (WHERE c.score_status='scored') n, count(*) FILTER (WHERE c.sentiment_label='negative') neg
                         FROM {spec.comment_table} c JOIN {spec.out_table} t USING (post_uid) GROUP BY 1""").df()
    rt = r[r["dimension"] == "d_theme"][["d_theme", "c_scored", "c_neg"]]
    mj = raw.merge(rt, left_on="d", right_on="d_theme")
    chk("roll-up by theme: pooled counts == recount from raw comments", 0, int(((mj["n"] != mj["c_scored"]) | (mj["neg"] != mj["c_neg"])).sum()), ((mj["n"] == mj["c_scored"]) & (mj["neg"] == mj["c_neg"])).all())
    n_pool = int(r[(r["c_scored"] < ROLLUP_MIN)][["c_net_sentiment", "c_tone_mean"]].notna().any(axis=1).sum() + r[(r["r_scored"] < ROLLUP_MIN)][["r_net_sentiment", "r_tone_mean"]].notna().any(axis=1).sum())
    chk("pooled statistics NULL where scored rows < roll-up minimum", 0, n_pool, n_pool == 0)
    nt = int((~p["post_in_store"]).sum()); nu = int((p["theme_status"] == "unassigned").sum())
    chk("videos without a post row / theme", "info", f"post_not_in_store={nt}, unassigned={nu}, themed={int(p['d_theme'].notna().sum())}", True, "INFO", "kept as explicit roll-up buckets, not dropped")
    return pd.DataFrame(rows)


def run(con, spec: Spec = None):
    """Build, store and validate one platform's per-post table and roll-up. Returns (per-post, roll-up, validation)."""
    spec = spec or Spec()
    fp_before = fingerprint(con, spec.protected)
    p = build(con, spec)
    for name, df in ((spec.out_table, p),):
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
        con.unregister("_t")
    r = rollup(p)
    con.register("_t", r)
    con.execute(f"CREATE OR REPLACE TABLE {spec.rollup_table} AS SELECT * FROM _t")
    con.unregister("_t")
    fp_after = fingerprint(con, spec.protected)
    v = validate(con, p, r, fp_before, fp_after, spec)
    con.register("_t", v)
    con.execute(f"CREATE OR REPLACE TABLE {spec.validation_table} AS SELECT * FROM _t")
    con.unregister("_t")
    return p, r, v


def main() -> int:
    con = duckdb.connect(str(DB))
    p, r, v = run(con, Spec())
    con.close()
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 70)
    print(v[["check", "status", "observed"]].to_string())
    print("\nstatus counts:", v["status"].value_counts().to_dict())
    print("\ndata status:", p["comment_data_status"].value_counts().to_dict())
    print("\ntheme status:", p["theme_status"].value_counts().to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
