import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import scenario_search as SS


def test_verify_prompt_defines_direct_and_not_the_scenario():
    brief = {"scenario": "Iran War", "v1": {"summary": "s", "anchors": ["a"] * 8, "not_this": ["video games"]}, "v2": None}
    p = SS.verify_system(brief)
    assert "Iran War" in p and "video games" in p and "direct" in p and "mentioned" in p and "unrelated" in p and "without using the scenario's words" in p


def test_verify_validation():
    ok = {"answers": [{"id": 1, "relevant": True, "people": [{"name": "A", "involvement": "direct"}]}, {"id": 2, "relevant": False, "people": []}]}
    assert SS._valid_verify(ok, 2) is None
    assert SS._valid_verify(ok, 3)
    assert SS._valid_verify({"answers": [{"id": 1, "relevant": "yes", "people": []}]}, 1)
    assert SS._valid_verify({"answers": [{"id": 1, "relevant": True, "people": [{"involvement": "sort of"}]}]}, 1)


def test_brief_validation_and_anchor_union():
    assert SS._valid_brief({"anchors": ["a"] * 8, "summary": "x"}) is None
    assert SS._valid_brief({"anchors": ["a"], "summary": "x"})
    b = {"scenario": "S", "v1": {"anchors": ["a1", "a2"]}, "v2": {"anchors": ["b1"]}}
    assert SS.anchor_texts(b) == ["S", "a1", "a2", "b1"]


def test_semantic_scores_normalise():
    v = pd.Series([0.4, 0.6, 0.8])
    assert [round(x, 6) for x in SS._sem_norm(v)] == [0.0, 0.5, 1.0]
    assert list(SS._sem_norm(pd.Series([0.5, 0.5]))) == [1.0, 1.0]
