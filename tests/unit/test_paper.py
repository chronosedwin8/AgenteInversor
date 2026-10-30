"""Paper trading: mismas operaciones que el backtest (regla 3), también tras reinicios."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from bot.backtest.engine import Backtester, ParamPeriod, SymbolData
from bot.data.backfill import HistorySync
from bot.data.klines import Candle
from bot.exchange.filters import SymbolFilters
from bot.execution.paper import PaperTrader
from bot.risk.manager import RiskEventKind
from bot.strategy.features import build_bars
from bot.strategy.hypotheses import H1Breakout, H1Params
from tests.fakes import FakeAdapter, MemorySessionStore, MemoryStore
from tests.strategy_helpers import CFG
from tests.unit.test_indicators import CANDLES

T0 = datetime(2024, 1, 1, tzinfo=UTC)
H4, DAY = timedelta(hours=4), timedelta(days=1)
N = 996  # 166 días exactos de velas de 4 h


def _series(symbol: str) -> tuple[list[Candle], list[Candle]]:
    """Velas reales (BTC 1 h del fixture) reetiquetadas como 4 h, y diarias agregadas."""
    four = []
    for i, c in enumerate(CANDLES[:N]):
        o = T0 + i * H4
        four.append(Candle(symbol, "4h", o, o + H4 - timedelta(milliseconds=1), c.open, c.high,
                           c.low, c.close, c.volume, c.quote_volume, c.trades, c.taker_buy_base,
                           c.taker_buy_quote))  # fmt: skip
    daily = []
    for k in range(0, N, 6):
        g = four[k : k + 6]
        daily.append(Candle(symbol, "1d", g[0].open_time, g[-1].close_time, g[0].open,
                            max(x.high for x in g), min(x.low for x in g), g[-1].close,
                            sum((x.volume for x in g), D(0)), D(0), 0, D(0), D(0)))  # fmt: skip
    return four, daily


@pytest.fixture
def filters(symbol_info: dict[str, dict[str, Any]]) -> SymbolFilters:
    return SymbolFilters.from_exchange_info(symbol_info["BTCUSDT"])


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


async def _load(
    store: MemoryStore, four: list[Candle], daily: list[Candle], upto: datetime
) -> None:
    await store.upsert([c for c in four if c.close_time < upto], "vision")
    await store.upsert([c for c in daily if c.close_time < upto], "vision")


def _paper(store: MemoryStore, sessions: MemorySessionStore, f: SymbolFilters,
           clock: Clock) -> PaperTrader:  # fmt: skip
    return PaperTrader(CFG, H1Params(), store, sessions,  # type: ignore[arg-type]
                       HistorySync(store, FakeAdapter(), None), FakeAdapter(),  # type: ignore[arg-type]
                       {"BTCUSDT": f}, clock=clock, live_quotes=False)  # fmt: skip


async def test_paper_matches_backtest_and_survives_restart(filters: SymbolFilters) -> None:
    four, daily = _series("BTCUSDT")
    start_day = 100  # EMA100 diaria lista
    store, sessions = MemoryStore(), MemorySessionStore()
    clock = Clock(T0 + start_day * DAY + timedelta(seconds=1))
    await _load(store, four, daily, clock.t)
    paper = _paper(store, sessions, filters, clock)
    await paper.start()  # empieza a operar desde la siguiente vela
    first_live = paper.last_bar_time

    # Avanza hasta el día 130, "reinicia" y sigue hasta el final.
    for upto_day, restart in ((130, False), (166, True)):
        clock.t = T0 + upto_day * DAY + timedelta(seconds=1)
        await _load(store, four, daily, clock.t)
        if restart:
            paper = _paper(store, sessions, filters, clock)
            await paper.start()
        await paper.step()
    assert await paper.step() == 0  # nada nuevo: no reprocesa ni duplica

    # Backtest con la misma lógica sobre los mismos datos.
    bars = build_bars("BTCUSDT", four, daily)
    bt = Backtester({"BTCUSDT": SymbolData("BTCUSDT", bars, filters)}, CFG.risk,
                    CFG.strategy.costs.fee_rate, CFG.strategy.costs.slippage_rate)  # fmt: skip
    assert first_live is not None
    start = next(b.close_time for b in bars if b.close_time > first_live)
    res = bt.run([ParamPeriod(start, H1Breakout(H1Params()))], start, bars[-1].close_time + H4)
    closed_bt = [t for t in res.trades if "fin_de_datos" not in t.exits]

    assert len(closed_bt) > 0, "el escenario debe generar operaciones"
    assert sessions.trades_rows == closed_bt  # idénticas, sin duplicados tras el reinicio
    assert len({(t.symbol, t.opened_at) for t in sessions.trades_rows}) == len(closed_bt)


async def test_kill_switch_state_persists_in_paper(filters: SymbolFilters) -> None:
    four, daily = _series("BTCUSDT")
    store, sessions = MemoryStore(), MemorySessionStore()
    clock = Clock(T0 + 100 * DAY + timedelta(seconds=1))
    await _load(store, four, daily, clock.t)
    paper = _paper(store, sessions, filters, clock)
    await paper.start()
    paper.session.risk.update(clock.t, D(179))  # capital simulado bajo el umbral
    assert paper.session.risk.kill_switch_active
    await paper._persist()
    restarted = _paper(store, sessions, filters, clock)
    await restarted.start()
    assert restarted.session.risk.kill_switch_active
    clock.t = T0 + 166 * DAY + timedelta(seconds=1)
    await _load(store, four, daily, clock.t)
    await restarted.step()
    assert (
        restarted.session.stats.signals == 0
        or restarted.session.stats.rejected.get("kill_switch_activo", 0)
        == restarted.session.stats.signals
    )
    assert sessions.trades_rows == []
    assert RiskEventKind.KILL_SWITCH not in [e.kind for e in restarted.session.stats.risk_events]
