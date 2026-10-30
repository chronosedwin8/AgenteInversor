"""Serialización del estado de una `TradingSession` (persistencia entre reinicios).

Se guarda todo lo necesario para continuar exactamente donde se dejó: caja, posiciones,
reservas, órdenes pendientes, planes de salida y estado del RiskManager. La estrategia no se
serializa: se reconstruye con sus parámetros fijos (H1-fijo).
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

from bot.execution.session import OpenPosition, PendingEntry, TradingSession
from bot.portfolio.portfolio import Holding
from bot.risk.manager import RiskState
from bot.strategy.base import Strategy
from bot.strategy.rules import EntrySignal, ExitReason, PositionPlan


def _enc(v: Any) -> Any:
    if isinstance(v, Decimal):
        return {"$dec": str(v)}
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, float) and v == float("inf"):
        return {"$inf": True}
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_enc(x) for x in v]
    return v


def _dec(v: Any) -> Any:
    if isinstance(v, dict):
        if "$dec" in v:
            return Decimal(v["$dec"])
        if "$dt" in v:
            return datetime.fromisoformat(v["$dt"])
        if "$inf" in v:
            return float("inf")
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def dump_session(s: TradingSession) -> str:
    pf = s.pf
    payload = {
        "cash": pf.cash,
        "holdings": {k: asdict(h) for k, h in pf.holdings.items()},
        "reserved": dict(pf.reserved),
        "marks": dict(pf.marks),
        "pending": {
            k: {"signal": asdict(p.signal), "limit": p.limit, "stop": p.stop, "qty": p.qty,
                "age": p.age}
            for k, p in s.pending.items()
        },
        "open": {
            k: {"plan": asdict(o.plan), "partial_qty": o.partial_qty} for k, o in s.open.items()
        },
        "risk": s.risk.state.to_json(),  # texto con el formato propio de RiskState
        "stats": {"signals": s.stats.signals, "unfilled": s.stats.unfilled,
                  "rejected": s.stats.rejected},
    }  # fmt: skip
    return json.dumps(_enc(payload))


def load_session(s: TradingSession, raw: str, strategy: Strategy) -> None:
    """Restaura el estado en una sesión recién creada (misma configuración)."""
    d = _dec(json.loads(raw))
    pf = s.pf
    pf.cash = d["cash"]
    hf = {f.name for f in fields(Holding)}
    pf.holdings = {k: Holding(**{x: v for x, v in h.items() if x in hf})
                   for k, h in d["holdings"].items()}  # fmt: skip
    pf.reserved = dict(d["reserved"])
    pf.marks = dict(d["marks"])
    s.pending = {
        k: PendingEntry(EntrySignal(**p["signal"]), p["limit"], p["stop"], p["qty"], strategy,
                        p["age"])
        for k, p in d["pending"].items()
    }  # fmt: skip
    opens = {}
    for k, o in d["open"].items():
        plan = dict(o["plan"])
        plan["stop_kind"] = ExitReason(plan["stop_kind"])
        opens[k] = OpenPosition(PositionPlan(**plan), strategy, o["partial_qty"])
    s.open = opens
    s.risk.state = RiskState.from_json(d["risk"])
    s.stats.signals = d["stats"]["signals"]
    s.stats.unfilled = d["stats"]["unfilled"]
    s.stats.rejected = d["stats"]["rejected"]
