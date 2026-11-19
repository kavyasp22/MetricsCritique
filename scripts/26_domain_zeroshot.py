#!/usr/bin/env python3
"""
Stage 26 -- zero-shot domain scoring of every embedded post with the pretrained bge-m3 (no training).

Reads  data/analytics.duckdb (READ-ONLY): post_embeddings, post_facts; data/post_dedup.parquet (stage 25, optional);
       data/domain_prototype_emb_v1.npz (stage 26a).
Writes data/post_domain_zs.parquet (new file, nothing existing changes):
    post_uid, platform, zs_domain (D01..D09 or none; provisional, about 60% right among security posts),
    zs_best, zs_best_score, zs_second, zs_margin, none_score, none_gap, confident_none, is_first,
    chars (text length), security_cue (a clear security term is in the text), skip_llm, needs_llm

`confident_none` = a "not security" description beats every domain by at least NONE_GAP (lib/domain_zeroshot.py). On 273
hand-reviewed posts those were 97% truly non-security (2 of 149 security posts dropped).
`skip_llm` = confident_none AND no security cue in the text (drone, missile, army, J-20, alert patterns ...): such a post is
never skipped. `needs_llm` = not skip_llm AND (text >= 40 characters OR it carries a security cue); short posts without a cue
are not routed (the LLM's own rule). Everything that needs the LLM still needs Gemma prompt v2: the zero-shot domain choice is
not reliable enough on its own. Posts without an embedding (about 700) are not in this file and also need the LLM.

    python scripts/26_domain_zeroshot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import domain_zeroshot as Z  # noqa: E402

SRC = ROOT / "data" / "analytics.duckdb"
PROTO = ROOT / "data" / "domain_prototype_emb_v1.npz"
DEDUP = ROOT / "data" / "post_dedup.parquet"
OUT = ROOT / "data" / "post_domain_zs.parquet"
BATCH = 40000
MIN_CHARS = 40          # the LLM routing stage (lib/entity_routing.MIN_TEXT_CHARS) does not route shorter posts


def main() -> int:
    if not PROTO.exists():
        print(f"ERROR: run scripts/26a_embed_domain_prototypes.py first ({PROTO.name} missing)")
        return 1
    pz = np.load(PROTO, allow_pickle=True)
    PX, pdom = pz["X"], pz["dom"]
    con = duckdb.connect(str(SRC), read_only=True)
    n_emb = con.execute("SELECT count(*) FROM post_embeddings").fetchall()[0][0]
    print(f"embedded posts: {n_emb:,}", flush=True)
    rdr = con.execute("SELECT e.post_uid, p.platform, e.embedding, left(coalesce(p.text, ''), 600) AS t, "
                      "length(trim(coalesce(p.text, ''))) AS n FROM post_embeddings e "
                      "JOIN post_facts p ON p.post_uid = e.post_uid").to_arrow_reader(BATCH)
    parts = []
    done = 0
    for rb in rdr:
        uid = rb.column(0).to_pylist()
        plat = rb.column(1).to_pylist()
        col = rb.column(2)
        X = col.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(uid), -1)
        s = Z.score(X, PX, pdom)
        txt = rb.column(3).to_pylist()
        s["chars"] = np.array(rb.column(4).to_pylist(), dtype=np.int32)
        s["security_cue"] = np.array([Z.security_cue(x) for x in txt], dtype=bool)
        parts.append(pd.DataFrame({"post_uid": uid, "platform": plat, **s}))
        done += len(uid)
        print(f"  scored {done:,}", flush=True)
    out = pd.concat(parts, ignore_index=True)
    if DEDUP.exists():
        d = pd.read_parquet(DEDUP, columns=["post_uid", "is_first"])
        out = out.merge(d, on="post_uid", how="left")
        out["is_first"] = out["is_first"].fillna(True)
    else:
        out["is_first"] = True
    out["skip_llm"] = out["confident_none"] & ~out["security_cue"]
    out["needs_llm"] = ~out["skip_llm"] & ((out["chars"] >= MIN_CHARS) | out["security_cue"])
    assert out["post_uid"].is_unique
    out["zs_best_score"] = out["zs_best_score"].astype("float32")
    for c in ("zs_margin", "none_score", "none_gap"):
        out[c] = out[c].astype("float32")
    out.to_parquet(OUT, index=False)
    n = len(out)
    print(f"\nwrote {OUT}: {n:,} posts")
    print("provisional domain (zero-shot):", out["zs_domain"].value_counts().to_dict())
    cn = out["confident_none"]
    dist = out[out["is_first"]]
    print(f"confident none: {cn.mean():.1%} of all posts | {dist['confident_none'].mean():.1%} of the {len(dist):,} distinct messages")
    print(f"  of which carry a security cue and are NOT skipped: {int((dist['confident_none'] & dist['security_cue']).sum()):,}")
    print(f"skip_llm (clearly not security, no cue): {int(dist['skip_llm'].sum()):,} distinct messages")
    print(f"short (< {MIN_CHARS} chars) without a cue, not routed: {int((~dist['skip_llm'] & ~dist['needs_llm']).sum()):,}")
    print(f"needs_llm: {int(dist['needs_llm'].sum()):,} distinct messages (plus the ones without an embedding)")
    print(f"security cue present in {int(dist['security_cue'].sum()):,} distinct messages; short messages rescued by a cue: "
          f"{int((dist['security_cue'] & (dist['chars'] < MIN_CHARS)).sum()):,}")
    t = out.groupby("platform").agg(posts=("post_uid", "size"), confident_none=("confident_none", "mean"))
    print(t.sort_values("posts", ascending=False).round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
