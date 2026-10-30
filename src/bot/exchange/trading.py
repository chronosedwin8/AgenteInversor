"""Cliente de órdenes de Binance Spot (Fase 4). Testnet por defecto.

Regla 1: contra el endpoint de PRODUCCIÓN, `require_live()` se exige al construir el cliente y
antes de cada orden. Regla 2: este cliente no decide nada; solo lo usa el OrderManager, que exige
una aprobación firmada del RiskManager para abrir posiciones.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from bot.config import EnvSettings
from bot.data.klines import ts_to_datetime
from bot.modes import require_live

TESTNET_URL = "https://testnet.binance.vision"
PRODUCTION_URLS = (
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
)


class ExchangeRejectedError(RuntimeError):
    """El exchange rechazó la orden (4xx): no se reintenta."""

    def __init__(self, code: int | None, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


class TransientExchangeError(RuntimeError):
    """Error de red, 5xx, 429: se puede reintentar."""


@dataclass(frozen=True)
class OrderInfo:
    symbol: str
    client_order_id: str
    order_id: int
    side: str
    type: str
    status: str  # NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED, REJECTED
    orig_qty: Decimal
    executed_qty: Decimal
    quote_qty: Decimal  # cummulativeQuoteQty
    stop_price: Decimal
    update_time: datetime

    @property
    def avg_price(self) -> Decimal:
        return self.quote_qty / self.executed_qty if self.executed_qty > 0 else Decimal(0)

    @property
    def is_open(self) -> bool:
        return self.status in ("NEW", "PARTIALLY_FILLED")


class TradingClient(Protocol):
    async def server_time(self) -> datetime: ...
    async def market_order(
        self, symbol: str, side: str, qty: Decimal, client_order_id: str
    ) -> OrderInfo: ...
    async def stop_loss_order(
        self, symbol: str, qty: Decimal, stop_price: Decimal, client_order_id: str
    ) -> OrderInfo: ...
    async def cancel_order(self, symbol: str, client_order_id: str) -> OrderInfo: ...
    async def get_order(self, symbol: str, client_order_id: str) -> OrderInfo | None: ...
    async def open_orders(self, symbol: str | None = None) -> list[OrderInfo]: ...
    async def balances(self) -> dict[str, Decimal]: ...
    async def orders_since(self, symbol: str, since: datetime) -> list[OrderInfo]: ...


def _num(x: Decimal) -> float:
    """El SDK recibe float: se verifica que la conversión no altere el valor."""
    f = float(x)
    if Decimal(repr(f)) != x.normalize():
        raise ValueError(f"{x} no se representa exactamente como float")
    return f


def _info(d: dict[str, Any]) -> OrderInfo:
    return OrderInfo(
        symbol=d["symbol"],
        client_order_id=d.get("clientOrderId") or d.get("origClientOrderId", ""),
        order_id=int(d.get("orderId", 0)),
        side=d.get("side", ""),
        type=d.get("type", ""),
        status=d.get("status", ""),
        orig_qty=Decimal(str(d.get("origQty", "0"))),
        executed_qty=Decimal(str(d.get("executedQty", "0"))),
        quote_qty=Decimal(str(d.get("cummulativeQuoteQty", "0"))),
        stop_price=Decimal(str(d.get("stopPrice", "0") or "0")),
        update_time=ts_to_datetime(int(d.get("updateTime") or d.get("transactTime") or 0)),
    )


class BinanceSpotTrading:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        env: EnvSettings,
        timeout_ms: int = 10000,
        recv_window_ms: int = 5000,
    ):
        from binance_common.configuration import ConfigurationRestAPI
        from binance_sdk_spot.spot import Spot

        self.production = base_url.rstrip("/") in PRODUCTION_URLS or "testnet" not in base_url
        self._env = env
        if self.production:
            require_live(env)  # regla 1: falla si no hay TRADING_MODE=live + LIVE_CONFIRM=yes
        self.base_url = base_url
        self.recv_window = recv_window_ms
        self._client = Spot(
            config_rest_api=ConfigurationRestAPI(
                api_key=api_key,
                api_secret=api_secret,
                base_path=base_url,
                timeout=timeout_ms,
                retries=0,  # los reintentos los decide el OrderManager (idempotentes)
            )
        )

    async def _call(self, fn: str, **kw: Any) -> Any:
        from binance_common.errors import (
            BadRequestError,
            ForbiddenError,
            NetworkError,
            NotFoundError,
            RateLimitBanError,
            ServerError,
            TooManyRequestsError,
            UnauthorizedError,
        )

        if self.production and fn in ("new_order", "order_cancel_replace"):
            require_live(self._env)  # se verifica otra vez antes de cada orden
        try:
            resp = await asyncio.to_thread(getattr(self._client.rest_api, fn), **kw)
            data = resp.data()
        except (NetworkError, ServerError, TooManyRequestsError) as exc:
            raise TransientExchangeError(str(exc)) from exc
        except (
            BadRequestError,
            UnauthorizedError,
            ForbiddenError,
            NotFoundError,
            RateLimitBanError,
        ) as exc:
            code = getattr(exc, "code", None)
            raise ExchangeRejectedError(code, str(exc)) from exc
        actual = getattr(data, "actual_instance", None)
        if actual is not None:
            data = actual
        if isinstance(data, list):
            return [x.to_dict() if hasattr(x, "to_dict") else x for x in data]
        return data.to_dict() if hasattr(data, "to_dict") else data

    async def server_time(self) -> datetime:
        d = await self._call("time")
        return ts_to_datetime(int(d["serverTime"]))

    async def market_order(
        self, symbol: str, side: str, qty: Decimal, client_order_id: str
    ) -> OrderInfo:
        d = await self._call(
            "new_order",
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=_num(qty),
            new_client_order_id=client_order_id,
            new_order_resp_type="FULL",
            recv_window=self.recv_window,
        )
        return _info(d)

    async def stop_loss_order(
        self, symbol: str, qty: Decimal, stop_price: Decimal, client_order_id: str
    ) -> OrderInfo:
        d = await self._call(
            "new_order",
            symbol=symbol,
            side="SELL",
            type="STOP_LOSS",
            quantity=_num(qty),
            stop_price=_num(stop_price),
            new_client_order_id=client_order_id,
            new_order_resp_type="RESULT",
            recv_window=self.recv_window,
        )
        return _info(d)

    async def cancel_order(self, symbol: str, client_order_id: str) -> OrderInfo:
        d = await self._call(
            "delete_order",
            symbol=symbol,
            orig_client_order_id=client_order_id,
            recv_window=self.recv_window,
        )
        return _info(d)

    async def get_order(self, symbol: str, client_order_id: str) -> OrderInfo | None:
        try:
            d = await self._call(
                "get_order",
                symbol=symbol,
                orig_client_order_id=client_order_id,
                recv_window=self.recv_window,
            )
        except ExchangeRejectedError as exc:
            if exc.code == -2013 or "-2013" in str(exc) or "does not exist" in str(exc):
                return None
            raise
        return _info(d)

    async def open_orders(self, symbol: str | None = None) -> list[OrderInfo]:
        d = await self._call("get_open_orders", symbol=symbol, recv_window=self.recv_window)
        return [_info(x) for x in d]

    async def orders_since(self, symbol: str, since: datetime) -> list[OrderInfo]:
        d = await self._call(
            "all_orders",
            symbol=symbol,
            start_time=int(since.timestamp() * 1000),
            recv_window=self.recv_window,
        )
        return [_info(x) for x in d]

    async def balances(self) -> dict[str, Decimal]:
        d = await self._call("get_account", omit_zero_balances=True, recv_window=self.recv_window)
        return {
            b["asset"]: Decimal(str(b["free"])) + Decimal(str(b["locked"]))
            for b in d.get("balances", [])
        }


def utcnow() -> datetime:
    return datetime.now(UTC)
