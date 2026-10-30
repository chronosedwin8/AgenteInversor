"""Filtros de trading por símbolo, leídos de /api/v3/exchangeInfo (regla 5: nunca hardcodeados).

Todo en Decimal. Redondeo:
- cantidad: siempre hacia abajo al múltiplo de stepSize (nunca comprar/vender más de lo calculado);
- precio: hacia abajo o hacia arriba al múltiplo de tickSize, según lo pida el llamador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Any, Literal

Rounding = Literal["down", "up"]


class SymbolFiltersError(ValueError):
    """exchangeInfo de un símbolo no trae los filtros mínimos necesarios."""


def _d(value: Any) -> Decimal:
    return Decimal(str(value)).normalize()


def _quantize(value: Decimal, step: Decimal, rounding: Rounding) -> Decimal:
    if step == 0:
        return value
    mode = ROUND_FLOOR if rounding == "down" else ROUND_CEILING
    units = (value / step).to_integral_value(rounding=mode)
    return (units * step).normalize()


def _is_multiple(value: Decimal, base: Decimal, step: Decimal) -> bool:
    if step == 0:
        return True
    return ((value - base) % step) == 0


@dataclass(frozen=True)
class PriceFilter:
    min_price: Decimal
    max_price: Decimal
    tick_size: Decimal


@dataclass(frozen=True)
class LotSize:
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal


@dataclass(frozen=True)
class NotionalFilter:
    min_notional: Decimal
    max_notional: Decimal | None
    apply_min_to_market: bool
    apply_max_to_market: bool
    avg_price_mins: int


@dataclass(frozen=True)
class PercentPriceBySide:
    bid_multiplier_up: Decimal
    bid_multiplier_down: Decimal
    ask_multiplier_up: Decimal
    ask_multiplier_down: Decimal
    avg_price_mins: int


class Violation(StrEnum):
    PRICE_NOT_POSITIVE = "PRICE_NOT_POSITIVE"
    PRICE_BELOW_MIN = "PRICE_BELOW_MIN"
    PRICE_ABOVE_MAX = "PRICE_ABOVE_MAX"
    PRICE_NOT_TICK_MULTIPLE = "PRICE_NOT_TICK_MULTIPLE"
    QTY_NOT_POSITIVE = "QTY_NOT_POSITIVE"
    QTY_BELOW_MIN = "QTY_BELOW_MIN"
    QTY_ABOVE_MAX = "QTY_ABOVE_MAX"
    QTY_NOT_STEP_MULTIPLE = "QTY_NOT_STEP_MULTIPLE"
    NOTIONAL_BELOW_MIN = "NOTIONAL_BELOW_MIN"
    NOTIONAL_ABOVE_MAX = "NOTIONAL_ABOVE_MAX"
    PERCENT_PRICE_OUT_OF_RANGE = "PERCENT_PRICE_OUT_OF_RANGE"


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    order_types: frozenset[str]
    price: PriceFilter
    lot: LotSize
    notional: NotionalFilter
    market_lot: LotSize | None = None
    percent_price_by_side: PercentPriceBySide | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    # ------------------------------------------------------------ parseo

    @classmethod
    def from_exchange_info(cls, symbol_info: dict[str, Any]) -> SymbolFilters:
        """Construye los filtros a partir de un elemento de exchangeInfo['symbols'] (JSON)."""
        symbol = symbol_info.get("symbol", "?")
        by_type: dict[str, dict[str, Any]] = {
            f["filterType"]: f for f in symbol_info.get("filters", [])
        }

        def need(name: str) -> dict[str, Any]:
            if name not in by_type:
                raise SymbolFiltersError(f"{symbol}: falta el filtro {name} en exchangeInfo")
            return by_type[name]

        pf = need("PRICE_FILTER")
        ls = need("LOT_SIZE")
        if "NOTIONAL" in by_type:
            nf = by_type["NOTIONAL"]
            notional = NotionalFilter(
                min_notional=_d(nf["minNotional"]),
                max_notional=_d(nf["maxNotional"]) if nf.get("maxNotional") else None,
                apply_min_to_market=bool(nf.get("applyMinToMarket", True)),
                apply_max_to_market=bool(nf.get("applyMaxToMarket", False)),
                avg_price_mins=int(nf.get("avgPriceMins", 5)),
            )
        elif "MIN_NOTIONAL" in by_type:  # formato antiguo
            nf = by_type["MIN_NOTIONAL"]
            notional = NotionalFilter(
                min_notional=_d(nf["minNotional"]),
                max_notional=None,
                apply_min_to_market=bool(nf.get("applyToMarket", True)),
                apply_max_to_market=False,
                avg_price_mins=int(nf.get("avgPriceMins", 5)),
            )
        else:
            raise SymbolFiltersError(f"{symbol}: falta el filtro NOTIONAL en exchangeInfo")

        market_lot = None
        if (ml := by_type.get("MARKET_LOT_SIZE")) is not None:
            market_lot = LotSize(_d(ml["minQty"]), _d(ml["maxQty"]), _d(ml["stepSize"]))

        ppbs = None
        if (pp := by_type.get("PERCENT_PRICE_BY_SIDE")) is not None:
            ppbs = PercentPriceBySide(
                bid_multiplier_up=_d(pp["bidMultiplierUp"]),
                bid_multiplier_down=_d(pp["bidMultiplierDown"]),
                ask_multiplier_up=_d(pp["askMultiplierUp"]),
                ask_multiplier_down=_d(pp["askMultiplierDown"]),
                avg_price_mins=int(pp.get("avgPriceMins", 5)),
            )

        return cls(
            symbol=symbol,
            status=str(symbol_info.get("status", "")),
            base_asset=str(symbol_info.get("baseAsset", "")),
            quote_asset=str(symbol_info.get("quoteAsset", "")),
            order_types=frozenset(symbol_info.get("orderTypes", [])),
            price=PriceFilter(_d(pf["minPrice"]), _d(pf["maxPrice"]), _d(pf["tickSize"])),
            lot=LotSize(_d(ls["minQty"]), _d(ls["maxQty"]), _d(ls["stepSize"])),
            notional=notional,
            market_lot=market_lot,
            percent_price_by_side=ppbs,
            raw=symbol_info,
        )

    # ------------------------------------------------------------ estado

    @property
    def is_trading(self) -> bool:
        return self.status == "TRADING"

    def supports(self, order_type: str) -> bool:
        return order_type in self.order_types

    # ------------------------------------------------------------ redondeo

    def round_price(self, price: Decimal, rounding: Rounding) -> Decimal:
        return _quantize(price, self.price.tick_size, rounding)

    def round_qty(self, qty: Decimal) -> Decimal:
        """Cantidad hacia abajo al múltiplo de stepSize."""
        if qty <= 0:
            return Decimal(0)
        return _quantize(qty, self.lot.step_size, "down")

    def min_qty_for_notional(self, price: Decimal, notional: Decimal | None = None) -> Decimal:
        """Menor cantidad (múltiplo de stepSize, ≥ minQty) cuyo valor alcanza `notional`
        (por defecto minNotional) al precio dado."""
        if price <= 0:
            raise ValueError("price debe ser positivo")
        target = self.notional.min_notional if notional is None else notional
        qty = _quantize(target / price, self.lot.step_size, "up")
        return max(qty, self.lot.min_qty)

    # ------------------------------------------------------------ validación

    def validate(
        self,
        price: Decimal,
        qty: Decimal,
        *,
        is_market: bool = False,
        side: Literal["BUY", "SELL"] | None = None,
        avg_price: Decimal | None = None,
    ) -> list[Violation]:
        """Devuelve la lista de filtros que la orden incumpliría (vacía = válida).

        `price` es el precio límite; en órdenes de mercado, el precio de referencia (Binance
        usa el precio medio de `avgPriceMins` minutos para NOTIONAL en órdenes de mercado).
        `side` y `avg_price` activan la comprobación de PERCENT_PRICE_BY_SIDE en órdenes límite.
        """
        out: list[Violation] = []

        if price <= 0:
            out.append(Violation.PRICE_NOT_POSITIVE)
        elif not is_market:
            pf = self.price
            if pf.min_price > 0 and price < pf.min_price:
                out.append(Violation.PRICE_BELOW_MIN)
            if pf.max_price > 0 and price > pf.max_price:
                out.append(Violation.PRICE_ABOVE_MAX)
            if pf.tick_size > 0 and not _is_multiple(price, pf.min_price, pf.tick_size):
                out.append(Violation.PRICE_NOT_TICK_MULTIPLE)

        # Las órdenes de mercado deben cumplir LOT_SIZE y además MARKET_LOT_SIZE.
        lots = [self.lot]
        if is_market and self.market_lot is not None:
            lots.append(self.market_lot)
        if qty <= 0:
            out.append(Violation.QTY_NOT_POSITIVE)
        else:
            for lot in lots:
                if qty < lot.min_qty and Violation.QTY_BELOW_MIN not in out:
                    out.append(Violation.QTY_BELOW_MIN)
                if lot.max_qty > 0 and qty > lot.max_qty and Violation.QTY_ABOVE_MAX not in out:
                    out.append(Violation.QTY_ABOVE_MAX)
                if (
                    not _is_multiple(qty, lot.min_qty, lot.step_size)
                    and Violation.QTY_NOT_STEP_MULTIPLE not in out
                ):
                    out.append(Violation.QTY_NOT_STEP_MULTIPLE)

        if price > 0 and qty > 0:
            value = price * qty
            nf = self.notional
            check_min = nf.apply_min_to_market if is_market else True
            check_max = nf.apply_max_to_market if is_market else True
            if check_min and value < nf.min_notional:
                out.append(Violation.NOTIONAL_BELOW_MIN)
            if check_max and nf.max_notional is not None and value > nf.max_notional:
                out.append(Violation.NOTIONAL_ABOVE_MAX)

        pp = self.percent_price_by_side
        if pp and side and avg_price and avg_price > 0 and not is_market and price > 0:
            if side == "BUY":
                lo, hi = avg_price * pp.bid_multiplier_down, avg_price * pp.bid_multiplier_up
            else:
                lo, hi = avg_price * pp.ask_multiplier_down, avg_price * pp.ask_multiplier_up
            if not (lo <= price <= hi):
                out.append(Violation.PERCENT_PRICE_OUT_OF_RANGE)

        return out
