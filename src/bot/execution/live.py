"""Ejecución con órdenes reales (Fase 4: Spot TESTNET) de H1-fijo.

- Señales con datos de mercado reales (mismas velas y features que backtest y paper).
- Mismas reglas (H1Breakout) y mismo RiskManager; contabilidad sobre un capital virtual de
  200 USDT (la cuenta de testnet tiene saldos ficticios mucho mayores).
- Órdenes por el OrderManager: compra a mercado, stop en el exchange, trailing con
  cancelar/recolocar, cierres a mercado, IDs idempotentes y reconciliación periódica.
- Si la reconciliación encuentra órdenes desconocidas, se pausan las entradas y se alerta.
- Estado persistente: un reinicio retoma posiciones y stops sin duplicar órdenes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bot.config import AppConfig
from bot.data.backfill import HistorySync, last_closed_open_time
from bot.data.klines import interval_delta
from bot.data.store import CandleStore
from bot.exchange.filters import SymbolFilters
from bot.exchange.trading import OrderInfo, TradingClient
from bot.execution.orders import OrderManager, PositionOrders, StopPlacementError
from bot.execution.state import _dec, _enc
from bot.execution.store import SessionStore
from bot.log import get_logger
from bot.portfolio.portfolio import Holding, Portfolio
from bot.risk.manager import EntryIntent, RiskEventKind, RiskManager, RiskState
from bot.strategy.features import Bar, build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params
from bot.strategy.rules import ExitReason, PositionPlan

log = get_logger(__name__)
SIGNAL_TF, REGIME_TF = "4h", "1d"
WINDOW = 50


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _order_from(d: dict[str, Any]) -> OrderInfo:
    return OrderInfo(**d)


class ExchangeTrader:
    def __init__(
        self,
        app: AppConfig,
        h1_params: H1Params,
        candles: CandleStore,
        sessions: SessionStore,
        sync: HistorySync,
        client: TradingClient,
        filters: dict[str, SymbolFilters],
        account: str = "testnet",
        clock: Callable[[], datetime] = _utcnow,
        reconcile_every_s: float = 300.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self.app = app
        self.symbols = tuple(sorted(filters))
        self.filters = filters
        self.strategy = H1Breakout(h1_params)
        self.candles, self.sessions, self.sync = candles, sessions, sync
        self.client = client
        self.account = account
        self.clock = clock
        self.reconcile_every_s = reconcile_every_s
        self.fee = app.strategy.costs.fee_rate
        self.pf = Portfolio(app.risk.initial_capital_usdt, self.fee)
        self.risk = RiskManager(app.risk, tz=app.settings.tz)
        self.om = OrderManager(client, self.risk, filters, self.fee, sleep=sleep)
        self.positions: dict[str, tuple[PositionOrders, PositionPlan]] = {}
        self.paused_reason: str | None = None
        self.last_bar_time: datetime | None = None
        self._events: list[tuple[datetime, str, dict[str, Any]]] = []
        self._persisted_trades = 0

    def _event(self, kind: str, at: datetime | None = None, **data: Any) -> None:
        at = at or self.clock()
        self._events.append((at, kind, {k: str(v) for k, v in data.items()}))
        log.info(f"{self.account}_{kind}", **{k: str(v) for k, v in data.items()})

    # ------------------------------------------------------------ estado

    def _dump(self) -> str:
        return json.dumps(_enc({
            "cash": self.pf.cash,
            "holdings": {k: asdict(h) for k, h in self.pf.holdings.items()},
            "marks": dict(self.pf.marks),
            "positions": {k: {"orders": asdict(o), "plan": asdict(p)}
                          for k, (o, p) in self.positions.items()},
            "risk": self.risk.state.to_json(),
            "paused_reason": self.paused_reason,
        }))  # fmt: skip

    def _load(self, raw: str) -> None:
        d = _dec(json.loads(raw))
        self.pf.cash = d["cash"]
        self.pf.holdings = {k: Holding(**h) for k, h in d["holdings"].items()}
        self.pf.marks = dict(d["marks"])
        self.positions = {}
        for k, v in d["positions"].items():
            o = v["orders"]
            orders = PositionOrders(o["symbol"], o["signal_time"], o["qty"],
                                    _order_from(o["entry"]), _order_from(o["stop"]),
                                    o["stop_version"])  # fmt: skip
            plan = dict(v["plan"])
            plan["stop_kind"] = ExitReason(plan["stop_kind"])
            self.positions[k] = (orders, PositionPlan(**plan))
        self.risk.state = RiskState.from_json(d["risk"])
        self.paused_reason = d["paused_reason"]

    async def _persist(self) -> None:
        if self.last_bar_time is None:
            raise RuntimeError("start() no se ejecutó")
        new = self.pf.trades[self._persisted_trades :]
        await self.sessions.save(self.account, self.last_bar_time, self._dump(), new,
                                 self._events)  # fmt: skip
        self._persisted_trades = len(self.pf.trades)
        self._events = []

    async def start(self) -> None:
        saved = await self.sessions.load(self.account)
        if saved is not None:
            self.last_bar_time, raw = saved
            self._load(raw)
            log.info(f"{self.account}_restored", positions=sorted(self.positions),
                     kill_switch=self.risk.kill_switch_active)  # fmt: skip
        else:
            now = self.clock()
            self.last_bar_time = (last_closed_open_time(now, SIGNAL_TF) + interval_delta(SIGNAL_TF)
                                  - timedelta(milliseconds=1))  # fmt: skip
        await self.reconcile()
        await self._persist()

    # ------------------------------------------------------------ cierres y contabilidad

    def _record_exit(self, sym: str, fill: OrderInfo, reason: str, at: datetime) -> None:
        self.positions.pop(sym)
        price = fill.avg_price if fill.avg_price > 0 else fill.stop_price
        trade = self.pf.sell(sym, self.pf.holdings[sym].qty, price, at, reason)
        if trade is not None:
            self.risk.on_trade_closed(trade.pnl, at, self.pf.equity())
            self._event("trade_closed", at, symbol=sym, pnl=trade.pnl, reason=reason,
                        price=price, order=fill.client_order_id)  # fmt: skip

    async def _close_all(self, at: datetime, reason: str) -> None:
        for sym in list(self.positions):
            orders, _ = self.positions[sym]
            fill = await self.om.close_position(orders, reason)
            self._record_exit(sym, fill, reason, at)

    # ------------------------------------------------------------ velas

    async def _bars(self) -> dict[str, list[Bar]]:
        now = self.clock()
        out = {}
        for sym in self.symbols:
            for tf in (SIGNAL_TF, REGIME_TF):
                b = await self.candles.bounds(sym, tf)
                end = last_closed_open_time(now, tf)
                if b.last is not None and b.last < end:
                    await self.sync.fill_range(sym, tf, b.last + interval_delta(tf), end, now)
            out[sym] = build_bars(sym, await self.candles.load_candles(sym, SIGNAL_TF),
                                  await self.candles.load_candles(sym, REGIME_TF))  # fmt: skip
        return out

    async def step(self) -> int:
        bars = await self._bars()
        index = {s: {b.close_time: i for i, b in enumerate(bs)} for s, bs in bars.items()}
        last = self.last_bar_time
        times = sorted({b.close_time for bs in bars.values() for b in bs
                        if last is None or b.close_time > last})  # fmt: skip
        for t in times:
            for sym in self.symbols:
                i = index[sym].get(t)
                if i is not None:
                    await self._on_bar(sym, bars[sym][max(0, i - WINDOW) : i + 1], t)
            events = self.risk.update(t, self.pf.equity())
            for e in events:
                self._event("risk_event", t, event=e.kind, equity=e.equity, detail=e.detail)
            if any(e.kind is RiskEventKind.KILL_SWITCH for e in events):
                await self._close_all(t, "kill_switch")
            self.last_bar_time = t
            await self._persist()
        return len(times)

    async def _on_bar(self, sym: str, window: list[Bar], t: datetime) -> None:
        bar = window[-1]
        f = self.filters[sym]
        if sym in self.positions:
            orders, plan = self.positions[sym]
            st = await self.client.get_order(sym, orders.stop.client_order_id)
            if st is not None and st.status == "FILLED":
                self._record_exit(sym, st, str(plan.stop_kind), t)
                return
            reason = self.strategy.on_candle_close(plan, bar)
            if reason is not None:
                fill = await self.om.close_position(orders, str(reason))
                self._record_exit(sym, fill, str(reason), t)
                return
            new_stop = f.round_price(Decimal(repr(plan.stop)), "down")
            if new_stop > orders.stop.stop_price:
                filled = await self.om.move_stop(orders, new_stop)
                if filled is not None:
                    self._record_exit(sym, filled, str(plan.stop_kind), t)
                    return
            self.pf.mark(sym, Decimal(repr(bar.close)))
            return

        if self.paused_reason is not None:
            return
        sig, _ = self.strategy.evaluate_entry(window[-(self.strategy.lookback + 1) :])
        if sig is None:
            return
        intent = EntryIntent(sym, f.round_price(Decimal(repr(sig.limit_price)), "down"),
                             f.round_price(Decimal(repr(sig.stop)), "down"))  # fmt: skip
        decision = self.risk.approve_entry(intent, self.pf, f, t)
        if not decision.approved:
            self._event("signal_rejected", t, symbol=sym, reason=decision.reason)
            return
        try:
            orders = await self.om.open_position(decision, intent, t)
        except StopPlacementError as exc:
            self._event("stop_failed_closed", t, symbol=sym, error=exc)
            return
        entry = orders.entry
        risk_amount = orders.qty * ((entry.avg_price - intent.stop)
                                    + self.fee * (entry.avg_price + intent.stop))  # fmt: skip
        self.pf.buy(sym, orders.qty, entry.avg_price, t, max(risk_amount, Decimal(0)))
        plan = self.strategy.open_plan(sig, float(entry.avg_price), float(intent.stop),
                                       float(self.fee), t)  # fmt: skip
        self.positions[sym] = (orders, plan)
        self._event("position_opened", t, symbol=sym, qty=orders.qty, price=entry.avg_price,
                    stop=intent.stop, entry_id=entry.client_order_id,
                    stop_id=orders.stop.client_order_id)  # fmt: skip

    # ------------------------------------------------------------ reconciliación

    async def check_clock(self, max_drift_ms: float = 1000.0) -> float:
        """Desfase reloj local vs servidor. Si supera el máximo, se pausan las entradas."""
        server = await self.client.server_time()
        drift = (self.clock() - server).total_seconds() * 1000
        if abs(drift) > max_drift_ms:
            self._pause(f"reloj desincronizado {drift:.0f} ms")
        elif self.paused_reason and self.paused_reason.startswith("reloj desincronizado"):
            self.paused_reason = None
            self._event("resumed", reason="reloj sincronizado")
        return drift

    async def reconcile(self) -> None:
        await self.check_clock()
        if not self.positions and self.paused_reason is None:
            unknown = [o for o in await self.client.open_orders()
                       if o.client_order_id.startswith(f"{self.om.prefix}-")]  # fmt: skip
            if unknown:
                self._pause(f"órdenes desconocidas: {[o.client_order_id for o in unknown]}")
            return
        report = await self.om.reconcile({s: o for s, (o, _) in self.positions.items()})
        now = self.clock()
        for sym, fill in report.stops_filled.items():
            self._record_exit(sym, fill, "stop_exchange", now)
        if report.stops_replaced:
            self._event("stops_replaced", now, symbols=report.stops_replaced)
        if report.unknown_orders:
            self._pause(
                f"órdenes desconocidas: {[o.client_order_id for o in report.unknown_orders]}"
            )

    def _pause(self, reason: str) -> None:
        self.paused_reason = reason
        log.error(f"{self.account}_paused", alert=True, reason=reason)
        self._event("paused", reason=reason)

    async def run(self, stop: asyncio.Event) -> None:
        await self.start()
        while not stop.is_set():
            try:
                await self.step()
                await self.reconcile()
                await self._persist()
            except Exception as exc:
                log.error(f"{self.account}_error", alert=True, error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.reconcile_every_s)
