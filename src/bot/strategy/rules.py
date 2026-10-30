"""Reglas de la estrategia (PLAN.md): seguir la tendencia comprando retrocesos.

Funciones puras sobre `Snapshot`: no envían órdenes, no conocen el modo (backtest/paper/live).

Interpretaciones de puntos ambiguos del plan (decididas antes de ver resultados):
- "Últimas 3 velas" incluye la vela gatillo y las 2 anteriores.
- Toque de EMA20: mínimo ≤ EMA20 + tolerancia×ATR. Ninguna de esas 3 velas cierra bajo la EMA50.
- RSI(14) entre 40 y 60 se evalúa en la vela gatillo (no perseguir sobrecompra).
- Volumen ≥ multiplicador × media de las 20 velas ANTERIORES (sin la gatillo).
- Histograma MACD subiendo: hist(gatillo) > hist(anterior).
- Precio de entrada: LIMIT_MAKER al cierre de la vela gatillo (aprox. del mejor bid).
- Trailing 2×ATR y salida por cierre bajo EMA20 aplican al RESTO, es decir tras la toma parcial.
  Trailing = máximo alcanzado desde la entrada − 2×ATR actual; el stop solo sube.
- Stop de tiempo: al cierre de la 8.ª vela desde el llenado, si nunca tocó +0,5R y no hubo
  toma parcial, se cierra.
- Stop a "entrada + comisiones" tras la parcial: precio que deja P&L neto 0 en el resto.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from bot.config import Settings, StrategyConfig
from bot.strategy.snapshot import Snapshot


@dataclass(frozen=True, slots=True)
class RuleParams:
    adx_min: float
    lookback: int
    touch_atr_tolerance: float
    rsi_min: float
    rsi_max: float
    volume_multiplier: float
    atr_min_pct: float
    atr_max_pct: float
    stop_atr_multiple: float
    stop_max_pct: float
    partial_take_r: float
    partial_fraction: float
    trailing_atr_multiple: float
    time_stop_candles: int
    time_stop_min_r: float
    max_holding: timedelta
    cancel_after_candles: int
    entry_window: tuple[time, time] | None
    tz: ZoneInfo

    @classmethod
    def from_config(cls, s: StrategyConfig, settings: Settings) -> RuleParams:
        w = settings.universe.crypto.entry_window_local
        return cls(
            adx_min=s.regime.adx_min,
            lookback=s.pullback.lookback_candles,
            touch_atr_tolerance=s.pullback.touch_atr_tolerance,
            rsi_min=s.pullback.rsi_min,
            rsi_max=s.pullback.rsi_max,
            volume_multiplier=s.trigger.volume_multiplier,
            atr_min_pct=s.quality.atr_min_pct,
            atr_max_pct=s.quality.atr_max_pct,
            stop_atr_multiple=s.exit.stop_atr_multiple,
            stop_max_pct=s.exit.stop_max_pct,
            partial_take_r=s.exit.partial_take_r,
            partial_fraction=s.exit.partial_fraction,
            trailing_atr_multiple=s.exit.trailing_atr_multiple,
            time_stop_candles=s.exit.time_stop_candles,
            time_stop_min_r=s.exit.time_stop_min_r,
            max_holding=timedelta(hours=s.exit.max_holding_hours),
            cancel_after_candles=s.entry.cancel_after_candles,
            entry_window=(w.start, w.end) if s.entry.enforce_entry_window else None,
            tz=settings.tz,
        )


# ------------------------------------------------------------------ entrada


@dataclass(frozen=True, slots=True)
class EntrySignal:
    symbol: str
    time: datetime  # close_time de la vela gatillo
    limit_price: float
    atr: float
    stop: float

    @property
    def risk_per_unit(self) -> float:
        return self.limit_price - self.stop


def initial_stop(entry: float, atr: float, p: RuleParams) -> float:
    """entrada − 1,5×ATR, con distancia máxima del 3%."""
    return max(entry - p.stop_atr_multiple * atr, entry * (1 - p.stop_max_pct / 100))


def in_entry_window(t: datetime, p: RuleParams) -> bool:
    if p.entry_window is None:
        return True
    local = t.astimezone(p.tz).time()
    start, end = p.entry_window
    return start <= local < end


def evaluate_entry(window: Sequence[Snapshot], p: RuleParams) -> tuple[EntrySignal | None, str]:
    """`window`: snapshots hasta la vela gatillo inclusive (mínimo lookback+1).

    Devuelve (señal, "ok") o (None, regla que falló). No verifica posiciones abiertas, spread,
    noticias ni IA: eso lo hacen el motor y el RiskManager.
    """
    if len(window) < p.lookback + 1:
        return None, "historia_insuficiente"
    cur = window[-1]
    prev = window[-2]
    if not (cur.ready and prev.ready):
        return None, "indicadores_no_listos"
    r = cur.regime
    if r is None or r.ema_fast is None or r.ema_slow is None or r.adx is None:
        return None, "indicadores_no_listos"
    atr, rsi, ema20 = cur.atr or 0.0, cur.rsi or 0.0, cur.ema_touch or 0.0

    # 1. Régimen alcista (1 h)
    if not (r.close > r.ema_slow and r.ema_fast > r.ema_slow):
        return None, "regimen_no_alcista"
    if r.adx < p.adx_min:
        return None, "adx_bajo"

    # 2. Retroceso (15 min)
    recent = window[-p.lookback :]
    touched = False
    for s in recent:
        if s.ema_touch is None or s.ema_floor is None or s.atr is None:
            return None, "indicadores_no_listos"
        if s.close < s.ema_floor:
            return None, "cierre_bajo_ema50"
        if s.low <= s.ema_touch + p.touch_atr_tolerance * s.atr:
            touched = True
    if not touched:
        return None, "sin_toque_ema20"
    if not (p.rsi_min <= rsi <= p.rsi_max):
        return None, "rsi_fuera_de_rango"

    # 3. Gatillo (15 min)
    if not (cur.close > prev.high and cur.close > ema20):
        return None, "sin_ruptura"
    if cur.volume < p.volume_multiplier * (cur.volume_ma_prev or 0.0):
        return None, "volumen_bajo"
    if not ((cur.macd_hist or 0.0) > (prev.macd_hist or 0.0)):
        return None, "macd_no_sube"

    # 4. Calidad
    atr_pct = atr / cur.close * 100
    if not (p.atr_min_pct <= atr_pct <= p.atr_max_pct):
        return None, "atr_fuera_de_rango"
    if not in_entry_window(cur.close_time, p):
        return None, "fuera_de_horario"

    entry = cur.close
    return EntrySignal(cur.symbol, cur.close_time, entry, atr, initial_stop(entry, atr, p)), "ok"


# ------------------------------------------------------------------ salida


class ExitReason(StrEnum):
    STOP = "stop"
    BREAKEVEN_STOP = "stop_breakeven"
    TRAILING_STOP = "stop_trailing"
    CLOSE_BELOW_EMA = "cierre_bajo_ema20"
    TIME_STOP = "stop_tiempo"
    MAX_HOLDING = "tiempo_maximo"
    END_OF_DATA = "fin_de_datos"


@dataclass
class PositionPlan:
    """Estado de gestión de una posición abierta (lo que decide la estrategia)."""

    entry_price: float
    initial_stop: float
    stop: float
    target: float  # precio de la toma parcial
    breakeven: float  # stop tras la parcial (entrada + comisiones)
    filled_at: datetime  # close_time de la vela en que se llenó
    max_high: float
    candles_held: int = 0
    partial_done: bool = False
    reached_min_r: bool = False
    stop_kind: ExitReason = ExitReason.STOP

    @property
    def r(self) -> float:
        return self.entry_price - self.initial_stop

    @classmethod
    def open(cls, entry: float, stop: float, fee_rate: float, filled_at: datetime,
             p: RuleParams) -> PositionPlan:  # fmt: skip
        r = entry - stop
        if r <= 0:
            raise ValueError("el stop debe estar por debajo de la entrada")
        breakeven = entry * (1 + fee_rate) / (1 - fee_rate)
        return cls(entry, stop, stop, entry + p.partial_take_r * r, breakeven, filled_at, entry)


def on_partial_filled(plan: PositionPlan) -> None:
    """Tras vender la parte parcial: el stop sube a entrada + comisiones."""
    plan.partial_done = True
    if plan.breakeven > plan.stop:
        plan.stop = plan.breakeven
        plan.stop_kind = ExitReason.BREAKEVEN_STOP


def on_candle_close(plan: PositionPlan, snap: Snapshot, p: RuleParams) -> ExitReason | None:
    """Gestión al cierre de cada vela de 15 min. Devuelve un motivo si hay que cerrar ya
    (a mercado, al cierre); si no, actualiza stop/trailing en `plan`."""
    plan.candles_held += 1
    plan.max_high = max(plan.max_high, snap.high)
    if plan.max_high >= plan.entry_price + p.time_stop_min_r * plan.r:
        plan.reached_min_r = True

    if snap.close_time - plan.filled_at >= p.max_holding:
        return ExitReason.MAX_HOLDING

    if plan.partial_done:
        if snap.ema_touch is not None and snap.close < snap.ema_touch:
            return ExitReason.CLOSE_BELOW_EMA
        if snap.atr is not None:
            trail = plan.max_high - p.trailing_atr_multiple * snap.atr
            if trail > plan.stop:
                plan.stop = trail
                plan.stop_kind = ExitReason.TRAILING_STOP
    elif plan.candles_held == p.time_stop_candles and not plan.reached_min_r:
        return ExitReason.TIME_STOP
    return None
