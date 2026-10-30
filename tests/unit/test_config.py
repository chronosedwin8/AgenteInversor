from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from bot.config import RiskConfig, Settings, load_config

CONFIG = Path("config")


def test_repo_config_loads() -> None:
    cfg = load_config(CONFIG)
    assert cfg.settings.universe.crypto.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert cfg.settings.data.intervals == ("15m", "1h", "4h", "1d")
    assert cfg.settings.data.history_months >= 12
    assert cfg.strategy.entry.order_type == "LIMIT_MAKER"


def test_bstocks_off_by_default_and_not_in_universe() -> None:
    cfg = load_config(CONFIG)
    assert cfg.settings.universe.bstocks.enabled is False
    assert cfg.settings.universe.active_symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def test_risk_thresholds_match_plan() -> None:
    risk = load_config(CONFIG).risk
    assert risk.initial_capital_usdt == Decimal(200)
    assert risk.kill_switch_capital == Decimal(180)
    assert risk.drawdown_alert_capital == Decimal(188)
    assert risk.risk_per_trade_pct == Decimal("0.5")
    assert risk.max_open_positions == 2
    assert risk.max_new_trades_per_day == 4


def test_risk_config_is_read_only() -> None:
    risk = load_config(CONFIG).risk
    with pytest.raises(ValidationError):
        risk.kill_switch_pct = Decimal(50)  # type: ignore[misc]


def _risk_dict() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load((CONFIG / "risk.yaml").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    "override",
    [
        {"reduced_risk_per_trade_pct": 1},  # mayor que el riesgo normal
        {"drawdown_alert_pct": 12},  # alerta después del kill switch
        {"risk_per_trade_pct": 5},  # fuera de rango
        {"leverage": "3x"},
        {"unknown_limit": 1},  # claves desconocidas no se aceptan en silencio
    ],
)
def test_incoherent_risk_config_is_rejected(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({**_risk_dict(), **override})


def test_invalid_timezone_rejected() -> None:
    raw = yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))
    raw["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)
