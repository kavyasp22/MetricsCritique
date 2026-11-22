import ast
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.audience_text import dup_key, pick_text, sha1_hex, triage
from lib.sentiment_model import BATCH, SENTIMENT_MODEL, label_probs, sentiment_to_continuous

SCRIPTS = Path(__file__).resolve().parents[1]


def test_triage_empty_and_deleted_and_symbols():
    assert triage(None)[0] == "empty"
    assert triage(float("nan"))[0] == "empty"
    assert triage("   \n ")[0] == "empty"
    assert triage("[deleted]")[0] == "deleted_marker"
    assert triage("This comment has been removed")[0] == "deleted_marker"
    assert triage("https://youtu.be/abc123")[0] == "unusable_url_only"
    assert triage("!!! ... ???")[0] == "unusable_symbols"


def test_triage_ok_rows_are_flagged_not_dropped():
    assert triage("Great video") == ("ok", [])
    st, fl = triage("\U0001F44D\U0001F44D")
    assert st == "ok" and "emoji_only" in fl
    st, fl = triage("ok")
    assert st == "ok" and "very_short" in fl
    st, fl = triage("see https://x.example/a good point")
    assert st == "ok" and "has_url" in fl
    st, fl = triage("a" * 2500)
    assert st == "ok" and "truncated" in fl


def test_pick_text_prefers_translation_like_posts():
    assert pick_text("hola", "hello") == ("hello", "translated")
    assert pick_text("hola", None) == ("hola", "original")
    assert pick_text("hola", "  ") == ("hola", "original")
    assert pick_text(None, None) == ("", "none")


def test_dup_key_ignores_case_space_and_urls():
    assert dup_key("  First!!  http://a.b/c ") == dup_key("first!! ")
    assert dup_key("A  B") == "a b"


def test_label_probs_argmax_and_tone_agree():
    out = [{"label": "positive", "score": 0.2}, {"label": "negative", "score": 0.7}, {"label": "neutral", "score": 0.1}]
    lp = label_probs(out)
    assert lp["label"] == "negative" and abs(lp["p_negative"] - 0.7) < 1e-9
    assert abs(sentiment_to_continuous(out) - (0.2 - 0.7)) < 1e-9
    lbl = [{"label": "LABEL_2", "score": 0.6}, {"label": "LABEL_0", "score": 0.1}, {"label": "LABEL_1", "score": 0.3}]
    assert label_probs(lbl)["label"] == "positive"
    assert label_probs([]) is None and label_probs([{"label": "positive", "score": 1.0}]) is None


def _funcs(path):
    tree = ast.parse(Path(path).read_text())
    return {n.name: ast.dump(n) for n in tree.body if isinstance(n, ast.FunctionDef)}, {
        t.id: ast.dump(n.value) for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}


def test_model_setup_is_identical_to_the_post_pipeline():
    """Comments and replies must use exactly the post pipeline's model, loader and tone formula."""
    f04, c04 = _funcs(SCRIPTS / "04_nlp_scores.py")
    fm, cm = _funcs(SCRIPTS / "lib" / "sentiment_model.py")
    for name in ("load_sentiment_pipeline", "sentiment_to_continuous"):
        assert fm[name] == f04[name], f"{name} drifted from 04_nlp_scores.py"
    assert cm["SENTIMENT_MODEL"] == c04["SENTIMENT_MODEL"] and cm["BATCH"] == c04["BATCH"]
    assert "[:2000]" in (SCRIPTS / "04_nlp_scores.py").read_text()          # the 2000-char cap lives in 04's batch loop
    assert SENTIMENT_MODEL == "cardiffnlp/twitter-xlm-roberta-base-sentiment" and BATCH == 64


def test_comment_uid_recipe_matches_the_foundation_store():
    assert sha1_hex("yt_comment", "abc") == hashlib.sha1("yt_comment|abc".encode()).hexdigest()
    assert sha1_hex("youtube_video", "v1") == hashlib.sha1("youtube_video|v1".encode()).hexdigest()


# ---- Phase 2: roll-up definitions -------------------------------------------------------------------------------
from lib.audience_rollup import MIN_SCORED, audience_gap, comment_data_status, gap_direction, net_sentiment, pct, reply_vs_comment


def test_gap_neutral_post_with_very_negative_comments():
    g = audience_gap(0.02, -0.55, 40)
    assert abs(g - (-0.57)) < 1e-9 and gap_direction(g) == "audience_more_negative"


def test_gap_positive_post_negative_audience_and_reverse():
    assert abs(audience_gap(0.6, -0.2, 20) - (-0.8)) < 1e-9
    assert gap_direction(audience_gap(-0.5, 0.1, 12)) == "audience_more_positive"


def test_gap_aligned_within_threshold():
    assert gap_direction(audience_gap(-0.2, -0.3, 30)) == "aligned"
    assert gap_direction(-0.30) == "audience_more_negative" and gap_direction(0.2999) == "aligned"


def test_gap_is_null_never_zero_when_inputs_are_missing_or_thin():
    assert audience_gap(None, -0.4, 50) is None                 # no post tone: not treated as neutral
    assert audience_gap(float("nan"), -0.4, 50) is None
    assert audience_gap(0.1, None, 50) is None
    assert audience_gap(0.1, -0.4, MIN_SCORED - 1) is None      # below minimum support
    assert audience_gap(0.1, -0.4, MIN_SCORED) is not None
    assert gap_direction(None) is None


