"""Fase 4: OrderManager — idempotencia, stop en el exchange, regla 1 y regla 2."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

import pytest

from bot.config import EnvSettings, load_config
from bot.exchange.filters import SymbolFilters
from bot.exchange.trading import (
    BinanceSpotTrading,
    ExchangeRejectedError,
    TransientExchangeError,
)
from bot.execution.orders import OrderManager, StopPlacementError, client_id
from bot.modes import LiveTradingNotAllowedError
from bot.portfolio.portfolio import Portfolio
from bot.risk.manager import EntryIntent, RiskAuthorizationError, RiskDecision, RiskManager
from tests.conftest import ROOT
from tests.fake_exchange import FakeExchange

CFG = load_config(ROOT / "config").risk
SIGNAL_T = datetime(2026, 3, 2, 15, 59, 59, 999000, tzinfo=UTC)
INTENT = EntryIntent("SOLUSDT", D("94.96"), D("90.00"))


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def sol(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])


def setup(sol: SymbolFilters) -> tuple[FakeExchange, RiskManager, OrderManager, RiskDecision]:
    ex = FakeExchange({"SOLUSDT": D("94.96")})
    risk = RiskManager(CFG)
    om = OrderManager(ex, risk, {"SOLUSDT": sol}, D("0.001"), sleep=_no_sleep)
    decision = risk.approve_entry(INTENT, Portfolio(D(200), D("0.001")), sol, SIGNAL_T)
    assert decision.approved
    return ex, risk, om, decision


def test_client_ids_are_deterministic_and_valid() -> None:
    a = client_id("h1", "SOLUSDT", "S", SIGNAL_T, 3)
    assert a == client_id("h1", "SOLUSDT", "S", SIGNAL_T, 3) == "h1-SOLUSDT-S-2603021559-3"
    assert len(a) <= 36


async def test_entry_then_stop_on_exchange(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    pos = await om.open_position(decision, INTENT, SIGNAL_T)
    assert ex.sent == {"BUY:MARKET": 1, "SELL:STOP_LOSS": 1}
    assert pos.stop.status == "NEW" and pos.stop.stop_price == D("90.00")
    assert pos.qty == sol.round_qty(decision.qty * D("0.999"))  # neta de comisión
    assert [o.client_order_id for o in await ex.open_orders()] == [pos.stop.client_order_id]


async def test_forged_or_reused_approval_is_refused(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    forged = RiskDecision(True, D("1"), "ok", "SOLUSDT", "1:" + "0" * 64)
    with pytest.raises(RiskAuthorizationError):
        await om.open_position(forged, INTENT, SIGNAL_T)
    with pytest.raises(RiskAuthorizationError):  # cantidad alterada tras aprobar
        await om.open_position(replace(decision, qty=decision.qty * 10), INTENT, SIGNAL_T)
    with pytest.raises(RiskAuthorizationError):  # aprobación de otro RiskManager
        other = RiskManager(CFG).approve_entry(INTENT, Portfolio(D(200), D("0.001")), sol, SIGNAL_T)
        await om.open_position(other, INTENT, SIGNAL_T)
    assert ex.sent == {}
    await om.open_position(decision, INTENT, SIGNAL_T)
    with pytest.raises(RiskAuthorizationError):  # un solo uso
        await om.open_position(decision, INTENT, SIGNAL_T)
    assert ex.sent["BUY:MARKET"] == 1


async def test_kill_switch_blocks_execution_even_with_approval(sol: SymbolFilters) -> None:
    ex, risk, om, decision = setup(sol)
    risk.update(SIGNAL_T, D(179))
    with pytest.raises(RiskAuthorizationError):
        await om.open_position(decision, INTENT, SIGNAL_T)
    assert ex.sent == {}


async def test_lost_response_does_not_duplicate_order(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    ex.lose_response.add("market")  # la compra se ejecuta pero la respuesta se pierde
    pos = await om.open_position(decision, INTENT, SIGNAL_T)
    assert ex.sent["BUY:MARKET"] == 1 and pos.entry.status == "FILLED"


async def test_restart_does_not_duplicate_orders(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    await om.open_position(decision, INTENT, SIGNAL_T)
    # "Reinicio": nuevo proceso, nuevo RiskManager, misma señal procesada otra vez.
    risk2 = RiskManager(CFG)
    om2 = OrderManager(ex, risk2, {"SOLUSDT": sol}, D("0.001"), sleep=_no_sleep)
    d2 = risk2.approve_entry(INTENT, Portfolio(D(200), D("0.001")), sol, SIGNAL_T)
    pos2 = await om2.open_position(d2, INTENT, SIGNAL_T)
    assert ex.sent == {"BUY:MARKET": 1, "SELL:STOP_LOSS": 1}
    assert pos2.stop.client_order_id == client_id("h1", "SOLUSDT", "S", SIGNAL_T)


async def test_transient_errors_are_retried(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    ex.fail_next["stop"] = [TransientExchangeError("502"), TransientExchangeError("timeout")]
    ex.fail_next["get"] = [TransientExchangeError("red")]
    pos = await om.open_position(decision, INTENT, SIGNAL_T)
    assert pos.stop.status == "NEW" and ex.sent["SELL:STOP_LOSS"] == 1


async def test_position_closed_immediately_if_stop_cannot_be_placed(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    ex.reject_stops = True
    with pytest.raises(StopPlacementError):
        await om.open_position(decision, INTENT, SIGNAL_T)
    assert ex.sent == {"BUY:MARKET": 1, "SELL:MARKET": 1}  # comprada y vendida de inmediato
    assert await ex.open_orders() == []


async def test_trailing_moves_stop_and_detects_fill(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    pos = await om.open_position(decision, INTENT, SIGNAL_T)
    first = pos.stop.client_order_id
    assert await om.move_stop(pos, D("93.00")) is None
    assert ex.orders[first].status == "CANCELED"
    assert pos.stop.stop_price == D("93.00") and pos.stop.client_order_id.endswith("-1")
    assert [o.client_order_id for o in await ex.open_orders()] == [pos.stop.client_order_id]
    ex.move_price("SOLUSDT", D("92.50"))  # salta el stop en el exchange
    filled = await om.move_stop(pos, D("94.00"))
    assert filled is not None and filled.status == "FILLED"
    assert ex.sent["SELL:STOP_LOSS"] == 2  # no se coloca un stop nuevo sobre una posición cerrada


async def test_close_position_cancels_stop_or_reports_its_fill(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    pos = await om.open_position(decision, INTENT, SIGNAL_T)
    out = await om.close_position(pos, "trailing")
    assert out.side == "SELL" and out.type == "MARKET" and await ex.open_orders() == []
    ex2, _, om2, d2 = setup(sol)
    pos2 = await om2.open_position(d2, INTENT, SIGNAL_T)
    ex2.move_price("SOLUSDT", D("89"))
    out2 = await om2.close_position(pos2, "señal")
    assert out2.type == "STOP_LOSS" and ex2.sent.get("SELL:MARKET", 0) == 0


async def test_reconcile_replaces_missing_stop_and_flags_unknown_orders(sol: SymbolFilters) -> None:
    ex, _, om, decision = setup(sol)
    pos = await om.open_position(decision, INTENT, SIGNAL_T)
    await ex.cancel_order("SOLUSDT", pos.stop.client_order_id)  # alguien canceló el stop
    await ex.stop_loss_order("SOLUSDT", D("0.1"), D("80"), "h1-SOLUSDT-S-9999999999")
    report = await om.reconcile({"SOLUSDT": pos})
    assert not report.ok
    assert report.stops_replaced == ["SOLUSDT"]
    assert [o.client_order_id for o in report.unknown_orders] == ["h1-SOLUSDT-S-9999999999"]
    assert any(o.client_order_id == pos.stop.client_order_id for o in await ex.open_orders())
    clean = await om.reconcile({"SOLUSDT": pos})
    assert clean.stops_replaced == []


# ------------------------------------------------------------------ regla 1


def test_production_client_requires_live_mode() -> None:
    env = EnvSettings(_env_file=None, trading_mode="paper")  # type: ignore[call-arg]
    with pytest.raises(LiveTradingNotAllowedError):
        BinanceSpotTrading("https://api.binance.com", "k", "s", env)
    with pytest.raises(LiveTradingNotAllowedError):  # cualquier host que no sea testnet
        BinanceSpotTrading("https://otro-host.example.com", "k", "s", env)
    testnet = BinanceSpotTrading("https://testnet.binance.vision", "k", "s", env)
    assert not testnet.production


def test_exchange_rejection_is_not_retried_blindly() -> None:
    assert not issubclass(ExchangeRejectedError, TransientExchangeError)
