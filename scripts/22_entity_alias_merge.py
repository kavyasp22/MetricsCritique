#!/usr/bin/env python3
"""
Stage 22 -- merge spelling variants of the same entity in the production-entity table.

Reads  data/prod_entities.duckdb (stage 21): entity_dim_prod, entity_mentions_prod.
Writes (same file, new tables; the stage-21 tables are not changed):
    entity_alias            one row per merged alias: alias_uid -> canonical_uid, rule, support
    entity_dim_prod_merged  one row per canonical entity, distinct posts counted ONCE per post
    entity_mentions_prod_canon  mentions with canonical_uid / canonical_name added

Merges only happen inside the same parent type (PERSON / ORG / LOCATION / EVENT). Rules,
strongest first; every merge records which rule fired so it can be reviewed and undone:

  R1 normalise   same name after case, accents, punctuation, a leading "the", leading
                 titles (president, general, dr ...) and a trailing 's are removed.
  R2 gazetteer   a surface form listed in data/gazetteer.json joins that gazetteer entity's
                 group (data/gazetteer.json is curated; 86 entries).
  R3 surname     a one-word PERSON ("Putin") joins the full-name PERSON that ends with it
                 ("Vladimir Putin") only if that full name holds >= 70% of the posts of all
                 full names ending with the same word and has >= 20 posts itself.
  R0 manual      pairs listed in data/entity_aliases_manual.json (reviewed by hand).
  R4 acronym     a 2-6 letter ORG ("PLA") joins the single multi-word ORG whose word
                 initials spell it ("People's Liberation Army"), only if exactly one such ORG
                 has >= 20 posts. Words like of / the / and are ignored.

Not merged on purpose: the same name with a different type (person:putin vs location:putin
stays separate; listed in the report), and anything ambiguous.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "prod_entities.duckdb"
GAZ = ROOT / "data" / "gazetteer.json"
MANUAL = ROOT / "data" / "entity_aliases_manual.json"   # [[alias_uid, canonical_uid], ...] reviewed by hand
WORDS = Path("/usr/share/dict/words")


def common_words() -> set[str]:
    """Lower-case English words; a one-word PERSON that is a common word ("attorney",
    "korean") is not merged into a full name by the surname rule."""
    if not WORDS.exists():
        return set()
    return {w.strip() for w in WORDS.read_text(encoding="utf-8", errors="ignore").splitlines()
            if w.strip().isalpha() and w.strip().islower()}

TITLES = {"president", "prime", "minister", "pm", "general", "gen", "dr", "mr", "mrs", "ms",
          "sir", "chairman", "premier", "senator", "king", "prince", "sheikh", "lord"}
STOP = {"of", "the", "and", "for", "in", "on"}
# a full name starting with one of these is a descriptive phrase, not a person ("south korean")
GENERIC_FIRST = {"south", "north", "east", "west", "central", "defense", "defence", "chief",
                 "former", "senior", "new", "local", "foreign", "chinese", "russian", "american",
                 "indian", "israeli", "ukrainian", "iranian", "pakistani", "british", "french"}
MIN_FULL_POSTS = 20
DOMINANCE = 0.70


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[’']s\b", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    toks = s.split()
    while toks and toks[0] == "the":
        toks = toks[1:]
    # strip a leading title only if a real name follows ("prime minister" and
    # "minister of" stay as they are, "president trump" -> "trump")
    while (len(toks) > 1 and toks[0] in TITLES and toks[1] not in STOP
           and (toks[1] not in TITLES or len(toks) > 2)):
        toks = toks[1:]
    return " ".join(toks)


def main() -> int:
    con = duckdb.connect(str(DB), read_only=False)
    dim = con.execute("SELECT entity_uid, canonical_name, entity_type, distinct_posts "
                      "FROM entity_dim_prod").fetchdf()
    dim["key"] = dim["canonical_name"].map(norm)
    dim = dim[dim["key"] != ""].copy()
    parent = {u: u for u in dim["entity_uid"]}      # union-find
    rule = {}                                       # alias_uid -> rule that merged it

    def find(u):
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    posts = dict(zip(dim["entity_uid"], dim["distinct_posts"]))
    etype = dict(zip(dim["entity_uid"], dim["entity_type"]))

    def union(a, b, why):
        ra, rb = find(a), find(b)
        if ra == rb or etype[a] != etype[b]:
            return
        keep, drop = (ra, rb) if posts[ra] >= posts[rb] else (rb, ra)
        parent[drop] = keep
        rule[drop] = why

    # R1 normalised name
    for (t, k), g in dim.groupby(["entity_type", "key"]):
        ids = list(g["entity_uid"])
        for other in ids[1:]:
            union(ids[0], other, "R1_normalise")

    # R2 gazetteer surface forms
    by_key = {(r.entity_type, r.key): r.entity_uid for r in dim.itertuples()}
    for ent in json.loads(GAZ.read_text(encoding="utf-8")):
        t = ent["entity_type"]
        members = [by_key[(t, norm(sf))] for sf in ent["surface_forms"] if (t, norm(sf)) in by_key]
        for other in members[1:]:
            union(members[0], other, "R2_gazetteer")

    # R3 surname -> full name (PERSON)
    persons = dim[dim["entity_type"] == "PERSON"]
    full_by_last = defaultdict(list)
    singles = []
    for r in persons.itertuples():
        toks = r.key.split()
        if len(toks) >= 2:
            if toks[0] not in GENERIC_FIRST:
                full_by_last[toks[-1]].append(r.entity_uid)
        elif len(toks) == 1:
            singles.append((r.entity_uid, toks[0]))
    common = common_words()
    blocked = []
    for uid, last in singles:
        cands = full_by_last.get(last, [])
        if not cands:
            continue
        if last in common:
            blocked.append((posts[uid], uid))
            continue
        tot = sum(posts[c] for c in cands)
        best = max(cands, key=lambda c: posts[c])
        if posts[best] >= MIN_FULL_POSTS and tot > 0 and posts[best] / tot >= DOMINANCE:
            union(best, uid, "R3_surname")

    # R0 manual pairs
    if MANUAL.exists():
        for a, b in json.loads(MANUAL.read_text(encoding="utf-8")):
            if a in parent and b in parent:
                union(b, a, "R0_manual")

    # R4 acronym -> multi-word ORG
    orgs = dim[dim["entity_type"] == "ORG"]
    initials = defaultdict(list)
    for r in orgs.itertuples():
        toks = [t for t in r.key.split() if t not in STOP]
        if len(toks) >= 2:
            initials["".join(t[0] for t in toks)].append(r.entity_uid)
    for r in orgs.itertuples():
        if " " in r.key or not r.key.isalpha() or not (2 <= len(r.key) <= 6):
            continue
        cands = [c for c in initials.get(r.key, []) if posts[c] >= MIN_FULL_POSTS]
        if len(cands) == 1:
            union(cands[0], r.entity_uid, "R4_acronym")

    # canonical id + alias table
    dim["canonical_uid"] = dim["entity_uid"].map(find)
    name_of = dict(zip(dim["entity_uid"], dim["canonical_name"]))
    alias = dim[dim["canonical_uid"] != dim["entity_uid"]].copy()
    alias["rule"] = alias["entity_uid"].map(rule).fillna("R1_normalise")
    alias_tbl = pd.DataFrame({
        "alias_uid": alias["entity_uid"], "alias_name": alias["canonical_name"],
        "canonical_uid": alias["canonical_uid"],
        "canonical_name": alias["canonical_uid"].map(name_of),
        "entity_type": alias["entity_type"], "rule": alias["rule"],
        "alias_posts": alias["distinct_posts"],
        "canonical_posts": alias["canonical_uid"].map(posts)})
    con.register("_a", alias_tbl)
    con.execute("CREATE OR REPLACE TABLE entity_alias AS SELECT * FROM _a")

    cmap = dim[["entity_uid", "canonical_uid"]].copy()
    cmap["canonical_name"] = cmap["canonical_uid"].map(name_of)
    con.register("_cm", cmap)
    con.execute("""
        CREATE OR REPLACE TABLE entity_mentions_prod_canon AS
        SELECT m.*, coalesce(c.canonical_uid, m.entity_uid) AS canonical_uid,
               coalesce(c.canonical_name, m.surface_form) AS canonical_name
        FROM entity_mentions_prod m LEFT JOIN _cm c USING (entity_uid)""")
    con.execute("""
        CREATE OR REPLACE TABLE entity_dim_prod_merged AS
        SELECT canonical_uid AS entity_uid, any_value(canonical_name) AS canonical_name,
               any_value(entity_type) AS entity_type,
               mode(entity_subtype) AS entity_subtype,
               count(DISTINCT entity_uid) AS n_variants, count(*) AS mention_rows,
               count(DISTINCT post_uid) AS distinct_posts,
               min(posted_at) AS first_seen, max(posted_at) AS last_seen,
               avg(ner_score) AS avg_ner_score
        FROM entity_mentions_prod_canon GROUP BY canonical_uid""")

    # readable display name: the fullest variant that holds >= 25% of the group's posts
    # (production stores names lower-case), title-cased, short acronyms upper-cased
    var = dim.assign(c=dim["canonical_uid"])
    disp = {}
    for cu, g in var.groupby("c"):
        tot = g["distinct_posts"].sum()
        full = g[(g["canonical_name"].str.split().str.len() > 1) & (g["distinct_posts"] >= 0.25 * tot)]
        pick = (full if len(full) else g).sort_values("distinct_posts", ascending=False).iloc[0]["canonical_name"]
        disp[cu] = " ".join(w.upper() if (w.isalpha() and len(w) <= 4 and len(pick.split()) == 1
                                          and pick.isalpha() and cu.startswith("org:"))
                            else w.capitalize() for w in pick.split())
    dd = pd.DataFrame({"entity_uid": list(disp), "display_name": list(disp.values())})
    con.register("_dd", dd)
    con.execute("""CREATE OR REPLACE TABLE entity_dim_prod_merged AS
                   SELECT m.*, d.display_name FROM entity_dim_prod_merged m
                   JOIN _dd d USING (entity_uid)""")

    n_before = len(dim)
    n_after = con.execute("SELECT count(*) FROM entity_dim_prod_merged").fetchall()[0][0]
    print(f"entities {n_before:,} -> {n_after:,} ({n_before - n_after:,} aliases merged)")
    print(alias_tbl.groupby("rule").size().to_string())
    print("one-word persons NOT merged by R3 because they are common English words (top 15):",
          [u for _, u in sorted(blocked, reverse=True)[:15]])
    for name in ["Vladimir Putin", "NATO", "People's Liberation Army", "Ladakh", "Donald Trump"]:
        r = con.execute("SELECT entity_uid, n_variants, distinct_posts FROM entity_dim_prod_merged "
                        "WHERE lower(canonical_name)=lower(?) ORDER BY distinct_posts DESC LIMIT 1",
                        [name]).fetchall()
        print(f"  {name:<28} {r}")
    # same name, different type: reported, not merged
    cross = con.execute("""
        SELECT count(*) FROM (SELECT split_part(entity_uid, ':', 2) k FROM entity_dim_prod_merged
                              GROUP BY 1 HAVING count(DISTINCT entity_type) > 1)""").fetchall()[0][0]
    print(f"names present under more than one type (not merged): {cross:,}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
