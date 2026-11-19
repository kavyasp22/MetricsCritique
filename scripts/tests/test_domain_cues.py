import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.domain_cues import cue_hits, hybrid_theme


def test_high_tier_needs_two_distinct_cues():
    assert hybrid_theme("Navy warship enters the South China Sea", "D04", 0.52, False) == ("D04", "high")


def test_medium_tier_one_cue():
    assert hybrid_theme("A new naval base opened", "D04", 0.52, False) == ("D04", "medium")


def test_no_cue_is_unassigned():
    assert hybrid_theme("lovely sunset at the beach today", "D04", 0.52, False) == ("unassigned", "")


def test_low_score_and_confident_none_are_unassigned():
    assert hybrid_theme("Navy warship in the South China Sea", "D04", 0.47, False)[0] == "unassigned"
    assert hybrid_theme("Navy warship in the South China Sea", "D04", 0.60, True)[0] == "unassigned"


def test_cue_must_belong_to_the_chosen_domain():
    assert hybrid_theme("Foreign minister met the ambassador", "D04", 0.55, False)[0] == "unassigned"


def test_nan_text_is_safe():
    assert cue_hits(float("nan"), "D01") == 0
