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

    async def acquire_lock(self, account: str) -> bool:
        """Bloqueo consultivo de sesión: un solo proceso opera cada cuenta. Se libera solo al
        cerrarse la conexión (también si el proceso muere)."""
        cur = await self._conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (f"trader:{account}",)
        )
        row = await cur.fetchone()
        return bool(row and row[0])

    async def heartbeat(self, service: str, started_at: datetime, now: datetime) -> None:
        await self._conn.execute(
            "INSERT INTO service_heartbeat (service, started_at, last_at) VALUES (%s, %s, %s)"
            " ON CONFLICT (service) DO UPDATE SET started_at = EXCLUDED.started_at,"
            " last_at = EXCLUDED.last_at",
            (service, started_at, now),
        )

    async def last_heartbeat(self, service: str) -> datetime | None:
        cur = await self._conn.execute(
            "SELECT last_at FROM service_heartbeat WHERE service = %s", (service,)
        )
        row = await cur.fetchone()
        return cast(datetime, row[0]) if row else None

    async def add_event(
        self, account: str, at: datetime, kind: str, detail: dict[str, Any]
    ) -> None:
        await self._conn.execute(
            "INSERT INTO session_events (account, at, kind, detail) VALUES (%s, %s, %s, %s)",
            (account, at, kind, Jsonb(detail)),
        )

    async def last_report_week(self) -> str | None:
        cur = await self._conn.execute(
            "SELECT detail->>'week' FROM session_events WHERE account = 'system'"
            " AND kind = 'weekly_report' ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return cast(str, row[0]) if row else None

    async def mark_report_sent(self, week: str, at: datetime, emailed: bool) -> None:
        await self.add_event("system", at, "weekly_report", {"week": week, "emailed": emailed})

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
