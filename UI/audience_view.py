"""Explore > Audience Sentiment: read-only query logic over the v2 audience-sentiment tables.

Reads ONLY data/audience_sentiment.duckdb (attached read-only for the length of one request, then closed, so the pipeline scripts
that write that file are never locked out for long):
    xplat_post_audience_sentiment_v2   one row per YouTube video / Instagram / LinkedIn post: post tone, comment (c_*) and reply (r_*) metrics, gaps, theme, date
    comment_sentiment, reply_sentiment, ig_comment_sentiment, ig_reply_sentiment, li_comment_sentiment   the scored comments and replies (used for the comment-date trend)
    enriched_post_text                 post text of the posts enriched without a stage 01 refresh (local only), for the snippets
Entity filter: post_uid lists come from geo.entity_mentions through the caller's cursor (the same mapping the other Explore tiles use);
post snippets for stored posts come from analytics.post_facts through that cursor too.

Rules that keep the numbers honest (same as the roll-up tables):
  * post, comment and reply sentiment are three separate layers, never averaged together;
  * a missing value is None (shown as a dash by the page), never 0; shares and means need >= MIN_POOLED scored rows, a gap needs >= MIN_GAP scored rows;
  * a post with no post tone has no gap (it is not treated as neutral);
  * the theme list comes from the data, so a later taxonomy change needs no code change here.
LinkedIn-specific notes:
  * LinkedIn is comment-only: no replies layer (reply table is None); all r_* values are always None.
  * Only the first ~10 comments per post are collected (LinkedIn scraper limit).
  * Comment collection began around August 2026.
Nothing is written anywhere.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import sys

import duckdb
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib.audience_rollup import gap_direction      # the pipeline's own rule and threshold (0.30): one definition, not a copy
from lib.stats_v2 import mann_kendall, theil_sen, wilson_interval   # the one implementation of each (stage 51 uses wilson too)
AUD_DB = DATA / "audience_sentiment.duckdb"
QUAL_DB = DATA / "sentiment_quality.duckdb"       # written by scripts/51_sentiment_quality.py; optional: the card works without it
QUAL_PLAT = {"youtube_video": "youtube", "instagram": "instagram", "linkedin": "linkedin", "snapchat": "snapchat"}   # this view's key -> stage 51's
PER_POST = "xplat_post_audience_sentiment_v2"
LAYERS = {  # platform -> (comment table, reply table or None)
    "youtube_video": ("comment_sentiment", "reply_sentiment"),
    "instagram": ("ig_comment_sentiment", "ig_reply_sentiment"),
    "linkedin": ("li_comment_sentiment", None),   # comment-only: no replies layer
    "snapchat": ("snap_comment_sentiment", None),  # comment-only: 20-comment Spotlight cap; posts in analytics store
}
PLATFORMS = tuple(LAYERS)
MIN_POOLED = 30        # shares / means need this many scored rows (same minimum as the pipeline's roll-up tables)
MIN_GAP = 5            # a per-post gap needs this many scored comments (or replies)
GAP_FLAG = 0.30        # |gap| at or above this = clearly different (provisional)
POST_CLASS = 0.15      # post tone above +0.15 / below -0.15 = positive / negative (the metrics' cut-off, provisional)
SMALL_N = 10           # display only: a gap built on fewer scored comments than this is flagged as a small sample
MIN_TOP = 10           # the divergent-post list has always needed this many scored comments; the analyst can only raise it
CAPTION_TONE = 0.5     # display only: kept for compatibility (Instagram "caption tone" flag)
EXTREME_TONE = 0.5     # display only: a post tone of this size, positive or negative, is flagged as a strong post tone (informational; no ranking or score uses it)
YT_CAP = 100           # YouTube comments were collected up to about this many per video (a collection limit, not a platform limit)
LI_CAP = 10            # LinkedIn scraper collects at most this many comments per post
SNAP_CAP = 20          # Snapchat Spotlight scraper collects at most this many comments per video
LI_CAVEAT = "Only first ~10 comments per post collected (LinkedIn scraper limit). Comment collection began ~August 2026. Posts have no tone score (not in analytics store)."
SNAP_CAVEAT = "Only first 20 comments per Spotlight video collected (Snapchat Spotlight scraper limit). Post-level tone scores available for most posts."
MAX_MIN_COMMENTS = 200
NO_THEME = "unassigned"


class AudienceUnavailable(RuntimeError):
    """The audience file is missing or locked by a rebuild: the caller answers 503 with the message."""


def _day(s: str, name: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        raise ValueError(f"{name} must be YYYY-MM-DD")
    return s


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _int(v):
    f = _num(v)
    return None if f is None else int(f)


def _pooled(df: pd.DataFrame, p: str) -> dict:
    """Pooled counts of one layer (p = 'c' comments or 'r' replies). Statistics are None below MIN_POOLED scored rows; counts are always kept."""
    n = int(df[f"{p}_scored"].sum())
    neg, neu, pos = (int(df[f"{p}_{k}"].sum()) for k in ("neg", "neu", "pos"))
    ok = n >= MIN_POOLED
    return {"posts_with_data": int((df[f"{p}_rows"] > 0).sum()), "rows": int(df[f"{p}_rows"].sum()), "scored": n,
            "not_scored": int(df[f"{p}_not_scored"].sum()), "failed": int(df[f"{p}_failed"].sum()),
            "neg": neg, "neu": neu, "pos": pos, "supported": ok,
            "neg_pct": 100 * neg / n if ok else None, "neu_pct": 100 * neu / n if ok else None, "pos_pct": 100 * pos / n if ok else None,
            "net": 100 * (pos - neg) / n if ok else None, "tone_mean": float(df[f"{p}_tone_sum"].sum()) / n if ok else None,
            "conf_mean": float(df[f"{p}_conf_sum"].sum()) / n if ok else None}


def _post_layer(df: pd.DataFrame) -> dict:
    """Post tone over the posts that HAVE audience data (so it can be compared with the audience): mean tone and the +-0.15 classes."""
    d = df[df["rows_total"] > 0]
    t = d.loc[d["post_tone_available"].fillna(False).astype(bool), "post_tone_score"].astype(float)
    n = int(len(t))
    ok = n >= MIN_POOLED
    return {"posts_with_data": int(len(d)), "with_tone": n, "without_tone": int(len(d) - n), "supported": ok,
            "tone_mean": float(t.mean()) if ok else None,
            "pos_pct": 100 * float((t > POST_CLASS).mean()) if ok else None, "neg_pct": 100 * float((t < -POST_CLASS).mean()) if ok else None,
            "neu_pct": 100 * float(((t <= POST_CLASS) & (t >= -POST_CLASS)).mean()) if ok else None,
            "net": 100 * (float((t > POST_CLASS).mean()) - float((t < -POST_CLASS).mean())) if ok else None}


def _gap(df: pd.DataFrame, col: str, dcol: str = "", ncol: str = "", min_n: int = MIN_GAP) -> dict:
    """Gap statistics over the posts that HAVE a gap. The three directions are decided here from those valid gap values (not read from the
    stored direction column), so a post with no gap is never "aligned" and more negative + aligned + more positive always equals n."""
    g = df[col].dropna().astype(float)
    g = g[np.isfinite(g)]
    cnt = None
    if ncol and ncol in df.columns:
        cnt = df.loc[g.index, ncol].astype(float)
        if min_n > MIN_GAP:                      # the analyst asked for more comments behind each gap; the gap values themselves are untouched
            keep = cnt >= min_n
            g, cnt = g[keep], cnt[keep]
    n = int(len(g))
    d = pd.Series([gap_direction(v) for v in g], dtype="object")
    bins = [-2.0, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 2.0001]
    labels = ["< -0.6", "-0.6 to -0.3", "-0.3 to -0.1", "-0.1 to +0.1", "+0.1 to +0.3", "+0.3 to +0.6", "> +0.6"]
    hist = pd.cut(g, bins=bins, right=False, labels=labels).value_counts().reindex(labels).fillna(0).astype(int) if n else pd.Series(0, index=labels)
    sizes = ({"rows": int(cnt.sum()), "median_rows": float(cnt.median()), "small": int((cnt < SMALL_N).sum()),
              "n_10_29": int(((cnt >= SMALL_N) & (cnt < 30)).sum()), "n_30_plus": int((cnt >= 30).sum())} if (cnt is not None and n) else
             {"rows": 0, "median_rows": None, "small": 0, "n_10_29": 0, "n_30_plus": 0})
    return {"n": n, **sizes, "mean": float(g.mean()) if n else None, "median": float(g.median()) if n else None,
            "more_negative": int((d == "audience_more_negative").sum()), "more_positive": int((d == "audience_more_positive").sum()),
            "aligned": int((d == "aligned").sum()), "hist": [{"bin": k, "n": int(v)} for k, v in hist.items()]}


def _gaps(df: pd.DataFrame, min_n: int = MIN_GAP) -> dict:
    rvc = df[df["gap_reply_vs_comment"].notna()]
    rvc_mean = _num(df["gap_reply_vs_comment"].mean()) if df["gap_reply_vs_comment"].notna().any() else None
    if min_n > MIN_GAP:        # the minimum applies here too: both the replies and the comments behind the post need at least min_n scored rows
        rvc = rvc[(rvc["c_scored"] >= min_n) & (rvc["r_scored"] >= min_n)]
        rvc_mean = _num(rvc["gap_reply_vs_comment"].mean()) if len(rvc) else None
    return {"comment_vs_post": _gap(df, "gap_comment_vs_post", "gap_comment_direction", "c_scored", min_n),
            "reply_vs_post": _gap(df, "gap_reply_vs_post", "gap_reply_direction", "r_scored", min_n),
            "reply_vs_comment": {"n": int(len(rvc)), "mean": rvc_mean,
                                 "comment_rows": int(rvc["c_scored"].sum()), "reply_rows": int(rvc["r_scored"].sum())},
            "flag": GAP_FLAG, "min_scored": MIN_GAP, "min_comments": min_n, "small_n": SMALL_N}


def _coverage(df: pd.DataFrame) -> dict:
    """Compact coverage of the posts that match the filters (counts only; nothing here changes a sentiment value)."""
    n = int(len(df))
    out = {"posts": n, "with_comments": int((df["c_rows"] > 0).sum()), "with_scored_comments": int((df["c_scored"] > 0).sum()),
           "with_replies": int((df["r_rows"] > 0).sum()), "with_any": int((df["rows_total"] > 0).sum()),
           "comment_rows": int(df["c_rows"].sum()), "comments_scored": int(df["c_scored"].sum())}
    out["coverage_pct"] = 100 * out["with_comments"] / n if n else None
    out["at_cap"] = int((df["c_rows"] >= YT_CAP).sum())
    out["median_comments"] = float(df.loc[df["c_rows"] > 0, "c_rows"].median()) if out["with_comments"] else None
    return out


def _status(df: pd.DataFrame) -> dict:
    s = df["comment_data_status"].value_counts().to_dict()
    return {"posts": int(len(df)), **{k: int(s.get(k, 0)) for k in ("has_comments", "confirmed_zero", "not_collected_known", "unknown_no_comment_data")}}


def _granularity(d_from: str, d_to: str) -> str:
    if not (d_from and d_to):
        return "month"
    days = (pd.Timestamp(d_to) - pd.Timestamp(d_from)).days + 1
    return "day" if days <= 31 else ("week" if days <= 120 else "month")


def _period(s: pd.Series, gran: str) -> pd.Series:
    t = pd.to_datetime(s, utc=True, errors="coerce").dt.tz_localize(None)
    if gran == "day":
        return t.dt.strftime("%Y-%m-%d")
    if gran == "week":
        return (t.dt.normalize() - pd.to_timedelta(t.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    return t.dt.strftime("%Y-%m") + "-01"


def _trend_post_basis(df: pd.DataFrame, gran: str) -> list:
    d = df[(df["rows_total"] > 0) & df["posted_at"].notna()].copy()
    if d.empty:
        return []
    d["period"] = _period(d["posted_at"], gran)
    out = []
    for (plat, per), g in d.groupby(["platform", "period"]):
        row = {"platform": plat, "period": per, "posts": int(len(g))}
        for p, name in (("c", "comment"), ("r", "reply")):
            n = int(g[f"{p}_scored"].sum()); neg, pos = int(g[f"{p}_neg"].sum()), int(g[f"{p}_pos"].sum())
            ok = n >= MIN_POOLED
            row[name] = {"scored": n, "neg": neg, "neu": int(g[f"{p}_neu"].sum()), "pos": pos,
                         "net": 100 * (pos - neg) / n if ok else None, "tone": float(g[f"{p}_tone_sum"].sum()) / n if ok else None}
        out.append(row)
    return sorted(out, key=lambda r: (r["platform"], r["period"]))


def _trend_comment_basis(con, plats, d_from, d_to, gran, ctx) -> list:
    """Same pooled figures, but the date is when the comment or reply itself was written (layer tables joined to the filtered posts)."""
    out = []
    unit = {"day": "day", "week": "week", "month": "month"}[gran]
    for plat in plats:
        ctbl, rtbl = LAYERS[plat]
        layers = [("comment", ctbl)]
        if rtbl is not None:
            layers.append(("reply", rtbl))
        for layer, tbl in layers:
            sql = (f"SELECT CAST(date_trunc('{unit}', c.posted_at) AS DATE) AS period, count(*) AS scored, "
                   f"count(*) FILTER (WHERE c.sentiment_label='negative') AS neg, count(*) FILTER (WHERE c.sentiment_label='neutral') AS neu, "
                   f"count(*) FILTER (WHERE c.sentiment_label='positive') AS pos, sum(c.tone_score) AS tone_sum "
                   f"FROM aud.{tbl} c JOIN f ON f.post_uid = c.post_uid WHERE c.score_status='scored' AND c.posted_at IS NOT NULL")
            params = []
            if d_from:
                sql += " AND CAST(c.posted_at AS DATE) >= ?"; params.append(d_from)
            if d_to:
                sql += " AND CAST(c.posted_at AS DATE) <= ?"; params.append(d_to)
            sql += " GROUP BY 1 ORDER BY 1"
            for r in con.execute(sql, params).fetchall():
                n = int(r[1]); ok = n >= MIN_POOLED
                out.append({"platform": plat, "layer": layer, "period": str(r[0]), "scored": n, "neg": int(r[2]), "neu": int(r[3]), "pos": int(r[4]),
                            "net": 100 * (int(r[4]) - int(r[2])) / n if ok else None, "tone": float(r[5]) / n if ok else None})
    return out


def _themes(df: pd.DataFrame, avail: list, min_n: int = MIN_GAP) -> list:
    d = df[df["rows_total"] > 0].copy()
    d["theme"] = np.where(d["d_theme"].notna(), d["d_theme"], np.where(d["theme_status"] == "post_not_in_store", "post_not_available", NO_THEME))
    rows = []
    for th, g in d.groupby("theme"):
        c, r = _pooled(g, "c"), _pooled(g, "r")
        gg = g.loc[g["c_scored"] >= min_n, "gap_comment_vs_post"].dropna()
        rows.append({"theme": th, "posts": int(len(g)), "c_scored": c["scored"], "c_net": c["net"], "c_tone": c["tone_mean"],
                     "r_scored": r["scored"], "r_net": r["net"], "r_tone": r["tone_mean"],
                     "gap_n": int(len(gg)), "gap_mean": float(gg.mean()) if len(gg) >= MIN_GAP else None})
    return sorted(rows, key=lambda x: -x["posts"])


def _top_gaps(df: pd.DataFrame, limit: int, min_c: int):
    """Posts with the largest comment gap and enough scored comments. This is the list's original logic (>= 10 scored comments, most negative /
    most positive gap); only the platform subset and an optional higher minimum are new."""
    cand = df[df["gap_comment_vs_post"].notna() & (df["c_scored"] >= min_c)]
    return cand.nsmallest(limit, "gap_comment_vs_post"), cand.nlargest(limit, "gap_comment_vs_post")


def _snippets(post_rows: pd.DataFrame, main_cur, con) -> dict:
    """Post title / opening text for the few posts shown in the gap lists (stored posts from analytics, enriched ones from the local text table)."""
    uids = post_rows["post_uid"].tolist()
    if not uids:
        return {}
    out = {}
    try:
        for uid, t in main_cur.execute("SELECT post_uid, left(coalesce(text, ''), 400) FROM analytics.post_facts WHERE post_uid IN (SELECT unnest(?))", [uids]).fetchall():
            out[uid] = t
    except Exception:
        pass
    miss = [u for u in uids if u not in out]
    if miss:
        try:
            for uid, t in con.execute("SELECT post_uid, left(coalesce(text, ''), 400) FROM aud.enriched_post_text WHERE post_uid IN (SELECT unnest(?))", [miss]).fetchall():
                out[uid] = t
        except Exception:
            pass
    return out


def _post_url(platform: str, native_id, url):
    if platform == "youtube_video" and native_id:
        return f"https://www.youtube.com/watch?v={native_id}"
    return url if isinstance(url, str) and url.startswith("http") else None


_COVER = {}


def entity_coverage(con, main_cur) -> dict:
    """How many posts WITH audience data have any entity data (the production entity extract is a snapshot, so newer posts have none)."""
    if "v" in _COVER:
        return _COVER["v"]
    d = con.execute(f"SELECT post_uid, platform, post_source FROM aud.{PER_POST} WHERE rows_total > 0").df()
    main_cur.register("aud_cov", d)
    try:
        have = {r[0] for r in main_cur.execute("SELECT DISTINCT m.post_uid FROM geo.entity_mentions m JOIN aud_cov c ON c.post_uid = m.post_uid").fetchall()}
    finally:
        main_cur.unregister("aud_cov")
    d["has_entity"] = d["post_uid"].isin(have)
    res = {}
    for plat, g in d.groupby("platform"):
        res[plat] = {"posts_with_data": int(len(g)), "with_entity_data": int(g["has_entity"].sum()),
                     "enriched_posts": int((g["post_source"] == "targeted_enrichment").sum()),
                     "enriched_with_entity_data": int(((g["post_source"] == "targeted_enrichment") & g["has_entity"]).sum())}
    _COVER["v"] = res
    return res


def explore_audience(qs: dict, main_cur) -> dict:
    d_from = _day(qs.get("from", [""])[0], "from") if qs.get("from", [""])[0] else ""
    d_to = _day(qs.get("to", [""])[0], "to") if qs.get("to", [""])[0] else ""
    plats = [x for x in qs.get("platforms", [""])[0].split(",") if x]
    asked = plats
    plats = [p for p in plats if p in PLATFORMS] if plats else list(PLATFORMS)
    theme = qs.get("theme", [""])[0].strip()
    ent = qs.get("entity_uid", [""])[0].strip()
    basis = qs.get("basis", ["post"])[0]
    if basis not in ("post", "comment"):
        raise ValueError("basis must be post or comment")
    limit = max(1, min(int(qs.get("limit", ["10"])[0] or 10), 50))
    try:
        min_c = int(qs.get("min_comments", [str(MIN_GAP)])[0] or MIN_GAP)
    except ValueError:
        raise ValueError("min_comments must be a whole number")
    min_c = max(MIN_GAP, min(min_c, MAX_MIN_COMMENTS))
    if not AUD_DB.exists():
        raise AudienceUnavailable("the audience sentiment data file is not built yet (scripts 32-40)")
    try:
        con = duckdb.connect(":memory:")
        con.execute(f"ATTACH '{AUD_DB.as_posix()}' AS aud (READ_ONLY)")
    except Exception as e:                                                   # the pipeline is writing the file
        raise AudienceUnavailable(f"the audience sentiment data is being rebuilt, try again in a minute ({type(e).__name__})")
    try:
        avail = [r[0] for r in con.execute(f"SELECT DISTINCT d_theme FROM aud.{PER_POST} WHERE d_theme IS NOT NULL ORDER BY 1").fetchall()]
        if theme and theme != NO_THEME and theme not in avail:
            raise ValueError("theme is not one of the themes in the data: " + ", ".join(avail))
        meta = con.execute("SELECT * FROM aud.enrichment_run_meta").df().iloc[0].to_dict() if _has(con, "enrichment_run_meta") else {}
        where, params = [], []
        where.append("p.platform IN (" + ",".join("?" * len(plats)) + ")") if plats else where.append("FALSE")
        params += plats
        if theme == NO_THEME:
            where.append("p.d_theme IS NULL AND p.theme_status = 'unassigned'")
        elif theme:
            where.append("p.d_theme = ?"); params.append(theme)
        ent_n = None
        if ent:
            ids = [r[0] for r in main_cur.execute("SELECT DISTINCT post_uid FROM geo.entity_mentions WHERE entity_uid = ?", [ent]).fetchall()]
            ent_n = len(ids)
            con.register("ent_posts", pd.DataFrame({"post_uid": pd.Series(ids, dtype="object")}))
            where.append("p.post_uid IN (SELECT CAST(post_uid AS VARCHAR) FROM ent_posts)")
        cond_other = " AND ".join(where) or "TRUE"                 # platform, theme, entity
        params_other = list(params)
        dwhere = []
        if d_from:
            dwhere.append("CAST(p.posted_at AS DATE) >= ?"); params.append(d_from)
        if d_to:
            dwhere.append("CAST(p.posted_at AS DATE) <= ?"); params.append(d_to)
        cond = " AND ".join(where + dwhere) or "TRUE"              # + the post date range
        df = con.execute(f"SELECT p.* EXCLUDE (built_at) FROM aud.{PER_POST} p WHERE {cond}", params).df()
        if basis == "comment":      # the date range then applies to the comment's own date, so the posts are not cut by their publish date
            con.register("f_df", con.execute(f"SELECT p.post_uid FROM aud.{PER_POST} p WHERE {cond_other}", params_other).df())
        else:
            con.register("f_df", df[["post_uid"]])
        con.execute("CREATE TEMP VIEW f AS SELECT post_uid FROM f_df")
        undated = int(con.execute(f"SELECT count(*) FROM aud.{PER_POST} p WHERE p.posted_at IS NULL AND p.platform IN ({','.join('?' * len(plats))})", plats).fetchone()[0]) if plats else 0
        # how much of the data the theme and entity filters keep (posts WITH comment data, same platforms and dates)
        base_w, base_p = [where[0]] + dwhere, (list(plats) if plats else []) + params[len(params_other):]      # platform condition first, then the date bounds
        def _n(extra=(), ep=()):
            c = " AND ".join(base_w + ["p.rows_total > 0"] + list(extra)) or "TRUE"
            return int(con.execute(f"SELECT count(*) FROM aud.{PER_POST} p WHERE {c}", base_p + list(ep)).fetchone()[0])
        ent_cond = "p.post_uid IN (SELECT CAST(post_uid AS VARCHAR) FROM ent_posts)"
        n_base = _n()
        filter_cov = {"posts_with_data": n_base, "with_theme": _n(["p.d_theme IS NOT NULL"])}
        if theme:
            th_c = ("p.d_theme IS NULL AND p.theme_status = 'unassigned'", []) if theme == NO_THEME else ("p.d_theme = ?", [theme])
            filter_cov["theme_kept"] = _n([th_c[0]], th_c[1])
        if ent:
            filter_cov["entity_kept"] = _n([ent_cond])
        gran = _granularity(d_from, d_to)
        lo_hi = con.execute(f"SELECT CAST(min(posted_at) AS DATE), CAST(max(posted_at) AS DATE), count(*) FILTER (WHERE posted_at < TIMESTAMP '2026-06-14') "
                            f"FROM aud.{PER_POST} WHERE rows_total > 0").fetchone()
        # Max comment timestamp across available comment sentiment tables
        c_tables = ["comment_sentiment", "ig_comment_sentiment"]
        if _has(con, "li_comment_sentiment"):
            c_tables.append("li_comment_sentiment")
        if _has(con, "snap_comment_sentiment"):
            c_tables.append("snap_comment_sentiment")
        union_sql = " UNION ALL ".join(f"SELECT max(posted_at) m FROM aud.{t} WHERE 1=1" for t in c_tables)
        c_hi = con.execute(f"SELECT CAST(max(m) AS DATE) FROM ({union_sql})").fetchone()[0]
        by = {}
        for key, sub in [("all", df)] + [(p, df[df["platform"] == p]) for p in PLATFORMS]:
            by[key] = {"post": _post_layer(sub), "comment": _pooled(sub, "c"), "reply": _pooled(sub, "r"), "gaps": _gaps(sub, min_c), "status": _status(sub),
                       "coverage": _coverage(sub)}
        # posts with the largest gap between the audience and the post (comments), enough comments to mean something
        top_c = max(MIN_TOP, min_c)
        neg, pos = _top_gaps(df, limit, top_c)
        per_plat = {p: _top_gaps(df[df["platform"] == p], limit, top_c) for p in plats}
        shown = pd.concat([neg, pos] + [x for pair in per_plat.values() for x in pair]).drop_duplicates("post_uid")
        snip = _snippets(shown, main_cur, con)

        def card(r):
            t = (snip.get(r["post_uid"]) or "").strip().split("\n")[0][:220]
            return {"post_uid": r["post_uid"], "platform": r["platform"], "date": str(r["posted_at"])[:10] if pd.notna(r["posted_at"]) else None,
                    "url": _post_url(r["platform"], r.get("post_native_id"), r.get("post_url")), "text": t or None, "theme": r["d_theme"] if pd.notna(r["d_theme"]) else None,
                    "post_tone": _num(r["post_tone_score"]), "comment_tone": _num(r["c_tone_mean"]), "gap": _num(r["gap_comment_vs_post"]),
                    "comments": _int(r["c_scored"]), "replies": _int(r["r_scored"]), "reply_gap": _num(r["gap_reply_vs_post"]),
                    "small_sample": bool(r["c_scored"] < SMALL_N),
                    "caption_effect": bool(r["platform"] == "instagram" and pd.notna(r["post_tone_score"]) and r["post_tone_score"] >= CAPTION_TONE),
                    "extreme_post_tone": bool(pd.notna(r["post_tone_score"]) and abs(r["post_tone_score"]) >= EXTREME_TONE)}
        trend = (_trend_post_basis(df, gran) if basis == "post" else _trend_comment_basis(con, plats, d_from, d_to, gran, None))
        res = {"version": "v2", "source": f"audience_sentiment.duckdb: {PER_POST} + comment / reply layer tables", "built": meta.get("built_at"),
               "filters": {"from": d_from, "to": d_to, "platforms": plats, "platforms_asked": asked, "theme": theme, "entity_uid": ent, "basis": basis},
               "unsupported_platforms": [p for p in asked if p not in PLATFORMS], "available_platforms": list(PLATFORMS),
               "platform_caveats": {"linkedin": LI_CAVEAT, "snapchat": SNAP_CAVEAT},
               "date_bounds": {"post_min": str(lo_hi[0]) if lo_hi[0] else None, "post_max": str(lo_hi[1]) if lo_hi[1] else None, "comment_max": str(c_hi) if c_hi else None,
                               "posts_before_2026_06_14": int(lo_hi[2])},
               "themes_available": avail, "n_posts": int(len(df)), "n_posts_with_data": int((df["rows_total"] > 0).sum()), "undated_posts_excluded_by_dates": undated if (d_from or d_to) else 0,
               "entity": {"uid": ent, "posts_in_entity_extract": ent_n, "coverage": entity_coverage(con, main_cur)} if ent else {"coverage": None},
               "layers": by, "themes": _themes(df, avail, min_c), "granularity": gran, "trend": trend, "trend_basis": basis,
               "top_gaps": {"negative": [card(r) for _, r in neg.iterrows()], "positive": [card(r) for _, r in pos.iterrows() if r["gap_comment_vs_post"] > 0],
                            "min_comments": top_c},
               "top_gaps_by_platform": {p: {"negative": [card(r) for _, r in n_.iterrows()], "positive": [card(r) for _, r in p_.iterrows() if r["gap_comment_vs_post"] > 0]}
                                        for p, (n_, p_) in per_plat.items()},
               "filter_coverage": filter_cov, "quality": quality_block(plats, d_from, d_to), "quality_trend": trend_block(plats, d_from, d_to),
               "limits": {"min_pooled": MIN_POOLED, "min_gap": MIN_GAP, "gap_flag": GAP_FLAG, "post_class": POST_CLASS, "small_n": SMALL_N, "min_top": MIN_TOP,
                          "min_comments": min_c, "caption_tone": CAPTION_TONE, "extreme_tone": EXTREME_TONE, "yt_cap": YT_CAP, "li_cap": LI_CAP, "snap_cap": SNAP_CAP, "thin_trend": 100}}
        return res
    finally:
        try:
            con.close()
        except Exception:
            pass


TREND_MIN_WEEKS = 8      # a trend needs this many weeks that each have >= MIN_POOLED scored rows
TREND_MIN_N = 30
TREND_MIN_COVER = 0.7    # and those weeks must fill at least this share of the weeks between the first and the last (a slope across a gap-ridden span is not a trend)


def trend_block(plats: list, d_from: str, d_to: str) -> dict | None:
    """Robust trend of weekly net sentiment (headline base) per platform x layer: Theil-Sen slope (points per week, 95% interval) and a
    Hamed-Rao-corrected Mann-Kendall test. Weeks under TREND_MIN_N rows are left out; fewer than TREND_MIN_WEEKS usable weeks, or weeks covering under TREND_MIN_COVER of the span -> NULL, not 0.
    Descriptive only: the trend of the comments that were collected, not of the audience."""
    if not QUAL_DB.exists():
        return None
    try:
        q = duckdb.connect(QUAL_DB.as_posix(), read_only=True)
        w = q.execute("SELECT * FROM sentiment_quality_weekly_v1").df()
    except Exception:
        return None
    else:
        q.close()
    w = w[w["platform"].isin([QUAL_PLAT[p] for p in plats if p in QUAL_PLAT])]
    if d_from:
        w = w[w["week"] >= d_from]
    if d_to:
        w = w[w["week"] <= d_to]
    inv = {v: k for k, v in QUAL_PLAT.items()}
    rows = []
    for (plat, layer), g in w.groupby(["platform", "layer"]):
        g = g[g["b_n"] >= TREND_MIN_N].sort_values("week")
        net = (100.0 * (g["b_pos"] - g["b_neg"]) / g["b_n"]).to_numpy(float)
        wk = pd.to_datetime(g["week"]).map(pd.Timestamp.toordinal).to_numpy(float) / 7.0     # real week spacing, gaps respected
        row = {"platform": inv[plat], "layer": layer, "weeks": int(len(g)), "first": str(g["week"].iloc[0]) if len(g) else None,
               "last": str(g["week"].iloc[-1]) if len(g) else None, "slope": None, "lo": None, "hi": None, "p": None, "trend": None, "ols_slope": None}
        span = int(round((wk[-1] - wk[0]))) + 1 if len(g) else 0
        row["cover"] = float(len(g) / span) if span else None
        if len(g) >= TREND_MIN_WEEKS and span and len(g) / span >= TREND_MIN_COVER:
            ts, mk = theil_sen(net, wk), mann_kendall(net)
            row.update(slope=float(ts["slope"]), lo=float(ts["lo"]), hi=float(ts["hi"]), p=float(mk["p"]), trend=mk["trend"],
                       ols_slope=float(np.polyfit(wk, net, 1)[0]), first_net=float(net[0]), last_net=float(net[-1]))
        rows.append(row)
    order = {k: i for i, k in enumerate(PLATFORMS)}
    rows.sort(key=lambda x: (order.get(x["platform"], 99), x["layer"]))
    return {"rows": rows, "min_weeks": TREND_MIN_WEEKS, "min_n": TREND_MIN_N, "min_cover": TREND_MIN_COVER}


def quality_block(plats: list, d_from: str, d_to: str) -> dict | None:
    """Sentiment-quality view (stage 51): per platform x layer, the net sentiment on four bases, plus how many rows each rule touched.

    A all scored rows (the number shown above) | B minus NO_TEXT (headline) | C confident rows only (max prob >= 0.60) | T rows with words
    (B minus emoji-only reactions). Counts are summed over the months that overlap the date range (month grain: stage 51 buckets by month),
    then shares and Wilson intervals are recomputed from the summed counts. Theme and entity filters cannot apply here (comment-level, no theme).
    None when stage 51 has not been run, so the card degrades to what it showed before."""
    if not QUAL_DB.exists():
        return None
    try:
        q = duckdb.connect(QUAL_DB.as_posix(), read_only=True)
    except Exception:
        return None
    try:
        r = q.execute("SELECT * FROM sentiment_quality_rollup_v1").df()
        meta = q.execute("SELECT * FROM sentiment_quality_meta_v1").df().iloc[0].to_dict()
    except Exception:
        return None
    finally:
        q.close()
    keep = [QUAL_PLAT[p] for p in plats if p in QUAL_PLAT]
    r = r[r["platform"].isin(keep)]
    dated = bool(d_from or d_to)
    if dated:
        r = r[(r["posted_month"] != "ALL") & r["posted_month"].notna()]
        if d_from:
            r = r[r["posted_month"] >= d_from[:7]]
        if d_to:
            r = r[r["posted_month"] <= d_to[:7]]
    else:
        r = r[r["posted_month"] == "ALL"]
    rows = []
    inv = {v: k for k, v in QUAL_PLAT.items()}
    for (plat, layer), g in r.groupby(["platform", "layer"]):
        def tot(c):
            return int(g[c].sum())
        def net(tag):
            n, pos, neg = tot(f"{tag}_n"), tot(f"{tag}_pos"), tot(f"{tag}_neg")
            return None if n < MIN_POOLED else 100.0 * (pos - neg) / n
        nb = tot("b_n")
        pos_p, pos_lo, pos_hi = wilson_interval([tot("b_pos")], [nb]) if nb >= MIN_POOLED else ([np.nan], [np.nan], [np.nan])
        neg_p, neg_lo, neg_hi = wilson_interval([tot("b_neg")], [nb]) if nb >= MIN_POOLED else ([np.nan], [np.nan], [np.nan])
        rows.append({"platform": inv[plat], "layer": layer, "scored": tot("a_n"), "no_text": tot("n_skipped"), "emoji_only": tot("n_emoji_only"),
                     "words_only": tot("t_n"), "confident": tot("c_n"), "uncertain": tot("n_uncertain"), "weak": tot("n_weak"),
                     "net_all": net("a"), "net_headline": net("b"), "net_words": net("t"), "net_confident": net("c"),
                     "pos_pct": None if np.isnan(pos_p[0]) else 100 * float(pos_p[0]), "pos_lo": None if np.isnan(pos_lo[0]) else 100 * float(pos_lo[0]), "pos_hi": None if np.isnan(pos_hi[0]) else 100 * float(pos_hi[0]),
                     "neg_pct": None if np.isnan(neg_p[0]) else 100 * float(neg_p[0]), "neg_lo": None if np.isnan(neg_lo[0]) else 100 * float(neg_lo[0]), "neg_hi": None if np.isnan(neg_hi[0]) else 100 * float(neg_hi[0])})
    order = {k: i for i, k in enumerate(PLATFORMS)}
    rows.sort(key=lambda x: (order.get(x["platform"], 99), x["layer"]))
    return {"rows": rows, "grain": "month" if dated else "all dates", "built": str(meta.get("built_at")), "conf_high": float(meta.get("conf_high", 0.6)),
            "conf_low": float(meta.get("conf_low", 0.45)), "min_pooled": MIN_POOLED, "ignores_theme_entity": True}


def _has(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name='aud' AND table_name = ?", [name]).fetchone()[0])
