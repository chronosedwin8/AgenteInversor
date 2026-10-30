"""Fase 3: RiskManager completo. Escenarios del kill switch obligatorios (CLAUDE.md, regla 7).

Criterio de la fase: el kill switch se activa exactamente en 180 USD y nada lo evita.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from bot.backtest.engine import Backtester, ParamPeriod, SymbolData
from bot.config import load_config
from bot.exchange.filters import SymbolFilters
from bot.portfolio.portfolio import Portfolio
from bot.risk.manager import (
    RESET_CONFIRMATION,
    EntryIntent,
    RiskEventKind,
    RiskManager,
    RiskState,
)
from bot.strategy.hypotheses import H1Breakout
from tests.conftest import ROOT
from tests.unit.test_hypotheses import H4, bar

CFG = load_config(ROOT / "config").risk
# 15:00 UTC = 10:00 Bogotá
NOW = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
INTENT = EntryIntent("SOLUSDT", D(100), D("98.5"))


@pytest.fixture
def sol(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])


def kinds(events: list[Any]) -> list[RiskEventKind]:
    return [e.kind for e in events]


def fresh(cash: str = "200") -> tuple[RiskManager, Portfolio]:
    return RiskManager(CFG), Portfolio(D(cash), D("0.001"))


# ------------------------------------------------------------------ kill switch (obligatorios)


def test_kill_switch_triggers_exactly_at_180() -> None:
    rm, _ = fresh()
    assert rm.kill_switch_capital() == D(180)
    assert rm.update(NOW, D("180.01")) == [] or not rm.kill_switch_active
    assert not rm.kill_switch_active
    events = rm.update(NOW + timedelta(minutes=1), D("180.00"))
    assert RiskEventKind.KILL_SWITCH in kinds(events)
    assert rm.kill_switch_active
    assert rm.state.kill_switch_equity == D("180.00")


def test_kill_switch_after_losing_streak(sol: SymbolFilters) -> None:
    """Racha de pérdidas: el capital baja operación a operación hasta cruzar 180."""
    rm, pf = fresh()
    equity = D(200)
    t = NOW
    trace = []
    for _ in range(40):
        t += timedelta(days=2)  # evita pausas y límites diarios
        rm.update(t, equity)
        if rm.kill_switch_active:
            break
        assert rm.approve_entry(INTENT, pf, sol, t).approved
        loss = D("0.6")
        equity -= loss
        rm.on_trade_closed(-loss, t, equity)
        trace.append(equity)
        rm.update(t, equity)
    assert rm.kill_switch_active
    assert rm.state.kill_switch_equity is not None and rm.state.kill_switch_equity <= D(180)
    assert all(e > D(180) for e in trace[:-1])  # no se activó antes de cruzar
    assert rm.approve_entry(INTENT, pf, sol, t + timedelta(days=5)).reason == "kill_switch_activo"


def test_kill_switch_on_10_percent_drop() -> None:
    rm, _ = fresh()
    rm.update(NOW, D(200))
    for v in ["195", "190", "185", "181", "180.5"]:
        rm.update(NOW, D(v))
        assert not rm.kill_switch_active
    rm.update(NOW, D("179.99"))
    assert rm.kill_switch_active


def test_kill_switch_on_price_gap() -> None:
    """Desfase de precio: el capital salta de 186 a 160 sin pasar por 180."""
    rm, _ = fresh()
    rm.update(NOW, D(186))
    events = rm.update(NOW + timedelta(seconds=1), D(160))
    assert RiskEventKind.KILL_SWITCH in kinds(events)
    assert rm.state.kill_switch_equity == D(160)


def test_nothing_bypasses_kill_switch(sol: SymbolFilters) -> None:
    rm, _ = fresh()
    rm.update(NOW, D(179))
    # Ni con capital recuperado, ni otro día, ni sin posiciones, ni otro par.
    later = NOW + timedelta(days=30)
    rm.update(later, D(250))
    assert rm.kill_switch_active
    for sym in ("BTCUSDT", "SOLUSDT"):
        d = rm.approve_entry(replace(INTENT, symbol=sym), Portfolio(D(250), D("0.001")), sol, later)
        assert not d.approved and d.reason == "kill_switch_activo"


def test_kill_switch_survives_restart() -> None:
    rm, _ = fresh()
    rm.update(NOW, D(175))
    restored = RiskManager(CFG, state=RiskState.from_json(rm.state.to_json()))
    assert restored.kill_switch_active
    assert restored.state.kill_switch_equity == D(175)


def test_kill_switch_reset_is_manual_only() -> None:
    rm, _ = fresh()
    rm.update(NOW, D(175))
    with pytest.raises(PermissionError):
        rm.reset_kill_switch("si", NOW, D(200))
    with pytest.raises(PermissionError):  # capital aún bajo el umbral
        rm.reset_kill_switch(RESET_CONFIRMATION, NOW, D(179))
    ev = rm.reset_kill_switch(RESET_CONFIRMATION, NOW, D(200))
    assert ev.kind is RiskEventKind.KILL_SWITCH_RESET and not rm.kill_switch_active


def test_repo_config_uses_peak_basis() -> None:
    assert CFG.kill_switch_basis == "peak"  # decisión de Edwin, 2026-10-30


def test_kill_switch_peak_basis_option() -> None:
    rm = RiskManager(CFG.model_copy(update={"kill_switch_basis": "peak"}))
    rm.update(NOW, D(250))
    assert rm.kill_switch_capital() == D(225)
    rm.update(NOW, D("225.01"))
    assert not rm.kill_switch_active
    rm.update(NOW, D(225))
    assert rm.kill_switch_active


def test_peak_basis_never_below_initial_threshold() -> None:
    rm = RiskManager(CFG.model_copy(update={"kill_switch_basis": "peak"}))
    rm.update(NOW, D(195))  # sin ganancias: el máximo de referencia sigue siendo 200
    assert rm.kill_switch_capital() == D(180)


def test_initial_basis_ignores_peak() -> None:
    rm = RiskManager(CFG.model_copy(update={"kill_switch_basis": "initial"}))
    rm.update(NOW, D(250))
    rm.update(NOW, D(200))
    assert rm.kill_switch_capital() == D(180) and not rm.kill_switch_active


def test_manual_reset_with_peak_basis_resets_reference() -> None:
    rm = RiskManager(CFG.model_copy(update={"kill_switch_basis": "peak"}))
    rm.update(NOW, D(250))
    rm.update(NOW, D(224))
    assert rm.kill_switch_active
    rm.reset_kill_switch(RESET_CONFIRMATION, NOW, D(224))
    assert not rm.kill_switch_active and rm.kill_switch_capital() == D("201.6")
    rm.update(NOW, D(210))
    assert not rm.kill_switch_active


def test_engine_kill_switch_closes_everything_and_stops_trading(
    symbol_info: dict[str, dict[str, Any]],
) -> None:
    """En el motor: un hueco a la baja lleva el capital bajo 180 → se cierra todo y no se
    abre nada más aunque la estrategia siga dando señales."""
    f = SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])
    # Capital 190 → riesgo y tope de posición suficientes para que un hueco de −40% cruce 180.
    cfg = CFG.model_copy(update={"initial_capital_usdt": D(200), "max_position_pct": D(40)})
    # ATR 0,5 en la señal → stop 100 (cercano) → la posición llega al tope del 40% (~76 USDT).
    up = [bar(0, 99, 100, 98, 99), bar(1, 99, 102, 99, 101, atr=0.5), bar(2, 101, 103, 101, 102)]
    crash = [bar(3, 60, 61, 59, 60, atr=0.1)]  # hueco: stop en ~97 se llena a la apertura (60)
    again = [bar(4, 60, 70, 60, 69, atr=0.1, donchian={20: 61.0, 40: 61.0}),
             bar(5, 69, 80, 69, 79, atr=0.1, donchian={20: 70.0, 40: 70.0})]  # fmt: skip
    bars = up + crash + again
    bt = Backtester({"SOLUSDT": SymbolData("SOLUSDT", bars, f)}, cfg, D("0.001"), D("0.0005"))
    res = bt.run([ParamPeriod(bars[0].close_time, H1Breakout())], bars[0].close_time,
                 bars[-1].close_time + H4, initial_capital=D(190))  # fmt: skip
    assert res.kill_switch_at == bars[3].close_time
    assert RiskEventKind.KILL_SWITCH in kinds(res.risk_events)
    assert len(res.trades) == 1  # nada nuevo tras el kill switch pese a las rupturas
    assert res.rejected.get("kill_switch_activo", 0) >= 1


# ------------------------------------------------------------------ resto de reglas


def test_drawdown_alert_halves_risk(sol: SymbolFilters) -> None:
    rm = RiskManager(CFG)
    events = rm.update(NOW, D(188))
    assert RiskEventKind.DRAWDOWN_ALERT in kinds(events)
    assert rm.current_risk_pct(D(188)) == D("0.25")
    assert rm.current_risk_pct(D("188.01")) == D("0.5")
    d = rm.approve_entry(INTENT, Portfolio(D(188), D("0.001")), sol, NOW)
    normal = RiskManager(CFG).approve_entry(INTENT, Portfolio(D(189), D("0.001")), sol, NOW)
    assert d.qty < normal.qty


def test_max_four_new_trades_per_bogota_day(sol: SymbolFilters) -> None:
    rm = RiskManager(CFG)
    for i in range(4):
        pf = Portfolio(D(200), D("0.001"))
        assert rm.approve_entry(replace(INTENT, symbol=f"X{i}"), pf, sol, NOW).approved
    assert rm.approve_entry(INTENT, Portfolio(D(200), D("0.001")), sol, NOW).reason == (
        "maximo_de_operaciones_diarias"
    )
    # 23:59 Bogotá del mismo día = 04:59 UTC del día siguiente: sigue bloqueado.
    late = datetime(2026, 3, 3, 4, 59, tzinfo=UTC)
    assert not rm.approve_entry(INTENT, Portfolio(D(200), D("0.001")), sol, late).approved
    next_day = datetime(2026, 3, 3, 5, 1, tzinfo=UTC)  # 00:01 Bogotá
    rm.update(next_day, D(200))
    assert rm.approve_entry(INTENT, Portfolio(D(200), D("0.001")), sol, next_day).approved


def test_daily_loss_limit_blocks_until_bogota_midnight(sol: SymbolFilters) -> None:
    rm = RiskManager(CFG)
    rm.update(NOW, D(200))
    assert rm.update(NOW + timedelta(hours=1), D("196.01")) == []
    ev = rm.update(NOW + timedelta(hours=2), D(196))  # −2%
    assert RiskEventKind.DAILY_LOSS_LIMIT in kinds(ev)
    pf = Portfolio(D(196), D("0.001"))
    assert rm.approve_entry(INTENT, pf, sol, NOW + timedelta(hours=3)).reason == (
        "limite_de_perdida_diaria"
    )
    midnight = datetime(2026, 3, 3, 5, 0, tzinfo=UTC)  # 00:00 Bogotá
    rm.update(midnight, D(196))
    assert rm.approve_entry(INTENT, pf, sol, midnight).approved


def test_three_consecutive_losses_pause_24h(sol: SymbolFilters) -> None:
    rm = RiskManager(CFG)
    rm.update(NOW, D(200))
    rm.on_trade_closed(D(-1), NOW, D(199))
    rm.on_trade_closed(D(2), NOW, D(201))  # una ganancia reinicia la racha
    for _ in range(2):
        assert rm.on_trade_closed(D(-1), NOW, D(199)) == []
    ev = rm.on_trade_closed(D(-1), NOW, D(198))
    assert RiskEventKind.LOSS_STREAK_PAUSE in kinds(ev)
    pf = Portfolio(D(198), D("0.001"))
    assert rm.approve_entry(INTENT, pf, sol, NOW + timedelta(hours=23)).reason == (
        "pausa_por_perdidas_consecutivas"
    )
    t = NOW + timedelta(hours=24, minutes=1)
    rm.update(t, D(198))
    assert rm.approve_entry(INTENT, pf, sol, t).approved


def test_total_exposure_cap(sol: SymbolFilters) -> None:
    rm = RiskManager(CFG.model_copy(update={"max_open_positions": 5}))
    pf = Portfolio(D(200), D("0.001"))
    pf.reserve("A", D(80))
    pf.reserve("B", D(79))
    d = rm.approve_entry(INTENT, pf, sol, NOW)  # solo queda ~1 USDT de margen: bajo el mínimo
    assert not d.approved and d.reason == "por_debajo_del_minimo"
    pf.reserve("C", D(1))
    assert rm.approve_entry(INTENT, pf, sol, NOW).reason == "exposicion_total_maxima"


def test_bstocks_limited_to_one(sol: SymbolFilters) -> None:
    rm = RiskManager(CFG, bstock_symbols=frozenset({"NVDABUSDT", "TSLABUSDT"}))
    pf = Portfolio(D(200), D("0.001"))
    pf.reserve("NVDABUSDT", D(20))
    d = rm.approve_entry(replace(INTENT, symbol="TSLABUSDT"), pf, sol, NOW)
    assert d.reason == "maximo_de_posiciones_bstocks"
    assert rm.approve_entry(INTENT, pf, sol, NOW).approved


def test_state_roundtrip() -> None:
    rm = RiskManager(CFG)
    rm.update(NOW, D(200))
    rm.on_trade_closed(D(-1), NOW, D(199))
    s = RiskState.from_json(rm.state.to_json())
    assert s.day == rm.state.day and s.day_start_equity == D(200) and s.consecutive_losses == 1
