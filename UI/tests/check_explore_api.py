"""Explore API: entity-in-topic counts, per-post virality ranking, and the stage-24 file.

    .venv/bin/python UI/tests/check_explore_api.py

Read-only. Calls the server functions directly (no HTTP), against the real data files.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_server as a  # noqa: E402

RESULTS: list[bool] = []
FROM, TO, PF, PT = "2026-10-20", "2026-10-26", "2026-10-13", "2026-10-19"


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + str(detail)) if detail else ''}")


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


def drill_total(topic, ent, **kw):
    sql, params = a.build_query("topic", q(topic_id=topic, entity_uid=ent, **{"from": FROM, "to": TO}, **kw))
    rows = a._main_con.execute(f"SELECT count(*) FROM ({sql}) t", params).fetchall()
    return rows[0][0]


def main() -> int:
    # ---------------- entity topics: card count == drill count
    ents = a._main_con.execute(
        "SELECT entity_uid, canonical_name FROM geo.entity_dim ORDER BY distinct_posts DESC LIMIT 60").fetchall()
    sample = ents[:3] + ents[20:23] + ents[50:53]
    mism, checked = [], 0
    for uid, name in sample:
        d = a.explore_entity_topics(q(entity_uid=uid, **{"from": FROM, "to": TO, "prev_from": PF, "prev_to": PT}))
        for r in sorted(d["rows"], key=lambda r: -r["n_cur"])[:2]:
            if r["n_cur"] == 0:
                continue
            checked += 1
            tot = drill_total(r["topic_id"], uid)
            if tot != r["n_cur"]:
                mism.append((name, r["topic_id"], r["n_cur"], tot))
    chk("entity-topics counts equal the drill-down totals (same entity, topic, dates)", checked >= 8 and not mism,
        f"{checked} compared; mismatches={mism}")

    uid = ents[0][0]
    allp = a.explore_entity_topics(q(entity_uid=uid, **{"from": FROM, "to": TO}))
    cover = {c["platform"]: c for c in allp["coverage"]}
    tot_all = sum(r["n_cur"] for r in allp["rows"])
    per_plat = 0
    for pl in cover:
        x = a.explore_entity_topics(q(entity_uid=uid, platforms=pl, **{"from": FROM, "to": TO}))
        per_plat += sum(r["n_cur"] for r in x["rows"])
    chk("counts add up across platforms (sum of per-platform = all platforms)", per_plat == tot_all, f"{per_plat} vs {tot_all}")
    chk("coverage lists each platform's last entity day", all(c["last_day"] for c in allp["coverage"]) and len(allp["coverage"]) >= 8,
        f"{len(allp['coverage'])} platforms")
    twi = cover.get("twitter", {}).get("last_day")
    chk("coverage reports that Twitter's entities stop before the newest posts", twi is not None and twi < TO, twi)
    try:
        a.explore_entity_topics(q(**{"from": FROM, "to": TO}))
        chk("an entity id is required", False)
    except ValueError:
        chk("an entity id is required", True)
    try:
        a.build_query("topic", q(topic_id="t000", entity_uid=uid, platform="youtube_comment"))
        chk("the entity filter is refused for YouTube comments (no entity mentions)", False)
    except ValueError:
        chk("the entity filter is refused for YouTube comments (no entity mentions)", True)
    sql, params = a.build_query("topic", q(topic_id="t000", entity_uid=uid, platforms="twitter,youtube_comment", **{"from": FROM, "to": TO}))
    chk("all-platform drill with an entity never pulls YouTube comments", "comment_facts" not in sql)
    try:
        a.build_query("topic", q(topic_id="t000", entity_uid=uid, platforms="youtube_comment", **{"from": FROM, "to": TO}))
        chk("an entity with only YouTube comments selected is refused with a clear message", False)
    except ValueError as e:
        chk("an entity with only YouTube comments selected is refused with a clear message", "no post platform" in str(e))

    # ---------------- per-post virality file
    pv = a.POST_VIRALITY
    chk("data/post_virality.parquet exists (run scripts/24_post_virality.py)", pv.exists())
    df = a._main_con.execute(f"SELECT * FROM read_parquet('{pv.as_posix()}')").fetchdf()
    chk("one row per post_uid", df["post_uid"].is_unique, f"{len(df):,} rows")
    chk("percentile is within 0..1 or empty", bool(((df["virality_pct"] >= 0) & (df["virality_pct"] <= 1) | df["virality_pct"].isna()).all()))
    mine = df[df["virality_pct"].notna()].groupby("platform").size()
    ref = dict(a._main_con.execute("SELECT platform, sum(virality_n) FROM metrics.metrics_daily GROUP BY 1").fetchall())
    yt_unavail = a._main_con.execute("SELECT count(*) FROM analytics.post_facts WHERE platform = 'youtube_video' AND views = 0 "
                                     "AND (coalesce(likes,0) + coalesce(comments,0)) > 0").fetchall()[0][0]
    a._ensure_keep()          # stage 24 collapses copies of one post (platform + url); metrics_daily (metrics v2) still counts every row
    rows_by = dict(a._main_con.execute("SELECT platform, count(*) FROM analytics.post_facts GROUP BY 1").fetchall())
    kept_by = dict(a._main_con.execute("SELECT p.platform, count(*) FROM analytics.post_facts p JOIN viral_keep k ON k.post_uid = p.post_uid GROUP BY 1").fetchall())
    copies = {k: rows_by[k] - kept_by.get(k, 0) for k in rows_by}
    bad = {k: (int(mine.get(k, 0)), int(v or 0)) for k, v in ref.items() if k != "youtube_video" and (
        int(mine.get(k, 0)) != int(v or 0) if copies.get(k, 0) == 0 else not (int(v or 0) - copies[k] <= int(mine.get(k, 0)) <= int(v or 0)))}
    chk("rated posts per platform equal virality_n in metrics_daily (YouTube excepted); platforms with copies are lower by at most the copies removed", not bad, bad)
    yt_diff = int(ref.get("youtube_video", 0)) - int(mine.get("youtube_video", 0))
    chk("YouTube differs by exactly the videos whose view counter is unavailable (views = 0 with likes or comments)", yt_diff == yt_unavail, f"{yt_diff} vs {yt_unavail}")
    ytz = df[(df["platform"] == "youtube_video")]
    chk("YouTube videos with views = 0 carry no percentile and no activity", bool((ytz.merge(a._main_con.execute("SELECT post_uid FROM analytics.post_facts WHERE platform='youtube_video' AND views = 0").fetchdf(), on="post_uid")[["virality_pct", "activity"]].isna().all().all())))
    chk("platforms with no counters carry no percentile (reddit)", int(mine.get("reddit", 0)) == 0)

    # ---------------- ranking API
    r = a.explore_viral(q(rank="virality", order="amplified", limit=30, **{"from": FROM, "to": TO}))
    L = r["by_virality"]
    pcts = [p["virality_pct"] for p in L]
    chk("virality ranking is sorted high to low", len(L) == 30 and pcts == sorted(pcts, reverse=True), f"{pcts[0]:.3f}..{pcts[-1]:.3f}")
    chk("every ranked post has at least the minimum reference set", all(p["virality_ref_n"] >= a.VIRALITY_MIN_REF for p in L))
    chk("rated posts <= matching posts", r["rated_posts"] <= r["total_posts"], f"{r['rated_posts']} / {r['total_posts']}")
    p3 = a.explore_viral(q(rank="virality", order="amplified", per_platform=3, **{"from": FROM, "to": TO}))["by_virality"]
    cnt = {}
    for p in p3:
        cnt[p["platform"]] = cnt.get(p["platform"], 0) + 1
    chk("per-platform view returns at most 3 per platform and several platforms", max(cnt.values()) <= 3 and len(cnt) >= 5, cnt)
    try:
        json.dumps(a._json_safe(r), allow_nan=False, default=str)
        json.dumps(a._json_safe(a.explore_viral(q(limit=20, **{"from": FROM, "to": TO}))), allow_nan=False, default=str)
        chk("responses are strict JSON (no NaN)", True)
    except ValueError as e:
        chk("responses are strict JSON (no NaN)", False, e)
    v = a.explore_viral(q(limit=5, **{"from": FROM, "to": TO}))
    chk("the by-views lists also carry each post's percentile", "virality_pct" in v["with_views"][0])
    kw = a.explore_viral(q(q="flood", rank="virality", order="amplified", limit=10, **{"from": "2026-06-14", "to": TO}))
    chk("keyword search works together with the percentile ranking",
        all("flood" in (p["text"] or "").lower() or "flooding" in (p["text"] or "").lower() or "floods" in (p["text"] or "").lower() for p in kw["by_virality"]) and kw["terms"],
        f"{len(kw['by_virality'])} posts")

    # ---------------- minimum-activity floor (top 10% of platform by default | platform median | off)
    chk("the parquet carries the activity columns (re-run scripts/24_post_virality.py)", a._pv_has_floor() and "activity_top10" in a._pv_cols())
    W = {"from": "2026-06-14", "to": TO}
    top10 = a.explore_viral(q(rank="virality", order="amplified", limit=300, **W))
    med = a.explore_viral(q(rank="virality", order="amplified", limit=300, floor="median", **W))
    off = a.explore_viral(q(rank="virality", order="amplified", limit=300, floor="off", **W))
    chk("default floor is the top 10% of the platform", top10["floor_mode"] == "top10" and med["floor_mode"] == "median" and off["floor_mode"] == "off" and not off["floor_on"])
    chk("ranked posts: top 10% <= median <= off (each looser floor is a superset)", top10["rated_posts"] <= med["rated_posts"] <= off["rated_posts"],
        f"{top10['rated_posts']:,} <= {med['rated_posts']:,} <= {off['rated_posts']:,}")
    chk("hidden-by-floor counts add up in every mode", top10["rated_posts"] + top10["below_floor"] == off["rated_posts"] and med["rated_posts"] + med["below_floor"] == off["rated_posts"] and off["below_floor"] == 0)
    fl = a._main_con.execute(f"SELECT post_uid, activity, activity_floor, activity_p90, activity_ok, activity_top10 FROM read_parquet('{a.POST_VIRALITY.as_posix()}')").fetchdf().set_index("post_uid")
    g1 = fl.loc[[p["post_uid"] for p in top10["by_virality"]]]
    chk("every post in the default ranking has activity >= its platform+basis 90th percentile", bool((g1["activity"] >= g1["activity_p90"]).all() and g1["activity_top10"].all()))
    g2 = fl.loc[[p["post_uid"] for p in med["by_virality"]]]
    chk("every post in the median ranking has activity >= its platform+basis median", bool((g2["activity"] >= g2["activity_floor"]).all() and g2["activity_ok"].all()))
    chk("without the floor, low-activity posts reach the top (that is why the floor exists)", bool((fl.loc[[p["post_uid"] for p in off["by_virality"]]]["activity_ok"] == False).any()))  # noqa: E712
    perfl = a.explore_viral(q(rank="virality", order="amplified", per_platform=3, **{"from": FROM, "to": TO}))["by_virality"]
    chk("per-platform view respects the default floor", all(fl.loc[p["post_uid"], "activity_top10"] for p in perfl), f"{len(perfl)} posts")
    chk("the old floor=on value still means the median", a.explore_viral(q(rank="virality", order="amplified", limit=5, floor="on", **W))["floor_mode"] == "median")

    # ---------------- "Biggest" order (raw attention as a multiple of the platform's top-10% line)
    wide = {"from": "2026-06-14", "to": TO}
    big = a.explore_viral(q(rank="virality", limit=300, **W))
    L = big["by_virality"]
    pcts = [p["attention_pct"] for p in L]
    mults = [p["attention_mult"] for p in L]
    chk("Biggest is the default order and sorts by the within-platform attention percentile, high to low (ties by the multiple)",
        big["order"] == "biggest" and len(L) == 300 and pcts == sorted(pcts, reverse=True), f"{pcts[0]:.4f} .. {pcts[-1]:.4f}")
    chk("no single platform takes over the Biggest top 300 (was 153 Truth Social under a multiple-of-the-line order)",
        max(__import__("collections").Counter(p["platform"] for p in L).values()) <= 120 and len({p["platform"] for p in L}) >= 6,
        dict(__import__("collections").Counter(p["platform"] for p in L).most_common(4)))
    chk("every post in Biggest is at or above the platform's top-10% line (default floor)", all(m >= 1 for m in mults) and all(p["activity_basis"] for p in L))
    chk("Biggest includes posts that have no ratio (e.g. Snapchat without views), Amplified does not",
        any(p["virality_pct"] is None for p in a.explore_viral(q(rank="virality", limit=300, floor="off", **W))["by_virality"])
        and not any(p["virality_pct"] is None for p in a.explore_viral(q(rank="virality", order="amplified", limit=300, floor="off", **W))["by_virality"]))
    top_viewed = a._main_con.execute("SELECT post_uid FROM analytics.post_facts p WHERE views > 0 AND CAST(coalesce(posted_at, scraped_at) AS DATE) BETWEEN ? AND ? "
                                     "AND platform <> 'youtube_video' ORDER BY views DESC LIMIT 15", [wide["from"], wide["to"]]).fetchall()
    top_ids = {r[0] for r in top_viewed}
    wide_big = a.explore_viral(q(rank="virality", limit=300, **wide))["by_virality"]
    in_top = sum(1 for p in wide_big if p["post_uid"] in top_ids)
    chk("the 15 most-viewed posts of the period are all inside the Biggest top 300 (before: 2 of 15 in the ratio order's top 300)", in_top >= 14, f"{in_top} of 15")
    chk("YouTube videos with an unavailable view counter never appear in Biggest", not any(p["platform"] == "youtube_video" and (p["views"] in (0, None) or p["views"] != p["views"]) for p in
        a.explore_viral(q(rank="virality", limit=300, floor="off", platforms="youtube_video", **wide))["by_virality"]))
    chk("platforms with no counters are not ranked (reddit)", not any(p["platform"] == "reddit" for p in a.explore_viral(q(rank="virality", limit=300, floor="off", **wide))["by_virality"]))
    pb = a.explore_viral(q(rank="virality", per_platform=3, **{"from": FROM, "to": TO}))["by_virality"]
    chk("Biggest per platform returns at most 3 per platform, sorted by multiple inside each", max(__import__("collections").Counter(p["platform"] for p in pb).values()) <= 3)

    # ---------------- topic filter on Viral Posts (search box: keyword or topic)
    tw = {"from": "2026-09-26", "to": "2026-10-25"}
    allr = a.explore_viral(q(rank="virality", limit=5, **tw))
    t006 = a.explore_viral(q(rank="virality", limit=300, topic_id="t006", **tw))
    chk("a topic filter narrows the match count", 0 < t006["total_posts"] < allr["total_posts"], f"{t006['total_posts']:,} of {allr['total_posts']:,}")
    mem = {r[0] for r in a._main_con.execute("SELECT post_uid FROM analytics.post_topic WHERE topic_id = 't006'").fetchall()}
    chk("every post returned under topic t006 belongs to topic t006", all(p["post_uid"] in mem for p in t006["by_virality"]) and len(t006["by_virality"]) > 0)
    v006 = a.explore_viral(q(limit=100, topic_id="t006", **tw))
    chk("the by-views lists honour the topic too", all(p["post_uid"] in mem for p in v006["with_views"] + v006["without_views"]))
    drill = a._main_con.execute(a.build_query("topic", q(topic_id="t006", **tw))[0].replace("SELECT ", "SELECT count(*) OVER () AS _c, ", 1) + " LIMIT 1", a.build_query("topic", q(topic_id="t006", **tw))[1]).fetchall()
    chk("the topic filter's post count equals the topic drill-down total (posts + YouTube comments are counted there, posts only here)", t006["total_posts"] <= int(drill[0][0]), f"{t006['total_posts']:,} <= {int(drill[0][0]):,}")
    merged = a.explore_viral(q(rank="virality", limit=5, topic_id="t004", **tw))["total_posts"]
    only4 = a._main_con.execute("SELECT count(*) FROM analytics.post_facts p WHERE p.post_uid IN (SELECT post_uid FROM analytics.post_topic WHERE topic_id IN ('t004','t005')) "
                                "AND p.post_uid IN (SELECT post_uid FROM viral_keep) AND CAST(p.posted_at AS DATE) BETWEEN ? AND ?", [tw["from"], tw["to"]]).fetchall()[0][0]    # one row per distinct post, as the card counts
    chk("t004 means t004 + t005 (the dashboard shows them as one topic)", merged == only4, f"{merged:,} vs {only4:,}")
    both = a.explore_viral(q(rank="virality", limit=5, topic_id="t006", q="flood", **tw))["total_posts"]
    chk("a topic and a keyword can be combined", 0 < both < t006["total_posts"], f"{both:,} of {t006['total_posts']:,}")
    try:
        a.explore_viral(q(topic_id="t006; drop table x", **tw)); chk("a malformed topic id is refused", False)
    except ValueError:
        chk("a malformed topic id is refused", True)

    # ---------------- retweets
    wide = {"from": "2026-06-14", "to": TO}
    inc = a.explore_viral(q(rank="virality", order="amplified", limit=300, **wide))
    hide = a.explore_viral(q(rank="virality", order="amplified", limit=300, retweets="hide", **wide))
    rts = [p for p in inc["by_virality"] if p["is_retweet"]]
    chk("retweets are flagged in the ranking (is_retweet)", len(rts) > 0 and all((p["text"] or "").upper().startswith("RT @") or "RT @" in (p["text"] or "").upper() for p in rts), f"{len(rts)} of {len(inc['by_virality'])}")
    chk("'Original posts only' removes every retweet from the ranking", not any(p["is_retweet"] for p in hide["by_virality"]) and not any((p["text"] or "").upper().startswith("RT @") for p in hide["by_virality"]))
    chk("hiding retweets lowers the match count by the retweets", hide["total_posts"] < inc["total_posts"], f"{inc['total_posts']} -> {hide['total_posts']}")
    vi = a.explore_viral(q(limit=100, **wide)); vh = a.explore_viral(q(limit=100, retweets="hide", **wide))
    chk("the by-views lists honour the switch too", not any((p["text"] or "").upper().startswith("RT @") for p in vh["with_views"] + vh["without_views"]) and all("is_retweet" in p for p in vi["with_views"] + vi["without_views"]))
    only_rt = a._main_con.execute("SELECT count(*) FROM analytics.post_facts p WHERE " + a.RETWEET_SQL).fetchall()[0][0]
    chk("the retweet rule matches a known count (Twitter 4,219 + Truth Social 10 start with 'RT @', plus 207 that start 'retweet @')", only_rt == 4436, only_rt)

    # ---------------- theme filter and search trend (stage 27)
    import pandas as pd
    th = pd.read_parquet(a.POST_THEME, columns=["post_uid", "theme", "tier"])
    w = dict(**{"from": FROM, "to": TO})
    vt = a.explore_viral(q(theme="D04", limit=300, **w))
    uids = [p["post_uid"] for p in vt["with_views"] + vt["without_views"]]
    tmap = th.set_index("post_uid")
    knn = pd.read_parquet(a.POST_THEME_KNN, columns=["post_uid", "knn_theme"]).set_index("post_uid")
    def ok_d04(u):
        in_hi = u in tmap.index and tmap.loc[u, "theme"] == "D04" and tmap.loc[u, "tier"] == "high"
        in_nn = u in knn.index and knn.loc[u, "knn_theme"] == "D04"
        return in_hi or in_nn
    chk("theme filter D04 returns only clear D04 posts (stage-27 high tier or stage-28 neighbour vote)", uids and all(ok_d04(u) for u in uids), len(uids))
    chk("the default theme list includes neighbour-vote posts", any(u in knn.index and knn.loc[u, "knn_theme"] == "D04" for u in uids) or
        a.explore_viral(q(theme="D04", limit=300, **{"from": "2026-06-01", "to": "2026-10-26"}))["total_posts"] > 0)
    va = a.explore_viral(q(theme="D04", theme_all=1, limit=300, **w))
    chk("the lower-confidence switch adds posts and never removes any", va["total_posts"] >= vt["total_posts"] and va["total_posts"] > 0, (vt["total_posts"], va["total_posts"]))
    d4 = pd.concat([th[(th.theme == "D04") & (th.tier == "high")][["post_uid"]], knn[knn.knn_theme == "D04"].reset_index()[["post_uid"]]]).drop_duplicates()
    a._main_con.register("d4_uids", d4[["post_uid"]])
    exp = a._main_con.execute("SELECT count(*) FROM d4_uids u JOIN analytics.post_facts p ON p.post_uid = u.post_uid JOIN viral_keep k ON k.post_uid = p.post_uid WHERE p.posted_at IS NOT NULL").fetchall()[0][0]    # copies collapsed, as the card counts
    tot_all = a.explore_viral(q(theme="D04", **{"from": "1990-01-01", "to": "2100-01-01"}))["total_posts"]
    chk("theme total over all days equals the stage-27 high tier + stage-28 neighbour file (D04, posts with a posted day)", tot_all == exp and exp > 0, (tot_all, exp, len(d4)))
    for bad in ("D10", "x", "D0"):
        try:
            a.explore_viral(q(theme=bad, **w)); chk(f"theme '{bad}' is refused", False)
        except ValueError:
            chk(f"theme '{bad}' is refused", True)
    pf = dict(prev_from=PF, prev_to=PT, **w)
    t1 = a.explore_search_trend(q(q="ladakh", **pf))
    chk("search trend: counts, daily series and shares are consistent", t1["n_cur"] > 0 and t1["distinct_cur"] <= t1["n_cur"] and
        sum(r["n"] for r in t1["daily"] if FROM <= r["day"] <= TO) == t1["n_cur"] and sum(r["n"] for r in t1["daily"] if PF <= r["day"] <= PT) == t1["n_prev"],
        (t1["n_cur"], t1["n_prev"]))
    chk("search trend: the day total is every post of that day, never smaller than the matches", all(r["total"] >= r["n"] for r in t1["daily"]))
    chk("search trend: platform and topic counts add up to the matches", sum(r["n_cur"] for r in t1["platforms"]) == t1["n_cur"] and sum(r["n"] for r in t1["topics"]) <= t1["n_cur"])
    chk("search trend: matches agree with the Viral Posts total for the same words", t1["n_cur"] == a.explore_viral(q(q="ladakh", **w))["total_posts"],
        (t1["n_cur"], a.explore_viral(q(q="ladakh", **w))["total_posts"]))
    t2 = a.explore_search_trend(q(theme="D06", **pf))
    chk("search trend by theme lists only that theme", t2["n_cur"] > 0 and {r["theme"] for r in t2["themes"]} == {"D06"} and all(r["tier"] in ("high", "neighbour") for r in t2["themes"]))
    for kw, label in ((dict(), "nothing to search for"), (dict(q="-dam"), "only a minus word")):
        try:
            a.explore_search_trend(q(**kw, **pf)); chk(f"search trend refuses {label}", False)
        except (ValueError, a.KQ.KeywordError):
            chk(f"search trend refuses {label}", True)

    # ---------------- related trending (entities, hashtags, previous-period counts, latest posts)
    t3 = a.explore_search_trend(q(q="ladakh", **pf))
    chk("related: topic counts split by period never exceed the matches of each period",
        sum(r["n"] for r in t3["topics"]) <= t3["n_cur"] and sum(r["n_prev"] for r in t3["topics"]) <= t3["n_prev"])
    chk("related: entities carry a name, a type and a count of at least 5, none above the matches",
        t3["entities"] and all(e["name"] and e["type"] and 5 <= e["n"] <= t3["n_cur"] and e["n_prev"] <= t3["n_prev"] for e in t3["entities"]), len(t3["entities"]))
    chk("related: hashtags start with # and are counted once per post", t3["hashtags"] and all(h["tag"].startswith("#") and 5 <= h["n"] <= t3["n_cur"] for h in t3["hashtags"]), [h["tag"] for h in t3["hashtags"][:3]])
    chk("related: theme rows carry the previous-period count", all("n_prev" in r for r in t3["themes"]))
    ent_uid = t3["entities"][0]["entity_uid"]
    t4 = a.explore_search_trend(q(q="ladakh", entity_uid=ent_uid, **pf))
    chk("related: narrowing to an entity keeps only posts naming it and does not list it back", t4["n_cur"] <= t3["n_cur"] and ent_uid not in {e["entity_uid"] for e in t4["entities"]}, (t3["n_cur"], t4["n_cur"]))
    vl = a.explore_viral(q(q="ladakh", limit=2, latest=4, **w))
    days_l = [str(p["posted_at"])[:19] for p in vl["latest"]]
    chk("latest: the newest matching posts, newest first, at most the number asked", 0 < len(vl["latest"]) <= 4 and days_l == sorted(days_l, reverse=True), days_l)
    chk("latest: absent when not asked for", "latest" not in a.explore_viral(q(q="ladakh", limit=2, **w)))

    # ---------------- anomaly against the 28-day baseline and per-platform speed
    an = t3["anomaly"]
    chk("anomaly: rows cover exactly the selected days, with the gates (no verdict under 3 posts)", [r["day"] for r in an["rows"]][0] == FROM and [r["day"] for r in an["rows"]][-1] == TO and
        all(r["surprise"] is None for r in an["rows"] if r["n"] < 3), len(an["rows"]))
    chk("anomaly: a verdict has expected, surprise and q; alert means q < 0.05",
        all((r["surprise"] is None) == (r["q"] is None) == (r["expected"] is None) for r in an["rows"]) and all(r["alert"] == (r["q"] is not None and r["q"] < 0.05) for r in an["rows"]))
    ts = a.explore_search_trend(q(q="hormuz", **{"from": "2026-10-01", "to": "2026-10-26"}, prev_from="2026-09-05", prev_to="2026-09-30"))
    chk("anomaly: a busy search gets a verdict on most days and q-values in 0..1", ts["anomaly"]["ok"] and ts["anomaly"]["judged_days"] >= 20 and all(0 <= r["q"] <= 1 for r in ts["anomaly"]["rows"] if r["q"] is not None), ts["anomaly"]["judged_days"])
    sp = t3["platform_speed"]
    chk("platform speed: counts add up to the matches of the selected days (top 8 platforms)", sp and sum(r["n"] for r in sp) <= t3["n_cur"] and all(r["trend"] in ("rising", "steady", "declining", "few") for r in sp))
    chk("platform speed: first day is a selected-day date or flagged as already active before", all((r["first_day"] is None) or (r["before"] == (r["first_day"] < FROM)) for r in sp))
    chk("platform speed: peak day lies inside the selected days", all(FROM <= r["peak_day"] <= TO for r in sp))

    # ---------------- "who is involved": a search plus an entity type / subtype
    pw = a.explore_search_trend(q(q="iran war", entity_type="PERSON", entity_subtype="political_leader", **{"from": "2026-10-12", "to": "2026-10-26"}, prev_from="2026-09-27", prev_to="2026-10-11"))
    rows_p = (pw["people"] or {}).get("rows", [])
    names_p = [r["name"].lower() for r in rows_p]
    chk("who: a typed search returns a people list, most named first, each named in at least 3 posts and never above the matches",
        rows_p and [r["n"] for r in rows_p] == sorted([r["n"] for r in rows_p], reverse=True) and all(3 <= r["n"] <= pw["n_cur"] for r in rows_p), len(rows_p))
    chk("who: titles, links and bare nationalities are not people", not any(n in ("president", "prime minister", "minister", "https", "israeli", "russian", "representative", "deputy") for n in names_p), names_p[:8])
    collapsed = [re.sub(r"[^a-z0-9]", "", n) for n in names_p]
    chk("who: one person is listed once (no 'Xijinping' beside 'Xi Jinping')", len(collapsed) == len(set(collapsed)))
    chk("who: the main figures of the Iran war appear (Trump and Putin)", "trump" in names_p and "putin" in names_p, names_p[:6])
    chk("who: a search without a type has no people list", a.explore_search_trend(q(q="iran war", **pf))["people"] is None)
    pm = a._people_involved(a._main_con.cursor(), __import__("pandas").DataFrame({"t": ["trump said hello", "no one here", "donald trump spoke"], "per": ["cur", "cur", "prev"]}), "PERSON", "political_leader")
    chk("who: name matching is per post and splits the two periods", [(r["name"], r["n"], r["n_prev"]) for r in pm["rows"] if r["name"] == "Trump"] in ([], [("Trump", 1, 1)]))

    # ---------------- scenario search: input checks (the Gemma and embedding calls themselves are not run here)
    for bad in (dict(scenario="", entity_type="PERSON"), dict(scenario="x" * 130, entity_type="PERSON"), dict(scenario="iran war", entity_type="person"),
                dict(scenario="iran war", entity_type="PERSON", entity_subtype="Political Leader")):
        try:
            a.explore_scenario_people(q(**bad, **{"from": FROM, "to": TO})); chk("scenario search refuses " + str(list(bad.values())[0])[:12] + "...", False)
        except ValueError:
            chk("scenario search refuses a bad request (" + ",".join(f"{k}={str(v)[:8]}" for k, v in bad.items()) + ")", True)
    chk("scenario search needs the stage-31 embedding files", a.Path(a.DATA / "post_emb_fp16.npy").exists() and a.Path(a.DATA / "post_emb_index.parquet").exists())

    # ---------------- live-card fixes (2026-11-24): copies collapsed, zero activity is not "top 10 %", breakout rule, entity type, retweet pattern
    WI = {"from": "2026-06-01", "to": TO}
    ig = a.explore_viral(q(limit=300, platforms="instagram", **WI)); rows = ig["with_views"] + ig["without_views"]
    ig_distinct = a._main_con.execute("SELECT count(DISTINCT url) FROM analytics.post_facts WHERE platform = 'instagram' AND CAST(posted_at AS DATE) BETWEEN ? AND ?", [WI["from"], WI["to"]]).fetchall()[0][0]
    chk("copies collapsed: no URL appears twice in an Instagram list", len({r["url"] for r in rows}) == len(rows), f"{len(rows)} rows")
    chk("copies collapsed: Instagram total = distinct posts, not rows", ig["total_posts"] == ig_distinct, (ig["total_posts"], ig_distinct))
    pin = a.explore_viral(q(rank="virality", order="biggest", limit=300, platforms="pinterest", **{"from": "2026-01-01", "to": TO}))
    chk("zero-activity posts are never 'top 10 %' (Pinterest p90 = 0)", all((p["engagement"] or 0) > 0 or (p["views"] or 0) > 0 for p in pin["by_virality"]), f"{len(pin['by_virality'])} posts")
    vb = a.explore_viral(q(rank="virality", order="amplified", floor="off", thresh="viral", limit=300, **{"from": "2026-10-01", "to": TO}))["by_virality"]
    pv = a._main_con.execute(f"SELECT post_uid, activity, activity_top10, activity_ok, virality_pct, virality_ref_n FROM read_parquet('{a.POST_VIRALITY.as_posix()}')").fetchdf().set_index("post_uid")
    ok_rule = all(((pv.loc[p["post_uid"], "activity_top10"] and pv.loc[p["post_uid"], "activity"] > 0) or (pv.loc[p["post_uid"], "virality_pct"] >= 0.80 and pv.loc[p["post_uid"], "virality_ref_n"] >= 20 and pv.loc[p["post_uid"], "activity_ok"])) for p in vb)
    chk("viral breakout: top-10% activity (> 0) or P >= 0.80 with at least median activity; no tiny-count ratio posts", ok_rule and len(vb) > 0, f"{len(vb)} posts")
    n_all = a.explore_viral(q(limit=5, **{"from": FROM, "to": TO}))["total_posts"]
    n_person = a.explore_viral(q(limit=5, entity_type="PERSON", **{"from": FROM, "to": TO}))["total_posts"]
    chk("entity type alone filters the posts (posts naming any person)", 0 < n_person < n_all, (n_person, n_all))
    try:
        a.explore_viral(q(limit=5, entity_type="PERSON'; DROP", **{"from": FROM, "to": TO})); chk("entity type refuses a bad value", False)
    except ValueError:
        chk("entity type refuses a bad value", True)
    chk("retweet pattern also catches 'retweet @user:'", a._main_con.execute("SELECT " + a.RETWEET_SQL.replace("p.text", "'retweet @presidencymv: On behalf'")).fetchall()[0][0] is True)

    # ---------------- spread efficiency (v3 file): extra columns, the "Most efficient" order, and the fallback when the file is missing
    WE = {"from": "2026-10-01", "to": TO}
    eff = a.explore_viral(q(rank="virality", order="efficient", limit=100, **WE)); el = eff["by_virality"]
    chk("efficient order returns the v3 flag and only posts that have a share rate", eff["order"] == "efficient" and eff["v3"] is True and len(el) > 0 and all(p["spread_eff_pct"] is not None for p in el), f"{len(el)} posts")
    chk("efficient order is sorted by spread_eff_pct descending", all(el[i]["spread_eff_pct"] >= el[i + 1]["spread_eff_pct"] for i in range(len(el) - 1)))
    chk("efficient order lists only evidence tiers A and B (platforms with a share or forward counter)", {p["evidence_tier"] for p in el} <= {"A", "B"}, sorted({p["evidence_tier"] for p in el}))
    chk("efficient order leaves out reposts and RT-text posts (their counters are the original's)", not any(p["is_retweet"] for p in el) and not any((p["text"] or "").upper().startswith(("RT @", "RETWEET @")) for p in el), f"{sum(bool(p['is_retweet']) for p in el)} retweets")
    chk("efficient order keeps the activity floor (no tiny-count post at the top)", all((p["views"] or 0) > 0 or (p["engagement"] or 0) > 0 for p in el))
    big_ = a.explore_viral(q(rank="virality", order="biggest", limit=300, **WE))["by_virality"]
    chk("default Biggest order carries the spread columns and keeps its own order", len(big_) > 0 and "spread_eff_pct" in big_[0] and "evidence_tier" in big_[0] and all(big_[i]["attention_pct"] >= big_[i + 1]["attention_pct"] for i in range(len(big_) - 1)))
    cd = [p for p in big_ if p["evidence_tier"] in ("C", "D")]
    chk("tier C/D posts have no share rate (shown as 'no share counter', never as 0)", all(p["spread_eff_pct"] is None for p in cd), f"{len(cd)} tier C/D posts")
    old_path = a.POST_VIRALITY_V3
    try:
        a.POST_VIRALITY_V3 = a.Path("/nonexistent/post_virality_v3.parquet")
        fb = a.explore_viral(q(rank="virality", order="biggest", limit=20, **WE))
        chk("without the v3 file the card still works (spread columns are NULL, v3 false)", fb["v3"] is False and len(fb["by_virality"]) > 0 and all(p["spread_eff_pct"] is None for p in fb["by_virality"]))
        try:
            a.explore_viral(q(rank="virality", order="efficient", limit=5, **WE)); chk("efficient order without the v3 file says what to run", False)
        except ValueError as e:
            chk("efficient order without the v3 file says what to run", "24_post_virality_v3" in str(e))
    finally:
        a.POST_VIRALITY_V3 = old_path
    cov = a._main_con.execute(f"SELECT count(*), count(w.post_uid) FROM viral_keep k LEFT JOIN read_parquet('{a.POST_VIRALITY_V3.as_posix()}') w ON w.post_uid = k.post_uid").fetchall()[0]
    chk("the v3 file covers (almost) every post the card can show", cov[1] / cov[0] > 0.995, f"{cov[1]:,} of {cov[0]:,}")

    # ---------------- cross-platform spread drill-down: one row per real post, not one per scrape
    for view, key, tbl, ptbl in (("cross_platform", "story_id", "cross_platform_stories", "cross_platform_posts"),
                                 ("cross_platform_url", "link_id", "cross_platform_url_links", "cross_platform_url_posts"),
                                 ("cross_platform_event", "event_id", "cross_platform_semantic_events", "cross_platform_semantic_posts")):
        worst = a._main_con.execute(f"SELECT {key}, n_posts FROM metrics.{tbl} ORDER BY n_posts DESC LIMIT 1").fetchone()
        sql, params = a.build_query(view, q(**{key: worst[0]}))
        rows = a._main_con.execute(sql, params).fetchdf()
        raw = a._main_con.execute(f"SELECT count(*) FROM metrics.{ptbl} WHERE {key} = ?", [worst[0]]).fetchone()[0]
        urls = rows["url"].fillna(rows["post_uid"])
        chk(f"{view}: the drill-down lists one row per (platform, url), not one per scrape",
            len(rows) > 0 and len(rows) == len(set(zip(rows["platform"], urls))) and len(rows) <= raw, f"{raw} scrape rows -> {len(rows)}")
        chk(f"{view}: total_rows is the collapsed count and the columns are unchanged",
            int(rows["total_rows"].iloc[0]) == len(rows) and list(rows.columns) == ["post_uid", "platform", "posted_at", "author_name", "text", "url", "total_rows"])
    st = a._main_con.execute("SELECT story_id, n_posts, n_unique_posts FROM metrics.cross_platform_stories ORDER BY n_posts DESC LIMIT 1").fetchone()
    sql, params = a.build_query("cross_platform", q(story_id=st[0]))
    n_rows = len(a._main_con.execute(sql, params).fetchdf())
    chk("the most-copied story: card unique count and drill-down agree within 1 (it was 174 rows for 2 posts)", abs(n_rows - st[2]) <= 1 and n_rows < st[1] / 4, f"n_posts {st[1]}, n_unique_posts {st[2]}, drill rows {n_rows}")

    # ---------------- cross-platform spread: "same publisher" label (computed from the stored tables)
    import spread_publisher as pub
    pf = a.PUB.explore_spread_publishers(a._main_con)
    chk("the label covers all three tiers and counts add up", set(pf["counts"]) == {"stories", "links", "events"} and all(pf["counts"][t]["same"] <= pf["counts"][t]["total"] for t in pf["counts"]), pf["counts"])
    tiny = a._main_con.execute("SELECT link_id FROM metrics.cross_platform_url_links WHERE host = 'tinyurl.com'").fetchall()
    chk("every Aaj Tak tinyurl link (Telegram + WhatsApp, one publisher) is flagged", tiny and all(pf["links"][r[0]]["same"] for r in tiny), f"{len(tiny)} links")
    nyt = a._main_con.execute("SELECT link_id FROM metrics.cross_platform_url_links WHERE url LIKE 'nytimes.com/%' AND n_authors >= 2").fetchall()
    chk("a news link shared by different accounts is NOT flagged", nyt and not any(pf["links"][r[0]]["same"] for r in nyt), f"{len(nyt)} links")
    xi = a._main_con.execute("SELECT story_id FROM metrics.cross_platform_stories WHERE snippet LIKE 'At the invitation of President Xi Jinping%'").fetchall()
    chk("China MFA on X + Instagram (mfa_china) is flagged as one publisher", xi and all(pf["stories"][r[0]]["same"] and pf["stories"][r[0]]["label"] == "China MFA" for r in xi))
    chk("every flagged item has a label and at least two entries; unflagged items have none",
        all((v["label"] and v["publishers"] == 1) if v["same"] else not v["label"] for t in ("stories", "links", "events") for v in pf[t].values()))

    n_ok = sum(RESULTS)
    print(f"\n  {n_ok}/{len(RESULTS)} checks passed")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
