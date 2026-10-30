"""Conexión a PostgreSQL y migraciones SQL numeradas (migrations/NNNN_nombre.sql)."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

from bot.log import get_logger

log = get_logger(__name__)

DEFAULT_MIGRATIONS_DIR = Path(os.environ.get("MIGRATIONS_DIR", "migrations"))


async def connect(dsn: str) -> psycopg.AsyncConnection[tuple[object, ...]]:
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True)


def load_migrations(directory: Path) -> list[tuple[str, str]]:
    """(nombre, sql) de cada archivo NNNN_*.sql, en orden."""
    files = sorted(p for p in directory.glob("*.sql") if p.name[:4].isdigit())
    if not files:
        raise FileNotFoundError(f"no hay migraciones en {directory.resolve()}")
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


async def migrate(conn: psycopg.AsyncConnection[tuple[object, ...]], directory: Path) -> list[str]:
    """Aplica en orden las migraciones pendientes, cada una en su transacción. Idempotente."""
    migrations = load_migrations(directory)
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    # Bloqueo consultivo: dos procesos (bot y CLI) no migran a la vez.
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(727274)")
        cur = await conn.execute("SELECT version FROM schema_migrations")
        done = {row[0] for row in await cur.fetchall()}
        applied: list[str] = []
        for name, sql in migrations:
            if name in done:
                continue
            await conn.execute(sql.encode())
            await conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (name,))
            applied.append(name)
            log.info("migration_applied", version=name)
    return applied
