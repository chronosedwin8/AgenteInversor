"""Hipótesis de la Fase 2b: features, reglas H1/H2/H3 y órdenes a mercado en el motor."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from bot.backtest.engine import Backtester, ParamPeriod, SymbolData
from bot.data.klines import Candle
from bot.exchange.filters import SymbolFilters
from bot.strategy.features import Bar, PriorHigh, build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params, H2DipBuy, H2Params, H3Combined
from bot.strategy.rules import ExitReason
from tests.strategy_helpers import CFG
from tests.unit.test_indicators import CANDLES

H4 = timedelta(hours=4)
T0 = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)


def bar(i: int, o: float, h: float, low: float, c: float, **kw: Any) -> Bar:
    close_time = T0 + (i + 1) * H4 - timedelta(milliseconds=1)
    values: dict[str, Any] = {
        "volume": 10.0,
        "atr": 2.0,
        "rsi3": 50.0,
        "ema": {5: c},
        "donchian": {20: 100.0, 40: 105.0},
        "regime_close": 110.0,
        "regime_ema": {50: 100.0, 100: 95.0},
    }
    values.update(kw)
    return Bar(
        "SOLUSDT", close_time - H4 + timedelta(milliseconds=1), close_time, o, h, low, c, **values
    )


# ------------------------------------------------------------------ features


def test_prior_high_excludes_current_bar() -> None:
    ph = PriorHigh(3)
    assert [ph.update(x) for x in [1, 5, 2, 3, 9, 1]] == [None, None, None, 5, 5, 9]


def test_build_bars_regime_uses_last_closed_daily_bar() -> None:
    # 1 h como "señal" y velas diarias sintéticas a partir de las mismas horas.
    signal = CANDLES[:300]
    daily: list[Candle] = []
    for k in range(0, 288, 24):
        day = signal[k : k + 24]
        daily.append(
            Candle(
                "BTCUSDT",
                "1d",
                day[0].open_time,
                day[-1].close_time,
                day[0].open,
                max(c.high for c in day),
                min(c.low for c in day),
                day[-1].close,
                sum((c.volume for c in day), D(0)),
                D(0),
                0,
                D(0),
                D(0),
            )
        )
    bars = build_bars("BTCUSDT", signal, daily)
    for b in bars:
        closed = [d for d in daily if d.close_time <= b.close_time]
        expected = float(closed[-1].close) if closed else None
        assert b.regime_close == expected
    # Recortar el futuro no cambia el pasado.
    assert build_bars("BTCUSDT", signal[:200], daily)[:200] == bars[:200]


# ------------------------------------------------------------------ H1


def test_h1_signal_on_breakout_in_uptrend() -> None:
    s = H1Breakout()
    sig, why = s.evaluate_entry([bar(0, 99, 100, 98, 99), bar(1, 99, 102, 99, 101)])
    assert why == "ok" and sig is not None
    assert sig.order == "market" and sig.tag == "H1"
    assert sig.stop == pytest.approx(101 - 2 * 2.0)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"close": 100.0}, "sin_ruptura"),  # igual al máximo previo no es ruptura
        ({"regime_close": 94.0}, "regimen_no_alcista"),
        ({"regime_ema": {50: 100.0, 100: None}}, "indicadores_no_listos"),
        ({"donchian": {20: None, 40: 105.0}}, "indicadores_no_listos"),
    ],
)
def test_h1_vetoes(changes: dict[str, Any], reason: str) -> None:
    b = replace(bar(1, 99, 102, 99, 101), **changes)
    assert H1Breakout().evaluate_entry([bar(0, 99, 100, 98, 99), b]) == (None, reason)


def test_h1_regime_period_and_breakout_length_are_parameters() -> None:
    b = bar(1, 99, 106, 99, 103, regime_ema={50: 112.0, 100: 95.0})
    assert H1Breakout(H1Params(regime_ema=50)).evaluate_entry([b, b])[1] == "regimen_no_alcista"
    assert H1Breakout(H1Params(breakout_n=40)).evaluate_entry([b, b])[1] == "sin_ruptura"


def test_h1_stop_capped_at_10_percent() -> None:
    sig, _ = H1Breakout().evaluate_entry(
        [bar(0, 99, 100, 98, 99), bar(1, 99, 102, 99, 101, atr=8.0)]
    )
    assert sig is not None and sig.stop == pytest.approx(101 * 0.9)


def test_h1_trailing_stop_only_moves_up() -> None:
    s = H1Breakout()
    sig, _ = s.evaluate_entry([bar(0, 99, 100, 98, 99), bar(1, 99, 102, 99, 101)])
    assert sig is not None
    plan = s.open_plan(sig, 101.0, 97.0, 0.001, T0)
    assert s.on_candle_close(plan, bar(2, 101, 110, 100, 109)) is None
    assert plan.stop == pytest.approx(110 - 3 * 2.0)
    s.on_candle_close(plan, bar(3, 109, 109.5, 105, 106))
    assert plan.stop == pytest.approx(104.0)
    assert plan.stop_kind is ExitReason.TRAILING_STOP
    assert plan.target == float("inf")  # sin toma parcial


# ------------------------------------------------------------------ H2


def test_h2_buys_oversold_in_uptrend_and_exits_at_mean() -> None:
    s = H2DipBuy()
    sig, why = s.evaluate_entry([bar(0, 100, 101, 99, 100), bar(1, 100, 100, 95, 96, rsi3=12.0)])
    assert why == "ok" and sig is not None and sig.tag == "H2"
    assert sig.stop == pytest.approx(96 - 2.5 * 2.0)
    plan = s.open_plan(sig, 96.0, 91.0, 0.001, T0)
    assert s.on_candle_close(plan, bar(2, 96, 97, 95, 96.5, ema={5: 97.0})) is None
    assert s.on_candle_close(plan, bar(3, 96.5, 98, 96, 97.5, ema={5: 97.0})) is (
        ExitReason.MEAN_REVERTED
    )


def test_h2_vetoes_and_time_stop() -> None:
    s = H2DipBuy(H2Params(rsi_below=10.0, max_bars=6))
    b = bar(1, 100, 100, 95, 96, rsi3=12.0)
    assert s.evaluate_entry([b, b])[1] == "sin_sobreventa"
    b = replace(b, rsi3=5.0, regime_close=90.0)
    assert s.evaluate_entry([b, b])[1] == "regimen_no_alcista"
    plan = s.open_plan(
        replace(H2DipBuy().evaluate_entry([b, replace(b, regime_close=110.0)])[0]),  # type: ignore[type-var]
        96.0,
        91.0,
        0.001,
        T0,
    )
    for i in range(5):
        assert s.on_candle_close(plan, bar(2 + i, 96, 96.5, 95, 95.5, ema={5: 97.0})) is None
    assert (
        s.on_candle_close(plan, bar(7, 96, 96.5, 95, 95.5, ema={5: 97.0})) is ExitReason.TIME_STOP
    )


# ------------------------------------------------------------------ H3


def test_h3_prefers_h1_and_routes_exits_by_tag() -> None:
    s = H3Combined(H1Breakout(), H2DipBuy())
    breakout_and_oversold = bar(1, 99, 102, 99, 101, rsi3=5.0)
    sig, _ = s.evaluate_entry([bar(0, 99, 100, 98, 99), breakout_and_oversold])
    assert sig is not None and sig.tag == "H1"
    sig2, _ = s.evaluate_entry([bar(0, 99, 100, 98, 99), bar(1, 100, 100, 95, 96, rsi3=5.0)])
    assert sig2 is not None and sig2.tag == "H2"
    plan = s.open_plan(sig2, 96.0, 91.0, 0.001, T0)
    assert plan.tag == "H2"
    assert s.on_candle_close(plan, bar(2, 96, 98, 96, 97.5, ema={5: 97.0})) is (
        ExitReason.MEAN_REVERTED
    )
    _, why = s.evaluate_entry([bar(0, 99, 100, 98, 99), bar(1, 99, 99.5, 98, 99)])
    assert why == "sin_ruptura|sin_sobreventa"


# ------------------------------------------------------------------ motor: órdenes a mercado


@pytest.fixture
def filters(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])


def _run(bars: list[Bar], filters: SymbolFilters) -> Any:
    bt = Backtester(
        {"SOLUSDT": SymbolData("SOLUSDT", bars, filters)}, CFG.risk, D("0.001"), D("0.0005")
    )
    start = bars[0].close_time
    return bt.run([ParamPeriod(start, H1Breakout())], start, bars[-1].close_time + H4)


def test_market_entry_fills_at_next_open_with_slippage(filters: SymbolFilters) -> None:
    bars = [
        bar(0, 99, 100, 98, 99),
        bar(1, 99, 102, 99, 101),
        bar(2, 101.5, 103.5, 101, 102),
        bar(3, 102, 102.5, 96, 96.5),
    ]
    (t,) = _run(bars, filters).trades
    assert t.entry_price == D("101.5") * D("1.0005")  # apertura de la vela siguiente
    assert t.opened_at == bars[2].close_time
    # Trailing tras la vela 2: 103,5 − 3×2 = 97,5 (> stop inicial 97): sale en 97,5.
    assert t.exits == (ExitReason.TRAILING_STOP,)
    assert t.avg_exit_price == D("97.5") * D("0.9995")


def test_market_entry_gapping_below_stop_exits_immediately(filters: SymbolFilters) -> None:
    bars = [bar(0, 99, 100, 98, 99), bar(1, 99, 102, 99, 101), bar(2, 95.0, 96, 94, 95)]
    (t,) = _run(bars, filters).trades
    assert t.exits == (ExitReason.STOP,) and t.pnl < 0
    assert t.avg_exit_price == D(95) * D("0.9995")


def test_features_cover_every_parameter_used_by_audit_and_grids() -> None:
    """Regresión: la sensibilidad de la auditoría usaba periodos que no se calculaban."""
    from bot.strategy.features import DONCHIAN, REGIME_EMAS, FeatureBuilder

    assert {50, 75, 100, 150, 200} <= set(REGIME_EMAS)
    assert {10, 15, 20, 30, 40, 60} <= set(DONCHIAN)
    fb = FeatureBuilder("BTCUSDT")
    day0 = datetime(2020, 1, 1, tzinfo=UTC)
    for i, c in enumerate(CANDLES[:250]):  # 250 diarias consecutivas
        o = day0 + timedelta(days=i)
        fb.on_regime_candle(
            replace(
                c, interval="1d", open_time=o, close_time=o + timedelta(days=1, milliseconds=-1)
            )
        )
    start = day0 + timedelta(days=250)
    last = None
    for i, c in enumerate(CANDLES[:70]):  # 70 velas de señal
        o = start + i * H4
        last = fb.on_signal_candle(
            replace(c, interval="4h", open_time=o, close_time=o + H4 - timedelta(milliseconds=1))
        )
    assert last is not None
    assert all(last.regime_ema[n] is not None for n in REGIME_EMAS)
    assert all(last.donchian[n] is not None for n in DONCHIAN)
