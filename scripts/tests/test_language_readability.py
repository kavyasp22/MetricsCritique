#!/usr/bin/env python3
"""Tests for the language-label fix and the single readability measure in scripts/04_nlp_scores.py.

Run:  .venv/bin/python -m pytest scripts/tests/test_language_readability.py -v
"""
import importlib.util
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("m04", ROOT / "scripts" / "04_nlp_scores.py")
m04 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m04)

EN = ("The committee reviewed the proposal carefully and decided that additional documentation would be required "
      "before the funding could be approved by the board of directors next month.")
HI = ("समिति ने प्रस्ताव की सावधानीपूर्वक समीक्षा की और निर्णय लिया कि अगले महीने निदेशक मंडल द्वारा धन स्वीकृत किए जाने से "
      "पहले अतिरिक्त दस्तावेज़ों की आवश्यकता होगी और यह प्रक्रिया पूरी की जाएगी")


@pytest.mark.parametrize("declared", [None, np.nan, float("nan"), "", "  ", "unknown", "(unknown)", "null", "nan", "None", pd.NA])
def test_missing_language_is_detected_from_the_text_not_stored_as_nan(declared):
    got = m04.resolve_lang(EN, declared)
    assert got == "en" and got != "nan"


def test_declared_language_is_kept_and_normalised():
    assert m04.resolve_lang(EN, "EN-us") == "en-us"
    assert m04.resolve_lang(EN, "hi") == "hi"
    assert m04.resolve_lang(EN, "bengali_long_name_xyz")[:8] == "bengali_"


def test_language_detection_is_reproducible_and_unknown_when_empty():
    assert {m04.resolve_lang(HI, None) for _ in range(5)} == {"hi"}
    assert m04.resolve_lang("", None) == "unknown" and m04.resolve_lang("   ", np.nan) == "unknown"


def test_one_readability_measure_for_every_language():
    en = m04.score_readability(EN, "en")
    hi = m04.score_readability(HI, "hi")
    for r in (en, hi):
        assert r["readability_valid"] and r["readability_metric"] == "lix" and np.isfinite(r["readability_score"])
    # same LIX definition on both: recompute by hand
    words = re.findall(r"\w+", EN, re.UNICODE)
    lix = len(words) / max(1, len(re.findall(r"[.!?。！？]+", EN)) or 1) + 100 * sum(len(w) > 6 for w in words) / len(words)
    assert en["readability_score"] == pytest.approx(lix)


def test_unscorable_cases_are_null_not_zero():
    assert m04.score_readability("too short", "en")["readability_reason"] == "too_short"
    zh = m04.score_readability("中 " * 60, "zh")        # long enough to pass the length floor, then not applicable
    assert zh["readability_reason"] == "lang_not_applicable" and zh["readability_score"] is None
    for lang in ("ja", "th", "my"):
        assert m04.score_readability(EN, lang)["readability_valid"] is False


def test_fk_grade_is_never_the_main_score(monkeypatch):
    monkeypatch.setattr(m04, "flesch_kincaid_grade", lambda t: 9.5)
    en = m04.score_readability(EN, "en")
    assert en["readability_fk_grade"] == 9.5 and en["readability_metric"] == "lix" and en["readability_score"] != 9.5
    assert m04.score_readability(HI, "hi")["readability_fk_grade"] is None        # English only


def test_published_scores_have_one_metric_and_no_nan_language_when_present():
    import duckdb
    db = ROOT / "data" / "analytics.duckdb"
    if not db.exists():
        pytest.skip("analytics.duckdb absent")
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = con.execute("describe post_scores").fetchdf().column_name.tolist()
        if "readability_fk_grade" not in cols:
            pytest.skip("post_scores not recomputed yet (run scripts/04b_readability_only.py)")
        metrics = {r[0] for r in con.execute("SELECT DISTINCT readability_metric FROM post_scores WHERE readability_valid").fetchall()}
        n_nan = con.execute("SELECT count(*) FROM post_scores WHERE lang_resolved = 'nan'").fetchone()[0]
    finally:
        con.close()
    assert metrics == {"lix"} and n_nan == 0
