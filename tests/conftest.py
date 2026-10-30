from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningún test hereda TRADING_MODE, claves o un .env del entorno del desarrollador."""
    for var in (
        "TRADING_MODE",
        "LIVE_CONFIRM",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(ROOT)


@pytest.fixture(scope="session")
def exchange_info() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "exchange_info.json").read_text())
    return data


@pytest.fixture(scope="session")
def symbol_info(exchange_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["symbol"]: s for s in exchange_info["symbols"]}
