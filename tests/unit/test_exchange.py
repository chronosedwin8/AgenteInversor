"""Paginación de klines, caché de exchangeInfo, spread y descargas de data.binance.vision."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bot.data.vision import Month, VisionChecksumError, VisionClient
from bot.exchange.base import BookTicker
from bot.exchange.filters import SymbolFilters
from bot.exchange.info import ExchangeInfoCache
from tests.conftest import FIXTURES
from tests.fakes import FakeAdapter

M15 = timedelta(minutes=15)


async def test_klines_range_paginates_in_pages_of_1000() -> None:
    adapter = FakeAdapter()
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = start + 2499 * M15
    candles = await adapter.get_klines_range("BTCUSDT", "15m", start, end)
    assert len(candles) == 2500
    assert len(adapter.kline_calls) == 3
    assert candles[0].open_time == start and candles[-1].open_time == end
    assert len({c.open_time for c in candles}) == 2500


def test_spread_pct() -> None:
    bt = BookTicker("BTCUSDT", Decimal("100"), Decimal(1), Decimal("100.05"), Decimal(1))
    assert round(bt.spread_pct, 4) == Decimal("0.0500")


async def test_exchange_info_cache_ttl(symbol_info: dict[str, Any]) -> None:
    filters = {s: SymbolFilters.from_exchange_info(i) for s, i in symbol_info.items()}
    adapter = FakeAdapter(filters=filters)
    now = [0.0]
    snapshots: list[Any] = []

    async def sink(f: dict[str, SymbolFilters]) -> None:
        snapshots.append(f)

    cache = ExchangeInfoCache(
        adapter, ["BTCUSDT"], ttl_seconds=60, on_refresh=sink, clock=lambda: now[0]
    )
    assert (await cache.get("BTCUSDT")).symbol == "BTCUSDT"
    await cache.get("BTCUSDT")
    assert adapter.filter_calls == 1
    now[0] = 61
    await cache.get("BTCUSDT")
    assert adapter.filter_calls == 2
    assert len(snapshots) == 2
    with pytest.raises(LookupError):
        await cache.get("DOGEUSDT")


# ------------------------------------------------------------------ data.binance.vision


def _zip(csv_bytes: bytes, name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv_bytes)
    return buf.getvalue()


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(self.status)

    async def read(self) -> bytes:
        return self._body


class _Session:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.requests = files, []  # type: ignore[var-annotated]

    def get(self, url: str, timeout: Any = None) -> _Resp:
        self.requests.append(url)
        for suffix, body in self.files.items():
            if url.endswith(suffix):
                return _Resp(200, body)
        return _Resp(404, b"")


def _files(tamper: bool = False) -> dict[str, bytes]:
    blob = _zip((FIXTURES / "BTCUSDT-15m-2026-09-head.csv").read_bytes(), "BTCUSDT-15m-2026-09.csv")
    digest = hashlib.sha256(blob + (b"x" if tamper else b"")).hexdigest()
    return {
        "BTCUSDT-15m-2026-09.zip": blob,
        "BTCUSDT-15m-2026-09.zip.CHECKSUM": f"{digest}  BTCUSDT-15m-2026-09.zip".encode(),
    }


async def test_vision_downloads_verifies_and_caches(tmp_path: Path) -> None:
    session = _Session(_files())
    client = VisionClient("https://data.binance.vision", tmp_path, session)  # type: ignore[arg-type]
    candles = await client.monthly("BTCUSDT", "15m", Month(2026, 9))
    assert candles is not None and len(candles) == 5
    assert session.requests[0] == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-2026-09.zip"
    )
    n = len(session.requests)
    assert await client.monthly("BTCUSDT", "15m", Month(2026, 9)) == candles
    assert len(session.requests) == n  # segunda vez desde caché


async def test_vision_checksum_mismatch_is_rejected_and_not_cached(tmp_path: Path) -> None:
    client = VisionClient("https://x", tmp_path, _Session(_files(tamper=True)))  # type: ignore[arg-type]
    with pytest.raises(VisionChecksumError):
        await client.monthly("BTCUSDT", "15m", Month(2026, 9))
    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240


async def test_vision_month_not_published_returns_none(tmp_path: Path) -> None:
    client = VisionClient("https://x", tmp_path, _Session({}))  # type: ignore[arg-type]
    assert await client.monthly("BTCUSDT", "15m", Month(2026, 10)) is None
