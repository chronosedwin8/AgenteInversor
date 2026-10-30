"""Hipótesis de la Fase 2b (ver docs/fase2b-protocolo.md, pre-registrado antes de probarlas).

H1: ruptura de 4 h a favor de la tendencia diaria (momentum).
H2: compra de sobreventa de 4 h dentro de la tendencia diaria (reversión).
H3: H1 + H2 en un mismo sistema.
Entradas a mercado en la apertura de la vela siguiente; sin cierre intradía obligatorio.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from bot.strategy.base import Strategy
from bot.strategy.features import Bar
from bot.strategy.rules import EntrySignal, ExitReason, PositionPlan

INF = float("inf")


def _regime_up(bar: Bar, ema_period: int) -> bool | None:
    ema = bar.regime_ema.get(ema_period)
    if bar.regime_close is None or ema is None:
        return None
    return bar.regime_close > ema


def _stop(entry: float, atr: float, multiple: float, max_pct: float) -> float:
    return max(entry - multiple * atr, entry * (1 - max_pct / 100))


def _plan(entry: float, stop: float, filled_at: datetime, tag: str) -> PositionPlan:
    if stop >= entry:
        raise ValueError("el stop debe estar por debajo de la entrada")
    # Sin toma parcial: objetivo infinito; breakeven sin uso.
    return PositionPlan(entry, stop, stop, INF, entry, filled_at, entry, tag=tag)


# ------------------------------------------------------------------ H1


@dataclass(frozen=True, slots=True)
class H1Params:
    regime_ema: int = 100
    breakout_n: int = 20
    stop_atr: float = 2.0
    stop_max_pct: float = 10.0
    trail_atr: float = 3.0


class H1Breakout:
    name = "H1_ruptura_4h"
    lookback = 1
    cancel_after_candles = 1

    def __init__(self, p: H1Params | None = None):
        self.p = p or H1Params()

    def evaluate_entry(self, window: Sequence[Bar]) -> tuple[EntrySignal | None, str]:
        p, b = self.p, window[-1]
        up = _regime_up(b, p.regime_ema)
        level = b.donchian.get(p.breakout_n)
        if up is None or level is None or b.atr is None:
            return None, "indicadores_no_listos"
        if not up:
            return None, "regimen_no_alcista"
        if b.close <= level:
            return None, "sin_ruptura"
        stop = _stop(b.close, b.atr, p.stop_atr, p.stop_max_pct)
        return EntrySignal(b.symbol, b.close_time, b.close, b.atr, stop, "market", "H1"), "ok"

    def open_plan(self, signal: EntrySignal, entry: float, stop: float, fee_rate: float,
                  filled_at: datetime) -> PositionPlan:  # fmt: skip
        return _plan(entry, stop, filled_at, "H1")

    def on_candle_close(self, plan: PositionPlan, bar: Bar) -> ExitReason | None:
        plan.candles_held += 1
        plan.max_high = max(plan.max_high, bar.high)
        if bar.atr is not None:
            trail = plan.max_high - self.p.trail_atr * bar.atr
            if trail > plan.stop:
                plan.stop = trail
                plan.stop_kind = ExitReason.TRAILING_STOP
        return None


# ------------------------------------------------------------------ H2


@dataclass(frozen=True, slots=True)
class H2Params:
    regime_ema: int = 100
    rsi_below: float = 20.0
    stop_atr: float = 2.5
    stop_max_pct: float = 10.0
    exit_ema: int = 5
    max_bars: int = 12


class H2DipBuy:
    name = "H2_sobreventa_4h"
    lookback = 1
    cancel_after_candles = 1

    def __init__(self, p: H2Params | None = None):
        self.p = p or H2Params()

    def evaluate_entry(self, window: Sequence[Bar]) -> tuple[EntrySignal | None, str]:
        p, b = self.p, window[-1]
        up = _regime_up(b, p.regime_ema)
        if up is None or b.rsi3 is None or b.atr is None:
            return None, "indicadores_no_listos"
        if not up:
            return None, "regimen_no_alcista"
        if b.rsi3 >= p.rsi_below:
            return None, "sin_sobreventa"
        stop = _stop(b.close, b.atr, p.stop_atr, p.stop_max_pct)
        return EntrySignal(b.symbol, b.close_time, b.close, b.atr, stop, "market", "H2"), "ok"

    def open_plan(self, signal: EntrySignal, entry: float, stop: float, fee_rate: float,
                  filled_at: datetime) -> PositionPlan:  # fmt: skip
        return _plan(entry, stop, filled_at, "H2")

    def on_candle_close(self, plan: PositionPlan, bar: Bar) -> ExitReason | None:
        plan.candles_held += 1
        plan.max_high = max(plan.max_high, bar.high)
        ema = bar.ema.get(self.p.exit_ema)
        if ema is not None and bar.close > ema:
            return ExitReason.MEAN_REVERTED
        if plan.candles_held >= self.p.max_bars:
            return ExitReason.TIME_STOP
        return None


# ------------------------------------------------------------------ H3


class H3Combined:
    """Evalúa H1 y, si no hay señal, H2. Cada posición sale con las reglas de quien la abrió."""

    name = "H3_combinada"
    lookback = 1
    cancel_after_candles = 1

    def __init__(self, h1: H1Breakout, h2: H2DipBuy):
        self.parts: dict[str, Strategy] = {"H1": h1, "H2": h2}

    def evaluate_entry(self, window: Sequence[Bar]) -> tuple[EntrySignal | None, str]:
        sig, why1 = self.parts["H1"].evaluate_entry(window)
        if sig is not None:
            return sig, why1
        sig, why2 = self.parts["H2"].evaluate_entry(window)
        return sig, why2 if sig is not None else f"{why1}|{why2}"

    def open_plan(self, signal: EntrySignal, entry: float, stop: float, fee_rate: float,
                  filled_at: datetime) -> PositionPlan:  # fmt: skip
        return self.parts[signal.tag].open_plan(signal, entry, stop, fee_rate, filled_at)

    def on_candle_close(self, plan: PositionPlan, bar: Bar) -> ExitReason | None:
        return self.parts[plan.tag].on_candle_close(plan, bar)
