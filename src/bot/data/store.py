"""Persistencia de velas y metadatos de mercado en PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import psycopg
from psycopg.types.json import Jsonb

from bot.data.klines import Candle, Gap
from bot.exchange.filters import SymbolFilters

Source = Literal["vision", "rest", "ws"]
Conn = psycopg.AsyncConnection[tuple[object, ...]]

_COLUMNS = (
    "symbol, interval, open_time, close_time, open, high, low, close, volume, quote_volume,"
    " trades, taker_buy_base, taker_buy_quote, source"
)


@dataclass(frozen=True)
class SeriesBounds:
    first: datetime | None
    last: datetime | None
    count: int


class CandleStore:
    def __init__(self, conn: Conn):
        self._conn = conn

    async def upsert(self, candles: Sequence[Candle], source: Source) -> int:
        """Inserta o actualiza velas (clave: symbol, interval, open_time). Idempotente."""
        if not candles:
            return 0
        async with self._conn.transaction():
            await self._conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS candles_stage"
                " (LIKE candles INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
            )
            async with self._conn.cursor() as cur:
                async with cur.copy(f"COPY candles_stage ({_COLUMNS}) FROM STDIN") as copy:
                    for c in candles:
                        await copy.write_row(
                            (
                                c.symbol, c.interval, c.open_time, c.close_time,
                                c.open, c.high, c.low, c.close, c.volume, c.quote_volume,
                                c.trades, c.taker_buy_base, c.taker_buy_quote, source,
                            )
                        )  # fmt: skip
                # _COLUMNS es una constante del módulo, no entrada externa.
                await cur.execute(
                    f"INSERT INTO candles ({_COLUMNS}) SELECT {_COLUMNS} FROM candles_stage"  # noqa: S608
                    " ON CONFLICT (symbol, interval, open_time) DO UPDATE SET"
                    " close_time = EXCLUDED.close_time, open = EXCLUDED.open,"
                    " high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,"
                    " volume = EXCLUDED.volume, quote_volume = EXCLUDED.quote_volume,"
                    " trades = EXCLUDED.trades, taker_buy_base = EXCLUDED.taker_buy_base,"
                    " taker_buy_quote = EXCLUDED.taker_buy_quote"
                )
                return cur.rowcount

    async def bounds(self, symbol: str, interval: str) -> SeriesBounds:
        cur = await self._conn.execute(
            "SELECT min(open_time), max(open_time), count(*) FROM candles"
            " WHERE symbol = %s AND interval = %s",
            (symbol, interval),
        )
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("consulta agregada sin filas")
        return SeriesBounds(
            cast(datetime | None, row[0]), cast(datetime | None, row[1]), cast(int, row[2])
        )

    async def count_between(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> int:
        cur = await self._conn.execute(
            "SELECT count(*) FROM candles WHERE symbol = %s AND interval = %s"
            " AND open_time >= %s AND open_time <= %s",
            (symbol, interval, start, end),
        )
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("consulta agregada sin filas")
        return cast(int, row[0])

    async def open_times(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[datetime]:
        cur = await self._conn.execute(
            "SELECT open_time FROM candles WHERE symbol = %s AND interval = %s"
            " AND open_time >= %s AND open_time <= %s ORDER BY open_time",
            (symbol, interval, start, end),
        )
        return [cast(datetime, row[0]) for row in await cur.fetchall()]

    async def save_symbol_snapshots(self, filters: dict[str, SymbolFilters]) -> None:
        async with self._conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO symbol_info_snapshots (symbol, raw) VALUES (%s, %s)",
                [(f.symbol, Jsonb(f.raw)) for f in filters.values()],
            )

    async def record_exchange_gap(self, symbol: str, gap: Gap, note: str) -> None:
        await self._conn.execute(
            "INSERT INTO candle_gaps_exchange (symbol, interval, gap_start, gap_end, note)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (symbol, interval, gap_start) DO UPDATE"
            " SET gap_end = EXCLUDED.gap_end, note = EXCLUDED.note, detected_at = now()",
            (symbol, gap.interval, gap.start, gap.end, note),
        )

    async def exchange_gaps(self, symbol: str, interval: str) -> list[Gap]:
        cur = await self._conn.execute(
            "SELECT gap_start, gap_end FROM candle_gaps_exchange"
            " WHERE symbol = %s AND interval = %s ORDER BY gap_start",
            (symbol, interval),
        )
        rows = await cur.fetchall()
        return [Gap(cast(datetime, r[0]), cast(datetime, r[1]), interval) for r in rows]
