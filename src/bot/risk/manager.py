"""RiskManager: autoridad final sobre cada orden de entrada (regla 2). Fase 3: reglas completas.

Reglas (config/risk.yaml, PLAN.md "Gestión de riesgo"):
- riesgo por operación 0,5% del capital (0,25% si capital ≤ alerta de drawdown, 188 USD);
- tope 40% por posición y 80% de exposición total; 2 posiciones, máx. 1 en bStocks; 1 por activo;
- máx. 4 operaciones nuevas por día (día de Bogotá);
- pérdida diaria ≥ 2% del capital al inicio del día → sin entradas hasta las 00:00 de Bogotá;
- 3 pérdidas consecutivas → pausa de 24 h;
- kill switch: capital ≤ 90% del máximo alcanzado (o del inicial, según `kill_switch_basis`;
  nunca por debajo de 180 USD con 200 iniciales) → se cancelan órdenes, se cierran
  posiciones y no se aprueba nada más. Persiste entre reinicios; solo se desactiva con
  `reset_kill_switch` y la frase de confirmación.

El mismo objeto se usa en backtest, paper y live. Sus decisiones son deterministas: dependen solo
del estado, el capital y la hora que se le pasan.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from bot.config import RiskConfig
from bot.exchange.filters import SymbolFilters
from bot.risk.sizing import position_size

RESET_CONFIRMATION = "REINICIO MANUAL DEL KILL SWITCH"


class PortfolioView(Protocol):
    def equity(self) -> Decimal: ...
    def free_cash(self) -> Decimal: ...
    def has_exposure(self, symbol: str) -> bool: ...
    def exposure_count(self) -> int: ...
    def exposure_symbols(self) -> set[str]: ...
    def exposure_value(self) -> Decimal: ...


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
    symbol: str = ""
    token: str = ""  # firma HMAC del RiskManager que la emitió (solo si approved)


class RiskAuthorizationError(PermissionError):
    """Se intentó ejecutar una orden sin una aprobación válida del RiskManager (regla 2)."""


class RiskEventKind(StrEnum):
    KILL_SWITCH = "kill_switch"
    DAILY_LOSS_LIMIT = "limite_perdida_diaria"
    LOSS_STREAK_PAUSE = "pausa_por_rachas"
    DRAWDOWN_ALERT = "alerta_drawdown"
    KILL_SWITCH_RESET = "kill_switch_reiniciado"


@dataclass(frozen=True, slots=True)
class RiskEvent:
    kind: RiskEventKind
    at: datetime
    equity: Decimal
    detail: str = ""


@dataclass
class RiskState:
    day: date | None = None
    day_start_equity: Decimal | None = None
    trades_today: int = 0
    consecutive_losses: int = 0
    paused_until: datetime | None = None
    blocked_until: datetime | None = None  # por pérdida diaria
    peak_equity: Decimal | None = None
    drawdown_alert: bool = False
    kill_switch: bool = False
    kill_switch_at: datetime | None = None
    kill_switch_equity: Decimal | None = None
    events: list[RiskEvent] = field(default_factory=list)

    # ---- serialización (persistencia en PostgreSQL / archivos)
    def to_json(self) -> str:
        def conv(v: Any) -> Any:
            if isinstance(v, Decimal):
                return {"$dec": str(v)}
            if isinstance(v, datetime):
                return {"$dt": v.isoformat()}
            if isinstance(v, date):
                return {"$date": v.isoformat()}
            return v

        d = asdict(self)
        d.pop("events")  # los eventos se auditan aparte
        return json.dumps({k: conv(v) for k, v in d.items()})

    @classmethod
    def from_json(cls, raw: str) -> RiskState:
        def conv(v: Any) -> Any:
            if isinstance(v, dict):
                if "$dec" in v:
                    return Decimal(v["$dec"])
                if "$dt" in v:
                    return datetime.fromisoformat(v["$dt"])
                if "$date" in v:
                    return date.fromisoformat(v["$date"])
            return v

        return cls(**{k: conv(v) for k, v in json.loads(raw).items()})


class RiskManager:
    def __init__(
        self,
        cfg: RiskConfig,
        tz: ZoneInfo | None = None,
        state: RiskState | None = None,
        bstock_symbols: frozenset[str] = frozenset(),
    ):
        self.cfg = cfg
        self.tz = tz or ZoneInfo("America/Bogota")
        self.state = state or RiskState()
        self.bstock_symbols = bstock_symbols
        self._key = secrets.token_bytes(32)  # por proceso: una aprobación no sobrevive reinicios
        self._issued = 0
        self._consumed: set[str] = set()

    # ------------------------------------------------------------ autorización (regla 2)

    def _sign(self, symbol: str, qty: Decimal, stop: Decimal, serial: int) -> str:
        msg = f"{symbol}|{qty}|{stop}|{serial}".encode()
        return f"{serial}:" + hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def consume(self, decision: RiskDecision, intent: EntryIntent) -> None:
        """Valida y consume (un solo uso) una aprobación antes de enviar una orden real."""
        if not decision.approved or not decision.token or decision.symbol != intent.symbol:
            raise RiskAuthorizationError("orden sin aprobación del RiskManager")
        serial = int(decision.token.split(":", 1)[0])
        expected = self._sign(intent.symbol, decision.qty, intent.stop, serial)
        if not hmac.compare_digest(expected, decision.token):
            raise RiskAuthorizationError("aprobación no emitida por este RiskManager")
        if decision.token in self._consumed:
            raise RiskAuthorizationError("aprobación ya utilizada")
        if self.state.kill_switch:
            raise RiskAuthorizationError("kill switch activo")
        self._consumed.add(decision.token)

    # ------------------------------------------------------------ umbrales

    @property
    def kill_switch_active(self) -> bool:
        return self.state.kill_switch

    def kill_switch_capital(self) -> Decimal:
        base = self.cfg.initial_capital_usdt
        if self.cfg.kill_switch_basis == "peak" and self.state.peak_equity is not None:
            base = max(base, self.state.peak_equity)
        return base * (1 - self.cfg.kill_switch_pct / 100)

    def current_risk_pct(self, equity: Decimal) -> Decimal:
        if equity <= self.cfg.drawdown_alert_capital:
            return self.cfg.reduced_risk_per_trade_pct
        return self.cfg.risk_per_trade_pct

    def _next_local_midnight(self, now: datetime) -> datetime:
        local = now.astimezone(self.tz)
        nxt = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=self.tz)
        return nxt.astimezone(now.tzinfo)

    def _event(
        self, kind: RiskEventKind, now: datetime, equity: Decimal, detail: str = ""
    ) -> RiskEvent:
        ev = RiskEvent(kind, now, equity, detail)
        self.state.events.append(ev)
        return ev

    # ------------------------------------------------------------ actualización

    def update(self, now: datetime, equity: Decimal) -> list[RiskEvent]:
        """Llamar en cada valoración del capital (cada vela, cada precio en vivo)."""
        st = self.state
        events: list[RiskEvent] = []
        today = now.astimezone(self.tz).date()
        if st.day != today:
            st.day = today
            st.day_start_equity = equity
            st.trades_today = 0
        if st.peak_equity is None or equity > st.peak_equity:
            st.peak_equity = equity

        if not st.kill_switch and equity <= self.kill_switch_capital():
            st.kill_switch = True
            st.kill_switch_at = now
            st.kill_switch_equity = equity
            events.append(
                self._event(
                    RiskEventKind.KILL_SWITCH,
                    now,
                    equity,
                    f"capital {equity:.2f} ≤ {self.kill_switch_capital():.2f}",
                )
            )

        alert = equity <= self.cfg.drawdown_alert_capital
        if alert and not st.drawdown_alert:
            events.append(
                self._event(
                    RiskEventKind.DRAWDOWN_ALERT,
                    now,
                    equity,
                    f"riesgo → {self.cfg.reduced_risk_per_trade_pct}%",
                )
            )
        st.drawdown_alert = alert

        start = st.day_start_equity
        limit = self.cfg.daily_loss_limit_pct / 100
        if start is not None and equity <= start * (1 - limit):
            until = self._next_local_midnight(now)
            if st.blocked_until is None or st.blocked_until < until:
                st.blocked_until = until
                events.append(
                    self._event(
                        RiskEventKind.DAILY_LOSS_LIMIT,
                        now,
                        equity,
                        f"sin entradas hasta {until.isoformat()}",
                    )
                )
        return events

    def on_trade_closed(self, pnl: Decimal, now: datetime, equity: Decimal) -> list[RiskEvent]:
        st = self.state
        if pnl < 0:
            st.consecutive_losses += 1
            if st.consecutive_losses >= self.cfg.max_consecutive_losses:
                st.paused_until = now + timedelta(hours=self.cfg.consecutive_losses_pause_hours)
                st.consecutive_losses = 0
                return [
                    self._event(
                        RiskEventKind.LOSS_STREAK_PAUSE,
                        now,
                        equity,
                        f"pausa hasta {st.paused_until.isoformat()}",
                    )
                ]
        else:
            st.consecutive_losses = 0
        return []

    def reset_kill_switch(self, confirmation: str, now: datetime, equity: Decimal) -> RiskEvent:
        """Solo manual. El nuevo capital de referencia sigue siendo el inicial (o el máximo)."""
        if confirmation != RESET_CONFIRMATION:
            raise PermissionError("frase de confirmación incorrecta: el kill switch sigue activo")
        if self.cfg.kill_switch_basis == "peak":
            # Nuevo punto de partida: el máximo de referencia pasa a ser el capital actual.
            self.state.peak_equity = equity
        if equity <= self.kill_switch_capital():
            raise PermissionError("el capital sigue por debajo del umbral del kill switch")
        self.state.kill_switch = False
        return self._event(RiskEventKind.KILL_SWITCH_RESET, now, equity, "reinicio manual")

    # ------------------------------------------------------------ aprobación

    def approve_entry(
        self,
        intent: EntryIntent,
        portfolio: PortfolioView,
        filters: SymbolFilters,
        now: datetime,
    ) -> RiskDecision:
        cfg, st = self.cfg, self.state

        def no(reason: str) -> RiskDecision:
            return RiskDecision(False, Decimal(0), reason)

        if st.kill_switch:
            return no("kill_switch_activo")
        if st.paused_until is not None and now < st.paused_until:
            return no("pausa_por_perdidas_consecutivas")
        if st.blocked_until is not None and now < st.blocked_until:
            return no("limite_de_perdida_diaria")
        today = now.astimezone(self.tz).date()
        if st.day == today and st.trades_today >= cfg.max_new_trades_per_day:
            return no("maximo_de_operaciones_diarias")
        if not filters.is_trading:
            return no("par_no_operable")
        if portfolio.has_exposure(intent.symbol):
            return no("ya_hay_posicion_en_el_activo")
        if portfolio.exposure_count() >= cfg.max_open_positions:
            return no("maximo_de_posiciones")
        if intent.symbol in self.bstock_symbols:
            open_bstocks = len(portfolio.exposure_symbols() & self.bstock_symbols)
            if open_bstocks >= cfg.max_open_positions_bstocks:
                return no("maximo_de_posiciones_bstocks")

        equity = portfolio.equity()
        if equity <= self.kill_switch_capital():
            return no("capital_en_umbral_de_kill_switch")
        room = equity * cfg.max_total_exposure_pct / 100 - portfolio.exposure_value()
        if room <= 0:
            return no("exposicion_total_maxima")
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
            max_cost=min(portfolio.free_cash(), room * (1 + cfg.fee_rate)),
        )
        if not size.ok:
            return no(size.reason)
        if st.day != today:  # primera operación del día antes de cualquier valoración
            st.day, st.day_start_equity, st.trades_today = today, equity, 0
        st.trades_today += 1
        self._issued += 1
        token = self._sign(intent.symbol, size.qty, intent.stop, self._issued)
        return RiskDecision(True, size.qty, "ok", intent.symbol, token)
