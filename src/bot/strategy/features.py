"""Velas con features para las hipótesis de la Fase 2b (señal 4 h, régimen diario).

Mismo principio que `snapshot.py`: cálculo incremental vela a vela (idéntico en vivo) y el
régimen sale de la última vela diaria CERRADA.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from bot.data.klines import Candle
from bot.strategy.indicators import ATR, EMA, RSI

SIGNAL_EMAS = (5,)
REGIME_EMAS = (50, 75, 100, 150, 200)  # incluye la rejilla de sensibilidad de la auditoría
DONCHIAN = (10, 15, 20, 30, 40, 60)


class PriorHigh:
    """Máximo de los `n` máximos ANTERIORES (sin la vela actual)."""

    def __init__(self, n: int):
        self._window: deque[float] = deque(maxlen=n)

    def update(self, high: float) -> float | None:
        value = max(self._window) if len(self._window) == self._window.maxlen else None
        self._window.append(high)
        return value


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr: float | None
    rsi3: float | None
    ema: dict[int, float | None] = field(default_factory=dict)  # EMAs de 4 h por periodo
    donchian: dict[int, float | None] = field(default_factory=dict)  # máximo previo de N velas
    regime_close: float | None = None  # cierre de la última vela diaria cerrada
    regime_ema: dict[int, float | None] = field(default_factory=dict)


class FeatureBuilder:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self._atr = ATR(14)
        self._rsi3 = RSI(3)
        self._ema = {n: EMA(n) for n in SIGNAL_EMAS}
        self._donch = {n: PriorHigh(n) for n in DONCHIAN}
        self._d_ema = {n: EMA(n) for n in REGIME_EMAS}
        self._d_close: float | None = None
        self._d_values: dict[int, float | None] = {n: None for n in REGIME_EMAS}
        self._d_close_time: datetime | None = None

    def on_regime_candle(self, c: Candle) -> None:
        close = float(c.close)
        self._d_close = close
        self._d_close_time = c.close_time
        self._d_values = {n: e.update(close) for n, e in self._d_ema.items()}

    def on_signal_candle(self, c: Candle) -> Bar:
        if self._d_close_time is not None and self._d_close_time > c.close_time:
            raise ValueError("régimen posterior a la vela de señal (mirada al futuro)")
        h, low, close = float(c.high), float(c.low), float(c.close)
        return Bar(
            symbol=self.symbol,
            open_time=c.open_time,
            close_time=c.close_time,
            open=float(c.open),
            high=h,
            low=low,
            close=close,
            volume=float(c.volume),
            atr=self._atr.update(h, low, close),
            rsi3=self._rsi3.update(close),
            ema={n: e.update(close) for n, e in self._ema.items()},
            donchian={n: d.update(h) for n, d in self._donch.items()},
            regime_close=self._d_close,
            regime_ema=dict(self._d_values),
        )


def build_bars(symbol: str, signal: list[Candle], regime: list[Candle]) -> list[Bar]:
    fb = FeatureBuilder(symbol)
    out: list[Bar] = []
    j = 0
    for c in signal:
        while j < len(regime) and regime[j].close_time <= c.close_time:
            fb.on_regime_candle(regime[j])
            j += 1
        out.append(fb.on_signal_candle(c))
    return out
