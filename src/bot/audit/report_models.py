"""Informe Markdown de la comparación de modelos y del análisis por régimen."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime

from bot.audit.models import Distribution, ModelComparison, ModelRow, RegimeTable


def _p(x: float) -> str:
    return f"{x * 100:.2f}%"


def _pf(x: float) -> str:
    return "∞" if math.isinf(x) else f"{x:.2f}"


def _num(fmt: str) -> Callable[[float], str]:
    return lambda x: format(x, fmt)


def _row(r: ModelRow) -> str:
    k = r.kill_switch_peak.date() if r.kill_switch_peak else "no"
    return (
        f"| {r.name} | {r.trades} | {r.net:+.2f} | {_pf(r.pf)} | {_p(r.risk.cagr)} | "
        f"{r.risk.sharpe:.2f} | {r.risk.sortino:.2f} | {_p(r.risk.max_dd)} | "
        f"{r.risk.calmar:.2f} | {r.time_in_market:.0%} | {r.avg_exposure:.1%} | "
        f"{_p(r.return_on_exposed)} | {r.r_q[0]:+.2f} / {r.r_q[1]:+.2f} / {r.r_q[2]:+.2f} | "
        f"{r.without_top10:+.2f} | {k} |"
    )


def _cell(d: Distribution, f: Callable[[ModelRow], float], fmt: Callable[[float], str]) -> str:
    lo, med, hi, _, _ = d.stat(f)
    return f"{fmt(med)} ({fmt(lo)} … {fmt(hi)})"


def _dist_row(name: str, d: Distribution) -> str:
    kills = sum(r.kill_switch_peak is not None for r in d.rows) / len(d.rows)
    cells = [
        _cell(d, lambda r: r.trades, _num(".0f")),
        _cell(d, lambda r: r.net, _num("+.1f")),
        _cell(d, lambda r: r.pf, _num(".2f")),
        _cell(d, lambda r: r.risk.sharpe, _num(".2f")),
        _cell(d, lambda r: r.risk.max_dd, _p),
        _cell(d, lambda r: r.return_on_exposed, _p),
    ]
    return f"| {name} | " + " | ".join(cells) + f" | {kills:.0%} |"


def _effect(name: str, b: ModelRow, d: Distribution) -> list[str]:
    out = []
    metrics: list[tuple[str, Callable[[ModelRow], float]]] = [
        ("neto (USDT)", lambda r: r.net),
        ("PF", lambda r: r.pf),
        ("Sharpe", lambda r: r.risk.sharpe),
    ]
    for label, f in metrics:
        _, med, _, mean, sd = d.stat(f)
        bv = f(b)
        pct = sum(f(r) < bv for r in d.rows) / len(d.rows)
        z = (bv - mean) / sd if sd > 0 else math.inf
        diff = bv - med
        out.append(
            f"| {name} | {label} | {bv:+.2f} | {med:+.2f} | {diff:+.2f} | {pct:.1%} | {z:+.2f} |"
        )
    return out


def render(cmp: ModelComparison, reg: RegimeTable) -> str:
    d1, tim, expo, net1 = cmp.d1
    d2, net2 = cmp.d2
    roe_d1 = _p(d1.cagr / expo) if expo else "—"
    lines = [
        f"# Comparación de modelos y análisis por régimen ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)",
        "",
        "Plan pre-registrado: [fase-testnet-plan.md](fase-testnet-plan.md) §2–3 (commit "
        "`12b3dbd`). Diagnóstico: no modifica H1 ni elige parámetros. BTC/ETH/SOL, "
        "2020-05-01 → 2026-10-30, costos 0,1% + 0,05% por lado, RiskManager completo con kill "
        "switch en base inicial; la última columna indica cuándo habría saltado con base en el "
        "máximo.",
        "",
        "## Modelos deterministas",
        "",
        "| Modelo | Ops | Neto | PF | CAGR | Sharpe | Sortino | DD máx | Calmar | Tiempo en "
        "mercado | Exposición media | Rent. s/ capital expuesto | R p10/p50/p90 | Neto sin 10 "
        "mejores | Kill switch (máx.) |",
        "|" + " --- |" * 15,
        _row(cmp.b),
        _row(cmp.c),
        f"| D1 régimen puro (1/3 por par) | — | {net1:+.2f} | — | {_p(d1.cagr)} | "
        f"{d1.sharpe:.2f} | {d1.sortino:.2f} | {_p(d1.max_dd)} | {d1.calmar:.2f} | {tim:.0%} | "
        f"{expo:.1%} | {roe_d1} (aprox.) | — | — | — |",
        f"| D2 comprar y mantener × exposición de B | — | {net2:+.2f} | — | {_p(d2.cagr)} | "
        f"{d2.sharpe:.2f} | {d2.sortino:.2f} | {_p(d2.max_dd)} | {d2.calmar:.2f} | 100% | "
        f"{cmp.b.avg_exposure:.1%} | — | — | — | — |",
        "",
        f"## Modelos aleatorios ({cmp.n_sims} simulaciones cada uno): mediana (p5 … p95)",
        "",
        "| Modelo | Ops | Neto | PF | Sharpe | DD máx | Rent. s/ capital expuesto | "
        "% con kill switch (máx.) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        _dist_row("A1 tendencia + aleatoria i.i.d.", cmp.a1),
        _dist_row("A2 tendencia + señales desplazadas (con dependencia)", cmp.a2),
        "",
        "## Tamaño del efecto de la ruptura (B frente a A)",
        "",
        "| Control | Métrica | B | Mediana A | Diferencia | Percentil de B en A | z |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *_effect("A1", cmp.b, cmp.a1),
        *_effect("A2", cmp.b, cmp.a2),
        "",
        "## Análisis por régimen de H1-fijo (descriptivo)",
        "",
        "Regla pre-registrada: tendencia *alcista* si cierre diario > EMA200 y EMA50 sube frente "
        "a 20 días antes; *bajista* si cierre < EMA200 y EMA50 baja; *lateral* en otro caso. "
        "Volatilidad *alta* si el ATR(14) diario en % supera la mediana del activo en el periodo.",
        "",
    ]
    for dim, rows in reg.rows.items():
        lines += [f"### {dim}", "", "| Grupo | Ops | Neto | PF | R medio |",
                  "| --- | --- | --- | --- | --- |"]  # fmt: skip
        for k, (n, net, pf, r) in rows.items():
            lines.append(f"| {k} | {n} | {net:+.2f} | {_pf(pf)} | {r:+.3f} |")
        lines.append("")
    return "\n".join(lines)
