"""Indicadores incrementales comparados contra TA-Lib (referencia independiente)."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
import talib

from bot.data.vision import parse_kline_csv
from bot.strategy.indicators import ADX, ATR, EMA, MACD, RSI, SMA
from tests.conftest import FIXTURES

CANDLES = parse_kline_csv((FIXTURES / "BTCUSDT-1h-1000.csv").read_bytes(), "BTCUSDT", "1h")
HIGH = np.array([float(c.high) for c in CANDLES])
LOW = np.array([float(c.low) for c in CANDLES])
CLOSE = np.array([float(c.close) for c in CANDLES])


def _assert_series(
    ours: list[float | None], ref: np.ndarray, start: int = 0, rel: float = 1e-9
) -> None:
    assert len(ours) == len(ref)
    first_ref = int(np.argmax(~np.isnan(ref)))
    first_ours = next(i for i, v in enumerate(ours) if v is not None)
    if start == 0:  # mismo arranque que TA-Lib
        assert first_ours == first_ref
    for i in range(max(first_ref, start), len(ref)):
        v = ours[i]
        assert v is not None
        assert math.isclose(v, ref[i], rel_tol=rel, abs_tol=1e-9), (i, v, ref[i])


def test_fixture_is_real_and_contiguous() -> None:
    assert len(CANDLES) == 1000
    steps = {(b.open_time - a.open_time).total_seconds() for a, b in itertools.pairwise(CANDLES)}
    assert steps == {3600.0}


@pytest.mark.parametrize("period", [20, 50, 200])
def test_ema_matches_talib(period: int) -> None:
    ema = EMA(period)
    _assert_series([ema.update(x) for x in CLOSE], talib.EMA(CLOSE, timeperiod=period))


def test_sma_matches_talib() -> None:
    sma = SMA(20)
    _assert_series([sma.update(x) for x in CLOSE], talib.SMA(CLOSE, timeperiod=20))


def test_rsi_matches_talib() -> None:
    rsi = RSI(14)
    _assert_series([rsi.update(x) for x in CLOSE], talib.RSI(CLOSE, timeperiod=14))


def test_atr_matches_talib() -> None:
    atr = ATR(14)
    ours = [atr.update(h, lo, c) for h, lo, c in zip(HIGH, LOW, CLOSE, strict=True)]
    _assert_series(ours, talib.ATR(HIGH, LOW, CLOSE, timeperiod=14))


def test_adx_matches_talib() -> None:
    adx = ADX(14)
    ours = [adx.update(h, lo, c) for h, lo, c in zip(HIGH, LOW, CLOSE, strict=True)]
    _assert_series(ours, talib.ADX(HIGH, LOW, CLOSE, timeperiod=14))


def test_macd_hist_matches_talib_after_warmup() -> None:
    # TA-Lib alinea la semilla de la EMA rápida con la lenta; la diferencia se extingue rápido.
    macd = MACD(12, 26, 9)
    ours = [v.hist if (v := macd.update(x)) else None for x in CLOSE]
    _, _, ref = talib.MACD(CLOSE, fastperiod=12, slowperiod=26, signalperiod=9)
    _assert_series(ours, ref, start=300, rel=1e-6)


def test_rsi_textbook_example() -> None:
    # Datos del ejemplo clásico de Wilder (StockCharts). Su tabla publica 70,53 porque redondea
    # los promedios intermedios; el cálculo exacto (igual que TA-Lib) da 70,46.
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89,
              46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]  # fmt: skip
    rsi = RSI(14)
    values = [rsi.update(c) for c in closes]
    assert values[13] is None
    assert [round(v, 2) for v in values[14:] if v is not None] == [
        70.46, 66.25, 66.48, 69.35, 66.29, 57.92
    ]  # fmt: skip


def test_indicators_return_none_until_warm() -> None:
    assert EMA(3).update(1.0) is None
    assert ATR(14).update(1, 1, 1) is None
    assert ADX(14).update(1, 1, 1) is None
    assert MACD().update(1.0) is None
