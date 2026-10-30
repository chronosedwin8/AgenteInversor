"""Comparación controlada de modelos y análisis por régimen (docs/fase-testnet-plan.md §2–3).

Diagnóstico: no modifica H1 ni elige parámetros.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from bot.audit.h1 import START, Run, _data, buy_and_hold, run_h1
from bot.audit.stats import RiskMetrics, returns, risk_metrics
from bot.backtest.engine import SymbolData
from bot.backtest.research import SymbolBars
from bot.config import AppConfig
from bot.data.klines import Candle
from bot.portfolio.portfolio import Trade
from bot.strategy.features import Bar
from bot.strategy.hypotheses import H1Breakout, H1Params, _regime_up, _stop
from bot.strategy.indicators import ATR, EMA
from bot.strategy.rules import EntrySignal

Progress = Callable[[str], None]
FEE, SLIP = 0.001, 0.0005


# ------------------------------------------------------------------ estrategias de control


class _H1Exits:
    """Salidas y stop idénticos a H1 (se delega en H1Breakout)."""

    lookback = 1
    cancel_after_candles = 1

    def __init__(self, base: H1Breakout):
        self.base = base

    def _signal(self, b: Bar) -> EntrySignal:
        p = self.base.p
        if b.atr is None:
            raise ValueError("vela sin ATR")
        stop = _stop(b.close, b.atr, p.stop_atr, p.stop_max_pct)
        return EntrySignal(b.symbol, b.close_time, b.close, b.atr, stop, "market", "H1")

    def open_plan(self, *a: Any) -> Any:
        return self.base.open_plan(*a)

    def on_candle_close(self, plan: Any, bar: Any) -> Any:
        return self.base.on_candle_close(plan, bar)


def _eligible(b: Bar, base: H1Breakout) -> bool:
    return b.atr is not None and bool(_regime_up(b, base.p.regime_ema))


class AlwaysInTrend(_H1Exits):
    """Modelo C: entra en cada vela con régimen alcista si no hay posición."""

    name = "C_entrada_inmediata"

    def evaluate_entry(self, window: Sequence[Bar]) -> tuple[EntrySignal | None, str]:
        b = window[-1]
        return (self._signal(b), "ok") if _eligible(b, self.base) else (None, "no")


class SignalSet(_H1Exits):
    """Entra en un conjunto fijo de (par, hora de cierre): señales desplazadas (modelo A2)."""

    name = "A2_desplazamiento_circular"

    def __init__(self, base: H1Breakout, signals: set[tuple[str, datetime]]):
        super().__init__(base)
        self.signals = signals

    def evaluate_entry(self, window: Sequence[Bar]) -> tuple[EntrySignal | None, str]:
        b = window[-1]
        if (b.symbol, b.close_time) in self.signals and _eligible(b, self.base):
            return self._signal(b), "ok"
        return None, "no"


def shifted_signals(data: dict[str, SymbolData], base: H1Breakout, rng: random.Random
                    ) -> set[tuple[str, datetime]]:  # fmt: skip
    """Desplaza circularmente la secuencia de señales de B dentro de las velas elegibles de cada
    par: conserva número y agrupamiento de señales, rompe su relación con el precio."""
    out: set[tuple[str, datetime]] = set()
    for sym, d in data.items():
        bars = d.snapshots
        elig = [i for i in range(1, len(bars)) if _eligible(bars[i], base)]
        flags = [base.evaluate_entry(bars[i - 1 : i + 1])[0] is not None for i in elig]
        if len(elig) < 2:
            continue
        k = rng.randrange(1, len(elig))
        for j, i in enumerate(elig):
            if flags[(j - k) % len(elig)]:
                out.add((sym, bars[i].close_time))
    return out


# ------------------------------------------------------------------ D1: régimen puro


def regime_only(
    bars: dict[str, SymbolBars], base: H1Breakout, end: date
) -> tuple[list[float], float, float]:
    """1/3 del capital por par, invertido mientras el régimen es alcista (al cierre de 4 h).
    Devuelve (capital diario, tiempo medio en mercado, exposición media)."""
    sleeve = 200.0 / len(bars)
    per_day: dict[date, float] = defaultdict(float)
    in_mkt_share, exposure = [], []
    for _sym, sb in bars.items():
        cash, units, in_count, total = sleeve, 0.0, 0, 0
        values: dict[date, float] = {}
        for b in sb.bars:
            if b.close_time < START:
                continue
            up = _eligible(b, base)
            if up and units == 0:
                units = cash * (1 - FEE) / (b.close * (1 + SLIP))
                cash = 0.0
            elif not up and units > 0:
                cash = units * b.close * (1 - SLIP) * (1 - FEE)
                units = 0.0
            total += 1
            in_count += units > 0
            values[b.close_time.date()] = cash + units * b.close
        in_mkt_share.append(in_count / max(total, 1))
        exposure.append(in_count / max(total, 1))
        last = sleeve
        d = START.date()
        while d <= end:
            last = values.get(d, last)
            per_day[d] += last
            d += timedelta(days=1)
    series = [per_day[d] for d in sorted(per_day)]
    return series, statistics.fmean(in_mkt_share), statistics.fmean(exposure)


# ------------------------------------------------------------------ métricas por modelo


@dataclass
class ModelRow:
    name: str
    trades: int
    net: float
    pf: float
    risk: RiskMetrics
    time_in_market: float
    avg_exposure: float
    return_on_exposed: float  # anual, sobre capital medio invertido
    r_q: tuple[float, float, float]
    without_top10: float
    kill_switch_peak: datetime | None


def _exposure(trades: Sequence[Trade], start: datetime, end: datetime, avg_equity: float
              ) -> tuple[float, float, float]:  # fmt: skip
    span = (end - start).total_seconds()
    ivals = sorted((t.opened_at, t.closed_at) for t in trades)
    covered, cs, ce = 0.0, None, None
    for s, e in ivals:
        if ce is None or s > ce:
            if cs is not None and ce is not None:
                covered += (ce - cs).total_seconds()
            cs, ce = s, e
        else:
            ce = max(ce, e)
    if cs is not None and ce is not None:
        covered += (ce - cs).total_seconds()
    cap_time = sum(float(t.qty * t.entry_price) * (t.closed_at - t.opened_at).total_seconds()
                   for t in trades)  # fmt: skip
    avg_invested = cap_time / span if span else 0.0
    net = sum(float(t.pnl) for t in trades)
    years = span / (365 * 86400)
    roe = (net / avg_invested) / years if avg_invested and years else 0.0
    return covered / span if span else 0.0, avg_invested / avg_equity if avg_equity else 0.0, roe


def _peak_kill(equity: Sequence[tuple[datetime, Any]]) -> datetime | None:
    peak = 200.0
    for t, v in equity:
        v = float(v)
        peak = max(peak, v)
        if v <= 0.9 * peak:
            return t
    return None


def model_row(name: str, run: Run) -> ModelRow:
    trades = run.result.trades
    first, last = run.result.equity[0][0], run.result.equity[-1][0]
    avg_eq = statistics.fmean(v for _, v in run.daily)
    tim, expo, roe = _exposure(trades, first, last, avg_eq)
    rs = sorted(float(t.r_multiple) for t in trades)
    q = (rs[int(0.1 * len(rs))], rs[len(rs) // 2], rs[int(0.9 * len(rs))]) if rs else (0, 0, 0)
    by = sorted(trades, key=lambda t: t.pnl, reverse=True)
    return ModelRow(
        name, len(trades), float(run.metrics.net_pnl), run.metrics.profit_factor, run.risk, tim,
        expo, roe, q, float(sum(t.pnl for t in by[10:])), _peak_kill(run.result.equity),
    )  # fmt: skip


@dataclass
class Distribution:
    rows: list[ModelRow]

    def stat(self, f: Callable[[ModelRow], float]) -> tuple[float, float, float, float, float]:
        xs = sorted(f(r) for r in self.rows)
        n = len(xs)
        return (xs[int(0.05 * n)], statistics.median(xs), xs[int(0.95 * n)],
                statistics.fmean(xs), statistics.pstdev(xs))  # fmt: skip


@dataclass
class ModelComparison:
    b: ModelRow
    c: ModelRow
    a1: Distribution
    a2: Distribution
    d1: tuple[RiskMetrics, float, float, float]  # métricas, tiempo, exposición, neto
    d2: tuple[RiskMetrics, float]
    n_sims: int


def compare_models(app: AppConfig, h1: H1Params, bars: dict[str, SymbolBars], n_sims: int,
                   progress: Progress = lambda _: None) -> ModelComparison:  # fmt: skip
    from bot.audit.h1 import RandomEntry, _signal_rate

    app_i = app.model_copy(update={"risk": app.risk.model_copy(
        update={"kill_switch_basis": "initial"})})  # fmt: skip
    base = H1Breakout(h1)
    data = _data(bars)
    b_run = run_h1(app_i, data, h1)
    b = model_row("B H1 (ruptura)", b_run)
    c = model_row("C entrada inmediata", run_h1(app_i, data, h1, strategy=AlwaysInTrend(base)))
    progress("B y C listos")
    p = _signal_rate(data, base, True)
    a1 = [model_row("A1", run_h1(app_i, data, h1, strategy=RandomEntry(p, 20_000 + k, True, base)))
          for k in range(n_sims)]  # fmt: skip
    progress("A1 listo")
    a2 = []
    for k in range(n_sims):
        sigs = shifted_signals(data, base, random.Random(30_000 + k))
        a2.append(model_row("A2", run_h1(app_i, data, h1, strategy=SignalSet(base, sigs))))
    progress("A2 listo")
    end = b_run.daily[-1][0]
    d1_series, tim, expo = regime_only(bars, base, end)
    d1 = (risk_metrics(d1_series), tim, expo, d1_series[-1] - 200.0)
    bh = buy_and_hold(bars, START, end)
    eq = [200.0]
    for x in returns(bh):
        eq.append(eq[-1] * (1 + x * b.avg_exposure))
    d2 = (risk_metrics(eq), eq[-1] - 200.0)
    return ModelComparison(b, c, Distribution(a1), Distribution(a2), d1, d2, n_sims)


# ------------------------------------------------------------------ régimen


def _daily_labels(daily: Sequence[Candle]) -> dict[datetime, tuple[str, float]]:
    """close_time diario → (tendencia, ATR% diario) según la regla pre-registrada."""
    e50, e200, atr = EMA(50), EMA(200), ATR(14)
    hist50: list[float | None] = []
    out = {}
    for c in daily:
        close, high, low = float(c.close), float(c.high), float(c.low)
        v50, v200, a = e50.update(close), e200.update(close), atr.update(high, low, close)
        hist50.append(v50)
        prev = hist50[-21] if len(hist50) > 20 else None
        if v50 is None or v200 is None or prev is None or a is None:
            continue
        if close > v200 and v50 > prev:
            trend = "alcista"
        elif close < v200 and v50 < prev:
            trend = "bajista"
        else:
            trend = "lateral"
        out[c.close_time] = (trend, a / close * 100)
    return out


@dataclass
class RegimeTable:
    rows: dict[str, dict[str, tuple[int, float, float, float]]]  # dimensión → etiqueta → stats


def _stats(ts: Sequence[Trade]) -> tuple[int, float, float, float]:
    gp = sum(float(t.pnl) for t in ts if t.pnl > 0)
    gl = -sum(float(t.pnl) for t in ts if t.pnl < 0)
    pf = gp / gl if gl > 0 else math.inf
    avg_r = statistics.fmean(float(t.r_multiple) for t in ts) if ts else 0.0
    return len(ts), sum(float(t.pnl) for t in ts), pf, avg_r


def regime_analysis(trades: Sequence[Trade], daily: dict[str, list[Candle]]) -> RegimeTable:
    labels = {s: _daily_labels(c) for s, c in daily.items()}
    medians = {s: statistics.median(v[1] for v in lab.values()) for s, lab in labels.items()}
    keys = {s: sorted(lab) for s, lab in labels.items()}

    def at(sym: str, t: datetime) -> tuple[str, float] | None:
        ks = keys[sym]
        lo, hi = 0, len(ks)
        while lo < hi:  # última diaria cerrada antes de la vela de señal (t − 4 h)
            mid = (lo + hi) // 2
            if ks[mid] <= t - timedelta(hours=4):
                lo = mid + 1
            else:
                hi = mid
        return labels[sym][ks[lo - 1]] if lo else None

    groups: dict[str, dict[str, list[Trade]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        own, mkt = at(t.symbol, t.opened_at), at("BTCUSDT", t.opened_at)
        if own is None or mkt is None:
            continue
        vol = "alta" if own[1] > medians[t.symbol] else "baja"
        year = f"{t.opened_at:%Y}"
        groups["tendencia del activo"][own[0]].append(t)
        groups["tendencia del mercado (BTC)"][mkt[0]].append(t)
        groups["volatilidad"][vol].append(t)
        groups["año × tendencia del activo"][f"{year} {own[0]}"].append(t)
        groups["año × volatilidad"][f"{year} {vol}"].append(t)
    return RegimeTable({dim: {k: _stats(v) for k, v in sorted(g.items())}
                        for dim, g in groups.items()})  # fmt: skip
