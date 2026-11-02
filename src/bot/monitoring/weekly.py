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
from html import escape
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


def _overlap_min(detail: dict[str, Any], start: datetime, end: datetime) -> float:
    """Minutos de una interrupción [from, to] que caen dentro del día [start, end)."""
    try:
        a, b = datetime.fromisoformat(detail["from"]), datetime.fromisoformat(detail["to"])
    except (KeyError, ValueError):
        return 0.0
    return max((min(b, end) - max(a, start)).total_seconds(), 0.0) / 60


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
        interruption = sum(
            _overlap_min(e[2], start, end) for e in acc.events if e[1] == "interruption"
        )
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
    return "Ganancia" if x > 0 else ("Pérdida" if x < 0 else "Sin cambios")


MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
DAY_NAMES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
EXIT_NAMES = {
    "stop": "Stop de pérdida",
    "stop_trailing": "Stop dinámico (asegura ganancias)",
    "stop_breakeven": "Stop en precio de entrada",
    "parcial": "Venta parcial",
    "kill_switch": "Kill switch (apagado de emergencia)",
    "stop_exchange": "Stop ejecutado en Binance",
    "stop_rechazado_cerrada": "Cierre inmediato (stop rechazado)",
    "huérfana_sin_stop": "Cierre de seguridad tras reinicio",
    "fin_de_datos": "Cierre al final del periodo",
}
GREEN, RED, GRAY = "#1e8e3e", "#d93025", "#5f6368"


def money(x: Decimal | float, sign: bool = False) -> str:
    """Formato colombiano: 1.234,56."""
    s = f"{x:+,.2f}" if sign else f"{x:,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def pct(x: Decimal | float) -> str:
    return money(x, sign=True) + " %"


def _color(x: Decimal) -> str:
    return GREEN if x > 0 else (RED if x < 0 else GRAY)


def _duration(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h} h {m} min" if h else f"{m} min"


def _day_label(d: date) -> str:
    return f"{DAY_NAMES[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def _exits(raw: str) -> str:
    parts = [EXIT_NAMES.get(p, p) for p in raw.split("|") if p]
    return " + ".join(parts)


