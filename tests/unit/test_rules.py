"""Reglas de entrada y salida de PLAN.md sobre snapshots sintéticos."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from bot.strategy.rules import (
    ExitReason,
    PositionPlan,
    evaluate_entry,
    initial_stop,
    on_candle_close,
    on_partial_filled,
)
from bot.strategy.snapshot import Regime
from tests.strategy_helpers import BASE, BULL, PARAMS, params, signal_window, snap


def test_good_window_produces_signal() -> None:
    sig, why = evaluate_entry(signal_window(), PARAMS)
    assert why == "ok" and sig is not None
    assert sig.limit_price == 101.0
    assert sig.stop == pytest.approx(99.5)  # 101 − 1,5 × ATR(1,0)
    assert sig.risk_per_unit == pytest.approx(1.5)
    assert sig.time == BASE + 3 * timedelta(minutes=15)


def _break(index: int, **changes: object) -> str:
    w = signal_window()
    w[index] = replace(w[index], **changes)  # type: ignore[arg-type]
    sig, why = evaluate_entry(w, PARAMS)
    assert sig is None
    return why


@pytest.mark.parametrize(
    ("index", "changes", "reason"),
    [
        # 1. Régimen (1 h)
        (3, {"regime": replace(BULL, close=94.0)}, "regimen_no_alcista"),
        (3, {"regime": replace(BULL, ema_fast=94.0)}, "regimen_no_alcista"),
        (3, {"regime": replace(BULL, adx=19.9)}, "adx_bajo"),
        # 2. Retroceso
        (1, {"low": 100.3}, "sin_toque_ema20"),  # 100,3 > 100 + 0,2×1
        (2, {"close": 97.9}, "cierre_bajo_ema50"),
        (3, {"rsi": 60.1}, "rsi_fuera_de_rango"),
        (3, {"rsi": 39.9}, "rsi_fuera_de_rango"),
        # 3. Gatillo
        (3, {"close": 100.8}, "sin_ruptura"),  # no supera el máximo anterior
        (3, {"close": 100.85, "ema_touch": 100.9}, "sin_ruptura"),  # bajo la EMA20
        (3, {"volume": 119.0}, "volumen_bajo"),  # < 1,2 × 100
        (3, {"macd_hist": -0.1}, "macd_no_sube"),
        # 4. Calidad
        (3, {"atr": 0.3}, "atr_fuera_de_rango"),  # 0,30% < 0,35%
        (3, {"atr": 3.1}, "atr_fuera_de_rango"),
        (3, {"rsi": None}, "indicadores_no_listos"),
    ],
)
def test_each_rule_can_veto(index: int, changes: dict[str, object], reason: str) -> None:
    assert _break(index, **changes) == reason


def test_touch_within_tolerance_counts() -> None:
    w = signal_window()
    w[1] = replace(w[1], low=100.19)  # a menos de 0,2 × ATR por encima de la EMA20
    w[2] = replace(w[2], low=100.25)
    assert evaluate_entry(w, PARAMS)[1] == "ok"


def test_touch_older_than_lookback_does_not_count() -> None:
    w = [snap(-1, 101, 101.5, 99.9, 101.0), *signal_window()]
    w[2] = replace(w[2], low=100.6)  # solo la vela fuera de la ventana de 3 tocó
    w[3] = replace(w[3], low=100.6)
    w[4] = replace(w[4], low=100.6)
    assert evaluate_entry(w, PARAMS)[1] == "sin_toque_ema20"


def test_entry_window_bogota() -> None:
    late = [replace(s, close_time=s.close_time + timedelta(hours=9)) for s in signal_window()]
    # 10:45 + 9 h = 19:45 hora Bogotá: fuera de 07:00–18:00
    assert evaluate_entry(late, PARAMS)[1] == "fuera_de_horario"
    assert evaluate_entry(late, params(entry_window=None))[1] == "ok"


def test_regime_not_ready_is_not_a_signal() -> None:
    w = signal_window()
    w[3] = replace(w[3], regime=Regime(BASE, 105.0, None, 95.0, 30.0))
    assert evaluate_entry(w, PARAMS)[1] == "indicadores_no_listos"


def test_initial_stop_capped_at_3_percent() -> None:
    assert initial_stop(100.0, 1.0, PARAMS) == pytest.approx(98.5)
    assert initial_stop(100.0, 3.0, PARAMS) == pytest.approx(97.0)  # 4,5% → tope 3%


# ------------------------------------------------------------------ salidas


def _plan() -> PositionPlan:
    return PositionPlan.open(100.0, 98.5, 0.001, BASE, PARAMS)


def test_plan_targets() -> None:
    p = _plan()
    assert p.r == pytest.approx(1.5)
    assert p.target == pytest.approx(102.25)  # +1,5R
    assert p.breakeven == pytest.approx(100.0 * 1.001 / 0.999)


def test_partial_moves_stop_to_breakeven_plus_fees() -> None:
    p = _plan()
    on_partial_filled(p)
    assert p.partial_done and p.stop == pytest.approx(p.breakeven) and p.stop > 100.0
    assert p.stop_kind is ExitReason.BREAKEVEN_STOP


def test_trailing_only_after_partial_and_only_up() -> None:
    p = _plan()
    assert on_candle_close(p, snap(1, 100, 104, 99.5, 103.5), PARAMS) is None
    assert p.stop == 98.5  # sin parcial no hay trailing
    on_partial_filled(p)
    on_candle_close(p, snap(2, 103.5, 105, 103, 104.5), PARAMS)
    assert p.stop == pytest.approx(105 - 2 * 1.0)
    on_candle_close(p, snap(3, 104.5, 104.6, 103.5, 104.0), PARAMS)
    assert p.stop == pytest.approx(103.0)  # no baja
    assert p.stop_kind is ExitReason.TRAILING_STOP


def test_close_below_ema20_exits_only_after_partial() -> None:
    p = _plan()
    below = snap(1, 100.5, 100.6, 99.5, 99.8)  # cierra bajo EMA20 = 100
    assert on_candle_close(p, below, PARAMS) is None
    on_partial_filled(p)
    assert on_candle_close(p, snap(2, 100.5, 100.6, 99.5, 99.8), PARAMS) is (
        ExitReason.CLOSE_BELOW_EMA
    )


def test_time_stop_at_8th_candle_if_half_r_not_reached() -> None:
    p = _plan()
    for i in range(1, 8):
        assert on_candle_close(p, snap(i, 100, 100.7, 99.5, 100.2), PARAMS) is None
    assert on_candle_close(p, snap(8, 100, 100.7, 99.5, 100.2), PARAMS) is ExitReason.TIME_STOP


def test_no_time_stop_if_half_r_reached() -> None:
    p = _plan()
    on_candle_close(p, snap(1, 100, 100.8, 99.5, 100.2), PARAMS)  # +0,53R
    for i in range(2, 12):
        assert on_candle_close(p, snap(i, 100, 100.5, 99.5, 100.2), PARAMS) is None


def test_max_holding_12h() -> None:
    p = _plan()
    p.reached_min_r = True
    assert on_candle_close(p, snap(47, 100, 101, 99.5, 100.5), PARAMS) is None
    assert on_candle_close(p, snap(48, 100, 101, 99.5, 100.5), PARAMS) is ExitReason.MAX_HOLDING
