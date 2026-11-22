"""Pure definitions for the YouTube audience-sentiment roll-ups (Phase 2). No I/O.

Scales: post tone_score, comment tone_score and reply tone_score are all P(positive) - P(negative) from the same
model, so all three live on [-1, 1] and can be compared directly. `net_sentiment` follows metrics v2
(`net_sentiment_score` = 100 * (positive share - negative share)), here over scored comments or replies.
"""
from __future__ import annotations

import math
from typing import Optional

MIN_SCORED = 5            # below this many scored rows an audience mean / gap is NULL (counts are kept)
GAP_FLAG = 0.30           # |gap| in tone units that marks a clear difference (twice the 0.15 class threshold in metrics v2)

STATUS_HAS = "has_comments"
STATUS_ZERO = "confirmed_zero"                 # YouTube reported 0 comments (never seen in this data so far)
STATUS_KNOWN = "not_collected_known"           # reported > 0 but we hold none
STATUS_UNKNOWN = "unknown_no_comment_data"     # nothing collected and no reported count: zero cannot be told from missing


def comment_data_status(n_rows: int, reported: Optional[float]) -> str:
    """Separate 'has data', 'confirmed zero', 'known but not collected' and 'cannot tell'."""
    if n_rows and n_rows > 0:
        return STATUS_HAS
    if reported is None or (isinstance(reported, float) and math.isnan(reported)):
        return STATUS_UNKNOWN
    return STATUS_ZERO if reported == 0 else STATUS_KNOWN


def net_sentiment(n_pos: int, n_neg: int, n_scored: int) -> Optional[float]:
    """100 * (positive share - negative share). NULL when nothing was scored (never 0)."""
    if not n_scored:
        return None
    return 100.0 * (n_pos - n_neg) / n_scored


def pct(n: int, n_scored: int) -> Optional[float]:
    return None if not n_scored else 100.0 * n / n_scored


def audience_gap(post_tone: Optional[float], audience_tone: Optional[float], n_scored: int, min_scored: int = MIN_SCORED) -> Optional[float]:
    """Audience mean tone minus post tone, in tone units (range -2..2).

    NULL when the post has no tone (never treated as neutral), when the audience has too few scored rows,
    or when either value is missing. Negative = the audience is more negative than the post.
    """
    if post_tone is None or audience_tone is None:
        return None
    if isinstance(post_tone, float) and math.isnan(post_tone):
        return None
    if isinstance(audience_tone, float) and math.isnan(audience_tone):
        return None
    if n_scored is None or n_scored < min_scored:
        return None
    return float(audience_tone) - float(post_tone)


def gap_direction(gap: Optional[float], flag: float = GAP_FLAG) -> Optional[str]:
    """Direction of a gap, or None when there is no gap. A missing gap reaches here as None OR as NaN (pandas turns None into NaN
    inside a float column); NaN fails every comparison below, so without this test it would fall through to "aligned"."""
    if gap is None or gap != gap:
        return None
    if gap <= -flag:
        return "audience_more_negative"
    if gap >= flag:
        return "audience_more_positive"
    return "aligned"


def reply_vs_comment(reply_tone: Optional[float], n_reply: int, comment_tone: Optional[float], n_comment: int,
                     min_scored: int = MIN_SCORED) -> Optional[float]:
    """Reply mean tone minus comment mean tone (escalation / disagreement inside the discussion). NULL below support."""
    if reply_tone is None or comment_tone is None or n_reply < min_scored or n_comment < min_scored:
        return None
    return float(reply_tone) - float(comment_tone)
