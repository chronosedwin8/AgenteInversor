"""Resolución del modo de operación y guardia contra órdenes reales (regla 1 de CLAUDE.md).

Live solo si TRADING_MODE=live, LIVE_CONFIRM=yes y existen claves de producción. Si falta
cualquier cosa, el bot opera en paper y emite una alerta. Cualquier código que vaya a enviar
una orden al endpoint de producción debe llamar a `require_live()` justo antes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bot.config import EnvSettings


class TradingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class LiveTradingNotAllowedError(RuntimeError):
    """Se intentó una operación real sin cumplir las condiciones del modo live."""


@dataclass(frozen=True)
class ModeResolution:
    mode: TradingMode
    requested: str
    warnings: tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        """True si se pidió live (o un modo inválido) y se terminó en paper."""
        return bool(self.warnings)


def _has(secret: object) -> bool:
    value = getattr(secret, "get_secret_value", lambda: "")()
    return bool(value and value.strip())


def live_preconditions(env: EnvSettings) -> list[str]:
    """Lista de condiciones incumplidas para operar en vivo (vacía = se permite live)."""
    problems: list[str] = []
    if env.trading_mode.strip().lower() != TradingMode.LIVE:
        problems.append("TRADING_MODE no es 'live'")
    if env.live_confirm != "yes":
        problems.append("LIVE_CONFIRM no es exactamente 'yes'")
    if not (_has(env.binance_api_key) and _has(env.binance_api_secret)):
        problems.append("faltan BINANCE_API_KEY / BINANCE_API_SECRET de producción")
    return problems


def resolve_mode(env: EnvSettings) -> ModeResolution:
    requested = env.trading_mode.strip().lower()
    try:
        mode = TradingMode(requested)
    except ValueError:
        return ModeResolution(
            TradingMode.PAPER,
            requested,
            (f"TRADING_MODE={env.trading_mode!r} no es válido; se usa paper",),
        )
    if mode is not TradingMode.LIVE:
        return ModeResolution(mode, requested)
    problems = live_preconditions(env)
    if problems:
        return ModeResolution(
            TradingMode.PAPER,
            requested,
            tuple(f"live rechazado: {p}; se arranca en paper" for p in problems),
        )
    return ModeResolution(TradingMode.LIVE, requested)


def require_live(env: EnvSettings) -> None:
    """Guardia obligatoria antes de cualquier envío de orden real. Lanza si no se permite."""
    problems = live_preconditions(env)
    if problems:
        raise LiveTradingNotAllowedError("; ".join(problems))
