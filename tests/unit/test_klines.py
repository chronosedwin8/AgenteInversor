from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.data.klines import (
    Candle,
    Gap,
    datetime_to_ms,
    find_gaps,
    floor_time,
    interval_delta,
    ts_to_datetime,
)
from bot.data.vision import parse_kline_csv
from tests.conftest import FIXTURES

T0 = datetime(2026, 9, 1, tzinfo=UTC)
M15 = timedelta(minutes=15)


def test_ms_and_us_timestamps_map_to_same_instant() -> None:
    assert ts_to_datetime(1788220800000) == ts_to_datetime(1788220800000000) == T0
    assert ts_to_datetime(1788221699999999) == T0 + M15 - timedelta(microseconds=1)
    assert datetime_to_ms(T0) == 1788220800000


def test_parse_vision_csv_microseconds_2025_onwards() -> None:
    data = (FIXTURES / "BTCUSDT-15m-2026-09-head.csv").read_bytes()
    candles = parse_kline_csv(data, "BTCUSDT", "15m")
    assert len(candles) == 5
    assert candles[0].open_time == T0
    assert candles[1].open_time - candles[0].open_time == M15
    assert candles[0].open == Decimal("78581.30000000")
    assert candles[0].trades == 26261


def test_parse_vision_csv_milliseconds_before_2025() -> None:
    data = (FIXTURES / "BTCUSDT-15m-2024-09-head.csv").read_bytes()
    candles = parse_kline_csv(data, "BTCUSDT", "15m")
    assert candles[0].open_time == datetime(2024, 9, 1, tzinfo=UTC)
    assert candles[0].close_time < candles[1].open_time


def test_csv_header_tolerated() -> None:
    data = b"open_time,open,high,low,close,volume,close_time,qv,n,tb,tq,ignore\n"
    data += (FIXTURES / "BTCUSDT-15m-2026-09-head.csv").read_bytes()
    assert len(parse_kline_csv(data, "BTCUSDT", "15m")) == 5


def test_from_rest_row() -> None:
    row = [1793367000000, "77284.00", "77304.01", "76967.97", "76984.73", "496.1", 1793367899999,
           "38261810.37", 118246, "199.97", "15423333.75", "0"]  # fmt: skip
    c = Candle.from_row("BTCUSDT", "15m", row)
    assert c.open_time == datetime(2026, 10, 30, 13, 30, tzinfo=UTC)
    assert c.high == Decimal("77304.01")


def test_from_ws_payload() -> None:
    k = {"t": 1788220800000, "T": 1788221699999, "s": "BTCUSDT", "i": "15m", "o": "1",
         "c": "2", "h": "3", "l": "0.5", "v": "10", "n": 5, "x": True, "q": "15", "V": "4",
         "Q": "6"}  # fmt: skip
    c = Candle.from_ws(k)
    assert (c.symbol, c.interval, c.open_time, c.close) == ("BTCUSDT", "15m", T0, Decimal(2))


def test_floor_time() -> None:
    assert floor_time(T0 + timedelta(minutes=29, seconds=59), "15m") == T0 + M15
    assert floor_time(T0 + timedelta(minutes=59), "1h") == T0
    with pytest.raises(ValueError):
        interval_delta("7m")


# ------------------------------------------------------------------ huecos


def series(n: int, skip: frozenset[int] | set[int] = frozenset()) -> list[datetime]:
    return [T0 + i * M15 for i in range(n) if i not in skip]


def test_no_gaps() -> None:
    assert find_gaps(series(10), T0, T0 + 9 * M15, "15m") == []


def test_gap_in_middle_start_and_end() -> None:
    gaps = find_gaps(series(10, {0, 4, 5, 9}), T0, T0 + 9 * M15, "15m")
    assert gaps == [
        Gap(T0, T0, "15m"),
        Gap(T0 + 4 * M15, T0 + 5 * M15, "15m"),
        Gap(T0 + 9 * M15, T0 + 9 * M15, "15m"),
    ]
    assert [g.missing for g in gaps] == [1, 2, 1]


def test_empty_series_is_one_big_gap() -> None:
    gaps = find_gaps([], T0, T0 + 95 * M15, "15m")
    assert len(gaps) == 1 and gaps[0].missing == 96


def test_duplicates_unsorted_and_out_of_range_ignored() -> None:
    times = list(reversed(series(5))) + series(5) + [T0 - M15, T0 + 50 * M15]
    assert find_gaps(times, T0, T0 + 4 * M15, "15m") == []


def test_misaligned_times_do_not_hide_gaps() -> None:
    times = [*series(3, {1}), T0 + M15 + timedelta(minutes=1)]
    assert find_gaps(times, T0, T0 + 2 * M15, "15m") == [Gap(T0 + M15, T0 + M15, "15m")]
