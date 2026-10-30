"""Velas en vivo por WebSocket (solo lectura). Guarda únicamente velas cerradas; si detecta un
salto (desconexión, reinicio) lo rellena por REST antes de seguir."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from bot.data.backfill import HistorySync, last_closed_open_time
from bot.data.klines import Candle, interval_delta
from bot.data.store import CandleStore
from bot.log import get_logger

log = get_logger(__name__)

CandleCallback = Callable[[Candle], Awaitable[None]]
Key = tuple[str, str]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_dict(msg: Any) -> dict[str, Any]:
    if isinstance(msg, dict):
        return msg
    to_dict = getattr(msg, "to_dict", None)
    if callable(to_dict):
        result: dict[str, Any] = to_dict()
        return result
    raise TypeError(f"mensaje de stream no reconocido: {type(msg)!r}")


class LiveCandles:
    def __init__(
        self,
        store: CandleStore,
        sync: HistorySync,
        symbols: Sequence[str],
        intervals: Sequence[str],
        stream_url: str,
        on_candle: CandleCallback | None = None,
        clock: Callable[[], datetime] = _utcnow,
        silence_timeout_s: float = 60.0,
    ):
        self.store = store
        self.sync = sync
        self.keys: list[Key] = [(s, i) for s in symbols for i in intervals]
        self.stream_url = stream_url
        self.on_candle = on_candle
        self.clock = clock
        self.silence_timeout_s = silence_timeout_s
        self.last: dict[Key, datetime] = {}
        self.received = 0

    async def catch_up(self) -> None:
        """Rellena por REST desde la última vela guardada hasta la última cerrada."""
        now = self.clock()
        for symbol, interval in self.keys:
            bounds = await self.store.bounds(symbol, interval)
            end = last_closed_open_time(now, interval)
            if bounds.last is None:
                log.warning("no_history", symbol=symbol, interval=interval)
                continue
            if bounds.last < end:
                start = bounds.last + interval_delta(interval)
                await self.sync.fill_range(symbol, interval, start, end, now)
            self.last[(symbol, interval)] = max(bounds.last, end)

    async def handle_message(self, msg: Any) -> Candle | None:
        """Procesa un mensaje de kline. Devuelve la vela si estaba cerrada y se guardó."""
        k = _as_dict(msg).get("k")
        if not k or not k.get("x"):
            return None  # vela aún abierta
        candle = Candle.from_ws(k)
        key = (candle.symbol, candle.interval)
        step = interval_delta(candle.interval)
        prev = self.last.get(key)
        if prev is not None and candle.open_time > prev + step:
            log.warning(
                "live_gap_detected",
                symbol=candle.symbol,
                interval=candle.interval,
                after=prev.isoformat(),
                before=candle.open_time.isoformat(),
            )
            await self.sync.fill_range(
                candle.symbol, candle.interval, prev + step, candle.open_time - step, self.clock()
            )
        await self.store.upsert([candle], "ws")
        if prev is None or candle.open_time > prev:
            self.last[key] = candle.open_time
        self.received += 1
        log.info(
            "candle_closed",
            symbol=candle.symbol,
            interval=candle.interval,
            open_time=candle.open_time.isoformat(),
            close=str(candle.close),
        )
        if self.on_candle is not None:
            await self.on_candle(candle)
        return candle

    async def _stream_once(self) -> None:
        from binance_common.configuration import ConfigurationWebSocketStreams
        from binance_sdk_spot.spot import Spot

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        client = Spot(
            config_ws_streams=ConfigurationWebSocketStreams(
                stream_url=self.stream_url, reconnect_delay=5000, reconnect_attempts=10
            )
        )
        conn = await client.websocket_streams.create_connection()  # type: ignore[no-untyped-call]
        try:
            for symbol, interval in self.keys:
                stream = await conn.kline(symbol=symbol.lower(), interval=interval)
                stream.on("message", lambda m: loop.call_soon_threadsafe(queue.put_nowait, m))
            log.info("ws_subscribed", streams=[f"{s.lower()}@kline_{i}" for s, i in self.keys])
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=self.silence_timeout_s)
                except TimeoutError:
                    raise ConnectionError("stream sin mensajes; se reconecta") from None
                await self.handle_message(msg)
        finally:
            await conn.close_connection(close_session=True)

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                await self.catch_up()
                stream = asyncio.create_task(self._stream_once())
                stopper = asyncio.create_task(stop.wait())
                done, pending = await asyncio.wait(
                    {stream, stopper}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if stream in done:
                    stream.result()  # propaga la excepción para reconectar
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("ws_error", error=repr(exc), retry_in_s=backoff)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                backoff = min(backoff * 2, 60.0)
