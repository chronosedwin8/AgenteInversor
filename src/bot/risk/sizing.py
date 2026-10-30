"""Cálculo del tamaño de posición (PLAN.md, "Cálculo del tamaño").

cantidad = min(riesgo / pérdida_por_unidad, tope% × capital / entrada), redondeada hacia abajo al
stepSize y validada contra los filtros del par. La pérdida por unidad incluye las comisiones de
entrada y de salida, para que tocar el stop cueste ≈ el riesgo fijado (0,5% del capital).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bot.exchange.filters import SymbolFilters


@dataclass(frozen=True, slots=True)
class SizeResult:
    qty: Decimal
    reason: str  # "ok" o motivo del rechazo

    @property
    def ok(self) -> bool:
        return self.reason == "ok"


def min_position_notional(
    filters: SymbolFilters, multiple: Decimal, margin_pct: Decimal
) -> Decimal:
    """Tamaño mínimo: múltiplo × NOTIONAL del par + margen (para poder vender el 50%)."""
    return filters.notional.min_notional * multiple * (1 + margin_pct / 100)


def position_size(
    *,
    capital: Decimal,
    entry: Decimal,
    stop: Decimal,
    filters: SymbolFilters,
    risk_pct: Decimal,
    max_position_pct: Decimal,
    fee_rate: Decimal,
    min_notional_multiple: Decimal,
    min_margin_pct: Decimal,
    max_cost: Decimal | None = None,
) -> SizeResult:
    if capital <= 0:
        return SizeResult(Decimal(0), "sin_capital")
    if entry <= 0 or stop <= 0 or stop >= entry:
        return SizeResult(Decimal(0), "stop_invalido")

    loss_per_unit = (entry - stop) + fee_rate * (entry + stop)
    qty_by_risk = capital * risk_pct / 100 / loss_per_unit
    qty_by_cap = capital * max_position_pct / 100 / entry
    qty = min(qty_by_risk, qty_by_cap)
    if max_cost is not None:
        qty = min(qty, max_cost / (entry * (1 + fee_rate)))
    qty = filters.round_qty(qty)

    if qty * entry < min_position_notional(filters, min_notional_multiple, min_margin_pct):
        return SizeResult(Decimal(0), "por_debajo_del_minimo")
    if filters.validate(entry, qty):
        return SizeResult(Decimal(0), "filtros_del_par")
    return SizeResult(qty, "ok")
