"""Redondeo por tickSize/stepSize y validación NOTIONAL contra exchangeInfo real."""

from __future__ import annotations

import copy
from decimal import Decimal as D
from typing import Any

import pytest

from bot.exchange.filters import SymbolFilters, SymbolFiltersError, Violation


@pytest.fixture
def btc(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])


@pytest.fixture
def sol(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])


# ------------------------------------------------------------------ parseo


def test_parses_filters_from_exchange_info(btc: SymbolFilters) -> None:
    assert btc.symbol == "BTCUSDT"
    assert btc.is_trading
    assert btc.supports("LIMIT_MAKER")
    assert btc.price.tick_size == D("0.01")
    assert btc.lot.step_size == D("0.00001")
    assert btc.lot.min_qty == D("0.00001")
    assert btc.notional.min_notional == D("5")
    assert btc.notional.apply_min_to_market is True
    assert btc.market_lot is not None
    assert btc.percent_price_by_side is not None


def test_each_symbol_has_its_own_filters(symbol_info: dict[str, dict[str, Any]]) -> None:
    steps = {s: SymbolFilters.from_exchange_info(i).lot.step_size for s, i in symbol_info.items()}
    assert steps == {"BTCUSDT": D("0.00001"), "ETHUSDT": D("0.0001"), "SOLUSDT": D("0.001")}


@pytest.mark.parametrize("missing", ["PRICE_FILTER", "LOT_SIZE", "NOTIONAL"])
def test_missing_required_filter_raises(symbol_info: dict[str, Any], missing: str) -> None:
    info = copy.deepcopy(symbol_info["BTCUSDT"])
    info["filters"] = [f for f in info["filters"] if f["filterType"] != missing]
    with pytest.raises(SymbolFiltersError, match=missing):
        SymbolFilters.from_exchange_info(info)


def test_legacy_min_notional_supported(symbol_info: dict[str, Any]) -> None:
    info = copy.deepcopy(symbol_info["BTCUSDT"])
    info["filters"] = [f for f in info["filters"] if f["filterType"] != "NOTIONAL"]
    info["filters"].append(
        {"filterType": "MIN_NOTIONAL", "minNotional": "10.0", "applyToMarket": False}
    )
    f = SymbolFilters.from_exchange_info(info)
    assert f.notional.min_notional == D(10)
    assert f.notional.apply_min_to_market is False


# ------------------------------------------------------------------ redondeo


@pytest.mark.parametrize(
    ("qty", "expected"),
    [
        (D("0.123456789"), D("0.12345")),
        (D("0.000019999"), D("0.00001")),
        (D("0.00001"), D("0.00001")),
        (D("0.000009"), D("0")),  # por debajo del paso → 0, nunca hacia arriba
        (D("1"), D("1")),
        (D("-1"), D("0")),
    ],
)
def test_round_qty_always_down(btc: SymbolFilters, qty: D, expected: D) -> None:
    assert btc.round_qty(qty) == expected


@pytest.mark.parametrize(
    ("price", "down", "up"),
    [
        (D("76884.646"), D("76884.64"), D("76884.65")),
        (D("76884.64"), D("76884.64"), D("76884.64")),
        (D("0.019"), D("0.01"), D("0.02")),
    ],
)
def test_round_price_to_tick(btc: SymbolFilters, price: D, down: D, up: D) -> None:
    assert btc.round_price(price, "down") == down
    assert btc.round_price(price, "up") == up


def test_rounded_values_pass_validation(sol: SymbolFilters) -> None:
    price = sol.round_price(D("187.3456"), "down")
    qty = sol.round_qty(D("0.35678"))
    assert (price, qty) == (D("187.34"), D("0.356"))
    assert sol.validate(price, qty) == []


def test_decimal_exactness_no_float_drift(btc: SymbolFilters) -> None:
    # 0.1 + 0.2 en float es 0.30000000000000004; con Decimal el redondeo es exacto.
    assert btc.round_qty(D("0.1") + D("0.2")) == D("0.3")
    assert btc.validate(D("100.00"), D("0.3")) == []


