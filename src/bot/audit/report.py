"""Informe Markdown de la auditoría de H1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bot.audit.h1 import AuditResult, Run
from bot.audit.stats import RiskMetrics


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _rm_row(name: str, m: RiskMetrics) -> str:
    calmar = "∞" if m.calmar == float("inf") else f"{m.calmar:.2f}"
    return (
        f"| {name} | {_pct(m.cagr)} | {_pct(m.vol)} | {m.sharpe:.2f} | {m.sortino:.2f} | "
        f"{_pct(m.max_dd)} | {calmar} | {m.max_dd_days} |"
    )


def render(a: AuditResult) -> str:
    S: dict[str, Any] = a.sections
    base: Run = S["base"]
    m = base.metrics
    verdict = "VENTAJA DEMOSTRADA" if a.edge_demonstrated else "VENTAJA NO DEMOSTRADA"
    L = [
        f"# Auditoría de H1 — resultados ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)",
        "",
        "Plan y regla de decisión pre-registrados: [auditoria-h1-plan.md](auditoria-h1-plan.md). "
        "H1 sin modificar (parámetros por defecto del protocolo). RiskManager completo (Fase 3), "
        "comisión 0,1% y deslizamiento 0,05% por lado salvo donde se indica. Sin IA.",
        "",
        f"## Veredicto: **{verdict}**",
        "",
        "| Condición | Cumple | Valor |",
        "| --- | --- | --- |",
        *[f"| {k} | {'✅' if ok else '❌'} | {v} |" for k, (ok, v) in a.decision.items()],
        "",
        f"## 0. H1-fijo, {base.daily[0][0]} → {base.daily[-1][0]}",
        "",
        f"{m.trades} operaciones, PF {m.profit_factor:.2f}, neto {m.net_pnl:+.2f} USDT "
        f"({m.return_pct:+.1f}%), ganadoras {m.win_rate:.0%}, R medio {m.avg_r:+.3f}, "
        f"comisiones {m.fees:.2f} USDT. Kill switch: "
        f"{S['risk']['kill_switch'] or 'no se activó'}. Eventos de riesgo: "
        f"{len(S['risk']['risk_events'])}.",
        "",
    ]
    # 1
    conc, share, total_r = S["concentration"]
    L += [
        "## 1. Concentración del resultado",
        "",
        "| Excluyendo las k mejores | Neto USDT | PF | R total |",
        "| --- | --- | --- | --- |",
        f"| ninguna | {float(m.net_pnl):+.2f} | {m.profit_factor:.2f} | {total_r:+.1f} |",
    ]
    L += [f"| k = {k} | {v[0]:+.2f} | {v[1]:.2f} | {v[2]:+.1f} |" for k, v in conc.items()]
    L += (
        [""]
        + [
            f"- El {int(p * 100)}% superior ({k} operaciones) aporta {s_net:.0%} del neto y "
            f"{s_gross:.0%} de la ganancia bruta."
            for p, (k, s_net, s_gross) in share.items()
        ]
        + [""]
    )
    # 2
    costs, breakeven = S["costs"]
    L += [
        "## 2. Costos",
        "",
        "| Escenario (comisión + deslizamiento por lado) | Ops | PF | Neto |",
        "| --- | --- | --- | --- |",
    ]
    L += [f"| {k} | {v[0]} | {v[1]:.2f} | {v[2]:+.2f} |" for k, v in costs.items()]
    sp = ", ".join(f"{s}: {v * 100:.4f}%" for s, v in S["spreads"].items())
    L += [
        "",
        f"- Deslizamiento de equilibrio (neto = 0) con comisión 0,1%: **{_pct(breakeven)} "
        "por lado**.",
        f"- Spread actual medido en el libro (mediana): {sp}. Una orden a "
        "mercado paga medio spread, incluido en el deslizamiento modelado.",
        "- Funding: **no aplica** (spot, sin apalancamiento, sin perpetuos ni préstamos). "
        "Tampoco se modela interés sobre el USDT inactivo.",
        "",
    ]
    # 3
    R = S["risk"]
    L += [
        "## 3. Riesgo, exposición y pérdidas",
        "",
        "| Serie | CAGR | Vol. anual | Sharpe | Sortino | DD máx | Calmar | Días máx en DD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        _rm_row("H1-fijo", R["h1"]),
        _rm_row("Comprar y mantener (1/3 c/u)", R["bh"]),
        _rm_row(
            f"Comprar y mantener × exposición media ({R['avg_exposure']:.0%})", R["bh_exposure"]
        ),
        "",
        f"- Tiempo con al menos una posición abierta: {R['time_in_market']:.0%}; exposición "
        f"media: {R['avg_exposure']:.1%} del capital.",
        f"- Asimetría diaria {R['h1'].skew:.2f}, curtosis {R['h1'].kurtosis:.1f}.",
        f"- Racha máxima de pérdidas: {R['max_losing_streak']} operaciones. Peor mes: "
        f"{R['worst_month'][0]} ({R['worst_month'][1]:+.2f} USDT).",
        "",
        "| Distribución de R | Operaciones |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in R["buckets"].items()],
        "",
        "Peores operaciones: "
        + "; ".join(
            f"{t.symbol} {t.opened_at:%Y-%m-%d} {t.pnl:+.2f} USDT ({t.r_multiple:+.2f}R)"
            for t in R["worst"]
        ),
        "",
    ]
    # 4
    boot, tb = S["bootstrap"]
    L += [
        "## 4. Robustez",
        "",
        "### Bootstrap estacionario de rendimientos diarios",
        "",
        "| Bloque medio | P(Sharpe ≤ 0) | Sharpe 5–95% | CAGR 5–95% |",
        "| --- | --- | --- | --- |",
    ]
    L += [
        f"| {k} días | {v[0]:.3f} | {v[1][0]:.2f} → {v[1][1]:.2f} | {_pct(v[2][0])} → "
        f"{_pct(v[2][1])} |"
        for k, v in boot.items()
    ]
    L += [
        "",
        "### Bootstrap por bloques de operaciones (bloques de 5)",
        "",
        f"- P(R medio ≤ 0) = {tb.p_mean_r_le_0:.3f}; PF p5/p50/p95 = "
        f"{tb.pf_quantiles['p05']:.2f} / {tb.pf_quantiles['p50']:.2f} / "
        f"{tb.pf_quantiles['p95']:.2f}; P(PF < 1) = {tb.p_pf_below_1:.3f}; "
        f"P(PF < 1,3) = {tb.p_pf_below_1_3:.3f}.",
        f"- Drawdown p50/p95/p99 = {_pct(tb.dd_quantiles['p50'])} / "
        f"{_pct(tb.dd_quantiles['p95'])} / {_pct(tb.dd_quantiles['p99'])}; "
        f"P(DD > 8%) = {tb.p_dd_above_8:.3f}; P(capital ≤ 180) = {tb.p_kill_switch:.3f}.",
        "",
    ]
    L += [
        "### Entradas aleatorias (mismas salidas, misma frecuencia)",
        "",
        "| Control | Prob. de entrada por vela | p (PF aleatorio ≥ PF H1) | PF mediano | PF p95 | "
        "Neto mediano |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    L += [
        f"| {k} | {v[0]:.4f} | {v[1]:.3f} | {v[2]:.2f} | {v[3]:.2f} | {v[4]:+.2f} |"
        for k, v in S["random"].items()
    ]
    rb = S["robust"]
    L += [
        "",
        "### Retraso, pares, años, universo ampliado",
        "",
        f"- Entrada retrasada una vela de 4 h: {rb['delayed'].trades} ops, PF "
        f"{rb['delayed'].profit_factor:.2f}, neto {rb['delayed'].net_pnl:+.2f}.",
        "",
    ]
    L += ["| Sin el par | Ops | PF | Neto |", "| --- | --- | --- | --- |"]
    L += [
        f"| {s} | {x.trades} | {x.profit_factor:.2f} | {x.net_pnl:+.2f} |"
        for s, x in rb["loo"].items()
    ]
    L += ["", "| Año | Ops | Neto | PF |", "| --- | --- | --- | --- |"]
    for y, ts in rb["years"].items():
        gp = sum(float(t.pnl) for t in ts if t.pnl > 0)
        gl = -sum(float(t.pnl) for t in ts if t.pnl < 0)
        pf = f"{gp / gl:.2f}" if gl > 0 else "∞"
        L.append(f"| {y} | {len(ts)} | {sum(float(t.pnl) for t in ts):+.2f} | {pf} |")
    ext: Run | None = rb["ext"]
    if ext is not None:
        em = ext.metrics
        L += [
            "",
            f"- Universo ampliado ({', '.join(rb['ext_symbols'])}): {em.trades} ops, PF "
            f"{em.profit_factor:.2f}, neto {em.net_pnl:+.2f} USDT, DD {_pct(ext.risk.max_dd)}. "
            "Por par: " + ", ".join(f"{s} {v:+.2f}" for s, v in sorted(em.by_symbol.items())),
            "",
        ]
    grid, stops = S["sensitivity"]
    pfs = [g[3].metrics.profit_factor for g in grid]
    better = sum(p >= m.profit_factor for p in pfs)
    L += [
        "### Sensibilidad (diagnóstico: no se eligen parámetros)",
        "",
        f"- 180 combinaciones: {sum(p > 1 for p in pfs)} con PF > 1 "
        f"({sum(p > 1 for p in pfs) / len(pfs):.0%}); {sum(p >= 1.3 for p in pfs)} con PF ≥ 1,3; "
        f"PF mediano {sorted(pfs)[len(pfs) // 2]:.2f}. Los parámetros del plan quedan en el puesto "
        f"{better} de 180 por PF.",
        "- Stop inicial: "
        + ", ".join(
            f"{k}×ATR → PF {v.profit_factor:.2f} ({v.trades} ops)" for k, v in stops.items()
        )
        + ".",
        "- Detalle completo en `docs/auditoria/h1_sensibilidad.csv`.",
        "",
    ]
    for ema in (50, 100, 200):
        L.append(f"PF con EMA régimen {ema} (filas: ruptura N; columnas: trailing K)")
        L.append("")
        ks = sorted({g[2] for g in grid})
        L.append("| N \\ K | " + " | ".join(f"{k:g}" for k in ks) + " |")
        L.append("|" + " --- |" * (len(ks) + 1))
        for n in sorted({g[1] for g in grid}):
            row = [
                next(g[3].metrics.profit_factor for g in grid if g[:3] == (ema, n, k)) for k in ks
            ]
            L.append(f"| {n} | " + " | ".join(f"{x:.2f}" for x in row) + " |")
        L.append("")
    dsr, sr_star, var_sr, n_var = S["dsr"]
    L += [
        "### Sharpe deflactado",
        "",
        f"- N = 81 configuraciones probadas; varianza de Sharpe estimada con {n_var} "
        f"configuraciones (rejilla de sensibilidad de H1 + rejilla de H2) = {var_sr:.2e}.",
        f"- Umbral SR* diario = {sr_star:.4f} (anual ≈ {sr_star * 365**0.5:.2f}); "
        f"probabilidad deflactada = **{dsr:.3f}**.",
        "",
    ]
    wf = S["wf"]
    L += [
        "### H1 con walk-forward (comparación, con RiskManager completo)",
        "",
        "| Etapa | Ops | PF | DD | Últ. 3 meses | Neto |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for k, r in wf.items():
        x = r.oos_metrics
        L.append(
            f"| {k} | {x.trades} | {x.profit_factor:.2f} | {x.max_drawdown_pct:.2f}% | "
            f"{r.last_months_pnl:+.2f} | {x.net_pnl:+.2f} |"
        )
    L += [
        "",
        "## 5. Cronología y anticipación",
        "",
        "Prueba de truncado (eliminar el futuro no cambia ninguna operación cerrada antes del "
        "corte): "
        + ", ".join(
            f"{c}: {n} ops {'idénticas ✅' if ok else 'DIFERENTES ❌'}"
            for c, (n, ok) in S["truncation"].items()
        )
        + ".",
        "",
    ]
    return "\n".join(L)
