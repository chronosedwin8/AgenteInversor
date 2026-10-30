"""Exchange falso con la semántica de Binance Spot relevante para la Fase 4."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from bot.exchange.trading import ExchangeRejectedError, OrderInfo, TransientExchangeError

NOW = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)


class FakeExchange:
    def __init__(self, prices: dict[str, Decimal], clock: Callable[[], datetime] | None = None):
        self.clock = clock or (lambda: NOW)
        self.prices = dict(prices)
        self.orders: dict[str, OrderInfo] = {}
        self.sent: Counter[str] = Counter()  # órdenes realmente creadas por tipo
        self.fail_next: dict[str, list[Exception]] = {}  # método → errores a lanzar antes
        self.lose_response: set[str] = set()  # ejecuta pero "se pierde" la respuesta
        self.reject_stops = False
        self.down = False  # Binance no responde: todo falla con error transitorio
        self.corrupt_reads = False  # respuestas de consulta incompletas
        self.sells_not_filled = False  # ventas aceptadas (NEW) que nunca se ejecutan
        self._id = 0

    def _maybe_fail(self, method: str) -> None:
        if self.down:
            raise TransientExchangeError("sin respuesta")
        errs = self.fail_next.get(method)
        if errs:
            raise errs.pop(0)

    def _new(self, sym: str, cid: str, side: str, typ: str, qty: Decimal, status: str,
             stop: Decimal = Decimal(0)) -> OrderInfo:  # fmt: skip
        if cid in self.orders:
            raise ExchangeRejectedError(-2010, "Duplicate order sent.")
        self._id += 1
        px = self.prices[sym]
        filled = status == "FILLED"
        info = OrderInfo(sym, cid, self._id, side, typ, status, qty, qty if filled else Decimal(0),
                         qty * px if filled else Decimal(0), stop, NOW)  # fmt: skip
        self.orders[cid] = info
        self.sent[f"{side}:{typ}"] += 1
        return info

    async def server_time(self) -> datetime:
        self._maybe_fail("time")
        return self.clock()

    async def market_order(
        self, symbol: str, side: str, qty: Decimal, client_order_id: str
    ) -> OrderInfo:
        self._maybe_fail("market")
        status = "NEW" if side == "SELL" and self.sells_not_filled else "FILLED"
        info = self._new(symbol, client_order_id, side, "MARKET", qty, status)
        if "market" in self.lose_response:
            self.lose_response.discard("market")
            raise TransientExchangeError("timeout tras ejecutar")
        return info

    async def stop_loss_order(self, symbol: str, qty: Decimal, stop_price: Decimal,
                              client_order_id: str) -> OrderInfo:  # fmt: skip
        self._maybe_fail("stop")
        if self.reject_stops:
            raise ExchangeRejectedError(-2010, "Stop price would trigger immediately.")
        return self._new(symbol, client_order_id, "SELL", "STOP_LOSS", qty, "NEW", stop_price)

    async def cancel_order(self, symbol: str, client_order_id: str) -> OrderInfo:
        self._maybe_fail("cancel")
        o = self.orders.get(client_order_id)
        if o is None or not o.is_open:
            raise ExchangeRejectedError(-2011, "Unknown order sent.")
        o = replace(o, status="CANCELED")
        self.orders[client_order_id] = o
        return o

    async def get_order(self, symbol: str, client_order_id: str) -> OrderInfo | None:
        self._maybe_fail("get")
        o = self.orders.get(client_order_id)
        if o is not None and self.corrupt_reads:
            return replace(o, status="", client_order_id="")
        return o

    async def open_orders(self, symbol: str | None = None) -> list[OrderInfo]:
        self._maybe_fail("open")
        return [o for o in self.orders.values() if o.is_open and (symbol in (None, o.symbol))]

    async def orders_since(self, symbol: str, since: datetime) -> list[OrderInfo]:
        self._maybe_fail("history")
        return [o for o in self.orders.values() if o.symbol == symbol]

    async def balances(self) -> dict[str, Decimal]:
        return {"USDT": Decimal(10_000)}

    def move_price(self, symbol: str, price: Decimal) -> list[OrderInfo]:
        """Mueve el precio; los stops de venta con stop ≥ precio se ejecutan a ese precio."""
        self.prices[symbol] = price
        filled = []
        for cid, o in list(self.orders.items()):
            if o.symbol == symbol and o.is_open and o.type == "STOP_LOSS" and price <= o.stop_price:
                o = replace(o, status="FILLED", executed_qty=o.orig_qty,
                            quote_qty=o.orig_qty * price)  # fmt: skip
                self.orders[cid] = o
                filled.append(o)
        return filled
