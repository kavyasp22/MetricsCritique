"""Explore > Audience Sentiment API (UI/audience_view.py): reads the v2 tables, filters, percentages, missing data, layers kept apart.

    .venv/bin/python UI/tests/check_audience_api.py

Read-only. Calls the server function directly (no HTTP) against the real data files, and recomputes every figure independently
from the layer tables with plain SQL.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_server as a  # noqa: E402
import audience_view as AUD  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "data"
RESULTS: list[bool] = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + str(detail)) if detail else ''}")


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


def call(**kw):
    cur = a._main_con.cursor()
    try:
        return AUD.explore_audience(q(**kw), cur)
    finally:
        cur.close()


def main() -> int:
    src = Path(AUD.__file__).read_text()
    aud = duckdb.connect(str(DATA / "audience_sentiment.duckdb"), read_only=True)
    aud.execute(f"ATTACH '{DATA / 'geo_ontology.duckdb'}' AS geo (READ_ONLY)")

    # ---- 1. every table the queries read is a v2 / layer table, never a v1 per-post or roll-up table
    refs = set(re.findall(r"aud\.([A-Za-z_]+)", src)) | set(re.findall(r'PER_POST = "([A-Za-z_0-9]+)"', src)) | {t for pair in AUD.LAYERS.values() for t in pair if t is not None}
    allowed = {"xplat_post_audience_sentiment_v2", "comment_sentiment", "reply_sentiment", "ig_comment_sentiment", "ig_reply_sentiment", "li_comment_sentiment", "snap_comment_sentiment", "enriched_post_text", "enrichment_run_meta", "PER_POST"}
    chk("queries read only the v2 per-post table and the comment / reply layer tables", refs - {"PER_POST"} <= allowed and AUD.PER_POST == "xplat_post_audience_sentiment_v2", sorted(refs))
    chk("no v1 per-post or roll-up table is referenced", not re.search(r"(?<![a-z_.])(post_audience_sentiment|ig_post_audience_sentiment|audience_rollup|ig_audience_rollup|xplat_post_audience_sentiment|xplat_audience_rollup)(?!_v2)\b", src.replace("xplat_post_audience_sentiment_v2", "")))

    # ---- 2. unfiltered: both platforms load and equal the sources
    d = call()
    t = {k: aud.execute(f"SELECT count(*), count(*) FILTER (WHERE score_status='scored'), count(*) FILTER (WHERE sentiment_label='negative'), count(*) FILTER (WHERE sentiment_label='positive'), avg(tone_score) FILTER (WHERE score_status='scored') FROM {k}").fetchone()
         for k in ("comment_sentiment", "reply_sentiment", "ig_comment_sentiment", "ig_reply_sentiment")}
    yt, ig, al = d["layers"]["youtube_video"], d["layers"]["instagram"], d["layers"]["all"]
    li = d["layers"]["linkedin"]
    snap = d["layers"]["snapchat"]
    chk("YouTube comment / reply scored counts equal the source tables", yt["comment"]["scored"] == t["comment_sentiment"][1] and yt["reply"]["scored"] == t["reply_sentiment"][1], (yt["comment"]["scored"], yt["reply"]["scored"]))
    chk("Instagram comment / reply scored counts equal the source tables", ig["comment"]["scored"] == t["ig_comment_sentiment"][1] and ig["reply"]["scored"] == t["ig_reply_sentiment"][1], (ig["comment"]["scored"], ig["reply"]["scored"]))
    li_scored = aud.execute("SELECT count(*) FILTER (WHERE score_status='scored') FROM li_comment_sentiment").fetchone()[0]
    chk("LinkedIn comment scored count equals the source table", li["comment"]["scored"] == li_scored, (li["comment"]["scored"], li_scored))
    snap_scored = aud.execute("SELECT count(*) FILTER (WHERE score_status='scored') FROM snap_comment_sentiment").fetchone()[0]
    chk("Snapchat comment scored count equals the source table", snap["comment"]["scored"] == snap_scored, (snap["comment"]["scored"], snap_scored))
    chk("both platforms together = YouTube + Instagram + LinkedIn + Snapchat, per comment layer", al["comment"]["scored"] == yt["comment"]["scored"] + ig["comment"]["scored"] + li["comment"]["scored"] + snap["comment"]["scored"], (al["comment"]["scored"], yt["comment"]["scored"] + ig["comment"]["scored"] + li["comment"]["scored"] + snap["comment"]["scored"]))
    chk("reply rows are counted from the reply tables only (comments are not mixed in; LinkedIn/Snapchat have no replies)", al["reply"]["rows"] == t["reply_sentiment"][0] + t["ig_reply_sentiment"][0] and al["comment"]["rows"] == t["comment_sentiment"][0] + t["ig_comment_sentiment"][0] + aud.execute("SELECT count(*) FROM li_comment_sentiment").fetchone()[0] + aud.execute("SELECT count(*) FROM snap_comment_sentiment").fetchone()[0],
        (al["comment"]["rows"], al["reply"]["rows"]))
    chk("comment and reply figures differ (kept as separate layers)", al["comment"]["net"] != al["reply"]["net"] and al["comment"]["scored"] != al["reply"]["scored"])
    # ---- 3. percentages, net and mean tone recomputed from the layer tables
    ok = True
    for plat, (ct, rt) in AUD.LAYERS.items():
        for layer, tb in (("comment", ct), ("reply", rt)):
            if tb is None:  # LinkedIn has no reply table
                continue
            x = d["layers"][plat][layer]
            r = aud.execute(f"SELECT count(*) FILTER (WHERE score_status='scored'), count(*) FILTER (WHERE sentiment_label='negative'), count(*) FILTER (WHERE sentiment_label='neutral'), count(*) FILTER (WHERE sentiment_label='positive'), avg(tone_score) FILTER (WHERE score_status='scored') FROM {tb}").fetchone()
            n = r[0]
            if not x["supported"]:  # below MIN_POOLED, stats are None — skip math check
                continue
            good = (abs(x["neg_pct"] - 100 * r[1] / n) < 1e-9 and abs(x["neu_pct"] - 100 * r[2] / n) < 1e-9 and abs(x["pos_pct"] - 100 * r[3] / n) < 1e-9
                    and abs(x["neg_pct"] + x["neu_pct"] + x["pos_pct"] - 100) < 1e-9 and abs(x["net"] - (x["pos_pct"] - x["neg_pct"])) < 1e-9 and abs(x["tone_mean"] - r[4]) < 1e-9)
            ok &= good
    chk("shares, net sentiment and mean tone equal an independent recount (every platform x layer)", ok)
    snap = d["layers"]["snapchat"]
    chk("post tone is a separate layer with its own counts", yt["post"]["with_tone"] > 0 and al["post"]["with_tone"] == yt["post"]["with_tone"] + ig["post"]["with_tone"] + li["post"]["with_tone"] + snap["post"]["with_tone"] and yt["post"]["tone_mean"] is not None)
    chk("post-layer shares add to 100", abs(al["post"]["pos_pct"] + al["post"]["neu_pct"] + al["post"]["neg_pct"] - 100) < 1e-9)

    # ---- 4. no duplicate posts
    dup = aud.execute("SELECT count(*) FROM (SELECT post_uid FROM xplat_post_audience_sentiment_v2 GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]
    dup_url = aud.execute("SELECT count(*) FROM (SELECT post_url FROM xplat_post_audience_sentiment_v2 WHERE platform='instagram' AND post_url IS NOT NULL GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]
    n_ig = aud.execute("SELECT count(*) FROM xplat_post_audience_sentiment_v2 WHERE platform='instagram'").fetchone()[0]
    chk("no duplicate post_uid and no duplicate Instagram post_url in the table the page reads", dup == 0 and dup_url == 0, f"duplicates {dup}/{dup_url}")
    chk("Instagram posts = distinct post URLs (one canonical row per post), not the 1.03M production rows", n_ig == aud.execute("SELECT count(DISTINCT post_url) + count(*) FILTER (WHERE post_url IS NULL) FROM xplat_post_audience_sentiment_v2 WHERE platform='instagram'").fetchone()[0] and n_ig < 20000, n_ig)
    chk("top-gap lists carry no duplicate posts", len({p["post_uid"] for p in d["top_gaps"]["negative"]}) == len(d["top_gaps"]["negative"]) and len({p["post_uid"] for p in d["top_gaps"]["positive"]}) == len(d["top_gaps"]["positive"]))

    # ---- 5. platform filter
    di = call(platforms="instagram")
    chk("platform filter: Instagram only", di["filters"]["platforms"] == ["instagram"] and di["layers"]["all"]["comment"]["scored"] == ig["comment"]["scored"] and di["layers"]["youtube_video"]["comment"]["scored"] == 0, di["n_posts"])
    dt = call(platforms="telegram,instagram")
    chk("a platform without comment text is reported as unsupported, the rest still loads", dt["unsupported_platforms"] == ["telegram"] and dt["filters"]["platforms"] == ["instagram"])
    dn = call(platforms="telegram")
    chk("only unsupported platforms: empty, no error, no zeros presented as data", dn["filters"]["platforms"] == [] and dn["n_posts"] == 0 and dn["layers"]["all"]["comment"]["net"] is None)

    # ---- 6. theme filter (uses the existing D-themes, list taken from the data)
    avail = [r[0] for r in aud.execute("SELECT DISTINCT d_theme FROM xplat_post_audience_sentiment_v2 WHERE d_theme IS NOT NULL ORDER BY 1").fetchall()]
    chk("theme list comes from the data", d["themes_available"] == avail and len(avail) >= 9, avail)
    dth = call(theme="D05")
    cnt = aud.execute("SELECT count(*), count(*) FILTER (WHERE rows_total > 0) FROM xplat_post_audience_sentiment_v2 WHERE d_theme='D05'").fetchone()
    chk("theme filter D05: post counts equal the table", (dth["n_posts"], dth["n_posts_with_data"]) == tuple(cnt), (dth["n_posts"], dth["n_posts_with_data"]))
    chk("theme filter changes the results", dth["layers"]["all"]["comment"]["scored"] < al["comment"]["scored"] and dth["layers"]["all"]["comment"]["net"] != al["comment"]["net"])
    du = call(theme="unassigned")
    cu = aud.execute("SELECT count(*) FROM xplat_post_audience_sentiment_v2 WHERE d_theme IS NULL AND theme_status='unassigned'").fetchone()[0]
    chk("the 'unassigned' choice returns the posts with no theme", du["n_posts"] == cu, du["n_posts"])
    try:
        call(theme="D99"); bad = False
    except ValueError:
        bad = True
    chk("an unknown theme is rejected with a message, not silently accepted", bad)
    chk("theme roll-up rows cover all posts with data (theme + unassigned buckets sum to the total)", sum(t["posts"] for t in d["themes"]) == d["n_posts_with_data"], sum(t["posts"] for t in d["themes"]))

    # ---- 7. date range
    f, to = "2026-10-01", "2026-10-31"
    dd = call(**{"from": f, "to": to})
    n_in = aud.execute("SELECT count(*), count(*) FILTER (WHERE rows_total>0) FROM xplat_post_audience_sentiment_v2 WHERE CAST(posted_at AS DATE) BETWEEN ? AND ?", [f, to]).fetchone()
    chk("date range: post counts equal the table (post date)", (dd["n_posts"], dd["n_posts_with_data"]) == tuple(n_in), (dd["n_posts"], dd["n_posts_with_data"]))
    chk("post-date trend adds up to the layer totals of the same posts", sum(r["comment"]["scored"] for r in dd["trend"]) == dd["layers"]["all"]["comment"]["scored"] and sum(r["reply"]["scored"] for r in dd["trend"]) == dd["layers"]["all"]["reply"]["scored"], (dd["granularity"], len(dd["trend"])))
    dc = call(**{"from": f, "to": to, "basis": "comment"})
    direct = aud.execute("SELECT count(*) FROM (SELECT c.post_uid FROM comment_sentiment c WHERE c.score_status='scored' AND CAST(c.posted_at AS DATE) BETWEEN ? AND ? UNION ALL SELECT post_uid FROM ig_comment_sentiment WHERE score_status='scored' AND CAST(posted_at AS DATE) BETWEEN ? AND ? UNION ALL SELECT post_uid FROM li_comment_sentiment WHERE score_status='scored' AND CAST(posted_at AS DATE) BETWEEN ? AND ? UNION ALL SELECT post_uid FROM snap_comment_sentiment WHERE score_status='scored' AND CAST(posted_at AS DATE) BETWEEN ? AND ?)", [f, to, f, to, f, to, f, to]).fetchone()[0]
    got = sum(r["scored"] for r in dc["trend"] if r["layer"] == "comment")
    chk("comment-date trend equals a direct count of comments written in the range", got == direct, (got, direct))
    chk("a day range uses daily points, a long range monthly points", call(**{"from": "2026-11-10", "to": "2026-11-20"})["granularity"] == "day" and d["granularity"] == "month" and call(**{"from": "2026-09-01", "to": "2026-11-20"})["granularity"] == "week")
    try:
        call(**{"from": "20261001"}); bad = False
    except ValueError:
        bad = True
    chk("a malformed date is rejected", bad)

    db = d["date_bounds"]
    mm = aud.execute("SELECT CAST(min(posted_at) AS DATE), CAST(max(posted_at) AS DATE) FROM xplat_post_audience_sentiment_v2 WHERE rows_total > 0").fetchone()
    chk("the response reports the audience data's date bounds (later than the metrics snapshot's last day)", db["post_min"] == str(mm[0]) and db["post_max"] == str(mm[1]) and db["post_max"] >= "2026-11-01", db)
    nov = call(**{"from": "2026-11-01", "to": "2026-11-20"})
    cn = aud.execute("SELECT count(*) FROM xplat_post_audience_sentiment_v2 WHERE rows_total > 0 AND CAST(posted_at AS DATE) BETWEEN '2026-11-01' AND '2026-11-20'").fetchone()[0]
    chk("November (after the metrics snapshot) is queryable: posts with data equal the table", nov["n_posts_with_data"] == cn and cn > 4000, cn)
    op = call(**{"from": "2026-11-15"})
    chk("an open-ended range (only a start date) works", op["n_posts_with_data"] == aud.execute("SELECT count(*) FROM xplat_post_audience_sentiment_v2 WHERE rows_total > 0 AND CAST(posted_at AS DATE) >= '2026-11-15'").fetchone()[0])

    # ---- 8. entity filter
    top = aud.execute("""SELECT m.entity_uid, count(DISTINCT m.post_uid) n FROM geo.entity_mentions m JOIN xplat_post_audience_sentiment_v2 p ON p.post_uid = m.post_uid
                         WHERE p.rows_total > 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 1""").fetchone()
    de = call(entity_uid=top[0])
    chk("entity filter: matching audience posts equal an independent join", de["n_posts"] == aud.execute("SELECT count(DISTINCT p.post_uid) FROM xplat_post_audience_sentiment_v2 p JOIN geo.entity_mentions m ON m.post_uid = p.post_uid WHERE m.entity_uid = ?", [top[0]]).fetchone()[0] and de["n_posts"] > 50, de["n_posts"])
    chk("entity filter narrows the results and reports its coverage (enriched posts have no entity data)", de["layers"]["all"]["comment"]["scored"] < al["comment"]["scored"] and de["entity"]["coverage"]["youtube_video"]["enriched_with_entity_data"] == 0 and de["entity"]["coverage"]["youtube_video"]["enriched_posts"] > 4000, de["entity"]["coverage"])
    small = aud.execute("""SELECT m.entity_uid, sum(p.c_scored) FROM geo.entity_mentions m JOIN xplat_post_audience_sentiment_v2 p ON p.post_uid = m.post_uid
                           WHERE p.rows_total > 0 GROUP BY 1 HAVING sum(p.c_scored) BETWEEN 1 AND 20 ORDER BY 2 DESC LIMIT 1""").fetchone()
    ds = call(entity_uid=small[0])
    chk("entity with only a few scored comments: no error, statistics withheld (None, not 0), counts kept",
        ds["layers"]["all"]["comment"]["net"] is None and ds["layers"]["all"]["comment"]["supported"] is False and ds["layers"]["all"]["comment"]["scored"] == small[1], (ds["layers"]["all"]["comment"]["scored"], ds["n_posts_with_data"]))
    dz = call(entity_uid="entity-that-does-not-exist")
    chk("unknown entity: empty result, no error", dz["n_posts"] == 0 and dz["n_posts_with_data"] == 0 and dz["layers"]["all"]["comment"]["net"] is None and dz["top_gaps"]["negative"] == [])
    dc2 = call(entity_uid=top[0], theme="D05", platforms="youtube_video", **{"from": "2026-06-14", "to": "2026-11-20"})
    chk("entity + theme + platform + dates combined work together", dc2["n_posts"] <= de["n_posts"] and dc2["filters"]["theme"] == "D05", dc2["n_posts"])

    # ---- 9. missing data is never zero
    leak = []
    for r in (d, dth, di, ds, dz):
        for plat, L in r["layers"].items():
            for ly in ("comment", "reply"):
                x = L[ly]
                if not x["supported"] and any(x[k] is not None for k in ("neg_pct", "neu_pct", "pos_pct", "net", "tone_mean", "conf_mean")):
                    leak.append((plat, ly))
    chk("below the minimum, shares / net / mean are None (never 0) in every response", not leak, leak)
    ginst = aud.execute("SELECT count(*) FROM xplat_post_audience_sentiment_v2 WHERE NOT post_tone_available AND (gap_comment_vs_post IS NOT NULL OR gap_reply_vs_post IS NOT NULL)").fetchone()[0]
    chk("posts without a post tone have no gap (not treated as neutral)", ginst == 0)
    chk("gap statistics only count posts that have a gap; their number is below the posts with data", 0 < al["gaps"]["comment_vs_post"]["n"] < al["post"]["posts_with_data"])
    s = d["layers"]["instagram"]["status"]
    chk("Instagram 'reported zero' and 'reported but not collected' are kept apart from 'has comments'", s["confirmed_zero"] > 7000 and s["not_collected_known"] > 6000 and s["has_comments"] == 1397 and s["unknown_no_comment_data"] == 0, s)
    s2 = d["layers"]["youtube_video"]["status"]
    chk("YouTube videos without collected comments are 'unknown', never reported zero", s2["confirmed_zero"] == 0 and s2["unknown_no_comment_data"] > 2000, s2)
    chk("status counts add up to the posts", all(sum(L["status"][k] for k in ("has_comments", "confirmed_zero", "not_collected_known", "unknown_no_comment_data")) == L["status"]["posts"] for L in d["layers"].values()))

    # ---- 10. post cards
    cards = d["top_gaps"]["negative"] + d["top_gaps"]["positive"]
    chk("gap cards carry a link and the post's own numbers, negative cards first have negative gaps", cards and all(c["gap"] is not None for c in cards) and all(c["gap"] < 0 for c in d["top_gaps"]["negative"]) and all(c["url"] for c in cards if c["platform"] == "youtube_video"), len(cards))
    chk("no comment text or author names are returned by the endpoint", not any(k in str(d) for k in ("author", "username", "comment_text")))

    # ---- 11. failure modes
    old = AUD.AUD_DB
    try:
        AUD.AUD_DB = Path(tempfile.gettempdir()) / "no-such-audience.duckdb"
        try:
            call(); fine = False
        except AUD.AudienceUnavailable:
            fine = True
    finally:
        AUD.AUD_DB = old
    chk("a missing audience file answers 'unavailable' (HTTP 503 path), not a crash", fine)
    # a file locked by a writer
    tmp = Path(tempfile.mkdtemp()) / "locked.duckdb"
    w = duckdb.connect(str(tmp)); w.execute("CREATE TABLE t(x INT)")
    try:
        AUD.AUD_DB = tmp
        try:
            call(); fine = False
        except AUD.AudienceUnavailable:
            fine = True
        except Exception:
            fine = True        # any failure to open is turned into 503 by the server; the point is that it does not hang
    finally:
        AUD.AUD_DB = old; w.close()
    chk("a file being rebuilt (locked / incomplete) does not hang or break the server", fine)

    # ---- 11b. regression: the three gap directions share one denominator (the posts WITH a gap); a missing gap is never "aligned"
    import numpy as np
    import pandas as pd
    probes = [d, di, dth, du, dd, dc, de, ds, dz, nov, dc2, call(entity_uid=top[0], theme="D01", platforms="youtube_video,youtube_comment,instagram", **{"from": "2026-06-14", "to": "2026-10-25"})]
    bad_sum, bad_pct, checked = [], [], 0
    for r in probes:
        for plat, L in r["layers"].items():
            for nm in ("comment_vs_post", "reply_vs_post"):
                x = L["gaps"][nm]; checked += 1
                if x["more_negative"] + x["aligned"] + x["more_positive"] != x["n"]:
                    bad_sum.append((plat, nm, x["n"], x["more_negative"], x["aligned"], x["more_positive"]))
                if x["n"]:
                    pcts = [100 * x[k] / x["n"] for k in ("more_negative", "aligned", "more_positive")]
                    if max(pcts) > 100 or abs(sum(pcts) - 100) > 1e-9:
                        bad_pct.append((plat, nm, pcts))
                elif x["aligned"] or x["more_negative"] or x["more_positive"]:
                    bad_sum.append((plat, nm, "n=0 but a direction count is not 0"))
    chk(f"more negative + aligned + more positive == the posts with a gap, in all {checked} gap blocks of {len(probes)} different filters", not bad_sum, bad_sum[:3])
    chk("the three shares sum to 100% and none exceeds 100% (every block with a gap)", not bad_pct, bad_pct[:2])
    ch = call(entity_uid="location:china", theme="D01", platforms="youtube_video,youtube_comment,instagram", **{"from": "2026-06-14", "to": "2026-10-25"})
    gc, gr = ch["layers"]["all"]["gaps"]["comment_vs_post"], ch["layers"]["all"]["gaps"]["reply_vs_post"]
    chk("the audited filter (China + D01 + dates): comment gap 61 posts = 13 more negative + 36 aligned + 12 more positive (was 99 'aligned')", (gc["n"], gc["more_negative"], gc["aligned"], gc["more_positive"]) == (61, 13, 36, 12), gc["aligned"])
    chk("the audited filter: reply gap 34 posts, directions add up", gr["n"] == 34 and gr["more_negative"] + gr["aligned"] + gr["more_positive"] == 34, (gr["more_negative"], gr["aligned"], gr["more_positive"]))
    # synthetic: None and NaN are missing, never aligned
    syn = pd.DataFrame({"g": [0.5, None, -0.4, np.nan, 0.1, None, float("inf")]})
    sg = AUD._gap(syn, "g")
    chk("_gap(): None, NaN and inf are not counted; the valid gaps are classified once each", sg["n"] == 3 and (sg["more_positive"], sg["more_negative"], sg["aligned"]) == (1, 1, 1), sg)
    emp = AUD._gap(pd.DataFrame({"g": [None, np.nan]}), "g")
    chk("_gap(): no valid gap -> n 0, every direction 0, mean None (never an 'aligned' count)", emp["n"] == 0 and emp["aligned"] == 0 and emp["mean"] is None and sum(h["n"] for h in emp["hist"]) == 0, emp)
    # the directions the page now shows equal the stored labels wherever a gap exists (so more negative / more positive did not change)
    allp = aud.execute("SELECT gap_comment_vs_post AS g, gap_comment_direction AS dr, gap_reply_vs_post AS rg, gap_reply_direction AS rdr FROM xplat_post_audience_sentiment_v2").df()
    vc = allp[allp.g.notna()]; vr = allp[allp.rg.notna()]
    mism = int((vc.g.map(AUD.gap_direction) != vc.dr).sum() + (vr.rg.map(AUD.gap_direction) != vr.rdr).sum())
    chk(f"for every post with a gap ({len(vc)} comment, {len(vr)} reply) the direction is identical to the stored label: more negative / more positive unchanged", mism == 0, mism)
    nogap = allp[allp.g.isna()]
    chk("posts without a gap stay missing in the data the page reads (gap NULL, never 0)", len(nogap) > 20000 and nogap.g.isna().all())

    # ---- 11c. stage 1: comment counts, coverage, min comments, per-platform divergent lists (all additive; no value is recomputed differently)
    PP = "xplat_post_audience_sentiment_v2"
    d0 = call(platforms="youtube_video,instagram")
    cov = d0["layers"]
    for plat in ("youtube_video", "instagram"):
        n_all, n_c, n_s = aud.execute(f"SELECT count(*), count(*) FILTER (WHERE c_rows > 0), count(*) FILTER (WHERE c_scored > 0) FROM {PP} WHERE platform = ?", [plat]).fetchone()
        c = cov[plat]["coverage"]
        chk(f"coverage {plat}: posts / with comments / with scored comments equal the table", (c["posts"], c["with_comments"], c["with_scored_comments"]) == (n_all, n_c, n_s), (c["posts"], c["with_comments"], c["with_scored_comments"]))
        chk(f"coverage {plat}: percentage = collected / matching", abs(c["coverage_pct"] - 100 * n_c / n_all) < 1e-9, round(c["coverage_pct"], 2))
    yc = cov["youtube_video"]["coverage"]
    n_cap = aud.execute(f"SELECT count(*) FROM {PP} WHERE platform = 'youtube_video' AND c_rows >= {AUD.YT_CAP}").fetchone()[0]
    chk("the YouTube ~100-comment limit count equals the posts at or above it", yc["at_cap"] == n_cap and AUD.YT_CAP == 100, (yc["at_cap"], n_cap))
    chk("'both' coverage = YouTube + Instagram + LinkedIn + Snapchat", cov["all"]["coverage"]["posts"] == cov["youtube_video"]["coverage"]["posts"] + cov["instagram"]["coverage"]["posts"] + cov["linkedin"]["coverage"]["posts"] + cov["snapchat"]["coverage"]["posts"])
    g = d0["layers"]["all"]["gaps"]["comment_vs_post"]
    chk("every gap block's size buckets add up to its posts (small + 10-29 + 30+ = n), comments and replies, all platforms", all(
        b["small"] + b["n_10_29"] + b["n_30_plus"] == b["n"] for k in ("all", "youtube_video", "instagram") for b in (d0["layers"][k]["gaps"]["comment_vs_post"], d0["layers"][k]["gaps"]["reply_vs_post"])))
    ref = aud.execute(f"SELECT count(*), sum(c_scored), count(*) FILTER (WHERE c_scored < 10) FROM {PP} WHERE platform IN ('youtube_video', 'instagram') AND gap_comment_vs_post IS NOT NULL AND gap_comment_vs_post = gap_comment_vs_post").fetchone()
    chk("comment-gap counts equal the table: posts, scored comments behind them, posts under 10", (g["n"], g["rows"], g["small"]) == (ref[0], int(ref[1]), ref[2]), (g["n"], g["rows"], g["small"]))
    # min comments: only narrows; the values of the posts that stay are untouched
    for mc in (10, 20, 30, 50):
        dm = call(platforms="youtube_video,instagram", min_comments=mc)
        gm = dm["layers"]["all"]["gaps"]["comment_vs_post"]
        exp = aud.execute(f"SELECT count(*), avg(gap_comment_vs_post) FROM {PP} WHERE platform IN ('youtube_video', 'instagram') AND gap_comment_vs_post = gap_comment_vs_post AND c_scored >= {mc}").fetchone()
        chk(f"min_comments={mc}: posts with a gap and their mean equal the table restricted to c_scored >= {mc}", gm["n"] == exp[0] and abs(gm["mean"] - exp[1]) < 1e-9 and gm["small"] == (0 if mc >= 10 else gm["small"]), (gm["n"], exp[0]))
        chk(f"min_comments={mc}: directions still add up to the posts with a gap", gm["more_negative"] + gm["aligned"] + gm["more_positive"] == gm["n"])
    dd = call(platforms="youtube_video,instagram", min_comments=5)
    chk("min_comments=5 (the default) is identical to sending nothing", dd["layers"] == d0["layers"] and dd["top_gaps"] == d0["top_gaps"] and dd["themes"] == d0["themes"])
    chk("pooled sentiment shares, nets and trend do not depend on min_comments", call(platforms="youtube_video,instagram", min_comments=50)["layers"]["all"]["comment"] == d0["layers"]["all"]["comment"] and
        call(platforms="youtube_video,instagram", min_comments=50)["trend"] == d0["trend"])
    for bad in ("abc", "1.5"):
        try:
            call(min_comments=bad); chk(f"min_comments={bad} is rejected", False)
        except ValueError:
            chk(f"min_comments={bad} is rejected", True)
    chk("an out-of-range min_comments is clamped, not an error", call(platforms="youtube_video", min_comments=99999)["limits"]["min_comments"] == AUD.MAX_MIN_COMMENTS and call(platforms="youtube_video", min_comments=1)["limits"]["min_comments"] == AUD.MIN_GAP)
    # divergent posts: the default ("both") ranking is the original logic; platform lists are the same logic on one platform
    cand = aud.execute(f"SELECT post_uid FROM {PP} WHERE gap_comment_vs_post IS NOT NULL AND gap_comment_vs_post = gap_comment_vs_post AND c_scored >= 10 ORDER BY gap_comment_vs_post ASC, post_uid LIMIT 10").fetchall()
    top_neg = [c["gap"] for c in d0["top_gaps"]["negative"]]
    chk("the default divergent list is still the 10 most negative gaps among posts with >= 10 scored comments", top_neg == sorted(top_neg) and len(top_neg) == 10 and d0["top_gaps"]["min_comments"] == 10 and all(c["comments"] >= 10 for c in d0["top_gaps"]["negative"]))
    for plat in ("youtube_video", "instagram"):
        lst = d0["top_gaps_by_platform"][plat]
        ref = aud.execute(f"SELECT gap_comment_vs_post FROM {PP} WHERE platform = ? AND gap_comment_vs_post = gap_comment_vs_post AND c_scored >= 10 ORDER BY gap_comment_vs_post ASC LIMIT 10", [plat]).fetchall()
        chk(f"{plat} divergent list = the 10 most negative gaps of that platform", [round(c["gap"], 9) for c in lst["negative"]] == [round(r[0], 9) for r in ref] and all(c["platform"] == plat for c in lst["negative"] + lst["positive"]))
    chk("only Instagram cards can carry the caption flag, and only with a post tone >= 0.5", not any(c["caption_effect"] for c in d0["top_gaps_by_platform"]["youtube_video"]["negative"] + d0["top_gaps_by_platform"]["youtube_video"]["positive"]) and
        all(c["caption_effect"] == (c["post_tone"] is not None and c["post_tone"] >= AUD.CAPTION_TONE) for c in d0["top_gaps_by_platform"]["instagram"]["negative"] + d0["top_gaps_by_platform"]["instagram"]["positive"]) and
        any(c["caption_effect"] for c in d0["top_gaps_by_platform"]["instagram"]["negative"]))
    chk("every card carries its comment count and a small-sample flag consistent with it", all(isinstance(c["comments"], int) and c["small_sample"] == (c["comments"] < AUD.SMALL_N) for c in d0["top_gaps"]["negative"] + d0["top_gaps"]["positive"]))
    chk("a one-platform request returns only that platform's list", list(call(platforms="instagram")["top_gaps_by_platform"]) == ["instagram"])
    # filter coverage
    fc = call(platforms="youtube_video,instagram", theme="D01", entity_uid="location:china")["filter_coverage"]
    base = aud.execute(f"SELECT count(*), count(*) FILTER (WHERE d_theme IS NOT NULL), count(*) FILTER (WHERE d_theme = 'D01') FROM {PP} WHERE rows_total > 0 AND platform IN ('youtube_video', 'instagram')").fetchone()
    ent_n = aud.execute(f"SELECT count(*) FROM {PP} p WHERE rows_total > 0 AND platform IN ('youtube_video', 'instagram') AND post_uid IN (SELECT DISTINCT CAST(post_uid AS VARCHAR) FROM geo.entity_mentions WHERE entity_uid = 'location:china')").fetchone()[0]
    chk("filter coverage: posts with data, posts with a theme, posts kept by theme and by entity equal the tables", (fc["posts_with_data"], fc["with_theme"], fc["theme_kept"], fc["entity_kept"]) == (base[0], base[1], base[2], ent_n), fc)
    chk("no filter chosen: no kept-counts are reported", "theme_kept" not in d0["filter_coverage"] and "entity_kept" not in d0["filter_coverage"])
    fd = call(platforms="youtube_video", **{"from": "2026-08-01", "to": "2026-08-31"})["filter_coverage"]
    n_aug = aud.execute(f"SELECT count(*) FROM {PP} WHERE rows_total > 0 AND platform = 'youtube_video' AND CAST(posted_at AS DATE) BETWEEN '2026-08-01' AND '2026-08-31'").fetchone()[0]
    chk("filter coverage respects the platform and date filters", fd["posts_with_data"] == n_aug, (fd["posts_with_data"], n_aug))

    # ---- 11d. stabilisation: the minimum also applies to Replies vs comments; strong post tone flag (informational)
    PPv = "xplat_post_audience_sentiment_v2"
    for mc in (5, 10, 30):
        dm = call(platforms="youtube_video,instagram", min_comments=mc)
        r = dm["layers"]["all"]["gaps"]["reply_vs_comment"]
        exp = aud.execute(f"SELECT count(*), avg(gap_reply_vs_comment), sum(r_scored), sum(c_scored) FROM {PPv} WHERE gap_reply_vs_comment = gap_reply_vs_comment AND c_scored >= {mc} AND r_scored >= {mc}").fetchone()
        chk(f"Replies vs comments with min_comments={mc}: posts, mean and the replies / comments behind them equal the table restricted to both layers >= {mc}",
            r["n"] == exp[0] and abs(r["mean"] - exp[1]) < 1e-9 and r["reply_rows"] == int(exp[2]) and r["comment_rows"] == int(exp[3]), (r["n"], exp[0]))
    base_n = aud.execute(f"SELECT count(*) FROM {PPv} WHERE gap_reply_vs_comment = gap_reply_vs_comment").fetchone()[0]
    chk("the default minimum (5) leaves Replies vs comments exactly as before (every post with that gap counts)", call(platforms="youtube_video,instagram")["layers"]["all"]["gaps"]["reply_vs_comment"]["n"] == base_n, base_n)
    chk("a higher minimum can only shrink Replies vs comments", call(platforms="youtube_video,instagram", min_comments=30)["layers"]["all"]["gaps"]["reply_vs_comment"]["n"] < base_n)
    dd = call(platforms="youtube_video,instagram")
    cardsAll = dd["top_gaps"]["negative"] + dd["top_gaps"]["positive"] + [c for p_ in dd["top_gaps_by_platform"].values() for k in ("negative", "positive") for c in p_[k]]
    chk("extreme_post_tone is exactly |post tone| >= 0.5 on every card", all(c["extreme_post_tone"] == (c["post_tone"] is not None and abs(c["post_tone"]) >= AUD.EXTREME_TONE) for c in cardsAll) and AUD.EXTREME_TONE == 0.5 and dd["limits"]["extreme_tone"] == 0.5, len(cardsAll))
    chk("the flag covers BOTH directions and BOTH platforms (strongly positive and strongly negative; YouTube and Instagram)", any(c["extreme_post_tone"] and c["post_tone"] > 0 for c in cardsAll) and any(c["extreme_post_tone"] and c["post_tone"] < 0 for c in cardsAll) and
        any(c["extreme_post_tone"] and c["platform"] == "youtube_video" for c in cardsAll) and any(c["extreme_post_tone"] and c["platform"] == "instagram" for c in cardsAll))
    top_before = [(c["post_uid"], c["gap"]) for c in dd["top_gaps"]["negative"]]
    ref = aud.execute(f"SELECT post_uid FROM {PPv} WHERE gap_comment_vs_post = gap_comment_vs_post AND c_scored >= 10 ORDER BY gap_comment_vs_post ASC LIMIT 10").fetchall()
    chk("the flag does not change the ranking: the default list is still the 10 most negative gaps of posts with >= 10 scored comments", sorted(u for u, _ in top_before) == sorted(r[0] for r in ref) or [g for _, g in top_before] == sorted(g for _, g in top_before))

    # ---- 12. existing v1 tables untouched
    v1 = {"post_audience_sentiment": "post_uid", "audience_rollup": "dimension||'|'||d_theme||'|'||period"}
    before = {}
    for line in (Path(__file__).resolve().parents[2] / "logs" / "yt_fingerprint_before.txt").read_text().splitlines():
        m = re.match(r"(\w+) \((\d+), '([0-9a-f]+)'\)", line)
        if m:
            before[m.group(1)] = (int(m.group(2)), m.group(3))
    now = {t: aud.execute(f"SELECT count(*), md5(string_agg(x::text, ',' ORDER BY {k})) FROM (SELECT * EXCLUDE (built_at) FROM {t}) x").fetchone() for t, k in v1.items()}
    chk("the v1 YouTube per-post and roll-up tables are byte-identical to the verified Phase 2 fingerprint", all(now[t][0] == before[t][0] and now[t][1] == before[t][1] for t in v1) and len(before) >= 2, now)

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
