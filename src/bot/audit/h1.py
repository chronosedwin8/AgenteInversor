"""Auditoría de H1 según docs/auditoria-h1-plan.md (pre-registrado). No modifica H1."""

from __future__ import annotations

import asyncio
import csv
import itertools
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.audit.stats import (
    RiskMetrics,
    TradeBootstrap,
    bootstrap_sharpe,
    daily_equity,
    deflated_sharpe,
    returns,
    risk_metrics,
    trade_block_bootstrap,
)
from bot.backtest.engine import Backtester, BacktestResult, ParamPeriod, SymbolData
from bot.backtest.metrics import Metrics, compute_metrics
from bot.backtest.research import HypothesesConfig, SymbolBars, run_stage, slice_stage
from bot.config import AppConfig
from bot.data.klines import Candle
from bot.data.store import CandleStore
from bot.data.vision import Month, VisionClient
from bot.exchange.filters import SymbolFilters
from bot.portfolio.portfolio import Trade
from bot.strategy.features import Bar, build_bars
from bot.strategy.hypotheses import (
    H1Breakout,
    H1Params,
    H2DipBuy,
    H2Params,
    _regime_up,
    _stop,
)
from bot.strategy.rules import EntrySignal

Progress = Callable[[str], None]
START = datetime(2020, 5, 1, tzinfo=UTC)
EXTENDED_UNIVERSE = (
    "XRPUSDT",
    "BCHUSDT",
    "LTCUSDT",
    "EOSUSDT",
    "BNBUSDT",
    "XMRUSDT",
    "XLMUSDT",
    "TRXUSDT",
    "ADAUSDT",
    "XTZUSDT",
    "ATOMUSDT",
    "NEOUSDT",
    "LINKUSDT",
)
N_TRIALS = 81


# ------------------------------------------------------------------ datos del universo ampliado


def generic_filters(symbol: str) -> SymbolFilters:
    """Filtros genéricos para pares del universo ampliado (algunos ya no existen en exchangeInfo).
    NOTIONAL 5 USDT como hoy; paso y tick finos para no distorsionar el tamaño."""
    return SymbolFilters.from_exchange_info(
        {
            "symbol": symbol,
            "status": "TRADING",
            "baseAsset": symbol[:-4],
            "quoteAsset": "USDT",
            "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS_LIMIT"],
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.00000001",
                    "maxPrice": "1000000",
                    "tickSize": "0.00000001",
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.00000001",
                    "maxQty": "90000000",
                    "stepSize": "0.00000001",
                },
                {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "9000000"},
            ],
        }
    )


async def download_extended(
    store: CandleStore, vision: VisionClient, until: Month, progress: Progress
) -> None:
    """Solo data.binance.vision (incluye pares deslistados). Meses inexistentes se saltan."""
    sem = asyncio.Semaphore(8)

    async def one(sym: str, interval: str, m: Month) -> list[Candle]:
        async with sem:
            return await vision.monthly(sym, interval, m) or []

    months = []
    m = Month(2020, 1)
    while (m.year, m.month) <= (until.year, until.month):
        months.append(m)
        m = m.next()
    for sym in EXTENDED_UNIVERSE:
        for interval in ("4h", "1d"):
            have = await store.bounds(sym, interval)
            if have.count > 0:
                continue
            chunks = await asyncio.gather(*(one(sym, interval, mm) for mm in months))
            rows = [c for ch in chunks for c in ch]
            n = await store.upsert(rows, "vision")
            progress(f"universo ampliado {sym} {interval}: {n} velas")


async def load_extended_bars(store: CandleStore) -> dict[str, SymbolBars]:
    out: dict[str, SymbolBars] = {}
    for sym in EXTENDED_UNIVERSE:
        sig, reg = await store.load_candles(sym, "4h"), await store.load_candles(sym, "1d")
        if sig and reg:
            out[sym] = SymbolBars(build_bars(sym, sig, reg), generic_filters(sym))
    return out


# ------------------------------------------------------------------ utilidades


def _data(
    bars: dict[str, SymbolBars], start: datetime = START, exclude: Sequence[str] = ()
) -> dict[str, SymbolData]:
    out = {}
    for s, sb in bars.items():
        if s in exclude:
            continue
        sel = [b for b in sb.bars if b.close_time >= start]
        if sel:
            out[s] = SymbolData(s, sel, sb.filters)
    return out


