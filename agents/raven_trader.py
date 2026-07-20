"""
Sniper Trades — precision live AI crypto trading agent.

LIVE_SNIPER_TRADER_PROMPT + multi-timeframe analysis over free public OHLCV.
TradingView-style tools are computed locally (no TV dependency).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════
# Live Sniper system prompt (primary — copy into any AI agent)
# ═══════════════════════════════════════════════════════════════════════════

LIVE_SNIPER_TRADER_PROMPT = """
You are RavenTrade-Core, the crypto-only intelligence layer inside Sniper Trades.
You monitor liquid centralized-market pairs, crypto news, new DEX token profiles,
and supported on-chain signals. Macro information is used only when it changes
crypto risk. Never pretend the available feeds cover every token or metric.
The jspace / J-Space view is the visible decision-audit layer for evidence, bias, and counter-cases;
it is not a claim that private chain-of-thought or consciousness is exposed.

DEFENSE RULES:
- Capital preservation outranks activity. Default to Hold/cash when signals split.
- Risk at stop defaults to 1% and is hard-capped at 2%; meme coins should be lower.
- Never invent balances, fills, funding, open interest, liquidations, or wallet state.
- Never request a seed phrase or private key. A wallet must sign every transaction.
- "Trade Now" opens a reviewed ticket or external DEX handoff; it is not silent execution.
- Leverage remains locked unless collateral, liquidation distance, funding, open
  interest, and liquidation data are all verified.

LIVE TOOL RULES:
- Show observable evidence and concise decision rationale, never private chain-of-thought.
- Display supported local indicator equivalents: candles, predictive volume pressure,
  RSI, MACD, Bollinger Bands, ATR, moving averages, pivots, Fibonacci, simplified
  Ichimoku, volume profile, support/resistance, and approximate order blocks.
- Label approximations honestly; do not claim native TradingView drawing tools or Pine
  execution are active when they are not.
- Cross-check multiple timeframes and announce material tool flips.

RESPONSE FLOW:
1. Cognitive Core Status — evidence, bias check, counter-case.
2. Market Horizon Scan — price, volatility, multi-timeframe structure.
3. Shadow Network Intel — crypto news, regulation, on-chain/DEX discovery.
4. Tool Talons Deployed — live readings and predictive-volume state.
5. Decision Forge — Hold / Long / Short / Exit, invalidation, worst case.
6. Wallet Nest Status — connected public address data or explicitly disconnected.
7. Next Flight Path — Live Sniper Verdict and guarded next action; paper first.
""".strip()

# Back-compat alias used by older imports / skills
TRADER_SYSTEM_PROMPT = LIVE_SNIPER_TRADER_PROMPT

# Timeframes Sniper prefers to cross-check (includes free 1m feeds)
MTF_STACK = ("1m", "5m", "15m", "1h", "4h", "1D")


# ── pure-Python indicators ─────────────────────────────────────────────────


def _closes(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c["c"]) for c in candles if c.get("c") is not None]


def _highs(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c["h"]) for c in candles if c.get("h") is not None]


def _lows(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c["l"]) for c in candles if c.get("l") is not None]


def _vols(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c.get("v") or 0.0) for c in candles]


_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1D": 86_400_000,
    "1w": 604_800_000,
    "1W": 604_800_000,
}


def predictive_volume_pressure(
    candles: List[Dict[str, Any]],
    timeframe: str,
    *,
    now_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Forecast volume expansion and directional pressure from observable OHLCV.

    The result is a probabilistic pressure heuristic, not a promise about the next
    price. When the last candle is still open, its volume is projected to the close.
    """
    if len(candles) < 8:
        return {
            "bias": "unavailable",
            "pressure_score": 0.0,
            "confidence": 0,
            "current_ratio": None,
            "projected_ratio": None,
            "trend_ratio": None,
            "zscore": None,
            "is_live_candle": False,
            "read": "insufficient volume history",
        }

    now_ms = float(now_ms if now_ms is not None else time.time() * 1000)
    duration = _TIMEFRAME_MS.get(timeframe, _TIMEFRAME_MS.get(timeframe.lower(), 0))
    raw_t = candles[-1].get("t")
    try:
        opened_ms = float(raw_t)
        if 1_000_000_000 <= opened_ms < 100_000_000_000:
            opened_ms *= 1000
    except (TypeError, ValueError):
        opened_ms = 0.0
    is_live = bool(
        duration
        and opened_ms > 1_000_000_000_000
        and opened_ms <= now_ms < opened_ms + duration * 1.15
    )

    signal = candles[-1]
    history = candles[:-1]
    history_vols = [float(row.get("v") or 0) for row in history[-20:]]
    positive = [value for value in history_vols if value > 0]
    average = sum(positive) / len(positive) if positive else 0.0
    signal_volume = float(signal.get("v") or 0)
    current_ratio = signal_volume / average if average else None
    elapsed = 1.0
    if is_live and duration:
        elapsed = min(1.0, max(0.08, (now_ms - opened_ms) / duration))
    projected_volume = signal_volume / elapsed if is_live else signal_volume
    projected_ratio = projected_volume / average if average else None
    if projected_ratio is not None:
        projected_ratio = min(projected_ratio, 8.0)

    prior = history_vols[-10:]
    recent_mean = sum(prior[-3:]) / min(3, len(prior)) if prior else 0.0
    older = prior[:-3]
    older_mean = sum(older) / len(older) if older else average
    trend_ratio = recent_mean / older_mean if older_mean else None
    variance = (
        sum((value - average) ** 2 for value in positive) / len(positive)
        if positive
        else 0.0
    )
    zscore = (projected_volume - average) / (variance**0.5) if variance > 0 else 0.0

    open_px = float(signal.get("o") or signal.get("c") or 0)
    close_px = float(signal.get("c") or open_px)
    high = float(signal.get("h") or max(open_px, close_px))
    low = float(signal.get("l") or min(open_px, close_px))
    candle_range = max(high - low, abs(close_px) * 1e-9, 1e-12)
    close_location = max(-1.0, min(1.0, ((close_px - low) / candle_range) * 2 - 1))
    previous_close = float(history[-1].get("c") or open_px) if history else open_px
    return_sign = 1.0 if close_px > previous_close else -1.0 if close_px < previous_close else 0.0
    direction = close_location * 0.65 + return_sign * 0.35
    expansion = min(projected_ratio or 0.0, 2.5)
    trend_component = max(-1.0, min(1.0, (trend_ratio or 1.0) - 1.0))
    score = max(-100.0, min(100.0, direction * expansion * 38 + trend_component * 18))
    if score >= 22:
        bias = "bullish pressure"
    elif score <= -22:
        bias = "bearish pressure"
    else:
        bias = "neutral / unresolved"
    history_confidence = min(35, len(positive) * 2)
    signal_confidence = min(35, abs(score) * 0.35)
    expansion_confidence = min(20, abs((projected_ratio or 1.0) - 1.0) * 18)
    confidence = int(min(90, 10 + history_confidence + signal_confidence + expansion_confidence))
    ratio_text = f"{projected_ratio:.2f}x projected" if projected_ratio is not None else "volume n/a"
    return {
        "bias": bias,
        "pressure_score": round(score, 1),
        "confidence": confidence,
        "current_ratio": round(current_ratio, 3) if current_ratio is not None else None,
        "projected_ratio": round(projected_ratio, 3) if projected_ratio is not None else None,
        "trend_ratio": round(trend_ratio, 3) if trend_ratio is not None else None,
        "zscore": round(zscore, 2),
        "is_live_candle": is_live,
        "read": f"{bias}; {ratio_text}; pressure {score:+.1f}/100; confidence {confidence}%",
    }


