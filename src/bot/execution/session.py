"""Sesión de trading: la lógica de cada vela, idéntica en backtest y en paper (regla 3).

Recibe velas cerradas por símbolo y, al final de cada instante, valora el capital y aplica el
RiskManager (incluido el kill switch). Los llenados son simulados (backtest y paper): orden a
mercado en la apertura de la vela siguiente, orden límite si el mínimo cruza el límite, stop
con hueco a la apertura, stop antes que objetivo dentro de la misma vela, comisión y
deslizamiento en todo llenado. La ejecución real (testnet/live) usa el OrderManager (Fase 4).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import RiskConfig
from bot.exchange.filters import SymbolFilters
from bot.portfolio.portfolio import Portfolio
from bot.risk.manager import EntryIntent, RiskEvent, RiskEventKind, RiskManager, RiskState
from bot.strategy.base import Strategy
from bot.strategy.rules import EntrySignal, ExitReason, PositionPlan, on_partial_filled


def dec(x: float) -> Decimal:
    return Decimal(repr(x))


@dataclass
class PendingEntry:
    signal: EntrySignal
    limit: Decimal
    stop: Decimal
    qty: Decimal
    strategy: Strategy
    age: int = 0


@dataclass
class OpenPosition:
    plan: PositionPlan
    strategy: Strategy
    partial_qty: Decimal


@dataclass
class SessionStats:
    signals: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    unfilled: int = 0
    risk_events: list[RiskEvent] = field(default_factory=list)
    kill_switch_at: datetime | None = None


SessionListener = Callable[[str, dict[str, Any]], None]


class TradingSession:
    def __init__(
        self,
        risk_cfg: RiskConfig,
        filters: dict[str, SymbolFilters],
        fee_rate: Decimal,
        slippage: Decimal,
        strategy_at: Callable[[datetime], Strategy],
        initial_capital: Decimal | None = None,
        risk_state: RiskState | None = None,
        tz: ZoneInfo | None = None,
        market_delay_bars: int = 0,
        listener: SessionListener | None = None,
    ):
        self.filters = filters
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.strategy_at = strategy_at
        self.market_delay_bars = market_delay_bars
        self.pf = Portfolio(initial_capital or risk_cfg.initial_capital_usdt, fee_rate)
        self.risk = RiskManager(risk_cfg, tz=tz, state=risk_state)
        self.pending: dict[str, PendingEntry] = {}
        self.open: dict[str, OpenPosition] = {}
        self.stats = SessionStats()
        self.equity: list[tuple[datetime, Decimal]] = []
        self._buy = 1 + slippage
        self._sell = 1 - slippage
        self._listener = listener

    def _emit(self, kind: str, **data: Any) -> None:
        if self._listener is not None:
            self._listener(kind, data)

    # ------------------------------------------------------------ por vela

    def on_bar(self, sym: str, window: Sequence[Any], t: datetime, entries_allowed: bool,
               check_signal: bool = True) -> bool:  # fmt: skip
        """`window`: velas cerradas del símbolo hasta la actual inclusive. Devuelve True si hubo
        algo que valorar (orden llenada o posición abierta)."""
        bar = window[-1]
        changed = False

        # 1) Orden de entrada pendiente
        po = self.pending.get(sym)
        if po is not None:
            fill: Decimal | None = None
            if po.signal.order == "market" and po.age >= self.market_delay_bars:
                fill = dec(bar.open) * self._buy
            elif po.signal.order == "limit" and dec(bar.low) < po.limit:
                fill = po.limit * self._buy
            if fill is not None:
                del self.pending[sym]
                self._enter(po, fill, bar, t)
                return True
            po.age += 1
            if po.signal.order == "market" and po.age <= self.market_delay_bars:
                return False
            if po.age >= po.strategy.cancel_after_candles + self.market_delay_bars:
                del self.pending[sym]
                self.pf.release(sym)
                self.stats.unfilled += 1
                self._emit("order_cancelled", symbol=sym, at=t)

        # 2) Posición abierta
        op = self.open.get(sym)
        if op is not None:
            plan = op.plan
            changed = True
            if bar.low <= plan.stop:
                px = min(bar.open, plan.stop)
                self._close(sym, dec(px) * self._sell, t, plan.stop_kind)
                return True
            if not plan.partial_done and bar.high >= plan.target:
                h = self.pf.holdings[sym]
                qty = op.partial_qty if 0 < op.partial_qty < h.qty else Decimal(0)
                if qty > 0:
                    self.pf.sell(sym, qty, dec(plan.target) * self._sell, t, "parcial")
                on_partial_filled(plan)
            reason = op.strategy.on_candle_close(plan, bar)
            if reason is not None:
                self._close(sym, dec(bar.close) * self._sell, t, reason)
                return True
            self.pf.mark(sym, dec(bar.close))

        # 3) Nueva señal
        if not entries_allowed or not check_signal or sym in self.pending or sym in self.open:
            return changed
        strategy = self.strategy_at(t)
        lb = strategy.lookback
        if len(window) < lb + 1:
            return changed
        sig, _ = strategy.evaluate_entry(window[-(lb + 1) :])
        if sig is None:
            return changed
        self.stats.signals += 1
        f = self.filters[sym]
        limit = f.round_price(dec(sig.limit_price), "down")
        stop = f.round_price(dec(sig.stop), "down")
        decision = self.risk.approve_entry(EntryIntent(sym, limit, stop), self.pf, f, t)
        if not decision.approved:
            self.stats.rejected[decision.reason] = self.stats.rejected.get(decision.reason, 0) + 1
            self._emit("signal_rejected", symbol=sym, at=t, reason=decision.reason)
            return changed
        margin = Decimal("1.02") if sig.order == "market" else Decimal(1)
        reserve = decision.qty * limit * self._buy * (1 + self.fee_rate) * margin
        self.pf.reserve(sym, min(reserve, self.pf.free_cash()))
        self.pending[sym] = PendingEntry(sig, limit, stop, decision.qty, strategy)
        self._emit("order_placed", symbol=sym, at=t, qty=str(decision.qty), ref=str(limit),
                   stop=str(stop), order=sig.order, tag=sig.tag)  # fmt: skip
        return changed

    # ------------------------------------------------------------ fin de cada instante

    def end_timestep(self, t: datetime, changed: bool) -> list[RiskEvent]:
        equity = self.pf.equity()
        events = self.risk.update(t, equity)
        self.stats.risk_events.extend(events)
        for e in events:
            self._emit("risk_event", event=str(e.kind), at=t, equity=str(e.equity), detail=e.detail)
        if any(e.kind is RiskEventKind.KILL_SWITCH for e in events):
            self.kill_all(t)
            changed = True
        if changed:
            self.equity.append((t, self.pf.equity()))
        return events

    def kill_all(self, t: datetime) -> None:
        """Kill switch: cancelar órdenes y cerrar todo al último precio, sin excepción."""
        for sym in list(self.pending):
            del self.pending[sym]
            self.pf.release(sym)
        for sym in list(self.open):
            px = self.pf.marks.get(sym, self.pf.holdings[sym].entry_price) * self._sell
            self._close(sym, px, t, "kill_switch")
        self.stats.kill_switch_at = t

    def close_all_at_marks(self, t: datetime, reason: str) -> None:
        for sym in list(self.open):
            px = self.pf.marks.get(sym, self.pf.holdings[sym].entry_price)
            self._close(sym, px, t, reason)
        for sym in list(self.pending):
            del self.pending[sym]
            self.pf.release(sym)

    # ------------------------------------------------------------ llenados

    def _enter(self, po: PendingEntry, fill: Decimal, bar: Any, t: datetime) -> None:
        sym = po.signal.symbol
        f = self.filters[sym]
        self.pf.release(sym)
        qty = min(po.qty, f.round_qty(self.pf.cash / (fill * (1 + self.fee_rate))))
        if qty <= 0:
            return
        risk_amount = qty * ((fill - po.stop) + self.fee_rate * (fill + po.stop))
        self.pf.buy(sym, qty, fill, t, max(risk_amount, Decimal(0)))
        self._emit("filled", symbol=sym, at=t, qty=str(qty), price=str(fill), stop=str(po.stop))
        if fill <= po.stop:  # abrió por debajo del stop: se sale de inmediato
            trade = self.pf.sell(sym, qty, dec(bar.open) * self._sell, t, ExitReason.STOP)
            if trade is not None:
                self._closed(trade.pnl, sym, t, ExitReason.STOP)
            return
        plan = po.strategy.open_plan(po.signal, float(fill), float(po.stop),
                                     float(self.fee_rate), t)  # fmt: skip
        partial = f.round_qty(qty * dec(plan.partial_fraction))
        self.open[sym] = OpenPosition(plan, po.strategy, partial)
        if bar.low <= plan.stop:
            self._close(sym, dec(plan.stop) * self._sell, t, plan.stop_kind)
            return
        reason = po.strategy.on_candle_close(plan, bar)
        if reason is not None:
            self._close(sym, dec(bar.close) * self._sell, t, reason)
        else:
            self.pf.mark(sym, dec(bar.close))

    def _close(self, sym: str, price: Decimal, t: datetime, reason: str) -> None:
        h = self.pf.holdings[sym]
        trade = self.pf.sell(sym, h.qty, price, t, str(reason))
        self.open.pop(sym, None)
        if trade is not None:
            self._closed(trade.pnl, sym, t, str(reason))

    def _closed(self, pnl: Decimal, sym: str, t: datetime, reason: str) -> None:
        self.stats.risk_events.extend(self.risk.on_trade_closed(pnl, t, self.pf.equity()))
        self._emit("trade_closed", symbol=sym, at=t, pnl=str(pnl), reason=reason)