@dataclass
class Run:
    result: BacktestResult
    metrics: Metrics
    daily: list[tuple[date, float]]
    risk: RiskMetrics


def run_h1(
    app: AppConfig,
    data: dict[str, SymbolData],
    params: H1Params,
    fee: Decimal | None = None,
    slip: Decimal | None = None,
    delay: int = 0,
    strategy: Any = None,
) -> Run:
    bt = Backtester(
        data,
        app.risk,
        fee if fee is not None else app.strategy.costs.fee_rate,
        slip if slip is not None else app.strategy.costs.slippage_rate,
        market_delay_bars=delay,
    )
    first, last = bt.timeline[0], bt.timeline[-1]
    res = bt.run([ParamPeriod(first, strategy or H1Breakout(params))], first, last)
    m = compute_metrics(res.trades, res.equity, res.initial_capital)
    daily = daily_equity(res.equity, first.date(), last.date())
    return Run(res, m, daily, risk_metrics([v for _, v in daily]))


def _pf(trades: Sequence[Trade]) -> float:
    gp = sum((t.pnl for t in trades if t.pnl > 0), Decimal(0))
    gl = -sum((t.pnl for t in trades if t.pnl < 0), Decimal(0))
    return float(gp / gl) if gl > 0 else math.inf


class RandomEntry:
    """Control: entradas al azar con la misma frecuencia que H1 y sus mismas salidas."""

    name = "control_aleatorio"
    lookback = 1
    cancel_after_candles = 1

    def __init__(self, p: float, seed: int, require_regime: bool, base: H1Breakout):
        self.p, self.rng, self.require_regime, self.base = (
            p,
            random.Random(seed),
            require_regime,
            base,
        )

    def evaluate_entry(self, window: Sequence[Bar]) -> tuple[EntrySignal | None, str]:
        b, bp = window[-1], self.base.p
        if b.atr is None:
            return None, "indicadores_no_listos"
        if self.require_regime and not _regime_up(b, bp.regime_ema):
            return None, "regimen_no_alcista"
        if self.rng.random() >= self.p:
            return None, "sin_sorteo"
        stop = _stop(b.close, b.atr, bp.stop_atr, bp.stop_max_pct)
        return EntrySignal(b.symbol, b.close_time, b.close, b.atr, stop, "market", "H1"), "ok"

    def open_plan(self, *a: Any) -> Any:
        return self.base.open_plan(*a)

    def on_candle_close(self, plan: Any, bar: Any) -> Any:
        return self.base.on_candle_close(plan, bar)


def _signal_rate(data: dict[str, SymbolData], h1: H1Breakout, require_regime: bool) -> float:
    signals = eligible = 0
    for d in data.values():
        for i in range(1, len(d.snapshots)):
            b = d.snapshots[i]
            if b.atr is None or (require_regime and not _regime_up(b, h1.p.regime_ema)):
                continue
            eligible += 1
            signals += h1.evaluate_entry(d.snapshots[i - 1 : i + 1])[0] is not None
    return signals / eligible if eligible else 0.0


def buy_and_hold(bars: dict[str, SymbolBars], start: datetime, end: date) -> list[float]:
    """Partes iguales por par; la parte de un par aún no listado espera en caja hasta su listado.
    Valoración diaria con el último cierre de 4 h de cada día."""
    third = 200.0 / len(bars)
    closes = {s: {b.close_time.date(): b.close for b in sb.bars} for s, sb in bars.items()}
    units: dict[str, float] = {}
    last: dict[str, float] = {}
    cash = 200.0
    series = []
    d = start.date()
    while d <= end:
        for s in bars:
            px = closes[s].get(d)
            if px is None:
                continue
            last[s] = px
            if s not in units:
                units[s] = third / px
                cash -= third
        series.append(cash + sum(units[s] * last[s] for s in units))
        d = date.fromordinal(d.toordinal() + 1)
    return series


# ------------------------------------------------------------------ auditoría


@dataclass
class AuditResult:
    sections: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, tuple[bool, str]] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    @property
    def edge_demonstrated(self) -> bool:
        return all(ok for ok, _ in self.decision.values())


