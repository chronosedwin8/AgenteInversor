from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from bot.backtest.metrics import Metrics
from bot.backtest.walkforward import (
    apply_combo,
    evaluate_acceptance,
    grid_combos,
    loosest_combo,
    make_windows,
)
from bot.data.backfill import last_closed_open_time
from bot.data.klines import Candle
from bot.strategy.snapshot import build_snapshots
from tests.strategy_helpers import CFG, PARAMS
from tests.unit.test_indicators import CANDLES


def dt(y: int, m: int, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def test_windows_for_36_months_of_history() -> None:
    start = dt(2023, 10) + timedelta(minutes=15) - timedelta(milliseconds=1)  # 1.er cierre
    end = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)
    ws = make_windows(start, end, CFG.backtest.walk_forward)
    assert ws[0].train_start == dt(2023, 11)  # octubre 2023 = calentamiento
    assert ws[0].train_end == ws[0].test_start == dt(2024, 5)
    assert ws[0].test_end == dt(2024, 8)
    assert [w.test_start.strftime("%Y-%m") for w in ws] == [
        "2024-05", "2024-08", "2024-11", "2025-02", "2025-05",
        "2025-08", "2025-11", "2026-02", "2026-05", "2026-08",
    ]  # fmt: skip
    assert ws[-1].test_end == end
    for a, b in itertools.pairwise(ws):
        assert a.test_end == b.test_start  # validación contigua, sin solapes
        assert b.train_end == b.test_start  # entrenamiento siempre ANTES de su validación


def test_incomplete_first_month_is_skipped() -> None:
    ws = make_windows(dt(2023, 10, 15), dt(2026, 1), CFG.backtest.walk_forward)
    assert ws[0].train_start == dt(2023, 12)


def test_grid_has_64_combos_and_includes_plan_defaults() -> None:
    combos = grid_combos(CFG.backtest.grid)
    assert len(combos) == 64
    plan = {
        "adx_min": 20,
        "stop_atr_multiple": 1.5,
        "partial_take_r": 1.5,
        "trailing_atr_multiple": 2.0,
        "volume_multiplier": 1.2,
        "enforce_entry_window": True,
    }
    assert plan in combos
    assert apply_combo(PARAMS, plan, CFG.settings) == PARAMS


def test_loosest_combo_is_least_restrictive() -> None:
    loose = apply_combo(PARAMS, loosest_combo(CFG.backtest.grid), CFG.settings)
    assert loose.adx_min == 20 and loose.volume_multiplier == 1.0 and loose.entry_window is None


def test_acceptance_thresholds() -> None:
    m = Metrics(trades=120, wins=50, win_rate=0.42, gross_profit=D(60), gross_loss=D(40),
                profit_factor=1.5, net_pnl=D(20), return_pct=10, max_drawdown_pct=7.5, avg_r=0.1,
                fees=D(5), avg_hold_hours=3)  # fmt: skip
    acc = evaluate_acceptance(m, 3.0, CFG.backtest.acceptance)
    assert all(ok for ok, _ in acc.values())
    bad = evaluate_acceptance(
        Metrics(**{**m.__dict__, "trades": 99, "profit_factor": 1.29, "max_drawdown_pct": 8.1}),
        -0.01,
        CFG.backtest.acceptance,
    )
    assert not any(ok for ok, _ in bad.values())


def test_snapshots_have_no_lookahead() -> None:
    """Truncar el futuro no cambia ningún snapshot pasado (15 m sintéticas a partir de 1 h)."""
    regime = CANDLES[:600]
    signal: list[Candle] = []
    for c in CANDLES[200:600]:  # cuatro velas de 15 min por cada hora
        for k in range(4):
            o = c.open_time + timedelta(minutes=15 * k)
            signal.append(Candle(c.symbol, "15m", o, o + timedelta(minutes=15, milliseconds=-1),
                                 c.open, c.high, c.low, c.close, c.volume / 4, c.quote_volume,
                                 c.trades, c.taker_buy_base, c.taker_buy_quote))  # fmt: skip
    full = build_snapshots("BTCUSDT", signal, regime, CFG.strategy)
    cut = 900
    partial = build_snapshots("BTCUSDT", signal[:cut], regime, CFG.strategy)
    assert full[:cut] == partial
    for s in full:
        assert s.regime is None or s.regime.close_time <= s.close_time
    # El régimen de una vela de 15 min es la última 1 h CERRADA, no la que está en curso.
    s = full[401]  # 15 min dentro de una hora
    assert s.regime is not None
    assert s.regime.close_time == last_closed_open_time(s.close_time, "1h") + timedelta(
        hours=1, milliseconds=-1
    )
