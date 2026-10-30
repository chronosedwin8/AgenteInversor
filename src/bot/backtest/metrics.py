"""Métricas de resultado: profit factor, drawdown, expectativa en R, desglose por mes y motivo."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from bot.portfolio.portfolio import Trade


@dataclass
class Metrics:
    trades: int
    wins: int
    win_rate: float
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: float
    net_pnl: Decimal
    return_pct: float
    max_drawdown_pct: float
    avg_r: float
    fees: Decimal
    avg_hold_hours: float
    exit_reasons: dict[str, int] = field(default_factory=dict)
    by_symbol: dict[str, Decimal] = field(default_factory=dict)
    by_month: dict[str, Decimal] = field(default_factory=dict)


def max_drawdown_pct(equity: Sequence[tuple[datetime, Decimal]]) -> float:
    peak = None
    worst = 0.0
    for _, v in equity:
        if peak is None or v > peak:
            peak = v
        if peak > 0:
            worst = max(worst, float((peak - v) / peak * 100))
    return worst


def profit_factor(trades: Sequence[Trade]) -> float:
    gp = sum((t.pnl for t in trades if t.pnl > 0), Decimal(0))
    gl = -sum((t.pnl for t in trades if t.pnl < 0), Decimal(0))
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return float(gp / gl)


def compute_metrics(
    trades: Sequence[Trade],
    equity: Sequence[tuple[datetime, Decimal]],
    initial_capital: Decimal,
) -> Metrics:
    n = len(trades)
    gp = sum((t.pnl for t in trades if t.pnl > 0), Decimal(0))
    gl = -sum((t.pnl for t in trades if t.pnl < 0), Decimal(0))
    net = gp - gl
    reasons: Counter[str] = Counter(t.exits[-1] for t in trades)
    by_symbol: dict[str, Decimal] = {}
    by_month: dict[str, Decimal] = {}
    for t in trades:
        by_symbol[t.symbol] = by_symbol.get(t.symbol, Decimal(0)) + t.pnl
        key = f"{t.opened_at:%Y-%m}"
        by_month[key] = by_month.get(key, Decimal(0)) + t.pnl
    hold = [(t.closed_at - t.opened_at).total_seconds() / 3600 for t in trades]
    return Metrics(
        trades=n,
        wins=sum(1 for t in trades if t.pnl > 0),
        win_rate=(sum(1 for t in trades if t.pnl > 0) / n) if n else 0.0,
        gross_profit=gp,
        gross_loss=gl,
        profit_factor=profit_factor(trades),
        net_pnl=net,
        return_pct=float(net / initial_capital * 100),
        max_drawdown_pct=max_drawdown_pct(equity),
        avg_r=(sum(float(t.r_multiple) for t in trades) / n) if n else 0.0,
        fees=sum((t.fees for t in trades), Decimal(0)),
        avg_hold_hours=(sum(hold) / n) if n else 0.0,
        exit_reasons=dict(reasons),
        by_symbol=by_symbol,
        by_month=dict(sorted(by_month.items())),
    )
