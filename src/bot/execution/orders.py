"""OrderManager (Fase 4): envío de órdenes reales (testnet) seguro e idempotente.

- Regla 2: abrir una posición exige una aprobación firmada y de un solo uso del RiskManager.
- Idempotencia: cada orden tiene un `newClientOrderId` determinista (estrategia, par, propósito,
  vela de señal, versión). Antes de enviar se consulta si ya existe; si un envío falla por red, se
  vuelve a consultar antes de reintentar. Un reinicio o un reintento no duplican órdenes.
- Stop-market (`STOP_LOSS`) en el exchange inmediatamente tras la compra; si no se puede colocar,
  cierre inmediato y verificado.
- Una orden aceptada no es una posición cerrada: los cierres solo se dan por hechos cuando el
  exchange devuelve la orden FILLED con toda la cantidad.
- Toda respuesta del exchange se valida; datos incompletos → `ExchangeDataError` (se pausa).
- Reintentos con backoff exponencial solo ante errores transitorios (red, 5xx, 429).
"""

from __future__ import annotations

import asyncio
import contextlib
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
KNOWN_STATUS = {
    "NEW",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "PENDING_CANCEL",
    "REJECTED",
    "EXPIRED",
    "EXPIRED_IN_MATCH",
}


class ExchangeDataError(RuntimeError):
    """El exchange devolvió datos incompletos o incoherentes: hay que pausar."""


class EmergencyCloseError(RuntimeError):
    """No se pudo CONFIRMAR el cierre de una posición: queda en estado de emergencia."""

    def __init__(self, msg: str, entry: OrderInfo | None = None):
        super().__init__(msg)
        self.entry = entry  # compra ejecutada cuya posición sigue abierta (si se conoce)


class StopPlacementError(RuntimeError):
    """No se pudo dejar la posición protegida por un stop: se cerró a mercado (verificado)."""

    def __init__(self, msg: str, entry: OrderInfo | None = None, exit: OrderInfo | None = None):
        super().__init__(msg)
        self.entry = entry
        self.exit = exit


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


