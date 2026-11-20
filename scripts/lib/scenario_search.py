"""Scenario search: who is actually involved in a scenario ("Iran War"), and the posts about their part in it.

Why not a keyword search: the words of a scenario appear in posts that are not about it, and many posts about it never use them. So relevance comes from
MEANING first, and the people from verification:

  1. Scenario brief (Gemma). From the typed scenario a short brief and 8 anchor sentences, each a different kind of post about it (a leader's statement,
     a strike report, negotiations, shipping, casualties, regional reaction ...). Written WITHOUT looking at keyword hits.
  2. Semantic retrieval (bge-m3). Every post of the selected days and platforms is ranked by cosine similarity to the anchors (stored vectors, data/post_emb_fp16.npy).
  3. Seeds and refinement. The 12 best posts of that ranking (found by meaning, not by words) are shown to Gemma, which rewrites the brief and anchors with what the
     scenario concretely is in this corpus (actors, events, places); the ranking is run again on the union of old and new anchors.
  4. Candidates. The top semantic posts that name one of the known people of the chosen type (aliases and spellings merged, titles removed: lib/entity_people.py).
  5. Verification (Gemma, 5 posts per call). For each shortlisted post: is it about the scenario (even without its words), and for each named person is the
     involvement `direct` (the post reports their action, decision, statement or role in the scenario), `mentioned` (in passing) or `unrelated`.
  6. Ranking. Scenario relevance and verified involvement decide (semantic score 0.55, Gemma involvement 0.45); views and engagement are only a last tie-break.

Pure logic plus HTTP calls; the API server (UI/api_server.py) hands it a DuckDB cursor. Nothing is written to a database.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import entity_people as EP
from . import gemma_client as G
from .config import embed_url

ROOT = Path(__file__).resolve().parents[2]
EMB = ROOT / "data" / "post_emb_fp16.npy"
IDX = ROOT / "data" / "post_emb_index.parquet"
CACHE = ROOT / "data" / "scenario_cache"
SEM_TOP = 1500          # posts kept after semantic ranking
SHORTLIST = 110         # posts with a named person that go to verification
PER_PERSON = 5          # best semantic posts checked for each person
MAX_PEOPLE = 24         # people with the most candidate posts that get checked
BATCH = 5               # posts per Gemma call (measured: 5 per call keeps accuracy at 29% of the tokens)
NO_SECRETS_NOTE = "scenario text and posts are sent to the local Gemma and the local embedding service only"

_state: dict = {"X": None, "ix": None}


# ------------------------------------------------------------------------------------------------------------------ embeddings
def _load():
    if _state["X"] is None:
        _state["X"] = np.load(EMB, mmap_mode="r")
        ix = pd.read_parquet(IDX)
        _state["ix"] = ix
    return _state["X"], _state["ix"]


def embed_texts(texts: list[str]) -> np.ndarray:
    req = urllib.request.Request(embed_url() + "/embed", data=json.dumps({"texts": texts}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        v = json.loads(r.read().decode())
    if "vectors" not in v:
        raise RuntimeError("embedding service: " + str(v.get("error")))
    a = np.asarray(v["vectors"], dtype=np.float32)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-9)


def semantic_rank(anchors: np.ndarray, day_from: str, day_to: str, platforms: Optional[list[str]], top: int = SEM_TOP) -> pd.DataFrame:
    """Distinct posts of the selected days / platforms ranked by the mean of their 3 best anchor similarities. Returns post_uid, platform, day, sem."""
    X, ix = _load()
    m = (ix["day"] >= day_from) & (ix["day"] <= day_to) & ix["is_first"].to_numpy()
    if platforms:
        m &= ix["platform"].isin(platforms)
    rows = np.flatnonzero(m.to_numpy())
    if not len(rows):
        return ix.iloc[0:0].assign(sem=[])
    sims = np.empty((len(rows), len(anchors)), np.float32)
    for s in range(0, len(rows), 50000):
        sims[s:s + 50000] = np.asarray(X[rows[s:s + 50000]], dtype=np.float32) @ anchors.T
    k = min(3, anchors.shape[0])
    sem = np.sort(sims, axis=1)[:, -k:].mean(axis=1)
    o = np.argsort(-sem)[:top]
    out = ix.iloc[rows[o]][["post_uid", "platform", "day"]].reset_index(drop=True)
    out["sem"] = sem[o]
    return out


# ------------------------------------------------------------------------------------------------------------------ brief (Gemma)
def _brief_prompt_v1(scenario: str) -> tuple[str, str]:
    sys_ = ("You prepare the search of a social-media monitoring system. Given a scenario, describe it and write anchor sentences: short, concrete sentences, each "
            "describing a DIFFERENT kind of post that is really about the scenario (for example a leader's statement or threat, a report of a strike or attack, "
            "negotiations or a ceasefire, sanctions or economic effects, shipping or energy effects, casualties or humanitarian effects, a regional reaction, an "
            "analysis). Do not just repeat the scenario's words; use the vocabulary people use when they discuss it. Also say what to EXCLUDE: kinds of posts that "
            "would contain the scenario's words but are about something else (games, sport, films, jokes, old history, unrelated local news).\n"
            'Reply with ONE JSON object: {"summary": "<2 sentences>", "anchors": ["<8 sentences of 15-30 words>"], "not_this": ["<3-5 short descriptions>"]}')
    return sys_, "Scenario: " + scenario


def _brief_prompt_v2(scenario: str, v1: dict, samples: list[str]) -> tuple[str, str]:
    sys_ = ("You refine the description of a scenario for a social-media monitoring system, using real posts retrieved by meaning for it. Ignore posts that are "
            "clearly not about the scenario. Describe what the scenario concretely is in THIS data: the time frame, the main actors (people, states, groups), "
            "the main events and places. Then write 8 new anchor sentences (15-30 words), each a different kind of post about it, naming the concrete actors, "
            "events and places you saw. List what to exclude.\n"
            'Reply with ONE JSON object: {"summary": "<3 sentences>", "actors": ["<up to 12 names>"], "anchors": ["<8 sentences>"], "not_this": ["<3-5 short descriptions>"]}')
    usr = ("Scenario: " + scenario + "\nFirst description: " + v1.get("summary", "") + "\n\nPosts retrieved by meaning:\n" +
           "\n".join(f"{i + 1}. {t}" for i, t in enumerate(samples)))
    return sys_, usr


def _valid_brief(o: dict) -> Optional[str]:
    if not isinstance(o.get("anchors"), list) or len(o["anchors"]) < 4:
        return "anchors"
    if not isinstance(o.get("summary"), str):
        return "summary"
    return None


def scenario_brief(scenario: str, sample_texts_fn) -> dict:
    """Two Gemma calls (+ the embedding service): first brief, retrieval, refined brief. Cached on disk by scenario text."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (hashlib.sha1(scenario.strip().lower().encode()).hexdigest()[:16] + ".json")
    if key.exists():
        return json.loads(key.read_text())
    s1, u1 = _brief_prompt_v1(scenario)
    r1 = G.chat_json(s1, u1, max_tokens=1500, validate=_valid_brief)
    if not r1["ok"]:
        raise RuntimeError("Gemma could not write the scenario brief: " + str(r1["error"]))
    v1 = r1["obj"]
    out = {"scenario": scenario, "v1": v1, "v2": None}
    anchors = embed_texts([scenario] + v1["anchors"][:8])
    samples = sample_texts_fn(anchors)                      # top semantic posts of the selected window (not keyword hits)
    if samples:
        s2, u2 = _brief_prompt_v2(scenario, v1, samples)
        r2 = G.chat_json(s2, u2, max_tokens=1800, validate=_valid_brief)
        if r2["ok"]:
            out["v2"] = r2["obj"]
    key.write_text(json.dumps(out, ensure_ascii=False))
    return out


