"""Interfaz común de exchange. Implementaciones: BinanceSpot (Fase 1), Paper y Backtest (después).

Fase 1: solo métodos de lectura. Los métodos de órdenes se añaden en la Fase 4 y solo podrán
invocarse con una aprobación del RiskManager (regla 2) y, contra producción, tras
`bot.modes.require_live()` (regla 1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bot.data.klines import Candle, interval_delta
from bot.exchange.filters import SymbolFilters

KLINES_PAGE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class BookTicker:
    symbol: str
    bid: Decimal
    bid_qty: Decimal
    ask: Decimal
    ask_qty: Decimal

    @property
    def spread_pct(self) -> Decimal:
        """Spread relativo al precio medio, en % (0,05 = 0,05%)."""
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid * 100 if mid > 0 else Decimal("Infinity")


class ExchangeAdapter(ABC):
    @abstractmethod
    async def get_symbol_filters(self, symbols: Sequence[str]) -> dict[str, SymbolFilters]:
        """Filtros de exchangeInfo de los símbolos pedidos."""

    @abstractmethod
    async def get_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime, limit: int
    ) -> list[Candle]:
        """Velas con open_time en [start, end], como máximo `limit`, en orden ascendente."""

    @abstractmethod
    async def get_book_ticker(self, symbol: str) -> BookTicker:
        """Mejor bid/ask actual."""

    @abstractmethod
    async def server_time(self) -> datetime:
        """Hora del servidor del exchange (UTC)."""

    async def get_klines_range(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Todas las velas con open_time en [start, end], paginando de 1000 en 1000."""
        step = interval_delta(interval)
        out: list[Candle] = []
        cursor = start
        while cursor <= end:
            page = await self.get_klines(symbol, interval, cursor, end, KLINES_PAGE_LIMIT)
            if not page:
                break
            out.extend(page)
            cursor = page[-1].open_time + step
            if len(page) < KLINES_PAGE_LIMIT:
                break
        return out
