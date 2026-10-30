from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

import pytest

from bot.config import load_config
from bot.exchange.filters import SymbolFilters
from bot.portfolio.portfolio import Portfolio
from bot.risk.manager import EntryIntent, RiskManager
from bot.risk.sizing import position_size
from tests.conftest import ROOT

RISK = load_config(ROOT / "config").risk
T = datetime(2026, 3, 2, tzinfo=UTC)


@pytest.fixture
def sol(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["SOLUSDT"])


def size(sol: SymbolFilters, capital: str, entry: str, stop: str, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "capital": D(capital), "entry": D(entry), "stop": D(stop), "filters": sol,
        "risk_pct": D("0.5"), "max_position_pct": D(40), "fee_rate": D("0.001"),
        "min_notional_multiple": D(2), "min_margin_pct": D(10),
    }  # fmt: skip
    args.update(kw)
    return position_size(**args)


def test_plan_example_with_fees_included(sol: SymbolFilters) -> None:
    # PLAN.md: capital 200, entrada 100, stop 98,5. Sin comisiones serían 0,667 unidades;
    # con 0,1% por lado incluidas, la pérdida en el stop queda ≈ 1,00 USD.
    r = size(sol, "200", "100", "98.5")
    assert r.ok and r.qty == D("0.588")
    loss = r.qty * (D(100) - D("98.5")) + r.qty * D("0.001") * (D(100) + D("98.5"))
    assert D("0.99") < loss <= D("1.00")


def test_without_fees_matches_plan_number(sol: SymbolFilters) -> None:
    assert size(sol, "200", "100", "98.5", fee_rate=D(0)).qty == D("0.666")


def test_position_capped_at_40_percent(sol: SymbolFilters) -> None:
    r = size(sol, "200", "100", "99.9")  # stop muy cercano → la cantidad por riesgo sería enorme
    assert r.qty * 100 <= 80 and r.qty == D("0.8")


def test_below_minimum_size_rejected(sol: SymbolFilters) -> None:
    # Tamaño mínimo = 2 × 5 USDT × 1,10 = 11 USDT. Con capital 20 → riesgo 0,10 → ~6 USDT.
    r = size(sol, "20", "100", "98.5")
    assert not r.ok and r.reason == "por_debajo_del_minimo"


def test_invalid_stop_rejected(sol: SymbolFilters) -> None:
    assert size(sol, "200", "100", "100").reason == "stop_invalido"
    assert size(sol, "200", "100", "101").reason == "stop_invalido"


def test_max_cost_limits_size(sol: SymbolFilters) -> None:
    r = size(sol, "200", "100", "98.5", max_cost=D("30"))
    assert r.ok and r.qty * D(100) * D("1.001") <= 30


# ------------------------------------------------------------------ RiskManager


def test_risk_manager_limits(sol: SymbolFilters) -> None:
    rm = RiskManager(RISK)
    pf = Portfolio(D(200), D("0.001"))
    intent = EntryIntent("SOLUSDT", D(100), D("98.5"))
    d = rm.approve_entry(intent, pf, sol)
    assert d.approved and d.qty == D("0.588")

    pf.reserve("SOLUSDT", D(60))
    assert rm.approve_entry(intent, pf, sol).reason == "ya_hay_posicion_en_el_activo"
    pf.reserve("ETHUSDT", D(60))
    other = EntryIntent("BTCUSDT", D(100), D("98.5"))
    assert rm.approve_entry(other, pf, sol).reason == "maximo_de_posiciones"


# ------------------------------------------------------------------ Portfolio


def test_round_trip_pnl_includes_fees() -> None:
    pf = Portfolio(D(200), D("0.001"))
    pf.buy("SOLUSDT", D("0.5"), D(100), T, risk_amount=D(1))
    assert pf.cash == D(200) - D("50.05")
    assert pf.sell("SOLUSDT", D("0.25"), D(110), T, "parcial") is None
    trade = pf.sell("SOLUSDT", D("0.25"), D(90), T, "stop")
    assert trade is not None
    # compra 50 + 0,05; ventas 27,5 − 0,0275 y 22,5 − 0,0225
    assert trade.pnl == D("-0.1000")
    assert trade.fees == D("0.1000")
    assert trade.avg_exit_price == D(100)
    assert trade.exits == ("parcial", "stop")
    assert pf.cash == D(200) + trade.pnl


def test_equity_marks_to_market_net_of_exit_fee() -> None:
    pf = Portfolio(D(200), D("0.001"))
    pf.buy("SOLUSDT", D(1), D(100), T, risk_amount=D(1))
    pf.mark("SOLUSDT", D(110))
    assert pf.equity() == D(200) - D("100.1") + D(110) * D("0.999")


def test_reservations_reduce_free_cash_not_equity() -> None:
    pf = Portfolio(D(200), D("0.001"))
    pf.reserve("SOLUSDT", D(80))
    assert pf.free_cash() == D(120) and pf.equity() == D(200)
    with pytest.raises(ValueError):
        pf.reserve("ETHUSDT", D(150))
