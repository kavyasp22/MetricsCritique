#!/usr/bin/env python3
"""
Stage 21 -- thin table of the entities already extracted in production.

Production tables carry a jsonb `entities` column written by the trained NER model:
    {"entities": [{"entity": "...", "entity_type": "<domain subtype>", "score": 0.97, "count": 4}]}
This stage only READS it (lib.pg.read_only_snapshot: SELECT-only, always ROLLBACK) and
writes a flat post-to-entity table. No post text is stored.

Output (new file, so no writer conflict with geo_ontology.duckdb / the dashboard):
    data/prod_entities.duckdb
        entity_mentions_prod  one row per (post, entity, label) from production
        entity_dim_prod       one row per entity
        entity_prod_coverage  per platform: prod rows with entities, matched to our
                              post_facts, unmatched, our posts, share covered
The Babelscape tables (entity_dim / entity_mentions) are NOT touched; the dashboard keeps
using them until the new table is verified.

post_uid is built exactly as scripts/01_extract_base.py does it, sha1(platform|native_id),
with the native_id expression taken from that script's PLATFORM_SQL, so the join to
analytics.post_facts is the same one the rest of the pipeline uses.

Labels that are not in the 25-subtype taxonomy are KEPT with entity_type 'UNMAPPED' and
listed in the report (never silently dropped).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.pg import read_only_snapshot  # noqa: E402

SRC_DB = ROOT / "data" / "analytics.duckdb"
OUT_DB = ROOT / "data" / "prod_entities.duckdb"
ITERSIZE = 20000


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def source_specs(extract) -> dict[str, tuple[str, str]]:
    """platform -> (table, native_id SQL expression), parsed from script 01's PLATFORM_SQL."""
    out = {}
    for platform, sql in extract.PLATFORM_SQL.items():
        m_nid = re.search(r"^\s*(.+?)\s+AS native_id", sql, re.M)
        m_tbl = re.search(r"\bFROM\s+(\w+)\s*$", sql.strip(), re.M)
        if not (m_nid and m_tbl):
            continue
        plat_lit = re.search(r"'([a-z_]+)'::text AS platform", sql)
        out[platform] = (m_tbl.group(1), m_nid.group(1).strip(),
                         plat_lit.group(1) if plat_lit else platform)
    return out


def parent_type_map(ent10) -> dict[str, str]:
    """subtype label -> PERSON/LOCATION/ORG/EVENT, from the taxonomy in script 10."""
    m = {}
    for parent, subs in ent10.PROD_ENTITY_TAXONOMY.items():
        for sub in subs:
            m[sub] = parent
    return m


def slug(s: str) -> str:
    return re.sub(r"[^\w]+", "_", s.lower().strip()).strip("_")


