from __future__ import annotations

import itertools
import math
import random
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from bot.audit.stats import (
    daily_equity,
    deflated_sharpe,
    expected_max_sharpe,
    max_drawdown,
    probabilistic_sharpe,
    risk_metrics,
    sharpe,
    stationary_bootstrap_indices,
    trade_block_bootstrap,
)


def test_daily_equity_forward_fills_and_uses_day_close() -> None:
    pts = [
        (datetime(2024, 1, 1, 10, tzinfo=UTC), D(100)),
        (datetime(2024, 1, 3, 23, tzinfo=UTC), D(110)),
    ]
    s = daily_equity(pts, date(2024, 1, 1), date(2024, 1, 4))
    assert s == [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 100.0),
                 (date(2024, 1, 3), 110.0), (date(2024, 1, 4), 110.0)]  # fmt: skip


def test_max_drawdown_and_duration() -> None:
    assert max_drawdown([100, 120, 90, 100, 130]) == (0.25, 2)


def test_risk_metrics_on_known_series() -> None:
    series = [100 * 1.001**i for i in range(366)]  # +0,1% diario constante
    m = risk_metrics(series)
    assert m.cagr == pytest.approx(1.001**365 - 1, rel=1e-9)
    assert m.max_dd == 0 and m.calmar == math.inf


def test_sharpe_of_noise_is_near_zero_and_bootstrap_indices_valid() -> None:
    rng = random.Random(1)
    r = [rng.gauss(0, 0.01) for _ in range(5000)]
    assert abs(sharpe(r)) < 0.6
    idx = stationary_bootstrap_indices(100, 10, random.Random(2))
    assert len(idx) == 100 and all(0 <= i < 100 for i in idx)
    runs = sum(1 for a, b in itertools.pairwise(idx) if b == (a + 1) % 100)
    assert 80 < runs < 99  # bloques de longitud media ~10


def test_trade_bootstrap_detects_edge_and_no_edge() -> None:
    good = [3.0, -1.0, -1.0, 2.0, -1.0] * 40  # R medio +0,4
    bad = [1.0, -1.0] * 100  # R medio 0
    g = trade_block_bootstrap(good, block=5, n_boot=500)
    b = trade_block_bootstrap(bad, block=5, n_boot=500)
    assert g.p_mean_r_le_0 < 0.01
    assert b.p_mean_r_le_0 > 0.3
    assert g.pf_quantiles["p50"] > 1.5


def test_probabilistic_and_deflated_sharpe() -> None:
    assert probabilistic_sharpe(0.0, 0.0, 1000, 0.0, 3.0) == pytest.approx(0.5)
    assert probabilistic_sharpe(0.1, 0.0, 1000, 0.0, 3.0) > 0.99
    # Más pruebas → umbral más alto.
    assert expected_max_sharpe(100, 0.001) > expected_max_sharpe(10, 0.001) > 0
    rng = random.Random(3)
    r = [rng.gauss(0.002, 0.02) for _ in range(2000)]
    dsr_few, _ = deflated_sharpe(r, 2, 0.0005)
    dsr_many, _ = deflated_sharpe(r, 500, 0.0005)
    assert dsr_many < dsr_few
