"""People / entity name clean-up and whole-word matching shared by the Explore "who is involved" list and the scenario search.

The entity extract stores titles as if they were people ("President", "Foreign Minister"), links, nationalities, plurals, bare first names and the
same person under several spellings (Xijinping / Xi Jinping / Obama / Barack Obama). `clean_candidates` removes or merges them; `find_people` names
the candidates a text mentions (whole word, case-insensitive). Heuristic lists, stated limits: see collaboration/04_CURRENT_STATE.md.
"""
from __future__ import annotations

import re

# titles that the entity extract stores as if they were people ("President", "Prime Minister"): never a "who"
GENERIC_PERSON = re.compile(r"^(the )?(vice[ -])?(president|prime minister|minister|secretary|chief|general|governor|mayor|chancellor|king|queen|leader|"
                            r"commander|senator|spokesman|spokesperson|officials?|officer|premier|speaker|judge|ambassador|director|chairman)s?$", re.I)


_FIRST_NAMES = {"donald", "joe", "benjamin", "narendra", "rahul", "hillary", "barack", "bill", "george", "vladimir", "volodymyr", "emmanuel", "boris", "angela",
                "justin", "keir", "scott", "pete", "marco", "mark", "sergey", "recep", "ali", "mohammed", "mohammad", "ahmed", "abdul", "imran", "shehbaz"}
_DEMONYM = re.compile(r"(ian|ish|ese|eli|ians|ans)$", re.I)
NICKNAMES = {"bibi": "benjamin netanyahu", "mbs": "mohammed bin salman"}
_JUNK = {"https", "http", "www", "rt", "potus", "rep", "mps", "pm", "prime", "of state", "state", "foreign", "defense", "defence", "home", "representative", "deputy", "vice", "national"}


def clean_candidates(cand):
    """Entity extract noise removed before anything is counted: titles ("Foreign Minister"), links, nationalities ("Israeli"), plurals ("Trumps"), bare first names,
    and the same person under several spellings (Xijinping / Xi Jinping, Obama / Barack Obama) merged under the longest name. Returns [(uid, display, subtype, corpus, [patterns])]."""
    rows = []
    for r in cand.itertuples():
        name = str(r.canonical_name).strip()
        low = name.lower()
        toks = low.split()
        if len(name) < 3 or low in _JUNK or GENERIC_PERSON.match(name) or re.search(r"\b(minister|president|secretary|commander|spokes\w+|officials?|ambassador|chief|governor|senator|mps|representative|deputy|members?|chairman|premier|advis[eo]rs?|affairs|lawmakers?|authorit(?:y|ies)|leaders?|council|government|supreme|vice|services?)\b", low):
            continue
        if len(toks) == 1 and (low in _FIRST_NAMES or _DEMONYM.search(low)):
            continue
        rows.append([r.entity_uid, name, r.entity_subtype, int(r.distinct_posts or 0), [low]])
    names = {r[1].lower(): r for r in rows}
    for nick, full in NICKNAMES.items():                                   # nicknames the entity layer holds as separate people
        if nick in names and full in names and nick != full:
            names[full][4].append(nick); names[full][3] += names[nick][3]
            rows = [r for r in rows if r[1].lower() != nick]; names.pop(nick)
    keep = {}
    for r in sorted(rows, key=lambda x: -len(x[1].split())):
        low = r[1].lower(); toks = low.split()
        if low.endswith("s") and low[:-1] in names:                       # plural / possessive of another candidate
            continue
        collapsed = re.sub(r"[^a-z0-9]", "", low)
        tgt = next((k for k, v in keep.items() if re.sub(r"[^a-z0-9]", "", k) == collapsed), None)
        if tgt is None and len(toks) == 1:                                 # a single token is merged into a longer name that starts or ends with it
            # ... but only when that longer name is a real share of the single token's mentions: "Trump" (14,000 posts) is the president, not "Trump Jr"
            tgt = next((k for k in keep if len(k.split()) > 1 and (k.split()[-1] == low or (k.split()[0] == low and keep[k][3] >= 0.4 * r[3]))), None)
        if tgt is None and len(toks) == 1 and len(low) >= 6:                # a handle written as one word (vladimirputin, pmmodi) goes to the person whose surname it ends with
            tgt = next((k for k in keep if len(k.split()[-1]) >= 4 and low != k.split()[-1] and low.endswith(k.split()[-1])), None)
        if tgt is not None:
            keep[tgt][3] += r[3]; keep[tgt][4].append(low)
        else:
            keep[low] = [r[0], r[1], r[2], r[3], list(dict.fromkeys(r[4]))]
    return list(keep.values())




def compile_patterns(cl):
    """[(uid, name, subtype, corpus, [patterns])] -> [(uid, name, regex)] (one whole-word alternation per person)."""
    out = []
    for uid, name, sub, corpus, pats in cl:
        rx = re.compile("|".join(r"(?<![^\W_])" + re.escape(x) + r"(?![^\W_])" for x in dict.fromkeys(pats)))
        out.append((uid, name, rx))
    return out


def find_people(text_lower: str, compiled) -> list[tuple[str, str]]:
    """[(uid, name)] of the candidates named in a lower-cased text."""
    return [(uid, name) for uid, name, rx in compiled if rx.search(text_lower)]
