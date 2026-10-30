"""Interfaz común de estrategia. El motor de backtest (y en el futuro paper/live) solo conoce esto.

Una estrategia nunca envía órdenes: propone señales y decide salidas al cierre de cada vela.
El tamaño y la aprobación final los decide el RiskManager.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from bot.strategy.rules import EntrySignal, ExitReason, PositionPlan


class BarLike(Protocol):
    @property
    def symbol(self) -> str: ...
    @property
    def close_time(self) -> datetime: ...
    @property
    def open(self) -> float: ...
    @property
    def high(self) -> float: ...
    @property
    def low(self) -> float: ...
    @property
    def close(self) -> float: ...


class Strategy(Protocol):
    name: str
    lookback: int  # velas anteriores a la gatillo que necesita la ventana
    cancel_after_candles: int  # vida de una orden límite no llenada

    def evaluate_entry(self, window: Sequence[Any]) -> tuple[EntrySignal | None, str]: ...

    def open_plan(
        self, signal: EntrySignal, entry: float, stop: float, fee_rate: float, filled_at: datetime
    ) -> PositionPlan: ...

    def on_candle_close(self, plan: PositionPlan, bar: Any) -> ExitReason | None: ...
