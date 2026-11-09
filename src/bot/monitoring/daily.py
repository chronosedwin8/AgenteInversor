"""Informe diario por correo: qué hizo el bot hoy y, si no operó, por qué.

Se envía a las 23:30 hora Bogotá (ya cerró la última vela de 4 h del día, 23:00). Reutiliza los
datos y el estilo del informe semanal; las velas se evalúan con la MISMA estrategia que opera, solo
para explicar la decisión: este módulo no envía órdenes ni modifica el estado de las cuentas.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.data.klines import interval_delta
from bot.execution.paper import REGIME_TF, SIGNAL_TF
from bot.log import get_logger
from bot.monitoring.weekly import (
    ACCOUNT_NAMES,
    GREEN,
    RED,
    AccountData,
    ClosedTrade,
    DayRow,
    MailConfig,
    _color,
    _day_label,
    _duration,
    _equity_at,
    _exits,
    _n,
    _save,
    _td,
    _th,
    _verdict,
    daily_rows,
    load_account,
    money,
    pct,
    send_mail,
    week_bounds,
)
from bot.strategy.features import Bar, build_bars
from bot.strategy.hypotheses import H1Breakout, _regime_up

log = get_logger(__name__)
SEND_AT = time(23, 30)  # hora Bogotá
# Una vela recién cerrada puede no estar procesada aún: no se cuenta como ausente.
PROCESSING_GRACE = timedelta(minutes=5)
INCIDENT_NAMES = {
    "emergency": "emergencia de ejecución",
    "stop_failed_closed": "stop rechazado (posición cerrada por seguridad)",
    "paused": "bot en pausa",
    "exchange_unavailable": "Binance no disponible",
}
REASON_NAMES = {
    "regimen_no_alcista": ("con tendencia bajista", "con tendencia bajista"),
    "sin_ruptura": ("con tendencia alcista pero sin ruptura", "con tendencia alcista sin ruptura"),
    "indicadores_no_listos": ("sin indicadores listos", "sin indicadores listos"),
    "ok": ("con señal de compra", "con señal de compra"),
}


# ------------------------------------------------------------------ cálculo


def day_window(day: date, tz: ZoneInfo, now: datetime) -> tuple[datetime, datetime]:
    """Día de Bogotá en UTC, recortado a `now` si aún no ha terminado."""
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
    return start, min(start + timedelta(days=1), now)


def expected_closes(lo: datetime, hi: datetime) -> int:
    """Velas de la temporalidad de señal que cierran en [lo, hi)."""
    step = interval_delta(SIGNAL_TF)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    t = epoch + ((lo - epoch) // step) * step + step - timedelta(milliseconds=1)
    if t < lo:
        t += step
    count = 0
    while t < hi:
        count += 1
        t += step
    return count


@dataclass(frozen=True)
class PairDay:
    symbol: str
    evaluated: int
    reasons: dict[str, int]
    signal_times: list[datetime]
    first_open: float | None = None
    last_close: float | None = None
    regime_close: float | None = None
    regime_ema: float | None = None
    regime_up: bool | None = None
    closest_breakout_pct: float | None = None  # lo que faltó para romper, con tendencia alcista

    @property
    def change_pct(self) -> float | None:
        if not self.first_open or self.last_close is None:
            return None
        return (self.last_close / self.first_open - 1) * 100

    @property
    def ema_gap_pct(self) -> float | None:
        if not self.regime_ema or self.regime_close is None:
            return None
        return (self.regime_close / self.regime_ema - 1) * 100

    @property
    def name(self) -> str:
        return self.symbol.replace("USDT", "")


def pair_day(
    symbol: str, bars: Sequence[Bar], strategy: H1Breakout, start: datetime, end: datetime
) -> PairDay:
    """Reevalúa las velas del día con la estrategia real para explicar cada decisión."""
    p = strategy.p
    idx = [i for i, b in enumerate(bars) if start <= b.close_time < end]
    if not idx:
        return PairDay(symbol, 0, {}, [])
    reasons: Counter[str] = Counter()
    signals: list[datetime] = []
    gaps: list[float] = []
    for i in idx:
        bar = bars[i]
        signal, reason = strategy.evaluate_entry(bars[max(0, i - 1) : i + 1])
        reasons[reason] += 1
        if signal is not None:
            signals.append(bar.close_time)
        level = bar.donchian.get(p.breakout_n)
        if _regime_up(bar, p.regime_ema) and level is not None:
            gaps.append((level / bar.close - 1) * 100)
    last = bars[idx[-1]]
    return PairDay(
        symbol,
        len(idx),
        dict(reasons),
        signals,
        bars[idx[0]].open,
        last.close,
        last.regime_close,
        last.regime_ema.get(p.regime_ema),
        _regime_up(last, p.regime_ema),
        min(gaps) if gaps else None,
    )


@dataclass
class AccountDay:
    acc: AccountData
    row: DayRow
    processed: int
    expected: int
    incidents: dict[str, int]
    total: Decimal  # acumulado desde el inicio

    @property
    def issues(self) -> list[str]:
        name = _account_label(self.acc.account)
        out = []
        if self.processed < self.expected:
            out.append(f"{name}: solo analizó {self.processed} de {self.expected} velas")
        if self.row.interruption_min >= 1:
            out.append(f"{name}: sin servicio {_duration(self.row.interruption_min)}")
        out += [f"{name}: {INCIDENT_NAMES[k]} (×{n})" for k, n in self.incidents.items()]
        if self.acc.kill_switch:
            out.append(f"{name}: kill switch ACTIVO")
        if self.acc.paused:
            out.append(f"{name}: en pausa ({self.acc.paused})")
        return out


def account_day(acc: AccountData, day: date, tz: ZoneInfo, now: datetime) -> AccountDay:
    start, end = day_window(day, tz, now)
    monday, _ = week_bounds(day)
    row = daily_rows(acc, monday, tz, now)[day.weekday()]
    in_day = [e for e in acc.events if start <= e[0] < end]
    processed = len({e[0] for e in in_day if e[1] == "bar_processed"})
    lo = max(start, acc.started_at) if acc.started_at else start
    expected = expected_closes(lo, min(end, now - PROCESSING_GRACE))
    incidents = Counter(e[1] for e in in_day if e[1] in INCIDENT_NAMES)
    return AccountDay(acc, row, processed, expected, dict(incidents),
                      _equity_at(acc, end) - acc.initial_capital)  # fmt: skip


def _describe_reasons(p: PairDay) -> str:
    if not p.evaluated:
        return "sin velas del día"
    parts = [f"{n} {REASON_NAMES.get(r, (r, r))[0 if n == 1 else 1]}"
             for r, n in sorted(p.reasons.items(), key=lambda x: -x[1])]  # fmt: skip
    return f"{_n(p.evaluated, 'vela', 'velas')}: " + ", ".join(parts)


def explain_market(pairs: Sequence[PairDay]) -> str:
    """Una frase que explique, en lenguaje llano, por qué el bot compró o no."""
    if not pairs or not any(p.evaluated for p in pairs):
        return "No se pudo evaluar el mercado: no hay velas guardadas de este día."
    signals = sum(len(p.signal_times) for p in pairs)
    if signals:
        names = ", ".join(p.name for p in pairs if p.signal_times)
        return f"La estrategia generó {_n(signals, 'señal', 'señales')} de compra ({names})."
    total = Counter[str]()
    for p in pairs:
        total.update(p.reasons)
    if total["indicadores_no_listos"]:
        return "No hubo señales: faltan datos para calcular algún indicador."
    if not total["sin_ruptura"]:
        return ("No hubo compras porque ningún par estuvo en tendencia alcista: el precio diario "
                "sigue por debajo de su media de 100 días y la estrategia se queda fuera por "
                "diseño.")  # fmt: skip
    best = min((p for p in pairs if p.closest_breakout_pct is not None),
               key=lambda p: p.closest_breakout_pct or 0.0, default=None)  # fmt: skip
    near = ""
    if best is not None and best.closest_breakout_pct is not None:
        near = f" Lo más cerca fue {best.name}, a {pct(best.closest_breakout_pct)} de romper."
    return ("No hubo compras: donde había tendencia alcista, el precio no superó el máximo de las "
            f"últimas 20 velas.{near}")  # fmt: skip


# ------------------------------------------------------------------ presentación


def _account_label(account: str) -> str:
    return "Paper" if account == "paper" else account.capitalize()


def _subject(days: Sequence[AccountDay], day: date, issues: Sequence[str]) -> str:
    trades = sum(len(d.row.trades) for d in days)
    buys = sum(d.row.signals for d in days)
    if issues:
        head = "REVISAR, hubo incidencias"
    elif trades or buys:
        head = (f"{_n(buys, 'compra', 'compras')}, "
                f"{_n(trades, 'operación cerrada', 'operaciones cerradas')}")  # fmt: skip
    else:
        head = "sin operaciones, funcionó normal"
    parts = [f"{_account_label(d.acc.account)} {money(d.row.change, True)} USDT" for d in days]
    return f"Bot de trading · {_day_label(day)}: {head} ({'; '.join(parts) or 'sin cuentas'})"


def _render_text(
    days: Sequence[AccountDay], pairs: Sequence[PairDay], day: date, tz: ZoneInfo,
    issues: Sequence[str],
) -> str:  # fmt: skip
    out = [f"REPORTE DIARIO DEL BOT DE TRADING — {_day_label(day)} {day.year} (hora Bogotá)", "",
           "Cuentas de PRUEBA: no es dinero real.", ""]  # fmt: skip
    if issues:
        out += ["REVISAR:"] + [f"  - {x}" for x in issues] + [""]
    else:
        out += ["Funcionamiento: normal, sin incidencias.", ""]
    out += [explain_market(pairs), ""]
    for d in days:
        out += [
            f"■ {ACCOUNT_NAMES.get(d.acc.account, d.acc.account)}",
            f"  Resultado del día: {money(d.row.change, True)} USDT ({pct(d.row.pct)}) → "
            f"{_verdict(d.row.change).upper()}",
            f"  Capital: {money(d.row.start)} → {money(d.row.end)} USDT "
            f"(acumulado {money(d.total, True)} USDT)",
            f"  Velas analizadas: {d.processed} de {d.expected}",
            f"  Compras: {d.row.signals} · Operaciones cerradas: {len(d.row.trades)} · "
            f"Señales bloqueadas: {d.row.rejected}",
            f"  Posiciones abiertas: {', '.join(d.acc.open_positions) or 'ninguna'}",
        ]
        out += [f"  {_trade_line(x, tz)}" for x in d.row.trades]
        out.append("")
    out.append("Mercado:")
    for p in pairs:
        out.append(f"  {p.name}: {_pair_price(p)}. {_regime_text(p)}. {_describe_reasons(p)}.")
    return "\n".join(out)


def _trade_line(x: ClosedTrade, tz: ZoneInfo) -> str:
    return (f"{x.symbol} {x.opened_at.astimezone(tz):%d/%m %H:%M} → "
            f"{x.closed_at.astimezone(tz):%d/%m %H:%M}: {money(x.pnl, True)} USDT — "
            f"{_exits(x.exits)}")  # fmt: skip


def _pair_price(p: PairDay) -> str:
    if p.last_close is None or p.change_pct is None:
        return "sin datos"
    return f"{money(p.last_close)} USDT ({pct(p.change_pct)} en el día)"


def _regime_text(p: PairDay) -> str:
    if p.regime_up is None or p.ema_gap_pct is None:
        return "tendencia sin calcular"
    word = "alcista" if p.regime_up else "bajista"
    return f"Tendencia {word} ({pct(p.ema_gap_pct)} frente a la media de 100 días)"


def _account_card(d: AccountDay, tz: ZoneInfo) -> str:
    name = ACCOUNT_NAMES.get(d.acc.account, d.acc.account)
    c = _color(d.row.change)
    velas_ok = d.processed >= d.expected
    open_now = ", ".join(s.replace("USDT", "") for s in d.acc.open_positions) or "ninguna"
    trades = ""
    if d.row.trades:
        rows = "".join(
            "<tr>" + _td(x.symbol.replace("USDT", ""), bold=True)
            + _td(f"{x.opened_at.astimezone(tz):%d/%m %H:%M}", nowrap=True)
            + _td(f"{x.closed_at.astimezone(tz):%d/%m %H:%M}", nowrap=True)
            + _td(f"{money(x.pnl, True)} USDT", "right", _color(x.pnl), True, nowrap=True)
            + _td(escape(_exits(x.exits))) + "</tr>"
            for x in d.row.trades
        )  # fmt: skip
        trades = f"""
  <table style="width:100%;border-collapse:collapse;margin-top:12px" cellpadding="0"
         cellspacing="0">
    <tr>{_th("Moneda")}{_th("Compra")}{_th("Venta")}{_th("Resultado", "right")}
        {_th("Motivo de la venta")}</tr>{rows}
  </table>"""
    label = 'style="padding:2px 16px 2px 0"'
    return f"""
