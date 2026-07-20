"""Lightweight trade-intelligence helpers (no external IO — always fast)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Common CDC / crypto symbols (expandable)
_SYMBOLS = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "CRO", "LTC", "AVAX", "LINK",
    "BNB", "DOT", "ATOM", "NEAR", "SUI", "APT", "ARB", "OP", "PEPE", "SHIB",
    "USDT", "USDC",
}

_TF = re.compile(
    r"\b(1m|5m|15m|30m|1h|4h|6h|12h|1d|1w|daily|weekly|intraday)\b",
    re.I,
)
_SIDE = re.compile(
    r"\b(long|short|buy|sell|accumulate|accumulation|scalp|swing|hedge)\b",
    re.I,
)


def parse_trade_intent(query: str) -> Dict[str, Any]:
    q = query.strip()
    upper = q.upper()
    symbols: List[str] = []
    for sym in _SYMBOLS:
        if re.search(rf"\b{re.escape(sym)}\b", upper):
            symbols.append(sym)
    # $BTC style
    for m in re.finditer(r"\$([A-Z]{2,10})\b", upper):
        if m.group(1) not in symbols:
            symbols.append(m.group(1))

    tfs = [m.group(1).lower() for m in _TF.finditer(q)]
    sides = [m.group(1).lower() for m in _SIDE.finditer(q)]

    primary = symbols[0] if symbols else None
    timeframe = tfs[0] if tfs else None
    bias = sides[0] if sides else "neutral"

    # Normalize bias
    if bias in ("buy", "long", "accumulate", "accumulation"):
        stance = "long_bias"
    elif bias in ("sell", "short"):
        stance = "short_bias"
    elif bias in ("scalp", "swing", "hedge"):
        stance = bias
    else:
        stance = "neutral"

    return {
        "primary_symbol": primary or "BTC",
        "symbols": symbols or (["BTC"] if not primary else symbols),
        "timeframe": timeframe or "4h",
        "stance": stance,
        "raw_query": q,
        "symbol_explicit": primary is not None,
        "timeframe_explicit": timeframe is not None,
    }


def build_structured_plan(intent: Dict[str, Any], nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    sym = intent["primary_symbol"]
    tf = intent["timeframe"]
    stance = intent["stance"]

    if stance == "long_bias":
        setup = "Setup A — Iron Accumulation (ladder into strength)"
        invalidation = f"Daily close structure break under recent swing low on {sym}"
        risk = "0.5–1.0% equity per idea; max 3 concurrent"
    elif stance == "short_bias":
        setup = "Fade / breakdown continuation (only with HTF LH/LL)"
        invalidation = f"Reclaim of breakdown pivot on {sym} {tf}"
        risk = "0.5% equity; reduce size in risk-off headlines"
    else:
        setup = "Observe / prepare levels — no forced entry"
        invalidation = "N/A until setup triggers"
        risk = "Cash preserved until confluence ≥ threshold"

    return {
        "symbol": sym,
        "timeframe": tf,
        "stance": stance,
        "setup": setup,
        "checklist": [
            "Liquidity tier S/A only (tight book)",
            "HTF bias labeled (TREND_UP / RANGE / TREND_DOWN)",
            "Invalidation price pre-defined",
            "RR ≥ 1:2 planned before entry",
            "Session reuse for multi-turn refinement",
        ],
        "invalidation": invalidation,
        "risk": risk,
        "jspace_focus": [n.get("label") for n in nodes[:5]],
        "next_actions": [
            f"Map {sym} {tf} structure (HH/HL vs LH/LL)",
            "Mark range high/low and mid",
            "Size only after confluence score",
            "Prefer dry-run / paper before live",
        ],
    }


def synthesize_report(intent: Dict[str, Any], plan: Dict[str, Any]) -> str:
    sym = plan["symbol"]
    tf = plan["timeframe"]
    return (
        f"**{sym} · {tf}** — stance `{plan['stance']}`.\n"
        f"{plan['setup']}.\n"
        f"Risk: {plan['risk']}.\n"
        f"Invalidation: {plan['invalidation']}.\n"
        f"Next: {'; '.join(plan['next_actions'][:2])}."
    )
