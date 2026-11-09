from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.main import _clock_drift_ms
from bot.monitoring import daily
from bot.monitoring.daily import (
    PairDay,
    account_day,
    expected_closes,
    explain_market,
    is_due,
    pair_day,
    render_daily,
)
from bot.monitoring.weekly import AccountData, ClosedTrade, MailConfig
from bot.strategy.features import Bar
from bot.strategy.hypotheses import H1Breakout

BOG = ZoneInfo("America/Bogota")
DAY = date(2026, 11, 9)
START = datetime(2026, 11, 9, 5, tzinfo=UTC)  # 00:00 Bogotá
NIGHT = START + timedelta(hours=23, minutes=30)
CLOSES = [START + timedelta(hours=3 + 4 * i) - timedelta(milliseconds=1) for i in range(6)]


def _bar(t: datetime, close: float, level: float, regime: float, ema: float) -> Bar:
    return Bar("BTCUSDT", t - timedelta(hours=4), t, close, close, close, close, 1.0, 1.0, 50.0,
               donchian={20: level}, regime_close=regime, regime_ema={100: ema})  # fmt: skip


def _acc(processed: int = 6) -> AccountData:
    acc = AccountData("paper", D(200), started_at=START - timedelta(days=9))
    acc.events = [(t, "bar_processed", {}) for t in CLOSES[:processed]]
    acc.equity = [(CLOSES[0], D(200))]
    return acc


def _down() -> PairDay:
    return PairDay("BTCUSDT", 6, {"regimen_no_alcista": 6}, [], 100.0, 96.0, 96.0, 100.0, False)


def test_expected_closes_full_day_partial_and_empty() -> None:
    assert expected_closes(START, START + timedelta(days=1)) == 6
    assert expected_closes(START, START + timedelta(hours=8)) == 2  # 03:00 y 07:00 Bogotá
    assert expected_closes(START, START + timedelta(hours=1)) == 0
    # El límite inferior incluye una vela que cierra justo ahí.
    assert expected_closes(CLOSES[0], START + timedelta(hours=4)) == 1


def test_pair_day_counts_each_decision_like_the_strategy() -> None:
    bars = [
        _bar(CLOSES[0] - timedelta(hours=4), 90, 95, 90, 100),  # día anterior: fuera de ventana
        _bar(CLOSES[0], 90, 95, 90, 100),  # tendencia bajista
        _bar(CLOSES[1], 98, 100, 110, 100),  # alcista, falta romper
        _bar(CLOSES[2], 99, 100, 110, 100),  # alcista, aún más cerca
        _bar(CLOSES[3], 101, 100, 110, 100),  # rompe
    ]
    p = pair_day("BTCUSDT", bars, H1Breakout(), START, START + timedelta(days=1))
    assert p.evaluated == 4
    assert p.reasons == {"regimen_no_alcista": 1, "sin_ruptura": 2, "ok": 1}
    assert p.signal_times == [CLOSES[3]]
    assert p.regime_up is True
    assert p.last_close == 101 and p.first_open == 90
    assert p.closest_breakout_pct is not None and p.closest_breakout_pct < 0  # rompió


def test_pair_day_without_candles_is_reported_as_empty() -> None:
    p = pair_day("BTCUSDT", [], H1Breakout(), START, START + timedelta(days=1))
    assert p.evaluated == 0 and p.change_pct is None and p.ema_gap_pct is None


def test_quiet_day_is_explained_not_flagged() -> None:
    subject, text, html = render_daily([_acc()], [_down()], DAY, BOG, NIGHT)
    assert subject.startswith("Bot de trading · Lunes 9 nov: sin operaciones, funcionó normal")
    assert "Paper +0,00 USDT" in subject
    assert "Velas analizadas: 6 de 6" in text
    assert "ningún par estuvo en tendencia alcista" in text
    assert "no es dinero real" in text
    assert "Funcionó con normalidad" in html and "Hay que revisar" not in html
    assert "6 velas: 6 con tendencia bajista" in html
    assert "-4,00 %" in html and "Bajista" in html


def test_missed_candles_are_never_presented_as_a_quiet_day() -> None:
    subject, text, html = render_daily([_acc(processed=2)], [_down()], DAY, BOG, NIGHT)
    assert "REVISAR" in subject and "sin operaciones" not in subject
    assert "Paper: solo analizó 2 de 6 velas" in text
    assert "Hay que revisar" in html and "Funcionó con normalidad" not in html


def test_missing_market_data_interruption_kill_switch_and_incidents_are_flagged() -> None:
    acc = _acc()
    acc.kill_switch = True
    off = {"from": (START + timedelta(hours=1)).isoformat(),
           "to": (START + timedelta(hours=2)).isoformat()}  # fmt: skip
    acc.events += [(START + timedelta(hours=2), "interruption", off),
                   (START + timedelta(hours=3), "emergency", {"error": "SECRETO"}),
                   (START + timedelta(hours=4), "emergency", {})]  # fmt: skip
    acc.events.sort(key=lambda e: e[0])
    empty = PairDay("ETHUSDT", 0, {}, [])
    subject, text, html = render_daily([acc], [_down(), empty], DAY, BOG, NIGHT)
    assert "REVISAR" in subject
    assert "sin servicio 1 h 0 min" in text
    assert "emergencia de ejecución (×2)" in text
    assert "kill switch ACTIVO" in text
    assert "ETH: sin velas guardadas" in text
    assert "SECRETO" not in text + html  # el detalle crudo no va al correo


