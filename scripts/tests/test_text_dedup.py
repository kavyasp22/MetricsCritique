"""Exact-copy detection: what merges and what must never merge."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.text_dedup import MIN_CHARS, dup_key, normalise  # noqa: E402

A, B = "author_a", "author_b"
MSG = "Public Health Policy and Action | 6 week online certificate course"


def test_case_whitespace_and_urls_do_not_matter():
    assert dup_key(A, MSG) == dup_key(A, "  " + MSG.upper() + "   https://example.org/x?y=1 ")
    assert dup_key(A, MSG + "\n\n") == dup_key(A, MSG)


def test_same_text_different_author_is_not_a_copy():
    assert dup_key(A, MSG) != dup_key(B, MSG)


def test_a_changed_word_or_number_is_not_merged():
    assert dup_key(A, MSG) != dup_key(A, MSG.replace("6 week", "8 week"))
    assert dup_key(A, MSG) != dup_key(A, MSG.replace("Public", "Private"))


def test_short_text_or_unknown_author_is_never_grouped():
    assert dup_key(A, "ok") is None
    assert dup_key(A, "x" * (MIN_CHARS - 1)) is None
    assert dup_key(A, "x" * MIN_CHARS) is not None
    assert dup_key(None, MSG) is None and dup_key("", MSG) is None
    assert dup_key(A, None) is None and dup_key(A, "   ") is None


def test_snapchat_counter_caption_is_ignored_but_the_description_is_kept():
    a = "3 likes, 2 comments, and 200 shares. A man rides a bicycle and is startled by a dog and falls into a pond. | Spotlight by @abc from October 15, 2026"
    b = "17 likes and 205 shares. A man rides a bicycle and is startled by a dog and falls into a pond. | Spotlight by @abc from October 15, 2026"
    c = "3 likes, 2 comments, and 200 shares. A woman rides a horse and is startled by a cat and falls into a pond."
    assert dup_key(A, a) == dup_key(A, b)
    assert dup_key(A, a) != dup_key(A, c)
    assert "man rides a bicycle" in normalise(a) and "likes" not in normalise(a)


def test_hindi_counter_caption():
    a = "255 लाइक्स, 9 कमेंट्स, और 127 बार शेयर किया गया. A soldier performs a flag ceremony in a remote mountainous location."
    b = "300 लाइक्स और 140 बार शेयर किया गया. A soldier performs a flag ceremony in a remote mountainous location."
    assert dup_key(A, a) == dup_key(A, b)
