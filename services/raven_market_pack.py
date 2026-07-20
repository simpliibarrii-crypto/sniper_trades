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
) -> Dict[str, Any]:
    """
    Pull ticker + candles for primary TF and a multi-TF stack from free
    public APIs (Binance → Kraken → Coinbase → Crypto.com).

    Always includes short scales (1m, 5m) when the primary is intraday so
    RavenTrader can cross-check micro structure.
    """
    raw = symbol.strip()
    if "_" not in raw and not raw.upper().endswith(("USDT", "USD", "USDC")):
        raw = f"{raw}_USDT"
    inst = free_market.normalize_instrument(raw)

    # Short-scale stack for free 1m feeds
    short_stack = ("1m", "5m", "15m")
    tfs: List[str] = []
    for tf in [primary_tf, *(extra_tfs or []), *short_stack, *MTF_STACK]:
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

    # More bars on 1m for usable RSI/MACD; fewer API weight on higher TF
    def _count_for(tf: str) -> int:
        if tf in ("1m", "3m"):
            return max(candle_count, 180)
        if tf in ("5m", "15m"):
            return max(candle_count, 120)
        return candle_count

    timeframes: Dict[str, Any] = {}
    sources_used: List[str] = []
    for tf in tfs[:8]:  # allow 1m+5m+15m+1h+4h+1D etc.
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
        "feed": "free_public",
    }
