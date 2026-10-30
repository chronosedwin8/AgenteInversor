"""Motor de backtest por eventos (varios símbolos, capital compartido), para cualquier `Strategy`.

Usa las mismas estrategias, el `RiskManager` y el `Portfolio` que paper/live (regla 3).
Supuestos de simulación, deliberadamente conservadores:

- La señal se evalúa al cierre de la vela. Orden LIMIT_MAKER: vive `cancel_after_candles`
  velas y se llena solo si el mínimo cae ESTRICTAMENTE por debajo del límite. Orden a mercado:
  se llena en la apertura de la vela siguiente.
- Todo llenado paga comisión y deslizamiento adverso, también los límites.
- Dentro de una vela, si se tocan stop y objetivo, se asume que primero se tocó el stop.
- En la vela del llenado puede saltar el stop, pero no la toma parcial.
- Un stop con hueco de apertura se llena al precio de apertura (peor que el stop).
- Las salidas por regla de cierre se ejecutan al precio de cierre.
- Spread y noticias no se simulan (no hay histórico); la IA no interviene (Fase 5).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from bot.config import RiskConfig
from bot.exchange.filters import SymbolFilters
from bot.portfolio.portfolio import Portfolio, Trade
from bot.risk.manager import EntryIntent, RiskManager
from bot.strategy.base import Strategy
from bot.strategy.rules import EntrySignal, ExitReason, PositionPlan, on_partial_filled


def _dec(x: float) -> Decimal:
    return Decimal(repr(x))


@dataclass
class SymbolData:
    symbol: str
    snapshots: Sequence[Any]  # Snapshot (H0) o Bar (Fase 2b): open/high/low/close/close_time
    filters: SymbolFilters

    def __post_init__(self) -> None:
        self.index = {s.close_time: i for i, s in enumerate(self.snapshots)}


@dataclass(frozen=True)
class ParamPeriod:
    start: datetime
    strategy: Strategy


@dataclass
class _Pending:
    signal: EntrySignal
    limit: Decimal
    stop: Decimal
    qty: Decimal
    strategy: Strategy
    age: int = 0


@dataclass
class _Open:
    plan: PositionPlan
    strategy: Strategy
    partial_qty: Decimal


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: list[tuple[datetime, Decimal]]
    initial_capital: Decimal
    signals: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    unfilled: int = 0


def candidate_indices(snapshots: Sequence[Any], loosest: Strategy) -> set[int]:
    """Índices donde hay señal con la estrategia más permisiva de la rejilla. Si los umbrales
    de la rejilla son monótonos, cualquier combinación más estricta es un subconjunto: es solo
    una optimización de velocidad; la decisión final la toma `evaluate_entry`."""
    lb = loosest.lookback
    return {
        i
        for i in range(lb, len(snapshots))
        if loosest.evaluate_entry(snapshots[i - lb : i + 1])[0] is not None
    }


class Backtester:
    def __init__(
        self,
        data: dict[str, SymbolData],
        risk_cfg: RiskConfig,
        fee_rate: Decimal,
        slippage: Decimal,
        candidates: dict[str, set[int]] | None = None,
    ):
        self.data = data
        self.risk_cfg = risk_cfg
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.candidates = candidates
        self.symbols = sorted(data)
        times: set[datetime] = set()
        for d in data.values():
            times.update(s.close_time for s in d.snapshots)
        self.timeline = sorted(times)

    def run(
        self,
        schedule: Sequence[ParamPeriod],
        start: datetime,
        end: datetime,
        initial_capital: Decimal | None = None,
    ) -> BacktestResult:
        """Entradas permitidas con señal en [start, end); las posiciones abiertas se gestionan
        hasta que cierran (o se acaban los datos)."""
        if not schedule:
            raise ValueError("schedule vacío")
        capital = initial_capital or self.risk_cfg.initial_capital_usdt
        pf = Portfolio(capital, self.fee_rate)
        risk = RiskManager(self.risk_cfg)
        starts = [p.start for p in schedule]
        pending: dict[str, _Pending] = {}
        open_: dict[str, _Open] = {}
        res = BacktestResult(trades=pf.trades, equity=[(start, capital)], initial_capital=capital)
        buy_slip, sell_slip = 1 + self.slippage, 1 - self.slippage

        i0 = bisect.bisect_left(self.timeline, start)
        for t in self.timeline[i0:]:
            if t >= end and not pending and not open_:
                break
            changed = False
            for sym in self.symbols:
                d = self.data[sym]
                i = d.index.get(t)
                if i is None:
                    continue
                bar = d.snapshots[i]

                # 1) Orden de entrada pendiente
                po = pending.get(sym)
                if po is not None:
                    fill: Decimal | None = None
                    if po.signal.order == "market":
                        fill = _dec(bar.open) * buy_slip
                    elif _dec(bar.low) < po.limit:
                        fill = po.limit * buy_slip
                    if fill is not None:
                        del pending[sym]
                        changed = True
                        self._enter(pf, open_, po, fill, bar, t, sell_slip)
                        continue
                    po.age += 1
                    if po.age >= po.strategy.cancel_after_candles:
                        del pending[sym]
                        pf.release(sym)
                        res.unfilled += 1

                # 2) Posición abierta
                op = open_.get(sym)
                if op is not None:
                    plan = op.plan
                    changed = True
                    if bar.low <= plan.stop:
                        px = min(bar.open, plan.stop)
                        self._close(pf, open_, sym, _dec(px) * sell_slip, t, plan.stop_kind)
                        continue
                    if not plan.partial_done and bar.high >= plan.target:
                        h = pf.holdings[sym]
                        qty = op.partial_qty if 0 < op.partial_qty < h.qty else Decimal(0)
                        if qty > 0:
                            pf.sell(sym, qty, _dec(plan.target) * sell_slip, t, "parcial")
                        on_partial_filled(plan)
                    reason = op.strategy.on_candle_close(plan, bar)
                    if reason is not None:
                        self._close(pf, open_, sym, _dec(bar.close) * sell_slip, t, reason)
                        continue
                    pf.mark(sym, _dec(bar.close))

                # 3) Nueva señal
                if t >= end or sym in pending or sym in open_:
                    continue
                if self.candidates is not None and i not in self.candidates[sym]:
                    continue
                strategy = schedule[max(bisect.bisect_right(starts, t) - 1, 0)].strategy
                lb = strategy.lookback
                if i < lb:
                    continue
                sig, _ = strategy.evaluate_entry(d.snapshots[i - lb : i + 1])
                if sig is None:
                    continue
                res.signals += 1
                f = d.filters
                limit = f.round_price(_dec(sig.limit_price), "down")
                stop = f.round_price(_dec(sig.stop), "down")
                decision = risk.approve_entry(EntryIntent(sym, limit, stop), pf, f)
                if not decision.approved:
                    res.rejected[decision.reason] = res.rejected.get(decision.reason, 0) + 1
                    continue
                # Reserva con margen: una orden a mercado puede llenarse algo por encima.
                margin = Decimal("1.02") if sig.order == "market" else Decimal(1)
                reserve = decision.qty * limit * buy_slip * (1 + self.fee_rate) * margin
                pf.reserve(sym, min(reserve, pf.free_cash()))
                pending[sym] = _Pending(sig, limit, stop, decision.qty, strategy)

            if changed:
                res.equity.append((t, pf.equity()))

        # Fin de datos: se cierra lo que quede al último precio conocido.
        last_t = self.timeline[-1] if self.timeline else end
        for sym in list(open_):
            last_px = pf.marks.get(sym, pf.holdings[sym].entry_price)
            self._close(pf, open_, sym, last_px, last_t, ExitReason.END_OF_DATA)
        for sym in list(pending):
            pf.release(sym)
        res.equity.append((last_t, pf.equity()))
        return res

    def _enter(self, pf: Portfolio, open_: dict[str, _Open], po: _Pending, fill: Decimal,
               bar: Any, t: datetime, sell_slip: Decimal) -> None:  # fmt: skip
        sym = po.signal.symbol
        filters = self.data[sym].filters
        pf.release(sym)
        qty = min(po.qty, filters.round_qty(pf.cash / (fill * (1 + self.fee_rate))))
        if qty <= 0:
            return
        risk_amount = qty * ((fill - po.stop) + self.fee_rate * (fill + po.stop))
        pf.buy(sym, qty, fill, t, max(risk_amount, Decimal(0)))
        if fill <= po.stop:  # abrió por debajo del stop: se sale de inmediato
            pf.sell(sym, qty, _dec(bar.open) * sell_slip, t, ExitReason.STOP)
            return
        plan = po.strategy.open_plan(po.signal, float(fill), float(po.stop),
                                     float(self.fee_rate), t)  # fmt: skip
        partial = filters.round_qty(qty * _dec(plan.partial_fraction))
        open_[sym] = _Open(plan, po.strategy, partial)
        if bar.low <= plan.stop:
            self._close(pf, open_, sym, _dec(plan.stop) * sell_slip, t, plan.stop_kind)
            return
        reason = po.strategy.on_candle_close(plan, bar)
        if reason is not None:
            self._close(pf, open_, sym, _dec(bar.close) * sell_slip, t, reason)
        else:
            pf.mark(sym, _dec(bar.close))

    def _close(self, pf: Portfolio, open_: dict[str, _Open], sym: str, price: Decimal,
               t: datetime, reason: str) -> None:  # fmt: skip
        h = pf.holdings[sym]
        pf.sell(sym, h.qty, price, t, str(reason))
        del open_[sym]