def test_reply_vs_comment_escalation_needs_support_on_both_sides():
    assert abs(reply_vs_comment(-0.6, 10, -0.1, 30) - (-0.5)) < 1e-9
    assert reply_vs_comment(-0.6, 3, -0.1, 30) is None
    assert reply_vs_comment(None, 10, -0.1, 30) is None


def test_net_sentiment_and_pct_are_null_not_zero_without_data():
    assert net_sentiment(5, 3, 10) == 20.0
    assert net_sentiment(0, 0, 0) is None and pct(0, 0) is None
    assert pct(1, 4) == 25.0


def test_zero_comments_vs_missing_comment_data():
    assert comment_data_status(7, None) == "has_comments"
    assert comment_data_status(0, 0) == "confirmed_zero"
    assert comment_data_status(0, 12) == "not_collected_known"
    assert comment_data_status(0, None) == "unknown_no_comment_data"
    assert comment_data_status(0, float("nan")) == "unknown_no_comment_data"


# ---- Phase 3: Instagram comments_data parsing ------------------------------------------------------------------
from lib.audience_instagram import flatten_payload
from lib.audience_text import mention_only


def _c(cid, text="hello", parent=None, replies=None):
    return {"comment_id": cid, "text": text, "username": "u", "likes": 1, "created_at": "2026-07-24T09:00:35+00:00",
            "is_verified": False, "parent_comment_id": parent, "replies": replies if replies is not None else []}


def test_nested_replies_become_reply_rows_with_their_parent():
    rows = flatten_payload([_c("1", replies=[_c("2", "hi", parent="1"), _c("3", "yo", parent="1")]), _c("4")])
    assert [r["comment_id"] for r in rows] == ["1", "2", "3", "4"]
    assert [r["layer"] for r in rows] == ["comment", "reply", "reply", "comment"]
    assert rows[1]["parent_comment_id"] == "1" and rows[1]["shape"] == "nested" and rows[1]["depth"] == 1
    assert all(r["item_status"] == "ok" for r in rows)


def test_reply_without_declared_parent_gets_the_comment_it_sits_under():
    rows = flatten_payload([_c("1", replies=[_c("2", parent=None)])])
    assert rows[1]["layer"] == "reply" and rows[1]["parent_comment_id"] == "1"


def test_top_level_object_with_a_parent_id_is_a_reply_flagged_by_shape():
    rows = flatten_payload([_c("9", parent="404")])
    assert rows[0]["layer"] == "reply" and rows[0]["shape"] == "top_level_with_parent" and rows[0]["parent_comment_id"] == "404"


def test_malformed_items_are_kept_with_a_status_not_dropped():
    rows = flatten_payload([{"id": 7, "type": "image", "url": "x", "accessibilityCaption": "y"}, "junk", _c("1")])
    assert [r["item_status"] for r in rows] == ["malformed_non_comment", "malformed_non_comment", "ok"]
    assert rows[0]["comment_id"] is None and rows[0]["raw"]
    assert flatten_payload("oops")[0]["item_status"] == "malformed_non_comment"
    assert flatten_payload(None) == [] and flatten_payload([]) == []


def test_empty_and_missing_text_stay_ok_structurally_and_are_triaged_later():
    rows = flatten_payload([_c("1", text=""), {"comment_id": "2"}])
    assert rows[0]["item_status"] == "ok" and rows[0]["text"] == ""
    assert rows[1]["item_status"] == "malformed_non_comment"          # no text key at all


def test_mention_only_comments_are_flagged():
    assert mention_only("@a.b @c_d") and mention_only(" @friend ")
    assert not mention_only("@friend nice shot") and not mention_only("hello") and not mention_only(None)
    assert not mention_only("@friend \U0001F525")


# ---- Regression: a missing gap (None OR NaN) is never "aligned" -------------------------------------------------------
import numpy as np
import pandas as pd


def test_missing_gap_is_missing_not_aligned_none_and_nan():
    assert gap_direction(None) is None
    assert gap_direction(float("nan")) is None
    assert gap_direction(np.nan) is None
    assert gap_direction(np.float64("nan")) is None
    assert gap_direction(np.float32("nan")) is None


def test_valid_gaps_keep_their_existing_classification_and_threshold():
    assert gap_direction(-0.30) == "audience_more_negative" and gap_direction(0.30) == "audience_more_positive"
    assert gap_direction(-0.2999) == "aligned" and gap_direction(0.2999) == "aligned" and gap_direction(0.0) == "aligned"
    assert gap_direction(-1.2) == "audience_more_negative" and gap_direction(np.float64(0.75)) == "audience_more_positive"


def test_a_pandas_column_with_missing_gaps_never_produces_aligned_for_them():
    """pandas turns None into NaN inside a float column: that is exactly how the defect reached gap_direction."""
    col = pd.Series([0.5, None, -0.4, np.nan, 0.1, None], dtype="float64")
    assert col.isna().sum() == 3 and all(isinstance(v, float) for v in col)
    labels = [gap_direction(v) for v in col]
    assert labels == ["audience_more_positive", None, "audience_more_negative", None, "aligned", None]
    valid = [x for x in labels if x is not None]
    counts = {k: valid.count(k) for k in ("audience_more_negative", "aligned", "audience_more_positive")}
    assert sum(counts.values()) == int(col.notna().sum())              # the three groups add up to the posts WITH a gap
    assert counts["aligned"] / len(valid) * 100 <= 100
    assert abs(sum(100 * v / len(valid) for v in counts.values()) - 100) < 1e-9
