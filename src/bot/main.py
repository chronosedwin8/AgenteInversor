"""Punto de entrada: `python -m bot.main <comando>`.

Comandos:
  run            arranca el bot en el modo resuelto (Fase 1: ingesta de datos en paper)
  migrate        aplica migraciones SQL
  backfill       descarga/sincroniza la historia de velas
  verify         informe de huecos (código de salida 1 si hay huecos sin explicar)
  exchange-info  muestra los filtros de exchangeInfo del universo
  mode           muestra el modo resuelto (sin secretos)
  backtest       walk-forward sobre la historia guardada; informe en reports/
  research       Fase 2b: hipótesis pre-registradas (desarrollo + confirmación)
  audit-h1       auditoría pre-registrada de H1 (docs/auditoria-h1-plan.md)
  analyze-models comparación A/B/C/D y análisis por régimen (docs/fase-testnet-plan.md)
  paper-status   estado del paper trading (capital, posiciones, operaciones, kill switch)
  testnet-check  verifica las claves de Spot Testnet (sin mostrarlas) y el reloj
  weekly-report  informe semanal (semana en curso); --send lo envía por correo
  risk-reset     reinicio MANUAL del kill switch de una cuenta (pide frase de confirmación)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
import time
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from bot import __version__
from bot.backtest.research import load_bars, load_hypotheses, run_research
from bot.backtest.runner import load_symbol_data, run_backtest
from bot.config import AppConfig, EnvSettings, load_config
from bot.data.backfill import HistorySync, VerifyReport, history_window
from bot.data.live import LiveCandles
from bot.data.store import CandleStore
from bot.data.vision import VisionClient
from bot.db import DEFAULT_MIGRATIONS_DIR, connect, migrate
from bot.exchange.binance_spot import BinanceSpot
from bot.exchange.info import ExchangeInfoCache
from bot.execution.paper import PaperTrader
from bot.execution.session import TradingSession
from bot.execution.state import dump_session, load_session
from bot.execution.store import SessionStore
from bot.log import configure_logging, get_logger, register_secrets
from bot.modes import TradingMode, resolve_mode
from bot.strategy.hypotheses import H1Breakout, H1Params

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
                start = await sync.effective_start(symbol, interval, start)
                reports.append(await sync.verify(symbol, interval, start, end))
    for r in reports:
        _print_report(r)
    return 0 if all(r.ok for r in reports) else 1


async def cmd_backtest(cfg: AppConfig, env: EnvSettings) -> int:
    conn = await connect(_dsn(env))
    async with conn:
        await migrate(conn, DEFAULT_MIGRATIONS_DIR)
        started = time.monotonic()
        data = await load_symbol_data(CandleStore(conn), cfg, cfg.settings.universe.active_symbols)
    log.info(
        "backtest_data_loaded",
        snapshots={s: len(d.snapshots) for s, d in data.items()},
        seconds=round(time.monotonic() - started, 1),
    )
    out = await asyncio.to_thread(
        run_backtest, cfg, data, Path("reports"), lambda msg: log.info("backtest", msg=msg)
    )
    wf = out.walk_forward
    m = wf.oos_metrics
    print(f"\nVeredicto Fase 2: {'PASA' if wf.passed else 'NO PASA'}")
    for name, (ok, detail) in wf.acceptance.items():
        print(f"  {'OK ' if ok else 'NO '} {name:<22} {detail}")
    print(f"  resultado fuera de muestra: {m.net_pnl:+.2f} USDT ({m.return_pct:+.2f}%), "
          f"win rate {m.win_rate:.1%}, R medio {m.avg_r:+.3f}")  # fmt: skip
    print(f"  informe: {out.report_path}")
    return 0 if wf.passed else 1


async def cmd_research(cfg: AppConfig, env: EnvSettings, confirm: set[str] | None = None) -> int:
    hyp = load_hypotheses(env.config_dir / "hypotheses.yaml")
    conn = await connect(_dsn(env))
    async with conn:
        await migrate(conn, DEFAULT_MIGRATIONS_DIR)
        bars = await load_bars(CandleStore(conn), cfg.settings.universe.active_symbols,
                               hyp.timeframes)  # fmt: skip
    log.info("research_data_loaded", bars={s: len(b.bars) for s, b in bars.items()})
    out = await asyncio.to_thread(
        run_research, cfg, hyp, bars, Path("reports"), Path("reports") / "fase2b-resultados.md",
        lambda msg: log.info("research", msg=msg), confirm,
    )  # fmt: skip
    for stage in (out.development, out.confirmation):
        if stage is None:
            continue
        for k, r in sorted(stage.results.items()):
            m = r.oos_metrics
            print(f"[{stage.name}] {k}: {'PASA' if r.passed else 'no pasa'}  ops={m.trades} "
                  f"PF={m.profit_factor:.2f} DD={m.max_drawdown_pct:.2f}% "
                  f"ult3m={r.last_months_pnl:+.2f} neto={m.net_pnl:+.2f}")  # fmt: skip
    print(f"ganadora: {out.winner or 'ninguna'}; informe: {out.report_path}")
    return 0 if out.winner else 1


async def cmd_audit_h1(cfg: AppConfig, env: EnvSettings, quick: bool) -> int:
    from bot.audit.h1 import download_extended, load_extended_bars, run_audit
    from bot.audit.report import render
    from bot.data.vision import Month

    hyp = load_hypotheses(env.config_dir / "hypotheses.yaml")
    data = cfg.settings.data
    adapter = _adapter(cfg)
    spreads: dict[str, float] = {}
    for sym in cfg.settings.universe.active_symbols:
        samples = []
        for _ in range(15):
            samples.append(float((await adapter.get_book_ticker(sym)).spread_pct) / 100)
            await asyncio.sleep(1)
        spreads[sym] = sorted(samples)[len(samples) // 2]
    conn = await connect(_dsn(env))
    async with conn, aiohttp.ClientSession() as session:
        await migrate(conn, DEFAULT_MIGRATIONS_DIR)
        store = CandleStore(conn)
        vision = VisionClient(data.vision_base_url, data.cache_dir, session)
        now = _now()
        await download_extended(store, vision, Month(now.year, now.month).prev(),
                                lambda msg: log.info("audit", msg=msg))  # fmt: skip
        bars = await load_bars(store, cfg.settings.universe.active_symbols, hyp.timeframes)
        ext = await load_extended_bars(store)
    out_dir = Path("docs") / "auditoria"
    result = await asyncio.to_thread(
        run_audit, cfg, hyp, bars, ext, spreads, out_dir,
        lambda msg: log.info("audit", msg=msg),
        200 if quick else 5000, 20 if quick else 1000,
    )  # fmt: skip
    path = Path("docs") / ("auditoria-h1-resultados-rapido.md" if quick
                           else "auditoria-h1-resultados.md")  # fmt: skip
    path.write_text(render(result), encoding="utf-8")
    for k, (ok, v) in result.decision.items():
        print(f"  {'OK ' if ok else 'NO '} {k}: {v}")
    verdict = "ventaja demostrada" if result.edge_demonstrated else "ventaja NO demostrada"
    print(f"veredicto: {verdict}; informe: {path}")
    return 0


async def cmd_paper_status(env: EnvSettings, account: str) -> int:
    conn = await connect(_dsn(env))
    async with conn:
        await migrate(conn, DEFAULT_MIGRATIONS_DIR)
        st = SessionStore(conn)
        saved = await st.load(account)
        if saved is None:
            print(f"cuenta {account!r}: sin estado (el paper trading no ha arrancado)")
            return 1
        last, raw = saved
        d = json.loads(raw)
        risk = json.loads(d["risk"])
        trades = await st.trades(account)
        pnl = sum(float(t[6]) for t in trades)
        print(f"cuenta {account}: última vela procesada {last}")
        print(
            f"  caja {d['cash']['$dec']} USDT; posiciones abiertas: {sorted(d['open'])}; "
            f"órdenes pendientes: {sorted(d['pending'])}"
        )
        print(f"  operaciones cerradas: {len(trades)}; resultado {pnl:+.4f} USDT")
        ks = "ACTIVO" if risk.get("kill_switch") else "inactivo"
        print(f"  kill switch: {ks}; pérdidas consecutivas: {risk.get('consecutive_losses')}")
        for t in trades[-10:]:
            print(
                f"   {t[0]} {t[1]:%Y-%m-%d %H:%M} -> {t[2]:%Y-%m-%d %H:%M} "
                f"pnl {float(t[6]):+.4f} R {float(t[7]):+.2f} {t[8]}"
            )
        for at, kind, detail in await st.recent_events(account, 10):
            print(f"   [{at:%Y-%m-%d %H:%M}] {kind} {detail}")
    return 0


async def cmd_risk_reset(env: EnvSettings, account: str, confirmation: str) -> int:
    """Reinicio manual del kill switch (PLAN.md: "reinicio solo manual")."""
    cfg = load_config(env.config_dir)
    hyp = load_hypotheses(env.config_dir / "hypotheses.yaml")
    conn = await connect(_dsn(env))
    async with conn:
        st = SessionStore(conn)
        saved = await st.load(account)
        if saved is None:
            print(f"cuenta {account!r} sin estado")
            return 1
        last, raw = saved
        strategy = H1Breakout(H1Params(**hyp.H1.defaults))  # type: ignore[arg-type]
        session = TradingSession(
            cfg.risk,
            {},
            cfg.strategy.costs.fee_rate,
            cfg.strategy.costs.slippage_rate,
            lambda _t: strategy,
        )
        load_session(session, raw, strategy)
        ev = session.risk.reset_kill_switch(confirmation, _now(), session.pf.equity())
        detail = {"detail": ev.detail, "equity": str(ev.equity)}
        await st.save(
            account, last or _now(), dump_session(session), [], [(ev.at, str(ev.kind), detail)]
        )
    print(f"kill switch de {account!r} reiniciado manualmente")
    return 0


async def cmd_analyze_models(cfg: AppConfig, env: EnvSettings, n_sims: int) -> int:
    from bot.audit.h1 import _data, run_h1
    from bot.audit.models import compare_models, regime_analysis
    from bot.audit.report_models import render

    hyp = load_hypotheses(env.config_dir / "hypotheses.yaml")
    symbols = cfg.settings.universe.active_symbols
    conn = await connect(_dsn(env))
    async with conn:
        store = CandleStore(conn)
        bars = await load_bars(store, symbols, hyp.timeframes)
        daily = {s: await store.load_candles(s, "1d") for s in symbols}
    h1 = H1Params(**hyp.H1.defaults)  # type: ignore[arg-type]

    def progress(msg: str) -> None:
        log.info("models", msg=msg)

    def work() -> str:
        cmp = compare_models(cfg, h1, bars, n_sims, progress)
        initial = cfg.risk.model_copy(update={"kill_switch_basis": "initial"})
        trades = run_h1(cfg.model_copy(update={"risk": initial}), _data(bars), h1).result.trades
        return render(cmp, regime_analysis(trades, daily))

    text = await asyncio.to_thread(work)
    path = Path("docs") / "comparacion-modelos-y-regimen.md"
    path.write_text(text, encoding="utf-8")
    print(f"informe: {path}")
    return 0


async def cmd_testnet_check(cfg: AppConfig, env: EnvSettings) -> int:
    from bot.exchange.trading import BinanceSpotTrading

    key, secret = env.binance_testnet_api_key, env.binance_testnet_api_secret
    if not (key and secret):
        print("faltan BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET en .env")
        return 1
    url = cfg.settings.execution.testnet_url
    client = BinanceSpotTrading(url, key.get_secret_value(), secret.get_secret_value(), env)
    if client.production:
        print("ERROR: el endpoint no es de testnet")
        return 1
    raw_offset = await client.sync_clock()  # corrige la firma de las peticiones
    server = await client.server_time()
    drift_ms = (_now() + timedelta(milliseconds=raw_offset) - server).total_seconds() * 1000
    print(f"desfase del reloj del sistema: {-raw_offset:+.0f} ms (corregido en la firma)")
    balances = await client.balances()
    open_orders = await client.open_orders()
    print(f"endpoint: {url} (testnet)")
    print(f"clave: ...{key.get_secret_value()[-4:]} (solo los 4 últimos caracteres)")
    print(f"desfase residual: {drift_ms:+.0f} ms {'OK' if abs(drift_ms) < 1000 else 'DEMASIADO'}")
    print(f"saldo USDT de testnet: {balances.get('USDT', 0)}; activos con saldo: {len(balances)}")
    print(f"órdenes abiertas: {len(open_orders)}")
    print("Las claves de producción (BINANCE_API_KEY) no se usan para la testnet.")
    return 0 if abs(drift_ms) < 1000 else 1


async def cmd_weekly_report(cfg: AppConfig, env: EnvSettings, send: bool) -> int:
    from bot.monitoring.weekly import (
        MailConfig,
        load_account,
        render_weekly,
        send_mail,
        week_bounds,
    )

    conn = await connect(_dsn(env))
    async with conn:
        store = SessionStore(conn)
        now = _now()
        tz = cfg.settings.tz
        monday, _ = week_bounds(now.astimezone(tz).date())
        accounts = []
        # Solo las cuentas que corren en ESTA máquina (las demás serían copias congeladas).
        here = ["paper"] if env.paper_enabled else []
        if cfg.settings.execution.testnet_enabled and env.binance_testnet_api_key:
            here.append("testnet")
        for name in here:
            acc = await load_account(store, name, cfg.risk.initial_capital_usdt)
            if acc is not None:
                accounts.append(acc)
    subject, body, html = render_weekly(accounts, monday, tz, now)
    print(subject)
    print()
    print(body)
    preview = Path("reports") / "informe-semanal-vista-previa.html"
    try:
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(html, encoding="utf-8")
        print(f"vista previa HTML: {preview}")
    except OSError as exc:
        print(f"(no se pudo guardar la vista previa: {exc})")
    if send:
        mail = MailConfig(env.smtp_host, env.smtp_port, env.smtp_user,
                          env.smtp_password.get_secret_value() if env.smtp_password else "",
                          env.report_email_to)  # fmt: skip
        if not mail.complete:
            print("\nfalta SMTP_USER / SMTP_PASSWORD / REPORT_EMAIL_TO en .env")
            return 1
        await asyncio.to_thread(send_mail, mail, "[PRUEBA] " + subject, body, html)
        print(f"\nenviado a {mail.to}")
    return 0


def cmd_mode(env: EnvSettings) -> int:
    res = resolve_mode(env)
    print(f"solicitado={res.requested!r} resuelto={res.mode.value}")
    for w in res.warnings:
        print(f"  aviso: {w}")
    return 0


# ------------------------------------------------------------------ run


async def _record_interruption(store: SessionStore, accounts: list[str]) -> None:
    """Si el último latido es antiguo, el servicio estuvo caído: se registra la interrupción."""
    last = await store.last_heartbeat("bot")
    now = _now()
    if last is not None and (now - last).total_seconds() > 180:
        minutes = (now - last).total_seconds() / 60
        log.warning("service_interruption", alert=True, since=last.isoformat(),
                    minutes=round(minutes, 1))  # fmt: skip
        detail = {"from": last.isoformat(), "to": now.isoformat(), "minutes": f"{minutes:.1f}"}
        for account in accounts:
            await store.add_event(account, now, "interruption", detail)


async def _heartbeat(
    stop: asyncio.Event,
    every_s: int,
    mode: TradingMode,
    live: LiveCandles,
    paper: Any = None,
    store: SessionStore | None = None,
    adapter: Any = None,
) -> None:
    started = time.monotonic()
    started_at = _now()
    beats = 0
    while not stop.is_set():
        if store is not None:
            try:
                await store.heartbeat("bot", started_at, _now())
            except Exception as exc:
                log.error("heartbeat_write_failed", error=repr(exc))
        if adapter is not None and beats % 10 == 0:  # reloj vs Binance cada ~10 min
            try:
                drift_ms = (_now() - await adapter.server_time()).total_seconds() * 1000
                if abs(drift_ms) > 1000:
                    log.warning("clock_drift", alert=True, drift_ms=round(drift_ms))
            except Exception as exc:
                log.warning("clock_check_failed", error=repr(exc))
        beats += 1
        extra: dict[str, Any] = {}
        if paper is not None:
            s = paper.session
            extra = {
                "paper_equity": str(s.pf.equity()),
                "paper_open": sorted(s.open),
                "paper_kill_switch": s.risk.kill_switch_active,
            }
        log.info(
            "heartbeat",
            mode=mode.value,
            uptime_s=int(time.monotonic() - started),
            candles_received=live.received,
            last={f"{s}:{i}": t.isoformat() for (s, i), t in live.last.items()},
            **extra,
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
        return await cmd_backtest(cfg, env)
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
    paper_conn = await connect(dsn)
    async with meta_conn, data_conn, paper_conn, aiohttp.ClientSession() as session:
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
        await _record_interruption(SessionStore(meta_conn), ["paper", "testnet"])
        # Paper trading de H1-fijo (estrategia experimental, sin capital real, sin IA).
        hyp = load_hypotheses(env.config_dir / "hypotheses.yaml")
        paper_store = CandleStore(paper_conn)
        paper: PaperTrader | None = PaperTrader(
            cfg,
            H1Params(**hyp.H1.defaults),  # type: ignore[arg-type]
            paper_store,
            SessionStore(paper_conn),
            HistorySync(paper_store, adapter, None),
            adapter,
            {s: await info.get(s) for s in symbols},
        )
        tasks: list[Coroutine[Any, Any, None]] = []
        ex_cfg = cfg.settings.execution
        key, secret = env.binance_testnet_api_key, env.binance_testnet_api_secret
        if ex_cfg.testnet_enabled and key and secret:
            from bot.exchange.trading import BinanceSpotTrading
            from bot.execution.live import ExchangeTrader

            testnet_conn = await connect(dsn)
            client = BinanceSpotTrading(ex_cfg.testnet_url, key.get_secret_value(),
                                        secret.get_secret_value(), env)  # fmt: skip
            testnet = ExchangeTrader(
                cfg,
                H1Params(**hyp.H1.defaults),  # type: ignore[arg-type]
                CandleStore(testnet_conn),
                SessionStore(testnet_conn),
                HistorySync(CandleStore(testnet_conn), adapter, None),
                client,
                {s: await info.get(s) for s in symbols},
            )
            tasks.append(testnet.run(stop))
            log.info("testnet_enabled", url=ex_cfg.testnet_url)
        else:
            log.info("testnet_disabled", reason="faltan claves de testnet o está desactivado")
        if not env.paper_enabled:
            paper = None
            log.info("paper_disabled", reason="PAPER_ENABLED=false en esta máquina")
        from bot.monitoring.weekly import MailConfig, weekly_report_loop

        mail = MailConfig(env.smtp_host, env.smtp_port, env.smtp_user,
                          env.smtp_password.get_secret_value() if env.smtp_password else "",
                          env.report_email_to)  # fmt: skip
        accounts = [a for a, on in (("paper", paper is not None),
                                    ("testnet", ex_cfg.testnet_enabled and bool(key)))
                    if on]  # fmt: skip
        tasks.append(weekly_report_loop(stop, SessionStore(meta_conn), accounts,
                                        cfg.risk.initial_capital_usdt, cfg.settings.tz, mail,
                                        Path("reports")))  # fmt: skip
        if paper is not None:
            tasks.append(paper.run(stop))
        tasks += [
            live.run(stop),
            _heartbeat(
                stop,
                cfg.settings.runtime.heartbeat_seconds,
                res.mode,
                live,
                paper,
                SessionStore(meta_conn),
                adapter,
            ),
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
    sub.add_parser("backtest")
    am = sub.add_parser("analyze-models")
    am.add_argument("--sims", type=int, default=1000)
    sub.add_parser("testnet-check")
    wr = sub.add_parser("weekly-report")
    wr.add_argument("--send", action="store_true")
    ps = sub.add_parser("paper-status")
    ps.add_argument("--account", default="paper")
    rr = sub.add_parser("risk-reset")
    rr.add_argument("--account", default="paper")
    rr.add_argument("--confirm", required=True, help="frase exacta de confirmación")
    a = sub.add_parser("audit-h1")
    a.add_argument("--quick", action="store_true", help="pocas repeticiones (prueba de humo)")
    r = sub.add_parser("research")
    r.add_argument("--confirm", nargs="*", default=None,
                   help="hipótesis a confirmar por enmienda registrada (p. ej. H1)")  # fmt: skip
    return p


def _run_async(coro: Coroutine[Any, Any, int]) -> int:
    # psycopg async necesita SelectorEventLoop en Windows; en Linux ya es el bucle por defecto.
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = EnvSettings()
    cfg = load_config(env.config_dir)
    if env.data_intervals.strip():
        intervals = tuple(x.strip() for x in env.data_intervals.split(",") if x.strip())
        data = cfg.settings.data.model_copy(update={"intervals": intervals})
        cfg = cfg.model_copy(update={"settings": cfg.settings.model_copy(update={"data": data})})
    configure_logging(cfg.settings.runtime.log_level, env.log_format)
    register_secrets(*(s.get_secret_value() for s in (
        env.binance_api_key, env.binance_api_secret, env.binance_testnet_api_key,
        env.binance_testnet_api_secret, env.anthropic_api_key, env.telegram_bot_token,
        env.database_url, env.smtp_password,
    ) if s is not None))  # fmt: skip
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
    if command == "backtest":
        return _run_async(cmd_backtest(cfg, env))
    if command == "analyze-models":
        return _run_async(cmd_analyze_models(cfg, env, args.sims))
    if command == "weekly-report":
        return _run_async(cmd_weekly_report(cfg, env, args.send))
    if command == "testnet-check":
        return _run_async(cmd_testnet_check(cfg, env))
    if command == "paper-status":
        return _run_async(cmd_paper_status(env, args.account))
    if command == "risk-reset":
        return _run_async(cmd_risk_reset(env, args.account, args.confirm))
    if command == "audit-h1":
        return _run_async(cmd_audit_h1(cfg, env, args.quick))
    if command == "research":
        confirm = set(args.confirm) if args.confirm else None
        return _run_async(cmd_research(cfg, env, confirm))
    return _run_async(cmd_run(cfg, env))


if __name__ == "__main__":
    sys.exit(main())
