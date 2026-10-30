"""Caché de filtros por símbolo con refresco periódico desde exchangeInfo."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence

from bot.exchange.base import ExchangeAdapter
from bot.exchange.filters import SymbolFilters
from bot.log import get_logger

log = get_logger(__name__)

SnapshotSink = Callable[[dict[str, SymbolFilters]], Awaitable[None]]


class ExchangeInfoCache:
    def __init__(
        self,
        adapter: ExchangeAdapter,
        symbols: Sequence[str],
        ttl_seconds: float,
        on_refresh: SnapshotSink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._adapter = adapter
        self._symbols = tuple(symbols)
        self._ttl = ttl_seconds
        self._on_refresh = on_refresh
        self._clock = clock
        self._filters: dict[str, SymbolFilters] = {}
        self._loaded_at: float | None = None

    @property
    def stale(self) -> bool:
        return self._loaded_at is None or self._clock() - self._loaded_at >= self._ttl

    async def refresh(self) -> dict[str, SymbolFilters]:
        filters = await self._adapter.get_symbol_filters(self._symbols)
        for f in filters.values():
            if not f.is_trading:
                log.warning("symbol_not_trading", symbol=f.symbol, status=f.status)
        self._filters = filters
        self._loaded_at = self._clock()
        if self._on_refresh is not None:
            await self._on_refresh(filters)
        log.info("exchange_info_refreshed", symbols=sorted(filters))
        return filters

    async def get(self, symbol: str) -> SymbolFilters:
        if self.stale:
            await self.refresh()
        try:
            return self._filters[symbol]
        except KeyError:
            raise LookupError(f"{symbol} no está en el universo cargado") from None
