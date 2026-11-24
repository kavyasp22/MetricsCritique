#!/usr/bin/env python3
"""Stage 43 -- hybrid labelling of posts: relevance + D-theme + T-topic with the local Gemma, two independent passes, cheap checks as flags.

Per post
  1. pre-filter  : stage 26 `skip_llm` (clearly not security) -> tier 'not_relevant', no Gemma call
  2. pass 1      : Gemma, taxonomy + rules, T-topics in the sheet order
  3. pass 2      : Gemma again with a DIFFERENT prompt (topics in reverse order, reason before label) so it is not a repeat of pass 1
  4. tier        : both No                           -> 'not_relevant'  (a flagged 'confident' label is demoted to 'needs_review', see step 5)
                   both Yes with the same T-topic    -> 'confident'
                   anything else (disagree, Unsure, failed call, NONE) -> 'needs_review' with a reason
  5. flags (information only, never change a label): nearest-neighbour vote of the reference posts differs from Gemma, stage-27 D-theme differs.

No training, no clustering decides a label, nothing is written to production Postgres; posts are read from the local store (READ-ONLY).
The taxonomy (Topics + Rules sheets) is frozen into data/gold/taxonomy_v1.json on first use, so later runs do not depend on a workbook path.

Reads   data/analytics.duckdb (read-only), data/post_domain_zs.parquet, data/post_theme_v1.parquet, data/gold/*.parquet, GEMMA_* from .env
Writes  data/gold/gemma_label_<set>_v1.parquet   (versioned, git-ignored)
Usage   python scripts/43_gemma_label_pipeline.py --set gold198|sample5000 [--taxonomy-xlsx <workbook>] [--limit N]
        python scripts/43_gemma_label_pipeline.py --set subset --ids-file <parquet with post_uid> --name <name> [--chunk 2000]
          subset mode: labels the listed posts only, in chunks of --chunk posts. Each chunk is saved to data/gold/gemma_chunks_<name>_v1/ as soon as it is
          done, so a stopped run resumes (finished chunks are read back, not re-asked). Output: data/gold/gemma_label_<name>_v1.parquet. The gold198 and
          sample5000 outputs are never touched by this mode.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import gemma_client as G  # noqa: E402

GOLD = ROOT / "data" / "gold"
TAX = GOLD / "taxonomy_v1.json"
VERSION = "v1"


def load_taxonomy(xlsx: str | None) -> dict:
    if TAX.exists():
        return json.loads(TAX.read_text())
    assert xlsx, "first run needs --taxonomy-xlsx (Topics and Rules sheets)"
    tp = pd.read_excel(xlsx, sheet_name="Topics")
    tp.columns = ["D", "T", "Name", "Definition", "Typical", "NotThis"]
    tp.loc[tp["T"] == "D06.T02", "Name"] = "Shared Rivers, Dams & Transboundary Water"       # source sheet typo ("faircraft")
    rules = pd.read_excel(xlsx, sheet_name="Rules")
    tax = {"topics": [{"T": r.T, "name": r.Name, "definition": r.Definition, "not": r.NotThis} for r in tp.itertuples()],
           "rules": [[r[0], r[1]] for r in rules.itertuples(index=False)]}
    TAX.write_text(json.dumps(tax, ensure_ascii=False, indent=1))
    return tax


def prompts(tax: dict):
    fmt = lambda t: f"{t['T']} {t['name']}: {t['definition']} NOT: {t['not']}"
    rl = "\n".join(f"- {a}: {b}" for a, b in tax["rules"])
    head = ("You are a national-security analyst labelling social-media posts. Judge ONLY the text given; do not guess a source.\n"
            "Decide (1) is the post security relevant (state security, conflict, diplomacy, strategic economy, humanitarian crisis)? Answer Yes, No or Unsure "
            "(Unsure: link-only, truncated or unreadable). (2) If Yes, pick the single best T-topic code from the list; if Yes but nothing fits, use NONE.\n")
    s1 = head + "T-TOPICS:\n" + "\n".join(fmt(t) for t in tax["topics"]) + f"\n\nRULES:\n{rl}\n\n" + \
        'Reply with ONE JSON object only: {"security_relevant":"Yes|No|Unsure","t_topic":"Dnn.Tnn or NONE","confidence":"High|Medium|Low"}'
    s2 = head + "T-TOPICS (listed in reverse order):\n" + "\n".join(fmt(t) for t in reversed(tax["topics"])) + f"\n\nRULES:\n{rl}\n\n" + \
        "First compare the two closest topics against their definitions and 'NOT' lines, then decide. " + \
        'Reply with ONE JSON object only, keys in this order: {"reason":"<=25 words","security_relevant":"Yes|No|Unsure","t_topic":"Dnn.Tnn or NONE","confidence":"High|Medium|Low"}'
    return s1, s2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=["gold198", "sample5000", "subset"])
    ap.add_argument("--taxonomy-xlsx")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids-file")
    ap.add_argument("--name")
    ap.add_argument("--chunk", type=int, default=2000)
    a = ap.parse_args()
    if a.set == "subset":
        assert a.ids_file and a.name and re.fullmatch(r"[a-z0-9_]+", a.name), "subset mode needs --ids-file and --name (a-z, 0-9, _)"
        assert a.name not in ("gold198", "sample5000"), "pick a new name"
    tax = load_taxonomy(a.taxonomy_xlsx)
    valid = {t["T"] for t in tax["topics"]} | {"NONE"}
    S1, S2 = prompts(tax)
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    if a.set == "gold198":
        base = pd.read_parquet(GOLD / "analyst_gold_v1.parquet")[["post_uid", "rid", "sec", "D", "T"]]
    elif a.set == "subset":
        base = pd.read_parquet(a.ids_file)[["post_uid"]].drop_duplicates("post_uid")
    else:
        base = duckdb.connect(str(GOLD / "topic_knn_v1.duckdb"), read_only=True).execute("SELECT post_uid FROM knn_label_v1").df()
    if a.limit:
        base = base.head(a.limit)
    tx = con.execute("SELECT post_uid, platform, lang, text FROM post_facts WHERE post_uid IN (SELECT unnest(?))", [base.post_uid.tolist()]).df()
    zs = pd.read_parquet(ROOT / "data" / "post_domain_zs.parquet", columns=["post_uid", "skip_llm"])
    th = pd.read_parquet(ROOT / "data" / "post_theme_v1.parquet", columns=["post_uid", "theme"])
    d = base.merge(tx, on="post_uid").merge(zs, on="post_uid", how="left").merge(th, on="post_uid", how="left")
    d["skip_llm"] = d.skip_llm.fillna(False).astype(bool)
    if a.set == "gold198":
        d["skip_llm"] = False          # the evaluation set is never pre-filtered: the pre-filter is measured separately

    def val(o):
        if o.get("security_relevant") not in ("Yes", "No", "Unsure"):
            return "bad security"
        if o["security_relevant"] == "Yes" and str(o.get("t_topic")).upper() not in valid:
            return "bad topic"
        return None

    def run(system, item):
        res = G.chat_json(system, "POST:\n" + re.sub(r"\s+", " ", str(item.text))[:1200], max_tokens=1500, validate=val)
        o = res["obj"] or {}
        return {"post_uid": item.post_uid, "ok": res["ok"], "sec": o.get("security_relevant"), "T": str(o.get("t_topic")).upper() if o else None, "conf": o.get("confidence"), "lat": res["latency_s"], "reason": o.get("reason")}

    todo = list(d[~d.skip_llm].itertuples())
    t0 = time.time()
    if a.set == "subset":
        ckdir = GOLD / f"gemma_chunks_{a.name}_{VERSION}"; ckdir.mkdir(exist_ok=True)
        parts = []
        for i in range(0, len(todo), a.chunk):
            f = ckdir / f"chunk_{i // a.chunk:04d}.parquet"
            if f.exists():                                    # finished earlier: read it back, do not ask Gemma again
                parts.append(pd.read_parquet(f)); print(f"chunk {i // a.chunk} read from {f.name}", flush=True); continue
            ch = todo[i:i + a.chunk]; tc = time.time()
            c1 = pd.DataFrame(G.run_batch(ch, lambda r: run(S1, r))).add_prefix("p1_").rename(columns={"p1_post_uid": "post_uid"})
            c2 = pd.DataFrame(G.run_batch(ch, lambda r: run(S2, r))).add_prefix("p2_").rename(columns={"p2_post_uid": "post_uid"})
            cc = c1.merge(c2, on="post_uid"); cc.to_parquet(f.with_suffix(".tmp"), index=False); f.with_suffix(".tmp").rename(f)
            print(f"chunk {i // a.chunk}: {len(ch)} posts in {time.time() - tc:.0f}s ({len(ch) / (time.time() - tc):.2f}/s); done {min(i + a.chunk, len(todo))}/{len(todo)}", flush=True)
            parts.append(cc)
        pc = pd.concat(parts, ignore_index=True)
        p1 = pc[["post_uid"] + [c for c in pc.columns if c.startswith("p1_")]]; p2 = pc[["post_uid"] + [c for c in pc.columns if c.startswith("p2_")]]
    else:
        p1 = pd.DataFrame(G.run_batch(todo, lambda r: run(S1, r))).add_prefix("p1_").rename(columns={"p1_post_uid": "post_uid"})
        p2 = pd.DataFrame(G.run_batch(todo, lambda r: run(S2, r))).add_prefix("p2_").rename(columns={"p2_post_uid": "post_uid"})
    wall = time.time() - t0
    d = d.merge(p1, on="post_uid", how="left").merge(p2, on="post_uid", how="left")

    # tier
    def tier(r):
        if r.skip_llm:
            return "not_relevant", "pre-filter"
        if not (r.p1_ok and r.p2_ok):
            return "needs_review", "gemma call failed"
        if r.p1_sec == "No" and r.p2_sec == "No":
            return "not_relevant", "both passes No"
        if r.p1_sec == "Yes" and r.p2_sec == "Yes":
            if r.p1_T == r.p2_T and r.p1_T != "NONE":
                return ("confident", "both passes agree") if "Low" not in (r.p1_conf, r.p2_conf) else ("needs_review", "agree but Low confidence")
            return "needs_review", "passes pick different T-topics" if r.p1_T != r.p2_T else "no T-topic fits"
        if "Unsure" in (r.p1_sec, r.p2_sec):
            return "needs_review", "Unsure"
        return "needs_review", "passes disagree on relevance"
    tr = d.apply(tier, axis=1, result_type="expand"); d["tier"], d["tier_reason"] = tr[0], tr[1]
    d["label_sec"] = np.where(d.tier == "not_relevant", "No", np.where(d.tier == "confident", "Yes", None))
    d["label_T"] = np.where(d.tier == "confident", d.p1_T, None)
    d["label_D"] = d.label_T.map(lambda t: t[:3] if isinstance(t, str) else None)

    # information-only flags: nearest-neighbour vote of the reference posts, stage-27 theme
    Gd = pd.read_parquet(GOLD / "analyst_gold_v1.parquet"); refs = Gd[Gd.sec == "Yes"][["post_uid", "T"]]
    ai = GOLD / "ai_review_cluster_v2_220_v2.parquet"
    if ai.exists() and a.set != "gold198":
        A = pd.read_parquet(ai).merge(pd.read_parquet(GOLD / "cluster_review_key_Cluster_Review_Sample_v2_220.parquet")[["row", "post_uid"]], on="row")
        refs = pd.concat([refs, A[(A.sec == "Yes") & (A["T"] != "NONE") & A["T"].notna()][["post_uid", "T"]]]).drop_duplicates("post_uid")
    refs = refs[refs.post_uid.isin(set(con.execute("SELECT post_uid FROM post_embeddings").df().post_uid))]

    def emb(u):
        df = con.execute("SELECT post_uid, embedding FROM post_embeddings WHERE post_uid IN (SELECT unnest(?))", [list(u)]).df()
        M = np.vstack(df.embedding.map(lambda v: np.asarray(v, dtype=np.float32)).values); M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
        return dict(zip(df.post_uid, M))
    E = emb(list(refs.post_uid) + list(d.post_uid)); R = np.vstack([E[u] for u in refs.post_uid]); rT = refs["T"].values
    kt, ks = [], []
    for u in d.post_uid:
        s = E[u] @ R.T
        if a.set in ("gold198", "subset"):          # the post itself is a reference: leave it out
            s = np.where(refs.post_uid.values == u, -9, s)
        idx = np.argsort(-s)[:10]; w = {}
        for i in idx:
            w[rT[i]] = w.get(rT[i], 0) + max(s[i], 0)
        tot = sum(w.values()) or 1; win = max(w, key=w.get); kt.append(win); ks.append(w[win] / tot)
    d["knn_T"], d["knn_share"] = kt, ks
    lab = d.label_T.notna()
    d["flag_knn"] = lab & (d.knn_T != d.label_T) & (d.knn_share >= 0.5)
    d["flag_s27"] = lab & d.theme.notna() & (d.theme != "unassigned") & (d.label_D != d.theme)
    # a flagged "confident" label is demoted to needs_review: on the 198 analyst rows confident labels were right 91% without a flag and 71% with one
    dem = (d.tier == "confident") & (d.flag_knn | d.flag_s27)
    d.loc[dem, "tier_reason"] = "cross-check flag (nearest-neighbour or stage-27 theme differs)"
    d.loc[dem, "tier"] = "needs_review"
    d.loc[dem, ["label_T", "label_D", "label_sec"]] = None
    d["version"] = VERSION
    out = GOLD / f"gemma_label_{a.name if a.set == 'subset' else a.set}_{VERSION}.parquet"
    d.drop(columns=["text"]).to_parquet(out, index=False)
    print(f"{a.set}: {len(d)} posts | pre-filter skipped {int(d.skip_llm.sum())} | gemma posts {len(todo)} x2 passes in {wall:.0f}s | tiers {d.tier.value_counts().to_dict()} -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