def anchor_texts(brief: dict) -> list[str]:
    a = [brief["scenario"]] + list(brief["v1"].get("anchors", []))[:8]
    if brief.get("v2"):
        a += list(brief["v2"].get("anchors", []))[:8]
    return a


# ------------------------------------------------------------------------------------------------------------------ verification (Gemma)
def verify_system(brief: dict) -> str:
    v = brief.get("v2") or brief["v1"]
    nt = "; ".join((brief["v1"].get("not_this") or []) + ((brief.get("v2") or {}).get("not_this") or []))
    return (
        f"You verify social-media posts for the scenario \"{brief['scenario']}\".\nScenario in this data: {v.get('summary', '')}\n"
        f"NOT the scenario (same words, other subject): {nt}\n\n"
        "For each numbered post you get the people whose names were found in it. Decide:\n"
        "1. relevant: true only if the post is about the scenario itself: its events, actions, decisions, statements, threats, negotiations, military or political roles, "
        "consequences. A post can be relevant without using the scenario's words. False if the post merely shares words or names but is about something else "
        "(another conflict, domestic politics, a game, sport, an old event, a joke).\n"
        "2. For each listed person: involvement = \"direct\" when the post is about what THAT person does, decides, orders, threatens, says, negotiates or is responsible for in "
        "the scenario (also when the post is others criticising, questioning or reacting to that person's decision or statement about the scenario, or an analysis of their "
        "policy on it); \"mentioned\" when the name is only a tag, a list item, background or a passing comparison and the post is not about that person's part; "
        "\"unrelated\" when the name appears in a different context from the scenario (or is not the person you would expect). "
        "role = 4-8 words on what they do in the scenario (empty when unrelated). "
        "evidence = a short phrase copied EXACTLY from the post that shows it (empty when unrelated).\n"
        'Reply with ONE JSON object: {"answers": [{"id": <post number>, "relevant": true|false, "reason": "<max 15 words>", "people": [{"name": "<as listed>", '
        '"involvement": "direct|mentioned|unrelated", "role": "...", "evidence": "..."}]}, ...]} with exactly one answer per post, in order.')


