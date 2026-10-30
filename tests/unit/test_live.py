from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bot.data.backfill import HistorySync
from bot.data.klines import Candle, datetime_to_ms
from bot.data.live import LiveCandles
from tests.fakes import FakeAdapter, MemoryStore, make_series

M15 = timedelta(minutes=15)
NOW = datetime(2026, 10, 30, 14, 7, tzinfo=UTC)


def ws_msg(open_time: datetime, closed: bool, symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "e": "kline",
        "s": symbol,
        "k": {
            "t": datetime_to_ms(open_time), "T": datetime_to_ms(open_time + M15) - 1,
            "s": symbol, "i": "15m", "o": "1", "c": "2", "h": "3", "l": "0.5",
            "v": "10", "n": 5, "x": closed, "q": "15", "V": "4", "Q": "6",
        },
    }  # fmt: skip


def make_live(store: MemoryStore, adapter: FakeAdapter, seen: list[Candle]) -> LiveCandles:
    async def on_candle(c: Candle) -> None:
        seen.append(c)

    sync = HistorySync(store, adapter, None)  # type: ignore[arg-type]
    return LiveCandles(
        store, sync, ["BTCUSDT"], ["15m"], "wss://unused",  # type: ignore[arg-type]
        on_candle=on_candle, clock=lambda: NOW,
    )  # fmt: skip


async def test_open_candles_are_ignored() -> None:
    store, seen = MemoryStore(), []  # type: ignore[var-annotated]
    live = make_live(store, FakeAdapter(), seen)
    assert await live.handle_message(ws_msg(NOW.replace(minute=0), closed=False)) is None
    assert store.rows == {} and seen == []


async def test_closed_candle_is_stored_and_emitted() -> None:
    store, seen = MemoryStore(), []  # type: ignore[var-annotated]
    live = make_live(store, FakeAdapter(), seen)
    t = datetime(2026, 10, 30, 13, 45, tzinfo=UTC)
    candle = await live.handle_message(ws_msg(t, closed=True))
    assert candle is not None and candle.open_time == t
    assert store.sources() == {"ws": 1}
    assert seen == [candle]
    assert live.last[("BTCUSDT", "15m")] == t


async def test_jump_in_stream_triggers_rest_backfill() -> None:
    store, adapter, seen = MemoryStore(), FakeAdapter(), []  # type: ignore[var-annotated]
    live = make_live(store, adapter, seen)
    t0 = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    await live.handle_message(ws_msg(t0, closed=True))
    # Se pierde la conexión: la siguiente vela cerrada llega 4 velas después.
    await live.handle_message(ws_msg(t0 + 4 * M15, closed=True))
    times = await store.open_times("BTCUSDT", "15m", t0, t0 + 4 * M15)
    assert times == [t0 + i * M15 for i in range(5)]
    assert store.sources() == {"ws": 2, "rest": 3}


async def test_catch_up_on_start_fills_from_last_stored() -> None:
    store, adapter, seen = MemoryStore(), FakeAdapter(), []  # type: ignore[var-annotated]
    start = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
    await store.upsert(make_series("BTCUSDT", "15m", start, start + 10 * M15), "vision")
    live = make_live(store, adapter, seen)
    await live.catch_up()
    bounds = await store.bounds("BTCUSDT", "15m")
    assert bounds.last == datetime(2026, 10, 30, 13, 45, tzinfo=UTC)
    assert live.last[("BTCUSDT", "15m")] == bounds.last


async def test_duplicate_closed_candle_is_idempotent() -> None:
    store, seen = MemoryStore(), []  # type: ignore[var-annotated]
    live = make_live(store, FakeAdapter(), seen)
    t = datetime(2026, 10, 30, 13, 45, tzinfo=UTC)
    await live.handle_message(ws_msg(t, closed=True))
    await live.handle_message(ws_msg(t, closed=True))
    assert len(store.rows) == 1