<div style="border:1px solid #dadce0;border-radius:10px;padding:16px 20px;margin:16px 0">
  <div style="font-size:15px;font-weight:600;color:#3c4043">{escape(name)}</div>
  <div style="font-size:26px;font-weight:700;color:{c};margin:6px 0 8px">
    {money(d.row.change, True)} USDT <span style="font-size:15px">({pct(d.row.pct)})</span></div>
  <table style="font-size:14px;color:#3c4043" cellpadding="0" cellspacing="0">
    <tr><td {label}>Capital</td>
        <td><b>{money(d.row.start)} → {money(d.row.end)} USDT</b></td></tr>
    <tr><td {label}>Acumulado desde el inicio</td>
        <td><b style="color:{_color(d.total)}">{money(d.total, True)} USDT</b></td></tr>
    <tr><td {label}>Velas analizadas</td>
        <td><b style="color:{GREEN if velas_ok else RED}">{d.processed} de {d.expected}</b></td>
    </tr>
    <tr><td {label}>Compras / cerradas / bloqueadas</td>
        <td><b>{d.row.signals} / {len(d.row.trades)} / {d.row.rejected}</b></td></tr>
    <tr><td {label}>Posiciones abiertas ahora</td><td><b>{open_now}</b></td></tr>
  </table>{trades}
