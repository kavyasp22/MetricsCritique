#!/usr/bin/env python3
"""Unit tests for the empirical-Bayes shrinkage primitive (F5).

Model: normal-normal posterior mean  theta = (n*xbar + k*mu) / (n + k),  k = sigma_w^2 / tau^2.
Foundation: Efron & Morris (1973), JASA 68(341):117-130 (empirical Bayes shrinkage toward a common
mean, prior variance estimated from the data). The unequal-n moment estimator of tau^2 in
`estimate_eb_k` is a DerSimonian-Laird-type estimator (their paper does not cover that case).

Run:  .venv/bin/python -m pytest scripts/tests/test_eb_shrink.py -v
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib.stats_v2 as S  # noqa: E402


def test_large_sample_keeps_raw_mean():
    assert S.eb_shrink(0.9, 1e9, 0.5, 0.44) == pytest.approx(0.9, abs=1e-6)


def test_tiny_sample_returns_parent_mean():
    assert S.eb_shrink(0.9, 1e-9, 0.5, 0.44) == pytest.approx(0.5, abs=1e-6)


def test_huge_k_returns_parent_mean():
    assert S.eb_shrink(0.9, 10, 0.5, 1e9) == pytest.approx(0.5, abs=1e-6)


def test_zero_k_is_no_shrinkage():
    assert S.eb_shrink(0.9, 10, 0.5, 0.0) == pytest.approx(0.9)


def test_weight_is_n_over_n_plus_k():
    n, k, x, mu = 12.0, 3.0, 0.8, 0.4
    w = (S.eb_shrink(x, n, mu, k) - mu) / (x - mu)
    assert w == pytest.approx(n / (n + k))


def test_shrinks_toward_parent_never_past_it():
    for x, mu in [(0.9, 0.5), (0.1, 0.5)]:
        v = S.eb_shrink(x, 5, mu, 2.0)
        assert min(x, mu) <= v <= max(x, mu)


def test_missing_inputs():
    assert np.isnan(S.eb_shrink(0.5, 0, 0.5, 1.0))          # no observations
    assert np.isnan(S.eb_shrink(np.nan, 5, 0.5, 1.0))       # no mean
    assert S.eb_shrink(0.7, 5, np.nan, 1.0) == pytest.approx(0.7)  # no parent -> raw


def test_more_data_means_less_shrinkage():
    shifts = [abs(S.eb_shrink(0.9, n, 0.5, 2.0) - 0.9) for n in (1, 5, 20, 200)]
    assert shifts == sorted(shifts, reverse=True)


def test_estimate_k_recovers_truth_and_beats_raw():
    rng = np.random.default_rng(0)
    tau2, s2 = 0.02, 0.05
    ns = rng.integers(3, 80, 400)
    theta = rng.normal(0.5, tau2 ** 0.5, 400)
    xbar = rng.normal(theta, (s2 / ns) ** 0.5)
    k, t = S.estimate_eb_k(xbar, ns, s2)
    assert k == pytest.approx(s2 / tau2, rel=0.25)
    assert t == pytest.approx(tau2, rel=0.25)
    post = np.array([S.eb_shrink(a, b, 0.5, k) for a, b in zip(xbar, ns)])
    assert np.mean((post - theta) ** 2) < np.mean((xbar - theta) ** 2)


def test_no_between_group_signal_shrinks_fully():
    rng = np.random.default_rng(1)
    ns = rng.integers(5, 50, 300)
    xbar = rng.normal(0.5, (0.05 / ns) ** 0.5)       # tau^2 = 0: all variation is noise
    k, _ = S.estimate_eb_k(xbar, ns, 0.05)
    assert k > 20


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
