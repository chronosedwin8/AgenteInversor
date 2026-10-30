"""Fase 2b: evaluación de las hipótesis pre-registradas (docs/fase2b-protocolo.md).

Etapa 1 (desarrollo) para H1, H2, H3. Etapa 2 (confirmación en holdout nunca mirado) SOLO para
las hipótesis que pasaron la etapa 1: el holdout no se usa para nada más.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from bot.backtest.engine import Backtester, ParamPeriod, SymbolData
from bot.backtest.metrics import Metrics
from bot.backtest.report import write_equity_csv, write_trades_csv
from bot.backtest.walkforward import (
    WalkForwardResult,
    WindowChoice,
    choose_per_window,
    evaluate_schedule,
    grid_combos,
)
from bot.config import AppConfig, WalkForwardConfig
from bot.data.store import CandleStore
from bot.exchange.filters import SymbolFilters
from bot.strategy.features import Bar, build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params, H2DipBuy, H2Params, H3Combined

Progress = Callable[[str], None]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Stage(_Frozen):
    start: date
    end: date | None = None


class HypothesisSpec(_Frozen):
    defaults: dict[str, float | int]
    grid: dict[str, tuple[float | int, ...]]


class Timeframes(_Frozen):
    signal: str
    regime: str


class HypothesesConfig(_Frozen):
    timeframes: Timeframes
    walk_forward: WalkForwardConfig
    stages: dict[str, Stage]
    H1: HypothesisSpec
    H2: HypothesisSpec


def load_hypotheses(path: Path) -> HypothesesConfig:
    return HypothesesConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ datos


@dataclass
class SymbolBars:
    bars: list[Bar]
    filters: SymbolFilters


async def load_bars(
    store: CandleStore, symbols: tuple[str, ...], tf: Timeframes
) -> dict[str, SymbolBars]:
    out: dict[str, SymbolBars] = {}
    for sym in symbols:
        raw = await store.latest_symbol_info(sym)
        if raw is None:
            raise LookupError(f"no hay exchangeInfo guardado para {sym}")
        signal = await store.load_candles(sym, tf.signal)
        regime = await store.load_candles(sym, tf.regime)
        if not signal or not regime:
            raise LookupError(f"faltan velas {tf.signal}/{tf.regime} de {sym}; ejecuta backfill")
        # Features calculadas sobre TODA la historia (son causales); luego se recorta por etapa.
        out[sym] = SymbolBars(
            build_bars(sym, signal, regime), SymbolFilters.from_exchange_info(raw)
        )
    return out


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def slice_stage(bars: dict[str, SymbolBars], stage: Stage) -> dict[str, SymbolData]:
    start = _utc(stage.start)
    end = _utc(stage.end) if stage.end else None
    out: dict[str, SymbolData] = {}
    for sym, sb in bars.items():
        sel = [b for b in sb.bars if b.close_time >= start and (end is None or b.close_time < end)]
        if sel:
            out[sym] = SymbolData(sym, sel, sb.filters)
    return out


# ------------------------------------------------------------------ hipótesis


def h1_factory(spec: HypothesisSpec) -> Callable[[Mapping[str, Any]], H1Breakout]:
    base = H1Params(**spec.defaults)  # type: ignore[arg-type]
    return lambda combo: H1Breakout(replace(base, **combo))


def h2_factory(spec: HypothesisSpec) -> Callable[[Mapping[str, Any]], H2DipBuy]:
    base = H2Params(**spec.defaults)  # type: ignore[arg-type]
    return lambda combo: H2DipBuy(replace(base, **combo))


@dataclass
class StageResult:
    name: str
    results: dict[str, WalkForwardResult] = field(default_factory=dict)
    choices: dict[str, list[WindowChoice]] = field(default_factory=dict)


def run_stage(
    name: str,
    data: dict[str, SymbolData],
    app: AppConfig,
    hyp: HypothesesConfig,
    only: set[str] | None,
    progress: Progress,
) -> StageResult:
    bt = Backtester(data, app.risk, app.strategy.costs.fee_rate, app.strategy.costs.slippage_rate)
    acc, wf = app.backtest.acceptance, hyp.walk_forward
    f1, f2 = h1_factory(hyp.H1), h2_factory(hyp.H2)
    c1, c2 = grid_combos(hyp.H1.grid), grid_combos(hyp.H2.grid)
    out = StageResult(name)
    want = only if only is not None else {"H1", "H2", "H3"}

    # Selección por ventana (solo entrenamiento) de H1 y H2; H3 reutiliza ambas elecciones.
    ch1 = choose_per_window(bt, f1, c1, wf, progress, f"[{name}] H1")
    ch2 = choose_per_window(bt, f2, c2, wf, progress, f"[{name}] H2")
    out.choices = {"H1": ch1, "H2": ch2}
    if "H1" in want:
        sched = [ParamPeriod(c.window.test_start, f1(c.combo)) for c in ch1]
        out.results["H1"] = evaluate_schedule(bt, ch1, sched, acc)
    if "H2" in want:
        sched = [ParamPeriod(c.window.test_start, f2(c.combo)) for c in ch2]
        out.results["H2"] = evaluate_schedule(bt, ch2, sched, acc)
    if "H3" in want:
        sched = [
            ParamPeriod(a.window.test_start, H3Combined(f1(a.combo), f2(b.combo)))
            for a, b in zip(ch1, ch2, strict=True)
        ]
        out.results["H3"] = evaluate_schedule(bt, ch1, sched, acc)
    return out


@dataclass
class ResearchOutput:
    development: StageResult
    confirmation: StageResult | None
    winner: str | None
    report_path: Path


def run_research(
    app: AppConfig, hyp: HypothesesConfig, bars: dict[str, SymbolBars], out_dir: Path,
    docs_path: Path, progress: Progress = lambda _: None,
) -> ResearchOutput:  # fmt: skip
    dev = run_stage("desarrollo", slice_stage(bars, hyp.stages["desarrollo"]), app, hyp, None,
                    progress)  # fmt: skip
    passed_dev = {k for k, r in dev.results.items() if r.passed}
    conf: StageResult | None = None
    if passed_dev:
        conf = run_stage("confirmacion", slice_stage(bars, hyp.stages["confirmacion"]), app, hyp,
                         passed_dev, progress)  # fmt: skip
    finalists = [k for k in sorted(passed_dev) if conf is not None and conf.results[k].passed]
    winner = (
        min(finalists, key=lambda k: conf.results[k].oos_metrics.max_drawdown_pct)
        if conf is not None and finalists
        else None
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir / f"research-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for stage in (dev, conf):
        if stage is None:
            continue
        for k, r in stage.results.items():
            write_trades_csv(run_dir / f"{stage.name}_{k}_trades.csv", r.oos.trades)
            write_equity_csv(run_dir / f"{stage.name}_{k}_equity.csv", r.oos.equity)
    text = render_report(dev, conf, winner, passed_dev, run_dir)
    (run_dir / "summary.md").write_text(text, encoding="utf-8")
    docs_path.write_text(text, encoding="utf-8")
    return ResearchOutput(dev, conf, winner, docs_path)


# ------------------------------------------------------------------ informe


def _row(name: str, r: WalkForwardResult) -> str:
    m: Metrics = r.oos_metrics
    marks = " ".join("✅" if ok else "❌" for ok, _ in r.acceptance.values())
    return (
        f"| {name} | {m.trades} | {m.profit_factor:.2f} | {m.max_drawdown_pct:.2f}% | "
        f"{r.last_months_pnl:+.2f} | {m.net_pnl:+.2f} ({m.return_pct:+.1f}%) | "
        f"{m.win_rate:.0%} | {m.avg_r:+.3f} | {marks} | {'PASA' if r.passed else 'no pasa'} |"
    )


def _stage_md(stage: StageResult) -> list[str]:
    first = next(iter(stage.results.values()))
    a, b = first.choices[0].window.test_start, first.choices[-1].window.test_end
    lines = [
        f"## Etapa: {stage.name} (fuera de muestra {a:%Y-%m-%d} → {b:%Y-%m-%d})",
        "",
        "| Hipótesis | Ops | PF | DD máx | Últ. 3 meses | Neto USDT | Ganadoras | R medio "
        "| Criterios (ops, PF, DD, 3m) | Veredicto |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [_row(k, r) for k, r in sorted(stage.results.items())]
    lines.append("")
    for k, r in sorted(stage.results.items()):
        m = r.oos_metrics
        reasons = ", ".join(f"{x}: {n}" for x, n in sorted(m.exit_reasons.items()))
        per_sym = ", ".join(f"{s}: {v:+.2f}" for s, v in sorted(m.by_symbol.items()))
        lines += [f"- **{k}**: cierres → {reasons}; por par → {per_sym}; señales "
                  f"{r.oos.signals}, rechazadas {r.oos.rejected}"]  # fmt: skip
    lines += ["", "Parámetros elegidos en cada entrenamiento:", ""]
    for k, chs in stage.choices.items():
        lines.append(f"- {k}: " + "; ".join(
            f"{c.window.test_start:%Y-%m} → {c.combo or 'defecto'} "
            f"(train n={c.train.trades}, PF={c.train.profit_factor:.2f})"
            for c in chs
        ))  # fmt: skip
    return [*lines, ""]


def render_report(dev: StageResult, conf: StageResult | None, winner: str | None,
                  passed_dev: set[str], run_dir: Path) -> str:  # fmt: skip
    lines = [
        f"# Fase 2b — Resultados ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)",
        "",
        "Protocolo pre-registrado: [fase2b-protocolo.md](fase2b-protocolo.md). "
        "Criterios: ≥ 100 operaciones, PF ≥ 1,3, drawdown ≤ 8%, últimos 3 meses > 0, en "
        "desarrollo **y** en confirmación.",
        "",
        f"**Resultado: {('gana ' + winner) if winner else 'ninguna hipótesis pasa'}**",
        "",
        *_stage_md(dev),
    ]
    if conf is None:
        lines += ["## Etapa: confirmación", "",
                  "No se ejecutó: ninguna hipótesis pasó desarrollo (el holdout queda intacto).",
                  ""]  # fmt: skip
    else:
        lines += [f"Pasaron desarrollo: {', '.join(sorted(passed_dev))}.", "", *_stage_md(conf)]
    lines += [f"Archivos: `{run_dir.as_posix()}`", ""]
    return "\n".join(lines)