def validate(o: OrderInfo, symbol: str | None = None, cid: str | None = None) -> OrderInfo:
    problems = []
    if not o.symbol or (symbol is not None and o.symbol != symbol):
        problems.append("symbol")
    if not o.client_order_id or (cid is not None and o.client_order_id != cid):
        problems.append("clientOrderId")
    if o.status not in KNOWN_STATUS:
        problems.append(f"status={o.status!r}")
    if o.orig_qty < 0 or o.executed_qty < 0 or o.quote_qty < 0:
        problems.append("cantidades negativas")
    if o.status == "FILLED" and (o.executed_qty <= 0 or o.quote_qty <= 0):
        problems.append("FILLED sin cantidad ejecutada")
    if problems:
        raise ExchangeDataError(f"respuesta inválida para {cid or o.client_order_id}: {problems}")
    return o


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

    async def _get(self, symbol: str, cid: str) -> OrderInfo | None:
        o = await self._retry("consultar", lambda: self.client.get_order(symbol, cid))
        return validate(o, symbol, cid) if o is not None else None

    async def _submit(
        self, symbol: str, cid: str, send: Callable[[], Awaitable[OrderInfo]]
    ) -> OrderInfo:
        """Envío idempotente: si la orden ya existe (reintento, reinicio) no se reenvía."""
        delay = self.base_delay_s
        for attempt in range(1, self.attempts + 1):
            existing = await self._get(symbol, cid)
            if existing is not None:
                return existing
            try:
                return validate(await send(), symbol, cid)
            except TransientExchangeError as exc:
                log.warning("order_send_uncertain", cid=cid, attempt=attempt, error=str(exc))
                if attempt == self.attempts:
                    raise
                await self._sleep(delay)
                delay = min(delay * 2, 30.0)
        raise AssertionError("inalcanzable")

    async def confirm_filled(self, o: OrderInfo, qty: Decimal) -> OrderInfo:
        """Una orden aceptada no es un cierre: espera a FILLED con toda la cantidad."""
        delay = self.base_delay_s
        for _ in range(self.attempts):
            if o.status == "FILLED" and o.executed_qty >= qty:
                return o
            if o.status in ("CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"):
                break
            await self._sleep(delay)
            delay = min(delay * 2, 30.0)
            current = await self._get(o.symbol, o.client_order_id)
            if current is None:
                break
            o = current
        raise EmergencyCloseError(
            f"{o.client_order_id}: cierre no confirmado ({o.status}, {o.executed_qty}/{qty})"
        )

    def emergency_id(self, sym: str, signal_time: datetime) -> str:
        """ID único de la venta de emergencia de una posición: todos los reintentos lo reusan,
        así una respuesta perdida nunca provoca una segunda venta."""
        return client_id(self.prefix, sym, "X", signal_time)

    async def emergency_sell(self, sym: str, qty: Decimal, signal_time: datetime) -> OrderInfo:
        return await self._market_sell(sym, qty, self.emergency_id(sym, signal_time))

    async def _market_sell(self, sym: str, qty: Decimal, cid: str) -> OrderInfo:
        try:
            out = await self._submit(
                sym, cid, lambda: self.client.market_order(sym, "SELL", qty, cid)
            )
        except (TransientExchangeError, ExchangeRejectedError) as exc:
            raise EmergencyCloseError(f"{cid}: venta no enviada ({exc})") from exc
        return await self.confirm_filled(out, qty)

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
        if entry.status != "FILLED":
            entry = await self.confirm_filled(entry, entry.orig_qty)
        # La comisión se cobra en el activo base: el stop protege la cantidad neta.
        qty = f.round_qty(entry.executed_qty * (1 - self.fee_rate))
        try:
            stop = await self.place_stop(sym, qty, intent.stop, signal_time, 0)
        except StopPlacementError as exc:
            raise StopPlacementError(str(exc), entry=entry, exit=exc.exit) from exc
        except EmergencyCloseError as exc:
            raise EmergencyCloseError(str(exc), entry=entry) from exc
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
            log.error("stop_placement_failed", symbol=sym, error=str(exc), alert=True)
            out = await self.emergency_sell(sym, qty, signal_time)  # EmergencyCloseError si falla
            raise StopPlacementError(
                f"{sym}: sin stop ({exc}); cerrada y verificada", exit=out
            ) from exc

    # ------------------------------------------------------------ gestión

    async def _cancel_or_status(self, sym: str, cid: str) -> OrderInfo:
        try:
            return validate(await self.client.cancel_order(sym, cid), sym)
        except ExchangeRejectedError:
            current = await self.client.get_order(sym, cid)
            if current is None:
                raise
            return validate(current, sym, cid)  # ya llenada o cancelada

    async def move_stop(self, pos: PositionOrders, new_stop: Decimal) -> OrderInfo | None:
        """Sube el stop (trailing). Devuelve la orden de stop LLENADA si ya había saltado."""
        cid = pos.stop.client_order_id
        old = await self._retry("cancelar", lambda: self._cancel_or_status(pos.symbol, cid))
        if old.status == "FILLED":
            return old
        pos.stop_version += 1
        pos.stop = await self.place_stop(
            pos.symbol, pos.qty, new_stop, pos.signal_time, pos.stop_version
        )
        return None

    async def close_position(self, pos: PositionOrders, reason: str) -> OrderInfo:
        """Cierre a mercado verificado. Si el stop ya saltó, devuelve ese llenado."""
        cid = pos.stop.client_order_id
        try:
            old = await self._retry("cancelar", lambda: self._cancel_or_status(pos.symbol, cid))
        except TransientExchangeError as exc:
            raise EmergencyCloseError(f"{pos.symbol}: no se pudo cancelar el stop ({exc})") from exc
        if old.status == "FILLED":
            return old
        xid = client_id(self.prefix, pos.symbol, "C", pos.signal_time, pos.stop_version)
        out = await self._market_sell(pos.symbol, pos.qty, xid)
        log.info("position_closed", symbol=pos.symbol, reason=reason, price=str(out.avg_price))
        return out

    async def cancel_all(self) -> list[OrderInfo]:
        """Cancela todas las órdenes abiertas propias. Devuelve las que siguen abiertas."""

        async def ours() -> list[OrderInfo]:
            orders = await self._retry("abiertas", lambda: self.client.open_orders())
            return [o for o in orders if o.client_order_id.startswith(f"{self.prefix}-")]

        for o in await ours():
            with contextlib.suppress(ExchangeRejectedError):  # ya no estaba abierta
                await self._retry("cancelar", self._canceller(o.symbol, o.client_order_id))
        return await ours()

    def _canceller(self, sym: str, cid: str) -> Callable[[], Awaitable[OrderInfo]]:
        return lambda: self.client.cancel_order(sym, cid)

    # ------------------------------------------------------------ reconciliación

    def _getter(self, sym: str, cid: str) -> Callable[[], Awaitable[OrderInfo | None]]:
        return lambda: self.client.get_order(sym, cid)

    async def reconcile(self, positions: dict[str, PositionOrders]) -> ReconcileReport:
        """Compara lo esperado con el exchange: cada posición debe tener su stop abierto."""
        report = ReconcileReport(ok=True)
        for sym, pos in positions.items():
            st = await self._retry("consultar", self._getter(sym, pos.stop.client_order_id))
            if st is not None:
                validate(st, sym)
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
            validate(o)
            ours = o.client_order_id.startswith(f"{self.prefix}-")
            if ours and o.client_order_id not in expected:
                report.unknown_orders.append(o)
                report.ok = False
        return report

    async def recent_orders(self, symbol: str, since: datetime) -> list[OrderInfo]:
        orders = await self._retry("historial", lambda: self.client.orders_since(symbol, since))
        return [
            validate(o, symbol) for o in orders if o.client_order_id.startswith(f"{self.prefix}-")
        ]
