#!/usr/bin/env python3
"""Stage 44 -- Re-check Gemma labelling on the 198 analyst gold rows using English-translated text.

Translates non-English posts into English via Gemma first, then runs the standard 2-pass
hybrid labelling pipeline (same prompts, rules and taxonomy as Stage 43).

Outputs comparison with original untranslated baseline (gemma_label_gold198_v1.parquet).
"""
from __future__ import annotations

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
from lib import gemma_client as G

GOLD = ROOT / "data" / "gold"
TAX_PATH = GOLD / "taxonomy_v1.json"
OUT_PATH = GOLD / "gemma_label_gold198_translated_v1.parquet"
BASE_PATH = GOLD / "gemma_label_gold198_v1.parquet"


def is_non_english(lang: str, text: str) -> bool:
    if lang not in ("en", "-"):
        return True
    # check non-ascii density
    non_ascii = len([c for c in text if ord(c) > 127])
    return non_ascii >= 15


def main() -> int:
    tax = json.loads(TAX_PATH.read_text())
    valid = {t["T"] for t in tax["topics"]} | {"NONE"}

    # Load prompts
    fmt = lambda t: f"{t['T']} {t['name']}: {t['definition']} NOT: {t['not']}"
    rl = "\n".join(f"- {a}: {b}" for a, b in tax["rules"])
    head = (
        "You are a national-security analyst labelling social-media posts. Judge ONLY the text given; do not guess a source.\n"
        "Decide (1) is the post security relevant (state security, conflict, diplomacy, strategic economy, humanitarian crisis)? Answer Yes, No or Unsure "
        "(Unsure: link-only, truncated or unreadable). (2) If Yes, pick the single best T-topic code from the list; if Yes but nothing fits, use NONE.\n"
    )
    s1 = head + "T-TOPICS:\n" + "\n".join(fmt(t) for t in tax["topics"]) + f"\n\nRULES:\n{rl}\n\n" + \
        'Reply with ONE JSON object only: {"security_relevant":"Yes|No|Unsure","t_topic":"Dnn.Tnn or NONE","confidence":"High|Medium|Low"}'
    s2 = head + "T-TOPICS (listed in reverse order):\n" + "\n".join(fmt(t) for t in reversed(tax["topics"])) + f"\n\nRULES:\n{rl}\n\n" + \
        "First compare the two closest topics against their definitions and 'NOT' lines, then decide. " + \
        'Reply with ONE JSON object only, keys in this order: {"reason":"<=25 words","security_relevant":"Yes|No|Unsure","t_topic":"Dnn.Tnn or NONE","confidence":"High|Medium|Low"}'

    # Load gold 198
    gold = pd.read_parquet(GOLD / "analyst_gold_v1.parquet")
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    tx = con.execute("SELECT post_uid, platform, lang, text FROM post_facts WHERE post_uid IN (SELECT unnest(?))", [gold.post_uid.tolist()]).df()
    zs = pd.read_parquet(ROOT / "data" / "post_domain_zs.parquet", columns=["post_uid", "skip_llm"])
    th = pd.read_parquet(ROOT / "data" / "post_theme_v1.parquet", columns=["post_uid", "theme"])
    d = gold[["post_uid", "rid", "sec", "D", "T"]].merge(tx, on="post_uid").merge(zs, on="post_uid", how="left").merge(th, on="post_uid", how="left")
    d["skip_llm"] = False

    # 1. Translate non-English posts
    print(f"Checking 198 gold rows for translation...")
    needs_trans = []
    for r in d.itertuples():
        if is_non_english(str(r.lang), str(r.text)):
            needs_trans.append(r)
    print(f"Found {len(needs_trans)} posts needing translation to English.")

    def translate(item):
        t = re.sub(r"\s+", " ", str(item.text))[:1200]
        res = G.chat_json(
            system="Translate the social media post into English accurately. Preserve technical/military/political terms. Reply with JSON: {\"english\": \"...\"}",
            user="POST:\n" + t,
            max_tokens=400,
        )
        en = res["obj"].get("english") if (res["ok"] and res["obj"]) else t
        return {"post_uid": item.post_uid, "translated_text": en, "trans_ok": res["ok"]}

    t0 = time.time()
    trans_map = {}
    if needs_trans:
        print("Translating non-English posts in parallel...")
        trans_res = G.run_batch(needs_trans, translate)
        trans_map = {r["post_uid"]: r["translated_text"] for r in trans_res}
        print(f"Translation finished in {time.time() - t0:.1f}s.")

    d["clean_text"] = d.apply(lambda r: trans_map.get(r["post_uid"], str(r["text"])), axis=1)

    # 2. Run Gemma 2-pass labelling on clean_text
    def val(o):
        if o.get("security_relevant") not in ("Yes", "No", "Unsure"):
            return "bad security"
        if o["security_relevant"] == "Yes" and str(o.get("t_topic")).upper() not in valid:
            return "bad topic"
        return None

    def run_pass(system, item):
        res = G.chat_json(system, "POST:\n" + re.sub(r"\s+", " ", str(item.clean_text))[:1200], max_tokens=1500, validate=val)
        o = res["obj"] or {}
        return {"post_uid": item.post_uid, "ok": res["ok"], "sec": o.get("security_relevant"), "T": str(o.get("t_topic")).upper() if o else None, "conf": o.get("confidence"), "lat": res["latency_s"], "reason": o.get("reason")}

    todo = list(d.itertuples())
    print("Running Pass 1 with Gemma...")
    p1 = pd.DataFrame(G.run_batch(todo, lambda r: run_pass(s1, r))).add_prefix("p1_").rename(columns={"p1_post_uid": "post_uid"})
    print("Running Pass 2 with Gemma (reverse topic order)...")
    p2 = pd.DataFrame(G.run_batch(todo, lambda r: run_pass(s2, r))).add_prefix("p2_").rename(columns={"p2_post_uid": "post_uid"})

    d = d.merge(p1, on="post_uid", how="left").merge(p2, on="post_uid", how="left")

    # 3. Compute tiers
    def tier(r):
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

    tr = d.apply(tier, axis=1, result_type="expand")
    d["tier"], d["tier_reason"] = tr[0], tr[1]
    d["label_sec"] = np.where(d.tier == "not_relevant", "No", np.where(d.tier == "confident", "Yes", None))
    d["label_T"] = np.where(d.tier == "confident", d.p1_T, None)
    d["label_D"] = d.label_T.map(lambda t: t[:3] if isinstance(t, str) else None)

    # 4. Save
    d.to_parquet(OUT_PATH, index=False)
    print(f"Saved translated run to {OUT_PATH}")

    # 5. Compare with untranslated baseline
    print("\n" + "="*70)
    print("=== TRANSLATED RE-CHECK EVALUATION ON GOLD 198 ROWS ===")
    print("="*70)

    base = pd.read_parquet(BASE_PATH) if BASE_PATH.exists() else None

    # Overall metrics
    yes_mask = d["sec"] == "Yes"
    conf_mask = d["tier"] == "confident"
    exact_T = (d.loc[yes_mask, "T"] == d.loc[yes_mask, "label_T"])
    sec_match = (d["sec"] == d["label_sec"])

    print(f"Total rows: {len(d)}")
    print(f"Tiers: {d['tier'].value_counts().to_dict()}")

    if base is not None:
        b_yes_mask = base["sec"] == "Yes"
        b_exact_T = (base.loc[b_yes_mask, "T"] == base.loc[b_yes_mask, "label_T"])
        b_sec_match = (base["sec"] == base["label_sec"])
        print(f"\nMetric Comparison (Untranslated Baseline -> Translated Re-check):")
        print(f"  Security Match Rate:        {b_sec_match.mean():.1%} -> {sec_match.mean():.1%}")
        print(f"  T-Topic Exact Match on Yes: {b_exact_T.mean():.1%} -> {exact_T.mean():.1%} (n={yes_mask.sum()})")
        print(f"  Confident Tier Count:       {(base['tier']=='confident').sum()} -> {conf_mask.sum()}")
        # Confident precision
        b_conf_yes = base[base["tier"] == "confident"]
        t_conf_yes = d[d["tier"] == "confident"]
        b_prec = (b_conf_yes["T"] == b_conf_yes["label_T"]).mean() if len(b_conf_yes) else 0
        t_prec = (t_conf_yes["T"] == t_conf_yes["label_T"]).mean() if len(t_conf_yes) else 0
        print(f"  Confident Tier Precision:   {b_prec:.1%} -> {t_prec:.1%}")

        # Check translated subset specifically
        trans_uids = set(trans_map.keys())
        sub_base = base[base["post_uid"].isin(trans_uids)]
        sub_d = d[d["post_uid"].isin(trans_uids)]
        sub_yes = sub_d["sec"] == "Yes"
        print(f"\nTranslated Non-English Posts Specifically (n={len(trans_uids)}):")
        print(f"  Security Match Rate:        {(sub_base['sec'] == sub_base['label_sec']).mean():.1%} -> {(sub_d['sec'] == sub_d['label_sec']).mean():.1%}")
        if sub_yes.sum() > 0:
            b_sub_T = (sub_base.loc[sub_base['sec']=='Yes', 'T'] == sub_base.loc[sub_base['sec']=='Yes', 'label_T']).mean()
            t_sub_T = (sub_d.loc[sub_yes, 'T'] == sub_d.loc[sub_yes, 'label_T']).mean()
            print(f"  T-Topic Exact Match on Yes: {b_sub_T:.1%} -> {t_sub_T:.1%} (n={sub_yes.sum()})")
    print("="*70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
