"""RiskManager: autoridad final sobre cada orden de entrada (regla 2).

Fase 2 (este archivo): tamaño de posición, posiciones simultáneas, una por activo, caja
disponible. Fase 3 añade aquí mismo: límite de operaciones/día, pérdida diaria, pérdidas
consecutivas, alerta de drawdown (riesgo reducido) y kill switch. El backtester, paper y live
llaman a esta misma clase.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from bot.config import RiskConfig
from bot.exchange.filters import SymbolFilters
from bot.risk.sizing import position_size


class PortfolioView(Protocol):
    def equity(self) -> Decimal: ...
    def free_cash(self) -> Decimal: ...
    def has_exposure(self, symbol: str) -> bool: ...
    def exposure_count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class EntryIntent:
    symbol: str
    limit_price: Decimal
    stop: Decimal


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    qty: Decimal
    reason: str


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def current_risk_pct(self, equity: Decimal) -> Decimal:
        return self.cfg.risk_per_trade_pct

    def approve_entry(
        self, intent: EntryIntent, portfolio: PortfolioView, filters: SymbolFilters
    ) -> RiskDecision:
        cfg = self.cfg
        if not filters.is_trading:
            return RiskDecision(False, Decimal(0), "par_no_operable")
        if portfolio.has_exposure(intent.symbol):
            return RiskDecision(False, Decimal(0), "ya_hay_posicion_en_el_activo")
        if portfolio.exposure_count() >= cfg.max_open_positions:
            return RiskDecision(False, Decimal(0), "maximo_de_posiciones")

        equity = portfolio.equity()
        size = position_size(
            capital=equity,
            entry=intent.limit_price,
            stop=intent.stop,
            filters=filters,
            risk_pct=self.current_risk_pct(equity),
            max_position_pct=cfg.max_position_pct,
            fee_rate=cfg.fee_rate,
            min_notional_multiple=cfg.min_position_notional_multiple,
            min_margin_pct=cfg.min_position_margin_pct,
            max_cost=portfolio.free_cash(),
        )
        if not size.ok:
            return RiskDecision(False, Decimal(0), size.reason)
        return RiskDecision(True, size.qty, "ok")
