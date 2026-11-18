"""Feedback store: validation, replace-on-revote, withdraw, per-reader lookup and summary (temp SQLite file)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "UI"))
import feedback_store as F  # noqa: E402


@pytest.fixture()
def st(tmp_path):
    return F.FeedbackStore(tmp_path / "fb.sqlite")


def body(**kw):
    b = dict(user="u1", item_type="alert", item_id="a|2026-10-25", day="2026-10-25", label="Obama", vote=1)
    b.update(kw)
    return b


def test_vote_replace_and_withdraw(st):
    st.vote(body(vote=1))
    st.vote(body(vote=-1, reasons=["routine_volume", "bogus"], other_text="x" * 500))
    v = st.votes_for("u1", "alert", ["a|2026-10-25"], "2026-10-25")["a|2026-10-25"]
    assert v["vote"] == -1 and v["reasons"] == ["routine_volume"]          # unknown reason dropped
    assert st.summary()["by_type"]["alert"] == {"up": 0, "down": 1}          # replaced, not added
    st.vote(body(vote=0))
    assert st.votes_for("u1", "alert", ["a|2026-10-25"], "2026-10-25") == {}


def test_reasons_and_text_only_kept_for_down_votes(st):
    st.vote(body(vote=1, reasons=["duplicate"], other_text="hello"))
    s = st.summary()
    assert s["down_reasons"] == {} and s["by_type"]["alert"]["up"] == 1


def test_other_readers_do_not_see_each_others_votes(st):
    st.vote(body(user="u1", vote=1)); st.vote(body(user="u2", vote=-1))
    assert st.votes_for("u1", "alert", ["a|2026-10-25"], "2026-10-25")["a|2026-10-25"]["vote"] == 1
    s = st.summary()
    assert s["readers"] == 2 and s["by_type"]["alert"] == {"up": 1, "down": 1}


@pytest.mark.parametrize("bad", [dict(user=""), dict(item_type="nope"), dict(item_id=""), dict(item_id="x" * 201), dict(vote=2)])
def test_validation(st, bad):
    with pytest.raises(F.FeedbackError):
        st.vote(body(**bad))
