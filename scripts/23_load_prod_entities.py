#!/usr/bin/env python3
"""
Stage 23 -- make the production-entity tables the live entity layer in geo_ontology.duckdb.

Reads  data/prod_entities.duckdb  (stage 21 + 22: entity_mentions_prod_canon, entity_dim_prod_merged)
Writes data/geo_ontology.duckdb   (sole writer: stop the dashboard container first)

The Babelscape tables are kept for rollback inside the same file:
    entity_dim       -> entity_dim_babelscape        (renamed once, never overwritten)
    entity_mentions  -> entity_mentions_babelscape
and entity_dim / entity_mentions are then rebuilt from the production entities with the
SAME column names the downstream code reads (script 11, UI/api_server.py, build_dashboard.py).

One row per (post, merged entity): a post that says both "Putin" and "Vladimir Putin" is one
mention of one entity, so volumes are not double counted.
subtype_source = 'prod_model'; subtype_confidence is NULL (production gives an NER score,
not a subtype confidence; we do not invent one).

Roll back:  DROP TABLE entity_dim; DROP TABLE entity_mentions;
            ALTER TABLE entity_dim_babelscape RENAME TO entity_dim;  (same for mentions)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "data" / "prod_entities.duckdb"
GEO = ROOT / "data" / "geo_ontology.duckdb"


def main() -> int:
    if not PROD.exists():
        print(f"ERROR: run stages 21 and 22 first ({PROD.name} missing)")
        return 1
    con = duckdb.connect(str(GEO), read_only=False)
    con.execute(f"ATTACH '{PROD}' AS prod (READ_ONLY)")
    have = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables "
                                      "WHERE table_catalog='geo_ontology'").fetchall()}
    for t in ("entity_mentions", "entity_dim"):
        if f"{t}_babelscape" not in have:
            if t not in have:
                print(f"ERROR: {t} missing and no {t}_babelscape backup table")
                return 1
            con.execute(f"ALTER TABLE {t} RENAME TO {t}_babelscape")
            print(f"kept old table as {t}_babelscape")
    now = datetime.now(timezone.utc).isoformat()
    con.execute("BEGIN TRANSACTION")
    con.execute(f"""
        CREATE OR REPLACE TABLE entity_mentions AS
        WITH ranked AS (
          SELECT *, row_number() OVER (PARTITION BY post_uid, canonical_uid
                                       ORDER BY ner_score DESC NULLS LAST, mention_id) AS rn
          FROM prod.entity_mentions_prod_canon)
        SELECT row_number() OVER (ORDER BY post_uid, canonical_uid) AS mention_id,
               post_uid, surface_form, canonical_uid AS entity_uid,
               entity_type, entity_subtype,
               CAST(NULL AS DOUBLE) AS subtype_confidence,
               'prod_model' AS subtype_source,
               ner_score, CAST(NULL AS VARCHAR) AS lang, posted_at,
               'prod_entities (paniyoor NER, production)' AS model,
               extracted_at, CAST(NULL AS VARCHAR) AS subtype_resolved_at,
               CAST(NULL AS VARCHAR) AS subtype_method
        FROM ranked WHERE rn = 1""")
    con.execute("""
        CREATE OR REPLACE TABLE entity_dim AS
        SELECT m.entity_uid, d.display_name AS canonical_name, m.entity_type,
               mode(m.entity_subtype) AS entity_subtype, 'prod_model' AS subtype_source,
               count(*) AS mention_count, count(DISTINCT m.post_uid) AS distinct_posts,
               min(m.posted_at) AS first_seen, max(m.posted_at) AS last_seen,
               avg(m.ner_score) AS avg_ner_score, CAST(NULL AS DOUBLE) AS avg_subtype_confidence
        FROM entity_mentions m JOIN prod.entity_dim_prod_merged d USING (entity_uid)
        GROUP BY m.entity_uid, d.display_name, m.entity_type""")
    con.execute("COMMIT")
    n_m = con.execute("SELECT count(*) FROM entity_mentions").fetchall()[0][0]
    n_d = con.execute("SELECT count(*) FROM entity_dim").fetchall()[0][0]
    n_p = con.execute("SELECT count(DISTINCT post_uid) FROM entity_mentions").fetchall()[0][0]
    print(f"entity_mentions={n_m:,} entity_dim={n_d:,} posts={n_p:,}")
    print(con.execute("SELECT entity_type, count(*), sum(distinct_posts) FROM entity_dim GROUP BY 1 ORDER BY 2 DESC").fetchall())
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
