"""Escenarios del motor de backtest con snapshots sintéticos (entrada 101, stop 99,5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from bot.backtest.engine import Backtester, BacktestResult, ParamPeriod, SymbolData
from bot.backtest.metrics import compute_metrics, max_drawdown_pct
from bot.exchange.filters import SymbolFilters
from bot.strategy.rules import ExitReason
from bot.strategy.snapshot import Snapshot
from tests.strategy_helpers import BASE, CFG, M15, PARAMS, signal_window, snap

FEE, SLIP = D("0.001"), D("0.0005")


@pytest.fixture
def filters(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])


def run(snaps: list[Snapshot], filters: SymbolFilters, **extra: list[Snapshot]) -> BacktestResult:
    data = {"SOLUSDT": SymbolData("SOLUSDT", snaps, filters)}
    for sym, s in extra.items():
        data[sym] = SymbolData(sym, s, filters)
    bt = Backtester(data, CFG.risk, FEE, SLIP)
    start = snaps[0].close_time
    return bt.run([ParamPeriod(start, PARAMS)], start, snaps[-1].close_time + M15)


def fill_candle(i: int = 4) -> Snapshot:
    return snap(i, 101.0, 101.2, 100.9, 101.1)  # mínimo 100,9 < límite 101 → se llena


def test_stop_loss_costs_about_one_r(filters: SymbolFilters) -> None:
    snaps = [*signal_window(), fill_candle(), snap(5, 100.5, 100.6, 99.0, 99.2)]
    res = run(snaps, filters)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exits == (ExitReason.STOP,)
    assert t.qty == D("0.588")
    assert D("-1.10") < t.pnl < D("-0.95")  # ≈ −0,5% de 200 con costos y deslizamiento
    assert t.r_multiple == pytest.approx(D(-1), abs=D("0.08"))
    assert t.opened_at == BASE + 4 * M15


def test_partial_then_trailing_exit_is_profitable(filters: SymbolFilters) -> None:
    snaps = [
        *signal_window(),
        fill_candle(),
        snap(5, 101.5, 103.5, 101.5, 103.2),  # toca +1,5R (≈103,38) → parcial; trailing 101,5
        snap(6, 103.2, 105.0, 103.1, 104.8),  # trailing → 103,0
        snap(7, 104.0, 104.2, 102.9, 103.1),  # toca 103,0 → cierre del resto
    ]
    res = run(snaps, filters)
    (t,) = res.trades
    assert t.exits == ("parcial", ExitReason.TRAILING_STOP)
    assert t.pnl > 0
    assert t.r_multiple > 1


def test_unfilled_limit_is_cancelled_after_two_candles(filters: SymbolFilters) -> None:
    snaps = [
        *signal_window(),
        snap(4, 101.1, 101.6, 101.0, 101.5),  # mínimo = límite: no cruza → no se llena
        snap(5, 101.5, 102.0, 101.2, 101.8),
        snap(6, 101.8, 102.0, 100.0, 100.5),  # ya cancelada
    ]
    res = run(snaps, filters)
    assert res.trades == [] and res.unfilled == 1


def test_gap_below_stop_fills_at_open(filters: SymbolFilters) -> None:
    snaps = [*signal_window(), fill_candle(), snap(5, 98.0, 98.2, 97.5, 97.8)]
    (t,) = run(snaps, filters).trades
    assert t.avg_exit_price == D("98.0") * (1 - SLIP)


def test_stop_assumed_before_target_in_same_candle(filters: SymbolFilters) -> None:
    snaps = [*signal_window(), fill_candle(), snap(5, 101.0, 104.0, 99.0, 103.0)]
    (t,) = run(snaps, filters).trades
    assert t.exits == (ExitReason.STOP,)


def test_stop_on_fill_candle(filters: SymbolFilters) -> None:
    snaps = [*signal_window(), snap(4, 101.0, 101.2, 99.0, 99.3)]
    (t,) = run(snaps, filters).trades
    assert t.exits == (ExitReason.STOP,) and t.opened_at == t.closed_at


def test_time_stop(filters: SymbolFilters) -> None:
    flat = [snap(i, 101.0, 101.5, 100.8, 101.2) for i in range(5, 12)]
    snaps = [*signal_window(), fill_candle(), *flat, snap(12, 101, 101.5, 100.8, 101.2)]
    (t,) = run(snaps, filters).trades
    assert t.exits == (ExitReason.TIME_STOP,)
    assert t.closed_at - t.opened_at == 7 * M15  # 8.ª vela contando la del llenado


def test_max_two_positions_across_symbols(filters: SymbolFilters) -> None:
    def series(sym: str) -> list[Snapshot]:
        w = [replace(s, symbol=sym) for s in signal_window()]
        return [
            *w,
            replace(fill_candle(), symbol=sym),
            replace(snap(5, 100.5, 100.6, 99.0, 99.2), symbol=sym),
        ]

    res = run(series("SOLUSDT"), filters, BTCUSDT=series("BTCUSDT"), ETHUSDT=series("ETHUSDT"))
    assert res.signals == 3
    assert res.rejected == {"maximo_de_posiciones": 1}
    assert len(res.trades) == 2


def test_no_entries_after_end_but_open_positions_are_managed(filters: SymbolFilters) -> None:
    snaps = [*signal_window(), fill_candle(), snap(5, 100.5, 100.6, 99.0, 99.2)]
    data = {"SOLUSDT": SymbolData("SOLUSDT", snaps, filters)}
    bt = Backtester(data, CFG.risk, FEE, SLIP)
    start = snaps[0].close_time
    # El periodo termina justo después de la señal: la posición se llena y se gestiona igual.
    res = bt.run([ParamPeriod(start, PARAMS)], start, snaps[3].close_time + timedelta(seconds=1))
    assert len(res.trades) == 1
    res2 = bt.run([ParamPeriod(start, PARAMS)], start, snaps[3].close_time)  # señal fuera
    assert res2.trades == [] and res2.signals == 0


def test_future_data_does_not_change_past_decisions(filters: SymbolFilters) -> None:
    base = [*signal_window(), fill_candle(), snap(5, 100.5, 100.6, 99.0, 99.2)]
    future_a = [snap(6, 99, 99.5, 98, 99), snap(7, 99, 99.2, 98.5, 99.0)]
    future_b = [snap(6, 99, 120, 98, 119), snap(7, 119, 130, 118, 129)]
    a = run(base + future_a, filters).trades
    b = run(base + future_b, filters).trades
    assert a == b


def test_equity_and_drawdown(filters: SymbolFilters) -> None:
    snaps = [*signal_window(), fill_candle(), snap(5, 100.5, 100.6, 99.0, 99.2)]
    res = run(snaps, filters)
    m = compute_metrics(res.trades, res.equity, res.initial_capital)
    assert m.trades == 1 and m.profit_factor == 0.0
    assert res.equity[-1][1] == D(200) + res.trades[0].pnl
    assert 0.4 < m.max_drawdown_pct < 0.6
    assert max_drawdown_pct([(BASE, D(100)), (BASE, D(120)), (BASE, D(90)), (BASE, D(130))]) == 25.0
