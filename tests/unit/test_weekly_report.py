from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from bot.monitoring.weekly import (
    AccountData,
    ClosedTrade,
    daily_rows,
    is_due,
    render_weekly,
    week_bounds,
)

BOG = ZoneInfo("America/Bogota")
MONDAY = date(2026, 11, 2)


def _utc(d: date, hour: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=BOG).astimezone(UTC)


def _acc() -> AccountData:
    acc = AccountData("paper", D(200), started_at=_utc(date(2026, 10, 30), 11))
    tue, thu = MONDAY + timedelta(days=1), MONDAY + timedelta(days=3)
    acc.trades = [
        ClosedTrade("BTCUSDT", _utc(MONDAY, 10), _utc(tue, 9), D(100), D(103), D("2.50"), D("2.1"),
                    "parcial|stop_trailing"),
        ClosedTrade("ETHUSDT", _utc(thu, 8), _utc(thu, 20), D(50), D(49), D("-1.00"), D("-1.0"),
                    "stop"),
    ]  # fmt: skip
    acc.equity = [
        (_utc(MONDAY, 12), D("200.40")),
        (_utc(tue, 12), D("202.50")),
        (_utc(thu, 21), D("201.50")),
    ]
    off = {"from": (_utc(MONDAY, 9) - timedelta(minutes=90)).isoformat(),
           "to": _utc(MONDAY, 9).isoformat()}  # fmt: skip
    acc.events = [(_utc(MONDAY, 9), "interruption", off),
                  (_utc(MONDAY, 10), "order_placed", {}),
                  (_utc(thu, 8), "signal_rejected", {})]  # fmt: skip
    return acc


def test_week_bounds_monday_to_sunday() -> None:
    assert week_bounds(date(2026, 11, 5)) == (MONDAY, date(2026, 11, 8))


def test_daily_rows_and_weekly_totals() -> None:
    now = _utc(date(2026, 11, 8), 23) + timedelta(minutes=30)
    rows = daily_rows(_acc(), MONDAY, BOG, now)
    assert len(rows) == 7
    mon, tue, wed, thu = rows[0], rows[1], rows[2], rows[3]
    assert mon.start == D(200) and mon.end == D("200.40") and mon.interruption_min == 90
    assert mon.signals == 1
    assert tue.change == D("2.10") and len(tue.trades) == 1
    assert wed.change == 0  # sin cambios
    assert thu.change == D("-1.00") and thu.rejected == 1
    subject, text, html = render_weekly([_acc()], MONDAY, BOG, now)
    # Texto plano: resultado con formato colombiano y veredicto.
    assert "+1,50 USDT" in text and "GANANCIA" in text  # 200 → 201,50
    assert "no es dinero real" in text
    assert "Lunes 2 nov" in text
    # Asunto claro.
    assert subject.startswith("Bot de trading · semana 2–8 nov: ganancia")
    assert "Paper +1,50 USDT" in subject
    # HTML: tarjeta con color de ganancia, días, operaciones traducidas, glosario.
    assert "<!DOCTYPE html>" in html and "#1e8e3e" in html
    assert "2 con ganancia · 1 con pérdida · 4 sin cambios" in html
    assert "Stop dinámico (asegura ganancias)" in html and "Venta parcial" in html
    assert "bot sin servicio 1 h 30 min" in html
    assert "1 señal bloqueada por el control de riesgo" in html
    assert "(1 ganadora)" in html and "Sin operaciones (no hubo señal de compra)" in html
    assert "Cómo leer este informe" in html
    assert ">BTC<" in html and ">ETH<" in html


def test_money_format_and_losing_week_is_red() -> None:
    from bot.monitoring.weekly import money

    assert money(D("1234.5")) == "1.234,50" and money(D("-3.2"), True) == "-3,20"
    acc = AccountData("paper", D(200))
    acc.equity = [(_utc(MONDAY, 12), D("195.00"))]
    subject, _, html = render_weekly([acc], MONDAY, BOG, _utc(MONDAY, 20))
    assert "pérdida" in subject and "#d93025" in html and "-5,00 USDT" in html


def test_is_due_only_sunday_night_once_per_week() -> None:
    sun_late = datetime(2026, 11, 8, 23, 31, tzinfo=BOG)
    sun_early = datetime(2026, 11, 8, 23, 29, tzinfo=BOG)
    sat = datetime(2026, 11, 7, 23, 45, tzinfo=BOG)
    assert is_due(sun_late, None) == (True, "2026-W45")
    assert is_due(sun_late, "2026-W45")[0] is False  # ya enviado
    assert is_due(sun_early, None)[0] is False
    assert is_due(sat, None)[0] is False


def test_interruption_minutes_are_split_by_day() -> None:
    acc = AccountData("paper", D(200))
    # Apagado del sábado 31/10 19:00 al lunes 02/11 09:00 (hora Bogotá): registrado el lunes.
    off = {"from": _utc(date(2026, 10, 31), 19).isoformat(), "to": _utc(MONDAY, 9).isoformat()}
    acc.events = [(_utc(MONDAY, 9), "interruption", off)]
    rows = daily_rows(acc, MONDAY, BOG, _utc(MONDAY, 12))
    assert rows[0].interruption_min == 9 * 60  # solo la parte del lunes
