"""Sentiment-quality rules (scripts/lib/sentiment_quality.py) and the Wilson interval (stats_v2)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import sentiment_quality as sq  # noqa: E402
from lib.stats_v2 import wilson_interval  # noqa: E402


def test_self_checks():
    assert all(sq.run_self_checks().values())


def test_skipped_rows_are_never_neutral_and_never_counted():
    import importlib.util
    spec = importlib.util.spec_from_file_location("s51", Path(__file__).resolve().parents[1] / "51_sentiment_quality.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    n = 40
    df = pd.DataFrame({"platform": "x", "layer": "comment", "posted_at": "2026-01-05T00:00:00Z", "score_status": "scored",
                       "label": ["positive"] * n, "p_negative": 0.05, "p_neutral": 0.05, "p_positive": 0.9,
                       "text_used": ["great work today"] * 30 + ["😂"] * 5 + ["?!"] * 5})
    r = m.rollup(m.add_quality(df))
    a = r[r.posted_month == "ALL"].iloc[0]
    assert (a.a_n, a.n_skipped, a.b_n, a.n_emoji_only, a.t_n) == (40, 5, 35, 5, 30)   # emoji kept in B, absent from T
    assert a.b_neu == 0 and a.n_skipped == 5           # NO_TEXT rows are excluded, not relabelled neutral


def test_below_min_n_is_null_not_zero():
    import importlib.util
    spec = importlib.util.spec_from_file_location("s51b", Path(__file__).resolve().parents[1] / "51_sentiment_quality.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    g = pd.DataFrame({"label": ["positive"] * 5})
    s = m._stats(g, "a")
    assert s["a_n"] == 5 and s["a_net"] is None and s["a_pos_pct"] is None


def test_wilson_bounds():
    p, lo, hi = wilson_interval([0, 3], [3, 3])
    assert lo[0] == 0 and hi[0] > 0.5 and hi[1] == 1 and lo[1] < 0.6


def _trend_fixture(tmp_path, monkeypatch, weeks, ns, nets):
    import duckdb
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "UI"))
    import audience_view as av
    db = tmp_path / "q.duckdb"
    rows = [("youtube", "comment", w, n, round(n * (50 + x) / 200), round(n * (50 - x) / 200)) for w, n, x in zip(weeks, ns, nets)]
    c = duckdb.connect(str(db))
    c.execute("CREATE TABLE sentiment_quality_weekly_v1 (platform VARCHAR, layer VARCHAR, week VARCHAR, b_n BIGINT, b_pos BIGINT, b_neg BIGINT)")
    c.executemany("INSERT INTO sentiment_quality_weekly_v1 VALUES (?,?,?,?,?,?)", rows)
    c.close()
    monkeypatch.setattr(av, "QUAL_DB", db)
    return av


def test_trend_block_detects_a_real_trend_and_ignores_a_single_spike(tmp_path, monkeypatch):
    weeks = [str(d.date()) for d in pd.date_range("2026-01-05", periods=16, freq="7D")]
    av = _trend_fixture(tmp_path, monkeypatch, weeks, [100] * 16, [-20 + 2 * i for i in range(16)])
    r = av.trend_block(["youtube_video"], "", "")["rows"][0]
    assert r["trend"] == "up" and abs(r["slope"] - 2.0) < 0.1 and r["p"] < 0.001      # net (pos - neg) rises exactly 2 points a week
    flat = [0.0] * 15 + [50.0]                                                          # one viral week, otherwise flat
    (tmp_path / "b").mkdir()
    av2 = _trend_fixture(tmp_path / "b", monkeypatch, weeks, [100] * 16, flat)
    r2 = av2.trend_block(["youtube_video"], "", "")["rows"][0]
    assert abs(r2["slope"]) < 0.5 and r2["trend"] == "none" and abs(r2["ols_slope"]) > 0.8   # Theil-Sen ignores the spike, OLS does not


def test_trend_block_is_null_not_zero_when_thin_or_gappy(tmp_path, monkeypatch):
    weeks = [str(d.date()) for d in pd.date_range("2026-01-05", periods=5, freq="7D")]
    av = _trend_fixture(tmp_path, monkeypatch, weeks, [100] * 5, [0] * 5)
    r = av.trend_block(["youtube_video"], "", "")["rows"][0]
    assert r["slope"] is None and r["trend"] is None and r["weeks"] == 5                # under 8 weeks
    gappy = [str(d.date()) for d in pd.date_range("2026-01-05", periods=60, freq="7D")][::6][:10]   # 10 weeks spread over ~54
    (tmp_path / "c").mkdir()
    av3 = _trend_fixture(tmp_path / "c", monkeypatch, gappy, [100] * 10, list(range(10)))
    r3 = av3.trend_block(["youtube_video"], "", "")["rows"][0]
    assert r3["slope"] is None and r3["cover"] < 0.7                                    # coverage rule
    weeks_low = [str(d.date()) for d in pd.date_range("2026-01-05", periods=10, freq="7D")]
    (tmp_path / "d").mkdir()
    av4 = _trend_fixture(tmp_path / "d", monkeypatch, weeks_low, [10] * 10, list(range(10)))   # < 30 rows each: no usable weeks
    assert av4.trend_block(["youtube_video"], "", "")["rows"][0]["weeks"] == 0
