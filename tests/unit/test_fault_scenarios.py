"""Escenarios de fallo exigidos (docs/fase-testnet-plan.md §4), con fallos inyectados."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from bot.data.klines import Candle
from bot.exchange.filters import SymbolFilters
from bot.exchange.trading import TransientExchangeError
from bot.execution.live import AnotherInstanceError, ExchangeTrader
from bot.execution.orders import client_id
from bot.portfolio.portfolio import Portfolio
from bot.risk.manager import EntryIntent
from tests.fake_exchange import FakeExchange
from tests.fakes import MemorySessionStore, MemoryStore
from tests.unit.test_exchange_trader import _trader
from tests.unit.test_paper import DAY, T0, Clock, _load, _series


@pytest.fixture
def btc(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])


class World:
    """Datos reales del fixture, un exchange falso y un ejecutor, avanzables vela a vela."""

    def __init__(self, btc: SymbolFilters):
        self.four, self.daily = _series("BTCUSDT")
        self.store, self.sessions = MemoryStore(), MemorySessionStore()
        self.clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
        self.ex = FakeExchange({"BTCUSDT": self.four[599].close}, self.clock)
        self.btc = btc
        self.bars = [c for c in self.four if c.open_time >= T0 + 100 * DAY]
        self.k = 0
        self.trader: ExchangeTrader | None = None

    async def boot(self) -> ExchangeTrader:
        await _load(self.store, self.four, self.daily, self.clock.t)
        self.sessions.locks.clear()  # cada arranque simula un proceso nuevo
        self.trader = _trader(self.store, self.sessions, self.ex, self.btc, self.clock)
        await self.trader.start()
        return self.trader

    async def advance(self, n: int = 1) -> Candle:
        assert self.trader is not None
        c = self.bars[self.k]
        for _ in range(n):
            c = self.bars[self.k]
            self.k += 1
            self.ex.prices["BTCUSDT"] = c.close
            self.clock.t = c.close_time + timedelta(seconds=1)
            await _load(self.store, self.four, self.daily, self.clock.t)
            await self.trader.reconcile()
            if self.trader.exchange_ok:
                await self.trader.step()
        return c

    async def until_position(self, limit: int = 300) -> None:
        assert self.trader is not None
        for _ in range(limit):
            await self.advance()
            if self.trader.positions:
                return
        raise AssertionError("no se abrió ninguna posición")


async def test_exchange_down_sends_no_orders_until_reconciled(btc: SymbolFilters) -> None:
    w = World(btc)
    w.ex.down = True
    t = await w.boot()
    assert not t.exchange_ok and not t.can_open
    await w.advance(120)
    assert w.ex.sent == {}  # nada enviado mientras no responde
    w.ex.down = False
    await w.until_position()
    assert t.exchange_ok and w.ex.sent["BUY:MARKET"] == 1


async def test_timeout_after_send_is_checked_before_retry(btc: SymbolFilters) -> None:
    w = World(btc)
    await w.boot()
    w.ex.lose_response.add("market")
    await w.until_position()
    assert w.ex.sent["BUY:MARKET"] == 1


async def test_rejected_stop_closes_and_verifies(btc: SymbolFilters) -> None:
    w = World(btc)
    t = await w.boot()
    w.ex.reject_stops = True
    for _ in range(300):
        await w.advance()
        if w.ex.sent.get("BUY:MARKET"):
            break
    assert w.ex.sent == {"BUY:MARKET": 1, "SELL:MARKET": 1}
    assert t.positions == {} and t.emergencies == {}
    assert len(t.pf.trades) == 1 and t.pf.trades[0].exits == ("stop_rechazado_cerrada",)


async def test_rejected_stop_and_unconfirmed_sale_becomes_emergency(btc: SymbolFilters) -> None:
    w = World(btc)
    t = await w.boot()
    w.ex.reject_stops = True
    w.ex.sells_not_filled = True  # la venta se acepta pero nunca se ejecuta
    for _ in range(300):
        await w.advance()
        if w.ex.sent.get("BUY:MARKET"):
            break
    assert "BTCUSDT" in t.emergencies and "BTCUSDT" in t.positions
    assert not t.can_open and t.paused_reason is not None
    assert t.pf.trades == []  # una orden aceptada NO es una posición cerrada
    # El exchange vuelve a ejecutar: el reintento usa el MISMO ID (sin segunda venta).
    w.ex.sells_not_filled = False
    xid = client_id("h1", "BTCUSDT", "X", t.positions["BTCUSDT"][0].signal_time)
    o = w.ex.orders[xid]
    w.ex.orders[xid] = replace(o, status="FILLED", executed_qty=o.orig_qty,
                               quote_qty=o.orig_qty * w.ex.prices["BTCUSDT"])  # fmt: skip
    await t.reconcile()
    assert t.positions == {} and t.emergencies == {}
    assert w.ex.sent["SELL:MARKET"] == 1


async def test_incomplete_api_data_pauses(btc: SymbolFilters) -> None:
    w = World(btc)
    t = await w.boot()
    await w.until_position()
    w.ex.corrupt_reads = True
    await t.reconcile()
    assert not t.exchange_ok
    assert t.paused_reason is not None and "incompletos" in t.paused_reason
    sent = dict(w.ex.sent)
    await w.advance(30)
    assert w.ex.sent.get("BUY:MARKET") == sent.get("BUY:MARKET")  # sin entradas nuevas


async def test_restart_mid_execution_rebuilds_from_exchange(btc: SymbolFilters) -> None:
    """Caída entre la compra (con su stop) y el guardado del estado: la posición se reconstruye
    desde el exchange y no se vuelve a comprar."""
    w = World(btc)
    t = await w.boot()
    await w.advance(5)
    # Ejecución "a medias": órdenes en el exchange que el estado guardado no conoce.
    sig_t = w.bars[w.k - 1].close_time
    intent = EntryIntent("BTCUSDT", w.ex.prices["BTCUSDT"], w.ex.prices["BTCUSDT"] * D("0.95"))
    d = t.risk.approve_entry(intent, Portfolio(D(200), D("0.001")), btc, sig_t)
    await t.om.open_position(d, intent, sig_t)
    restarted = await w.boot()  # el proceso murió antes de guardar
    assert "BTCUSDT" in restarted.positions
    orders, _ = restarted.positions["BTCUSDT"]
    assert orders.stop.status == "NEW" and orders.stop.type == "STOP_LOSS"
    buys = w.ex.sent["BUY:MARKET"]
    await w.advance(3)
    assert w.ex.sent["BUY:MARKET"] == buys  # no duplica la compra


async def test_restart_with_orphan_entry_without_stop_closes_it(btc: SymbolFilters) -> None:
    w = World(btc)
    t = await w.boot()
    await w.advance(5)
    sig_t = w.bars[w.k - 1].close_time
    cid = client_id("h1", "BTCUSDT", "E", sig_t)
    await w.ex.market_order("BTCUSDT", "BUY", D("0.001"), cid)  # compra sin stop: caída antes
    restarted = await w.boot()
    assert restarted.positions == {}
    assert w.ex.sent["SELL:MARKET"] == 1
    assert restarted.pf.trades[-1].exits == ("huérfana_sin_stop",)
    assert t is not restarted


async def test_two_processes_cannot_trade_the_same_account(btc: SymbolFilters) -> None:
    w = World(btc)
    await w.boot()
    second = _trader(w.store, w.sessions, w.ex, btc, w.clock)  # sin liberar el bloqueo
    with pytest.raises(AnotherInstanceError):
        await second.start()


async def test_kill_switch_cancels_every_order_and_reconciles_to_zero(btc: SymbolFilters) -> None:
    w = World(btc)
    t = await w.boot()
    await w.until_position()
    await w.ex.stop_loss_order("BTCUSDT", D("0.001"), D("1"), "h1-BTCUSDT-S-0001010000")
    t.risk.update(w.clock.t, D(179))
    await t._kill(w.clock.t)
    assert t.positions == {}
    assert await w.ex.open_orders() == []
    assert w.ex.sent["SELL:MARKET"] == 1


async def test_connection_lost_during_close_keeps_emergency_and_retries(btc: SymbolFilters) -> None:
    w = World(btc)
    t = await w.boot()
    await w.until_position()
    w.ex.fail_next["market"] = [TransientExchangeError("red")] * 20
    await t._close("BTCUSDT", "prueba", w.clock.t)
    assert t.emergencies == {"BTCUSDT": "prueba"} and not t.can_open
    await t._persist()
    w.ex.down = True  # el exchange sigue caído durante el reinicio
    restarted = await w.boot()
    assert restarted.emergencies == {"BTCUSDT": "prueba"}  # la emergencia sobrevive al reinicio
    w.ex.down = False
    w.ex.fail_next["market"] = []
    await restarted.reconcile()
    assert restarted.positions == {} and restarted.emergencies == {}
    assert w.ex.sent["SELL:MARKET"] == 1