</div>"""


def _pairs_table(pairs: Sequence[PairDay]) -> str:
    rows = []
    for p in pairs:
        change = (_td(pct(p.change_pct), "right", GREEN if p.change_pct > 0 else RED, True,
                      nowrap=True)
                  if p.change_pct is not None else _td("—", "right"))  # fmt: skip
        price = money(p.last_close) if p.last_close is not None else "—"
        gap = p.ema_gap_pct
        trend = "—" if gap is None else (
            f'<b style="color:{GREEN if p.regime_up else RED}">'
            f"{'Alcista' if p.regime_up else 'Bajista'}</b><br>{pct(gap)} vs. media 100 d"
        )  # fmt: skip
        rows.append("<tr>" + _td(p.name, bold=True) + _td(price, "right", nowrap=True) + change
                    + _td(trend) + _td(escape(_describe_reasons(p))) + "</tr>")  # fmt: skip
    return f"""
<table style="width:100%;border-collapse:collapse" cellpadding="0" cellspacing="0">
  <tr>{_th("Par")}{_th("Precio", "right")}{_th("Día", "right")}{_th("Tendencia")}
      {_th("Decisión en cada vela")}</tr>{"".join(rows)}
</table>"""


def _render_html(
    days: Sequence[AccountDay], pairs: Sequence[PairDay], day: date, tz: ZoneInfo,
    issues: Sequence[str],
) -> str:  # fmt: skip
    if issues:
        items = "".join(f"<li>{escape(x)}</li>" for x in issues)
        banner = (f'<div style="background:#fce8e6;border-left:4px solid {RED};padding:10px 14px;'
                  f'margin:16px 0;font-size:14px;color:#3c4043"><b>Hay que revisar:</b>'
                  f'<ul style="margin:6px 0 0;padding-left:20px">{items}</ul></div>')  # fmt: skip
    else:
        banner = (f'<div style="background:#e6f4ea;border-left:4px solid {GREEN};padding:10px 14px;'
                  f'margin:16px 0;font-size:14px;color:#3c4043"><b>Funcionó con normalidad:</b> '
                  f"analizó todas las velas del día y no hubo incidencias.</div>")  # fmt: skip
    cards = "".join(_account_card(d, tz) for d in days)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#f8f9fa;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:720px;margin:0 auto;background:#ffffff;padding:24px 28px">
  <div style="font-size:22px;font-weight:700;color:#202124">Reporte diario del bot de trading</div>
  <div style="font-size:15px;color:#5f6368;margin-top:4px">
    {_day_label(day)} {day.year} · hora Bogotá</div>
  {banner}
  <div style="background:#f1f3f4;padding:10px 14px;font-size:15px;color:#202124;line-height:1.4">
    {escape(explain_market(pairs))}</div>
  {cards}
  <h3 style="font-size:16px;color:#202124;margin:22px 0 8px">Mercado y decisiones de la estrategia
  </h3>
  {_pairs_table(pairs)}
  <div style="border-top:1px solid #dadce0;margin-top:24px;padding-top:12px;font-size:12px;
              color:#80868b;line-height:1.5">
    <b>Cuentas de prueba: no es dinero real.</b> La estrategia (H1) es experimental. Cada día
    cierran 6 velas de 4 horas (03, 07, 11, 15, 19 y 23 h). Solo compra si el cierre diario está
    por encima de su media de 100 días <i>y</i> la vela de 4 h supera el máximo de las 20
    anteriores.
  </div>
</div></body></html>"""


