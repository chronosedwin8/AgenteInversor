"""Persistencia de sesiones (paper/testnet): estado, operaciones cerradas y eventos."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

import psycopg
from psycopg.types.json import Jsonb

from bot.portfolio.portfolio import Trade

Conn = psycopg.AsyncConnection[tuple[object, ...]]


class SessionStore:
    def __init__(self, conn: Conn):
        self._conn = conn

    async def load(self, account: str) -> tuple[datetime | None, str] | None:
        cur = await self._conn.execute(
            "SELECT last_bar_time, state::text FROM session_state WHERE account = %s", (account,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return cast(datetime | None, row[0]), cast(str, row[1])

    async def save(self, account: str, last_bar_time: datetime, state_json: str,
                   trades: Sequence[Trade], events: Sequence[tuple[datetime, str, dict[str, Any]]]
                   ) -> None:  # fmt: skip
        """Todo en una transacción: estado, operaciones nuevas y eventos se guardan juntos."""
        async with self._conn.transaction(), self._conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO session_state (account, last_bar_time, state, updated_at)"
                " VALUES (%s, %s, %s::jsonb, now()) ON CONFLICT (account) DO UPDATE SET"
                " last_bar_time = EXCLUDED.last_bar_time, state = EXCLUDED.state,"
                " updated_at = now()",
                (account, last_bar_time, state_json),
            )
            if trades:
                await cur.executemany(
                    "INSERT INTO session_trades (account, symbol, opened_at, closed_at, qty,"
                    " entry_price, exit_price, pnl, fees, r_multiple, exits) VALUES"
                    " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (account, symbol, opened_at) DO NOTHING",
                    [(account, t.symbol, t.opened_at, t.closed_at, t.qty, t.entry_price,
                      t.avg_exit_price, t.pnl, t.fees, t.r_multiple, "|".join(t.exits))
                     for t in trades],
                )  # fmt: skip
            if events:
                await cur.executemany(
                    "INSERT INTO session_events (account, at, kind, detail)"
                    " VALUES (%s, %s, %s, %s)",
                    [(account, at, kind, Jsonb(detail)) for at, kind, detail in events],
                )

    async def trades(self, account: str) -> list[tuple[Any, ...]]:
        cur = await self._conn.execute(
            "SELECT symbol, opened_at, closed_at, qty, entry_price, exit_price, pnl, r_multiple,"
            " exits FROM session_trades WHERE account = %s ORDER BY closed_at",
            (account,),
        )
        return list(await cur.fetchall())

    async def recent_events(self, account: str, limit: int = 20) -> list[tuple[Any, ...]]:
        cur = await self._conn.execute(
            "SELECT at, kind, detail FROM session_events WHERE account = %s"
            " ORDER BY id DESC LIMIT %s",
            (account, limit),
        )
        return list(await cur.fetchall())
