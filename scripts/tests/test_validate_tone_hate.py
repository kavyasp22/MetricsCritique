#!/usr/bin/env python3
"""Unit tests for the helpers in scripts/validate_tone_hate.py (no data needed)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vth", ROOT / "scripts" / "validate_tone_hate.py")
vth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vth)


def test_wilson_interval_bounds_and_edges():
    lo, hi = vth.wilson(0, 50)
    assert lo == 0.0 and 0.0 < hi < 0.1
    lo, hi = vth.wilson(50, 50)
    assert hi == 1.0 and 0.9 < lo < 1.0
    lo, hi = vth.wilson(30, 100)
    assert lo < 0.30 < hi
    assert np.isnan(vth.wilson(0, 0)[0])


def test_cls_thresholds_are_strict():
    t = pd.Series([0.2, 0.15, -0.15, -0.2, 0.0])
    assert vth.cls(t, 0.15).tolist() == [1, 0, 0, -1, 0]


def test_macro_f1_and_kappa_perfect_and_chance():
    y = np.array([-1, 0, 1, 0, 1, -1])
    assert vth.macro_f1(y, y) == pytest.approx(1.0)
    assert vth.kappa(y, y) == pytest.approx(1.0)
    p = np.zeros_like(y)                       # always neutral
    assert vth.kappa(y, p) == pytest.approx(0.0)
    assert vth.macro_f1(y, p) < 0.5


def test_label_sample_is_consistent_when_present():
    f = ROOT / "data" / "calibration" / "labelled_sample.parquet"
    if not f.exists():
        pytest.skip("labelled sample absent")
    d = pd.read_parquet(f)
    assert d.post_uid.is_unique and len(d) > 100
    assert set(d.label_tone.unique()) <= {-1, 0, 1} and set(d.label_hate.unique()) <= {0, 1}
    assert d.tone_score.between(-1, 1).all() and d.hate_score.between(0, 1).all()
    # every labelled hate row is also flagged as profane/insult OR is an explicit threat/slur case: hate implies toxicity
    assert (d.loc[d.label_hate == 1, "profane_or_insult"] == 1).mean() > 0.9