def render_daily(
    accounts: Sequence[AccountData],
    pairs: Sequence[PairDay],
    day: date,
    tz: ZoneInfo,
    now: datetime,
) -> tuple[str, str, str]:
    """(asunto, texto plano, HTML) del reporte."""
    days = [account_day(a, day, tz, now) for a in accounts]
    issues = [x for d in days for x in d.issues]
    issues += [f"{p.name}: sin velas guardadas de este día" for p in pairs if not p.evaluated]
    return (_subject(days, day, issues), _render_text(days, pairs, day, tz, issues),
            _render_html(days, pairs, day, tz, issues))  # fmt: skip


# ------------------------------------------------------------------ datos y envío


async def load_pairs(
    candles: Any,
    symbols: Sequence[str],
    strategy: H1Breakout,
    start: datetime,
    end: datetime,
) -> list[PairDay]:
    out = []
    for s in symbols:
        signal, regime = (await candles.load_candles(s, SIGNAL_TF),
                          await candles.load_candles(s, REGIME_TF))  # fmt: skip
        bars = await asyncio.to_thread(build_bars, s, signal, regime)  # no bloquear el trading
        out.append(pair_day(s, bars, strategy, start, end))
    return out


async def build_daily(
    store: Any,
    candles: Any,
    accounts: Sequence[str],
    initial: Decimal,
    symbols: Sequence[str],
    strategy: H1Breakout,
    day: date,
    tz: ZoneInfo,
    now: datetime,
) -> tuple[str, str, str]:
    data = [a for a in [await load_account(store, x, initial) for x in accounts] if a]
    start, end = day_window(day, tz, now)
    pairs = await load_pairs(candles, symbols, strategy, start, end)
    return render_daily(data, pairs, day, tz, now)


