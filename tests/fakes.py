"""Dobles de prueba en memoria para el store y el adaptador de exchange."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from bot.data.klines import Candle, Gap, interval_delta
from bot.data.store import SeriesBounds
from bot.exchange.base import BookTicker, ExchangeAdapter
from bot.exchange.filters import SymbolFilters


def make_candle(symbol: str, interval: str, open_time: datetime, price: str = "100") -> Candle:
    p = Decimal(price)
    step = interval_delta(interval)
    return Candle(
        symbol, interval, open_time, open_time + step - timedelta(milliseconds=1),
        p, p + 1, p - 1, p, Decimal(10), Decimal(1000), 5, Decimal(4), Decimal(400),
    )  # fmt: skip


def make_series(symbol: str, interval: str, start: datetime, end: datetime) -> list[Candle]:
    step = interval_delta(interval)
    out, t = [], start
    while t <= end:
        out.append(make_candle(symbol, interval, t))
        t += step
    return out


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, datetime], tuple[Candle, str]] = {}
        self.gaps: dict[tuple[str, str, datetime], Gap] = {}
        self.snapshots: list[dict[str, SymbolFilters]] = []

    async def upsert(self, candles: Sequence[Candle], source: str) -> int:
        for c in candles:
            self.rows[(c.symbol, c.interval, c.open_time)] = (c, source)
        return len(candles)

    def _times(self, symbol: str, interval: str) -> list[datetime]:
        return sorted(t for (s, i, t) in self.rows if s == symbol and i == interval)

    async def bounds(self, symbol: str, interval: str) -> SeriesBounds:
        ts = self._times(symbol, interval)
        return SeriesBounds(ts[0] if ts else None, ts[-1] if ts else None, len(ts))

    async def count_between(self, symbol: str, interval: str, a: datetime, b: datetime) -> int:
        return len([t for t in self._times(symbol, interval) if a <= t <= b])

    async def open_times(
        self, symbol: str, interval: str, a: datetime, b: datetime
    ) -> list[datetime]:
        return [t for t in self._times(symbol, interval) if a <= t <= b]

    async def save_symbol_snapshots(self, filters: dict[str, SymbolFilters]) -> None:
        self.snapshots.append(filters)

    async def record_exchange_gap(self, symbol: str, gap: Gap, note: str) -> None:
        self.gaps[(symbol, gap.interval, gap.start)] = gap

    async def exchange_gaps(self, symbol: str, interval: str) -> list[Gap]:
        return sorted(
            (g for (s, i, _), g in self.gaps.items() if s == symbol and i == interval),
            key=lambda g: g.start,
        )

    def sources(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, src in self.rows.values():
            out[src] = out.get(src, 0) + 1
        return out


class FakeAdapter(ExchangeAdapter):
    """Sirve velas sintéticas continuas, salvo los open_time en `missing` (huecos del exchange)."""

    def __init__(
        self,
        missing: Iterable[datetime] = (),
        filters: dict[str, SymbolFilters] | None = None,
    ) -> None:
        self.missing = set(missing)
        self.filters = filters or {}
        self.kline_calls: list[tuple[str, str, datetime, datetime, int]] = []
        self.filter_calls = 0

    async def get_symbol_filters(self, symbols: Sequence[str]) -> dict[str, SymbolFilters]:
        self.filter_calls += 1
        return {s: self.filters[s] for s in symbols}

    async def get_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime, limit: int
    ) -> list[Candle]:
        self.kline_calls.append((symbol, interval, start, end, limit))
        out = [
            c for c in make_series(symbol, interval, start, end) if c.open_time not in self.missing
        ]
        return out[:limit]

    async def get_book_ticker(self, symbol: str) -> BookTicker:
        return BookTicker(symbol, Decimal(100), Decimal(1), Decimal("100.01"), Decimal(1))

    async def server_time(self) -> datetime:
        raise NotImplementedError


class FakeVision:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.calls: list[str] = []

    async def monthly(self, symbol: str, interval: str, month: Any) -> list[Candle] | None:
        from bot.data.backfill import month_start

        self.calls.append(f"{symbol}-{interval}-{month}")
        if not self.available:
            return None
        step = interval_delta(interval)
        return make_series(symbol, interval, month_start(month), month_start(month.next()) - step)
