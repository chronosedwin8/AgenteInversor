"""Orquestación del backtest: carga de datos desde PostgreSQL, snapshots, walk-forward, informe."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.backtest.engine import Backtester, ParamPeriod, SymbolData, candidate_indices
from bot.backtest.metrics import Metrics, compute_metrics
from bot.backtest.report import write_report
from bot.backtest.walkforward import (
    WalkForwardResult,
    apply_combo,
    grid_combos,
    loosest_combo,
    run_walk_forward,
)
from bot.config import AppConfig
from bot.data.store import CandleStore
from bot.exchange.filters import SymbolFilters
from bot.strategy.rules import PullbackStrategy, RuleParams
from bot.strategy.snapshot import build_snapshots


async def load_symbol_data(
    store: CandleStore, cfg: AppConfig, symbols: tuple[str, ...]
) -> dict[str, SymbolData]:
    tf = cfg.strategy.timeframes
    out: dict[str, SymbolData] = {}
    for sym in symbols:
        raw = await store.latest_symbol_info(sym)
        if raw is None:
            raise LookupError(
                f"no hay exchangeInfo guardado para {sym}; ejecuta `run` o `backfill`"
            )
        signal = await store.load_candles(sym, tf.signal)
        regime = await store.load_candles(sym, tf.regime)
        if not signal or not regime:
            raise LookupError(f"no hay velas de {sym}; ejecuta `backfill`")
        snaps = build_snapshots(sym, signal, regime, cfg.strategy)
        out[sym] = SymbolData(sym, snaps, SymbolFilters.from_exchange_info(raw))
    return out


@dataclass
class BacktestOutput:
    walk_forward: WalkForwardResult
    baseline: Metrics
    report_path: Path


def build_backtester(cfg: AppConfig, data: dict[str, SymbolData], base: RuleParams) -> Backtester:
    loose = PullbackStrategy(apply_combo(base, loosest_combo(cfg.backtest.grid), cfg.settings))
    candidates = {s: candidate_indices(d.snapshots, loose) for s, d in data.items()}
    return Backtester(
        data,
        cfg.risk,
        fee_rate=cfg.strategy.costs.fee_rate,
        slippage=cfg.strategy.costs.slippage_rate,
        candidates=candidates,
    )


def run_backtest(
    cfg: AppConfig,
    data: dict[str, SymbolData],
    reports_dir: Path,
    progress: Callable[[str], None] = lambda _: None,
) -> BacktestOutput:
    base = RuleParams.from_config(cfg.strategy, cfg.settings)
    bt = build_backtester(cfg, data, base)
    progress(f"candidatos por par: { {s: len(c) for s, c in (bt.candidates or {}).items()} }")

    def factory(combo: Mapping[str, Any]) -> PullbackStrategy:
        return PullbackStrategy(apply_combo(base, combo, cfg.settings))

    wf = run_walk_forward(
        bt, factory, grid_combos(cfg.backtest.grid), cfg.backtest.walk_forward,
        cfg.backtest.acceptance, progress,
    )  # fmt: skip

    first, last = bt.timeline[0], bt.timeline[-1]
    base_run = bt.run([ParamPeriod(first, PullbackStrategy(base))], first, last)
    baseline = compute_metrics(base_run.trades, base_run.equity, base_run.initial_capital)

    r = cfg.risk
    notes = [
        f"Capital inicial {r.initial_capital_usdt} USDT, riesgo {r.risk_per_trade_pct}% por "
        f"operación (comisiones incluidas en el tamaño), tope {r.max_position_pct}% por "
        f"posición, máx. {r.max_open_positions} posiciones.",
        f"Comisión {cfg.strategy.costs.fee_rate * 100}% por lado y deslizamiento "
        f"{cfg.strategy.costs.slippage_rate * 100}% en cada llenado (también en límites).",
        "Supuestos conservadores: stop antes que objetivo en la misma vela; stops con hueco se "
        "llenan a la apertura; la orden límite se llena solo si el precio cruza el límite.",
        "No se simulan spread histórico, noticias ni IA; tampoco los límites diarios, pérdidas "
        "consecutivas, drawdown ni kill switch (llegan en la Fase 3 y se volverá a correr).",
        f"Señales: {wf.oos.signals}; rechazadas por riesgo: {wf.oos.rejected}; "
        f"órdenes no llenadas: {wf.oos.unfilled}.",
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = write_report(reports_dir / f"backtest-{stamp}", wf, baseline, (first, last), notes)
    return BacktestOutput(wf, baseline, path)
