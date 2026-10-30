"""Estado de indicadores por vela cerrada de 15 min (y régimen de la última vela de 1 h cerrada).

`SymbolIndicators` se alimenta vela a vela, igual en vivo que en backtest. Para no mirar al
futuro, la vela de 1 h solo se usa cuando ya cerró: su close_time ≤ close_time de la de 15 min.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.config import StrategyConfig
from bot.data.klines import Candle
from bot.strategy.indicators import ADX, ATR, EMA, MACD, RSI, SMA


@dataclass(frozen=True, slots=True)
class Regime:
    close_time: datetime
    close: float
    ema_fast: float | None
    ema_slow: float | None
    adx: float | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    symbol: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema_touch: float | None  # EMA20
    ema_floor: float | None  # EMA50
    rsi: float | None
    atr: float | None
    macd_hist: float | None
    volume_ma_prev: float | None  # media de volumen de las N velas anteriores (sin la actual)
    regime: Regime | None

    @property
    def ready(self) -> bool:
        r = self.regime
        return (
            self.ema_touch is not None
            and self.ema_floor is not None
            and self.rsi is not None
            and self.atr is not None
            and self.macd_hist is not None
            and self.volume_ma_prev is not None
            and r is not None
            and r.ema_fast is not None
            and r.ema_slow is not None
            and r.adx is not None
        )


class SymbolIndicators:
    def __init__(self, symbol: str, cfg: StrategyConfig):
        self.symbol = symbol
        p, t, q, rg = cfg.pullback, cfg.trigger, cfg.quality, cfg.regime
        self._ema_touch = EMA(p.ema_touch)
        self._ema_floor = EMA(p.ema_floor)
        self._rsi = RSI(p.rsi_period)
        self._atr = ATR(q.atr_period)
        self._macd = MACD(t.macd.fast, t.macd.slow, t.macd.signal)
        self._vol_ma = SMA(t.volume_ma_period)
        self._r_fast = EMA(rg.ema_fast)
        self._r_slow = EMA(rg.ema_slow)
        self._r_adx = ADX(rg.adx_period)
        self.regime: Regime | None = None
        self._last_1h: datetime | None = None
        self._last_15m: datetime | None = None

    def on_regime_candle(self, c: Candle) -> Regime:
        """Vela cerrada del timeframe de régimen (1 h)."""
        if self._last_1h is not None and c.open_time <= self._last_1h:
            raise ValueError(f"{self.symbol}: vela de régimen repetida o desordenada")
        self._last_1h = c.open_time
        close, high, low = float(c.close), float(c.high), float(c.low)
        self.regime = Regime(
            close_time=c.close_time,
            close=close,
            ema_fast=self._r_fast.update(close),
            ema_slow=self._r_slow.update(close),
            adx=self._r_adx.update(high, low, close),
        )
        return self.regime

    def on_signal_candle(self, c: Candle) -> Snapshot:
        """Vela cerrada del timeframe de señal (15 min)."""
        if self._last_15m is not None and c.open_time <= self._last_15m:
            raise ValueError(f"{self.symbol}: vela de señal repetida o desordenada")
        self._last_15m = c.open_time
        o, h, low, close, v = (float(c.open), float(c.high), float(c.low), float(c.close),
                               float(c.volume))  # fmt: skip
        vol_ma_prev = self._vol_ma.value
        self._vol_ma.update(v)
        macd = self._macd.update(close)
        regime = self.regime
        if regime is not None and regime.close_time > c.close_time:
            raise ValueError("régimen posterior a la vela de señal (mirada al futuro)")
        return Snapshot(
            symbol=self.symbol,
            open_time=c.open_time,
            close_time=c.close_time,
            open=o,
            high=h,
            low=low,
            close=close,
            volume=v,
            ema_touch=self._ema_touch.update(close),
            ema_floor=self._ema_floor.update(close),
            rsi=self._rsi.update(close),
            atr=self._atr.update(h, low, close),
            macd_hist=macd.hist if macd else None,
            volume_ma_prev=vol_ma_prev,
            regime=regime,
        )


def build_snapshots(
    symbol: str, signal_candles: list[Candle], regime_candles: list[Candle], cfg: StrategyConfig
) -> list[Snapshot]:
    """Recorre la historia en orden temporal (misma lógica que en vivo) y devuelve los snapshots."""
    ind = SymbolIndicators(symbol, cfg)
    out: list[Snapshot] = []
    j = 0
    for c in signal_candles:
        while j < len(regime_candles) and regime_candles[j].close_time <= c.close_time:
            ind.on_regime_candle(regime_candles[j])
            j += 1
        out.append(ind.on_signal_candle(c))
    return out
