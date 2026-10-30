"""Contra la API pública real (solo datos de mercado). Excluido de CI: `pytest -m network`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bot.data.backfill import last_closed_open_time
from bot.exchange.binance_spot import BinanceSpot

pytestmark = pytest.mark.network


async def test_exchange_info_and_rounding_live() -> None:
    adapter = BinanceSpot()
    filters = await adapter.get_symbol_filters(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    for f in filters.values():
        assert f.is_trading and f.supports("LIMIT_MAKER")
        book = await adapter.get_book_ticker(f.symbol)
        price = f.round_price(book.bid, "down")
        qty = f.min_qty_for_notional(price)
        assert f.validate(price, qty) == []


async def test_klines_live_paginate_and_are_contiguous() -> None:
    adapter = BinanceSpot()
    now = datetime.now(UTC)
    end = last_closed_open_time(now, "15m")
    start = end - timedelta(minutes=15) * 1199
    candles = await adapter.get_klines_range("BTCUSDT", "15m", start, end)
    assert len(candles) == 1200
    assert candles[0].open_time == start and candles[-1].open_time == end


async def test_server_time_is_close_to_local_clock() -> None:
    skew = abs(await BinanceSpot().server_time() - datetime.now(UTC))
    assert skew < timedelta(seconds=5)
