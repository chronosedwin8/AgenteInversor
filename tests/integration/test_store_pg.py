from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from bot.data.backfill import HistorySync
from bot.data.klines import Gap
from bot.data.store import CandleStore
from bot.db import migrate
from bot.exchange.filters import SymbolFilters
from tests.conftest import ROOT
from tests.fakes import FakeAdapter, FakeVision, make_series

pytestmark = pytest.mark.integration

Conn = psycopg.AsyncConnection[tuple[object, ...]]
H1 = timedelta(hours=1)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


async def test_migrations_are_idempotent(pg: Conn) -> None:
    assert await migrate(pg, ROOT / "migrations") == []
    cur = await pg.execute("SELECT version FROM schema_migrations")
    assert [r[0] for r in await cur.fetchall()] == ["0001_candles.sql"]


async def test_upsert_roundtrip_and_idempotency(pg: Conn) -> None:
    store = CandleStore(pg)
    candles = make_series("BTCUSDT", "1h", T0, T0 + 23 * H1)
    assert await store.upsert(candles, "vision") == 24
    assert await store.upsert(candles, "rest") == 24  # actualiza, no duplica
    bounds = await store.bounds("BTCUSDT", "1h")
    assert (bounds.first, bounds.last, bounds.count) == (T0, T0 + 23 * H1, 24)

    cur = await pg.execute(
        "SELECT open, high, trades FROM candles WHERE symbol='BTCUSDT' AND open_time=%s", (T0,)
    )
    assert await cur.fetchone() == (Decimal(100), Decimal(101), 5)
    assert await store.count_between("BTCUSDT", "1h", T0, T0 + 5 * H1) == 6
    assert await store.open_times("BTCUSDT", "1h", T0, T0 + H1) == [T0, T0 + H1]


async def test_invalid_ohlc_rejected_by_db(pg: Conn) -> None:
    bad = make_series("BTCUSDT", "1h", T0, T0)[0]
    object.__setattr__(bad, "high", Decimal(1))  # high < low
    with pytest.raises(psycopg.errors.CheckViolation):
        await CandleStore(pg).upsert([bad], "rest")


async def test_exchange_gaps_and_snapshots(pg: Conn, symbol_info: dict[str, Any]) -> None:
    store = CandleStore(pg)
    gap = Gap(T0, T0 + H1, "1h")
    await store.record_exchange_gap("BTCUSDT", gap, "test")
    await store.record_exchange_gap("BTCUSDT", gap, "test")
    assert await store.exchange_gaps("BTCUSDT", "1h") == [gap]

    filters = {"BTCUSDT": SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])}
    await store.save_symbol_snapshots(filters)
    cur = await pg.execute("SELECT raw->>'symbol' FROM symbol_info_snapshots")
    assert await cur.fetchall() == [("BTCUSDT",)]


async def test_history_sync_against_postgres_has_no_gaps(pg: Conn) -> None:
    now = datetime(2026, 10, 30, 14, 7, tzinfo=UTC)
    outage = {datetime(2026, 3, 10, 4, tzinfo=UTC)}
    adapter = FakeAdapter(missing=outage)
    sync = HistorySync(CandleStore(pg), adapter, FakeVision())  # type: ignore[arg-type]
    report = await sync.sync("ETHUSDT", "1h", 13, now)
    assert report.ok, report
    assert report.count == report.expected
    # Se borra una hora que el "exchange" (REST) tampoco tiene: el relleno no puede inventarla.
    await pg.execute(
        "DELETE FROM candles WHERE symbol='ETHUSDT' AND interval='1h' AND open_time=%s",
        (datetime(2026, 3, 10, 4, tzinfo=UTC),),
    )
    rest_only = HistorySync(CandleStore(pg), adapter, None)  # type: ignore[arg-type]
    report = await rest_only.sync("ETHUSDT", "1h", 13, now)
    assert report.gaps == []
    assert len(report.exchange_gaps) == 1  # el "exchange" no la tiene: queda registrada
    assert report.count == report.expected - 1