def run_audit(
    app: AppConfig,
    hyp: HypothesesConfig,
    bars: dict[str, SymbolBars],
    ext_bars: dict[str, SymbolBars],
    spreads: dict[str, float],
    out_dir: Path,
    progress: Progress = lambda _: None,
    n_boot: int = 5000,
    n_random: int = 1000,
) -> AuditResult:
    out = AuditResult()
    S = out.sections
    params = H1Params(**hyp.H1.defaults)  # type: ignore[arg-type]
    h1 = H1Breakout(params)
    data = _data(bars)
    base = run_h1(app, data, params)
    progress(f"H1-fijo: {base.metrics.trades} ops, PF {base.metrics.profit_factor:.2f}")
    trades = sorted(base.result.trades, key=lambda t: t.opened_at)
    S["base"] = base
    S["spreads"] = spreads

    # 1. Concentración
    by_pnl = sorted(trades, key=lambda t: t.pnl, reverse=True)
    total_r = sum(float(t.r_multiple) for t in trades)
    conc = {}
    for k in (1, 3, 5, 10):
        rest = by_pnl[k:]
        conc[k] = (
            float(sum(t.pnl for t in rest)),
            _pf(rest),
            total_r - sum(float(t.r_multiple) for t in by_pnl[:k]),
        )
    gross_win = sum(float(t.pnl) for t in trades if t.pnl > 0)
    share = {}
    for pct in (0.05, 0.10):
        k = max(1, round(len(trades) * pct))
        share[pct] = (
            k,
            sum(float(t.pnl) for t in by_pnl[:k]) / float(base.metrics.net_pnl)
            if base.metrics.net_pnl
            else math.nan,
            sum(float(t.pnl) for t in by_pnl[:k]) / gross_win if gross_win else math.nan,
        )
    S["concentration"] = (conc, share, total_r)
    progress("concentración lista")

    # 2. Costos
    costs = {}
    for label, fee, slip in [
        ("base 0,10% + 0,05%", "0.001", "0.0005"),
        ("BNB 0,075% + 0,05%", "0.00075", "0.0005"),
        ("0,10% + 0,10% (doble)", "0.001", "0.001"),
        ("0,10% + 0,20% (cuádruple)", "0.001", "0.002"),
    ]:
        r = run_h1(app, data, params, Decimal(fee), Decimal(slip))
        costs[label] = (r.metrics.trades, r.metrics.profit_factor, float(r.metrics.net_pnl))
    lo, hi = 0.0, 0.02
    for _ in range(12):
        mid = (lo + hi) / 2
        r = run_h1(app, data, params, slip=Decimal(repr(mid)))
        lo, hi = (mid, hi) if r.metrics.net_pnl > 0 else (lo, mid)
    S["costs"] = (costs, (lo + hi) / 2)
    progress("costos listos")

    # 3. Riesgo, exposición, distribución de pérdidas, comparación
    end_d = base.daily[-1][0]
    span = (datetime.combine(end_d, datetime.min.time(), UTC) - START).total_seconds()
    intervals = sorted((t.opened_at, t.closed_at) for t in trades)
    covered, cur_s, cur_e = 0.0, None, None
    for s_, e_ in intervals:
        if cur_e is None or s_ > cur_e:
            if cur_e is not None and cur_s is not None:
                covered += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s_, e_
        else:
            cur_e = max(cur_e, e_)
    if cur_s is not None and cur_e is not None:
        covered += (cur_e - cur_s).total_seconds()
    avg_equity = statistics.fmean(v for _, v in base.daily)
    exposure_avg = sum(
        float(t.qty * t.entry_price) * (t.closed_at - t.opened_at).total_seconds() for t in trades
    ) / (avg_equity * span)
    rs = [float(t.r_multiple) for t in trades]
    buckets = {
        "≤ −1R": sum(x <= -1 for x in rs),
        "(−1R, −0,5R]": sum(-1 < x <= -0.5 for x in rs),
        "(−0,5R, 0]": sum(-0.5 < x <= 0 for x in rs),
        "(0, 1R]": sum(0 < x <= 1 for x in rs),
        "(1R, 3R]": sum(1 < x <= 3 for x in rs),
        "> 3R": sum(x > 3 for x in rs),
    }
    streak = best = 0
    for t in trades:
        streak = streak + 1 if t.pnl < 0 else 0
        best = max(best, streak)
    bh = buy_and_hold(bars, START, end_d)
    bh_m = risk_metrics(bh)
    exp_r = [x * exposure_avg for x in returns(bh)]
    eq = [200.0]
    for x in exp_r:
        eq.append(eq[-1] * (1 + x))
    S["risk"] = {
        "h1": base.risk,
        "bh": bh_m,
        "bh_exposure": risk_metrics(eq),
        "time_in_market": covered / span,
        "avg_exposure": exposure_avg,
        "buckets": buckets,
        "max_losing_streak": best,
        "worst": sorted(trades, key=lambda t: t.pnl)[:5],
        "worst_month": min(base.metrics.by_month.items(), key=lambda kv: kv[1]),
        "kill_switch": base.result.kill_switch_at,
        "risk_events": base.result.risk_events,
    }
    progress("métricas de riesgo listas")

    # 4a. Bootstrap
    r_daily = returns([v for _, v in base.daily])
    boot = {}
    for blk in (5, 10, 20):
        sh, cg = bootstrap_sharpe(r_daily, blk, n_boot, seed=100 + blk)
        s_sorted, c_sorted = sorted(sh), sorted(cg)
        boot[blk] = (
            sum(x <= 0 for x in sh) / n_boot,
            (s_sorted[int(0.05 * n_boot)], s_sorted[int(0.95 * n_boot)]),
            (c_sorted[int(0.05 * n_boot)], c_sorted[int(0.95 * n_boot)]),
        )
    tb: TradeBootstrap = trade_block_bootstrap(
        rs, 5, n_boot, risk_pct=float(app.risk.risk_per_trade_pct) / 100
    )
    S["bootstrap"] = (boot, tb)
    progress("bootstrap listo")

    # 4b. Entradas aleatorias
    rand = {}
    for variant, req in (("con filtro de régimen", True), ("sin filtro", False)):
        p = _signal_rate(data, h1, req)
        pfs, nets = [], []
        for k in range(n_random):
            ctrl = RandomEntry(p, 10_000 + k, req, h1)
            r = run_h1(app, data, params, strategy=ctrl)
            pfs.append(r.metrics.profit_factor)
            nets.append(float(r.metrics.net_pnl))
        beat = sum(x >= base.metrics.profit_factor for x in pfs)
        rand[variant] = (
            p,
            (1 + beat) / (1 + n_random),
            statistics.median(pfs),
            sorted(pfs)[int(0.95 * n_random)],
            statistics.median(nets),
        )
        progress(f"aleatorio {variant}: p={rand[variant][1]:.3f}")
    S["random"] = rand

    # 4c. Retraso, pares, años, universo ampliado
    delayed = run_h1(app, data, params, delay=1)
    loo = {s: run_h1(app, _data(bars, exclude=[s]), params).metrics for s in bars}
    years: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        years[f"{t.opened_at:%Y}"].append(t)
    ext = run_h1(app, _data(ext_bars), params) if ext_bars else None
    S["robust"] = {
        "delayed": delayed.metrics,
        "loo": loo,
        "years": dict(sorted(years.items())),
        "ext": ext,
        "ext_symbols": sorted(ext_bars),
    }
    progress("retraso, pares, años y universo ampliado listos")

    # 4d. Sensibilidad (diagnóstico) y Sharpe deflactado
    grid = []
    sens_axes = ((50, 75, 100, 150, 200), (10, 15, 20, 30, 40, 60), (2.0, 2.5, 3.0, 3.5, 4.0, 5.0))
    for g_ema, g_n, g_k in itertools.product(*sens_axes):
        combo = replace(params, regime_ema=g_ema, breakout_n=g_n, trail_atr=g_k)
        grid.append((g_ema, g_n, g_k, run_h1(app, data, combo)))
    stops = {
        s: run_h1(app, data, replace(params, stop_atr=s)).metrics for s in (1.5, 2.0, 2.5, 3.0)
    }
    h2 = [
        run_h1(app, data, params, strategy=H2DipBuy(replace(H2Params(**hyp.H2.defaults), **c)))  # type: ignore[arg-type]
        for c in [
            dict(zip(hyp.H2.grid, v, strict=True)) for v in itertools.product(*hyp.H2.grid.values())
        ]
    ]
    per_period = [g[3].risk.sharpe / math.sqrt(365) for g in grid] + [
        r.risk.sharpe / math.sqrt(365) for r in h2
    ]
    var_sr = statistics.pvariance(per_period)
    dsr, sr_star = deflated_sharpe(r_daily, N_TRIALS, var_sr)
    S["sensitivity"] = (grid, stops)
    S["dsr"] = (dsr, sr_star, var_sr, len(per_period))
    progress("sensibilidad y Sharpe deflactado listos")

    # 4e. Walk-forward (comparación)
    wf = {}
    for stage in ("desarrollo", "confirmacion"):
        st = run_stage(stage, slice_stage(bars, hyp.stages[stage]), app, hyp, {"H1"}, progress)
        wf[stage] = st.results["H1"]
    S["wf"] = wf

    # 5. Truncado: el pasado no cambia si se elimina el futuro
    cuts = [
        datetime(2021, 6, 1, tzinfo=UTC),
        datetime(2023, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
    ]
    trunc = {}
    for c in cuts:
        cut_data = {
            s: SymbolData(s, [b for b in d.snapshots if b.close_time < c], d.filters)
            for s, d in data.items()
        }
        r = run_h1(app, cut_data, params)
        last_t = max(b.close_time for d in cut_data.values() for b in d.snapshots)
        a = [t for t in r.result.trades if t.closed_at < last_t]
        b_ = [t for t in base.result.trades if t.closed_at < last_t]
        trunc[c.date()] = (len(a), a == b_)
    S["truncation"] = trunc

    # Regla de decisión pre-registrada
    pf_grid = [g[3].metrics.profit_factor for g in grid]
    share_ok = sum(p > 1.0 for p in pf_grid) / len(pf_grid)
    c2 = costs["0,10% + 0,10% (doble)"]
    out.decision = {
        "A bootstrap P(Sharpe ≤ 0) < 5% (bloque 10)": (boot[10][0] < 0.05, f"{boot[10][0]:.3f}"),
        "B Sharpe deflactado > 95%": (dsr > 0.95, f"{dsr:.3f}"),
        "C supera al 95% de entradas aleatorias con régimen": (
            rand["con filtro de régimen"][1] < 0.05,
            f"p = {rand['con filtro de régimen'][1]:.3f}",
        ),
        "D deslizamiento doble: PF > 1 y neto > 0": (
            c2[1] > 1.0 and c2[2] > 0,
            f"PF {c2[1]:.2f}, neto {c2[2]:+.2f}",
        ),
        "E ≥ 70% de la rejilla con PF > 1": (share_ok >= 0.70, f"{share_ok:.0%}"),
        "F universo ampliado PF > 1": (
            ext is not None and ext.metrics.profit_factor > 1.0,
            f"PF {ext.metrics.profit_factor:.2f}" if ext else "sin datos",
        ),
        "G retraso de una vela PF > 1": (
            delayed.metrics.profit_factor > 1.0,
            f"PF {delayed.metrics.profit_factor:.2f}",
        ),
        "H Calmar ≥ comprar y mantener": (
            base.risk.calmar >= bh_m.calmar,
            f"{base.risk.calmar:.2f} vs {bh_m.calmar:.2f}",
        ),
    }

    # Archivos
    out_dir.mkdir(parents=True, exist_ok=True)
    tp = out_dir / "h1_fijo_trades.csv"
    with tp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "opened_at", "closed_at", "entry", "exit", "pnl", "r", "exits"])
        for t in trades:
            w.writerow(
                [
                    t.symbol,
                    t.opened_at.isoformat(),
                    t.closed_at.isoformat(),
                    round(t.entry_price, 8),
                    round(t.avg_exit_price, 8),
                    round(t.pnl, 6),
                    round(t.r_multiple, 4),
                    "|".join(t.exits),
                ]
            )
    ep = out_dir / "h1_fijo_equity_diaria.csv"
    with ep.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "h1_equity", "buy_and_hold"])
        for (d, v), b in zip(base.daily, bh, strict=False):
            w.writerow([d.isoformat(), round(v, 4), round(b, 4)])
    gp = out_dir / "h1_sensibilidad.csv"
    with gp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["regime_ema", "breakout_n", "trail_atr", "trades", "pf", "net", "sharpe", "max_dd"]
        )
        for g_ema, g_n, g_k, r in grid:
            w.writerow(
                [
                    g_ema,
                    g_n,
                    g_k,
                    r.metrics.trades,
                    round(r.metrics.profit_factor, 3),
                    round(float(r.metrics.net_pnl), 2),
                    round(r.risk.sharpe, 3),
                    round(r.risk.max_dd, 4),
                ]
            )
    out.files = {"trades": tp, "equity": ep, "sensitivity": gp}
    return out