def test_plan_sizing_example_rounds_to_step(sol: SymbolFilters) -> None:
    # PLAN.md: capital 200, entrada 100, stop 98,5 → 1,00 / 1,5 = 0,6667 unidades.
    qty = sol.round_qty(D("200") * D("0.005") / (D("100") - D("98.5")))
    assert qty == D("0.666")
    assert sol.validate(D("100"), qty) == []


# ------------------------------------------------------------------ NOTIONAL


def test_notional_below_min_rejected(btc: SymbolFilters) -> None:
    # 0,00006 BTC × 77.000 = 4,62 USDT < 5
    assert Violation.NOTIONAL_BELOW_MIN in btc.validate(D("77000"), D("0.00006"))


def test_notional_exactly_at_min_accepted(btc: SymbolFilters) -> None:
    # 0,0001 BTC × 50.000 = 5,00 USDT
    assert btc.validate(D("50000"), D("0.0001")) == []


def test_notional_above_max_rejected(btc: SymbolFilters) -> None:
    assert Violation.NOTIONAL_ABOVE_MAX in btc.validate(D("1000000"), D("9.1"))


def test_market_order_notional_respects_apply_to_market(symbol_info: dict[str, Any]) -> None:
    info = copy.deepcopy(symbol_info["BTCUSDT"])
    for f in info["filters"]:
        if f["filterType"] == "NOTIONAL":
            f["applyMinToMarket"] = False
    f = SymbolFilters.from_exchange_info(info)
    assert Violation.NOTIONAL_BELOW_MIN in f.validate(D("77000"), D("0.00006"))
    assert Violation.NOTIONAL_BELOW_MIN not in f.validate(D("77000"), D("0.00006"), is_market=True)


def test_min_qty_for_notional_meets_min(btc: SymbolFilters) -> None:
    price = D("76884.64")
    qty = btc.min_qty_for_notional(price)
    assert qty == D("0.00007")  # 5 / 76884.64 = 0.0000650 → arriba al paso
    assert btc.validate(price, qty) == []
    assert Violation.NOTIONAL_BELOW_MIN in btc.validate(price, qty - btc.lot.step_size)


def test_min_qty_for_custom_notional(sol: SymbolFilters) -> None:
    # Tamaño mínimo del plan: 2 × NOTIONAL (10 USDT) para poder vender el 50%.
    qty = sol.min_qty_for_notional(D("187.34"), D(2) * sol.notional.min_notional)
    assert qty == D("0.054")  # 10 / 187,34 = 0,05338 → arriba al paso de 0,001
    half = sol.round_qty(qty / 2)
    assert sol.validate(D("187.34"), half) == []  # la mitad sigue siendo vendible


# ------------------------------------------------------------------ resto de violaciones


@pytest.mark.parametrize(
    ("price", "qty", "violation"),
    [
        (D("76884.645"), D("0.001"), Violation.PRICE_NOT_TICK_MULTIPLE),
        (D("0.001"), D("8000"), Violation.PRICE_BELOW_MIN),
        (D("2000000"), D("0.001"), Violation.PRICE_ABOVE_MAX),
        (D("76884.64"), D("0.000015"), Violation.QTY_NOT_STEP_MULTIPLE),
        (D("76884.64"), D("0.000001"), Violation.QTY_BELOW_MIN),
        (D("100"), D("9001"), Violation.QTY_ABOVE_MAX),
        (D("76884.64"), D("0"), Violation.QTY_NOT_POSITIVE),
        (D("0"), D("0.001"), Violation.PRICE_NOT_POSITIVE),
    ],
)
def test_violations(btc: SymbolFilters, price: D, qty: D, violation: Violation) -> None:
    assert violation in btc.validate(price, qty)


def test_percent_price_by_side(btc: SymbolFilters) -> None:
    avg = D("77000")
    # bidMultiplierDown 0.5 → compra por debajo de 38.500 rechazada
    assert Violation.PERCENT_PRICE_OUT_OF_RANGE in btc.validate(
        D("38000"), D("0.001"), side="BUY", avg_price=avg
    )
    assert btc.validate(D("76000"), D("0.001"), side="BUY", avg_price=avg) == []
