"""Paper trading de H1-fijo: precios reales de Binance, órdenes simuladas, sin capital real.

Determinista y robusto ante reinicios:
- en cada paso sincroniza por REST las velas de 4 h y diarias y procesa, en orden, todas las
  velas de 4 h cerradas posteriores a la última procesada (guardada en PostgreSQL);
- las features se reconstruyen con `build_bars` sobre toda la historia, igual que el backtest;
- la lógica de cada vela es la `TradingSession` del backtest (regla 3) con el RiskManager
  completo; el estado (incluido el kill switch) se guarda tras cada vela.
La capa de IA está aplazada: no interviene.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bot.config import AppConfig
from bot.data.backfill import HistorySync, last_closed_open_time
from bot.data.klines import interval_delta
from bot.data.store import CandleStore
from bot.exchange.base import ExchangeAdapter
from bot.exchange.filters import SymbolFilters
from bot.execution.session import TradingSession
from bot.execution.state import dump_session, load_session
from bot.execution.store import SessionStore
from bot.log import get_logger
from bot.strategy.features import Bar, build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params

log = get_logger(__name__)
SIGNAL_TF, REGIME_TF = "4h", "1d"
WINDOW = 50


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PaperTrader:
    def __init__(
        self,
        app: AppConfig,
        h1_params: H1Params,
        candles: CandleStore,
        sessions: SessionStore,
        sync: HistorySync,
        adapter: ExchangeAdapter,
        filters: dict[str, SymbolFilters],
        account: str = "paper",
        clock: Callable[[], datetime] = _utcnow,
        live_quotes: bool = True,
    ):
        self.app = app
        self.symbols = tuple(sorted(filters))
        self.strategy = H1Breakout(h1_params)
        self.candles = candles
        self.sessions = sessions
        self.sync = sync
        self.adapter = adapter
        self.account = account
        self.clock = clock
        self.live_quotes = live_quotes
        self._events: list[tuple[datetime, str, dict[str, Any]]] = []
        self.session = TradingSession(
            app.risk, filters, app.strategy.costs.fee_rate, app.strategy.costs.slippage_rate,
            lambda _t: self.strategy, tz=app.settings.tz, listener=self._on_event,
        )  # fmt: skip
        self.last_bar_time: datetime | None = None
        self._persisted_trades = 0

    def _on_event(self, kind: str, data: dict[str, Any]) -> None:
        at = data.pop("at", self.clock())
        self._events.append((at, kind, {k: str(v) for k, v in data.items()}))
        log.info("paper_" + kind, account=self.account, **{k: str(v) for k, v in data.items()})

    # ------------------------------------------------------------ arranque

    async def start(self) -> None:
        if not await self.sessions.acquire_lock(self.account):
            raise RuntimeError(f"otra instancia ya opera la cuenta {self.account!r}")
        saved = await self.sessions.load(self.account)
        if saved is not None:
            self.last_bar_time, raw = saved
            load_session(self.session, raw, self.strategy)
            log.info("paper_restored", account=self.account, last_bar=str(self.last_bar_time),
                     equity=str(self.session.pf.equity()),
                     kill_switch=self.session.risk.kill_switch_active)  # fmt: skip
        else:
            # Sin estado: se empieza a operar desde la próxima vela (sin "operar" el pasado).
            now = self.clock()
            self.last_bar_time = last_closed_open_time(now, SIGNAL_TF) + interval_delta(
                SIGNAL_TF) - timedelta(milliseconds=1)  # fmt: skip
            await self._persist()
            log.info("paper_started", account=self.account, from_bar=str(self.last_bar_time),
                     capital=str(self.session.pf.equity()))  # fmt: skip

    # ------------------------------------------------------------ paso

    async def _refresh_data(self) -> None:
        now = self.clock()
        for sym in self.symbols:
            for tf in (SIGNAL_TF, REGIME_TF):
                b = await self.candles.bounds(sym, tf)
                end = last_closed_open_time(now, tf)
                if b.last is not None and b.last < end:
                    await self.sync.fill_range(sym, tf, b.last + interval_delta(tf), end, now)

    async def _bars(self) -> dict[str, list[Bar]]:
        out = {}
        for sym in self.symbols:
            sig = await self.candles.load_candles(sym, SIGNAL_TF)
            reg = await self.candles.load_candles(sym, REGIME_TF)
            out[sym] = build_bars(sym, sig, reg)
        return out

    async def step(self) -> int:
        """Procesa todas las velas de 4 h cerradas nuevas. Devuelve cuántos instantes procesó."""
        await self._refresh_data()
        bars = await self._bars()
        index = {s: {b.close_time: i for i, b in enumerate(bs)} for s, bs in bars.items()}
        last = self.last_bar_time
        times = sorted({b.close_time for bs in bars.values() for b in bs
                        if last is None or b.close_time > last})  # fmt: skip
        newest = times[-1] if times else None
        for t in times:
            changed = False
            for sym in self.symbols:
                i = index[sym].get(t)
                if i is None:
                    continue
                window = bars[sym][max(0, i - WINDOW) : i + 1]
                changed |= self.session.on_bar(sym, window, t, entries_allowed=True)
            self.session.end_timestep(t, changed)
            # Evidencia prospectiva: cuándo se procesó cada vela respecto a su cierre.
            lag = (self.clock() - t).total_seconds()
            self._events.append((t, "bar_processed", {
                "processed_at": self.clock().isoformat(), "lag_s": f"{lag:.0f}",
                "catch_up": str(lag > interval_delta(SIGNAL_TF).total_seconds()),
                "equity": str(self.session.pf.equity()),
            }))  # fmt: skip
            self.last_bar_time = t
            if t == newest and self.live_quotes:
                await self._attach_quotes(t)
            await self._persist()
        return len(times)

    async def _attach_quotes(self, t: datetime) -> None:
        """Para validar costos: precio real del libro en el momento de cada orden simulada."""
        for i, (at, kind, detail) in enumerate(self._events):
            if kind == "order_placed" and at == t:
                with contextlib.suppress(Exception):
                    bt = await self.adapter.get_book_ticker(detail["symbol"])
                    detail.update(ask_real=str(bt.ask), bid_real=str(bt.bid))
                    self._events[i] = (at, kind, detail)

    async def _persist(self) -> None:
        if self.last_bar_time is None:
            raise RuntimeError("start() no se ejecutó")
        new_trades = self.session.pf.trades[self._persisted_trades :]
        await self.sessions.save(self.account, self.last_bar_time, dump_session(self.session),
                                 new_trades, self._events)  # fmt: skip
        self._persisted_trades = len(self.session.pf.trades)
        self._events = []

    # ------------------------------------------------------------ bucle

    async def run(self, stop: asyncio.Event) -> None:
        await self.start()
        while not stop.is_set():
            try:
                n = await self.step()
                if n:
                    log.info("paper_step", processed=n, equity=str(self.equity()),
                             open=sorted(self.session.open), pending=sorted(self.session.pending),
                             kill_switch=self.session.risk.kill_switch_active)  # fmt: skip
            except Exception as exc:
                log.error("paper_step_failed", error=repr(exc))
            # Siguiente cierre de 4 h + 30 s de margen para que Binance publique la vela.
            now = self.clock()
            nxt = last_closed_open_time(now, SIGNAL_TF) + 2 * interval_delta(SIGNAL_TF)
            wait = max((nxt - now).total_seconds() + 30, 30)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=min(wait, 900))

    def equity(self) -> Decimal:
        return self.session.pf.equity()
