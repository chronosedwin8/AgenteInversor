"""Carga de configuración: variables de entorno (.env) + YAML en config/.

Los secretos solo vienen del entorno (SecretStr, nunca se imprimen). Los YAML se validan con
modelos inmutables; `RiskConfig` en particular no se puede modificar en ejecución.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INTERVALS = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"]


class EnvSettings(BaseSettings):
    """Variables de entorno. Ver .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    trading_mode: str = "paper"
    live_confirm: str = ""

    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None
    binance_testnet_api_key: SecretStr | None = None
    binance_testnet_api_secret: SecretStr | None = None

    anthropic_api_key: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    database_url: SecretStr | None = None
    config_dir: Path = Path("config")
    log_format: Literal["json", "console"] = "json"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------- settings.yaml


class TimeWindow(_Frozen):
    start: time
    end: time


class CryptoUniverse(_Frozen):
    symbols: tuple[str, ...]
    entry_window_local: TimeWindow


class BStocksUniverse(_Frozen):
    enabled: bool = False
    symbols: tuple[str, ...] = ()
    calendar: str = "XNYS"
    open_offset_minutes: int = 15
    close_offset_minutes: int = 30
    force_exit_minutes_before_close: int = 10


class Universe(_Frozen):
    crypto: CryptoUniverse
    bstocks: BStocksUniverse

    @property
    def active_symbols(self) -> tuple[str, ...]:
        extra = self.bstocks.symbols if self.bstocks.enabled else ()
        return self.crypto.symbols + extra


class DataConfig(_Frozen):
    intervals: tuple[INTERVALS, ...]
    history_months: int = Field(ge=12)
    cache_dir: Path
    rest_base_url: str
    ws_stream_url: str
    vision_base_url: str
    rest_timeout_ms: int = 10000
    exchange_info_refresh_minutes: int = 60


class RuntimeConfig(_Frozen):
    heartbeat_seconds: int = 60
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class Settings(_Frozen):
    timezone: str
    universe: Universe
    data: DataConfig
    runtime: RuntimeConfig

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"zona horaria desconocida: {v}") from None
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


# ---------------------------------------------------------------- strategy.yaml


class RegimeParams(_Frozen):
    ema_fast: int
    ema_slow: int
    adx_period: int
    adx_min: float


class PullbackParams(_Frozen):
    lookback_candles: int
    ema_touch: int
    ema_floor: int
    touch_atr_tolerance: float
    rsi_period: int
    rsi_min: float
    rsi_max: float


class MacdParams(_Frozen):
    fast: int
    slow: int
    signal: int


class TriggerParams(_Frozen):
    volume_ma_period: int
    volume_multiplier: float
    macd: MacdParams


class QualityParams(_Frozen):
    atr_period: int
    atr_min_pct: float
    atr_max_pct: float
    max_spread_pct: float


class EntryParams(_Frozen):
    order_type: Literal["LIMIT_MAKER"]
    cancel_after_candles: int


class ExitParams(_Frozen):
    stop_atr_multiple: float
    stop_max_pct: float
    partial_take_r: float
    partial_fraction: float
    trailing_atr_multiple: float
    exit_on_close_below_ema: int
    time_stop_candles: int
    time_stop_min_r: float
    max_holding_hours: int


class CostParams(_Frozen):
    fee_rate: Decimal
    slippage_rate: Decimal


class Timeframes(_Frozen):
    regime: INTERVALS
    signal: INTERVALS


class StrategyConfig(_Frozen):
    timeframes: Timeframes
    regime: RegimeParams
    pullback: PullbackParams
    trigger: TriggerParams
    quality: QualityParams
    entry: EntryParams
    exit: ExitParams
    costs: CostParams


# ---------------------------------------------------------------- risk.yaml


class RiskConfig(_Frozen):
    """Límites de riesgo. Inmutable: cambiar un límite exige reiniciar el bot."""

    initial_capital_usdt: Decimal = Field(gt=0)
    risk_per_trade_pct: Decimal = Field(gt=0, le=2)
    max_position_pct: Decimal = Field(gt=0, le=100)
    min_position_notional_multiple: Decimal = Field(ge=1)
    min_position_margin_pct: Decimal = Field(ge=0)
    max_open_positions: int = Field(ge=1)
    max_open_positions_bstocks: int = Field(ge=0)
    max_new_trades_per_day: int = Field(ge=1)
    daily_loss_limit_pct: Decimal = Field(gt=0)
    max_consecutive_losses: int = Field(ge=1)
    consecutive_losses_pause_hours: int = Field(ge=0)
    drawdown_alert_pct: Decimal = Field(gt=0)
    reduced_risk_per_trade_pct: Decimal = Field(gt=0)
    kill_switch_pct: Decimal = Field(gt=0, lt=100)
    leverage: Literal["none"]
    fee_rate: Decimal = Field(ge=0, lt=Decimal("0.01"))

    @model_validator(mode="after")
    def _coherent(self) -> RiskConfig:
        if self.reduced_risk_per_trade_pct > self.risk_per_trade_pct:
            raise ValueError("reduced_risk_per_trade_pct no puede superar risk_per_trade_pct")
        if self.drawdown_alert_pct >= self.kill_switch_pct:
            raise ValueError("drawdown_alert_pct debe ser menor que kill_switch_pct")
        if self.max_open_positions_bstocks > self.max_open_positions:
            raise ValueError("max_open_positions_bstocks no puede superar max_open_positions")
        return self

    @property
    def kill_switch_capital(self) -> Decimal:
        """Capital al que se activa el kill switch (180 USD con 200 iniciales y 10%)."""
        return self.initial_capital_usdt * (1 - self.kill_switch_pct / 100)

    @property
    def drawdown_alert_capital(self) -> Decimal:
        return self.initial_capital_usdt * (1 - self.drawdown_alert_pct / 100)


# ---------------------------------------------------------------- carga


class AppConfig(_Frozen):
    settings: Settings
    strategy: StrategyConfig
    risk: RiskConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} debe contener un mapa YAML")
    return data


def load_config(config_dir: Path | None = None) -> AppConfig:
    cfg_dir = config_dir or EnvSettings().config_dir
    return AppConfig(
        settings=Settings.model_validate(_read_yaml(cfg_dir / "settings.yaml")),
        strategy=StrategyConfig.model_validate(_read_yaml(cfg_dir / "strategy.yaml")),
        risk=RiskConfig.model_validate(_read_yaml(cfg_dir / "risk.yaml")),
    )