def sma(series: List[float], n: int) -> Optional[float]:
    if len(series) < n:
        return None
    return sum(series[-n:]) / n


def ema_series(series: List[float], n: int) -> List[float]:
    if not series:
        return []
    k = 2 / (n + 1)
    out = [series[0]]
    for x in series[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def ema(series: List[float], n: int) -> Optional[float]:
    if len(series) < n:
        return None
    return ema_series(series, n)[-1]


def rsi(series: List[float], n: int = 14) -> Optional[float]:
    if len(series) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = series[i] - series[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - (100 / (1 + rs))


def macd(
    series: List[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Dict[str, Optional[float]]:
    if len(series) < slow + signal:
        return {"macd": None, "signal": None, "hist": None}
    ef = ema_series(series, fast)
    es = ema_series(series, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig = ema_series(line, signal)
    hist = line[-1] - sig[-1]
    return {"macd": line[-1], "signal": sig[-1], "hist": hist}


def bollinger(
    series: List[float], n: int = 20, k: float = 2.0
) -> Dict[str, Optional[float]]:
    if len(series) < n:
        return {"mid": None, "upper": None, "lower": None, "pct_b": None}
    window = series[-n:]
    mid = sum(window) / n
    var = sum((x - mid) ** 2 for x in window) / n
    std = var**0.5
    upper, lower = mid + k * std, mid - k * std
    pct_b = (series[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b}


def atr(candles: List[Dict[str, Any]], n: int = 14) -> Optional[float]:
    if len(candles) < n + 1:
        return None
    trs: List[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i].get("h") or 0)
        l = float(candles[i].get("l") or 0)
        pc = float(candles[i - 1].get("c") or 0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def stochastic(
    candles: List[Dict[str, Any]], n: int = 14
) -> Dict[str, Optional[float]]:
    if len(candles) < n:
        return {"k": None, "d": None}
    window = candles[-n:]
    hi = max(float(c["h"]) for c in window if c.get("h") is not None)
    lo = min(float(c["l"]) for c in window if c.get("l") is not None)
    close = float(candles[-1]["c"])
    if hi == lo:
        k = 50.0
    else:
        k = 100 * (close - lo) / (hi - lo)
    return {"k": k, "d": k}  # simplified %K; full %D needs multi-bar smooth


def obv_trend(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 5:
        return "n/a"
    obv = 0.0
    series = [0.0]
    for i in range(1, len(candles)):
        c0 = float(candles[i - 1].get("c") or 0)
        c1 = float(candles[i].get("c") or 0)
        v = float(candles[i].get("v") or 0)
        if c1 > c0:
            obv += v
        elif c1 < c0:
            obv -= v
        series.append(obv)
    if series[-1] > series[-5]:
        return "rising"
    if series[-1] < series[-5]:
        return "falling"
    return "flat"


def pivots(candles: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    if not candles:
        return {"p": None, "r1": None, "s1": None, "r2": None, "s2": None}
    # classic pivot from prior completed bar (use second-to-last if possible)
    bar = candles[-2] if len(candles) > 1 else candles[-1]
    h, l, c = float(bar["h"]), float(bar["l"]), float(bar["c"])
    p = (h + l + c) / 3
    r1 = 2 * p - l
    s1 = 2 * p - h
    r2 = p + (h - l)
    s2 = p - (h - l)
    return {"p": p, "r1": r1, "s1": s1, "r2": r2, "s2": s2}


def fib_levels(candles: List[Dict[str, Any]], lookback: int = 50) -> Dict[str, Optional[float]]:
    if len(candles) < 5:
        return {}
    window = candles[-lookback:]
    hi = max(float(c["h"]) for c in window if c.get("h") is not None)
    lo = min(float(c["l"]) for c in window if c.get("l") is not None)
    diff = hi - lo
    if diff <= 0:
        return {"swing_high": hi, "swing_low": lo}
    # assume last swing direction from position in range
    mid = (hi + lo) / 2
    last = float(candles[-1]["c"])
    # retracement from high (for pullbacks in uptrend) and extensions
    return {
        "swing_high": hi,
        "swing_low": lo,
        "fib_0.236": hi - 0.236 * diff,
        "fib_0.382": hi - 0.382 * diff,
        "fib_0.5": hi - 0.5 * diff,
        "fib_0.618": hi - 0.618 * diff,
        "fib_0.786": hi - 0.786 * diff,
        "near_mid": abs(last - mid) / diff < 0.08,
    }


def candle_pattern(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 2:
        return "insufficient"
    a, b = candles[-2], candles[-1]
    o, h, l, c = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])
    body = abs(c - o)
    rng = max(h - l, 1e-12)
    upper = h - max(o, c)
    lower = min(o, c) - l
    prev_o, prev_c = float(a["o"]), float(a["c"])
    if body / rng < 0.12:
        return "doji / indecision"
    if lower > body * 2 and upper < body * 0.5 and c > o:
        return "hammer (bullish rejection)"
    if upper > body * 2 and lower < body * 0.5 and c < o:
        return "shooting star (bearish rejection)"
    if prev_c < prev_o and c > o and c >= prev_o and o <= prev_c:
        return "bullish engulfing"
    if prev_c > prev_o and c < o and c <= prev_o and o >= prev_c:
        return "bearish engulfing"
    if c > o:
        return "bullish candle"
    return "bearish candle"


def ichimoku_bias(series: List[float]) -> str:
    if len(series) < 52:
        return "n/a (need 52 bars)"
    # tenkan 9, kijun 26 simplified from closes
    def mid_hi_lo(window: List[float]) -> float:
        return (max(window) + min(window)) / 2

    tenkan = mid_hi_lo(series[-9:])
    kijun = mid_hi_lo(series[-26:])
    # span A/B approx
    span_a = (tenkan + kijun) / 2
    span_b = mid_hi_lo(series[-52:])
    price = series[-1]
    if price > span_a and price > span_b and tenkan > kijun:
        return "bullish (price above cloud, TK cross up)"
    if price < span_a and price < span_b and tenkan < kijun:
        return "bearish (price below cloud, TK cross down)"
    return "mixed / in cloud"


def volume_profile_hint(candles: List[Dict[str, Any]], bins: int = 8) -> Dict[str, Any]:
    if len(candles) < 10:
        return {"poc": None, "note": "insufficient volume history"}
    prices = _closes(candles)
    vols = _vols(candles)
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return {"poc": prices[-1], "note": "flat range"}
    width = (hi - lo) / bins
    buckets = [0.0] * bins
    for p, v in zip(prices, vols):
        idx = min(bins - 1, int((p - lo) / width))
        buckets[idx] += v
    best = max(range(bins), key=lambda i: buckets[i])
    poc = lo + (best + 0.5) * width
    return {"poc": poc, "note": f"POC ~ {poc:.4f} (highest volume node)"}


# ── timeframe analysis ─────────────────────────────────────────────────────


def analyze_timeframe(
    candles: List[Dict[str, Any]],
    timeframe: str,
    ticker: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    closes = _closes(candles)
    last = closes[-1] if closes else (ticker or {}).get("last")
    tools: List[Dict[str, str]] = []

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    ema21 = ema(closes, 21)
    tools.append(
        {
            "tool": "Moving Averages (SMA20/50, EMA21)",
            "reading": (
                f"price={_fmt(last)} SMA20={_fmt(sma20)} SMA50={_fmt(sma50)} EMA21={_fmt(ema21)}"
            ),
        }
    )

    r = rsi(closes)
    tools.append(
        {
            "tool": "RSI(14)",
            "reading": (
                f"{_fmt(r, 1)}"
                + (
                    " overbought"
                    if r and r > 70
                    else " oversold"
                    if r and r < 30
                    else " neutral zone"
                    if r
                    else " n/a"
                )
            ),
        }
    )

    m = macd(closes)
    tools.append(
        {
            "tool": "MACD(12,26,9)",
            "reading": (
                f"line={_fmt(m['macd'], 4)} signal={_fmt(m['signal'], 4)} "
                f"hist={_fmt(m['hist'], 4)}"
                + (
                    " bullish hist"
                    if m["hist"] and m["hist"] > 0
                    else " bearish hist"
                    if m["hist"] and m["hist"] < 0
                    else ""
                )
            ),
        }
    )

    bb = bollinger(closes)
    tools.append(
        {
            "tool": "Bollinger Bands(20,2)",
            "reading": (
                f"upper={_fmt(bb['upper'])} mid={_fmt(bb['mid'])} lower={_fmt(bb['lower'])} "
                f"%B={_fmt(bb['pct_b'], 3)}"
            ),
        }
    )

    a = atr(candles)
    tools.append({"tool": "ATR(14)", "reading": f"{_fmt(a)} (volatility ruler)"})

    st = stochastic(candles)
    tools.append(
        {
            "tool": "Stochastic(14)",
            "reading": f"%K={_fmt(st['k'], 1)}"
            + (
                " overbought"
                if st["k"] and st["k"] > 80
                else " oversold"
                if st["k"] and st["k"] < 20
                else ""
            ),
        }
    )

    tools.append(
        {"tool": "On-Balance Volume", "reading": f"trend={obv_trend(candles)}"}
    )
    tools.append(
        {"tool": "Candlestick pattern", "reading": candle_pattern(candles)}
    )
    tools.append(
        {"tool": "Ichimoku (simplified)", "reading": ichimoku_bias(closes)}
    )

    pv = pivots(candles)
    tools.append(
        {
            "tool": "Pivot Points",
            "reading": f"P={_fmt(pv['p'])} R1={_fmt(pv['r1'])} S1={_fmt(pv['s1'])}",
        }
    )

    fib = fib_levels(candles)
    tools.append(
        {
            "tool": "Fibonacci retracements",
            "reading": (
                f"swing { _fmt(fib.get('swing_low')) }→{ _fmt(fib.get('swing_high')) }; "
                f"0.382={_fmt(fib.get('fib_0.382'))} 0.5={_fmt(fib.get('fib_0.5'))} "
                f"0.618={_fmt(fib.get('fib_0.618'))}"
            ),
        }
    )

    vp = volume_profile_hint(candles)
    tools.append(
        {
            "tool": "Volume Profile (POC approx)",
            "reading": vp.get("note", "n/a"),
        }
    )

    # Support / resistance from recent swings
    if len(candles) >= 20:
        recent = candles[-30:]
        res = max(float(c["h"]) for c in recent if c.get("h") is not None)
        sup = min(float(c["l"]) for c in recent if c.get("l") is not None)
        tools.append(
            {
                "tool": "Support / Resistance",
                "reading": f"local support≈{_fmt(sup)} resistance≈{_fmt(res)}",
            }
        )
    else:
        res = sup = None

    # Structure / Wyckoff-ish volume at extremes
    vols = _vols(candles)
    avg_vol = sum(vols[-20:]) / min(20, len(vols)) if vols else 0
    last_vol = vols[-1] if vols else 0
    vol_note = (
        "high volume"
        if avg_vol and last_vol > avg_vol * 1.5
        else "low volume"
        if avg_vol and last_vol < avg_vol * 0.6
        else "average volume"
    )
    tools.append(
        {
            "tool": "Volume / Wyckoff effort",
            "reading": f"{vol_note} (last={_fmt(last_vol, 2)} vs avg={_fmt(avg_vol, 2)})",
        }
    )

    volume_forecast = predictive_volume_pressure(candles, timeframe)
    tools.append(
        {
            "tool": "Predictive Volume Engine",
            "reading": volume_forecast["read"],
        }
    )

    # Order blocks (approx last strong impulse candle)
    tools.append(
        {
            "tool": "Order Blocks (approx)",
            "reading": _order_block_hint(candles),
        }
    )

    bias_score, bias_label = _score_bias(closes, r, m, bb, sma20, sma50)
    tools.append(
        {
            "tool": "Composite TF bias",
            "reading": f"{bias_label} (score={bias_score:+.2f})",
        }
    )

    return {
        "timeframe": timeframe,
        "last": last,
        "tools": tools,
        "bias_score": bias_score,
        "bias_label": bias_label,
        "rsi": r,
        "atr": a,
        "sma20": sma20,
        "sma50": sma50,
        "pivots": pv,
        "fib": fib,
        "support": sup if len(candles) >= 20 else (pv.get("s1") if pv else None),
        "resistance": res if len(candles) >= 20 else (pv.get("r1") if pv else None),
        "volume_forecast": volume_forecast,
        "bars": len(candles),
    }


def _order_block_zone(candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(candles) < 6:
        return None
    best_i, best_body = -1, 0.0
    start = max(0, len(candles) - 20)
    for i in range(start, len(candles) - 1):
        c = candles[i]
        body = abs(float(c["c"]) - float(c["o"]))
        if body > best_body:
            best_body = body
            best_i = i
    if best_i < 0:
        return None
    c = candles[best_i]
    o, cl = float(c["o"]), float(c["c"])
    bull = cl > o
    return {
        "index": best_i,
        "top": max(o, cl),
        "bottom": min(o, cl),
        "side": "bullish" if bull else "bearish",
        "label": "Bull OB" if bull else "Bear OB",
    }


def _order_block_hint(candles: List[Dict[str, Any]]) -> str:
    z = _order_block_zone(candles)
    if not z:
        return "n/a"
    return (
        f"{z['label']} near {_fmt(z['bottom'])}–{_fmt(z['top'])} (recent impulse)"
    )


def _rolling_sma_series(series: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(series)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(sum(series[i + 1 - n : i + 1]) / n)
    return out


def _bb_series(
    series: List[float], n: int = 20, k: float = 2.0
) -> Dict[str, List[Optional[float]]]:
    mid: List[Optional[float]] = []
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    for i in range(len(series)):
        if i + 1 < n:
            mid.append(None)
            upper.append(None)
            lower.append(None)
            continue
        window = series[i + 1 - n : i + 1]
        m = sum(window) / n
        var = sum((x - m) ** 2 for x in window) / n
        std = var**0.5
        mid.append(m)
        upper.append(m + k * std)
        lower.append(m - k * std)
    return {"mid": mid, "upper": upper, "lower": lower}


def build_chart_overlay(
    candles: List[Dict[str, Any]],
    analysis: Optional[Dict[str, Any]],
    decision: Optional[Dict[str, Any]] = None,
    max_bars: int = 120,
) -> Dict[str, Any]:
    """Numeric layers the UI draws on the main chart.

    Series length always matches the returned candle window (last max_bars).
    Levels far from candle range are omitted so scale stays readable.
    """
    if max_bars and len(candles) > max_bars:
        candles = candles[-max_bars:]
    closes = _closes(candles)
    vols = _vols(candles)
    n = len(closes)
    candle_high = max(
        (float(c["h"]) for c in candles if c.get("h") is not None), default=None
    )
    candle_low = min(
        (float(c["l"]) for c in candles if c.get("l") is not None), default=None
    )
    ema21_full = ema_series(closes, 21) if n else []
    if len(ema21_full) < n:
        ema21_full = [None] * (n - len(ema21_full)) + list(ema21_full)  # type: ignore[list-item]
    bb = _bb_series(closes, 20, 2.0)
    analysis = analysis or {}
    decision = decision or {}
    fib = analysis.get("fib") or {}
    piv = analysis.get("pivots") or {}
    levels: List[Dict[str, Any]] = []

    def _in_band(price: float, pad_mult: float = 0.12) -> bool:
        if candle_high is None or candle_low is None:
            return True
        pad = (candle_high - candle_low) * pad_mult or abs(candle_high) * 0.01
        return (candle_low - pad) <= price <= (candle_high + pad)

    def add_level(
        lid: str,
        price: Any,
        label: str,
        kind: str,
        color: str,
        *,
        require_in_band: bool = True,
    ) -> None:
        if price is None:
            return
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        if require_in_band and not _in_band(p):
            return
        levels.append(
            {"id": lid, "price": p, "label": label, "kind": kind, "color": color}
        )

    add_level("support", analysis.get("support"), "Support", "sr", "#22c55e")
    add_level("resistance", analysis.get("resistance"), "Resistance", "sr", "#ef4444")
    add_level("pivot", piv.get("p"), "Pivot", "pivot", "#60a5fa")
    add_level("r1", piv.get("r1"), "R1", "pivot", "#f87171")
    add_level("s1", piv.get("s1"), "S1", "pivot", "#4ade80")
    add_level("r2", piv.get("r2"), "R2", "pivot", "#f87171")
    add_level("s2", piv.get("s2"), "S2", "pivot", "#4ade80")
    for key, lab in (
        ("fib_0.236", "Fib 23.6%"),
        ("fib_0.382", "Fib 38.2%"),
        ("fib_0.5", "Fib 50%"),
        ("fib_0.618", "Fib 61.8%"),
        ("fib_0.786", "Fib 78.6%"),
        ("swing_high", "Swing High"),
        ("swing_low", "Swing Low"),
    ):
        add_level(key, fib.get(key), lab, "fib", "#eab308")

    # trade plan markers
    markers: List[Dict[str, Any]] = []
    for mid, key, lab, color in (
        ("entry", "entry", "ENTRY", "#a855f7"),
        ("stop", "stop_loss", "STOP", "#ef4444"),
        ("tp1", "take_profit_1", "TP1", "#22c55e"),
        ("tp2", "take_profit_2", "TP2", "#14b8a6"),
    ):
        px = decision.get(key)
        if px is None:
            continue
        markers.append(
            {
                "id": mid,
                "price": float(px),
                "label": lab,
                "color": color,
                "side": decision.get("direction") or decision.get("side"),
                "in_band": _in_band(float(px), pad_mult=1.5),
            }
        )

    zone = _order_block_zone(candles)
    zones: List[Dict[str, Any]] = []
    if zone:
        zones.append(
            {
                "id": "order_block",
                "top": zone["top"],
                "bottom": zone["bottom"],
                "index": zone["index"],
                "side": zone["side"],
                "label": zone["label"],
            }
        )

    tools_active = [
        "Candles",
        "Volume",
        "SMA20",
        "SMA50",
        "EMA21",
        "Bollinger",
        "RSI",
        "ATR",
        "MACD",
        "Stochastic",
        "Fib",
        "Pivots",
        "S/R",
        "Order Block",
        "Ichimoku",
        "Volume Profile",
    ]
    return {
        "timeframe": analysis.get("timeframe"),
        "bars": n,
        "scale_hint": {"candle_high": candle_high, "candle_low": candle_low},
        "series": {
            "sma20": _rolling_sma_series(closes, 20),
            "sma50": _rolling_sma_series(closes, 50),
            "ema21": ema21_full,
            "bb_mid": bb["mid"],
            "bb_upper": bb["upper"],
            "bb_lower": bb["lower"],
            "volume": vols,
        },
        "levels": levels,
        "zones": zones,
        "markers": markers,
        "stats": {
            "rsi": analysis.get("rsi"),
            "atr": analysis.get("atr"),
            "sma20": analysis.get("sma20"),
            "sma50": analysis.get("sma50"),
            "bias_label": analysis.get("bias_label"),
            "bias_score": analysis.get("bias_score"),
            "pattern": next(
                (
                    t.get("reading")
                    for t in (analysis.get("tools") or [])
                    if t.get("tool") == "Candlestick pattern"
                ),
                None,
            ),
            "ichimoku": next(
                (
                    t.get("reading")
                    for t in (analysis.get("tools") or [])
                    if "Ichimoku" in str(t.get("tool") or "")
                ),
                None,
            ),
            "macd": next(
                (
                    t.get("reading")
                    for t in (analysis.get("tools") or [])
                    if str(t.get("tool") or "").startswith("MACD")
                ),
                None,
            ),
            "stoch": next(
                (
                    t.get("reading")
                    for t in (analysis.get("tools") or [])
                    if "Stochastic" in str(t.get("tool") or "")
                ),
                None,
            ),
            "obv": next(
                (
                    t.get("reading")
                    for t in (analysis.get("tools") or [])
                    if "On-Balance" in str(t.get("tool") or "")
                ),
                None,
            ),
            "volume_forecast": analysis.get("volume_forecast"),
            "poc": (analysis.get("fib") and None)
            or next(
                (
                    t.get("reading")
                    for t in (analysis.get("tools") or [])
                    if "Volume Profile" in str(t.get("tool") or "")
                ),
                None,
            ),
        },
        "tools_active": tools_active,
        "tools_detail": analysis.get("tools") or [],
    }


def _score_bias(
    closes: List[float],
    r: Optional[float],
    m: Dict[str, Optional[float]],
    bb: Dict[str, Optional[float]],
    sma20: Optional[float],
    sma50: Optional[float],
) -> Tuple[float, str]:
    score = 0.0
    if not closes:
        return 0.0, "no data"
    last = closes[-1]
    if sma20 and last > sma20:
        score += 0.35
    elif sma20 and last < sma20:
        score -= 0.35
    if sma50 and last > sma50:
        score += 0.35
    elif sma50 and last < sma50:
        score -= 0.35
    if sma20 and sma50:
        if sma20 > sma50:
            score += 0.25
        else:
            score -= 0.25
    if r is not None:
        if r > 55:
            score += 0.15
        elif r < 45:
            score -= 0.15
        if r > 75:
            score -= 0.2  # overbought caution
        if r < 25:
            score += 0.2  # oversold bounce potential
    hist = m.get("hist")
    if hist is not None:
        score += 0.25 if hist > 0 else -0.25
    pct_b = bb.get("pct_b")
    if pct_b is not None:
        if pct_b > 1.0:
            score -= 0.1
        elif pct_b < 0.0:
            score += 0.1
    if score >= 0.55:
        label = "TREND_UP"
    elif score <= -0.55:
        label = "TREND_DOWN"
    else:
        label = "RANGE / MIXED"
    return score, label


def _fmt(v: Any, d: int = 2) -> str:
    if v is None:
        return "n/a"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.{d}f}"
    if abs(x) >= 1:
        return f"{x:.{d}f}"
    return f"{x:.{max(d, 4)}f}"


# ── full Sniper Trades live decision ───────────────────────────────────────


def raven_analyze(
    intent: Dict[str, Any],
    market: Dict[str, Any],
    nodes: Optional[List[Dict[str, Any]]] = None,
    prior_position: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build Sniper Trades live response from intent + multi-TF market pack.

    market schema:
      {
        "instrument": "BTC_USDT",
        "ticker": {...},
        "timeframes": { "1h": {"candles":[...], "source":"..."}, ... }
      }
    """
    sym = intent.get("primary_symbol") or "BTC"
    user_tf = intent.get("timeframe") or "4h"
    stance_hint = intent.get("stance") or "neutral"
    instrument = market.get("instrument") or f"{sym}_USDT"
    ticker = market.get("ticker") or {}
    tfs: Dict[str, Any] = market.get("timeframes") or {}
    data_sources = market.get("data_sources") or []

    analyses: Dict[str, Dict[str, Any]] = {}
    for tf, pack in tfs.items():
        candles = pack.get("candles") or []
        if candles:
            analyses[tf] = analyze_timeframe(candles, tf, ticker)

    primary = analyses.get(user_tf) or next(iter(analyses.values()), None)
    last = (
        (primary or {}).get("last")
        or ticker.get("last")
        or ticker.get("ask")
        or ticker.get("bid")
    )

    # Aggregate multi-TF score
    scores = [a["bias_score"] for a in analyses.values()]
    mtf_score = sum(scores) / len(scores) if scores else 0.0
    labels = [f"{tf}:{a['bias_label']}" for tf, a in analyses.items()]
    bull_tfs = sum(1 for a in analyses.values() if a["bias_score"] > 0.3)
    bear_tfs = sum(1 for a in analyses.values() if a["bias_score"] < -0.3)
    aligned = [
        tf
        for tf, analysis in analyses.items()
        if analysis["bias_score"] * mtf_score > 0
        and abs(analysis["bias_score"]) > 0.3
    ]

    # User stance vs market — detect confirmation bias
    bias_notes: List[str] = []
    if stance_hint == "long_bias" and mtf_score < -0.2:
        bias_notes.append(
            "User asked long, but multi-TF composite is weak/bearish — "
            "countering FOMO by requiring structure + RR≥1.5 before entry."
        )
    if stance_hint == "short_bias" and mtf_score > 0.2:
        bias_notes.append(
            "User asked short against a constructive multi-TF tape — "
            "countering bearish confirmation bias; will not short strength."
        )
    if stance_hint == "long_bias" and mtf_score > 0.4:
        bias_notes.append(
            "Long bias aligns with composite — countering overconfidence by "
            "checking lower-TF RSI/volume for exhaustion."
        )
    if stance_hint == "scalp":
        bias_notes.append(
            "Scalp framing can force trades — countering by demanding 1m+5m alignment."
        )
    if not bias_notes:
        bias_notes.append(
            "No strong stance conflict; still scanning for volume/RSI divergence "
            "and tool flips that kill the sniper thesis."
        )

    # Direction comes from market evidence, never merely from the user's requested bias.
    if mtf_score >= 0.45 and stance_hint != "short_bias" and bull_tfs >= 2:
        direction = "Long"
    elif mtf_score <= -0.45 and stance_hint != "long_bias" and bear_tfs >= 2:
        direction = "Short"
    else:
        direction = "Hold"

    atr_v = (primary or {}).get("atr")
    support = (primary or {}).get("support")
    resistance = (primary or {}).get("resistance")
    rsi_v = (primary or {}).get("rsi")
    volume_forecast = (primary or {}).get("volume_forecast") or {}

    if last and atr_v:
        if direction == "Long":
            entry = float(last)
            stop = max(entry - 1.5 * atr_v, float(support or entry - 2 * atr_v))
            tp1 = entry + 2.0 * atr_v
            if resistance and resistance > entry:
                tp1 = max(tp1, float(resistance))
            tp2 = entry + 3.0 * atr_v
            leverage = "1x spot only — derivatives locked until funding/OI/liquidations are verified"
        elif direction == "Short":
            entry = float(last)
            stop = min(entry + 1.5 * atr_v, float(resistance or entry + 2 * atr_v))
            tp1 = entry - 2.0 * atr_v
            if support and support < entry:
                tp1 = min(tp1, float(support))
            tp2 = entry - 3.0 * atr_v
            leverage = "paper/reduce-only — leveraged shorts locked without derivatives telemetry"
        else:
            entry = float(last)
            stop = entry - 1.5 * atr_v
            tp1 = entry + 1.5 * atr_v
            tp2 = entry + 2.5 * atr_v
            leverage = "0x — no new risk until high-probability sniper setup"
    else:
        entry = last
        stop = tp1 = tp2 = None
        leverage = "n/a until price/ATR available"

    # RR
    rr = None
    if entry and stop and tp1 and abs(entry - stop) > 0:
        rr = abs(tp1 - entry) / abs(entry - stop)

    gate_reasons: List[str] = []
    projected_ratio = volume_forecast.get("projected_ratio")
    volume_bias = str(volume_forecast.get("bias") or "unavailable")
    if direction in ("Long", "Short") and len(analyses) < 2:
        gate_reasons.append("fewer than two verified timeframes")
    if direction in ("Long", "Short") and bull_tfs and bear_tfs:
        gate_reasons.append("bullish and bearish timeframes conflict")
    if direction == "Long" and volume_bias == "bearish pressure":
        gate_reasons.append("predictive volume pressure opposes the long")
    if direction == "Short" and volume_bias == "bullish pressure":
        gate_reasons.append("predictive volume pressure opposes the short")
    if direction in ("Long", "Short") and projected_ratio is not None and projected_ratio < 0.6:
        gate_reasons.append("projected volume is below 0.6x baseline")
    if direction in ("Long", "Short") and rr is not None and rr < 1.5:
        gate_reasons.append("risk/reward is below 1.5")

    # High-probability gate: any unresolved hard conflict defaults to cash.
    if direction in ("Long", "Short") and gate_reasons:
        direction = "Hold"
        leverage = "0x — defense gate blocked new risk"
        size_pct = 0.0
        risk_note = "0% — " + "; ".join(gate_reasons)
    elif direction == "Hold":
        size_pct = 0.0
        risk_note = "0% — observe only"
    elif rr and rr >= 2.0:
        size_pct = 1.0
        risk_note = "≤1.0% portfolio risk at stop"
    elif rr and rr >= 1.5:
        size_pct = 0.75
        risk_note = "≤0.75% portfolio risk at stop"
    else:
        size_pct = 0.5
        risk_note = "≤0.5% portfolio risk (edge thinner)"

    # Exit if prior paper position is invalidated by price
    verdict = direction  # Long | Short | Hold | Exit
    if prior_position and prior_position.get("side") in ("Long", "Short", "BUY", "SELL"):
        side = prior_position.get("side")
        if side == "BUY":
            side = "Long"
        if side == "SELL":
            side = "Short"
        p_stop = prior_position.get("stop_loss")
        p_tp = prior_position.get("take_profit_1") or prior_position.get("take_profit")
        if last and p_stop is not None:
            if side == "Long" and float(last) <= float(p_stop):
                verdict = "Exit"
            if side == "Short" and float(last) >= float(p_stop):
                verdict = "Exit"
        if last and p_tp is not None and verdict != "Exit":
            if side == "Long" and float(last) >= float(p_tp):
                verdict = "Exit"
            if side == "Short" and float(last) <= float(p_tp):
                verdict = "Exit"
        if verdict == "Exit":
            direction = "Exit"

    # Tools list (Active TradingView Analysis)
    tools_lines: List[str] = []
    for tf, a in analyses.items():
        tools_lines.append(f"[{tf}] bars={a['bars']} bias={a['bias_label']}")
        for t in a["tools"]:
            tools_lines.append(f"  • {t['tool']}: {t['reading']}")

    if not tools_lines:
        tools_lines.append(
            "No live candles available — analysis is intent-only; refresh free market feed."
        )

    # Confluences
    confluences: List[str] = []
    if len(aligned) >= 3:
        confluences.append(f"MTF alignment on {', '.join(aligned)} (sniper confluence)")
    if rsi_v is not None and direction == "Long" and 40 <= rsi_v <= 65:
        confluences.append("RSI mid-zone supports trend continuation (not exhausted)")
    if rsi_v is not None and direction == "Short" and 35 <= rsi_v <= 60:
        confluences.append("RSI not oversold — room for continuation short")
    if primary and primary.get("support") and last and direction == "Long":
        if abs(float(last) - float(primary["support"])) / float(last) < 0.01:
            confluences.append("Price hugging support — sniper long zone")
    if primary and primary.get("resistance") and last and direction == "Short":
        if abs(float(last) - float(primary["resistance"])) / float(last) < 0.01:
            confluences.append("Price at resistance — sniper short zone")
    if not confluences:
        confluences.append("No stacked high-probability confluence yet — patience.")

    # Conflicts / tool flips
    conflicts: List[str] = []
    tool_flips: List[str] = []
    if rsi_v and rsi_v > 70 and direction == "Long":
        conflicts.append("RSI overbought vs Long → reduce size or wait pullback")
        tool_flips.append("RSI flip risk: overbought")
    if rsi_v and rsi_v < 30 and direction == "Short":
        conflicts.append("RSI oversold vs Short → squeeze risk")
        tool_flips.append("RSI flip risk: oversold bounce")
    if bull_tfs and bear_tfs:
        conflicts.append(
            f"MTF split: {bull_tfs} bullish TF vs {bear_tfs} bearish TF → Hold bias"
        )
        tool_flips.append("Composite TF bias split")
    for reason in gate_reasons:
        note = f"Defense gate: {reason}"
        if note not in conflicts:
            conflicts.append(note)
    if not conflicts:
        conflicts.append("No major hard conflicts; residual risk is news/gap and liquidity.")

    # Conviction 0–100
    conviction = _conviction(mtf_score, rr, len(aligned), bull_tfs, bear_tfs, direction)

    jspace = _build_jspace(
        instrument=instrument,
        user_tf=user_tf,
        stance_hint=stance_hint,
        direction=direction,
        mtf_score=mtf_score,
        labels=labels,
        bias_notes=bias_notes,
        conflicts=conflicts,
        confluences=confluences,
        last=last,
        ticker=ticker,
        data_sources=data_sources,
        nodes=nodes or [],
        conviction=conviction,
    )

    tv_analysis = _build_tv_analysis(
        tools_lines=tools_lines,
        confluences=confluences,
        tool_flips=tool_flips,
        labels=labels,
        primary=primary,
        last=last,
    )

    next_action = _next_action(direction, instrument, user_tf, stop, entry, rr)
    strategy_position = {
        "status": "proposed" if direction in ("Long", "Short") else "flat",
        "side": direction if direction in ("Long", "Short", "Exit") else "Flat",
        "instrument": instrument,
        "entry": entry if direction in ("Long", "Short") else None,
        "stop_loss": stop if direction in ("Long", "Short") else None,
        "take_profit_1": tp1 if direction in ("Long", "Short") else None,
        "take_profit_2": tp2 if direction in ("Long", "Short") else None,
        "leverage": leverage,
        "position_size_pct": size_pct,
        "risk_note": risk_note,
        "risk_reward": round(rr, 2) if rr else None,
        "next_action": next_action,
        "prior": prior_position,
    }

    verdict_payload = {
        "verdict": direction if direction != "Hold" else "Hold",
        "conviction": conviction,
        "conviction_label": _conviction_label(conviction),
        "mtf_score": round(mtf_score, 3),
        "mtf_labels": labels,
        "primary_timeframe": user_tf,
        "one_liner": _verdict_line(sym, direction, conviction, entry, stop, tp1, rr),
    }

    decision = {
        "instrument": instrument,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "leverage": leverage,
        "position_size_pct": size_pct,
        "risk_note": risk_note,
        "risk_reward": round(rr, 2) if rr else None,
        "mtf_score": round(mtf_score, 3),
        "mtf_labels": labels,
        "primary_timeframe": user_tf,
        "verdict": verdict_payload["verdict"],
        "conviction": conviction,
    }

    summary = verdict_payload["one_liner"]
    result_text = format_sniper_response(
        jspace, tv_analysis, strategy_position, verdict_payload
    )

    plan = {
        "symbol": sym,
        "instrument": instrument,
        "timeframe": user_tf,
        "stance": direction.lower().replace(" / ", "_").replace(" ", "_"),
        "setup": f"Sniper Trades · {direction} · conviction {conviction}% · MTF {mtf_score:+.2f}",
        "checklist": [
            "Active TV tools listed with readings",
            "Multi-TF live cross-check complete",
            "Confirmation bias disclosed + countered",
            "Sniper confluence or explicit Hold",
            "Invalidation (stop) pre-defined",
            "RR ≥ 1.5 for live-size proposals",
            "Paper/dry-run before live",
        ],
        "invalidation": (
            f"Stop {_fmt(stop)}" if stop and direction in ("Long", "Short") else "No open sniper risk"
        ),
        "risk": risk_note,
        "jspace_focus": [n.get("label") for n in (nodes or [])[:5]],
        "next_actions": [next_action, "Re-run on 1m close", "Announce tool flips instantly"],
        "trade_decision": decision,
        "strategy_position": strategy_position,
        "verdict": verdict_payload,
        "tools_consulted": tools_lines[:40],
    }

    # Prefer primary TF candles for chart overlay
    primary_candles: List[Dict[str, Any]] = []
    if user_tf in tfs:
        primary_candles = (tfs.get(user_tf) or {}).get("candles") or []
    if not primary_candles:
        for pack in tfs.values():
            if pack.get("candles"):
                primary_candles = pack["candles"]
                break
    overlay = build_chart_overlay(primary_candles, primary, decision)

    return {
        "jspace": jspace,
        "tv_analysis": tv_analysis,
        "strategy_position": strategy_position,
        "verdict": verdict_payload,
        "trade_decision": decision,
        "summary": summary,
        "result_text": result_text,
        "plan": plan,
        "chart_overlay": overlay,
        "analyses": {
            tf: {
                "bias_label": a["bias_label"],
                "bias_score": a["bias_score"],
                "rsi": a["rsi"],
                "atr": a["atr"],
                "last": a["last"],
                "sma20": a.get("sma20"),
                "sma50": a.get("sma50"),
                "support": a.get("support"),
                "resistance": a.get("resistance"),
                "pivots": a.get("pivots"),
                "fib": a.get("fib"),
                "volume_forecast": a.get("volume_forecast"),
                "tools": a.get("tools"),
                "bars": a.get("bars"),
            }
            for tf, a in analyses.items()
        },
    }


def _conviction(
    mtf_score: float,
    rr: Optional[float],
    aligned_n: int,
    bull_tfs: int,
    bear_tfs: int,
    direction: str,
) -> int:
    if direction == "Hold":
        base = 35 + min(25, int(abs(mtf_score) * 20))
        return max(15, min(55, base))
    if direction == "Exit":
        return 80
    score = 40 + min(35, int(abs(mtf_score) * 40))
    score += min(15, aligned_n * 5)
    if rr and rr >= 2.0:
        score += 10
    elif rr and rr >= 1.5:
        score += 5
    if bull_tfs and bear_tfs:
        score -= 15
    return max(10, min(95, score))


def _conviction_label(c: int) -> str:
    if c >= 80:
        return "very high"
    if c >= 65:
        return "high"
    if c >= 50:
        return "moderate"
    if c >= 35:
        return "low"
    return "very low"


def _next_action(
    direction: str,
    instrument: str,
    user_tf: str,
    stop: Any,
    entry: Any,
    rr: Optional[float],
) -> str:
    if direction == "Hold":
        return f"Stay flat on {instrument}; wait for 1m/5m/HTF confluence and RR≥1.5"
    if direction == "Exit":
        return f"Flatten {instrument} immediately; re-map structure on {user_tf}"
    if direction == "Long":
        return (
            f"Sniper long plan {instrument}: arm entry≈{_fmt(entry)}, SL {_fmt(stop)}; "
            f"paper first" + (f" (RR≈{rr:.2f})" if rr else "")
        )
    if direction == "Short":
        return (
            f"Sniper short plan {instrument}: arm entry≈{_fmt(entry)}, SL {_fmt(stop)}; "
            f"paper first" + (f" (RR≈{rr:.2f})" if rr else "")
        )
    return "Reassess on next candle close"


def _verdict_line(
    sym: str,
    direction: str,
    conviction: int,
    entry: Any,
    stop: Any,
    tp1: Any,
    rr: Optional[float],
) -> str:
    if direction == "Hold":
        return (
            f"Hold {sym} — conviction {conviction}%: no high-probability sniper setup; "
            f"preserve cash and watch tool flips."
        )
    if direction == "Exit":
        return f"Exit {sym} — conviction {conviction}%: invalidation or target hit; flatten risk now."
    return (
        f"{direction} {sym} near {_fmt(entry)} | SL {_fmt(stop)} | TP1 {_fmt(tp1)}"
        + (f" | RR≈{rr:.2f}" if rr else "")
        + f" | conviction {conviction}% — paper first, dry-run before live."
    )


def _build_jspace(
    *,
    instrument: str,
    user_tf: str,
    stance_hint: str,
    direction: str,
    mtf_score: float,
    labels: List[str],
    bias_notes: List[str],
    conflicts: List[str],
    confluences: List[str],
    last: Any,
    ticker: Dict[str, Any],
    data_sources: List[str],
    nodes: List[Dict[str, Any]],
    conviction: int,
) -> str:
    src = ticker.get("source") or (data_sources[0] if data_sources else "free")
    lines = [
        f"Live tape: Sniper Trades locking on {instrument} @ {_fmt(last)} via {src} "
        f"(focus TF {user_tf}, stance hint `{stance_hint}`).",
        f"Real-time read: composite MTF score {mtf_score:+.3f} → provisional **{direction}** "
        f"(conviction scaffold {conviction}%).",
        f"MTF labels: {', '.join(labels) if labels else 'none'}.",
        "",
        "Why sniper opportunity (or not):",
        *[f"  • {c}" for c in confluences],
        "",
        "Confirmation bias — detect & counter:",
        *[f"  - {b}" for b in bias_notes],
        "",
        "Conflicts / resolution:",
        *[f"  - {c}" for c in conflicts],
        "  Resolution: only arm risk when RR≥1.5, multi-TF not split, and stop is structure-based. "
        "Patient. Ruthless. Edge over FOMO.",
    ]
    if nodes:
        lines.append("")
        lines.append(
            "J-Space active concepts: "
            + ", ".join(str(n.get("label", n.get("id"))) for n in nodes[:6])
        )
    return "\n".join(lines)


def _build_tv_analysis(
    *,
    tools_lines: List[str],
    confluences: List[str],
    tool_flips: List[str],
    labels: List[str],
    primary: Optional[Dict[str, Any]],
    last: Any,
) -> str:
    lines = [
        "Every tool reading (local OHLCV equivalents of TradingView stack):",
    ]
    for line in tools_lines[:56]:
        lines.append(line if line.startswith("[") or line.startswith("  ") else f"  {line}")
    if len(tools_lines) > 56:
        lines.append(f"  … +{len(tools_lines) - 56} more readings truncated")
    lines.extend(
        [
            "",
            f"Key levels ({(primary or {}).get('timeframe', 'primary')}): "
            f"price={_fmt(last)}  support≈{_fmt((primary or {}).get('support'))}  "
            f"resistance≈{_fmt((primary or {}).get('resistance'))}  ATR={_fmt((primary or {}).get('atr'))}",
            f"MTF state: {', '.join(labels) if labels else 'n/a'}",
            "",
            "Sniper confluences:",
            *[f"  ✓ {c}" for c in confluences],
            "",
            "Tool flips announced:",
            *(
                [f"  ⚡ {f}" for f in tool_flips]
                if tool_flips
                else ["  • none this tick"]
            ),
        ]
    )
    return "\n".join(lines)


def format_sniper_response(
    jspace: str,
    tv_analysis: str,
    strategy_position: Dict[str, Any],
    verdict: Dict[str, Any],
) -> str:
    sp = strategy_position
    body = f"""1. **Cognitive Core Status — Raven Eye Open**:
{jspace}

2. **Market Horizon Scan**:
- Instrument: `{sp.get('instrument')}`
- Status: {sp.get('status')} · Side: **{sp.get('side')}**

3. **Shadow Network Intel**:
- News/on-chain context is attached when those feeds are available; missing feeds never count as confirmation.

4. **Tool Talons Deployed — Active TradingView Analysis**:
{tv_analysis}

5. **Decision Forge — Current Strategy Position**:
- Entry: {_fmt(sp.get('entry'))}
- Stop-loss: {_fmt(sp.get('stop_loss'))}
- Take-profit: TP1 {_fmt(sp.get('take_profit_1'))} · TP2 {_fmt(sp.get('take_profit_2'))}
- Leverage: {sp.get('leverage')}
- Position size: {sp.get('position_size_pct')}% portfolio risk ({sp.get('risk_note')})
- Risk/Reward: {_fmt(sp.get('risk_reward'), 2) if sp.get('risk_reward') is not None else 'n/a'}
- Next precision action: {sp.get('next_action')}

6. **Wallet Nest Status**: Disconnected unless an explicitly connected public wallet snapshot is supplied. No balance inferred.

7. **Next Flight Path — Live Sniper Verdict**: **{verdict.get('verdict')}** · conviction {verdict.get('conviction')}% ({verdict.get('conviction_label')})
{verdict.get('one_liner')}
"""
    return body.strip()


# Back-compat name
format_raven_response = format_sniper_response  # type: ignore[assignment,misc]
