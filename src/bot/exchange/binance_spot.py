"""Adaptador de Binance Spot sobre el SDK oficial `binance-sdk-spot`.

Fase 1: solo lectura y sin credenciales. Por defecto apunta a data-api.binance.vision, que
solo sirve datos de mercado y no acepta órdenes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import SPOT_REST_API_MARKET_URL
from binance_sdk_spot.spot import Spot

from bot.data.klines import Candle, datetime_to_ms, ts_to_datetime
from bot.exchange.base import BookTicker, ExchangeAdapter
from bot.exchange.filters import SymbolFilters
from bot.log import get_logger

log = get_logger(__name__)


def _as_dict(model: Any) -> Any:
    """Modelos del SDK → JSON plano (claves como en la API de Binance)."""
    actual = getattr(model, "actual_instance", None)
    if actual is not None:
        model = actual
    to_dict = getattr(model, "to_dict", None)
    return to_dict() if callable(to_dict) else model


class BinanceSpot(ExchangeAdapter):
    def __init__(self, rest_base_url: str = SPOT_REST_API_MARKET_URL, timeout_ms: int = 10000):
        self._client = Spot(
            config_rest_api=ConfigurationRestAPI(
                base_path=rest_base_url, timeout=timeout_ms, retries=3, backoff=1000
            )
        )
        self.rest_base_url = rest_base_url

    async def _call(self, fn: str, **kwargs: Any) -> Any:
        method = getattr(self._client.rest_api, fn)
        response = await asyncio.to_thread(method, **kwargs)
        return response.data()

    async def exchange_info_raw(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        data = await self._call("exchange_info", symbols=list(symbols))
        payload = _as_dict(data)
        symbols_info: list[dict[str, Any]] = payload.get("symbols", [])
        return symbols_info

    async def get_symbol_filters(self, symbols: Sequence[str]) -> dict[str, SymbolFilters]:
        infos = await self.exchange_info_raw(symbols)
        out = {info["symbol"]: SymbolFilters.from_exchange_info(info) for info in infos}
        missing = set(symbols) - set(out)
        if missing:
            raise LookupError(f"exchangeInfo no devolvió: {sorted(missing)}")
        return out

    async def get_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime, limit: int
    ) -> list[Candle]:
        rows = await self._call(
            "klines",
            symbol=symbol,
            interval=interval,
            start_time=datetime_to_ms(start),
            end_time=datetime_to_ms(end),
            limit=limit,
        )
        candles = [Candle.from_row(symbol, interval, row) for row in rows]
        return [c for c in candles if start <= c.open_time <= end]

    async def get_book_ticker(self, symbol: str) -> BookTicker:
        d = _as_dict(await self._call("ticker_book_ticker", symbol=symbol))
        return BookTicker(
            symbol=d["symbol"],
            bid=Decimal(d["bidPrice"]),
            bid_qty=Decimal(d["bidQty"]),
            ask=Decimal(d["askPrice"]),
            ask_qty=Decimal(d["askQty"]),
        )

    async def server_time(self) -> datetime:
        d = _as_dict(await self._call("time"))
        return ts_to_datetime(int(d["serverTime"]))
