"""Punto de entrada: `python -m bot.main <comando>`.

Comandos:
  run            arranca el bot en el modo resuelto (Fase 1: ingesta de datos en paper)
  migrate        aplica migraciones SQL
  backfill       descarga/sincroniza la historia de velas
  verify         informe de huecos (código de salida 1 si hay huecos sin explicar)
  exchange-info  muestra los filtros de exchangeInfo del universo
  mode           muestra el modo resuelto (sin secretos)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import aiohttp

from bot import __version__
from bot.config import AppConfig, EnvSettings, load_config
from bot.data.backfill import HistorySync, VerifyReport, history_window
from bot.data.live import LiveCandles
from bot.data.store import CandleStore
from bot.data.vision import VisionClient
from bot.db import DEFAULT_MIGRATIONS_DIR, connect, migrate
from bot.exchange.binance_spot import BinanceSpot
from bot.exchange.info import ExchangeInfoCache
from bot.log import configure_logging, get_logger
from bot.modes import TradingMode, resolve_mode

log = get_logger("bot")


def _now() -> datetime:
    return datetime.now(UTC)


def _dsn(env: EnvSettings) -> str:
    if env.database_url is None:
        raise SystemExit("DATABASE_URL no está definido (ver .env.example)")
    return env.database_url.get_secret_value()


def _adapter(cfg: AppConfig) -> BinanceSpot:
    return BinanceSpot(cfg.settings.data.rest_base_url, cfg.settings.data.rest_timeout_ms)


# ------------------------------------------------------------------ comandos


async def cmd_migrate(env: EnvSettings) -> int:
    conn = await connect(_dsn(env))
    async with conn:
        applied = await migrate(conn, DEFAULT_MIGRATIONS_DIR)
    log.info("migrations_done", applied=applied)
    return 0


async def cmd_exchange_info(cfg: AppConfig) -> int:
    symbols = cfg.settings.universe.active_symbols
    filters = await _adapter(cfg).get_symbol_filters(symbols)
    print(f"{'símbolo':<10} {'estado':<9} {'tickSize':>10} {'stepSize':>10} {'minQty':>10} "
          f"{'minNotional':>12} LIMIT_MAKER")  # fmt: skip
    for s in symbols:
        f = filters[s]
        print(
            f"{s:<10} {f.status:<9} {f.price.tick_size:>10} {f.lot.step_size:>10} "
            f"{f.lot.min_qty:>10} {f.notional.min_notional:>12} {f.supports('LIMIT_MAKER')}"
        )
    return 0


def _print_report(r: VerifyReport) -> None:
    status = "OK" if r.ok else "HUECOS"
    print(
        f"{r.symbol:<9} {r.interval:<4} {r.start:%Y-%m-%d %H:%M} → {r.end:%Y-%m-%d %H:%M} "
        f"esperadas={r.expected:>6} presentes={r.count:>6} huecos={len(r.gaps):>3} "
        f"huecos_exchange={len(r.exchange_gaps):>2}  {status}"
    )
    for g in r.gaps[:10]:
        print(f"    hueco: {g.start:%Y-%m-%d %H:%M} → {g.end:%Y-%m-%d %H:%M} ({g.missing} velas)")
    for g in r.exchange_gaps:
        print(f"    exchange sin datos: {g.start:%Y-%m-%d %H:%M} → {g.end:%Y-%m-%d %H:%M}")


async def _sync_all(cfg: AppConfig, env: EnvSettings, months: int | None) -> list[VerifyReport]:
    data = cfg.settings.data
    conn = await connect(_dsn(env))
    async with conn, aiohttp.ClientSession() as session:
        await migrate(conn, DEFAULT_MIGRATIONS_DIR)
        sync = HistorySync(
            CandleStore(conn),
            _adapter(cfg),
            VisionClient(data.vision_base_url, data.cache_dir, session),
        )
        reports = []
        now = _now()
        for symbol in cfg.settings.universe.active_symbols:
            for interval in data.intervals:
                r = await sync.sync(symbol, interval, months or data.history_months, now)
                log.info(
                    "history_synced",
                    symbol=symbol,
                    interval=interval,
                    count=r.count,
                    expected=r.expected,
                    gaps=len(r.gaps),
                    exchange_gaps=len(r.exchange_gaps),
                )
                reports.append(r)
        return reports


async def cmd_backfill(cfg: AppConfig, env: EnvSettings, months: int | None) -> int:
    reports = await _sync_all(cfg, env, months)
    for r in reports:
        _print_report(r)
    return 0 if all(r.ok for r in reports) else 1


async def cmd_verify(cfg: AppConfig, env: EnvSettings) -> int:
    data = cfg.settings.data
    conn = await connect(_dsn(env))
    async with conn:
        sync = HistorySync(CandleStore(conn), _adapter(cfg), None)
        now = _now()
        reports = []
        for symbol in cfg.settings.universe.active_symbols:
            for interval in data.intervals:
                start, end = history_window(now, interval, data.history_months)
                reports.append(await sync.verify(symbol, interval, start, end))
    for r in reports:
        _print_report(r)
    return 0 if all(r.ok for r in reports) else 1


def cmd_mode(env: EnvSettings) -> int:
    res = resolve_mode(env)
    print(f"solicitado={res.requested!r} resuelto={res.mode.value}")
    for w in res.warnings:
        print(f"  aviso: {w}")
    return 0


# ------------------------------------------------------------------ run


async def _heartbeat(
    stop: asyncio.Event, every_s: int, mode: TradingMode, live: LiveCandles
) -> None:
    started = time.monotonic()
    while not stop.is_set():
        log.info(
            "heartbeat",
            mode=mode.value,
            uptime_s=int(time.monotonic() - started),
            candles_received=live.received,
            last={f"{s}:{i}": t.isoformat() for (s, i), t in live.last.items()},
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every_s)


async def _refresh_exchange_info(
    stop: asyncio.Event, cache: ExchangeInfoCache, every_s: int
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
        except TimeoutError:
            try:
                await cache.refresh()
            except Exception as exc:
                log.error("exchange_info_refresh_failed", error=repr(exc))


async def cmd_run(cfg: AppConfig, env: EnvSettings) -> int:
    res = resolve_mode(env)
    for w in res.warnings:
        # Fase 6 enviará esta alerta por Telegram.
        log.warning("mode_degraded", alert=True, reason=w)
    log.info("bot_starting", version=__version__, mode=res.mode.value, requested=res.requested)

    if res.mode is TradingMode.BACKTEST:
        log.error("backtest_not_available", detail="el backtester llega en la Fase 2")
        return 2
    if res.mode is TradingMode.LIVE:
        log.error("live_not_available", detail="no hay ejecución de órdenes hasta la Fase 4/8")
        return 2

    data = cfg.settings.data
    symbols = cfg.settings.universe.active_symbols
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
            loop.add_signal_handler(sig, stop.set)

    adapter = _adapter(cfg)
    dsn = _dsn(env)
    meta_conn = await connect(dsn)
    data_conn = await connect(dsn)
    async with meta_conn, data_conn, aiohttp.ClientSession() as session:
        await migrate(meta_conn, DEFAULT_MIGRATIONS_DIR)

        info = ExchangeInfoCache(
            adapter,
            symbols,
            ttl_seconds=data.exchange_info_refresh_minutes * 60,
            on_refresh=CandleStore(meta_conn).save_symbol_snapshots,
        )
        for f in (await info.refresh()).values():
            log.info(
                "symbol_filters",
                symbol=f.symbol,
                status=f.status,
                tick_size=str(f.price.tick_size),
                step_size=str(f.lot.step_size),
                min_qty=str(f.lot.min_qty),
                min_notional=str(f.notional.min_notional),
                limit_maker=f.supports("LIMIT_MAKER"),
            )

        store = CandleStore(data_conn)
        sync = HistorySync(
            store, adapter, VisionClient(data.vision_base_url, data.cache_dir, session)
        )
        now = _now()
        for symbol in symbols:
            for interval in data.intervals:
                r = await sync.sync(symbol, interval, data.history_months, now)
                log.info(
                    "history_synced",
                    symbol=symbol,
                    interval=interval,
                    count=r.count,
                    expected=r.expected,
                    gaps=len(r.gaps),
                    exchange_gaps=len(r.exchange_gaps),
                )

        live = LiveCandles(store, sync, symbols, data.intervals, data.ws_stream_url)
        tasks: list[Coroutine[Any, Any, None]] = [
            live.run(stop),
            _heartbeat(stop, cfg.settings.runtime.heartbeat_seconds, res.mode, live),
            _refresh_exchange_info(stop, info, data.exchange_info_refresh_minutes * 60),
        ]
        await asyncio.gather(*tasks)
    log.info("bot_stopped")
    return 0


# ------------------------------------------------------------------ CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bot", description="Bot de trading intradía (Binance Spot)")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run")
    sub.add_parser("migrate")
    b = sub.add_parser("backfill")
    b.add_argument("--months", type=int, default=None, help="meses completos (por defecto: config)")
    sub.add_parser("verify")
    sub.add_parser("exchange-info")
    sub.add_parser("mode")
    return p


def _run_async(coro: Coroutine[Any, Any, int]) -> int:
    # psycopg async necesita SelectorEventLoop en Windows; en Linux ya es el bucle por defecto.
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = EnvSettings()
    cfg = load_config(env.config_dir)
    configure_logging(cfg.settings.runtime.log_level, env.log_format)
    command = args.command or "run"
    if command == "mode":
        return cmd_mode(env)
    if command == "migrate":
        return _run_async(cmd_migrate(env))
    if command == "exchange-info":
        return _run_async(cmd_exchange_info(cfg))
    if command == "backfill":
        return _run_async(cmd_backfill(cfg, env, args.months))
    if command == "verify":
        return _run_async(cmd_verify(cfg, env))
    return _run_async(cmd_run(cfg, env))


if __name__ == "__main__":
    sys.exit(main())
