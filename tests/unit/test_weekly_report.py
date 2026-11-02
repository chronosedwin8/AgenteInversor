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
    acc.events = [(_utc(MONDAY, 9), "interruption", {"minutes": "90"}),
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
    subject, body = render_weekly([_acc()], MONDAY, BOG, now)
    assert "GANANCIA" in body and "+1.50 USDT" in body  # 200 → 201,50
    assert "NO es dinero real" in body
    assert "paper +1.50 USDT" in subject
    assert "Días con ganancia: 2; con pérdida: 1; sin cambios: 4" in body
    assert "ETHUSDT" in body and "BTCUSDT" in body


def test_is_due_only_sunday_night_once_per_week() -> None:
    sun_late = datetime(2026, 11, 8, 23, 31, tzinfo=BOG)
    sun_early = datetime(2026, 11, 8, 23, 29, tzinfo=BOG)
    sat = datetime(2026, 11, 7, 23, 45, tzinfo=BOG)
    assert is_due(sun_late, None) == (True, "2026-W45")
    assert is_due(sun_late, "2026-W45")[0] is False  # ya enviado
    assert is_due(sun_early, None)[0] is False
    assert is_due(sat, None)[0] is False
