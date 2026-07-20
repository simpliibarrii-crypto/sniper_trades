"""Fetch multi-timeframe free market pack for RavenTrader (1m capable)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.raven_trader import MTF_STACK
from services import free_market


def build_market_pack(
    symbol: str,
    primary_tf: str = "4h",
    extra_tfs: Optional[List[str]] = None,
    candle_count: int = 120,
    fast: bool = False,
) -> Dict[str, Any]:
    """
    Pull ticker + candles for primary TF and a multi-TF stack from free
    public APIs (Binance → Kraken → Coinbase → Crypto.com), via SQLite ledger cache.

    fast=True: chart-priority pack — primary + a few key TFs only (much faster).
    """
    raw = symbol.strip()
    if "_" not in raw and not raw.upper().endswith(("USDT", "USD", "USDC")):
        raw = f"{raw}_USDT"
    inst = free_market.normalize_instrument(raw)

    # Short-scale stack for free 1m feeds
    if fast:
        short_stack = ("15m", "1h", "4h")  # primary already included
    else:
        short_stack = ("1m", "5m", "15m")
    tfs: List[str] = []
    stack = [primary_tf, *(extra_tfs or []), *short_stack]
    if not fast:
        stack.extend(MTF_STACK)
    for tf in stack:
        t = (tf or "").strip()
        if not t:
            continue
        if t.lower() in ("daily", "1d", "d"):
            t = "1D"
        if t.lower() in ("weekly", "1w", "w"):
            t = "1W"
        if t not in tfs:
            tfs.append(t)

    ticker: Dict[str, Any] = {}
    try:
        ticker = free_market.get_ticker(inst)
    except Exception as exc:  # noqa: BLE001
        ticker = {"source": "error", "instrument": inst, "error": str(exc)}

    def _count_for(tf: str) -> int:
        if tf == primary_tf:
            return max(candle_count, 120)
        if tf in ("1m", "3m"):
            return 120 if fast else max(candle_count, 120)
        if tf in ("5m", "15m"):
            return 100
        return 80 if fast else candle_count

    timeframes: Dict[str, Any] = {}
    sources_used: List[str] = []
    # primary first for chart paint
    ordered = [primary_tf] + [t for t in tfs if t != primary_tf]
    limit = 4 if fast else 8
    for tf in ordered[:limit]:
        try:
            pack = free_market.get_candles(inst, timeframe=tf, count=_count_for(tf))
            timeframes[tf] = pack
            src = pack.get("source")
            if src and src not in sources_used:
                sources_used.append(src)
        except Exception as exc:  # noqa: BLE001
            timeframes[tf] = {
                "source": "error",
                "instrument": inst,
                "timeframe": tf,
                "count": 0,
                "candles": [],
                "error": str(exc),
            }

    return {
        "instrument": inst,
        "ticker": ticker,
        "timeframes": timeframes,
        "data_sources": sources_used,
        "feed": "free_public+ledger",
        "fast": fast,
    }