def verify_user(items: list[dict]) -> str:
    return "\n\n".join(f"Post {i + 1} (people found: {', '.join(x['names']) or 'none'}):\n{x['text'][:1200]}" for i, x in enumerate(items))


def _valid_verify(o: dict, n: int) -> Optional[str]:
    a = o.get("answers")
    if not isinstance(a, list) or len(a) != n:
        return f"answers must be a list of {n}"
    for i, x in enumerate(a):
        if not isinstance(x, dict) or x.get("id") != i + 1 or not isinstance(x.get("relevant"), bool) or not isinstance(x.get("people", []), list):
            return f"answer {i + 1}"
        for pr in x.get("people", []):
            if pr.get("involvement") not in ("direct", "mentioned", "unrelated"):
                return f"answer {i + 1}: involvement"
    return None


def verify_posts(brief: dict, items: list[dict]) -> list[dict]:
    sysm = verify_system(brief)
    chunks = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]

    def one(ch):
        r = G.chat_json(sysm, verify_user(ch), max_tokens=min(3500, 500 + 260 * len(ch)), validate=lambda o: _valid_verify(o, len(ch)))
        res = []
        for j, it in enumerate(ch):
            a = r["obj"]["answers"][j] if r["ok"] else None
            res.append({"post_uid": it["post_uid"], "ok": r["ok"], "relevant": (a or {}).get("relevant"), "reason": (a or {}).get("reason", ""),
                        "people": (a or {}).get("people", []), "error": r["error"]})
        return res

    done = [x for part in G.run_batch(chunks, one, concurrency=10) for x in part]
    bad = [it for it, r in zip(items, done) if not r["ok"]]
    if bad:                                              # a failed call (busy server, unparseable answer) is retried once, two posts at a time
        again = {r["post_uid"]: r for part in G.run_batch([bad[i:i + 2] for i in range(0, len(bad), 2)], one, concurrency=10) for r in part}
        done = [again.get(r["post_uid"], r) if not r["ok"] else r for r in done]
    return done