def main() -> int:
    extract = _load(ROOT / "scripts" / "01_extract_base.py", "extract01")
    ent10 = _load(ROOT / "scripts" / "10_entity_extraction.py", "entity10")
    parent = parent_type_map(ent10)
    specs = source_specs(extract)

    src = duckdb.connect(str(SRC_DB), read_only=True)
    ours = src.execute("SELECT post_uid, platform, posted_at FROM post_facts").fetchdf()
    src.close()
    our_uid = dict(zip(ours["post_uid"], ours["platform"]))
    our_posts_by_plat = Counter(ours["platform"])
    print(f"our posts: {len(ours):,}")

    rows = []                       # one dict per mention
    cov = []                        # per-platform coverage
    best_len = {}                   # post_uid -> mention count already kept (dedupe twitter/x)
    label_counts = Counter()
    n_blank_native = Counter()
    snapshot_ts = None

    with read_only_snapshot() as (conn, cur, ts):
        snapshot_ts = ts
        for key, (table, nid_expr, plat) in specs.items():
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name=%s AND column_name='entities'", (table,))
            if cur.fetchone() is None:
                print(f"  skip {key}: {table} has no entities column")
                continue
            sql = (f"SELECT {nid_expr} AS native_id, entities->'entities' AS ents "
                   f"FROM {table} "
                   f"WHERE CASE WHEN jsonb_typeof(entities->'entities')='array' "
                   f"THEN jsonb_array_length(entities->'entities') ELSE 0 END > 0")
            n_rows = matched = unmatched = 0
            sc = conn.cursor(name=f"ent_{key}")
            sc.itersize = ITERSIZE
            sc.execute(sql)
            for native_id, ents in sc:
                n_rows += 1
                if native_id is None or str(native_id).strip() == "":
                    n_blank_native[plat] += 1
                    continue
                uid = extract._uid(plat, str(native_id))
                if uid not in our_uid:
                    unmatched += 1
                    continue
                if best_len.get(uid, -1) >= len(ents):
                    matched += 1
                    continue
                if uid in best_len:               # a longer list for the same post: replace
                    rows = [r for r in rows if r["post_uid"] != uid]
                best_len[uid] = len(ents)
                matched += 1
                seen = set()
                for e in ents:
                    name = (e.get("entity") or "").strip()
                    label = (e.get("entity_type") or "").strip()
                    if not name:
                        continue
                    k2 = (name.lower(), label)
                    if k2 in seen:
                        continue
                    seen.add(k2)
                    ptype = parent.get(label, "UNMAPPED")
                    label_counts[(ptype, label)] += 1
                    rows.append({
                        "post_uid": uid, "platform": our_uid[uid], "surface_form": name,
                        "entity_uid": f"{ptype.lower()}:{slug(name)}",
                        "entity_type": ptype, "entity_subtype": label or None,
                        "ner_score": e.get("score"), "n_in_post": e.get("count"),
                    })
            sc.close()
            cov.append({"platform": plat, "source_table": table,
                        "prod_rows_with_entities": n_rows, "matched_to_our_posts": matched,
                        "unmatched": unmatched, "our_posts": our_posts_by_plat.get(plat, 0)})
            print(f"  {key:<18} {table:<24} prod rows with entities={n_rows:>8,} "
                  f"matched={matched:>8,} unmatched={unmatched:>8,}", flush=True)
        conn.rollback()

    m = pd.DataFrame(rows)
    if m.empty:
        print("ERROR: no mentions read")
        return 1
    m.insert(0, "mention_id", range(1, len(m) + 1))
    posted = ours.set_index("post_uid")["posted_at"]
    m["posted_at"] = m["post_uid"].map(posted)
    m["source"] = "prod_entities"
    m["snapshot_ts"] = snapshot_ts.isoformat()
    m["extracted_at"] = datetime.now(timezone.utc).isoformat()

    top_name = (m.groupby(["entity_uid", "surface_form"]).size().reset_index(name="n")
                .sort_values(["entity_uid", "n"], ascending=[True, False])
                .drop_duplicates("entity_uid").set_index("entity_uid")["surface_form"])
    dim = (m.groupby("entity_uid")
           .agg(entity_type=("entity_type", "first"), entity_subtype=("entity_subtype", "first"),
                n_subtypes=("entity_subtype", "nunique"), mention_rows=("mention_id", "size"),
                distinct_posts=("post_uid", "nunique"), first_seen=("posted_at", "min"),
                last_seen=("posted_at", "max"), avg_ner_score=("ner_score", "mean"))
           .reset_index())
    dim.insert(1, "canonical_name", dim["entity_uid"].map(top_name))
    # Coverage per OUR platform, from distinct posts actually kept (prod rows can repeat a
    # post, and two prod tables can feed one platform, so row counts overstate coverage).
    prod_rows = pd.DataFrame(cov).groupby("platform", as_index=False).agg(
        source_tables=("source_table", lambda x: ", ".join(sorted(set(x)))),
        prod_rows_with_entities=("prod_rows_with_entities", "sum"),
        prod_rows_not_in_our_extract=("unmatched", "sum"))
    kept = m.groupby("platform")["post_uid"].nunique().rename("our_posts_with_entities")
    covdf = (pd.DataFrame({"platform": list(our_posts_by_plat), "our_posts": list(our_posts_by_plat.values())})
             .merge(kept, on="platform", how="left").merge(prod_rows, on="platform", how="left"))
    covdf["our_posts_with_entities"] = covdf["our_posts_with_entities"].fillna(0).astype(int)
    covdf["share_of_our_posts_with_entities"] = covdf["our_posts_with_entities"] / covdf["our_posts"]

    con = duckdb.connect(str(OUT_DB), read_only=False)
    con.register("_m", m)
    con.register("_d", dim)
    con.register("_c", covdf)
    con.execute("CREATE OR REPLACE TABLE entity_mentions_prod AS SELECT * FROM _m")
    con.execute("CREATE OR REPLACE TABLE entity_dim_prod AS SELECT * FROM _d")
    con.execute("CREATE OR REPLACE TABLE entity_prod_coverage AS SELECT * FROM _c")
    con.close()

    print(f"\nwrote {OUT_DB.name}: {len(m):,} mentions, {len(dim):,} entities, "
          f"{m['post_uid'].nunique():,} posts of {len(ours):,} ({100*m['post_uid'].nunique()/len(ours):.1f}%)")
    print("\nper platform (matched posts / our posts):")
    for r in covdf.sort_values("our_posts", ascending=False).itertuples():
        print(f"  {r.platform:<18} {r.our_posts_with_entities:>8,} / {r.our_posts:>8,}  "
              f"{100*r.share_of_our_posts_with_entities:5.1f}%")
    print("\nlabels (parent type, subtype): mention counts")
    for (pt, lab), n in label_counts.most_common():
        print(f"  {pt:<9} {lab or '(blank)':<34} {n:>9,}")
    unm = sum(n for (pt, _), n in label_counts.items() if pt == "UNMAPPED")
    print(f"\nUNMAPPED mentions: {unm:,} ({100*unm/len(m):.2f}%)")
    if n_blank_native:
        print(f"rows skipped for blank native id: {dict(n_blank_native)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
