"""Simulacro de kill switch (docs/fase-testnet-plan.md §5) contra un exchange falso."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from bot.data.backfill import HistorySync
from bot.exchange.filters import SymbolFilters
from bot.exchange.trading import TransientExchangeError
from bot.execution.drill import (
    DRILL_PREFIX,
    DrillReport,
    run_kill_switch_drill,
    signal_bar,
)
from bot.execution.live import ExchangeTrader
from bot.main import cmd_testnet_drill
from bot.strategy.features import build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params
from tests.fake_exchange import FakeExchange
from tests.fakes import FakeAdapter, MemorySessionStore, MemoryStore
from tests.strategy_helpers import CFG
from tests.unit.test_exchange_trader import _no_sleep, _trader
from tests.unit.test_paper import DAY, T0, Clock, _load, _series


@pytest.fixture
def btc(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])


class _World:
    def __init__(self, btc: SymbolFilters):
        self.four, self.daily = _series("BTCUSDT")
        self.store, self.sessions = MemoryStore(), MemorySessionStore()
        self.clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
        self.ex = FakeExchange({"BTCUSDT": self.four[599].close}, self.clock)
        self.btc = btc
        self.boots = 0
        self.on_boot: Callable[[int], Any] | None = None

    async def boot(self) -> ExchangeTrader:
        await _load(self.store, self.four, self.daily, self.clock.t)
        self.boots += 1
        if self.on_boot is not None:
            result = self.on_boot(self.boots)
            if asyncio.iscoroutine(result):
                await result
        t = ExchangeTrader(
            CFG,
            H1Params(),
            self.store,
            self.sessions,  # type: ignore[arg-type]
            HistorySync(self.store, FakeAdapter(), None),
            self.ex,
            {"BTCUSDT": self.btc},  # type: ignore[arg-type]
            account="drill-test",
            clock=self.clock,
            sleep=_no_sleep,
            order_prefix=DRILL_PREFIX,
        )
        await t.start()
        return t

    async def release(self, t: ExchangeTrader) -> None:
        self.sessions.locks.discard(t.account)

    async def run(self) -> DrillReport:
        return await run_kill_switch_drill(self.boot, self.release, "BTCUSDT")


async def test_full_drill_passes_and_leaves_nothing_open(btc: SymbolFilters) -> None:
    w = _World(btc)
    report = await w.run()
    assert report.error is None, report.render()
    assert report.ok, report.render()
    assert w.boots == 3  # arranque + reinicio con posición + reinicio tras el kill switch
    assert w.ex.sent["BUY:MARKET"] == 1 and w.ex.sent["SELL:MARKET"] == 1
    assert await w.ex.open_orders() == []
    assert w.sessions.locks == set()  # libera el bloqueo de la cuenta
    assert "SUPERADO" in report.render() and "NO SUPERADO" not in report.render()


async def test_drill_never_touches_the_two_week_testnet_account(btc: SymbolFilters) -> None:
    w = _World(btc)
    main = _trader(w.store, MemorySessionStore(), w.ex, btc, w.clock)
    await _load(w.store, w.four, w.daily, w.clock.t)
    await main.start()
    seen: list[tuple[str | None, int]] = []

    async def check_main(n: int) -> None:
        if n == 2:  # el reinicio ocurre con la posición del simulacro abierta
            assert len(await w.ex.open_orders()) == 1
            await main.reconcile()
            seen.append((main.paused_reason, len(main.positions)))

    w.on_boot = check_main
    assert (await w.run()).ok
    assert seen == [(None, 0)]  # ni pausa por "órdenes desconocidas" ni adopta la posición
    await main.reconcile()
    assert main.paused_reason is None and not main.risk.kill_switch_active


async def test_unconfirmed_close_can_never_report_success(btc: SymbolFilters) -> None:
    w = _World(btc)

    def break_sells(n: int) -> None:
        if n == 2:
            w.ex.fail_next["market"] = [TransientExchangeError("red")] * 200

    w.on_boot = break_sells
    report = await w.run()
    assert report.ok is False
    assert any(not c.ok and "cerradas" in c.name for c in report.checks), report.render()
    assert "NO SUPERADO" in report.render()


async def test_failure_after_buying_cleans_up_the_position(btc: SymbolFilters) -> None:
    w = _World(btc)

    def crash_on_restart(n: int) -> None:
        if n == 2:
            raise RuntimeError("proceso caído")

    w.on_boot = crash_on_restart
    report = await w.run()
    assert report.ok is False and "proceso caído" in (report.error or "")
    assert w.ex.sent["SELL:MARKET"] == 1 and await w.ex.open_orders() == []
    assert any("Limpieza" in c.name and c.ok for c in report.checks)


def test_forced_signal_goes_through_the_real_strategy() -> None:
    four, daily = _series("BTCUSDT")
    real = build_bars("BTCUSDT", four[:700], daily)[-1]
    forced = signal_bar(real, real.close_time, H1Params())
    signal, reason = H1Breakout().evaluate_entry([forced])
    assert reason == "ok" and signal is not None and signal.stop < real.close


def test_command_refuses_without_the_confirmation_phrase() -> None:
    code = asyncio.run(cmd_testnet_drill(None, None, "si", "BTCUSDT"))  # type: ignore[arg-type]
    assert code == 2  # no llega a tocar configuración, base de datos ni exchange
