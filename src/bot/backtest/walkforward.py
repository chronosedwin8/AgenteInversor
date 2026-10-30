"""Walk-forward: elegir parámetros en un periodo de entrenamiento y validarlos en el siguiente.

Los parámetros de cada ventana de validación se eligen SOLO con su entrenamiento. Todo el tramo
fuera de muestra se simula como un único recorrido continuo (capital encadenado), con los
parámetros de la ventana en que se produce cada señal.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.backtest.engine import Backtester, BacktestResult, ParamPeriod
from bot.backtest.metrics import Metrics, compute_metrics
from bot.config import AcceptanceConfig, Settings, WalkForwardConfig
from bot.data.vision import Month
from bot.strategy.base import Strategy
from bot.strategy.rules import RuleParams


def _month_start(m: Month) -> datetime:
    return datetime(m.year, m.month, 1, tzinfo=UTC)


def _add(m: Month, n: int) -> Month:
    for _ in range(n):
        m = m.next()
    return m


@dataclass(frozen=True)
class Window:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def make_windows(data_start: datetime, data_end: datetime, cfg: WalkForwardConfig) -> list[Window]:
    first = Month(data_start.year, data_start.month)
    # data_start es el cierre de la primera vela (p. ej. día 1, 00:14:59 o 03:59:59 en 4 h). Si
    # la historia no empieza el primer día del mes, ese mes está incompleto: se empieza después.
    if data_start - _month_start(first) >= timedelta(days=1):
        first = first.next()
    m = _add(first, cfg.warmup_months)
    out: list[Window] = []
    while True:
        test = _add(m, cfg.train_months)
        test_start = _month_start(test)
        if test_start >= data_end:
            break
        test_end = min(_month_start(_add(test, cfg.test_months)), data_end)
        out.append(Window(_month_start(m), test_start, test_start, test_end))
        m = _add(m, cfg.test_months)
    return out


# ------------------------------------------------------------------ rejilla


def apply_combo(base: RuleParams, combo: Mapping[str, Any], settings: Settings) -> RuleParams:
    changes = dict(combo)
    if "enforce_entry_window" in changes:
        w = settings.universe.crypto.entry_window_local
        changes["entry_window"] = (w.start, w.end) if changes.pop("enforce_entry_window") else None
    return replace(base, **changes)


def grid_combos(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*grid.values())]


def loosest_combo(grid: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """Valores más permisivos para la entrada (para precalcular candidatos)."""
    out: dict[str, Any] = {}
    if "adx_min" in grid:
        out["adx_min"] = min(grid["adx_min"])
    if "volume_multiplier" in grid:
        out["volume_multiplier"] = min(grid["volume_multiplier"])
    if "enforce_entry_window" in grid:
        out["enforce_entry_window"] = False
    return out


# ------------------------------------------------------------------ ejecución


@dataclass
class WindowChoice:
    window: Window
    combo: dict[str, Any]
    train: Metrics
    used_default: bool


@dataclass
class WalkForwardResult:
    choices: list[WindowChoice]
    oos: BacktestResult
    oos_metrics: Metrics
    last_months_pnl: float
    acceptance: dict[str, tuple[bool, str]]

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.acceptance.values())


StrategyFactory = Callable[[Mapping[str, Any]], Strategy]


def select_params(
    bt: Backtester,
    factory: StrategyFactory,
    combos: Sequence[dict[str, Any]],
    window: Window,
    min_trades: int,
) -> WindowChoice:
    """Mejor combinación en el entrenamiento: mayor profit factor entre las que tienen al menos
    `min_trades` operaciones (empate: mayor resultado neto). Si ninguna, valores por defecto."""
    best: tuple[float, float, dict[str, Any], Metrics] | None = None
    for combo in combos:
        r = bt.run([ParamPeriod(window.train_start, factory(combo))], window.train_start,
                   window.train_end)  # fmt: skip
        m = compute_metrics(r.trades, r.equity, r.initial_capital)
        if m.trades < min_trades:
            continue
        key = (min(m.profit_factor, 1e9), float(m.net_pnl))
        if best is None or key > best[:2]:
            best = (key[0], key[1], combo, m)
    if best is None:
        r = bt.run([ParamPeriod(window.train_start, factory({}))], window.train_start,
                   window.train_end)  # fmt: skip
        return WindowChoice(
            window, {}, compute_metrics(r.trades, r.equity, r.initial_capital), True
        )
    return WindowChoice(window, best[2], best[3], False)


def evaluate_acceptance(
    m: Metrics, last_pnl: float, acc: AcceptanceConfig
) -> dict[str, tuple[bool, str]]:
    return {
        "operaciones": (m.trades >= acc.min_trades, f"{m.trades} (mín. {acc.min_trades})"),
        "profit_factor": (
            m.profit_factor >= acc.min_profit_factor,
            f"{m.profit_factor:.2f} (mín. {acc.min_profit_factor})",
        ),
        "drawdown_max": (
            m.max_drawdown_pct <= acc.max_drawdown_pct,
            f"{m.max_drawdown_pct:.2f}% (máx. {acc.max_drawdown_pct}%)",
        ),
        f"ultimos_{acc.last_months_positive}_meses": (
            last_pnl > 0,
            f"{last_pnl:+.2f} USDT (debe ser > 0)",
        ),
    }


def windows_for(bt: Backtester, wf: WalkForwardConfig) -> list[Window]:
    windows = make_windows(bt.timeline[0], bt.timeline[-1], wf)
    if not windows:
        raise ValueError("no hay datos suficientes para ninguna ventana de walk-forward")
    return windows


def choose_per_window(
    bt: Backtester,
    factory: StrategyFactory,
    combos: Sequence[dict[str, Any]],
    wf: WalkForwardConfig,
    progress: Callable[[str], None] = lambda _: None,
    label: str = "",
) -> list[WindowChoice]:
    choices: list[WindowChoice] = []
    for w in windows_for(bt, wf):
        ch = select_params(bt, factory, combos, w, wf.min_train_trades)
        progress(
            f"{label} ventana {w.test_start:%Y-%m}: train PF={ch.train.profit_factor:.2f} "
            f"n={ch.train.trades} combo={ch.combo or 'por defecto'}"
        )
        choices.append(ch)
    return choices


def evaluate_schedule(
    bt: Backtester,
    choices: list[WindowChoice],
    schedule: list[ParamPeriod],
    acc: AcceptanceConfig,
) -> WalkForwardResult:
    """Un único recorrido continuo fuera de muestra con los parámetros de cada ventana."""
    oos_start, oos_end = choices[0].window.test_start, choices[-1].window.test_end
    oos = bt.run(schedule, oos_start, oos_end)
    m = compute_metrics(oos.trades, oos.equity, oos.initial_capital)
    last_m = Month(oos_end.year, oos_end.month)
    for _ in range(acc.last_months_positive - 1):
        last_m = last_m.prev()
    cutoff = _month_start(last_m)
    last_pnl = float(sum(t.pnl for t in oos.trades if t.opened_at >= cutoff))
    return WalkForwardResult(choices, oos, m, last_pnl, evaluate_acceptance(m, last_pnl, acc))


def run_walk_forward(
    bt: Backtester,
    factory: StrategyFactory,
    combos: Sequence[dict[str, Any]],
    wf: WalkForwardConfig,
    acc: AcceptanceConfig,
    progress: Callable[[str], None] = lambda _: None,
    label: str = "",
) -> WalkForwardResult:
    choices = choose_per_window(bt, factory, combos, wf, progress, label)
    schedule = [ParamPeriod(c.window.test_start, factory(c.combo)) for c in choices]
    return evaluate_schedule(bt, choices, schedule, acc)
