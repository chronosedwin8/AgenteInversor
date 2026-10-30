"""Modelo de vela y utilidades de intervalos/huecos (funciones puras, sin E/S)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

_INTERVALS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
}

# Por debajo de esto el timestamp está en ms; por encima, en µs. (1e14 ms ≈ año 5138;
# 1e14 µs ≈ año 1973.) data.binance.vision usa µs para spot desde 2025-01-01.
_MICROS_THRESHOLD = 10**14
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def interval_delta(interval: str) -> timedelta:
    try:
        return _INTERVALS[interval]
    except KeyError:
        raise ValueError(f"intervalo no soportado: {interval}") from None


def ts_to_datetime(ts: int) -> datetime:
    """Timestamp de Binance (ms o µs) → datetime UTC."""
    micros = ts if ts >= _MICROS_THRESHOLD else ts * 1000
    return _EPOCH + timedelta(microseconds=micros)


def datetime_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria")
    return (dt - _EPOCH) // timedelta(milliseconds=1)


def floor_time(dt: datetime, interval: str) -> datetime:
    """Inicio de la vela que contiene `dt` (intervalos alineados a la época UTC)."""
    step = interval_delta(interval)
    return _EPOCH + ((dt - _EPOCH) // step) * step


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int
    taker_buy_base: Decimal
    taker_buy_quote: Decimal

    @classmethod
    def from_row(cls, symbol: str, interval: str, row: Sequence[Any]) -> Candle:
        """Fila de /api/v3/klines o de un CSV de data.binance.vision (12 columnas)."""
        if len(row) < 11:
            raise ValueError(f"fila de kline incompleta: {row!r}")
        return cls(
            symbol=symbol,
            interval=interval,
            open_time=ts_to_datetime(int(row[0])),
            close_time=ts_to_datetime(int(row[6])),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
            quote_volume=Decimal(str(row[7])),
            trades=int(row[8]),
            taker_buy_base=Decimal(str(row[9])),
            taker_buy_quote=Decimal(str(row[10])),
        )

    @classmethod
    def from_ws(cls, k: dict[str, Any]) -> Candle:
        """Campo `k` de un mensaje del stream <symbol>@kline_<interval>."""
        return cls(
            symbol=str(k["s"]),
            interval=str(k["i"]),
            open_time=ts_to_datetime(int(k["t"])),
            close_time=ts_to_datetime(int(k["T"])),
            open=Decimal(str(k["o"])),
            high=Decimal(str(k["h"])),
            low=Decimal(str(k["l"])),
            close=Decimal(str(k["c"])),
            volume=Decimal(str(k["v"])),
            quote_volume=Decimal(str(k["q"])),
            trades=int(k["n"]),
            taker_buy_base=Decimal(str(k["V"])),
            taker_buy_quote=Decimal(str(k["Q"])),
        )


@dataclass(frozen=True, slots=True)
class Gap:
    """Rango de velas faltantes: open_time de la primera y de la última vela ausente."""

    start: datetime
    end: datetime
    interval: str

    @property
    def missing(self) -> int:
        return int((self.end - self.start) / interval_delta(self.interval)) + 1


def find_gaps(
    open_times: Iterable[datetime], start: datetime, end: datetime, interval: str
) -> list[Gap]:
    """Huecos entre `start` y `end` (ambos open_time de velas, inclusivos).

    `open_times` puede venir desordenado o con duplicados. Valores desalineados se ignoran.
    """
    step = interval_delta(interval)
    if end < start:
        return []
    present = sorted({t for t in open_times if start <= t <= end})
    gaps: list[Gap] = []
    expected = start
    for t in present:
        if (t - start) % step:
            continue
        if t > expected:
            gaps.append(Gap(expected, t - step, interval))
        expected = t + step
    if expected <= end:
        gaps.append(Gap(expected, end, interval))
    return gaps
