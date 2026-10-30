"""Regla 1: solo TRADING_MODE=live + LIVE_CONFIRM=yes + claves de producción permiten live."""

from __future__ import annotations

import pytest

from bot.config import EnvSettings
from bot.modes import (
    LiveTradingNotAllowedError,
    TradingMode,
    live_preconditions,
    require_live,
    resolve_mode,
)

KEYS = {"binance_api_key": "k" * 16, "binance_api_secret": "s" * 16}


def env(**kw: str) -> EnvSettings:
    return EnvSettings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_default_is_paper() -> None:
    res = resolve_mode(env())
    assert res.mode is TradingMode.PAPER
    assert not res.degraded


def test_backtest_and_paper_pass_through() -> None:
    assert resolve_mode(env(trading_mode="backtest")).mode is TradingMode.BACKTEST
    assert resolve_mode(env(trading_mode="paper")).mode is TradingMode.PAPER


def test_invalid_mode_falls_back_to_paper_with_warning() -> None:
    res = resolve_mode(env(trading_mode="real"))
    assert res.mode is TradingMode.PAPER
    assert res.degraded


def test_full_live_conditions_allow_live() -> None:
    e = env(trading_mode="live", live_confirm="yes", **KEYS)
    assert resolve_mode(e).mode is TradingMode.LIVE
    assert live_preconditions(e) == []
    require_live(e)  # no lanza


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trading_mode": "live"},  # sin confirmación ni claves
        {"trading_mode": "live", "live_confirm": "yes"},  # sin claves
        {"trading_mode": "live", **KEYS},  # sin confirmación
        {"trading_mode": "live", "live_confirm": "YES", **KEYS},  # confirmación no exacta
        {"trading_mode": "live", "live_confirm": "yes ", **KEYS},
        {"trading_mode": "live", "live_confirm": "true", **KEYS},
        {"trading_mode": "live", "live_confirm": "yes", "binance_api_key": "k"},  # falta secret
        {"trading_mode": "live", "live_confirm": "yes",
         "binance_api_key": "  ", "binance_api_secret": "s"},  # clave en blanco
        {"trading_mode": "paper", "live_confirm": "yes", **KEYS},  # modo paper
        # Las claves de testnet NO habilitan producción.
        {
            "trading_mode": "live",
            "live_confirm": "yes",
            "binance_testnet_api_key": "k",
            "binance_testnet_api_secret": "s",
        },
    ],
)  # fmt: skip
def test_live_is_refused_unless_every_condition_holds(kwargs: dict[str, str]) -> None:
    e = env(**kwargs)
    assert resolve_mode(e).mode is not TradingMode.LIVE
    with pytest.raises(LiveTradingNotAllowedError):
        require_live(e)


def test_refused_live_degrades_to_paper_and_warns() -> None:
    res = resolve_mode(env(trading_mode="live"))
    assert res.mode is TradingMode.PAPER
    assert res.degraded
    assert any("LIVE_CONFIRM" in w for w in res.warnings)


def test_secrets_are_not_exposed_in_repr() -> None:
    e = env(**KEYS)
    assert "kkkk" not in repr(e)
    assert "ssss" not in str(e.model_dump())