def _n(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _observations(r: DayRow) -> str:
    notes = []
    if r.trades:
        wins = sum(1 for x in r.trades if x.pnl > 0)
        notes.append(f"{_n(len(r.trades), 'operación cerrada', 'operaciones cerradas')} "
                     f"({_n(wins, 'ganadora', 'ganadoras')})")  # fmt: skip
    if r.signals:
        notes.append(_n(r.signals, "compra nueva", "compras nuevas"))
    if r.rejected:
        notes.append(_n(r.rejected, "señal bloqueada por el control de riesgo",
                        "señales bloqueadas por el control de riesgo"))  # fmt: skip
    if r.interruption_min >= 1:
        notes.append(f"bot sin servicio {_duration(r.interruption_min)}")
    if not notes:
        return "Sin operaciones (no hubo señal de compra)"
    text = "; ".join(notes)
    return text[0].upper() + text[1:]


@dataclass
class AccountWeek:
    acc: AccountData
    rows: list[DayRow]
    start: Decimal
    end: Decimal
    trades: list[ClosedTrade]
    total: Decimal

    @property
    def change(self) -> Decimal:
        return self.end - self.start

    @property
    def change_pct(self) -> Decimal:
        return self.change / self.start * 100 if self.start else Decimal(0)

    @property
    def wins(self) -> int:
        return sum(1 for x in self.trades if x.pnl > 0)


def summarize(acc: AccountData, monday: date, tz: ZoneInfo, now: datetime) -> AccountWeek | None:
    rows = daily_rows(acc, monday, tz, now)
    if not rows:
        return None
    trades = [x for r in rows for x in r.trades]
    total = _equity_at(acc, now) - acc.initial_capital
    return AccountWeek(acc, rows, rows[0].start, rows[-1].end, trades, total)


# ------------------------------------------------------------------ texto


def _render_text(weeks: Sequence[AccountWeek], monday: date, tz: ZoneInfo) -> str:
    sunday = monday + timedelta(days=6)
    out = [
        f"INFORME SEMANAL DEL BOT DE TRADING — {_day_label(monday)} a {_day_label(sunday)} "
        f"{sunday.year} (hora Bogotá)",
        "",
        "Cuentas de PRUEBA: no es dinero real.",
        "",
    ]
    for w in weeks:
        name = ACCOUNT_NAMES.get(w.acc.account, w.acc.account)
        out += [
            f"■ {name}",
            f"  Resultado de la semana: {money(w.change, True)} USDT ({pct(w.change_pct)}) → "
            f"{_verdict(w.change).upper()}",
            f"  Capital: {money(w.start)} → {money(w.end)} USDT",
            f"  Acumulado desde el inicio: {money(w.total, True)} USDT",
            f"  Operaciones cerradas: {len(w.trades)} ({w.wins} ganadoras)",
            "",
            "  Día a día:",
        ]
        for r in w.rows:
            out.append(f"   {_day_label(r.day):<18} {money(r.change, True):>8} USDT  "
                       f"(capital {money(r.end)})  {_observations(r)}")  # fmt: skip
        if w.trades:
            out += ["", "  Operaciones:"]
            for x in w.trades:
                out.append(f"   {x.symbol} {x.opened_at.astimezone(tz):%d/%m %H:%M} → "
                           f"{x.closed_at.astimezone(tz):%d/%m %H:%M}: "
                           f"{money(x.pnl, True)} USDT — {_exits(x.exits)}")  # fmt: skip
        state = ", ".join(w.acc.open_positions) or "ninguna"
        out += ["", f"  Posiciones abiertas: {state}. Kill switch: "
                f"{'ACTIVO' if w.acc.kill_switch else 'inactivo'}.", ""]  # fmt: skip
    return "\n".join(out)


# ------------------------------------------------------------------ HTML


def _td(content: str, align: str = "left", color: str = "#202124", bold: bool = False,
        nowrap: bool = False) -> str:  # fmt: skip
    weight = "600" if bold else "400"
    wrap = "white-space:nowrap;" if nowrap else ""
    style = (f"padding:8px 10px;border-bottom:1px solid #eceff1;text-align:{align};"
             f"color:{color};font-weight:{weight};font-size:14px;{wrap}")  # fmt: skip
    return f'<td style="{style}">{content}</td>'


def _th(content: str, align: str = "left") -> str:
    return (f'<th style="padding:8px 10px;background:#f1f3f4;text-align:{align};color:#5f6368;'
            f'font-size:12px;font-weight:600;text-transform:uppercase">{content}</th>')  # fmt: skip


def _account_html(w: AccountWeek, tz: ZoneInfo) -> str:
    name = ACCOUNT_NAMES.get(w.acc.account, w.acc.account)
    c = _color(w.change)
    days_up = sum(1 for r in w.rows if r.change > 0)
    days_down = sum(1 for r in w.rows if r.change < 0)
    days_flat = len(w.rows) - days_up - days_down
    card = f"""
<div style="border:1px solid #dadce0;border-radius:10px;padding:18px 20px;margin:18px 0">
  <div style="font-size:15px;font-weight:600;color:#3c4043">{escape(name)}</div>
  <div style="font-size:30px;font-weight:700;color:{c};margin:6px 0 2px">
    {money(w.change, True)} USDT <span style="font-size:16px">({pct(w.change_pct)})</span></div>
  <div style="font-size:16px;font-weight:600;color:{c}">{_verdict(w.change)} en la semana</div>
  <table style="margin-top:12px;font-size:14px;color:#3c4043" cellpadding="0" cellspacing="0">
    <tr><td style="padding:2px 16px 2px 0">Capital</td>
        <td><b>{money(w.start)} → {money(w.end)} USDT</b></td></tr>
    <tr><td style="padding:2px 16px 2px 0">Acumulado desde el inicio</td>
        <td><b style="color:{_color(w.total)}">{money(w.total, True)} USDT</b>
        {f"(desde el {w.acc.started_at.astimezone(tz):%d/%m/%Y})" if w.acc.started_at else ""}
        </td></tr>
    <tr><td style="padding:2px 16px 2px 0">Operaciones cerradas</td>
        <td><b>{len(w.trades)}</b> ({_n(w.wins, "ganadora", "ganadoras")})</td></tr>
    <tr><td style="padding:2px 16px 2px 0">Días</td>
        <td>{days_up} con ganancia · {days_down} con pérdida · {days_flat} sin cambios</td></tr>
  </table>
</div>"""
    rows = "".join(
        "<tr>" + _td(_day_label(r.day), bold=True, nowrap=True)
        + _td(f"{money(r.change, True)} USDT", "right", _color(r.change), True, nowrap=True)
        + _td(money(r.end), "right", nowrap=True) + _td(escape(_observations(r))) + "</tr>"
        for r in w.rows
    )  # fmt: skip
    daily = f"""
<h3 style="font-size:16px;color:#202124;margin:22px 0 8px">Día a día</h3>
<table style="width:100%;border-collapse:collapse" cellpadding="0" cellspacing="0">
  <tr>{_th("Día")}{_th("Resultado", "right")}{_th("Capital al cierre", "right")}
      {_th("Qué pasó")}</tr>{rows}
</table>"""
    trades = ""
    if w.trades:
        trs = "".join(
            "<tr>" + _td(x.symbol.replace("USDT", ""), bold=True)
            + _td(f"{x.opened_at.astimezone(tz):%d/%m %H:%M}", nowrap=True)
            + _td(f"{x.closed_at.astimezone(tz):%d/%m %H:%M}", nowrap=True)
            + _td(f"{money(x.pnl, True)} USDT", "right", _color(x.pnl), True, nowrap=True)
            + _td(escape(_exits(x.exits))) + "</tr>"
            for x in w.trades
        )  # fmt: skip
        trades = f"""
<h3 style="font-size:16px;color:#202124;margin:22px 0 8px">Operaciones de la semana</h3>
<table style="width:100%;border-collapse:collapse" cellpadding="0" cellspacing="0">
  <tr>{_th("Moneda")}{_th("Compra")}{_th("Venta")}{_th("Resultado", "right")}
      {_th("Motivo de la venta")}</tr>{trs}
</table>"""
    status_open = ", ".join(p.replace("USDT", "") for p in w.acc.open_positions) or "ninguna"
    ks = ('<b style="color:#d93025">ACTIVO: el bot se detuvo por pérdidas</b>'
          if w.acc.kill_switch else "inactivo (normal)")  # fmt: skip
    pause = f" · En pausa: {escape(w.acc.paused)}" if w.acc.paused else ""
    status = (f'<p style="font-size:14px;color:#3c4043;margin:14px 0 0">Posiciones abiertas '
              f"ahora: <b>{status_open}</b> · Kill switch: {ks}{pause}</p>")  # fmt: skip
    return card + daily + trades + status


def _render_html(weeks: Sequence[AccountWeek], monday: date, tz: ZoneInfo) -> str:
    sunday = monday + timedelta(days=6)
    body = "".join(_account_html(w, tz) for w in weeks)  # fmt: skip
    if not weeks:
        body = '<p style="font-size:14px">Sin actividad registrada esta semana.</p>'
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#f8f9fa;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:720px;margin:0 auto;background:#ffffff;padding:24px 28px">
  <div style="font-size:22px;font-weight:700;color:#202124">Informe semanal del bot de trading</div>
  <div style="font-size:15px;color:#5f6368;margin-top:4px">
    {_day_label(monday)} – {_day_label(sunday)} {sunday.year} · hora Bogotá</div>
  <div style="background:#fef7e0;border-left:4px solid #f9ab00;padding:10px 14px;margin:16px 0;
              font-size:14px;color:#3c4043">
    <b>Cuentas de prueba: no es dinero real.</b> La estrategia (H1) es experimental: aún no se ha
    demostrado que gane de forma sostenida.</div>
  {body}
  <div style="border-top:1px solid #dadce0;margin-top:28px;padding-top:12px;font-size:12px;
              color:#80868b;line-height:1.5">
    <b>Cómo leer este informe.</b> <i>Paper trading</i>: el bot opera con precios reales pero
    las compras son simuladas. <i>Testnet</i>: órdenes reales en el entorno de pruebas de Binance,
    sin dinero real. <i>Capital al cierre</i>: valor de la cuenta (efectivo + monedas, descontando
    comisiones). <i>Kill switch</i>: apagado automático si la cuenta cae un 10 % desde su máximo.
    Un día "sin señales" es normal: la estrategia solo compra cuando hay tendencia y ruptura.
  </div>
</div></body></html>"""


def render_weekly(
    accounts: Sequence[AccountData], monday: date, tz: ZoneInfo, now: datetime
) -> tuple[str, str, str]:
    """(asunto, texto plano, HTML) del informe."""
    sunday = monday + timedelta(days=6)
    weeks = [w for w in (summarize(a, monday, tz, now) for a in accounts) if w is not None]
    total = sum((w.change for w in weeks), Decimal(0))
    parts = [f"{'Paper' if w.acc.account == 'paper' else 'Testnet'} {money(w.change, True)} USDT"
             for w in weeks]  # fmt: skip
    subject = (f"Bot de trading · semana {monday.day}–{sunday.day} {MONTHS[sunday.month - 1]}: "
               f"{_verdict(total).lower()} ({'; '.join(parts) or 'sin actividad'})")  # fmt: skip
    return subject, _render_text(weeks, monday, tz), _render_html(weeks, monday, tz)


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


def send_mail(cfg: MailConfig, subject: str, body: str, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Bot de trading <{cfg.user}>"
    msg["To"] = cfg.to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
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
                subject, body, html = render_weekly(data, monday, tz, now)
                await asyncio.to_thread(_save, out_dir / f"informe-semanal-{key}.html", html)
                if mail.complete:
                    await asyncio.to_thread(send_mail, mail, subject, body, html)
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
