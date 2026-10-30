"""Controles de la comparación de modelos y regla de régimen (docs/fase-testnet-plan.md)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

from bot.audit.models import AlwaysInTrend, SignalSet, _daily_labels, shifted_signals
from bot.backtest.engine import SymbolData
from bot.data.klines import Candle
from bot.exchange.filters import SymbolFilters
from bot.strategy.features import build_bars
from bot.strategy.hypotheses import H1Breakout
from tests.unit.test_hypotheses import bar
from tests.unit.test_paper import _series


def _data(symbol_info: dict[str, dict[str, Any]]) -> dict[str, SymbolData]:
    four, daily = _series("BTCUSDT")
    f = SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])
    return {"BTCUSDT": SymbolData("BTCUSDT", build_bars("BTCUSDT", four, daily), f)}


def test_shifted_signals_preserve_count_and_change_timing(symbol_info: dict[str, Any]) -> None:
    data = _data(symbol_info)
    base = H1Breakout()
    bars = data["BTCUSDT"].snapshots
    original = {(b.symbol, b.close_time) for i, b in enumerate(bars[1:], 1)
                if base.evaluate_entry(bars[i - 1 : i + 1])[0] is not None}  # fmt: skip
    shifted = shifted_signals(data, base, random.Random(1))
    assert len(original) > 5
    assert len(shifted) == len(original)  # mismo número de señales
    assert shifted != original  # distinta ubicación
    # Todas las señales desplazadas caen en velas con régimen alcista.
    elig = {(b.symbol, b.close_time) for b in bars if b.atr is not None
            and b.regime_close is not None and b.regime_ema[100] is not None
            and b.regime_close > b.regime_ema[100]}  # fmt: skip
    assert shifted <= elig
    s = SignalSet(base, shifted)
    hits = sum(s.evaluate_entry(bars[i - 1 : i + 1])[0] is not None for i in range(1, len(bars)))
    assert hits == len(shifted)


def test_always_in_trend_enters_only_with_uptrend() -> None:
    c = AlwaysInTrend(H1Breakout())
    up = bar(1, 99, 100, 98, 99)  # régimen 110 > EMA100 95, sin ruptura
    assert c.evaluate_entry([up, up])[0] is not None
    down = replace(up, regime_close=90.0)
    assert c.evaluate_entry([down, down])[0] is None


def test_daily_regime_labels_follow_preregistered_rule() -> None:
    t0 = datetime(2020, 1, 1, tzinfo=UTC)

    def series(prices: list[float]) -> list[Candle]:
        out = []
        for i, p in enumerate(prices):
            o = t0 + timedelta(days=i)
            px = D(repr(p))
            out.append(
                Candle(
                    "X",
                    "1d",
                    o,
                    o + timedelta(days=1, milliseconds=-1),
                    px,
                    px * D("1.01"),
                    px * D("0.99"),
                    px,
                    D(1),
                    D(1),
                    1,
                    D(0),
                    D(0),
                )
            )
        return out

    rising = _daily_labels(series([100 * 1.002**i for i in range(300)]))
    falling = _daily_labels(series([100 * 0.998**i for i in range(300)]))
    assert list(rising.values())[-1][0] == "alcista"
    assert list(falling.values())[-1][0] == "bajista"
    assert all(v[1] > 0 for v in rising.values())  # ATR% positivo
