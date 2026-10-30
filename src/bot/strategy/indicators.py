"""Indicadores incrementales (O(1) por vela), idénticos en backtest, paper y live.

Convenciones de arranque iguales a TA-Lib (los tests lo comparan contra TA-Lib):
- EMA: semilla = media simple de los primeros n valores; k = 2/(n+1).
- RSI, ATR, ADX: suavizado de Wilder con semilla por media simple.
Cada `update` devuelve None mientras el indicador no tiene datos suficientes.
Los indicadores trabajan en float: son analítica, no dinero.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


class EMA:
    def __init__(self, period: int):
        self.period = period
        self.k = 2.0 / (period + 1)
        self._seed: list[float] = []
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.period:
                self.value = sum(self._seed) / self.period
                self._seed = []
            return self.value
        self.value += self.k * (x - self.value)
        return self.value


class SMA:
    def __init__(self, period: int):
        self.period = period
        self._window: deque[float] = deque(maxlen=period)
        self._sum = 0.0

    def update(self, x: float) -> float | None:
        if len(self._window) == self.period:
            self._sum -= self._window[0]
        self._window.append(x)
        self._sum += x
        return self._sum / self.period if len(self._window) == self.period else None

    @property
    def value(self) -> float | None:
        return self._sum / self.period if len(self._window) == self.period else None


class RSI:
    """RSI de Wilder."""

    def __init__(self, period: int = 14):
        self.period = period
        self._prev: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss = 0.0
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        change = close - self._prev
        self._prev = close
        gain, loss = max(change, 0.0), max(-change, 0.0)
        n = self.period
        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < n:
                return None
            self._avg_gain = sum(self._gains) / n
            self._avg_loss = sum(self._losses) / n
            self._gains, self._losses = [], []
        else:
            self._avg_gain = (self._avg_gain * (n - 1) + gain) / n
            self._avg_loss = (self._avg_loss * (n - 1) + loss) / n
        total = self._avg_gain + self._avg_loss
        self.value = 100.0 * self._avg_gain / total if total > 0 else 0.0
        return self.value


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


class ATR:
    """ATR de Wilder."""

    def __init__(self, period: int = 14):
        self.period = period
        self._prev_close: float | None = None
        self._seed: list[float] = []
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            self._prev_close = close
            return None
        tr = _true_range(high, low, self._prev_close)
        self._prev_close = close
        n = self.period
        if self.value is None:
            self._seed.append(tr)
            if len(self._seed) == n:
                self.value = sum(self._seed) / n
                self._seed = []
            return self.value
        self.value = (self.value * (n - 1) + tr) / n
        return self.value


class ADX:
    """ADX de Wilder, con la misma secuencia de arranque que TA-Lib (primer valor en 2n-1)."""

    def __init__(self, period: int = 14):
        self.period = period
        self._bars = 0
        self._prev_high = 0.0
        self._prev_low = 0.0
        self._prev_close = 0.0
        self._plus_dm = 0.0
        self._minus_dm = 0.0
        self._tr = 0.0
        self._sum_dx = 0.0
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        n = self.period
        i = self._bars
        self._bars += 1
        if i == 0:
            self._prev_high, self._prev_low, self._prev_close = high, low, close
            return None
        diff_p = high - self._prev_high
        diff_m = self._prev_low - low
        tr = _true_range(high, low, self._prev_close)
        self._prev_high, self._prev_low, self._prev_close = high, low, close
        plus = diff_p if (diff_p > 0 and diff_p > diff_m) else 0.0
        minus = diff_m if (diff_m > 0 and diff_p < diff_m) else 0.0

        if i <= n - 1:  # acumulación inicial de n-1 barras
            self._plus_dm += plus
            self._minus_dm += minus
            self._tr += tr
            return None

        self._plus_dm += plus - self._plus_dm / n
        self._minus_dm += minus - self._minus_dm / n
        self._tr += tr - self._tr / n
        dx = 0.0
        if self._tr != 0:
            plus_di = 100.0 * self._plus_dm / self._tr
            minus_di = 100.0 * self._minus_dm / self._tr
            s = plus_di + minus_di
            if s != 0:
                dx = 100.0 * abs(minus_di - plus_di) / s
        if self.value is None:
            self._sum_dx += dx
            if i == 2 * n - 1:
                self.value = self._sum_dx / n
            return self.value
        self.value = (self.value * (n - 1) + dx) / n
        return self.value


@dataclass(frozen=True, slots=True)
class MACDValue:
    macd: float
    signal: float
    hist: float


class MACD:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._fast = EMA(fast)
        self._slow = EMA(slow)
        self._signal = EMA(signal)
        self.value: MACDValue | None = None

    def update(self, close: float) -> MACDValue | None:
        f = self._fast.update(close)
        s = self._slow.update(close)
        if f is None or s is None:
            return None
        line = f - s
        sig = self._signal.update(line)
        if sig is None:
            return None
        self.value = MACDValue(line, sig, line - sig)
        return self.value
