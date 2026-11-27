"""Quality rules for comment sentiment: confidence tiers and skip reasons. Pure functions, no I/O, no model.

Emoji-only comments are kept (tagged `is_emoji_only`); only NO_TEXT is excluded.

Nothing here changes a stored score. It adds flags next to the stored probabilities so a roll-up can show
sentiment over (a) every scored row (the existing number), (b) rows minus NO_TEXT (headline), (c) confident rows only, and (t) rows with words, to show what emoji reactions add.

Thresholds are PROVISIONAL: the Phase 0 audit showed a hard 0.60 gate would drop 52-54% of Instagram / Snapchat
comments and ~80% of their neutral ones, so rows are tiered, never deleted.
"""
from __future__ import annotations

import re

import numpy as np

CONF_HIGH = 0.60        # max class probability at or above this = confident
CONF_LOW = 0.45         # below this = weak; between = uncertain
MIN_LETTERS = 2         # fewer alphabetic letters than this = no sentiment-bearing text

_LETTER = re.compile(r"[^\W\d_]")
_URL = re.compile(r"https?://\S+|www\.\S+")


def letter_count(text) -> int:
    return len(_LETTER.findall(_URL.sub(" ", text))) if isinstance(text, str) else 0


def is_emoji_only(text) -> bool:
    """Fewer than MIN_LETTERS letters but at least one non-ASCII symbol (emoji reaction). Scored normally; tagged, not dropped:
    the model reads them sensibly (hearts / thumbs-up positive, crying negative), so they are real audience reaction."""
    return isinstance(text, str) and bool(text.strip()) and letter_count(text) < MIN_LETTERS and any(not c.isascii() for c in text)


def skip_reason(text) -> str | None:
    """'NO_TEXT' when nothing sentiment-bearing is there (empty, punctuation, a single letter, URL only); else None.
    Emoji-only text is NOT skipped (see is_emoji_only). Never mapped to neutral."""
    if not isinstance(text, str) or not text.strip():
        return "NO_TEXT"
    if letter_count(text) >= MIN_LETTERS or is_emoji_only(text):
        return None
    return "NO_TEXT"


def max_prob(p_neg, p_neu, p_pos):
    return np.fmax.reduce([np.asarray(p_neg, float), np.asarray(p_neu, float), np.asarray(p_pos, float)])


def conf_tier(mx) -> np.ndarray:
    """'confident' >= CONF_HIGH, 'uncertain' [CONF_LOW, CONF_HIGH), 'weak' below, '' where mx is NaN."""
    mx = np.asarray(mx, float)
    out = np.where(mx >= CONF_HIGH, "confident", np.where(mx >= CONF_LOW, "uncertain", "weak")).astype(object)
    out[np.isnan(mx)] = ""
    return out


def run_self_checks() -> dict:
    r = {}
    r["skip_empty"] = skip_reason("  ") == "NO_TEXT" and skip_reason(None) == "NO_TEXT"
    r["emoji_only_kept_and_tagged"] = skip_reason("😂😂") is None and is_emoji_only("😂😂") and not is_emoji_only("wah 😂")
    r["skip_punct_only"] = skip_reason("?!...") == "NO_TEXT"
    r["skip_one_letter"] = skip_reason("k") == "NO_TEXT"
    r["keep_short_word"] = skip_reason("ok") is None and skip_reason("wah 😂") is None
    r["url_letters_ignored"] = skip_reason("https://example.com/abc") == "NO_TEXT"
    r["keep_urdu_script"] = skip_reason("بہت اچھا") is None
    t = conf_tier([0.9, 0.6, 0.5, 0.45, 0.4, np.nan])
    r["tiers"] = list(t) == ["confident", "confident", "uncertain", "uncertain", "weak", ""]
    r["max_prob"] = float(max_prob(0.2, 0.5, 0.3)) == 0.5
    return r


if __name__ == "__main__":
    c = run_self_checks()
    for k, v in c.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    raise SystemExit(0 if all(c.values()) else 1)