def test_trade_of_the_day_appears_with_result() -> None:
    acc = _acc()
    acc.equity = [(CLOSES[0], D(200)), (CLOSES[4], D("202.50"))]
    acc.trades = [ClosedTrade("BTCUSDT", CLOSES[0], CLOSES[4], D(100), D(103), D("2.50"), D("2.1"),
                              "stop_trailing")]  # fmt: skip
    acc.events.append((CLOSES[0], "order_placed", {}))
    acc.events.sort(key=lambda e: e[0])
    subject, text, html = render_daily([acc], [_down()], DAY, BOG, NIGHT)
    assert "1 compra, 1 operación cerrada" in subject and "+2,50 USDT" in subject
    assert "GANANCIA" in text
    assert "Stop dinámico (asegura ganancias)" in html and "#1e8e3e" in html


def test_account_day_tolerates_a_bar_that_just_closed() -> None:
    # 2 minutos después del cierre de las 07:00: esa vela aún puede estar en proceso.
    now = CLOSES[1] + timedelta(minutes=2)
    d = account_day(_acc(processed=1), DAY, BOG, now)
    assert (d.processed, d.expected) == (1, 1) and d.issues == []


def test_account_started_mid_day_only_expects_its_own_candles() -> None:
    acc = _acc()
    acc.started_at = CLOSES[3]
    acc.events = [(t, "bar_processed", {}) for t in CLOSES[3:]]
    d = account_day(acc, DAY, BOG, NIGHT)
    assert (d.processed, d.expected) == (3, 3)


def test_explanations_by_situation() -> None:
    assert "no hay velas" in explain_market([PairDay("BTCUSDT", 0, {}, [])])
    near = PairDay("ETHUSDT", 6, {"sin_ruptura": 6}, [], closest_breakout_pct=0.5)
    assert "ETH" in explain_market([near]) and "+0,50 %" in explain_market([near])
    sig = PairDay("SOLUSDT", 6, {"ok": 1, "sin_ruptura": 5}, [CLOSES[0]])
    assert "1 señal de compra (SOL)" in explain_market([sig])


def test_is_due_once_per_day_and_catches_up_after_downtime() -> None:
    night = datetime(2026, 11, 9, 23, 31, tzinfo=BOG)
    early = datetime(2026, 11, 9, 23, 29, tzinfo=BOG)
    assert is_due(night, "2026-11-08") == (True, DAY)
    assert is_due(night, "2026-11-09")[0] is False
    assert is_due(early, "2026-11-08")[0] is False
    # El bot estaba caído a las 23:30 del día 8: al volver se envía el reporte del día 8.
    assert is_due(early, "2026-11-07") == (True, date(2026, 11, 8))
    # Recién desplegado: no se envía nada hasta la hora.
    assert is_due(early, None)[0] is False
    assert is_due(night, None) == (True, DAY)


class _Store:
    def __init__(self) -> None:
        self.marks: list[str] = []

    async def last_daily_report(self) -> str | None:
        return self.marks[-1] if self.marks else None

    async def mark_daily_report_sent(self, day: str, at: datetime, emailed: bool) -> None:
        self.marks.append(day)

    async def load(self, account: str) -> None:
        return None


class _Candles:
    async def load_candles(self, symbol: str, interval: str) -> list[Any]:
        return []


def _run_loop(tmp: Path, sent: list[str], monkey: Any, fail: bool = False) -> _Store:
    def fake_send(cfg: MailConfig, subject: str, body: str, html: str | None = None) -> None:
        if fail:
            raise OSError("smtp caído")
        sent.append(subject)

    monkey.setattr(daily, "send_mail", fake_send)

    async def go(store: _Store) -> None:
        stop = asyncio.Event()
        mail = MailConfig("h", 587, "u", "p", "to")
        task = asyncio.create_task(daily.daily_report_loop(
            stop, store, _Candles(), ["paper"], D(200), ["BTCUSDT"], H1Breakout(), BOG, mail, tmp,
            clock=lambda: NIGHT,
        ))  # fmt: skip
        await asyncio.sleep(0.05)
        stop.set()
        await task

    store = _Store()
    asyncio.run(go(store))
    return store


def test_loop_sends_once_and_records_it(tmp_path: Path, monkeypatch: Any) -> None:
    sent: list[str] = []
    store = _run_loop(tmp_path, sent, monkeypatch)
    assert len(sent) == 1 and store.marks == ["2026-11-09"]
    assert (tmp_path / "reporte-diario-2026-11-09.html").exists()


def test_failed_send_is_not_marked_so_it_is_retried(tmp_path: Path, monkeypatch: Any) -> None:
    store = _run_loop(tmp_path, [], monkeypatch, fail=True)
    assert store.marks == []


def test_clock_drift_is_centered_on_the_round_trip() -> None:
    before = datetime(2026, 11, 9, 12, tzinfo=UTC)
    after = before + timedelta(seconds=2)
    # Reloj perfecto: Binance responde a mitad del viaje; antes se medía −1000 ms.
    drift, margin = _clock_drift_ms(before, after, before + timedelta(seconds=1))
    assert drift == 0 and margin == 1000
    # Desfase real de 3 s: se sigue detectando.
    drift, _ = _clock_drift_ms(before, after, before + timedelta(seconds=4))
    assert drift == -3000
