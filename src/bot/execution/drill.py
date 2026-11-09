"""Simulacro de kill switch en Spot Testnet (docs/fase-testnet-plan.md §5).

Abre una posición real de prueba, simula una caída del proceso con la posición abierta, dispara
el kill switch y comprueba en el exchange que no queda nada abierto.

Usa el mismo `ExchangeTrader` y el mismo `RiskManager` que la testnet, pero con una cuenta lógica
propia y otro prefijo de órdenes: la cuenta `testnet` y su prueba de dos semanas no se enteran. Lo
único simulado es la señal (fuerza tendencia y ruptura sobre la última vela real) y la caída del
capital que activa el kill switch. Nunca toca producción: `BinanceSpotTrading` exige una URL de
testnet o el modo live completo, y el comando se niega a usar otra cosa.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from bot.exchange.trading import OrderInfo
from bot.execution.live import ExchangeTrader
from bot.risk.manager import RiskEventKind
from bot.strategy.features import Bar
from bot.strategy.hypotheses import H1Params

DRILL_PREFIX = "dr"
CONFIRM_PHRASE = "SIMULACRO-TESTNET"
SIMULATED_EQUITY_FACTOR = Decimal("0.85")  # supera el −10 % del kill switch

Boot = Callable[[], Awaitable[ExchangeTrader]]  # proceso nuevo, ya arrancado
Release = Callable[[ExchangeTrader], Awaitable[None]]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DrillReport:
    account: str
    symbol: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.checks) and self.error is None and all(c.ok for c in self.checks)

    def render(self) -> str:
        lines = [
            f"# Simulacro de kill switch — cuenta `{self.account}`, {self.symbol}",
            "",
            f"**Resultado: {'SUPERADO' if self.ok else 'NO SUPERADO'}**",
            "",
        ]
        for c in self.checks:
            detail = f" — {c.detail}" if c.detail else ""
            lines.append(f"- [{'x' if c.ok else ' '}] {c.name}{detail}")
        if self.error:
            lines += ["", f"Error: `{self.error}`"]
        return "\n".join(lines) + "\n"


def signal_bar(real: Bar, at: datetime, p: H1Params) -> Bar:
    """Última vela real con tendencia y ruptura forzadas: la señal recorre el camino normal."""
    return replace(
        real,
        close_time=at,
        regime_close=2.0,
        regime_ema={p.regime_ema: 1.0},
        donchian={p.breakout_n: real.close * 0.5},
    )


class _Drill:
    def __init__(self, boot: Boot, release: Release, trader: ExchangeTrader, symbol: str):
        self.boot, self.release, self.t, self.symbol = boot, release, trader, symbol
        self.report = DrillReport(trader.account, symbol)
        self.started = trader.clock() - timedelta(minutes=1)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.report.checks.append(Check(name, ok, detail))
        return ok

    async def own_open(self) -> list[OrderInfo]:
        return [
            o
            for o in await self.t.client.open_orders()
            if o.client_order_id.startswith(f"{DRILL_PREFIX}-")
        ]

    async def history(self) -> list[OrderInfo]:
        return await self.t.om.recent_orders(self.symbol, self.started)

    async def entries(self) -> int:
        return sum(1 for o in await self.history() if "-E-" in o.client_order_id)

    async def restart(self) -> None:
        """Proceso nuevo: el estado sale de la base de datos y del exchange, no de la memoria."""
        await self.release(self.t)
        self.t = await self.boot()

    async def open_position(self) -> bool:
        t, sym = self.t, self.symbol
        ready = t.can_open and not t.positions and not await self.own_open()
        if not self.add("Cuenta de simulacro limpia y operable", ready):
            return False
        now = t.clock()
        await t._on_bar(sym, [signal_bar((await t._bars())[sym][-1], now, t.strategy.p)], now)
        await t._persist()
        if not self.add("Señal aprobada por el RiskManager y compra ejecutada", sym in t.positions):
            return False
        orders, _ = t.positions[sym]
        ids = [o.client_order_id for o in await self.own_open()]
        return self.add(
            "La posición tiene su stop abierto en el exchange",
            ids == [orders.stop.client_order_id] and orders.stop.type == "STOP_LOSS",
            f"cantidad {orders.qty}, precio {orders.entry.avg_price}, "
            f"stop {orders.stop.stop_price}",
        )

    async def restart_with_position(self) -> bool:
        before = self.t.positions[self.symbol][0].stop.client_order_id
        await self.restart()
        t = self.t
        after = (
            t.positions[self.symbol][0].stop.client_order_id if self.symbol in t.positions else ""
        )
        restored = self.add("Tras el reinicio la posición y su stop siguen", after == before, after)
        opens = len(await self.own_open())
        no_dup = self.add(
            "El reinicio no duplicó órdenes",
            opens == 1 and await self.entries() == 1,
            f"{opens} orden abierta",
        )
        return restored and no_dup

    async def trigger_kill_switch(self) -> bool:
        t, sym = self.t, self.symbol
        orders, _ = t.positions[sym]
        now = t.clock()
        events = t.risk.update(now, t.pf.equity() * SIMULATED_EQUITY_FACTOR)
        for e in events:
            t._event("risk_event", now, event=e.kind, equity=e.equity, detail=e.detail)
        if not self.add(
            "El RiskManager activó el kill switch",
            any(e.kind is RiskEventKind.KILL_SWITCH for e in events),
        ):
            return False
        await t._kill(now)
        await t._persist()
        closed = self.add(
            "Posiciones cerradas y sin emergencias", not t.positions and not t.emergencies
        )
        left = await self.own_open()
        none_open = self.add(
            "Cero órdenes abiertas en el exchange", not left, f"{len(left)} abiertas"
        )
        by_id = {o.client_order_id: o for o in await self.history()}
        sells = [o for o in by_id.values() if o.side == "SELL" and o.type == "MARKET"]
        sold = self.add(
            "Venta confirmada (FILLED) por la cantidad completa",
            len(sells) == 1 and sells[0].status == "FILLED" and sells[0].executed_qty == orders.qty,
            f"{len(sells)} venta(s)",
        )
        stop = by_id.get(orders.stop.client_order_id)
        cancelled = self.add(
            "Stop cancelado en el exchange",
            stop is not None and stop.status == "CANCELED",
            stop.status if stop is not None else "no encontrado",
        )
        recorded = self.add(
            "Operación registrada con motivo kill_switch",
            len(t.pf.trades) == 1 and "kill_switch" in str(t.pf.trades[0].exits),
        )
        return closed and none_open and sold and cancelled and recorded

    async def restart_after_kill(self) -> None:
        await self.restart()
        t, sym = self.t, self.symbol
        self.add(
            "El kill switch sobrevive al reinicio",
            t.risk.kill_switch_active and not t.can_open,
        )
        now = t.clock()
        last = (await t._bars())[sym][-1]
        await t._on_bar(sym, [signal_bar(last, now, t.strategy.p)], now)
        self.add(
            "Con el kill switch activo una nueva señal no abre nada",
            sym not in t.positions and await self.entries() == 1 and not await self.own_open(),
        )

    async def cleanup(self) -> None:
        t = self.t
        if t.positions or await self.own_open():
            await t._kill(t.clock())
            await t._persist()
            self.add(
                "Limpieza tras el fallo: nada abierto",
                not t.positions and not await self.own_open(),
            )


async def run_kill_switch_drill(boot: Boot, release: Release, symbol: str) -> DrillReport:
    drill = _Drill(boot, release, await boot(), symbol)
    try:
        if (
            await drill.open_position()
            and await drill.restart_with_position()
            and await drill.trigger_kill_switch()
        ):
            await drill.restart_after_kill()
    except Exception as exc:
        drill.report.error = repr(exc)
    try:
        await drill.cleanup()
    except Exception as exc:
        drill.report.error = (drill.report.error or "") + f" | limpieza: {exc!r}"
    await release(drill.t)
    return drill.report
