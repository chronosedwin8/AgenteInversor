"""Fábrica de snapshots sintéticos para probar reglas y motor sin depender de indicadores."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.config import load_config
from bot.strategy.rules import RuleParams
from bot.strategy.snapshot import Regime, Snapshot
from tests.conftest import ROOT

M15 = timedelta(minutes=15)
# 15:00 UTC = 10:00 hora Bogotá (dentro de la ventana 07:00–18:00)
BASE = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)

CFG = load_config(ROOT / "config")
PARAMS = RuleParams.from_config(CFG.strategy, CFG.settings)

BULL = Regime(close_time=BASE - timedelta(hours=1), close=105.0, ema_fast=100.0, ema_slow=95.0,
              adx=30.0)  # fmt: skip


def snap(i: int, o: float, h: float, low: float, c: float, symbol: str = "SOLUSDT",
         **kw: Any) -> Snapshot:  # fmt: skip
    """Vela i (close_time = BASE + i×15 min) con indicadores "buenos" por defecto."""
    close_time = BASE + i * M15
    values: dict[str, Any] = {
        "volume": 100.0,
        "ema_touch": 100.0,
        "ema_floor": 98.0,
        "rsi": 50.0,
        "atr": 1.0,
        "macd_hist": 0.0,
        "volume_ma_prev": 100.0,
        "regime": BULL,
    }
    values.update(kw)
    return Snapshot(
        symbol=symbol,
        open_time=close_time - M15 + timedelta(milliseconds=1),
        close_time=close_time,
        open=o,
        high=h,
        low=low,
        close=c,
        **values,
    )


def signal_window(symbol: str = "SOLUSDT", start: int = 0) -> list[Snapshot]:
    """4 velas que cumplen todas las reglas de entrada en la última (índice start+3).

    Entrada límite 101,0; ATR 1,0 → stop 99,5 (R = 1,5).
    """
    s = start
    return [
        snap(s + 0, 101.0, 101.5, 100.5, 101.0, symbol, macd_hist=-0.3),
        snap(s + 1, 101.0, 101.2, 100.1, 100.5, symbol, macd_hist=-0.2),  # toca la EMA20
        snap(s + 2, 100.5, 100.8, 100.25, 100.4, symbol, macd_hist=-0.1),  # no toca
        snap(s + 3, 100.4, 101.3, 100.3, 101.0, symbol, macd_hist=0.05, volume=150.0),  # gatillo
    ]


def params(**changes: Any) -> RuleParams:
    return replace(PARAMS, **changes)
