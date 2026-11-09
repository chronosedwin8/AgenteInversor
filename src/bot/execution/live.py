"""Ejecución con órdenes reales (Fase 4: Spot TESTNET) de H1-fijo.

- Señales con datos de mercado reales (mismas velas y features que backtest y paper).
- Mismas reglas (H1Breakout) y mismo RiskManager; contabilidad sobre un capital virtual de
  200 USDT (la cuenta de testnet tiene saldos ficticios mucho mayores).
- Órdenes por el OrderManager: compra a mercado, stop-market en el exchange, trailing con
  cancelar/recolocar, cierres verificados, IDs idempotentes y reconciliación periódica.

Garantías ante fallos (docs/fase-testnet-plan.md §4):
- un solo proceso por cuenta (bloqueo en PostgreSQL);
- si el exchange no responde o devuelve datos incompletos: sin entradas nuevas hasta una
  reconciliación correcta;
- un cierre no confirmado deja la posición en EMERGENCIA (persistente): se reintenta en cada
  ciclo y no se abre nada mientras haya emergencias;
- tras un reinicio se reconstruyen desde el exchange las posiciones cuya entrada se ejecutó
  pero no llegó a guardarse; sin stop → cierre de emergencia;
- kill switch: cierra posiciones, cancela TODAS las órdenes propias y verifica que no quede
  ninguna;
- una señal procesada tarde (más de una vela después de su cierre) no abre posiciones: el precio
  ya no es el de la señal.
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
from bot.exchange.trading import (
    ExchangeRejectedError,
    OrderInfo,
    TradingClient,
    TransientExchangeError,
)
from bot.execution.orders import (
    EmergencyCloseError,
    ExchangeDataError,
    OrderManager,
    PositionOrders,
    StopPlacementError,
)
from bot.execution.state import _dec, _enc
from bot.execution.store import SessionStore
from bot.log import event_fields, get_logger
from bot.portfolio.portfolio import Holding, Portfolio
from bot.risk.manager import EntryIntent, RiskEventKind, RiskManager, RiskState
from bot.strategy.features import Bar, build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params
from bot.strategy.rules import EntrySignal, ExitReason, PositionPlan

log = get_logger(__name__)
SIGNAL_TF, REGIME_TF = "4h", "1d"
WINDOW = 50
EXCHANGE_ERRORS = (TransientExchangeError, ExchangeRejectedError, ExchangeDataError)


class AnotherInstanceError(RuntimeError):
    """Otra instancia ya opera esta cuenta."""


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
        order_prefix: str = "h1",
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
        self.om = OrderManager(
            client, self.risk, filters, self.fee, prefix=order_prefix, sleep=sleep
        )
        self.positions: dict[str, tuple[PositionOrders, PositionPlan]] = {}
        self.emergencies: dict[str, str] = {}  # símbolo → motivo (cierre no confirmado)
        self.paused_reason: str | None = None
        self.exchange_ok = False
        self.last_bar_time: datetime | None = None
        self._events: list[tuple[datetime, str, dict[str, Any]]] = []
        self._persisted_trades = 0

    def _event(self, kind: str, at: datetime | None = None, **data: Any) -> None:
        at = at or self.clock()
        detail = {k: str(v) for k, v in data.items()}
        self._events.append((at, kind, detail))
        log.info(f"{self.account}_{kind}", **event_fields(detail))

    @property
    def can_open(self) -> bool:
        return (
            self.exchange_ok
            and self.paused_reason is None
            and not self.emergencies
            and not self.risk.kill_switch_active
        )

    # ------------------------------------------------------------ estado

    def _dump(self) -> str:
        return json.dumps(
            _enc(
                {
                    "cash": self.pf.cash,
                    "holdings": {k: asdict(h) for k, h in self.pf.holdings.items()},
                    "marks": dict(self.pf.marks),
                    "positions": {
                        k: {"orders": asdict(o), "plan": asdict(p) if p is not None else None}
                        for k, (o, p) in self.positions.items()
                    },
                    "risk": self.risk.state.to_json(),
                    "paused_reason": self.paused_reason,
                    "emergencies": self.emergencies,
                }
            )
        )

    def _load(self, raw: str) -> None:
        d = _dec(json.loads(raw))
        self.pf.cash = d["cash"]
        self.pf.holdings = {k: Holding(**h) for k, h in d["holdings"].items()}
        self.pf.marks = dict(d["marks"])
        self.positions = {}
        for k, v in d["positions"].items():
            o = v["orders"]
            orders = PositionOrders(
                o["symbol"],
                o["signal_time"],
                o["qty"],
                _order_from(o["entry"]),
                _order_from(o["stop"]),
                o["stop_version"],
            )
            if v["plan"] is None:  # posición en emergencia sin plan de salida
                self.positions[k] = (orders, None)  # type: ignore[assignment]
                continue
            plan = dict(v["plan"])
            plan["stop_kind"] = ExitReason(plan["stop_kind"])
            self.positions[k] = (orders, PositionPlan(**plan))
        self.risk.state = RiskState.from_json(d["risk"])
        self.paused_reason = d["paused_reason"]
        self.emergencies = dict(d.get("emergencies") or {})

    async def _persist(self) -> None:
        if self.last_bar_time is None:
            raise RuntimeError("start() no se ejecutó")
        new = self.pf.trades[self._persisted_trades :]
        await self.sessions.save(self.account, self.last_bar_time, self._dump(), new, self._events)
        self._persisted_trades = len(self.pf.trades)
        self._events = []

    async def start(self) -> None:
        if not await self.sessions.acquire_lock(self.account):
            raise AnotherInstanceError(f"otra instancia ya opera la cuenta {self.account!r}")
        saved = await self.sessions.load(self.account)
        if saved is not None:
            self.last_bar_time, raw = saved
            self._load(raw)
            log.info(
                f"{self.account}_restored",
                positions=sorted(self.positions),
                emergencies=self.emergencies,
                kill_switch=self.risk.kill_switch_active,
            )
        else:
            now = self.clock()
            self.last_bar_time = (
                last_closed_open_time(now, SIGNAL_TF)
                + interval_delta(SIGNAL_TF)
                - timedelta(milliseconds=1)
            )
        await self.reconcile()
        await self._persist()

    # ------------------------------------------------------------ cierres y contabilidad

    def _record_exit(self, sym: str, fill: OrderInfo, reason: str, at: datetime) -> None:
        self.positions.pop(sym, None)
        self.emergencies.pop(sym, None)
        price = fill.avg_price if fill.avg_price > 0 else fill.stop_price
        trade = self.pf.sell(sym, self.pf.holdings[sym].qty, price, at, reason)
        if trade is not None:
            self.risk.on_trade_closed(trade.pnl, at, self.pf.equity())
            self._event(
                "trade_closed",
                at,
                symbol=sym,
                pnl=trade.pnl,
                reason=reason,
                price=price,
                order=fill.client_order_id,
            )

    async def _close(self, sym: str, reason: str, at: datetime) -> None:
        """Cierre verificado; si no se confirma, la posición pasa a emergencia."""
        orders, _ = self.positions[sym]
        try:
            if orders.stop.type == "STOP_LOSS":
                fill = await self.om.close_position(orders, reason)
            else:  # sin stop real: venta de emergencia verificada (ID idempotente único)
                fill = await self.om.emergency_sell(sym, orders.qty, orders.signal_time)
        except (EmergencyCloseError, *EXCHANGE_ERRORS) as exc:
            if sym not in self.emergencies:
                log.error(f"{self.account}_emergency", alert=True, symbol=sym, error=str(exc))
                self._event("emergency", at, symbol=sym, reason=reason, error=exc)
            self.emergencies[sym] = reason
            return
        self._record_exit(sym, fill, reason, at)

    async def _retry_emergencies(self) -> None:
        for sym, reason in list(self.emergencies.items()):
            if sym in self.positions:
                await self._close(sym, reason, self.clock())
            else:
                self.emergencies.pop(sym)

    async def _kill(self, at: datetime) -> None:
        """Kill switch: cerrar todo, cancelar TODAS las órdenes propias y verificar cero."""
        for sym in list(self.positions):
            await self._close(sym, "kill_switch", at)
        remaining = await self.om.cancel_all()
        self._event(
            "kill_switch_cleanup",
            at,
            open_positions=sorted(self.positions),
            emergencies=sorted(self.emergencies),
            open_orders=[o.client_order_id for o in remaining],
        )
        if remaining or self.positions:
            log.error(
                f"{self.account}_kill_switch_incomplete",
                alert=True,
                remaining=[o.client_order_id for o in remaining],
            )

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
            out[sym] = build_bars(
                sym,
                await self.candles.load_candles(sym, SIGNAL_TF),
                await self.candles.load_candles(sym, REGIME_TF),
            )
        return out

    async def step(self) -> int:
        bars = await self._bars()
        index = {s: {b.close_time: i for i, b in enumerate(bs)} for s, bs in bars.items()}
        last = self.last_bar_time
        times = sorted(
            {
                b.close_time
                for bs in bars.values()
                for b in bs
                if last is None or b.close_time > last
            }
        )
        for t in times:
            for sym in self.symbols:
                i = index[sym].get(t)
                if i is not None:
                    await self._on_bar(sym, bars[sym][max(0, i - WINDOW) : i + 1], t)
            events = self.risk.update(t, self.pf.equity())
            for e in events:
                self._event("risk_event", t, event=e.kind, equity=e.equity, detail=e.detail)
            if any(e.kind is RiskEventKind.KILL_SWITCH for e in events):
                await self._kill(t)
            self._events.append(
                (
                    t,
                    "bar_processed",
                    {"processed_at": self.clock().isoformat(), "equity": str(self.pf.equity())},
                )
            )
            self.last_bar_time = t
            await self._persist()
        return len(times)

    async def _on_bar(self, sym: str, window: list[Bar], t: datetime) -> None:
        bar = window[-1]
        f = self.filters[sym]
        if sym in self.positions:
            if sym in self.emergencies:
                return  # se gestiona en _retry_emergencies
            orders, plan = self.positions[sym]
            st = await self.client.get_order(sym, orders.stop.client_order_id)
            if st is not None and st.status == "FILLED":
                self._record_exit(sym, st, str(plan.stop_kind), t)
                return
            reason = self.strategy.on_candle_close(plan, bar)
            if reason is not None:
                await self._close(sym, str(reason), t)
                return
            new_stop = f.round_price(Decimal(repr(plan.stop)), "down")
            if new_stop > orders.stop.stop_price:
                filled = await self.om.move_stop(orders, new_stop)
                if filled is not None:
                    self._record_exit(sym, filled, str(plan.stop_kind), t)
                    return
            self.pf.mark(sym, Decimal(repr(bar.close)))
            return

        if not self.can_open:
            return
        if self.clock() - t > interval_delta(SIGNAL_TF):
            return  # señal caducada: se procesa tarde (caída, reinicio); no se entra
        sig, _ = self.strategy.evaluate_entry(window[-(self.strategy.lookback + 1) :])
        if sig is None:
            return
        intent = EntryIntent(
            sym,
            f.round_price(Decimal(repr(sig.limit_price)), "down"),
            f.round_price(Decimal(repr(sig.stop)), "down"),
        )
        decision = self.risk.approve_entry(intent, self.pf, f, t)
        if not decision.approved:
            self._event("signal_rejected", t, symbol=sym, reason=decision.reason)
            return
        try:
            orders = await self.om.open_position(decision, intent, t)
        except StopPlacementError as exc:
            if exc.entry is not None and exc.exit is not None:
                self._book_entry(sym, exc.entry, exc.entry.executed_qty, intent.stop, t)
                self._record_exit(sym, exc.exit, "stop_rechazado_cerrada", t)
            self._event("stop_failed_closed", t, symbol=sym, error=exc)
            return
        except EmergencyCloseError as exc:
            # Se compró, el stop falló y el cierre no se confirmó: posición SIN stop en
            # emergencia. Se registra para reintentar la venta (mismo ID) en cada ciclo.
            if exc.entry is not None:
                qty = f.round_qty(exc.entry.executed_qty * (1 - self.fee))
                self._book_entry(sym, exc.entry, qty, intent.stop, t)
                self.positions[sym] = (PositionOrders(sym, t, qty, exc.entry, exc.entry), None)  # type: ignore[assignment]
            self.emergencies[sym] = "stop_rechazado"
            self._pause(f"emergencia en {sym}: {exc}")
            return
        self._book_entry(sym, orders.entry, orders.qty, intent.stop, t)
        plan = self.strategy.open_plan(
            sig, float(orders.entry.avg_price), float(intent.stop), float(self.fee), t
        )
        self.positions[sym] = (orders, plan)
        self._event(
            "position_opened",
            t,
            symbol=sym,
            qty=orders.qty,
            price=orders.entry.avg_price,
            stop=intent.stop,
            entry_id=orders.entry.client_order_id,
            stop_id=orders.stop.client_order_id,
        )

    def _book_entry(
        self, sym: str, entry: OrderInfo, qty: Decimal, stop: Decimal, t: datetime
    ) -> None:
        px = entry.avg_price
        risk_amount = qty * ((px - stop) + self.fee * (px + stop))
        self.pf.buy(sym, qty, px, t, max(risk_amount, Decimal(0)))

    # ------------------------------------------------------------ reconstrucción

    async def recover_orphans(self, lookback: timedelta = timedelta(days=7)) -> None:
        """Entradas ejecutadas en el exchange que no están en el estado (caída entre la compra y
        el guardado): con stop abierto se adoptan; sin stop se cierran de emergencia."""
        since = self.clock() - lookback
        for sym in self.symbols:
            if sym in self.positions:
                continue
            orders = await self.om.recent_orders(sym, since)
            by_id = {o.client_order_id: o for o in orders}
            for entry in (o for o in orders if f"-{sym}-E-" in o.client_order_id):
                if entry.status != "FILLED":
                    continue
                stamp = entry.client_order_id.rsplit("-E-", 1)[1]
                related = [o for c, o in by_id.items() if stamp in c and c != entry.client_order_id]
                closed = any(o.status == "FILLED" and o.side == "SELL" for o in related)
                if closed or any(
                    t.symbol == sym and t.opened_at.strftime("%y%m%d%H%M") == stamp
                    for t in self.pf.trades
                ):
                    continue
                stops = [o for o in related if o.type == "STOP_LOSS" and o.is_open]
                signal_time = datetime.strptime(stamp, "%y%m%d%H%M").replace(
                    tzinfo=UTC
                ) + timedelta(seconds=59, milliseconds=999)
                if stops:
                    stop = max(stops, key=lambda o: o.client_order_id)
                    parts = stop.client_order_id.split("-")
                    version = int(parts[4]) if len(parts) > 4 else 0
                    pos = PositionOrders(sym, signal_time, stop.orig_qty, entry, stop, version)
                    self._book_entry(sym, entry, stop.orig_qty, stop.stop_price, signal_time)
                    rebuilt = EntrySignal(
                        sym,
                        signal_time,
                        float(entry.avg_price),
                        0.0,
                        float(stop.stop_price),
                        "market",
                        "H1",
                    )
                    plan = self.strategy.open_plan(
                        rebuilt,
                        float(entry.avg_price),
                        float(stop.stop_price),
                        float(self.fee),
                        signal_time,
                    )
                    self.positions[sym] = (pos, plan)
                    self._event(
                        "position_rebuilt",
                        symbol=sym,
                        entry=entry.client_order_id,
                        stop=stop.client_order_id,
                    )
                else:
                    qty = self.filters[sym].round_qty(entry.executed_qty * (1 - self.fee))
                    self._book_entry(sym, entry, qty, entry.avg_price * Decimal("0.9"), signal_time)
                    try:
                        fill = await self.om.emergency_sell(sym, qty, signal_time)
                    except EmergencyCloseError as exc:
                        dummy = PositionOrders(sym, signal_time, qty, entry, entry, 0)
                        self.positions[sym] = (dummy, None)  # type: ignore[assignment]
                        self.emergencies[sym] = "huérfana_sin_stop"
                        self._event("emergency", symbol=sym, error=exc)
                        continue
                    self._record_exit(sym, fill, "huérfana_sin_stop", self.clock())
                break

    # ------------------------------------------------------------ reconciliación

    async def check_clock(self, max_drift_ms: float = 1000.0) -> float:
        """Sincroniza la corrección de reloj de la firma y comprueba el desfase residual. Si el
        desfase bruto es enorme o el residual supera el máximo, se pausan las entradas."""
        try:
            offset = await self.client.sync_clock()
        except ValueError as exc:  # desfase bruto > 30 s
            self._pause(f"reloj desincronizado: {exc}")
            return float("inf")
        if abs(offset) > max_drift_ms:
            log.warning(f"{self.account}_clock_offset_corrected", offset_ms=round(offset))
        before = self.clock()
        server = await self.client.server_time()
        # Punto medio de la petición: la latencia de red no es desfase del reloj.
        midpoint = before + (self.clock() - before) / 2
        drift = (midpoint + timedelta(milliseconds=offset) - server).total_seconds() * 1000
        if abs(drift) > max_drift_ms:
            self._pause(f"reloj desincronizado {drift:.0f} ms")
        elif self.paused_reason and self.paused_reason.startswith("reloj desincronizado"):
            self.paused_reason = None
            self._event("resumed", reason="reloj sincronizado")
        return drift

    async def reconcile(self) -> None:
        """Si el exchange falla o responde datos inválidos, no se abre nada hasta la próxima
        reconciliación correcta."""
        try:
            await self.check_clock()
            await self._reconcile_orders()
            await self.recover_orphans()
            await self._retry_emergencies()
        except EXCHANGE_ERRORS as exc:
            if self.exchange_ok:
                log.error(f"{self.account}_exchange_unavailable", alert=True, error=str(exc))
                self._event("exchange_unavailable", error=exc)
            self.exchange_ok = False
            if isinstance(exc, ExchangeDataError):
                self._pause(f"datos incompletos del exchange: {exc}")
            return
        if not self.exchange_ok:
            self._event("exchange_available")
        self.exchange_ok = True

    async def _reconcile_orders(self) -> None:
        healthy = {
            s: o
            for s, (o, p) in self.positions.items()
            if s not in self.emergencies and p is not None
        }
        if not healthy:
            ours = [
                o
                for o in await self.client.open_orders()
                if o.client_order_id.startswith(f"{self.om.prefix}-")
            ]
            if ours and self.paused_reason is None:
                self._pause(f"órdenes desconocidas: {[o.client_order_id for o in ours]}")
            return
        report = await self.om.reconcile(healthy)
        now = self.clock()
        for sym, fill in report.stops_filled.items():
            self._record_exit(sym, fill, "stop_exchange", now)
        if report.stops_replaced:
            self._event("stops_replaced", now, symbols=report.stops_replaced)
        if report.unknown_orders:
            ids = [o.client_order_id for o in report.unknown_orders]
            self._pause(f"órdenes desconocidas: {ids}")

    def _pause(self, reason: str) -> None:
        if self.paused_reason == reason:
            return
        self.paused_reason = reason
        log.error(f"{self.account}_paused", alert=True, reason=reason)
        self._event("paused", reason=reason)

    async def run(self, stop: asyncio.Event) -> None:
        await self.start()
        while not stop.is_set():
            try:
                await self.reconcile()
                if self.exchange_ok:
                    await self.step()
                await self._persist()
            except EXCHANGE_ERRORS as exc:
                self.exchange_ok = False
                log.error(f"{self.account}_exchange_error", alert=True, error=repr(exc))
                with contextlib.suppress(Exception):
                    await self._persist()
            except Exception as exc:
                log.error(f"{self.account}_error", alert=True, error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.reconcile_every_s)
