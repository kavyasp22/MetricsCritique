#!/usr/bin/env python3
"""Stage 27 -- hybrid theme (D01..D09) per post: pretrained bge-m3 zero-shot domain (stage 26) + a keyword cue of the same theme.

Reads data/post_domain_zs.parquet and the post text of data/analytics.duckdb (READ-ONLY); writes data/post_theme_v1.parquet:
    post_uid, theme ('D01'..'D09' or 'unassigned'), tier ('high' | 'medium' | ''), cue_hits, zs_best_score
See scripts/lib/domain_cues.py for the rule and collaboration/04_CURRENT_STATE.md for the measured precision.
Usage: python scripts/27_post_theme.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.domain_cues import cue_hits, hybrid_theme  # noqa: E402

OUT = ROOT / "data" / "post_theme_v1.parquet"


def main() -> int:
    z = pd.read_parquet(ROOT / "data" / "post_domain_zs.parquet",
                        columns=["post_uid", "platform", "zs_best", "zs_best_score", "confident_none", "is_first"])
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    t = con.execute("select post_uid, text from post_facts").fetchdf()
    con.close()
    m = z.merge(t, on="post_uid", how="left")
    res = [hybrid_theme(x.text, x.zs_best, x.zs_best_score, bool(x.confident_none)) for x in m.itertuples()]
    m["theme"] = [r[0] for r in res]
    m["tier"] = [r[1] for r in res]
    m["cue_hits"] = [cue_hits(x.text, x.theme) if x.theme != "unassigned" else 0 for x in m.itertuples()]
    out = m[["post_uid", "platform", "theme", "tier", "cue_hits", "zs_best_score", "is_first"]]
    assert out["post_uid"].is_unique
    out.to_parquet(OUT, index=False)
    d = out[out["is_first"]]
    print(f"wrote {OUT}: {len(out):,} posts ({len(d):,} distinct messages)")
    print(d.groupby(["theme", "tier"]).size().unstack(fill_value=0).to_string())
    print("assigned share of distinct messages:", round((d["theme"] != "unassigned").mean(), 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
