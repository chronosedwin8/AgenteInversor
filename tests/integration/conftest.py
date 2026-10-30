from __future__ import annotations

import os
from collections.abc import AsyncIterator

import psycopg
import pytest

from bot.db import connect, migrate
from tests.conftest import ROOT

TEST_DSN = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def pg() -> AsyncIterator[psycopg.AsyncConnection[tuple[object, ...]]]:
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL no definido")
    conn = await connect(TEST_DSN)
    # Esquema limpio en cada test: la BD es exclusiva para tests.
    await conn.execute("DROP SCHEMA public CASCADE")
    await conn.execute("CREATE SCHEMA public")
    await migrate(conn, ROOT / "migrations")
    try:
        yield conn
    finally:
        await conn.close()
