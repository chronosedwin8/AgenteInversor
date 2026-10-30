"""Sincronización de historia: meses completos desde data.binance.vision, mes en curso y
huecos por REST. Idempotente: se puede ejecutar en cada arranque."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from bot.data.klines import Gap, find_gaps, floor_time, interval_delta
from bot.data.store import CandleStore, Source
from bot.data.vision import Month, VisionClient, last_complete_months
from bot.exchange.base import ExchangeAdapter
from bot.log import get_logger

log = get_logger(__name__)

# Huecos más recientes que esto no se declaran "del exchange": pueden ser solo latencia.
_RECENT_GRACE_INTERVALS = 2


def month_start(m: Month) -> datetime:
    return datetime(m.year, m.month, 1, tzinfo=UTC)


def last_closed_open_time(now: datetime, interval: str) -> datetime:
    """open_time de la última vela cerrada a la hora `now`."""
    return floor_time(now, interval) - interval_delta(interval)


@dataclass
class VerifyReport:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    expected: int
    count: int
    first: datetime | None
    last: datetime | None
    gaps: list[Gap] = field(default_factory=list)
    exchange_gaps: list[Gap] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Sin huecos sin explicar (los del exchange quedan registrados aparte)."""
        return not self.gaps and self.first == self.start and self.last == self.end


class HistorySync:
    def __init__(self, store: CandleStore, adapter: ExchangeAdapter, vision: VisionClient | None):
        self.store = store
        self.adapter = adapter
        self.vision = vision

    async def sync(
        self, symbol: str, interval: str, history_months: int, now: datetime
    ) -> VerifyReport:
        step = interval_delta(interval)
        current = Month(now.year, now.month)
        months = last_complete_months(current, history_months)

        for m in months:
            start = month_start(m)
            end = month_start(m.next()) - step
            expected = (end - start) // step + 1
            if await self.store.count_between(symbol, interval, start, end) == expected:
                continue
            candles = (
                await self.vision.monthly(symbol, interval, m) if self.vision is not None else None
            )
            source: Source = "vision"
            if candles is None:
                candles = await self.adapter.get_klines_range(symbol, interval, start, end)
                source = "rest"
            n = await self.store.upsert(candles, source)
            log.info(
                "history_month_loaded",
                symbol=symbol,
                interval=interval,
                month=str(m),
                source=source,
                rows=n,
            )

        history_start = month_start(months[0])
        end = last_closed_open_time(now, interval)
        await self.fill_range(symbol, interval, history_start, end, now)
        return await self.verify(symbol, interval, history_start, end)

    async def fill_range(
        self, symbol: str, interval: str, start: datetime, end: datetime, now: datetime
    ) -> int:
        """Rellena por REST los huecos en [start, end]. Lo que el exchange no tiene se registra."""
        step = interval_delta(interval)
        present = await self.store.open_times(symbol, interval, start, end)
        gaps = find_gaps(present, start, end, interval)
        filled = 0
        for gap in gaps:
            candles = await self.adapter.get_klines_range(symbol, interval, gap.start, gap.end)
            filled += await self.store.upsert(candles, "rest")
        if gaps:
            log.info("gaps_filled", symbol=symbol, interval=interval, gaps=len(gaps), rows=filled)
            present = await self.store.open_times(symbol, interval, start, end)
            grace = now - step * _RECENT_GRACE_INTERVALS
            for gap in find_gaps(present, start, end, interval):
                if gap.end < grace:
                    await self.store.record_exchange_gap(
                        symbol, gap, "Binance no devuelve velas para este rango (REST vacío)"
                    )
                    log.warning(
                        "exchange_gap",
                        symbol=symbol,
                        interval=interval,
                        start=gap.start.isoformat(),
                        end=gap.end.isoformat(),
                        missing=gap.missing,
                    )
        return filled

    async def verify(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> VerifyReport:
        step = interval_delta(interval)
        present = await self.store.open_times(symbol, interval, start, end)
        exchange_gaps = await self.store.exchange_gaps(symbol, interval)
        known = {(g.start, g.end) for g in exchange_gaps}
        all_gaps = find_gaps(present, start, end, interval)
        gaps = [g for g in all_gaps if (g.start, g.end) not in known]
        return VerifyReport(
            symbol=symbol,
            interval=interval,
            start=start,
            end=end,
            expected=(end - start) // step + 1,
            count=len(present),
            first=present[0] if present else None,
            last=present[-1] if present else None,
            gaps=gaps,
            exchange_gaps=[g for g in exchange_gaps if g.start >= start and g.end <= end],
        )


def history_window(now: datetime, interval: str, history_months: int) -> tuple[datetime, datetime]:
    current = Month(now.year, now.month)
    months = last_complete_months(current, history_months)
    return month_start(months[0]), last_closed_open_time(now, interval)
