#!/usr/bin/env python3
"""Recompute ONLY the language label and the readability columns of `post_scores`.

Why: `04_nlp_scores.py` had two defects in this part (fixed there):
  1. a missing declared language arrived as NaN and was stored as the string 'nan', so language detection never ran
     for every platform without a language field (about 182,000 posts);
  2. readability used Flesch-Kincaid for English and LIX for other languages and the cell mean averaged the two scales.
This script applies the fixed logic to the existing `post_scores` WITHOUT re-running the sentiment and toxicity
models: tone_*, hate_* and every other column are left exactly as they are.

Updates:   lang_resolved, readability_score (LIX), readability_metric, readability_valid, readability_reason,
           readability_fk_grade (new, English only), valid
Backup:    table `post_scores_pre_readability_fix` in analytics.duckdb (created once) and a copy of the parquet.
Needs:     data/analytics.duckdb writable (stop the dashboard container first: DuckDB is single-writer).

Run:  .venv/bin/python scripts/04b_readability_only.py            (add --dry-run to compute and report only)
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_M04 = None


def _m04():
    global _M04
    if _M04 is None:
        spec = importlib.util.spec_from_file_location("m04", HERE / "04_nlp_scores.py")
        _M04 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_M04)
    return _M04


def _resolve_chunk(items):
    m = _m04()
    return [m.resolve_lang(t, d) for t, d in items]


def main() -> int:
    dry = "--dry-run" in sys.argv
    m = _m04()
    from lib.config import DUCKDB_PATH, DATA_DIR
    from lib.duck import connect, write_parquet, replace_table_from_df

    t0 = time.time()
    con = connect(read_only=dry)
    ps = con.execute("SELECT * FROM post_scores").df()
    pf = con.execute("SELECT post_uid, text, lang FROM post_facts").df()
    print(f"post_scores {len(ps):,} rows, post_facts {len(pf):,} rows", flush=True)
    d = ps.merge(pf, on="post_uid", how="left")
    d["text"] = d["text"].fillna("").astype(str)
    items = list(zip(d["text"], d["lang"]))

    chunk = 4000
    chunks = [items[i:i + chunk] for i in range(0, len(items), chunk)]
    out: list[str] = []
    with ProcessPoolExecutor(max_workers=min(40, max(2, len(chunks)))) as ex:
        for i, res in enumerate(ex.map(_resolve_chunk, chunks)):
            out.extend(res)
            if i % 20 == 0:
                print(f"  language {len(out):,}/{len(items):,} ({time.time() - t0:.0f}s)", flush=True)
    d["lang_resolved_new"] = out
    reads = [m.score_readability(t, lg) for t, lg in zip(d["text"], d["lang_resolved_new"])]
    r = pd.DataFrame(reads)
    # Keep the Flesch-Kincaid grades already computed (textstat needs language data that may be missing in this
    # environment): where the old score WAS a Flesch-Kincaid grade, carry it over into the new fk column.
    old_fk = ps["readability_score"].where(ps["readability_metric"] == "flesch_kincaid_grade")
    r["readability_fk_grade"] = r["readability_fk_grade"].astype("float64").fillna(old_fk).where(r["readability_valid"])

    old_nan = int((ps["lang_resolved"].astype(str) == "nan").sum())
    new = ps.copy()
    new["lang_resolved"] = d["lang_resolved_new"].to_numpy()
    for c in ("readability_score", "readability_metric", "readability_valid", "readability_reason", "readability_fk_grade"):
        new[c] = r[c].to_numpy()
    new["valid"] = (new["tone_valid"].fillna(False).astype(bool) | new["hate_valid"].fillna(False).astype(bool)
                    | new["readability_valid"].fillna(False).astype(bool))
    # tone/hate must be untouched
    for c in ("tone_score", "tone_valid", "hate_score", "hate_valid", "word_count"):
        assert new[c].equals(ps[c]) or (new[c].fillna(-9e9).to_numpy() == ps[c].fillna(-9e9).to_numpy()).all(), c

    print(f"language label 'nan': {old_nan:,} -> {int((new['lang_resolved'].astype(str) == 'nan').sum()):,}; "
          f"readability valid {ps['readability_valid'].mean():.3f} -> {new['readability_valid'].mean():.3f}; "
          f"metrics {new['readability_metric'].value_counts(dropna=False).to_dict()}")
    if dry:
        print("dry run: nothing written")
        con.close()
        return 0
    con.execute("CREATE TABLE IF NOT EXISTS post_scores_pre_readability_fix AS SELECT * FROM post_scores")
    pq = Path(DATA_DIR) / "parquet" / "post_scores"
    if pq.exists() and not (Path(DATA_DIR) / "parquet" / "post_scores_pre_readability_fix").exists():
        shutil.copytree(pq, Path(DATA_DIR) / "parquet" / "post_scores_pre_readability_fix")
    write_parquet(new, "post_scores")
    replace_table_from_df(con, "post_scores", new)
    n = con.execute("SELECT count(*) FROM post_scores").fetchone()[0]
    con.close()
    print(f"post_scores rewritten ({n:,} rows) in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
