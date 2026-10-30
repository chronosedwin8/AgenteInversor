"""Estadística de la auditoría: métricas diarias, bootstrap y Sharpe deflactado.

Python puro y semillas fijas: los resultados son reproducibles bit a bit.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

DAYS_PER_YEAR = 365
EULER_GAMMA = 0.5772156649015329
_N = statistics.NormalDist()


# ------------------------------------------------------------------ series diarias


def daily_equity(
    points: Sequence[tuple[datetime, Decimal]], start: date, end: date
) -> list[tuple[date, float]]:
    """Capital al cierre (00:00 UTC del día siguiente) de cada día, arrastrando el último valor."""
    pts = sorted(points, key=lambda p: p[0])
    out: list[tuple[date, float]] = []
    i, last = 0, float(pts[0][1]) if pts else 0.0
    d = start
    while d <= end:
        cutoff = datetime.combine(d + timedelta(days=1), time(0), tzinfo=UTC)
        while i < len(pts) and pts[i][0] < cutoff:
            last = float(pts[i][1])
            i += 1
        out.append((d, last))
        d += timedelta(days=1)
    return out


def returns(series: Sequence[float]) -> list[float]:
    return [b / a - 1 for a, b in itertools.pairwise(series) if a > 0]


@dataclass(frozen=True)
class RiskMetrics:
    days: int
    cagr: float
    vol: float
    sharpe: float
    sortino: float
    max_dd: float
    calmar: float
    max_dd_days: int
    skew: float
    kurtosis: float  # no excedente (normal = 3)


def _moments(r: Sequence[float]) -> tuple[float, float]:
    n = len(r)
    if n < 3:
        return 0.0, 3.0
    m = statistics.fmean(r)
    sd = statistics.pstdev(r)
    if sd == 0:
        return 0.0, 3.0
    skew = sum(((x - m) / sd) ** 3 for x in r) / n
    kurt = sum(((x - m) / sd) ** 4 for x in r) / n
    return skew, kurt


def sharpe(r: Sequence[float]) -> float:
    if len(r) < 2:
        return 0.0
    sd = statistics.stdev(r)
    return statistics.fmean(r) / sd * math.sqrt(DAYS_PER_YEAR) if sd > 0 else 0.0


def max_drawdown(series: Sequence[float]) -> tuple[float, int]:
    peak, peak_i, worst, longest = -math.inf, 0, 0.0, 0
    for i, v in enumerate(series):
        if v >= peak:
            peak, peak_i = v, i
        else:
            worst = max(worst, (peak - v) / peak)
            longest = max(longest, i - peak_i)
    return worst, longest


def risk_metrics(series: Sequence[float]) -> RiskMetrics:
    r = returns(series)
    years = max(len(series) - 1, 1) / DAYS_PER_YEAR
    cagr = (series[-1] / series[0]) ** (1 / years) - 1 if series[0] > 0 else 0.0
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in r) / len(r)) if r else 0.0
    sortino = statistics.fmean(r) / downside * math.sqrt(DAYS_PER_YEAR) if downside > 0 else 0.0
    dd, dd_days = max_drawdown(series)
    skew, kurt = _moments(r)
    return RiskMetrics(
        days=len(series),
        cagr=cagr,
        vol=statistics.stdev(r) * math.sqrt(DAYS_PER_YEAR) if len(r) > 1 else 0.0,
        sharpe=sharpe(r),
        sortino=sortino,
        max_dd=dd,
        calmar=cagr / dd if dd > 0 else math.inf,
        max_dd_days=dd_days,
        skew=skew,
        kurtosis=kurt,
    )


# ------------------------------------------------------------------ bootstrap


def stationary_bootstrap_indices(n: int, mean_block: float, rng: random.Random) -> list[int]:
    """Politis & Romano (1994): bloques de longitud geométrica con media `mean_block`."""
    p = 1.0 / mean_block
    out: list[int] = []
    i = rng.randrange(n)
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def bootstrap_sharpe(
    r: Sequence[float], mean_block: float, n_boot: int, seed: int = 7
) -> tuple[list[float], list[float]]:
    """Distribución de Sharpe anualizado y CAGR por bootstrap estacionario."""
    rng = random.Random(seed)
    sharpes, cagrs = [], []
    n = len(r)
    for _ in range(n_boot):
        idx = stationary_bootstrap_indices(n, mean_block, rng)
        sample = [r[i] for i in idx]
        sharpes.append(sharpe(sample))
        growth = math.prod(1 + x for x in sample)
        cagrs.append(growth ** (DAYS_PER_YEAR / n) - 1)
    return sharpes, cagrs


@dataclass(frozen=True)
class TradeBootstrap:
    p_mean_r_le_0: float
    pf_quantiles: dict[str, float]
    p_pf_below_1: float
    p_pf_below_1_3: float
    p_dd_above_8: float
    p_kill_switch: float
    dd_quantiles: dict[str, float]


def _pf(rs: Sequence[float]) -> float:
    gp = sum(x for x in rs if x > 0)
    gl = -sum(x for x in rs if x < 0)
    return gp / gl if gl > 0 else math.inf


def trade_block_bootstrap(
    r_multiples: Sequence[float],
    block: int,
    n_boot: int,
    risk_pct: float = 0.005,
    capital: float = 200.0,
    kill_capital: float = 180.0,
    seed: int = 11,
) -> TradeBootstrap:
    """Bootstrap circular por bloques de la secuencia de operaciones (en R). Cada remuestreo se
    convierte en una curva de capital arriesgando `risk_pct` del capital por operación."""
    rng = random.Random(seed)
    n = len(r_multiples)
    means, pfs, dds, kills = [], [], [], 0
    for _ in range(n_boot):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(r_multiples[(start + k) % n] for k in range(block))
        sample = sample[:n]
        means.append(statistics.fmean(sample))
        pfs.append(_pf(sample))
        eq, peak, worst, killed = capital, capital, 0.0, False
        for x in sample:
            eq += eq * risk_pct * x
            peak = max(peak, eq)
            worst = max(worst, (peak - eq) / peak)
            killed = killed or eq <= kill_capital
        dds.append(worst)
        kills += killed

    def q(xs: list[float], p: float) -> float:
        s = sorted(xs)
        return s[min(int(p * len(s)), len(s) - 1)]

    return TradeBootstrap(
        p_mean_r_le_0=sum(m <= 0 for m in means) / n_boot,
        pf_quantiles={k: q(pfs, v) for k, v in {"p05": 0.05, "p50": 0.5, "p95": 0.95}.items()},
        p_pf_below_1=sum(p < 1 for p in pfs) / n_boot,
        p_pf_below_1_3=sum(p < 1.3 for p in pfs) / n_boot,
        p_dd_above_8=sum(d > 0.08 for d in dds) / n_boot,
        p_kill_switch=kills / n_boot,
        dd_quantiles={k: q(dds, v) for k, v in {"p50": 0.5, "p95": 0.95, "p99": 0.99}.items()},
    )


# ------------------------------------------------------------------ Sharpe deflactado


def probabilistic_sharpe(sr: float, sr_benchmark: float, n: int, skew: float, kurt: float) -> float:
    """PSR (Bailey & López de Prado). `sr`, `sr_benchmark` por periodo (no anualizados)."""
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr**2, 1e-12))
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / denom)


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Sharpe máximo esperado entre `n_trials` estrategias sin ventaja (por periodo)."""
    return math.sqrt(var_sr) * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(r: Sequence[float], n_trials: int, var_sr: float) -> tuple[float, float]:
    """(DSR, umbral SR*) con `var_sr` = varianza de los Sharpe por periodo de las pruebas."""
    sd = statistics.stdev(r)
    sr = statistics.fmean(r) / sd if sd > 0 else 0.0
    skew, kurt = _moments(r)
    sr_star = expected_max_sharpe(n_trials, var_sr)
    return probabilistic_sharpe(sr, sr_star, len(r), skew, kurt), sr_star
