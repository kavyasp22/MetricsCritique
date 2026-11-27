"""Rule-based sarcasm CUE detector for comment text. A flag, never a relabel: it does not change any stored sentiment.

Why it exists: a comment such as "wow great job, another broken promise 🙄" has positive words, and the model can read it as positive.
Only POSITIVE-labelled comments can be hurt this way, so the flag is meant to be read next to the positive share.

Tiers (provisional; precision must be measured on a judged sample before the numbers are trusted):
  likely    a sarcastic idiom ("yeah right", "what a great ..."), or praise + a strong irony emoji (eye-roll, clown, upside-down, smirk)
  possible  praise + a laughing emoji (laughter is usually sincere, so this tier is weak evidence)
  none
Works on the text the model scored (`text_used`): on YouTube that is the English translation, on the other platforms the original.
"""
from __future__ import annotations

import re

STRONG_IRONY = "🙄🤡🙃😏🤦"            # eye-roll, clown, upside-down, smirk, facepalm
LAUGH = "😂🤣💀"                       # laughing / "dead": mostly sincere, weak evidence
_PRAISE = (r"amazing|great|genius|brilliant|wonderful|perfect|fantastic|excellent|awesome|best|bravo|congrat\w*|well done|good job|nice job|"
           r"love it|so smart|wow|zabardast|shandar|kamaal|mashallah|wah\b|waah|kya baat")
# Judged out (2026-11-27, 36 flagged positive-labelled comments read): "what a great/amazing/wonderful ...", "wah kya" and "as if" were
# overwhelmingly SINCERE praise (every LinkedIn hit; precision about 25% with them), so they are not cues. Kept: idioms that read as mockery.
_IDIOMS = [r"\byeah,? right\b", r"\bsure,? buddy\b", r"\bnice try\b", r"\bthanks for nothing\b", r"\boh,? really\b",
           r"\bhow (?:original|surprising)\b", r"\bgood luck with that\b", r"\bwhat a (?:joke|surprise|shock)\b",
           r"\bso (?:brave|smart|clever)\b.{0,20}\b(?:not|really)\b", r"\bsure it (?:is|was|will)\b", r"\bbest comeback\b"]
PRAISE_RE = re.compile(_PRAISE, re.I)
IDIOM_RE = re.compile("|".join(_IDIOMS), re.I)


def sarcasm_cue(text) -> str:
    """'likely' | 'possible' | 'none'."""
    if not isinstance(text, str) or not text:
        return "none"
    if IDIOM_RE.search(text):
        return "likely"
    praise = bool(PRAISE_RE.search(text))
    if praise and any(c in text for c in STRONG_IRONY):
        return "likely"
    if praise and any(c in text for c in LAUGH):
        return "possible"
    return "none"


def run_self_checks() -> dict:
    r = {}
    r["idiom"] = sarcasm_cue("Yeah right, they will keep that promise") == "likely" and sarcasm_cue("nice try though") == "likely"
    r["praise_plus_eyeroll"] = sarcasm_cue("great job guys 🙄") == "likely"
    r["praise_plus_laugh_is_weak"] = sarcasm_cue("amazing work 😂") == "possible"
    r["plain_praise_not_flagged"] = sarcasm_cue("great work, thank you") == "none"
    r["emoji_alone_not_flagged"] = sarcasm_cue("🙄") == "none" and sarcasm_cue("😂😂") == "none"
    r["sincere_praise_idioms_not_flagged"] = all(sarcasm_cue(t) == "none" for t in ("What a great day!", "What an amazing event", "wah kya baat hai", "Prepare as if there is no one coming"))
    r["empty_and_none"] = sarcasm_cue("") == "none" and sarcasm_cue(None) == "none"
    return r


if __name__ == "__main__":
    c = run_self_checks()
    for k, v in c.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    raise SystemExit(0 if all(c.values()) else 1)
