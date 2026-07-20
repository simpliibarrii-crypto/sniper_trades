"""Aggregate paper portfolio stats from the copy-trade ledger."""

from __future__ import annotations

from typing import Any, Dict, List

from services.copy_trade import get_engine
from services import free_market


def portfolio_snapshot() -> Dict[str, Any]:
    eng = get_engine()
    state = eng.list_state()
    followers = state.get("followers") or []
    balances = state.get("paper_balances") or {}
    positions = state.get("paper_positions") or {}
    fills = state.get("fills") or []

    # mark open paper positions with live prices when possible
    open_rows: List[Dict[str, Any]] = []
    total_cash = 0.0
    total_equity = 0.0
    for f in followers:
        if (f.get("mode") or "paper") != "paper":
            continue
        fid = f["follower_id"]
        cash = float(balances.get(fid, 0.0))
        total_cash += cash
        pos = positions.get(fid) or {}
        mtm = cash
        for inst, qty in pos.items():
            q = float(qty or 0)
            if abs(q) < 1e-12:
                continue
            last = None
            try:
                last = free_market.get_ticker(inst).get("last")
            except Exception:
                last = None
            value = (float(last) * q) if last else None
            if value is not None:
                mtm += value
            open_rows.append(
                {
                    "follower_id": fid,
                    "follower_name": f.get("name"),
                    "instrument": inst,
                    "qty": q,
                    "mark": last,
                    "mark_value": round(value, 2) if value is not None else None,
                }
            )
        total_equity += mtm

    paper_fills = [x for x in fills if x.get("mode") == "paper"]
    return {
        "followers_paper": len([f for f in followers if f.get("mode") == "paper"]),
        "cash_total": round(total_cash, 2),
        "equity_mark": round(total_equity, 2),
        "open_positions": open_rows,
        "open_count": len(open_rows),
        "paper_fills": len(paper_fills),
        "recent_fills": paper_fills[-15:],
    }
