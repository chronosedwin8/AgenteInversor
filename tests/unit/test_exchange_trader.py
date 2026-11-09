"""Fase 4: ejecutor con órdenes reales (contra un exchange falso con semántica de Binance).

Criterios de la fase como invariantes: toda posición abierta tiene su stop en el exchange y un
reinicio forzado no duplica órdenes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from bot.data.backfill import HistorySync
from bot.exchange.filters import SymbolFilters
from bot.execution.live import ExchangeTrader
from bot.strategy.hypotheses import H1Params
from tests.fake_exchange import FakeExchange
from tests.fakes import FakeAdapter, MemorySessionStore, MemoryStore
from tests.strategy_helpers import CFG
from tests.unit.test_paper import DAY, T0, Clock, _load, _series


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def btc(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])


def _trader(
    store: MemoryStore,
    sessions: MemorySessionStore,
    ex: FakeExchange,
    f: SymbolFilters,
    clock: Clock,
) -> ExchangeTrader:
    return ExchangeTrader(
        CFG,
        H1Params(),
        store,
        sessions,  # type: ignore[arg-type]
        HistorySync(store, FakeAdapter(), None),
        ex,
        {"BTCUSDT": f},  # type: ignore[arg-type]
        clock=clock,
        sleep=_no_sleep,
    )


async def _assert_protected(trader: ExchangeTrader, ex: FakeExchange) -> None:
    open_ids = {o.client_order_id for o in await ex.open_orders()}
    for sym, (orders, _) in trader.positions.items():
        assert orders.stop.client_order_id in open_ids, f"{sym} sin stop en el exchange"
        assert orders.stop.type == "STOP_LOSS"
    # Ningún stop huérfano: cada orden abierta pertenece a una posición viva.
    assert open_ids <= {o.stop.client_order_id for o, _ in trader.positions.values()}


async def test_every_position_protected_and_restart_does_not_duplicate(btc: SymbolFilters) -> None:
    four, daily = _series("BTCUSDT")
    store, sessions = MemoryStore(), MemorySessionStore()
    clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
    await _load(store, four, daily, clock.t)
    ex = FakeExchange({"BTCUSDT": four[599].close}, clock)
    trader = _trader(store, sessions, ex, btc, clock)
    await trader.start()

    bars = [c for c in four if T0 + 100 * DAY <= c.open_time < T0 + 160 * DAY]
    for k, c in enumerate(bars):
        ex.move_price("BTCUSDT", c.low)  # durante la vela: puede saltar el stop en el exchange
        ex.prices["BTCUSDT"] = c.close
        clock.t = c.close_time + timedelta(seconds=1)
        await _load(store, four, daily, clock.t)
        if k == len(bars) // 2:  # reinicio forzado a mitad de camino
            sessions.locks.clear()  # el proceso anterior murió: PostgreSQL libera el bloqueo
            trader = _trader(store, sessions, ex, btc, clock)
            await trader.start()
        await trader.step()
        await trader.reconcile()
        await _assert_protected(trader, ex)

    entries = [o for o in ex.orders.values() if o.side == "BUY"]
    assert entries, "el escenario debe abrir posiciones"
    assert len({o.client_order_id for o in entries}) == len(entries) == ex.sent["BUY:MARKET"]
    assert trader.paused_reason is None
    # Cada operación cerrada se registró una sola vez.
    keys = [(t.symbol, t.opened_at) for t in sessions.trades_rows]
    assert keys, "debe haber operaciones cerradas"
    assert len(keys) == len(set(keys))


async def test_kill_switch_closes_on_exchange(btc: SymbolFilters) -> None:
    four, daily = _series("BTCUSDT")
    store, sessions = MemoryStore(), MemorySessionStore()
    clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
    await _load(store, four, daily, clock.t)
    ex = FakeExchange({"BTCUSDT": four[599].close}, clock)
    trader = _trader(store, sessions, ex, btc, clock)
    await trader.start()
    # Avanza hasta que haya una posición abierta.
    for c in [c for c in four if c.open_time >= T0 + 100 * DAY]:
        ex.prices["BTCUSDT"] = c.close
        clock.t = c.close_time + timedelta(seconds=1)
        await _load(store, four, daily, clock.t)
        await trader.step()
        if trader.positions:
            break
    assert trader.positions
    trader.risk.update(clock.t, D(179))  # capital virtual bajo el umbral
    assert trader.risk.kill_switch_active
    await trader._kill(clock.t)
    assert trader.positions == {}
    assert await ex.open_orders() == []  # stops cancelados en el exchange
    assert ex.sent["SELL:MARKET"] >= 1


async def test_unknown_orders_pause_new_entries(btc: SymbolFilters) -> None:
    four, daily = _series("BTCUSDT")
    store, sessions = MemoryStore(), MemorySessionStore()
    clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
    await _load(store, four, daily, clock.t)
    ex = FakeExchange({"BTCUSDT": four[599].close}, clock)
    await ex.stop_loss_order("BTCUSDT", D("0.001"), D("1000"), "h1-BTCUSDT-S-0001010000")
    trader = _trader(store, sessions, ex, btc, clock)
    await trader.start()
    assert trader.paused_reason is not None and "desconocidas" in trader.paused_reason
    for c in [c for c in four if c.open_time >= T0 + 100 * DAY][:120]:
        ex.prices["BTCUSDT"] = c.close
        clock.t = c.close_time + timedelta(seconds=1)
        await _load(store, four, daily, clock.t)
        await trader.step()
    assert ex.sent.get("BUY:MARKET", 0) == 0  # en pausa no entra


async def test_clock_drift_pauses_entries_and_recovers(btc: SymbolFilters) -> None:
    four, daily = _series("BTCUSDT")
    store, sessions = MemoryStore(), MemorySessionStore()
    clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
    await _load(store, four, daily, clock.t)
    server = Clock(clock.t + timedelta(seconds=3))  # servidor 3 s adelantado
    ex = FakeExchange({"BTCUSDT": four[599].close}, server)
    trader = _trader(store, sessions, ex, btc, clock)
    await trader.start()
    assert trader.paused_reason is not None and "reloj" in trader.paused_reason
    server.t = clock.t
    await trader.reconcile()
    assert trader.paused_reason is None


class _SlowTimeExchange(FakeExchange):
    """Binance responde a mitad de `latency`; `skew` es el desfase real del reloj local."""

    latency = timedelta(0)
    skew = timedelta(0)

    async def server_time(self) -> Any:
        self.clock.t += self.latency / 2  # type: ignore[attr-defined]
        server = self.clock() + self.skew
        self.clock.t += self.latency / 2  # type: ignore[attr-defined]
        return server


async def test_network_latency_does_not_pause_but_real_drift_does(btc: SymbolFilters) -> None:
    clock = Clock(T0)
    ex = _SlowTimeExchange({"BTCUSDT": D(100)}, clock)
    trader = _trader(MemoryStore(), MemorySessionStore(), ex, btc, clock)

    ex.latency = timedelta(seconds=3)  # el caso real: arranque lento en la EC2
    assert abs(await trader.check_clock()) < 1
    assert trader.paused_reason is None

    ex.latency, ex.skew = timedelta(milliseconds=50), timedelta(seconds=3)  # desfase de verdad
    assert await trader.check_clock() == pytest.approx(-3000, abs=1)
    assert trader.paused_reason is not None and "reloj desincronizado" in trader.paused_reason

    ex.skew = timedelta(0)  # se recupera solo al volver a estar sincronizado
    await trader.check_clock()
    assert trader.paused_reason is None
