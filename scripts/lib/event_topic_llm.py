"""Prompt, validator and helpers for Gemma A-code (event topic) labelling. Pure functions (no network, no DB).

The vocabulary (10 codes, 41 sub-codes) comes from scripts/lib/taxonomy.py, so the prompt can never drift from the dashboard's labels.
Two extra answers exist because the taxonomy has no home for them:
  none              the post is not about a state-level, inter-state or security matter (ad, joke, personal, fiction, game, history, local crime)
  conflict_unmapped armed conflict between or inside states (strikes, shelling, fighting, ceasefire) that none of the sub-codes covers
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from . import taxonomy as T
from .entity_routing import MAX_TEXT_CHARS, evidence_in_text

NONE = "none"
UNMAPPED = "conflict_unmapped"
PROMPT_VERSION = "v1"


@lru_cache(maxsize=1)
def codes() -> dict:
    """{sub-code: (display, description)} and {parent: display}."""
    a = T.build()["axes"]
    sub = {e["code"]: (e["display"], e["description"]) for e in a["theme_subcode"]["entries"].values()}
    par = {e["code"]: e["display"] for e in a["theme_code"]["entries"].values()}
    return {"sub": sub, "par": par}


def valid_answers() -> set[str]:
    return set(codes()["sub"]) | {NONE, UNMAPPED}


def system_prompt() -> str:
    c = codes()
    lines = []
    for p in sorted(c["par"]):
        lines.append(f"{p} {c['par'][p]}")
        for s in sorted(k for k in c["sub"] if k.split(".")[0] == p):
            d, desc = c["sub"][s]
            lines.append(f"  {s} {d}: {desc[:150]}")
    return (
        "You label one social-media post with the single best-fitting EVENT TOPIC from the list below. The topics describe relations between "
        "states and security matters. Answer in JSON only.\n\nTOPICS\n" + "\n".join(lines) +
        f"\n\nOTHER ANSWERS\n  {NONE}: the post is not about a state-level, inter-state or security matter. Use it for ads, jokes, personal chatter, "
        "fiction and games, history lessons, sport, ordinary local crime, company news, scam alerts and generic opinion without a concrete state-level event.\n"
        f"  {UNMAPPED}: armed conflict between or inside states (strikes, shelling, drones, fighting, ceasefire, casualties) that none of the topics above covers.\n\n"
        "RULES\n1. Pick the most specific sub-code (like 05.3), not the parent. 2. Judge what the post is about, not which words it contains. "
        "3. Mediation, a state backing a side in a conflict, or reaction to a conflict goes to 08.x only when that is the point of the post. "
        "4. Naval or shipping events go to 05.x; a state's weapons or forces go to 06.x; border and line-of-control events go to 07.x. "
        "5. Cyber incidents, ransomware victims, phishing, surveillance technology and AI governance go to 09.x. "
        "6. Do not stretch: when unsure whether the post fits any topic, answer none.\n\n"
        'Reply with ONE JSON object: {"subcode": "<code or none or conflict_unmapped>", "confidence": <0..1>, '
        '"evidence": "<a short phrase copied exactly from the post that shows the topic, or empty for none>"}'
    )


def user_prompt(text: str) -> str:
    return "Post:\n" + (text or "")[:MAX_TEXT_CHARS]


def validate(obj: dict) -> Optional[str]:
    s = obj.get("subcode")
    if s not in valid_answers():
        return f"subcode={s!r}"
    try:
        c = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return "confidence not a number"
    if not 0.0 <= c <= 1.0:
        return "confidence outside 0..1"
    return None


def parent_of(sub: str) -> Optional[str]:
    return sub.split(".")[0] if re.fullmatch(r"\d\d\.\d", sub or "") else None