def is_due(now_local: datetime, last_sent: str | None) -> tuple[bool, date]:
    """(¿toca enviar?, día que cubre). Si el bot estuvo caído a la hora, se envía al volver."""
    today = now_local.date()
    passed = now_local.time() >= SEND_AT
    target = today if passed else today - timedelta(days=1)
    if last_sent is None:  # primera vez: esperar a la hora, sin correos al desplegar
        return passed, target
    return last_sent < target.isoformat(), target


async def daily_report_loop(
    stop: asyncio.Event,
    store: Any,
    candles: Any,
    accounts: Sequence[str],
    initial: Decimal,
    symbols: Sequence[str],
    strategy: H1Breakout,
    tz: ZoneInfo,
    mail: MailConfig,
    out_dir: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    while not stop.is_set():
        try:
            now = clock()
            due, day = is_due(now.astimezone(tz), await store.last_daily_report())
            if due:
                subject, body, html = await build_daily(
                    store, candles, accounts, initial, symbols, strategy, day, tz, now
                )
                try:  # la copia local nunca debe impedir el envío
                    await asyncio.to_thread(_save, out_dir / f"reporte-diario-{day}.html", html)
                except OSError as exc:
                    log.warning("daily_report_copy_failed", error=repr(exc))
                if mail.complete:
                    await asyncio.to_thread(send_mail, mail, subject, body, html)
                    log.info("daily_report_sent", day=day.isoformat(), to=mail.to)
                else:
                    log.warning("daily_report_not_emailed", day=day.isoformat(),
                                reason="falta configuración SMTP en .env")  # fmt: skip
                await store.mark_daily_report_sent(day.isoformat(), now, mail.complete)
        except Exception as exc:
            log.error("daily_report_failed", alert=True, error=repr(exc))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=600)
