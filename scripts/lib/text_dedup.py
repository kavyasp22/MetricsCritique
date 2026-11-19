"""Exact-copy detection for posts: same author, same normalised text.

Normalisation removes only what does not change the message: case, runs of whitespace, URLs, and the counter caption that
Snapchat's generated text starts with ("3 likes, 2 comments, and 200 shares. ..." / the Hindi form) plus its trailing
"Spotlight by @name from <date>" tail. Digits and wording are kept, so two different messages are never merged.
Texts shorter than MIN_CHARS after normalisation are never grouped (a repeated "ok" or "Pinterest" is not a copy).
"""
from __future__ import annotations

import hashlib
import re

MIN_CHARS = 20
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_WS = re.compile(r"\s+")
# Snapchat: "17.0K likes, 145 comments, and 11.3K shares. ..." and "255 लाइक्स, 9 कमेंट्स, और 127 बार शेयर किया गया. ..."
_SNAP_HEAD = re.compile(
    r"^\s*(?:[\d.,]+[km]?\s*(?:likes?|comments?|shares?|लाइक्स?|कमेंट्स?|बार शेयर किया गया)[,. ]*(?:and|और)?\s*)+\.?\s*", re.I)
_SNAP_TAIL = re.compile(r"\|?\s*spotlight by @\S+ from [a-z]+ \d{1,2}, \d{4}\s*$", re.I)


def normalise(text) -> str:
    if not isinstance(text, str):
        return ""
    t = text.lower()
    t = _URL.sub(" ", t)
    t = _SNAP_HEAD.sub("", t)
    t = _SNAP_TAIL.sub("", t)
    return _WS.sub(" ", t).strip()


def dup_key(author_uid, text):
    """sha1 of author + normalised text, or None when the text is too short or the author is unknown."""
    n = normalise(text)
    if len(n) < MIN_CHARS or author_uid is None or str(author_uid) == "":
        return None
    return hashlib.sha1(f"{author_uid}|{n}".encode("utf-8")).hexdigest()
