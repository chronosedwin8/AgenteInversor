"""Portafolio: caja, posiciones, reservas de órdenes pendientes, P&L con comisiones (Decimal).

"Capital" (PLAN.md) = USDT libre + valor de mercado de las posiciones − comisiones de cierre
estimadas. Las órdenes de entrada pendientes reservan caja (no cambian el capital).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class Holding:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    opened_at: datetime
    risk_amount: Decimal  # pérdida prevista hasta el stop inicial, con comisiones
    cost: Decimal  # USDT pagados en la compra, comisión incluida
    proceeds: Decimal = Decimal(0)  # USDT recibidos en ventas, netos de comisión
    gross_proceeds: Decimal = Decimal(0)  # valor bruto vendido (para el precio medio)
    fees: Decimal = Decimal(0)
    exits: list[str] = field(default_factory=list)
    initial_qty: Decimal = Decimal(0)


@dataclass(frozen=True)
class Trade:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    qty: Decimal
    entry_price: Decimal
    avg_exit_price: Decimal
    pnl: Decimal  # neto de comisiones
    fees: Decimal
    risk_amount: Decimal
    exits: tuple[str, ...]

    @property
    def r_multiple(self) -> Decimal:
        return self.pnl / self.risk_amount if self.risk_amount > 0 else Decimal(0)


class Portfolio:
    def __init__(self, initial_cash: Decimal, fee_rate: Decimal):
        self.cash = initial_cash
        self.fee_rate = fee_rate
        self.holdings: dict[str, Holding] = {}
        self.reserved: dict[str, Decimal] = {}
        self.marks: dict[str, Decimal] = {}
        self.trades: list[Trade] = []

    # ------------------------------------------------------------ PortfolioView

    def equity(self) -> Decimal:
        value = self.cash
        for h in self.holdings.values():
            price = self.marks.get(h.symbol, h.entry_price)
            value += h.qty * price * (1 - self.fee_rate)
        return value

    def free_cash(self) -> Decimal:
        return self.cash - sum(self.reserved.values(), Decimal(0))

    def has_exposure(self, symbol: str) -> bool:
        return symbol in self.holdings or symbol in self.reserved

    def exposure_count(self) -> int:
        return len(self.holdings.keys() | self.reserved.keys())

    def exposure_symbols(self) -> set[str]:
        return set(self.holdings) | set(self.reserved)

    def exposure_value(self) -> Decimal:
        """Valor de mercado de las posiciones + caja reservada para órdenes pendientes."""
        held = sum(
            (h.qty * self.marks.get(h.symbol, h.entry_price) for h in self.holdings.values()),
            Decimal(0),
        )
        return held + sum(self.reserved.values(), Decimal(0))

    # ------------------------------------------------------------ operaciones

    def mark(self, symbol: str, price: Decimal) -> None:
        self.marks[symbol] = price

    def reserve(self, symbol: str, amount: Decimal) -> None:
        if amount > self.free_cash():
            raise ValueError("reserva mayor que la caja libre")
        self.reserved[symbol] = amount

    def release(self, symbol: str) -> None:
        self.reserved.pop(symbol, None)

    def buy(self, symbol: str, qty: Decimal, price: Decimal, at: datetime,
            risk_amount: Decimal) -> Holding:  # fmt: skip
        if symbol in self.holdings:
            raise ValueError(f"ya existe una posición en {symbol}")
        self.release(symbol)
        notional = qty * price
        fee = notional * self.fee_rate
        if notional + fee > self.cash:
            raise ValueError("caja insuficiente")
        self.cash -= notional + fee
        h = Holding(symbol, qty, price, at, risk_amount, notional + fee, fees=fee, initial_qty=qty)
        self.holdings[symbol] = h
        self.marks[symbol] = price
        return h

    def sell(self, symbol: str, qty: Decimal, price: Decimal, at: datetime,
             reason: str) -> Trade | None:  # fmt: skip
        """Vende `qty`; si la posición queda en cero devuelve la operación cerrada."""
        h = self.holdings[symbol]
        if qty <= 0 or qty > h.qty:
            raise ValueError("cantidad de venta inválida")
        gross = qty * price
        fee = gross * self.fee_rate
        self.cash += gross - fee
        h.qty -= qty
        h.proceeds += gross - fee
        h.gross_proceeds += gross
        h.fees += fee
        h.exits.append(reason)
        if h.qty > 0:
            return None
        del self.holdings[symbol]
        self.marks.pop(symbol, None)
        trade = Trade(
            symbol=symbol,
            opened_at=h.opened_at,
            closed_at=at,
            qty=h.initial_qty,
            entry_price=h.entry_price,
            avg_exit_price=h.gross_proceeds / h.initial_qty,
            pnl=h.proceeds - h.cost,
            fees=h.fees,
            risk_amount=h.risk_amount,
            exits=tuple(h.exits),
        )
        self.trades.append(trade)
        return trade
