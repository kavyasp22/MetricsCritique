#!/usr/bin/env python3
"""
Comment-sentiment quality layer (Phase 1+2 of the sentiment-adoption plan), all four comment platforms.

Reads  data/audience_sentiment.duckdb (READ ONLY: stored scores) and data/audience/*_extract.parquet (text only).
Writes data/sentiment_quality.duckdb (its own file; nothing existing is modified, no model, no GPU, no prod access):
  comment_quality_v1          one row per scored comment: max_prob, conf_tier, skip_reason
  sentiment_quality_weekly_v1 platform x layer x week: headline-base counts (for trends)
  sentiment_quality_rollup_v1 platform x layer x month: counts and net sentiment on three bases with Wilson intervals
    base A  all scored rows            (what the dashboard shows today)
    base B  A minus NO_TEXT            (HEADLINE; emoji-only reactions are kept)
    base C  confident rows only        (B and max_prob >= 0.60)
    base T  rows with words            (B minus emoji-only; shows what emoji reactions add)
  Statistics are NULL (not 0) below MIN_N rows; counts are always kept.

    .venv/bin/python scripts/51_sentiment_quality.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.sentiment_quality import CONF_HIGH, CONF_LOW, conf_tier, is_emoji_only, max_prob, skip_reason  # noqa: E402
from lib.stats_v2 import wilson_interval  # noqa: E402

DATA = HERE.parent / "data"
SRC = DATA / "audience_sentiment.duckdb"
OUT = DATA / "sentiment_quality.duckdb"
MIN_N = 30
# (platform, source table, layer, text extract parquet)
SOURCES = [("youtube", "comment_sentiment", "comment", "yt_comments_extract.parquet"),
           ("youtube", "reply_sentiment", "reply", "yt_comments_extract.parquet"),
           ("instagram", "ig_comment_sentiment", "comment", "ig_comments_extract.parquet"),
           ("instagram", "ig_reply_sentiment", "reply", "ig_comments_extract.parquet"),
           ("linkedin", "li_comment_sentiment", "comment", "li_comments_extract.parquet"),
           ("snapchat", "snap_comment_sentiment", "comment", "snap_comments_extract.parquet")]


def load() -> pd.DataFrame:
    con = duckdb.connect(str(SRC), read_only=True)
    parts, texts = [], {}
    for plat, table, layer, ext in SOURCES:
        df = con.execute(f"SELECT comment_uid, posted_at, score_status, sentiment_label AS label, p_negative, p_neutral, p_positive FROM {table}").df()
        if ext not in texts:
            texts[ext] = pd.read_parquet(DATA / "audience" / ext, columns=["comment_uid", "text_used"]).drop_duplicates("comment_uid")
        df = df.merge(texts[ext], on="comment_uid", how="left")
        df.insert(1, "platform", plat); df.insert(2, "layer", layer)
        parts.append(df)
    con.close()
    return pd.concat(parts, ignore_index=True)


def add_quality(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["max_prob"] = max_prob(df["p_negative"], df["p_neutral"], df["p_positive"])
    df["conf_tier"] = conf_tier(df["max_prob"])
    df["skip_reason"] = [skip_reason(t) for t in df["text_used"]]
    df["is_emoji_only"] = [is_emoji_only(t) for t in df["text_used"]]
    df["posted_month"] = pd.to_datetime(df["posted_at"], utc=True, errors="coerce").dt.strftime("%Y-%m")
    return df


def _stats(g: pd.DataFrame, tag: str) -> dict:
    n = len(g); pos = int((g.label == "positive").sum()); neg = int((g.label == "negative").sum()); neu = int((g.label == "neutral").sum())
    ok = n >= MIN_N
    out = {f"{tag}_n": n, f"{tag}_pos": pos, f"{tag}_neg": neg, f"{tag}_neu": neu}
    for name, k in (("pos", pos), ("neg", neg)):
        p, lo, hi = wilson_interval([k], [n]) if ok else ([np.nan], [np.nan], [np.nan])
        out[f"{tag}_{name}_pct"] = 100 * float(p[0]) if ok else None
        out[f"{tag}_{name}_lo"] = 100 * float(lo[0]) if ok else None
        out[f"{tag}_{name}_hi"] = 100 * float(hi[0]) if ok else None
    out[f"{tag}_net"] = 100.0 * (pos - neg) / n if ok else None
    return out


def rollup(q: pd.DataFrame) -> pd.DataFrame:
    s = q[q.score_status == "scored"]
    rows = []
    for (plat, layer, month), g in s.groupby(["platform", "layer", "posted_month"], dropna=False):
        b = g[g.skip_reason.isna()]
        c = b[b.conf_tier == "confident"]
        rows.append({"platform": plat, "layer": layer, "posted_month": month, "n_skipped": int(g.skip_reason.notna().sum()), "n_emoji_only": int(g.is_emoji_only.sum()),
                     "n_uncertain": int((b.conf_tier == "uncertain").sum()), "n_weak": int((b.conf_tier == "weak").sum()),
                     **_stats(g, "a"), **_stats(b, "b"), **_stats(c, "c"), **_stats(b[~b.is_emoji_only], "t")})
    # an all-time row per platform x layer
    for (plat, layer), g in s.groupby(["platform", "layer"]):
        b = g[g.skip_reason.isna()]; c = b[b.conf_tier == "confident"]
        rows.append({"platform": plat, "layer": layer, "posted_month": "ALL", "n_skipped": int(g.skip_reason.notna().sum()), "n_emoji_only": int(g.is_emoji_only.sum()),
                     "n_uncertain": int((b.conf_tier == "uncertain").sum()), "n_weak": int((b.conf_tier == "weak").sum()),
                     **_stats(g, "a"), **_stats(b, "b"), **_stats(c, "c"), **_stats(b[~b.is_emoji_only], "t")})
    return pd.DataFrame(rows)


def weekly(q: pd.DataFrame) -> pd.DataFrame:
    """Weekly counts on the headline base (B = scored minus NO_TEXT), per platform x layer. Counts only: shares, net, Theil-Sen and
    Mann-Kendall are computed from these at read time so any date range can be trended (UI/audience_view.trend_block)."""
    b = q[(q.score_status == "scored") & q.skip_reason.isna()].copy()
    b["week"] = pd.to_datetime(b["posted_at"], utc=True, errors="coerce").dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d")
    b = b[b.week.notna()]
    g = b.groupby(["platform", "layer", "week"])
    return g.agg(b_n=("label", "size"), b_pos=("label", lambda x: int((x == "positive").sum())), b_neg=("label", lambda x: int((x == "negative").sum()))).reset_index()


def main() -> int:
    q = add_quality(load())
    r = rollup(q)
    # ---- verification: base A must equal the stored labels; B subset of A; C subset of B
    s = q[q.score_status == "scored"]
    allr = r[r.posted_month == "ALL"]
    bad = []
    for plat, layer, g in ((p, l, s[(s.platform == p) & (s.layer == l)]) for p, l in allr[["platform", "layer"]].itertuples(index=False)):
        row = allr[(allr.platform == plat) & (allr.layer == layer)].iloc[0]
        if row.a_n != len(g) or row.a_pos != (g.label == "positive").sum() or not (row.c_n <= row.b_n <= row.a_n):
            bad.append((plat, layer))
    meta = pd.DataFrame([{"built_at": pd.Timestamp.now("UTC"), "conf_high": CONF_HIGH, "conf_low": CONF_LOW, "min_n": MIN_N,
                          "rows": len(q), "scored": len(s), "verification_failures": len(bad)}])
    con = duckdb.connect(str(OUT))
    for name, df in (("comment_quality_v1", q.drop(columns=["text_used"])), ("sentiment_quality_rollup_v1", r), ("sentiment_quality_weekly_v1", weekly(q)), ("sentiment_quality_meta_v1", meta)):
        con.register("_t", df); con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t"); con.unregister("_t")
    con.close()
    show = allr[["platform", "layer", "a_n", "n_skipped", "n_emoji_only", "b_n", "c_n", "a_net", "b_net", "c_net", "t_n", "t_net"]].round(1)
    print(show.to_string(index=False))
    print("verification failures:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