# ------------------------------------------------------------------------------------------------------------------ the whole search
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _sem_norm(v: pd.Series) -> pd.Series:
    lo, hi = float(v.min()), float(v.max())
    return (v - lo) / (hi - lo) if hi > lo else v * 0 + 1.0


def run(cur, scenario: str, day_from: str, day_to: str, platforms: Optional[list[str]], etype: str, esub: str, *, refresh: bool = False) -> dict:
    """cur = a read-only DuckDB cursor on analytics.duckdb (post_facts, geo.entity_dim attached as in api_server)."""
    t0 = time.time(); timing = {}
    ckey = hashlib.sha1(json.dumps([scenario.strip().lower(), day_from, day_to, sorted(platforms or []), etype, esub]).encode()).hexdigest()[:16]
    CACHE.mkdir(parents=True, exist_ok=True)
    cf = CACHE / f"result_{ckey}.json"
    if cf.exists() and not refresh:
        return json.loads(cf.read_text()) | {"cached": True}

    def texts_of(uids, n=900):
        if not uids:
            return {}
        cur.register("u_tmp", pd.DataFrame({"post_uid": list(uids)}))
        d = cur.execute(f"SELECT p.post_uid, p.platform, p.posted_at, p.author_name, p.url, p.views, p.likes, p.comments, p.shares, "
                        f"left(coalesce(p.text, ''), 3000) AS text FROM analytics.post_facts p JOIN u_tmp ON u_tmp.post_uid = p.post_uid").fetchdf()
        cur.unregister("u_tmp")
        return {r.post_uid: r for r in d.itertuples()}

    # 1-3. brief, semantic ranking, refinement
    def samples(anchors):
        r = semantic_rank(anchors, day_from, day_to, platforms, top=12)
        tx = texts_of(r.post_uid.tolist())
        return [_norm(tx[u].text)[:260] for u in r.post_uid if u in tx]

    brief = scenario_brief(scenario, samples)
    timing["brief_s"] = round(time.time() - t0, 1)
    anchors = embed_texts(anchor_texts(brief))
    ranked = semantic_rank(anchors, day_from, day_to, platforms)
    timing["retrieval_s"] = round(time.time() - t0, 1)

    # 4. candidates: the posts of the ranking that name a known person of the chosen type
    where, params = ["entity_type = ?"], [etype]
    if esub:
        where.append("entity_subtype = ?"); params.append(esub)
    cand = cur.execute(f"SELECT entity_uid, canonical_name, entity_type, entity_subtype, distinct_posts FROM geo.entity_dim WHERE {' AND '.join(where)} "
                       "ORDER BY distinct_posts DESC LIMIT 300", params).fetchdf()
    cl = sorted(EP.clean_candidates(cand), key=lambda x: -x[3])[:120]
    comp = EP.compile_patterns(cl)
    tx = texts_of(ranked.post_uid.tolist())
    named: dict[str, list] = {}                 # person -> semantic-ranked posts naming them (best first)
    info: dict[str, dict] = {}
    n_named = 0
    for r in ranked.itertuples():
        t = tx.get(r.post_uid)
        if t is None:
            continue
        names = EP.find_people(_norm(t.text).lower(), comp)
        if names:
            n_named += 1
            info[r.post_uid] = {"post_uid": r.post_uid, "names": [n for _, n in names], "uids": [u for u, _ in names], "text": _norm(t.text), "sem": float(r.sem)}
            for u, _ in names:
                named.setdefault(u, []).append(r.post_uid)
    # balanced shortlist: every well-covered person gets their PER_PERSON best posts checked, so one name that is in most posts (the president) cannot fill it
    top_people = sorted(named, key=lambda u: -len(named[u]))[:MAX_PEOPLE]
    chosen: dict[str, None] = {}
    for u in top_people:
        for uid in named[u][:PER_PERSON]:
            chosen.setdefault(uid)
    short = [info[u] for u in sorted(chosen, key=lambda u: -info[u]["sem"])][:SHORTLIST]
    n_cand = {u: len(v) for u, v in named.items()}
    timing["shortlist_s"] = round(time.time() - t0, 1)

    # 5. verification
    ver = verify_posts(brief, short)
    timing["verify_s"] = round(time.time() - t0, 1)
    vmap = {v["post_uid"]: v for v in ver}
    sem = _sem_norm(pd.Series([s["sem"] for s in short])) if short else pd.Series([], dtype=float)

    # 6. ranking
    W = {"direct": 1.0, "mentioned": 0.35, "unrelated": 0.0}
    people: dict[str, dict] = {}
    posts_out = []
    for s, sn in zip(short, sem):
        v = vmap.get(s["post_uid"])
        if not v or not v["ok"]:
            continue
        t = tx[s["post_uid"]]
        rel = bool(v["relevant"])
        by_name = {p.get("name", "").lower(): p for p in v["people"]}
        for uid, name in zip(s["uids"], s["names"]):
            p = by_name.get(name.lower()) or {"involvement": "unrelated", "role": "", "evidence": ""}
            inv = p.get("involvement", "unrelated") if rel else "unrelated"
            score = 0.55 * float(sn) + 0.45 * W[inv] if rel else 0.0
            ev = p.get("evidence") or ""
            rec = {"post_uid": s["post_uid"], "name": name, "involvement": inv, "role": p.get("role", ""), "evidence": ev,
                   "evidence_ok": bool(ev) and re.sub(r"\s+", " ", ev).lower() in s["text"].lower(), "relevant": rel, "reason": v.get("reason", ""),
                   "score": round(score, 4), "sem": round(s["sem"], 4), "platform": t.platform, "posted_at": str(t.posted_at), "author_name": t.author_name,
                   "url": t.url, "views": None if pd.isna(t.views) else int(t.views), "text": s["text"][:420]}
            posts_out.append(rec)
            o = people.setdefault(uid, {"entity_uid": uid, "name": name, "candidates": n_cand.get(uid, 0), "checked": 0, "direct": 0, "mentioned": 0, "unrelated": 0, "score": 0.0, "roles": {}})
            o[inv] += 1; o["checked"] += 1
            o["score"] += score if inv == "direct" else 0.0
            if inv == "direct" and p.get("role"):
                o["roles"][p["role"]] = o["roles"].get(p["role"], 0) + 1
    import math
    for o in people.values():          # who is most involved: the share of direct involvement among their checked posts, applied to every scenario post that names them
        o["involvement"] = round(o["direct"] / o["checked"], 2) if o["checked"] else 0.0
        rate = (o["direct"] + 1) / (o["checked"] + 2)                           # shrunk share of direct involvement in the checked posts
        o["est_direct_posts"] = int(round(rate * o["candidates"]))              # that share applied to all the scenario posts that name them
        o["score"] = round(rate * o["candidates"], 2)
    plist = sorted(people.values(), key=lambda o: (-o["score"], -o["involvement"]))
    for o in plist:
        o["top_role"] = max(o["roles"], key=o["roles"].get) if o["roles"] else ""
        o.pop("roles")
    out = {"scenario": scenario, "from": day_from, "to": day_to, "entity_type": etype, "entity_subtype": esub,
           "brief": {"summary": (brief.get("v2") or brief["v1"]).get("summary", ""), "actors": (brief.get("v2") or {}).get("actors", []),
                     "not_this": brief["v1"].get("not_this", []), "anchors_used": len(anchor_texts(brief))},
           "stats": {"semantic_candidates": int(len(ranked)), "naming_a_person": n_named, "verified": int(len(ver)), "verified_ok": int(sum(1 for v in ver if v["ok"])),
                     "relevant": int(sum(1 for v in ver if v["relevant"])), "people_candidates": len(cl)},
           "people": plist, "posts": posts_out, "timing": timing, "total_s": round(time.time() - t0, 1), "cached": False,
           "method": "meaning first (bge-m3 retrieval from a Gemma-written scenario brief), then Gemma verification of each shortlisted post; views are not used for relevance"}
    cf.write_text(json.dumps(out, ensure_ascii=False, default=str))
    return out
