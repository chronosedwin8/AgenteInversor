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
from bot.execution.session import TradingSession
from bot.portfolio.portfolio import Trade
from bot.risk.manager import RiskEvent
from bot.strategy.base import Strategy
from bot.strategy.rules import ExitReason


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
class BacktestResult:
    trades: list[Trade]
    equity: list[tuple[datetime, Decimal]]
    initial_capital: Decimal
    signals: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    unfilled: int = 0
    risk_events: list[RiskEvent] = field(default_factory=list)
    kill_switch_at: datetime | None = None


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
    """Recorre la historia y alimenta una `TradingSession` (la misma lógica que en paper)."""

    def __init__(
        self,
        data: dict[str, SymbolData],
        risk_cfg: RiskConfig,
        fee_rate: Decimal,
        slippage: Decimal,
        candidates: dict[str, set[int]] | None = None,
        market_delay_bars: int = 0,
    ):
        self.data = data
        self.risk_cfg = risk_cfg
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.candidates = candidates
        self.market_delay_bars = market_delay_bars  # auditoría: retrasar entradas a mercado
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
        starts = [p.start for p in schedule]

        def strategy_at(t: datetime) -> Strategy:
            return schedule[max(bisect.bisect_right(starts, t) - 1, 0)].strategy

        capital = initial_capital or self.risk_cfg.initial_capital_usdt
        session = TradingSession(
            self.risk_cfg, {s: d.filters for s, d in self.data.items()}, self.fee_rate,
            self.slippage, strategy_at, capital, market_delay_bars=self.market_delay_bars,
        )  # fmt: skip
        max_lb = max(p.strategy.lookback for p in schedule)
        session.equity.append((start, capital))

        i0 = bisect.bisect_left(self.timeline, start)
        for t in self.timeline[i0:]:
            if t >= end and not session.pending and not session.open:
                break
            changed = False
            for sym in self.symbols:
                d = self.data[sym]
                i = d.index.get(t)
                if i is None:
                    continue
                check = self.candidates is None or i in self.candidates[sym]
                window = d.snapshots[max(0, i - max_lb) : i + 1]
                changed |= session.on_bar(sym, window, t, entries_allowed=t < end,
                                          check_signal=check)  # fmt: skip
            session.end_timestep(t, changed)

        last_t = self.timeline[-1] if self.timeline else end
        session.close_all_at_marks(last_t, ExitReason.END_OF_DATA)
        session.equity.append((last_t, session.pf.equity()))
        st = session.stats
        return BacktestResult(
            trades=session.pf.trades,
            equity=session.equity,
            initial_capital=capital,
            signals=st.signals,
            rejected=st.rejected,
            unfilled=st.unfilled,
            risk_events=st.risk_events,
            kill_switch_at=st.kill_switch_at,
        )
