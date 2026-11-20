"""Event-contextual entity intelligence for a trending event: who is actually involved, what are they doing, which real posts show it.

Topic-agnostic (military conflict, political event, protest, diplomacy, economy, disaster, cyber incident ...). The event is NOT taken from the typed words
or from a model's general knowledge; it is read from the posts of the data, and the posts that ground it stay the reference all the way to validation.

  1. Hint (Gemma, 1 call). From the typed words only: a short list of example sentences, used ONLY to start a semantic ranking (never as the event's truth).
  2. Semantic ranking (bge-m3). Every distinct post of the selected days / platforms is ranked by cosine similarity to the hint sentences (stored vectors).
  3. Diverse grounding sample (60 posts, post ids kept). Not the top 60: drawn from the top 1,500 by stratified farthest-point sampling over platform x time
     bucket x meaning, with a third of the picks taken from the fringe of the ranking (rank 500-1,500) so that posts that do NOT belong are in the sample too.
  4. Grounded event context (Gemma). From the sampled posts only: what the event is, developments with time range and supporting post numbers, actors (people,
     organisations, countries) with roles and aliases, titles that point to a person ("the US president" -> who, with evidence), contradictions, and an honest
     assessment: clear | multiple_developments | ambiguous | insufficient.
  5. Adaptive grounding. If the sample is insufficient / ambiguous / contradictory or Gemma asks for more, further posts are retrieved (its follow-up
     questions and the developments it found become new retrieval anchors; each round adds a diverse batch, at most 3 rounds, at most 160 posts) and the context
     is rebuilt. Several developments are kept as several developments; an ambiguous trend is reported as ambiguous, not forced into one story.
  6. Retrieval by the grounded context. New anchors (developments, actors' actions) rank the posts again; the posts naming a known person of the chosen type
     (spellings merged, titles removed, titles that the context resolves, actors the context found) are shortlisted, the best 5 per person.
  7. Validation (Gemma, 5 posts per call) against the grounded context: relevant to the event (words not needed)? For each person: direct (took part, led,
     ordered, conducted, or made a statement about the event), indirect (connected through a relevant decision, response, organisation or negotiation),
     mention (named in connection with the event but involvement not established) or unrelated, with role and an evidence phrase copied from the post. A direct or
     indirect call without an evidence phrase found in the post is downgraded to mention.
  8. Ranking by verified involvement and relevance. Mention counts, views and engagement are not used: a person's strength is how many of their 5 best
     posts show real involvement; ties go to the number of verified involvement posts.

Pure logic plus HTTP calls; the API server hands it a DuckDB cursor. Nothing is written to a database.
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
SEM_TOP = 1500          # posts kept after a semantic ranking
GROUND_N = 60           # first grounding sample
GROUND_STEP = 40        # posts added per adaptive round
GROUND_MAX = 160        # most posts that may ground a context
GROUND_ROUNDS = 3
SHORTLIST = 110         # posts with a named person that go to validation
PER_PERSON = 5          # best semantic posts validated for each person
MAX_PEOPLE = 24         # people with the most candidate posts that get validated
BATCH = 5               # posts per Gemma call (measured: 5 per call keeps accuracy at 29% of the tokens)
W_INV = {"direct": 1.0, "indirect": 0.5, "mention": 0.0, "unrelated": 0.0}
CACHE_VERSION = "g2"    # bump when the method changes: old cached results are not reused

_state: dict = {"X": None, "ix": None, "pos": None}


# ------------------------------------------------------------------------------------------------------------------ embeddings
def _load():
    if _state["X"] is None:
        _state["X"] = np.load(EMB, mmap_mode="r")
        ix = pd.read_parquet(IDX)
        _state["ix"] = ix
        _state["pos"] = pd.Series(np.arange(len(ix)), index=ix["post_uid"])
    return _state["X"], _state["ix"]


def embed_texts(texts: list[str]) -> np.ndarray:
    req = urllib.request.Request(embed_url() + "/embed", data=json.dumps({"texts": texts}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        v = json.loads(r.read().decode())
    if "vectors" not in v:
        raise RuntimeError("embedding service: " + str(v.get("error")))
    a = np.asarray(v["vectors"], dtype=np.float32)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-9)


def semantic_rank(anchors: np.ndarray, day_from: str, day_to: str, platforms: Optional[list[str]], top: int = SEM_TOP, exclude: Optional[set] = None) -> pd.DataFrame:
    """Distinct posts of the selected days / platforms ranked by the mean of their 3 best anchor similarities. Returns post_uid, platform, day, sem (rank order)."""
    X, ix = _load()
    m = (ix["day"] >= day_from) & (ix["day"] <= day_to) & ix["is_first"].to_numpy()
    if platforms:
        m &= ix["platform"].isin(platforms)
    if exclude:
        m &= ~ix["post_uid"].isin(exclude)
    rows = np.flatnonzero(m.to_numpy())
    if not len(rows):
        return ix.iloc[0:0][["post_uid", "platform", "day"]].assign(sem=[])
    sims = np.empty((len(rows), len(anchors)), np.float32)
    for s in range(0, len(rows), 50000):
        sims[s:s + 50000] = np.asarray(X[rows[s:s + 50000]], dtype=np.float32) @ anchors.T
    k = min(3, anchors.shape[0])
    sem = np.sort(sims, axis=1)[:, -k:].mean(axis=1)
    o = np.argsort(-sem)[:top]
    out = ix.iloc[rows[o]][["post_uid", "platform", "day"]].reset_index(drop=True)
    out["sem"] = sem[o]
    return out


def diverse_sample(pool: pd.DataFrame, n: int, day_from: str, day_to: str, fringe_from: int = 500) -> list[str]:
    """n post ids drawn from a ranked pool, NOT the top n: round-robin over platform x time-bucket strata, in each stratum the post farthest (in meaning)
    from everything already chosen; every third pick comes from the fringe of the ranking (rank >= fringe_from) so that posts that do not belong are sampled too."""
    X, _ = _load()
    pos = _state["pos"]
    pool = pool.reset_index(drop=True).assign(rank=lambda d: np.arange(len(d)))
    if len(pool) <= n:
        return pool["post_uid"].tolist()
    d0, d1 = pd.Timestamp(day_from), pd.Timestamp(day_to)
    span = max((d1 - d0).days, 1)
    pool["bucket"] = [min(4, int(5 * (pd.Timestamp(d) - d0).days / (span + 1))) for d in pool["day"]]
    vec = np.asarray(X[pos.loc[pool["post_uid"]].to_numpy()], dtype=np.float32)
    strata = {k: g.index.tolist() for k, g in pool.groupby(["platform", "bucket"])}
    order = sorted(strata, key=lambda k: -len(strata[k]))
    chosen: list[int] = []
    taken: set[int] = set()
    cv = np.empty((0, vec.shape[1]), np.float32)
    while len(chosen) < n:
        progressed = False
        for k in order:
            if len(chosen) >= n:
                break
            cand = [i for i in strata[k] if i not in taken]
            if not cand:
                continue
            if len(chosen) % 3 == 2 and any(pool.at[i, "rank"] >= fringe_from for i in cand):
                cand = [i for i in cand if pool.at[i, "rank"] >= fringe_from]
            cand = cand[:80]
            if len(cv):
                dist = 1.0 - (vec[cand] @ cv.T).max(axis=1)         # distance to the nearest post already chosen
                best = cand[int(np.argmax(dist))]
            else:
                best = cand[0]
            chosen.append(best); taken.add(best); cv = np.vstack([cv, vec[best][None, :]]); progressed = True
        if not progressed:
            break
    return pool.loc[chosen, "post_uid"].tolist()


# ------------------------------------------------------------------------------------------------------------------ Gemma helpers
def _chat(system: str, user: str, max_tokens: int, validate) -> dict:
    r = G.chat_json(system, user, max_tokens=max_tokens, validate=validate)
    if not r["ok"]:
        raise RuntimeError("Gemma call failed: " + str(r["error"]))
    return r["obj"]


def hint_sentences(scenario: str) -> list[str]:
    """Step 1: example sentences from the typed words only. They start a ranking; they are never treated as facts about the event."""
    sysm = ("You help a search system find social-media posts about a topic by meaning. Given a short topic, write 8 short, concrete example sentences "
            "(15-30 words), each of a DIFFERENT kind of post that could be about it (an official statement, a report of an action or incident, a reaction, an "
            "analysis, a consequence, a negotiation or response ...). Use the vocabulary people use. Do not assume details you cannot know.\n"
            'Reply with ONE JSON object: {"sentences": ["...", "... 8 in total"]}')
    o = _chat(sysm, "Topic: " + scenario, 1000, lambda o: None if isinstance(o.get("sentences"), list) and len(o["sentences"]) >= 4 else "sentences")
    return [str(x) for x in o["sentences"][:8]]


def _ground_prompt() -> str:
    return (
        "You read a SAMPLE of real social-media posts that were retrieved for a trending topic, and describe the event they show. Use ONLY the posts: every claim must "
        "be supported by post numbers; do not add facts from your own knowledge; if the posts do not establish something, say so. The sample deliberately contains posts "
        "from several platforms and days and some posts that may NOT belong to the topic. The topic can be of any kind (conflict, politics, protest, diplomacy, economy, "
        "disaster, cyber incident ...).\n"
        "Reply with ONE JSON object:\n"
        '{"assessment": "clear|multiple_developments|ambiguous|insufficient",\n'
        ' "event_summary": "<2-4 sentences: what the posts show is happening, when, where>",\n'
        ' "developments": [{"title": "<short>", "summary": "<1-2 sentences>", "time": "<dates or period seen in the posts, or empty>", "posts": [<post numbers>]}],\n'
        ' "actors": [{"name": "<as written>", "type": "person|organisation|country|group", "role": "<what they do in the event>", "aliases": ["<other names, titles, spellings seen>"], "posts": [<numbers>]}],\n'
        ' "titles": [{"title": "<a title or description used instead of a name, with its country or organisation>", "person": "<who it refers to, as written in the posts>", "posts": [<numbers>]}],\n'
        ' "off_topic": [<post numbers that are NOT about the event>],\n'
        ' "contradictions": ["<where posts disagree about what happened>"],\n'
        ' "needs_more": true|false, "needs_more_reason": "<why, or empty>",\n'
        ' "followup": ["<up to 4 short example sentences of posts you would want more of to settle what is unclear>"],\n'
        ' "anchors": ["<8 short sentences, each a different kind of post about THIS event as the posts show it, naming the concrete actors, places and actions>"]}\n'
        "Use needs_more=true when the sample is too thin, ambiguous or contradictory to say what the event is, or when it seems to hold several developments you cannot yet separate.")


def _valid_ground(o: dict) -> Optional[str]:
    if o.get("assessment") not in ("clear", "multiple_developments", "ambiguous", "insufficient"):
        return "assessment"
    if not isinstance(o.get("event_summary"), str) or not isinstance(o.get("anchors"), list) or len(o["anchors"]) < 3:
        return "summary or anchors"
    for k in ("developments", "actors", "titles", "off_topic", "contradictions", "followup"):
        if not isinstance(o.get(k, []), list):
            return k
    return None


def ground_context(scenario: str, items: list[dict]) -> dict:
    """Gemma reads the sample (items: [{post_uid, platform, day, text}]) and returns the grounded context; post numbers are mapped back to post ids."""
    per = 330 if len(items) <= 100 else 240
    user = "Topic typed by the user: " + scenario + "\n\nSample posts:\n" + "\n".join(f"[{i + 1}] ({it['platform']}, {it['day']}) {it['text'][:per]}" for i, it in enumerate(items))
    o = _chat(_ground_prompt(), user, 3800, _valid_ground)
    ids = [it["post_uid"] for it in items]

    def ev(nums):
        return [ids[int(n) - 1] for n in (nums or []) if isinstance(n, (int, float, str)) and str(n).isdigit() and 1 <= int(n) <= len(ids)]

    for d in o.get("developments", []):
        d["post_ids"] = ev(d.get("posts"))
    for a in o.get("actors", []):
        a["post_ids"] = ev(a.get("posts"))
    for t in o.get("titles", []):
        t["post_ids"] = ev(t.get("posts"))
    o["off_topic_ids"] = ev(o.get("off_topic"))
    return o


def build_grounding(cur, scenario: str, day_from: str, day_to: str, platforms: Optional[list[str]], texts_of, log) -> dict:
    """Steps 1-5: hint, ranking, diverse sample, grounded context, adaptive rounds."""
    hints = hint_sentences(scenario)
    anchors = embed_texts([scenario] + hints)
    ranked = semantic_rank(anchors, day_from, day_to, platforms)
    sample_ids = diverse_sample(ranked, GROUND_N, day_from, day_to)
    meta = ranked.set_index("post_uid")
    rounds = []
    ctx = None
    for rnd in range(GROUND_ROUNDS + 1):
        tx = texts_of(sample_ids)
        items = [{"post_uid": u, "platform": tx[u].platform, "day": str(meta.at[u, "day"]) if u in meta.index else "", "text": _norm(tx[u].text)} for u in sample_ids if u in tx]
        ctx = ground_context(scenario, items)
        rounds.append({"round": rnd, "sample": len(items), "assessment": ctx["assessment"], "needs_more": bool(ctx.get("needs_more")), "reason": ctx.get("needs_more_reason", "")})
        log(f"grounding round {rnd}: {len(items)} posts -> {ctx['assessment']}, needs_more={ctx.get('needs_more')}")
        # keep retrieving while the sample cannot establish the event (insufficient / ambiguous); a context that is clear enough but holds several developments or
        # contradictions gets ONE more round to separate them, after that the developments and contradictions are reported as they are, not chased forever
        hard = ctx["assessment"] in ("insufficient", "ambiguous")
        soft = rnd == 0 and (bool(ctx.get("needs_more")) or bool(ctx.get("contradictions")))
        thin = hard or soft
        if not thin or rnd == GROUND_ROUNDS or len(sample_ids) >= GROUND_MAX:
            break
        # adaptive step: what Gemma asked for and the developments it found become new retrieval anchors; a diverse batch of new posts is added
        asks = [str(x) for x in ctx.get("followup", [])][:4] + [f"{d.get('title', '')}: {d.get('summary', '')}" for d in ctx.get("developments", [])][:4]
        if not asks:
            break
        more = semantic_rank(embed_texts(asks), day_from, day_to, platforms, exclude=set(sample_ids))
        meta = pd.concat([meta, more.set_index("post_uid")[["platform", "day", "sem"]]]).loc[lambda d: ~d.index.duplicated()]
        add = diverse_sample(more, min(GROUND_STEP, GROUND_MAX - len(sample_ids)), day_from, day_to)
        if not add:
            break
        sample_ids = sample_ids + add
    ctx["sample_post_ids"] = sample_ids
    ctx["rounds"] = rounds
    ctx["hints"] = hints
    return ctx


# ------------------------------------------------------------------------------------------------------------------ validation (Gemma)
def verify_system(ctx: dict, scenario: str, exemplars: list[str]) -> str:
    dev = "\n".join(f"- {d.get('title', '')}: {d.get('summary', '')} ({d.get('time', '')})" for d in ctx.get("developments", [])[:6]) or "- (none separated)"
    off = "; ".join(ctx.get("_off_topic_texts", [])[:5])
    amb = ""
    if ctx["assessment"] != "clear":
        amb = f"NOTE: the event is only {ctx['assessment']} in the data; judge a post against ANY of the developments below and do not assume more than they say.\n"
    return (
        f"You validate social-media posts for the event \"{scenario}\" as the real posts of this data establish it.\n"
        f"EVENT (from the data): {ctx.get('event_summary', '')}\nDEVELOPMENTS:\n{dev}\n{amb}"
        f"POSTS THAT ARE NOT ABOUT IT (examples seen): {off or 'none seen'}\n"
        f"EXAMPLES OF POSTS THAT ARE ABOUT IT: {' | '.join(exemplars[:3])}\n\n"
        "For each numbered post you get the people whose names (or a title the event context resolves) were found in it. Decide:\n"
        "1. relevant: true only if the post is about the event itself: its events, actions, decisions, statements, threats, negotiations, military or political roles, "
        "consequences. A post can be relevant without the event's words. False if it merely shares words or names but is about something else.\n"
        "2. For each listed person, involvement:\n"
        "   \"direct\"   = the post says what THIS person did, plans, decided, ordered, led, carried out, threatened, negotiated or said about the event, or reports that "
        "(a headline or live report of their action counts);\n"
        "   \"indirect\" = the person is connected only through someone or something else: their government's, agency's or organisation's action, a subordinate's act, an "
        "action or statement of others aimed at them, or a negotiation or response they are party to but where the post does not show them acting;\n"
        "   \"mention\"  = the name is there but the post does not show what the person does in the event: a tag or hashtag, a list of names, a live-updates title that only "
        "lists names, background, a comparison, an opinion about them not tied to an event action;\n"
        "   \"unrelated\"= the name appears in a different context from the event, or a title does not refer to this person here.\n"
        "   Examples (any topic): \"Governor X declares emergency after the flood\" = direct; \"The governor's office confirms evacuation orders\" = indirect; "
        "\"Flood update | X | Y | Z live\" = mention; \"X to visit the same city next week for another reason\" = unrelated.\n"
        "   Never answer direct only because the name is in the post. role = 4-8 words on what they do in the event (empty for mention or unrelated). "
        "evidence = a phrase copied EXACTLY from the post that shows the involvement (required for direct and indirect). development = the number of the development "
        "above the post is about (0 if none).\n"
        'Reply with ONE JSON object: {"answers": [{"id": <post number>, "relevant": true|false, "development": <int>, "reason": "<max 15 words>", "people": [{"name": "<as listed>", '
        '"involvement": "direct|indirect|mention|unrelated", "role": "...", "evidence": "..."}]}, ...]} with exactly one answer per post, in order.')


def verify_user(items: list[dict]) -> str:
    def names(x):
        return ", ".join(f"{n}" + (f" (found as \"{via}\")" if via else "") for n, via in zip(x["names"], x["via"])) or "none"
    return "\n\n".join(f"Post {i + 1} (people found: {names(x)}):\n{x['text'][:1200]}" for i, x in enumerate(items))


def _valid_verify(o: dict, n: int) -> Optional[str]:
    a = o.get("answers")
    if not isinstance(a, list) or len(a) != n:
        return f"answers must be a list of {n}"
    for i, x in enumerate(a):
        if not isinstance(x, dict) or x.get("id") != i + 1 or not isinstance(x.get("relevant"), bool) or not isinstance(x.get("people", []), list):
            return f"answer {i + 1}"
        for pr in x.get("people", []):
            if pr.get("involvement") not in W_INV:
                return f"answer {i + 1}: involvement"
    return None


def verify_posts(system_prompt: str, items: list[dict]) -> list[dict]:
    chunks = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]

    def one(ch):
        r = G.chat_json(system_prompt, verify_user(ch), max_tokens=min(3800, 600 + 280 * len(ch)), validate=lambda o: _valid_verify(o, len(ch)))
        res = []
        for j, it in enumerate(ch):
            a = r["obj"]["answers"][j] if r["ok"] else None
            res.append({"post_uid": it["post_uid"], "ok": r["ok"], "relevant": (a or {}).get("relevant"), "reason": (a or {}).get("reason", ""),
                        "development": (a or {}).get("development", 0), "people": (a or {}).get("people", []), "error": r["error"]})
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


def _ckey(*parts) -> str:
    return hashlib.sha1(json.dumps([CACHE_VERSION, *parts], default=str).encode()).hexdigest()[:16]


def run(cur, scenario: str, day_from: str, day_to: str, platforms: Optional[list[str]], etype: str, esub: str, *, refresh: bool = False, log=lambda m: None) -> dict:
    """cur = a read-only DuckDB cursor on analytics.duckdb (post_facts, geo.entity_dim attached as in api_server)."""
    t0 = time.time(); timing = {}
    CACHE.mkdir(parents=True, exist_ok=True)
    rkey = _ckey(scenario.strip().lower(), day_from, day_to, sorted(platforms or []), etype, esub)
    rf = CACHE / f"result_{rkey}.json"
    if rf.exists() and not refresh:
        return json.loads(rf.read_text()) | {"cached": True}

    def texts_of(uids):
        uids = list(dict.fromkeys(uids))
        if not uids:
            return {}
        cur.register("u_tmp", pd.DataFrame({"post_uid": uids}))
        d = cur.execute("SELECT p.post_uid, p.platform, p.posted_at, p.author_name, p.url, p.views, left(coalesce(p.text, ''), 3000) AS text "
                        "FROM analytics.post_facts p JOIN u_tmp ON u_tmp.post_uid = p.post_uid").fetchdf()
        cur.unregister("u_tmp")
        return {r.post_uid: r for r in d.itertuples()}

    # 1-5. grounded event context (shared by every entity type of the same search: cached on its own)
    gkey = _ckey("ground", scenario.strip().lower(), day_from, day_to, sorted(platforms or []))
    gf = CACHE / f"ground_{gkey}.json"
    if gf.exists() and not refresh:
        ctx = json.loads(gf.read_text())
    else:
        ctx = build_grounding(cur, scenario, day_from, day_to, platforms, texts_of, log)
        gf.write_text(json.dumps(ctx, ensure_ascii=False, default=str))
    timing["grounding_s"] = round(time.time() - t0, 1)
    gtx = texts_of(ctx["sample_post_ids"])
    ctx["_off_topic_texts"] = [_norm(gtx[u].text)[:140] for u in ctx.get("off_topic_ids", []) if u in gtx]
    exemplar_ids = []
    for d in ctx.get("developments", []):
        exemplar_ids += d.get("post_ids", [])[:1]
    exemplars = [_norm(gtx[u].text)[:220] for u in dict.fromkeys(exemplar_ids) if u in gtx][:3]

    # 6. retrieval by the grounded context (anchors: the event as the posts show it, the developments, the typed words)
    # the typed-words hint sentences stay in the anchor set (they cover kinds of posts the sample may not have shown, e.g. economic effects); they only retrieve, the
    # grounded context alone decides relevance in the validation
    anchor_txt = [scenario] + [str(a) for a in ctx.get("anchors", [])][:8] + [f"{d.get('title', '')}: {d.get('summary', '')}" for d in ctx.get("developments", [])][:6] + [str(h) for h in ctx.get("hints", [])][:8]
    ranked = semantic_rank(embed_texts(anchor_txt), day_from, day_to, platforms)
    timing["retrieval_s"] = round(time.time() - t0, 1)

    where, params = ["entity_type = ?"], [etype]
    if esub:
        where.append("entity_subtype = ?"); params.append(esub)
    cand = cur.execute(f"SELECT entity_uid, canonical_name, entity_type, entity_subtype, distinct_posts FROM geo.entity_dim WHERE {' AND '.join(where)} "
                       "ORDER BY distinct_posts DESC LIMIT 300", params).fetchdf()
    cl = sorted(EP.clean_candidates(cand), key=lambda x: -x[3])[:120]
    # actors the context found (people only when the chosen type is PERSON) join the candidates, with the aliases the posts showed
    known = {re.sub(r"[^a-z0-9]", "", c[1].lower()): c for c in cl}
    if etype == "PERSON":
        for a in ctx.get("actors", []):
            if a.get("type") != "person" or not a.get("name"):
                continue
            nm = str(a["name"]).strip()
            key = re.sub(r"[^a-z0-9]", "", nm.lower())
            tgt = known.get(key) or next((c for k, c in known.items() if key and (k.endswith(key) or key.endswith(k)) and min(len(k), len(key)) >= 5), None)
            als = [x.lower() for x in [nm] + [str(z) for z in a.get("aliases", [])] if isinstance(x, str) and len(x) >= 4 and not EP.GENERIC_PERSON.match(x.strip())]
            if tgt is not None:
                tgt[4].extend(als)
            elif len(nm) >= 4 and not EP.GENERIC_PERSON.match(nm):
                new = ["ctx:" + key, nm, "political_leader", 0, list(dict.fromkeys(als or [nm.lower()]))]
                cl.append(new); known[key] = new
    comp = EP.compile_patterns(cl)
    title_rx = []                                         # titles the context resolves to a person ("the US president" -> Trump), only with evidence in the sample
    if etype == "PERSON":
        for t in ctx.get("titles", []):
            ttl, who = str(t.get("title", "")).strip().lower(), re.sub(r"[^a-z0-9]", "", str(t.get("person", "")).lower())
            tgt = known.get(who) or next((c for k, c in known.items() if who and (k.endswith(who) or who.endswith(k)) and min(len(k), len(who)) >= 5), None)
            if tgt is not None and len(ttl) >= 8 and t.get("post_ids"):
                title_rx.append((tgt[0], tgt[1], re.compile(r"(?<![^\W_])" + re.escape(ttl) + r"(?![^\W_])"), ttl))
    tx = texts_of(ranked.post_uid.tolist())
    named: dict[str, list] = {}
    info: dict[str, dict] = {}
    n_named = 0
    for r in ranked.itertuples():
        t = tx.get(r.post_uid)
        if t is None:
            continue
        low = _norm(t.text).lower()
        hits = {u: (n, "") for u, n in EP.find_people(low, comp)}
        for u, n, rx, ttl in title_rx:
            if u not in hits and rx.search(low):
                hits[u] = (n, ttl)
        if hits:
            n_named += 1
            info[r.post_uid] = {"post_uid": r.post_uid, "names": [v[0] for v in hits.values()], "via": [v[1] for v in hits.values()], "uids": list(hits), "text": _norm(t.text), "sem": float(r.sem)}
            for u in hits:
                named.setdefault(u, []).append(r.post_uid)
    top_people = sorted(named, key=lambda u: -len(named[u]))[:MAX_PEOPLE]
    chosen: dict[str, None] = {}
    for u in top_people:                                  # balanced shortlist: each well-covered person gets their PER_PERSON best posts validated
        for uid in named[u][:PER_PERSON]:
            chosen.setdefault(uid)
    short = [info[u] for u in sorted(chosen, key=lambda u: -info[u]["sem"])][:SHORTLIST]
    n_cand = {u: len(v) for u, v in named.items()}
    timing["shortlist_s"] = round(time.time() - t0, 1)

    # 7. validation against the grounded context
    ver = verify_posts(verify_system(ctx, scenario, exemplars), short)
    timing["verify_s"] = round(time.time() - t0, 1)
    vmap = {v["post_uid"]: v for v in ver}
    sem = _sem_norm(pd.Series([s["sem"] for s in short])) if short else pd.Series([], dtype=float)

    # 8. ranking by verified involvement
    people: dict[str, dict] = {}
    posts_out = []
    for s, sn in zip(short, sem):
        v = vmap.get(s["post_uid"])
        if not v or not v["ok"]:
            continue
        t = tx[s["post_uid"]]
        rel = bool(v["relevant"])
        by_name = {str(p.get("name", "")).lower(): p for p in v["people"]}
        for uid, name, via in zip(s["uids"], s["names"], s["via"]):
            p = by_name.get(name.lower()) or {"involvement": "unrelated", "role": "", "evidence": ""}
            inv = p.get("involvement", "unrelated") if rel else "unrelated"
            ev = (p.get("evidence") or "").strip()
            ev_ok = bool(ev) and re.sub(r"\s+", " ", ev).lower() in s["text"].lower()
            if inv in ("direct", "indirect") and not ev_ok:
                inv = "mention"                           # every involvement call needs evidence found in the post
            score = (0.4 * float(sn) + 0.2 + 0.4 * W_INV[inv]) if rel else 0.0
            rec = {"post_uid": s["post_uid"], "name": name, "via_title": via, "involvement": inv, "role": p.get("role", ""), "evidence": ev, "evidence_ok": ev_ok, "relevant": rel,
                   "development": v.get("development", 0), "reason": v.get("reason", ""), "score": round(score, 4), "sem": round(s["sem"], 4), "platform": t.platform,
                   "posted_at": str(t.posted_at), "author_name": t.author_name, "url": t.url, "views": None if pd.isna(t.views) else int(t.views), "text": s["text"][:420]}
            posts_out.append(rec)
            o = people.setdefault(uid, {"entity_uid": uid, "name": name, "candidates": n_cand.get(uid, 0), "checked": 0, "direct": 0, "indirect": 0, "mention": 0, "unrelated": 0, "roles": {}, "ws": []})
            o[inv] += 1; o["checked"] += 1
            o["ws"].append((score, W_INV[inv]))
            if inv in ("direct", "indirect") and p.get("role"):
                o["roles"][p["role"]] = o["roles"].get(p["role"], 0) + 1
    for o in people.values():          # strength = involvement shown by the person's 5 best posts; mention counts and views play no part
        top = sorted(o.pop("ws"), key=lambda x: -x[0])[:PER_PERSON]
        o["strength"] = round(sum(w for _, w in top) / max(len(top), 1), 2)
        o["top_role"] = max(o["roles"], key=o["roles"].get) if o["roles"] else ""
        o.pop("roles")
    plist = sorted(people.values(), key=lambda o: (-o["strength"], -(o["direct"] + 0.5 * o["indirect"])))
    keep = ("assessment", "event_summary", "developments", "actors", "titles", "contradictions", "needs_more", "needs_more_reason", "rounds", "sample_post_ids", "off_topic_ids")
    gnd = {k: ctx.get(k) for k in keep}
    gnd["sample_platforms"] = {k: int(v) for k, v in pd.Series([gtx[u].platform for u in ctx["sample_post_ids"] if u in gtx]).value_counts().items()}
    gnd["sample_size"] = len(ctx["sample_post_ids"])
    out = {"scenario": scenario, "from": day_from, "to": day_to, "entity_type": etype, "entity_subtype": esub, "grounding": gnd,
           "stats": {"semantic_candidates": int(len(ranked)), "naming_a_person": n_named, "verified": int(len(ver)), "verified_ok": int(sum(1 for v in ver if v["ok"])),
                     "relevant": int(sum(1 for v in ver if v["relevant"])), "people_candidates": len(cl)},
           "people": plist, "posts": posts_out, "timing": timing, "total_s": round(time.time() - t0, 1), "cached": False,
           "method": "event read from a diverse sample of the data's own posts, retrieval by meaning, Gemma validation of every shortlisted post; mention counts and views are not used"}
    rf.write_text(json.dumps(out, ensure_ascii=False, default=str))
    return out
