"""Explore API: entity-in-topic counts, per-post virality ranking, and the stage-24 file.

    .venv/bin/python UI/tests/check_explore_api.py

Read-only. Calls the server functions directly (no HTTP), against the real data files.
"""
from __future__ import annotations

import json
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
    bad = {k: (int(mine.get(k, 0)), int(v or 0)) for k, v in ref.items() if int(mine.get(k, 0)) != int(v or 0)}
    chk("rated posts per platform equal virality_n summed in metrics_daily (same numbers as the group metrics)", not bad, bad)
    chk("platforms with no counters carry no percentile (reddit)", int(mine.get("reddit", 0)) == 0)

    # ---------------- ranking API
    r = a.explore_viral(q(rank="virality", limit=30, **{"from": FROM, "to": TO}))
    L = r["by_virality"]
    pcts = [p["virality_pct"] for p in L]
    chk("virality ranking is sorted high to low", len(L) == 30 and pcts == sorted(pcts, reverse=True), f"{pcts[0]:.3f}..{pcts[-1]:.3f}")
    chk("every ranked post has at least the minimum reference set", all(p["virality_ref_n"] >= a.VIRALITY_MIN_REF for p in L))
    chk("rated posts <= matching posts", r["rated_posts"] <= r["total_posts"], f"{r['rated_posts']} / {r['total_posts']}")
    p3 = a.explore_viral(q(rank="virality", per_platform=3, **{"from": FROM, "to": TO}))["by_virality"]
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
    kw = a.explore_viral(q(q="flood", rank="virality", limit=10, **{"from": "2026-06-14", "to": TO}))
    chk("keyword search works together with the percentile ranking",
        all("flood" in (p["text"] or "").lower() or "flooding" in (p["text"] or "").lower() or "floods" in (p["text"] or "").lower() for p in kw["by_virality"]) and kw["terms"],
        f"{len(kw['by_virality'])} posts")

    n_ok = sum(RESULTS)
    print(f"\n  {n_ok}/{len(RESULTS)} checks passed")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
