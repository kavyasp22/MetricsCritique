#!/usr/bin/env python3
"""Refresh the three Cross-Platform Spread tables inside the dashboard snapshot from data/metrics.duckdb.

Stage 19 only patches `cross_platform_stories` into UI/data/metrics.json; the shared-link and similar-meaning tables reach the snapshot only
through a full `UI/build_dashboard.py` run, which rewrites far more. After stage 19 and/or stage 20, run this instead: it replaces exactly
    cross_platform_stories, cross_platform_url_links, cross_platform_semantic_events
in metrics.json and metrics.json.gz using build_dashboard's own `frame_to_columnar` and the same ORDER BYs, and touches nothing else
(writing the untouched snapshot back with these settings reproduces the file byte for byte). Files are replaced atomically.

    .venv/bin/python scripts/refresh_cross_platform_snapshot.py            # reads data/metrics.duckdb READ-ONLY, rewrites UI/data/metrics.json(.gz)
    .venv/bin/python scripts/refresh_cross_platform_snapshot.py --dry-run  # report only
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI"))
from build_dashboard import frame_to_columnar  # noqa: E402  (module-level function; the import has no side effects)

QUERIES = {
    "cross_platform_stories": 'SELECT * FROM "cross_platform_stories"',
    "cross_platform_url_links": 'SELECT * FROM "cross_platform_url_links" ORDER BY n_platforms DESC, n_posts DESC',
    "cross_platform_semantic_events": 'SELECT * FROM "cross_platform_semantic_events" ORDER BY n_platforms DESC, n_unique_posts DESC',
}


def refresh(metrics_db: Path, json_path: Path, gz_path: Path, dry_run: bool = False) -> dict:
    con = duckdb.connect(str(metrics_db), read_only=True)
    try:
        new = {name: frame_to_columnar(con.execute(q).fetchdf(), name) for name, q in QUERIES.items()}
    finally:
        con.close()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    report = {}
    for name, t in new.items():
        old = payload["tables"].get(name)
        report[name] = {"old_rows": old["n"] if old else None, "new_rows": t["n"], "changed": old != t}
        payload["tables"][name] = t
    if dry_run:
        return report
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    for path, data in ((json_path, raw.encode("utf-8")), (gz_path, gzip.compress(raw.encode("utf-8"), compresslevel=6))):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)                                            # atomic: readers see the old or the new file, never half of one
    return report


def main() -> int:
    dry = "--dry-run" in sys.argv
    rep = refresh(ROOT / "data" / "metrics.duckdb", ROOT / "UI" / "data" / "metrics.json", ROOT / "UI" / "data" / "metrics.json.gz", dry)
    for name, r in rep.items():
        print(f"  {name:32s} {r['old_rows']} -> {r['new_rows']} rows" + ("  (changed)" if r["changed"] else "  (identical)"))
    print("dry run: nothing written" if dry else "snapshot updated (metrics.json and metrics.json.gz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
