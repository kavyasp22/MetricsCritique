#!/usr/bin/env python3
"""
Does a median/MAD tone-drift rule beat the shipped Student-t mean/sd rule? (read-only evaluation; nothing is written or changed)

Series: data/metrics.duckdb metrics_daily, in-window days with a tone, one tone_mean / tone_n series per (topic_id, platform) cell, calendar gaps = NaN
(the exact rows 07_metrics_v2.add_tone_drift uses).
Tests, all on the real cells:
  1. replication   the shipped rule (stats_v2.prior_window_drift) must reproduce the stored tone_drift_z, or the comparison is not valid.
  2. false alerts  each series' days are shuffled (breaking any real drift, keeping its level/variance); both rules then flag a day when
                   the lower-tail p < 0.05 (Student-t with base_n - 1 df for both). A well-calibrated rule flags ~5% of days.
  3. agreement     on the unshuffled series: how often the two rules flag the same days.
Verdict printed at the end; the decision to adopt is a spec change, not made here.

    .venv/bin/python scripts/review/robust_drift_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from lib.stats_v2 import prior_window_drift, prior_window_drift_robust  # noqa: E402

DB = HERE.parent / "data" / "metrics.duckdb"
ALPHA = 0.05
rng = np.random.default_rng(20261127)


def series():
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("SELECT day, topic_id, platform, tone_mean, tone_n, tone_drift_z FROM metrics_daily WHERE in_window AND tone_mean IS NOT NULL   -- the same rows 07.add_tone_drift uses").df()
    con.close()
    df["day"] = pd.to_datetime(df["day"])
    for (t, p), g in df.groupby(["topic_id", "platform"]):
        if len(g) < 25:
            continue
        idx = pd.date_range(g.day.min(), g.day.max(), freq="D")
        g = g.set_index("day").reindex(idx)
        yield t, p, g["tone_mean"].to_numpy(float), g["tone_n"].to_numpy(float), g["tone_drift_z"].to_numpy(float)


def flags(z, bn):
    ok = np.isfinite(z) & (bn >= 2)
    p = np.full(z.shape, np.nan)
    p[ok] = stats.t.cdf(z[ok], df=bn[ok] - 1)
    return p < ALPHA, ok


def main() -> int:
    n_ser = 0; rep_n = rep_ok = 0
    fa = {"mean_sd": [0, 0], "median_mad": [0, 0]}
    real = {"both": 0, "only_mean_sd": 0, "only_median_mad": 0, "tested": 0}
    for t, p, v, n, stored in series():
        n_ser += 1
        z1, _, b1 = prior_window_drift(v, n)
        m = np.isfinite(stored) & np.isfinite(z1)
        rep_n += int(np.isfinite(stored).sum()); rep_ok += int((m & (np.abs(stored - z1) < 1e-6)).sum())
        z2, b2 = prior_window_drift_robust(v, n)
        f1, o1 = flags(z1, b1); f2, o2 = flags(z2, b2)
        both = o1 & o2
        real["tested"] += int(both.sum()); real["both"] += int((f1 & f2 & both).sum())
        real["only_mean_sd"] += int((f1 & ~f2 & both).sum()); real["only_median_mad"] += int((~f1 & f2 & both).sum())
        for _ in range(3):                                                    # shuffled copies: no real drift, so flags are false alerts
            idx = rng.permutation(len(v))
            vs, ns = v[idx], n[idx]
            for key, (zz, bb) in (("mean_sd", prior_window_drift(vs, ns)[0::2]), ("median_mad", prior_window_drift_robust(vs, ns))):
                ff, oo = flags(zz, bb)
                fa[key][0] += int((ff & oo).sum()); fa[key][1] += int(oo.sum())
    print(f"series evaluated: {n_ser}")
    print(f"1. replication of stored tone_drift_z: {rep_ok}/{rep_n} within 1e-6")
    for k, (a, b) in fa.items():
        print(f"2. false-alert rate on shuffled series, {k:10s}: {100 * a / max(b, 1):5.2f}%  ({a}/{b} testable days; nominal {100 * ALPHA:.0f}%)")
    print(f"3. agreement on real series: {real['tested']} testable days; flagged by both {real['both']}, only mean/sd {real['only_mean_sd']}, only median/MAD {real['only_median_mad']}")
    ok_rep = rep_n > 0 and rep_ok / rep_n > 0.99
    fa_ms, fa_mm = (100 * fa[k][0] / max(fa[k][1], 1) for k in ("mean_sd", "median_mad"))
    print("verdict:", "comparison valid" if ok_rep else "REPLICATION FAILED, comparison not valid",
          "| median/MAD is", "better calibrated" if abs(fa_mm - 5) < abs(fa_ms - 5) else "worse calibrated", "than mean/sd (closer to nominal 5% = better)")
    return 0 if ok_rep else 1


if __name__ == "__main__":
    raise SystemExit(main())
