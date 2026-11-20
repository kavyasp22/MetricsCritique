import sys
from pathlib import Path
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import scenario_search as SS


def _ctx(assessment="clear"):
    return {"assessment": assessment, "event_summary": "Escalating clashes at the border.", "developments": [{"title": "Clashes", "summary": "s", "time": "Oct 20"}], "_off_topic_texts": ["a game review"]}


def test_validation_prompt_has_the_four_classes_and_the_grounded_context():
    p = SS.verify_system(_ctx(), "Border Clashes", ["post one", "post two"])
    for w in ("direct", "indirect", "mention", "unrelated", "Escalating clashes at the border.", "a game review", "post one", "copied EXACTLY", "Never answer direct only because the name is in the post"):
        assert w in p
    assert "NOTE: the event is only" not in p
    assert "NOTE: the event is only ambiguous" in SS.verify_system(_ctx("ambiguous"), "x", [])


def test_prompts_are_topic_agnostic():
    p = SS._ground_prompt() + SS.verify_system(_ctx(), "Flood", [])
    assert "Iran" not in p and "conflict, politics, protest, diplomacy, economy" in p


def test_verify_validation_uses_the_four_classes():
    ok = {"answers": [{"id": 1, "relevant": True, "people": [{"name": "A", "involvement": "indirect"}]}, {"id": 2, "relevant": False, "people": []}]}
    assert SS._valid_verify(ok, 2) is None
    assert SS._valid_verify(ok, 3)
    assert SS._valid_verify({"answers": [{"id": 1, "relevant": True, "people": [{"involvement": "mentioned"}]}]}, 1)   # the old class name is refused
    assert SS._valid_verify({"answers": [{"id": 1, "relevant": "yes", "people": []}]}, 1)


def test_grounding_validation():
    good = {"assessment": "clear", "event_summary": "x", "anchors": ["a", "b", "c"]}
    assert SS._valid_ground(good) is None
    assert SS._valid_ground({**good, "assessment": "sure"})
    assert SS._valid_ground({**good, "anchors": ["a"]})
    assert SS._valid_ground({**good, "developments": "none"})


def test_involvement_weights_and_semantic_scores():
    assert SS.W_INV["direct"] > SS.W_INV["indirect"] > SS.W_INV["mention"] == SS.W_INV["unrelated"] == 0
    assert [round(x, 6) for x in SS._sem_norm(pd.Series([0.4, 0.6, 0.8]))] == [0.0, 0.5, 1.0]


@pytest.mark.skipif(not SS.EMB.exists(), reason="stage 31 embedding files not built")
def test_grounding_sample_is_diverse_not_the_top_n():
    X, ix = SS._load()
    ix2 = ix[(ix["day"] >= "2026-10-12") & (ix["day"] <= "2026-10-26") & ix["is_first"]].sample(600, random_state=1)
    pool = ix2[["post_uid", "platform", "day"]].assign(sem=range(600, 0, -1)).reset_index(drop=True)      # rank = order
    got = SS.diverse_sample(pool, 36, "2026-10-12", "2026-10-26")
    assert len(got) == 36 and len(set(got)) == 36
    top = set(pool.post_uid.head(36))
    assert len(set(got) & top) < 30                                  # not the top 36
    g = pool[pool.post_uid.isin(got)]
    assert g.platform.nunique() >= 4 and g.day.nunique() >= 5
    assert (pool.reset_index().set_index("post_uid").loc[got, "index"] >= 500).sum() >= 3     # some from the fringe of the ranking
