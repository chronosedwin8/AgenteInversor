"""OrderManager (Fase 4): envío de órdenes reales (testnet) seguro e idempotente.

- Regla 2: abrir una posición exige una aprobación firmada y de un solo uso del RiskManager.
- Idempotencia: cada orden tiene un `newClientOrderId` determinista (estrategia, par, propósito,
  vela de señal, versión). Antes de enviar se consulta si ya existe; si un envío falla por red, se
  vuelve a consultar antes de reintentar. Un reinicio o un reintento no duplican órdenes.
- Stop en el exchange inmediatamente tras la compra; si no se puede colocar, cierre inmediato.
- Reintentos con backoff exponencial solo ante errores transitorios (red, 5xx, 429).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TypeVar

from bot.exchange.filters import SymbolFilters
from bot.exchange.trading import (
    ExchangeRejectedError,
    OrderInfo,
    TradingClient,
    TransientExchangeError,
)
from bot.log import get_logger
from bot.risk.manager import EntryIntent, RiskDecision, RiskManager

log = get_logger(__name__)
T = TypeVar("T")


class StopPlacementError(RuntimeError):
    """No se pudo dejar la posición protegida por un stop: se cerró a mercado."""


def client_id(
    prefix: str, symbol: str, purpose: str, signal_time: datetime, version: int = 0
) -> str:
    """Máx. 36 caracteres [A-Za-z0-9-_]: p. ej. 'h1-SOLUSDT-S-2611031600-2'."""
    cid = f"{prefix}-{symbol}-{purpose}-{signal_time:%y%m%d%H%M}"
    if version:
        cid += f"-{version}"
    if len(cid) > 36:
        raise ValueError(f"clientOrderId demasiado largo: {cid}")
    return cid


@dataclass
class PositionOrders:
    symbol: str
    signal_time: datetime
    qty: Decimal  # cantidad protegida por el stop (neta de comisión en el activo base)
    entry: OrderInfo
    stop: OrderInfo
    stop_version: int = 0


@dataclass
class ReconcileReport:
    ok: bool
    stops_filled: dict[str, OrderInfo] = field(default_factory=dict)
    stops_replaced: list[str] = field(default_factory=list)
    unknown_orders: list[OrderInfo] = field(default_factory=list)


class OrderManager:
    def __init__(
        self,
        client: TradingClient,
        risk: RiskManager,
        filters: dict[str, SymbolFilters],
        fee_rate: Decimal,
        prefix: str = "h1",
        attempts: int = 5,
        base_delay_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.client = client
        self.risk = risk
        self.filters = filters
        self.fee_rate = fee_rate
        self.prefix = prefix
        self.attempts = attempts
        self.base_delay_s = base_delay_s
        self._sleep = sleep

    async def _retry(self, what: str, fn: Callable[[], Awaitable[T]]) -> T:
        delay = self.base_delay_s
        for attempt in range(1, self.attempts + 1):
            try:
                return await fn()
            except TransientExchangeError as exc:
                if attempt == self.attempts:
                    raise
                log.warning("exchange_retry", what=what, attempt=attempt, error=str(exc))
                await self._sleep(delay)
                delay = min(delay * 2, 30.0)
        raise AssertionError("inalcanzable")

    async def _submit(
        self, symbol: str, cid: str, send: Callable[[], Awaitable[OrderInfo]]
    ) -> OrderInfo:
        """Envío idempotente: si la orden ya existe (reintento, reinicio) no se reenvía."""
        delay = self.base_delay_s
        for attempt in range(1, self.attempts + 1):
            existing = await self._retry("consultar", lambda: self.client.get_order(symbol, cid))
            if existing is not None:
                return existing
            try:
                return await send()
            except TransientExchangeError as exc:
                log.warning("order_send_uncertain", cid=cid, attempt=attempt, error=str(exc))
                if attempt == self.attempts:
                    raise
                await self._sleep(delay)
                delay = min(delay * 2, 30.0)
        raise AssertionError("inalcanzable")

    # ------------------------------------------------------------ apertura

    async def open_position(
        self, decision: RiskDecision, intent: EntryIntent, signal_time: datetime
    ) -> PositionOrders:
        self.risk.consume(decision, intent)  # regla 2: sin aprobación firmada no hay orden
        sym, f = intent.symbol, self.filters[intent.symbol]
        cid = client_id(self.prefix, sym, "E", signal_time)
        entry = await self._submit(
            sym, cid, lambda: self.client.market_order(sym, "BUY", decision.qty, cid)
        )
        if entry.executed_qty <= 0:
            raise ExchangeRejectedError(None, f"compra sin ejecutar: {entry.status}")
        # La comisión se cobra en el activo base: el stop protege la cantidad neta.
        qty = f.round_qty(entry.executed_qty * (1 - self.fee_rate))
        stop = await self.place_stop(sym, qty, intent.stop, signal_time, 0)
        log.info(
            "position_opened",
            symbol=sym,
            qty=str(qty),
            entry=str(entry.avg_price),
            stop=str(intent.stop),
            entry_id=cid,
            stop_id=stop.client_order_id,
        )
        return PositionOrders(sym, signal_time, qty, entry, stop)

    async def place_stop(
        self, sym: str, qty: Decimal, stop_price: Decimal, signal_time: datetime, version: int
    ) -> OrderInfo:
        cid = client_id(self.prefix, sym, "S", signal_time, version)
        price = self.filters[sym].round_price(stop_price, "down")
        try:
            return await self._submit(
                sym, cid, lambda: self.client.stop_loss_order(sym, qty, price, cid)
            )
        except (TransientExchangeError, ExchangeRejectedError) as exc:
            # PLAN.md: si el stop no se puede colocar, la posición se cierra de inmediato.
            log.error("stop_placement_failed", symbol=sym, error=str(exc))
            xid = client_id(self.prefix, sym, "X", signal_time, version)
            await self._submit(sym, xid, lambda: self.client.market_order(sym, "SELL", qty, xid))
            raise StopPlacementError(f"{sym}: sin stop ({exc}); posición cerrada") from exc

    # ------------------------------------------------------------ gestión

    async def move_stop(self, pos: PositionOrders, new_stop: Decimal) -> OrderInfo | None:
        """Sube el stop (trailing). Devuelve la orden de stop LLENADA si ya había saltado."""
        old = await self._retry(
            "cancelar", lambda: self._cancel_or_status(pos.symbol, pos.stop.client_order_id)
        )
        if old.status == "FILLED":
            return old
        pos.stop_version += 1
        pos.stop = await self.place_stop(
            pos.symbol, pos.qty, new_stop, pos.signal_time, pos.stop_version
        )
        return None

    async def _cancel_or_status(self, sym: str, cid: str) -> OrderInfo:
        try:
            return await self.client.cancel_order(sym, cid)
        except ExchangeRejectedError:
            current = await self.client.get_order(sym, cid)
            if current is None:
                raise
            return current  # ya llenada o cancelada

    async def close_position(self, pos: PositionOrders, reason: str) -> OrderInfo:
        """Cierre a mercado. Si el stop ya saltó, devuelve ese llenado."""
        old = await self._retry(
            "cancelar", lambda: self._cancel_or_status(pos.symbol, pos.stop.client_order_id)
        )
        if old.status == "FILLED":
            return old
        cid = client_id(self.prefix, pos.symbol, "C", pos.signal_time, pos.stop_version)
        out = await self._submit(
            pos.symbol, cid, lambda: self.client.market_order(pos.symbol, "SELL", pos.qty, cid)
        )
        log.info("position_closed", symbol=pos.symbol, reason=reason, price=str(out.avg_price))
        return out

    # ------------------------------------------------------------ reconciliación

    def _getter(self, sym: str, cid: str) -> Callable[[], Awaitable[OrderInfo | None]]:
        return lambda: self.client.get_order(sym, cid)

    async def reconcile(self, positions: dict[str, PositionOrders]) -> ReconcileReport:
        """Compara lo esperado con el exchange: cada posición debe tener su stop abierto."""
        report = ReconcileReport(ok=True)
        for sym, pos in positions.items():
            st = await self._retry("consultar", self._getter(sym, pos.stop.client_order_id))
            if st is not None and st.status == "FILLED":
                report.stops_filled[sym] = st
            elif st is None or not st.is_open:
                log.error("stop_missing", symbol=sym, status=getattr(st, "status", None))
                pos.stop_version += 1
                pos.stop = await self.place_stop(
                    sym, pos.qty, pos.stop.stop_price, pos.signal_time, pos.stop_version
                )
                report.stops_replaced.append(sym)
                report.ok = False
        expected = {p.stop.client_order_id for p in positions.values()}
        for o in await self._retry("abiertas", lambda: self.client.open_orders()):
            ours = o.client_order_id.startswith(f"{self.prefix}-")
            if ours and o.client_order_id not in expected:
                report.unknown_orders.append(o)
                report.ok = False
        return report
