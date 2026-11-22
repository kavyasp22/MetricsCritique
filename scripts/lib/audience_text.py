"""Text triage for comments and replies. Pure functions: no I/O, no model, no database.

Every extracted row gets exactly one `text_status`; nothing is dropped. Only `ok` rows are sent to the sentiment
model, the others keep their row with NULL sentiment and the reason in `text_status`.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_CHARS = 2000                      # same cap as the post pipeline

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_WS = re.compile(r"\s+")
_DELETED = re.compile(
    r"^\s*[\[\(]?\s*(?:this\s+comment\s+(?:has\s+been\s+)?)?(?:deleted|removed|comment\s+removed)\s*[\]\)]?\s*\.?\s*$",
    re.I,
)

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_DELETED = "deleted_marker"
STATUS_URL_ONLY = "unusable_url_only"
STATUS_SYMBOLS = "unusable_symbols"
NOT_SCORED = {STATUS_EMPTY, STATUS_DELETED, STATUS_URL_ONLY, STATUS_SYMBOLS}


def _is_emoji_like(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat in ("So", "Sk") or ord(ch) >= 0x1F000


def triage(text) -> tuple[str, list[str]]:
    """Return (text_status, flags) for one comment text.

    Statuses: ok | empty | deleted_marker | unusable_url_only | unusable_symbols.
    Flags (only on ok rows, informational, the row is still scored): emoji_only, very_short, truncated, has_url.
    """
    if text is None or (isinstance(text, float) and text != text):
        return STATUS_EMPTY, []
    t = str(text)
    if not t.strip():
        return STATUS_EMPTY, []
    if _DELETED.match(t):
        return STATUS_DELETED, []
    stripped = _URL.sub(" ", t)
    n_alnum = sum(1 for ch in stripped if ch.isalnum())
    has_emoji = any(_is_emoji_like(ch) for ch in stripped)
    if n_alnum == 0 and not has_emoji:
        return (STATUS_URL_ONLY if _URL.search(t) else STATUS_SYMBOLS), []
    flags: list[str] = []
    if n_alnum == 0 and has_emoji:
        flags.append("emoji_only")
    elif n_alnum < 3:
        flags.append("very_short")
    if len(t) > MAX_CHARS:
        flags.append("truncated")
    if _URL.search(t):
        flags.append("has_url")
    return STATUS_OK, flags


def pick_text(original, translated) -> tuple[str, str]:
    """Same convention as the post pipeline (COALESCE(translated, original)). Returns (text_used, text_source)."""
    tr = "" if translated is None or (isinstance(translated, float) and translated != translated) else str(translated)
    og = "" if original is None or (isinstance(original, float) and original != original) else str(original)
    if tr.strip():
        return tr, "translated"
    if og.strip():
        return og, "original"
    return "", "none"


def dup_key(text) -> str:
    """Normalised text used to find repeated comments within one video (case, spacing and URLs ignored)."""
    t = _URL.sub(" ", str(text or "")).lower()
    return _WS.sub(" ", t).strip()


def sha1_hex(*parts) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


_MENTION = re.compile(r"@[\w.]+", re.U)


def mention_only(text) -> bool:
    """True when the text is only @handles (Instagram 'tag a friend' comments): scored like any text, flagged for the summary."""
    if text is None or (isinstance(text, float) and text != text):
        return False
    t = _MENTION.sub(" ", _URL.sub(" ", str(text)))
    return bool(str(text).strip()) and not any(ch.isalnum() for ch in t) and not any(_is_emoji_like(ch) for ch in t) and "@" in str(text)
