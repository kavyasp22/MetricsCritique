"""Keyword query language for searching post text (read-only; builds a SQL condition, never runs it).

What the user can type
    flood                  every word form: flood, floods, flooding, flooded (+ a short list of closely related words)
    flood relief           both words must be present
    "flash flood"          the exact phrase
    flood OR landslide     either (AND binds tighter than OR)
    flood -dam             without this word or phrase
Words are matched as whole words (so "pla" does not match "plastics"), case-insensitively, in any script.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_QUERY_CHARS = 120
MAX_TERMS = 8

# Closely related words that mean the same event type. Deliberately short: a wrong synonym adds noise,
# a missing one only misses a few posts.
RELATED = {
    "flood": ["inundation", "deluge", "waterlogging", "flash flood"],
    "earthquake": ["quake", "tremor", "aftershock"],
    "wildfire": ["forest fire", "bushfire"],
    "protest": ["demonstration", "rally"],
    "strike": ["walkout"],
    "election": ["polls", "vote"],
    "cyclone": ["hurricane", "typhoon"],
    "hurricane": ["cyclone", "typhoon"],
    "typhoon": ["cyclone", "hurricane"],
    "drought": ["water shortage"],
    "outbreak": ["epidemic"],
    "shooting": ["gunfire"],
    "blast": ["explosion"],
    "explosion": ["blast"],
}

_TOKEN = re.compile(r'"([^"]+)"|(-?)([^\s"]+)')
_PLAIN = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")


class KeywordError(ValueError):
    pass


@dataclass
class Term:
    text: str
    phrase: bool = False
    patterns: list = field(default_factory=list)      # whole-word alternatives that all count as this term


@dataclass
class Query:
    raw: str
    alternatives: list = field(default_factory=list)  # list of AND-groups; any group may match
    excludes: list = field(default_factory=list)

    def words(self) -> list:
        """Every word or phrase to highlight in a result."""
        out, seen = [], set()
        for grp in self.alternatives:
            for t in grp:
                for p in t.patterns:
                    if p not in seen:
                        seen.add(p); out.append(p)
        return out


def word_forms(w: str) -> list:
    """Simple English inflections of a word, plus nothing for short or non-Latin words (those match exactly)."""
    w = w.lower()
    if len(w) < 4 or not _PLAIN.match(w):
        return [w]
    vowels = "aeiou"
    if w.endswith("e"):
        f = {w, w + "s", w + "d", w[:-1] + "ing"}
    elif w.endswith("y") and w[-2] not in vowels:
        f = {w, w[:-1] + "ies", w[:-1] + "ied", w + "ing"}
    else:
        f = {w, w + "s", w + "es", w + "ed", w + "ing"}
        # consonant-vowel-consonant endings double the last letter (stop -> stopped); "flood" (two vowels) does not
        if w[-1] not in vowels + "wxy" and w[-2] in vowels and w[-3] not in vowels:
            f |= {w + w[-1] + "ing", w + w[-1] + "ed"}
    return sorted(f)


def _term(text: str, phrase: bool) -> Term:
    t = text.strip().lower()
    if phrase or " " in t:
        return Term(t, True, [t])
    pats = word_forms(t)
    for rel in RELATED.get(t, []):
        pats += [rel] if " " in rel else word_forms(rel)
    return Term(t, False, sorted(set(pats)))


def parse(raw: str) -> Query:
    raw = (raw or "").strip()
    if not raw:
        raise KeywordError("type a word or phrase to search for")
    if len(raw) > MAX_QUERY_CHARS:
        raise KeywordError(f"the search is longer than {MAX_QUERY_CHARS} characters")
    q = Query(raw)
    cur: list = []
    n = 0
    for m in _TOKEN.finditer(raw):
        phrase, neg, word = m.group(1), m.group(2), m.group(3)
        if phrase is not None:
            term, negated = _term(phrase, True), False
        elif word.upper() == "OR" and not neg:
            if cur:
                q.alternatives.append(cur)
            cur = []
            continue
        else:
            word = word.strip("()")
            if not word:
                continue
            term, negated = _term(word, False), bool(neg)
        n += 1
        if n > MAX_TERMS:
            raise KeywordError(f"use at most {MAX_TERMS} words or phrases")
        (q.excludes if negated else cur).append(term)
    if cur:
        q.alternatives.append(cur)
    if not q.alternatives:
        raise KeywordError("add at least one word to search for (a minus sign only excludes)")
    return q


def _regex(patterns: list) -> str:
    alts = "|".join(re.sub(r"\\ ", r"\\s+", re.escape(p)) for p in patterns)
    # RE2 has no look-around: whole word = start or a non-letter/digit before, and one after
    return r"(^|[^\pL\pN])(" + alts + r")([^\pL\pN]|$)"


def to_sql(q: Query, col: str = "p.text"):
    """(sql condition, params) for DuckDB: `regexp_matches(lower(col), ?)` per term. col must be a trusted identifier."""
    params: list = []

    def cond(t: Term, neg=False):
        params.append(_regex(t.patterns))
        return f"{'NOT ' if neg else ''}regexp_matches(lower(coalesce({col}, '')), ?)"

    ors = []
    for grp in q.alternatives:
        ors.append("(" + " AND ".join(cond(t) for t in grp) + ")")
    sql = "(" + " OR ".join(ors) + ")"
    for t in q.excludes:
        sql += " AND " + cond(t, neg=True)
    return sql, params
