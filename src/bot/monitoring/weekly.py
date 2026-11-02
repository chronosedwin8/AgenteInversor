"""Informe semanal por correo: detalle por día y total de la semana de cada cuenta (paper,
testnet). Se envía el domingo a las 23:30 hora Bogotá, una sola vez por semana.

Las cuentas son simuladas (paper) o de pruebas (testnet): el informe lo dice explícitamente.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import smtplib
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.log import get_logger

log = get_logger(__name__)
SEND_AT = time(23, 30)  # domingo, hora Bogotá
DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
ACCOUNT_NAMES = {
    "paper": "Paper trading (precios reales, órdenes simuladas)",
    "testnet": "Spot Testnet (órdenes reales en el entorno de pruebas)",
}


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    entry: Decimal
    exit: Decimal
    pnl: Decimal
    r: Decimal
    exits: str


@dataclass
class AccountData:
    account: str
    initial_capital: Decimal
    trades: list[ClosedTrade] = field(default_factory=list)
    equity: list[tuple[datetime, Decimal]] = field(default_factory=list)  # muestras por vela
    events: list[tuple[datetime, str, dict[str, Any]]] = field(default_factory=list)
    open_positions: list[str] = field(default_factory=list)
    kill_switch: bool = False
    paused: str | None = None
    started_at: datetime | None = None


# ------------------------------------------------------------------ cálculo


def week_bounds(any_day: date) -> tuple[date, date]:
    monday = any_day - timedelta(days=any_day.weekday())
    return monday, monday + timedelta(days=6)


def _equity_at(acc: AccountData, t: datetime) -> Decimal:
    """Capital a la hora t: última muestra registrada; si no hay, capital inicial + realizado."""
    best: Decimal | None = None
    for at, v in acc.equity:
        if at <= t:
            best = v
        else:
            break
    if best is not None:
        return best
    return acc.initial_capital + sum((x.pnl for x in acc.trades if x.closed_at <= t), Decimal(0))


@dataclass
class DayRow:
    day: date
    start: Decimal
    end: Decimal
    trades: list[ClosedTrade]
    signals: int
    rejected: int
    interruption_min: float

    @property
    def change(self) -> Decimal:
        return self.end - self.start

    @property
    def pct(self) -> Decimal:
        return self.change / self.start * 100 if self.start else Decimal(0)


def daily_rows(acc: AccountData, monday: date, tz: ZoneInfo, now: datetime) -> list[DayRow]:
    acc.equity.sort(key=lambda x: x[0])
    rows = []
    for i in range(7):
        d = monday + timedelta(days=i)
        start = datetime.combine(d, time(0), tzinfo=tz).astimezone(UTC)
        end = min(start + timedelta(days=1), now)
        if start > now:
            break
        in_day = [x for x in acc.trades if start <= x.closed_at < end]
        ev = [e for e in acc.events if start <= e[0] < end]
        interruption = sum(float(e[2].get("minutes", 0)) for e in ev if e[1] == "interruption")
        rows.append(
            DayRow(
                d,
                _equity_at(acc, start),
                _equity_at(acc, end),
                in_day,
                sum(1 for e in ev if e[1] in ("order_placed", "position_opened")),
                sum(1 for e in ev if e[1] == "signal_rejected"),
                interruption,
            )
        )
    return rows


def _verdict(x: Decimal) -> str:
    return "GANANCIA" if x > 0 else ("PÉRDIDA" if x < 0 else "SIN CAMBIOS")


def render_weekly(
    accounts: Sequence[AccountData], monday: date, tz: ZoneInfo, now: datetime
) -> tuple[str, str]:
    """(asunto, cuerpo en texto) del informe."""
    sunday = monday + timedelta(days=6)
    lines = [
        f"Informe semanal del bot de trading — {monday:%d/%m/%Y} a {sunday:%d/%m/%Y} (hora Bogotá)",
        "",
        "IMPORTANTE: son cuentas simuladas o de pruebas. NO es dinero real. Estrategia H1 "
        "experimental: la auditoría no demostró una ventaja reproducible.",
        "",
    ]
    summary = []
    for acc in accounts:
        rows = daily_rows(acc, monday, tz, now)
        if not rows:
            continue
        name = ACCOUNT_NAMES.get(acc.account, acc.account)
        lines += [
            f"== {name} — capital virtual inicial {acc.initial_capital} USDT ==",
            "",
            f"{'Día':<10} {'Capital inicio':>14} {'Capital fin':>12} {'Resultado':>18} "
            f"{'Ops':>4} {'Gan.':>4} {'Señales':>8} {'Rech.':>6} {'Sin servicio':>13}",
        ]
        for r in rows:
            wins = sum(1 for x in r.trades if x.pnl > 0)
            res = f"{r.change:+.2f} ({r.pct:+.2f}%)"
            off = f"{r.interruption_min:.0f} min" if r.interruption_min else "-"
            lines.append(
                f"{DAYS[r.day.weekday()]} {r.day:%d/%m} {r.start:>14.2f} {r.end:>12.2f} "
                f"{res:>18} {len(r.trades):>4} {wins:>4} {r.signals:>8} {r.rejected:>6} {off:>13}"
            )
        w_start, w_end = rows[0].start, rows[-1].end
        w_change = w_end - w_start
        w_pct = w_change / w_start * 100 if w_start else Decimal(0)
        w_trades = [x for r in rows for x in r.trades]
        win_days = sum(1 for r in rows if r.change > 0)
        loss_days = sum(1 for r in rows if r.change < 0)
        total = _equity_at(acc, now) - acc.initial_capital
        lines += [
            "",
            f"Semana: {w_start:.2f} → {w_end:.2f} USDT = {w_change:+.2f} USDT ({w_pct:+.2f}%) "
            f"→ {_verdict(w_change)}",
            f"Días con ganancia: {win_days}; con pérdida: {loss_days}; sin cambios: "
            f"{len(rows) - win_days - loss_days}. Operaciones cerradas: {len(w_trades)} "
            f"({sum(1 for x in w_trades if x.pnl > 0)} ganadoras).",
            f"Acumulado desde el inicio"
            f"{f' ({acc.started_at:%d/%m/%Y})' if acc.started_at else ''}: {total:+.2f} USDT "
            f"({total / acc.initial_capital * 100:+.2f}%).",
            f"Posiciones abiertas ahora: {', '.join(acc.open_positions) or 'ninguna'}. "
            f"Kill switch: {'ACTIVO' if acc.kill_switch else 'inactivo'}."
            f"{f' En pausa: {acc.paused}.' if acc.paused else ''}",
        ]
        if w_trades:
            lines += ["", "Operaciones de la semana:"]
            for x in w_trades:
                lines.append(
                    f"  {x.symbol:<8} {x.opened_at.astimezone(tz):%d/%m %H:%M} → "
                    f"{x.closed_at.astimezone(tz):%d/%m %H:%M}  entrada {x.entry:.4f}  "
                    f"salida {x.exit:.4f}  {x.pnl:+.2f} USDT ({x.r:+.2f}R)  {x.exits}"
                )
        lines.append("")
        summary.append(f"{acc.account} {w_change:+.2f} USDT")
    lines += ["Consultas: python -m bot.main paper-status (en el servidor)."]
    subject = f"Bot trading — semana {monday:%d/%m}–{sunday:%d/%m}: " + (
        "; ".join(summary) or "sin actividad"
    )
    return subject, "\n".join(lines)


# ------------------------------------------------------------------ datos


async def load_account(store: Any, account: str, initial: Decimal) -> AccountData | None:
    saved = await store.load(account)
    if saved is None:
        return None
    _, raw = saved
    state = json.loads(raw)
    acc = AccountData(account, initial)
    for t in await store.trades(account):
        sym, opened, closed, _qty, entry, exit_px, pnl, r, exits = t
        acc.trades.append(
            ClosedTrade(
                sym,
                opened,
                closed,
                Decimal(entry),
                Decimal(exit_px),
                Decimal(pnl),
                Decimal(r),
                exits,
            )
        )
    for at, kind, detail in await store.recent_events(account, 100_000):
        acc.events.append((at, kind, detail))
        if kind == "bar_processed" and detail.get("equity"):
            acc.equity.append((at, Decimal(detail["equity"])))
        if acc.started_at is None or at < acc.started_at:
            acc.started_at = at  # primer evento registrado de la cuenta
    acc.events.sort(key=lambda e: e[0])
    acc.open_positions = sorted(state.get("open") or state.get("positions") or {})
    risk = json.loads(state["risk"]) if isinstance(state.get("risk"), str) else {}
    acc.kill_switch = bool(risk.get("kill_switch"))
    acc.paused = state.get("paused_reason")
    return acc


# ------------------------------------------------------------------ envío


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    user: str
    password: str
    to: str

    @property
    def complete(self) -> bool:
        return bool(self.host and self.user and self.password and self.to)


def send_mail(cfg: MailConfig, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.user
    msg["To"] = cfg.to
    msg.set_content(body)
    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(cfg.user, cfg.password)
        s.send_message(msg)


def _save(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def is_due(now_local: datetime, last_sent_week: str | None) -> tuple[bool, str]:
    """¿Toca enviar? Domingo desde las 23:30 hora Bogotá, una vez por semana ISO."""
    year, week, _ = now_local.isocalendar()
    key = f"{year}-W{week:02d}"
    due = now_local.weekday() == 6 and now_local.time() >= SEND_AT and last_sent_week != key
    return due, key


async def weekly_report_loop(
    stop: asyncio.Event,
    store: Any,
    accounts: Sequence[str],
    initial: Decimal,
    tz: ZoneInfo,
    mail: MailConfig,
    out_dir: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    while not stop.is_set():
        try:
            now = clock()
            last = await store.last_report_week()
            due, key = is_due(now.astimezone(tz), last)
            if due:
                monday, _ = week_bounds(now.astimezone(tz).date())
                data = [a for a in [await load_account(store, x, initial) for x in accounts] if a]
                subject, body = render_weekly(data, monday, tz, now)
                await asyncio.to_thread(_save, out_dir / f"informe-semanal-{key}.txt", body)
                if mail.complete:
                    await asyncio.to_thread(send_mail, mail, subject, body)
                    log.info("weekly_report_sent", week=key, to=mail.to)
                else:
                    log.warning(
                        "weekly_report_not_emailed",
                        week=key,
                        reason="falta configuración SMTP en .env",
                    )
                await store.mark_report_sent(key, now, mail.complete)
        except Exception as exc:
            log.error("weekly_report_failed", alert=True, error=repr(exc))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=600)
