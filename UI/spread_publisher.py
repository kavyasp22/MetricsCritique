"""Cross-Platform Spread: the 'same publisher' label (read-only, computed from the stored tables, nothing is written).

A story, link or event is `same` when EVERY account behind it is one publisher, i.e. the same organisation posting its own content to several
platforms (Aaj Tak's Telegram + WhatsApp, BBC News + BBC Chinese, an embassy's X + Instagram). That is real distribution but not a story spreading
to others, so the card hides those rows by default behind a toggle (never deletes them).

Accounts are matched conservatively (a false match would hide a genuinely independent pickup):
  1. same normalised name (lower case, letters and digits only, generic words such as "official" / "spokesperson" dropped, non-Latin text dropped)
  2. same set of name words in any order ("mfa_china" = "CHINA MFA Spokesperson")
  3. one name contains the other, when the shorter is at least 7 characters and is not only generic words ("navalinstitute" in "usnavalinstitute";
     "World News" is not "BBC World News")
  4. both fall in one brand group of UI/publisher_aliases.json (curated keywords: "contains", or "=name" for an exact short name)
Names sharing only a short or common word do NOT match ("MFA Russia" is not "CHINA MFA Spokesperson").
Mixed items (some independent accounts) are never `same`.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ALIASES = Path(__file__).resolve().parent / "publisher_aliases.json"
GENERIC = {"official", "spokesperson", "spokespersons", "channel", "page", "the", "of", "and", "tv", "ltd", "inc", "group", "media"}
MIN_CONTAIN = 7
WEAK = {"news", "world", "breaking", "daily", "latest", "live", "today", "times", "post", "report", "reports", "global", "international",
        "update", "updates", "online", "network", "new"}               # words that alone do not identify a publisher ("World News" is not "BBC World News")

TIERS = (("stories", "cross_platform_posts", "story_id"),
         ("links", "cross_platform_url_posts", "link_id"),
         ("events", "cross_platform_semantic_posts", "event_id"))


def _groups() -> dict[str, list[str]]:
    """Brand groups; keywords go through the same clean-up as account names so "Maritime Institute of Malaysia" matches its keyword."""
    try:
        raw = json.loads(ALIASES.read_text(encoding="utf-8"))["groups"]
    except (OSError, ValueError, KeyError):
        return {}
    out = {}
    for k, v in raw.items():
        kws = []
        for x in v:
            exact = x.startswith("=")                                 # "=aspi": the whole normalised name must equal it
            c = normalise(x[1:] if exact else x)[0]
            if c:
                kws.append(("=" + c) if exact else c)
        out[k] = kws
    return out


def normalise(name) -> tuple[str, frozenset]:
    """(compact name, set of name words) after the clean-up above. ('', empty) when nothing usable is left."""
    if name is None:
        return "", frozenset()
    s = unicodedata.normalize("NFKD", str(name)).lower()
    s = "".join(ch for ch in s if ch.isascii())                       # drops CJK / emoji / accents' marks
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w not in GENERIC]
    return "".join(words), frozenset(words)


def _brand(compact: str, groups: dict) -> str | None:
    for g, kws in groups.items():
        if any((compact == k[1:]) if k.startswith("=") else (k in compact) for k in kws):
            return g
    return None


def same_account(a, b, groups: dict | None = None) -> bool:
    """True when two account names read as one publisher (see the module note). a, b are (compact, words) from `normalise`."""
    (ca, wa), (cb, wb) = a, b
    if not ca or not cb:
        return False
    if ca == cb or (wa and wa == wb):
        return True
    (short, sw), long_ = ((ca, wa), cb) if len(ca) <= len(cb) else ((cb, wb), ca)
    if len(short) >= MIN_CONTAIN and short in long_ and (sw - WEAK):
        return True
    g = groups if groups is not None else _groups()
    ba, bb = _brand(ca, g), _brand(cb, g)
    return bool(ba and ba == bb)


def classify(accounts: list, groups: dict | None = None) -> dict:
    """accounts: list of (platform, name) for ONE story / link / event. -> {same, publishers, label}."""
    groups = groups if groups is not None else _groups()
    uniq = {}
    for plat, name in accounts:
        key = (plat, str(name))
        uniq.setdefault(key, normalise(name))
    items = list(uniq.items())
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if find(i) != find(j) and same_account(items[i][1], items[j][1], groups):
                parent[find(j)] = find(i)
    comps = {}
    for i, ((plat, name), _) in enumerate(items):
        comps.setdefault(find(i), []).append((plat, name))
    n_pub = len(comps)
    unknown = any(not nm[0] for _, nm in items)                         # an account with no usable name cannot be shown to be the same
    one = n_pub == 1 and not unknown and len(items) >= 2
    label = None
    if one:
        brand = next((g for g in (_brand(nm[0], groups) for _, nm in items) if g), None)
        label = brand or min((str(name) for (_, name), _ in items), key=len)      # a curated brand name, else the shortest account name
    return {"same": bool(one), "publishers": n_pub, "label": label}


def explore_spread_publishers(main_cur) -> dict:
    """{tier: {id: {same, publishers, label, undated}}, counts: {tier: {total, same, undated}}} for the three tiers."""
    groups = _groups()
    out, counts = {}, {}
    for tier, table, key in TIERS:
        rows = main_cur.execute(f"""
            SELECT cp.{key} AS id, p.platform, coalesce(p.author_name, p.author_uid) AS acct, (p.posted_at IS NULL)::INT AS undated
            FROM (SELECT DISTINCT {key}, post_uid FROM metrics.{table}) cp
            JOIN analytics.post_facts p ON p.post_uid = cp.post_uid""").fetchall()
        by, und = {}, {}
        for i, plat, acct, u in rows:
            by.setdefault(i, []).append((plat, acct))
            und[i] = und.get(i, 0) + int(u or 0)
        # `undated` = members with no post date (all WhatsApp posts): they can never be the earliest and drop out of the spread hours,
        # so the card shows a dash for the spread of such an item instead of a misleading 0.0 h
        res = {i: {**classify(v, groups), "undated": und.get(i, 0)} for i, v in by.items()}
        out[tier] = res
        counts[tier] = {"total": len(res), "same": sum(1 for v in res.values() if v["same"]), "undated": sum(1 for v in res.values() if v["undated"])}
    out["counts"] = counts
    return out
