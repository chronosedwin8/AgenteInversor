"""Motor de backtest por eventos sobre velas de 15 min (varios símbolos, capital compartido).

Usa exactamente las reglas de `bot.strategy.rules`, el `RiskManager` y el `Portfolio` que se usan
en paper/live (regla 3). Supuestos de simulación, deliberadamente conservadores:

- La señal se evalúa al cierre de la vela gatillo; la orden LIMIT_MAKER vive en las 2 velas
  siguientes y se llena solo si el mínimo cae ESTRICTAMENTE por debajo del límite.
- Todo llenado paga comisión (0,1%) y deslizamiento adverso (0,05%), también los límites.
- Dentro de una vela, si se tocan stop y objetivo, se asume que primero se tocó el stop.
- En la vela del llenado puede saltar el stop, pero no la toma parcial.
- Un stop con hueco de apertura se llena al precio de apertura (peor que el stop).
- Las salidas por regla de cierre (EMA20, tiempo) se ejecutan al precio de cierre.
- Spread y noticias no se simulan (no hay histórico); la IA no interviene (Fase 5).
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from bot.config import RiskConfig
from bot.exchange.filters import SymbolFilters
from bot.portfolio.portfolio import Portfolio, Trade
from bot.risk.manager import EntryIntent, RiskManager
from bot.strategy.rules import (
    ExitReason,
    PositionPlan,
    RuleParams,
    evaluate_entry,
    on_candle_close,
    on_partial_filled,
)
from bot.strategy.snapshot import Snapshot


def _dec(x: float) -> Decimal:
    return Decimal(repr(x))


@dataclass
class SymbolData:
    symbol: str
    snapshots: list[Snapshot]
    filters: SymbolFilters

    def __post_init__(self) -> None:
        self.index = {s.close_time: i for i, s in enumerate(self.snapshots)}


@dataclass(frozen=True)
class ParamPeriod:
    start: datetime
    params: RuleParams


@dataclass
class _Pending:
    symbol: str
    limit: Decimal
    stop: Decimal
    qty: Decimal
    params: RuleParams
    placed_at: datetime
    age: int = 0


@dataclass
class _Open:
    plan: PositionPlan
    params: RuleParams
    partial_qty: Decimal


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: list[tuple[datetime, Decimal]]
    initial_capital: Decimal
    signals: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    unfilled: int = 0


def candidate_indices(snapshots: Sequence[Snapshot], loosest: RuleParams) -> set[int]:
    """Índices donde la señal pasa con los parámetros más permisivos de la rejilla. Como los
    umbrales de la rejilla son monótonos, cualquier combinación más estricta es un subconjunto:
    es solo una optimización de velocidad, la decisión final la toma `evaluate_entry`."""
    lb = loosest.lookback
    out: set[int] = set()
    for i in range(lb, len(snapshots)):
        sig, _ = evaluate_entry(snapshots[i - lb : i + 1], loosest)
        if sig is not None:
            out.add(i)
    return out


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
                snap = d.snapshots[i]

                # 1) Orden de entrada pendiente
                po = pending.get(sym)
                if po is not None:
                    if _dec(snap.low) < po.limit:
                        del pending[sym]
                        fill = po.limit * buy_slip
                        risk_amount = po.qty * ((fill - po.stop) + self.fee_rate * (fill + po.stop))
                        pf.buy(sym, po.qty, fill, t, risk_amount)
                        plan = PositionPlan.open(
                            float(fill), float(po.stop), float(self.fee_rate), t, po.params
                        )
                        partial = d.filters.round_qty(po.qty * _dec(po.params.partial_fraction))
                        open_[sym] = _Open(plan, po.params, partial)
                        changed = True
                        if snap.low <= plan.stop:
                            self._close(
                                pf, open_, sym, _dec(plan.stop) * sell_slip, t, plan.stop_kind
                            )
                            continue
                        reason = on_candle_close(plan, snap, po.params)
                        if reason is not None:
                            self._close(pf, open_, sym, _dec(snap.close) * sell_slip, t, reason)
                        else:
                            pf.mark(sym, _dec(snap.close))
                        continue
                    po.age += 1
                    if po.age >= po.params.cancel_after_candles:
                        del pending[sym]
                        pf.release(sym)
                        res.unfilled += 1

                # 2) Posición abierta
                op = open_.get(sym)
                if op is not None:
                    plan = op.plan
                    changed = True
                    if snap.low <= plan.stop:
                        px = min(snap.open, plan.stop)
                        self._close(pf, open_, sym, _dec(px) * sell_slip, t, plan.stop_kind)
                        continue
                    if not plan.partial_done and snap.high >= plan.target:
                        h = pf.holdings[sym]
                        qty = op.partial_qty if 0 < op.partial_qty < h.qty else Decimal(0)
                        if qty > 0:
                            pf.sell(sym, qty, _dec(plan.target) * sell_slip, t, "parcial")
                        on_partial_filled(plan)
                    reason = on_candle_close(plan, snap, op.params)
                    if reason is not None:
                        self._close(pf, open_, sym, _dec(snap.close) * sell_slip, t, reason)
                        continue
                    pf.mark(sym, _dec(snap.close))

                # 3) Nueva señal
                if t >= end or sym in pending or sym in open_:
                    continue
                if self.candidates is not None and i not in self.candidates[sym]:
                    continue
                params = schedule[max(bisect.bisect_right(starts, t) - 1, 0)].params
                if i < params.lookback:
                    continue
                sig, _ = evaluate_entry(d.snapshots[i - params.lookback : i + 1], params)
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
                pf.reserve(sym, decision.qty * limit * buy_slip * (1 + self.fee_rate))
                pending[sym] = _Pending(sym, limit, stop, decision.qty, params, t)

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

    def _close(self, pf: Portfolio, open_: dict[str, _Open], sym: str, price: Decimal,
               t: datetime, reason: str) -> None:  # fmt: skip
        h = pf.holdings[sym]
        pf.sell(sym, h.qty, price, t, str(reason))
        del open_[sym]


def schedule_single(params: RuleParams, start: datetime) -> list[ParamPeriod]:
    return [ParamPeriod(start, params)]


def merge_candidates(sets: Iterable[dict[str, set[int]]]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for s in sets:
        for k, v in s.items():
            out.setdefault(k, set()).update(v)
    return out
