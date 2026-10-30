from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.data.backfill import HistorySync, last_closed_open_time, month_start
from bot.data.vision import Month, last_complete_months
from tests.fakes import FakeAdapter, FakeVision, MemoryStore, make_series

NOW = datetime(2026, 10, 30, 14, 7, tzinfo=UTC)
H1 = timedelta(hours=1)


def test_last_complete_months_excludes_current() -> None:
    months = last_complete_months(Month(2026, 10), 13)
    assert months[0] == Month(2025, 9)
    assert months[-1] == Month(2026, 9)
    assert len(months) == 13


def test_last_closed_open_time() -> None:
    assert last_closed_open_time(NOW, "15m") == datetime(2026, 10, 30, 13, 45, tzinfo=UTC)
    assert last_closed_open_time(NOW, "1h") == datetime(2026, 10, 30, 13, 0, tzinfo=UTC)


async def test_full_sync_has_no_gaps_and_uses_vision_for_complete_months() -> None:
    store, adapter, vision = MemoryStore(), FakeAdapter(), FakeVision()
    sync = HistorySync(store, adapter, vision)  # type: ignore[arg-type]
    report = await sync.sync("BTCUSDT", "1h", 13, NOW)

    assert report.ok, report
    assert report.start == datetime(2025, 9, 1, tzinfo=UTC)
    assert report.end == datetime(2026, 10, 30, 13, tzinfo=UTC)
    assert report.count == report.expected
    assert len(vision.calls) == 13
    # El mes en curso viene por REST.
    month_rows = (report.end - month_start(Month(2026, 10))) // H1 + 1
    assert store.sources() == {"vision": report.expected - month_rows, "rest": month_rows}


async def test_sync_is_idempotent_and_skips_complete_months() -> None:
    store, adapter, vision = MemoryStore(), FakeAdapter(), FakeVision()
    sync = HistorySync(store, adapter, vision)  # type: ignore[arg-type]
    await sync.sync("BTCUSDT", "1h", 13, NOW)
    vision.calls.clear()
    adapter.kline_calls.clear()

    report = await sync.sync("BTCUSDT", "1h", 13, NOW)
    assert report.ok
    assert vision.calls == []  # nada que volver a descargar
    assert adapter.kline_calls == []


async def test_rest_fallback_when_vision_month_not_published() -> None:
    store, adapter, vision = MemoryStore(), FakeAdapter(), FakeVision(available=False)
    report = await HistorySync(store, adapter, vision).sync("ETHUSDT", "1h", 12, NOW)  # type: ignore[arg-type]
    assert report.ok
    assert set(store.sources()) == {"rest"}


async def test_gaps_are_refilled_from_rest() -> None:
    store, adapter = MemoryStore(), FakeAdapter()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 2, tzinfo=UTC)
    series = make_series("SOLUSDT", "1h", start, end)
    holes = {series[3].open_time, series[4].open_time, series[10].open_time}
    await store.upsert([c for c in series if c.open_time not in holes], "vision")

    sync = HistorySync(store, adapter, None)  # type: ignore[arg-type]
    filled = await sync.fill_range("SOLUSDT", "1h", start, end, NOW)
    assert filled == 3
    assert (await sync.verify("SOLUSDT", "1h", start, end)).ok


async def test_unfillable_exchange_gap_is_recorded_not_hidden() -> None:
    outage = {datetime(2026, 10, 1, 5, tzinfo=UTC), datetime(2026, 10, 1, 6, tzinfo=UTC)}
    store, adapter = MemoryStore(), FakeAdapter(missing=outage)
    start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)
    sync = HistorySync(store, adapter, None)  # type: ignore[arg-type]

    await sync.fill_range("BTCUSDT", "1h", start, end, NOW)
    report = await sync.verify("BTCUSDT", "1h", start, end)

    assert report.gaps == []  # no quedan huecos *sin explicar*
    assert len(report.exchange_gaps) == 1
    assert report.exchange_gaps[0].missing == 2
    assert report.count == report.expected - 2  # pero las velas faltan de verdad


async def test_recent_missing_candle_is_not_declared_exchange_gap() -> None:
    last = last_closed_open_time(NOW, "1h")
    store, adapter = MemoryStore(), FakeAdapter(missing={last})
    sync = HistorySync(store, adapter, None)  # type: ignore[arg-type]
    start = last - 10 * H1
    await sync.fill_range("BTCUSDT", "1h", start, last, NOW)
    report = await sync.verify("BTCUSDT", "1h", start, last)
    assert report.exchange_gaps == []
    assert not report.ok  # se reintentará en la próxima pasada
