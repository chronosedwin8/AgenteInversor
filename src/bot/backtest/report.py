"""Informe del backtest: summary.md, trades.csv, equity.csv, windows.json en reports/."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bot.backtest.metrics import Metrics
from bot.backtest.walkforward import WalkForwardResult
from bot.portfolio.portfolio import Trade


def _metrics_md(title: str, m: Metrics) -> list[str]:
    reasons = ", ".join(f"{k}: {v}" for k, v in sorted(m.exit_reasons.items()))
    by_symbol = ", ".join(f"{k}: {v:+.2f}" for k, v in sorted(m.by_symbol.items()))
    return [
        f"### {title}",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Operaciones | {m.trades} |",
        f"| Ganadoras | {m.wins} ({m.win_rate:.1%}) |",
        f"| Profit factor (neto de costos) | {m.profit_factor:.2f} |",
        f"| Resultado neto | {m.net_pnl:+.2f} USDT ({m.return_pct:+.2f}%) |",
        f"| Drawdown máximo | {m.max_drawdown_pct:.2f}% |",
        f"| R medio por operación | {m.avg_r:+.3f} R |",
        f"| Comisiones pagadas | {m.fees:.2f} USDT |",
        f"| Duración media | {m.avg_hold_hours:.1f} h |",
        f"| Motivo de cierre final | {reasons} |",
        f"| Resultado por par | {by_symbol} |",
        "",
    ]


def _monthly_md(m: Metrics) -> list[str]:
    lines = ["| Mes | Resultado (USDT) |", "| --- | --- |"]
    lines += [f"| {k} | {v:+.2f} |" for k, v in m.by_month.items()]
    return [*lines, ""]


def write_trades_csv(path: Path, trades: Sequence[Trade]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "opened_at", "closed_at", "qty", "entry_price", "avg_exit_price",
                    "pnl", "fees", "risk_amount", "r_multiple", "exits"])  # fmt: skip
        for t in trades:
            w.writerow([
                t.symbol, t.opened_at.isoformat(), t.closed_at.isoformat(), t.qty, t.entry_price,
                round(t.avg_exit_price, 8), round(t.pnl, 6), round(t.fees, 6),
                round(t.risk_amount, 6), round(t.r_multiple, 4), "|".join(t.exits),
            ])  # fmt: skip


def write_equity_csv(path: Path, equity: Sequence[tuple[datetime, Decimal]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "equity"])
        for t, v in equity:
            w.writerow([t.isoformat(), round(v, 6)])


def write_report(
    out_dir: Path,
    wf: WalkForwardResult,
    baseline: Metrics,
    baseline_range: tuple[datetime, datetime],
    notes: Sequence[str],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_trades_csv(out_dir / "trades_oos.csv", wf.oos.trades)
    write_equity_csv(out_dir / "equity_oos.csv", wf.oos.equity)
    windows = [
        {
            "train": [c.window.train_start.isoformat(), c.window.train_end.isoformat()],
            "test": [c.window.test_start.isoformat(), c.window.test_end.isoformat()],
            "combo": c.combo,
            "por_defecto": c.used_default,
            "train_trades": c.train.trades,
            "train_profit_factor": c.train.profit_factor,
            "train_net_pnl": str(c.train.net_pnl),
        }
        for c in wf.choices
    ]
    (out_dir / "windows.json").write_text(json.dumps(windows, indent=1, default=str))

    m = wf.oos_metrics
    verdict = "PASA" if wf.passed else "NO PASA"
    lines = [
        f"# Backtest walk-forward — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"**Veredicto Fase 2: {verdict}**",
        "",
        "| Criterio | Resultado | Valor |",
        "| --- | --- | --- |",
    ]
    for name, (ok, detail) in wf.acceptance.items():
        lines.append(f"| {name} | {'✅' if ok else '❌'} | {detail} |")
    oos_from = wf.choices[0].window.test_start
    oos_to = wf.choices[-1].window.test_end
    lines += ["", f"## Fuera de muestra ({oos_from:%Y-%m-%d} → {oos_to:%Y-%m-%d})", ""]
    lines += _metrics_md("Resultado fuera de muestra", m)
    lines += _monthly_md(m)
    lines += [
        "## Parámetros elegidos por ventana (solo con datos de entrenamiento)",
        "",
        "| Validación | Entrenamiento: n / PF | Parámetros |",
        "| --- | --- | --- |",
    ]
    for c in wf.choices:
        combo = ", ".join(f"{k}={v}" for k, v in c.combo.items()) or "por defecto (plan)"
        lines.append(
            f"| {c.window.test_start:%Y-%m} → {c.window.test_end:%Y-%m-%d} | "
            f"{c.train.trades} / {c.train.profit_factor:.2f} | {combo} |"
        )
    first, last = baseline_range
    lines += [
        "",
        f"## Referencia: parámetros del plan sobre todo el periodo ({first:%Y-%m-%d} → "
        f"{last:%Y-%m-%d})",
        "",
        "No cuenta para el criterio (no es fuera de muestra); sirve de comparación.",
        "",
    ]
    lines += _metrics_md("Parámetros del plan, periodo completo", baseline)
    lines += ["## Notas", "", *[f"- {n}" for n in notes], ""]
    path = out_dir / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
