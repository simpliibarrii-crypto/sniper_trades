"""
Free public crypto market data — no API keys required.

Sources (tried in order per request):
  1. Binance public REST   — excellent 1m–1M klines, no key
  2. Kraken public REST    — 1m OHLC
  3. Coinbase Exchange     — 1m candles (granularity 60)
  4. Crypto.com public     — fallback (via cdc_market)

All return the same normalized shape used by RavenTrader / UI.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

# ── symbol helpers ─────────────────────────────────────────────────────────


def normalize_instrument(symbol: str) -> str:
    s = symbol.strip().upper().replace("-", "_").replace("/", "_").replace(" ", "")
    if s.endswith("USDT") and "_" not in s:
        s = s[:-4] + "_USDT"
    elif s.endswith("USD") and "_" not in s and not s.endswith("USDT"):
        s = s[:-3] + "_USD"
    elif s.endswith("USDC") and "_" not in s:
        s = s[:-4] + "_USDC"
    if "_" not in s:
        s = f"{s}_USDT"
    return s


def _base_quote(inst: str) -> Tuple[str, str]:
    inst = normalize_instrument(inst)
    base, _, quote = inst.partition("_")
    return base, quote or "USDT"


def _binance_symbol(inst: str) -> str:
    b, q = _base_quote(inst)
    # Binance uses XBT? No — BTCUSDT. Kraken uses XBT.
    return f"{b}{q}"


def _kraken_pair(inst: str) -> str:
    b, q = _base_quote(inst)
    # Kraken legacy names
    if b == "BTC":
        b = "XBT"
    if q == "USDT":
        return f"{b}USDT"  # modern Kraken
    if q == "USD":
        return f"{b}USD"
    return f"{b}{q}"


def _coinbase_product(inst: str) -> str:
    b, q = _base_quote(inst)
    # Coinbase has BTC-USD more than BTC-USDT; map USDT→USD for free feed
    if q == "USDT":
        q = "USD"
    return f"{b}-{q}"


# ── timeframe maps ─────────────────────────────────────────────────────────

# Our app uses: 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W
_BINANCE_TF = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1d": "1d",
    "1D": "1d",
    "1w": "1w",
    "1W": "1w",
    "daily": "1d",
    "weekly": "1w",
}

# Kraken intervals in minutes
_KRAKEN_TF = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1D": 1440,
    "1w": 10080,
    "1W": 10080,
    "daily": 1440,
    "weekly": 10080,
}

# Coinbase granularity in seconds
_COINBASE_TF = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
    "1D": 86400,
    "daily": 86400,
}


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _http_get(url: str, params: Optional[Dict[str, Any]] = None, timeout: float = 12.0) -> Any:
    headers = {
        "User-Agent": "SniperTrades-RavenTrader/4.9 (local; free-market)",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        r = client.get(url, params=params or {})
        r.raise_for_status()
        return r.json()


# ── Binance ────────────────────────────────────────────────────────────────


def _binance_ticker(inst: str) -> Dict[str, Any]:
    sym = _binance_symbol(inst)
    row = _http_get(
        "https://api.binance.com/api/v3/ticker/24hr",
        {"symbol": sym},
    )
    last = _f(row.get("lastPrice"))
    return {
        "source": "binance",
        "instrument": normalize_instrument(inst),
        "last": last,
        "bid": _f(row.get("bidPrice")),
        "ask": _f(row.get("askPrice")),
        "high": _f(row.get("highPrice")),
        "low": _f(row.get("lowPrice")),
        "change": _f(row.get("priceChangePercent")) / 100.0
        if row.get("priceChangePercent") is not None
        else None,
        "volume": _f(row.get("volume")),
        "volume_value": _f(row.get("quoteVolume")),
        "ts": int(row.get("closeTime") or time.time() * 1000),
    }


def _binance_candles(inst: str, timeframe: str, count: int) -> Dict[str, Any]:
    interval = _BINANCE_TF.get(timeframe) or _BINANCE_TF.get(timeframe.lower())
    if not interval:
        raise ValueError(f"binance unsupported timeframe: {timeframe}")
    sym = _binance_symbol(inst)
    limit = max(5, min(int(count), 1000))
    rows = _http_get(
        "https://api.binance.com/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit},
    )
    candles: List[Dict[str, Any]] = []
    for r in rows:
        # [ openTime, o, h, l, c, volume, closeTime, ... ]
        candles.append(
            {
                "t": int(r[0]),
                "o": _f(r[1]),
                "h": _f(r[2]),
                "l": _f(r[3]),
                "c": _f(r[4]),
                "v": _f(r[5]),
            }
        )
    return {
        "source": "binance",
        "instrument": normalize_instrument(inst),
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles,
    }


def _binance_book(inst: str, depth: int) -> Dict[str, Any]:
    sym = _binance_symbol(inst)
    limit = min(max(depth, 5), 100)
    # valid limits: 5,10,20,50,100,500,1000,5000
    for cand in (5, 10, 20, 50, 100):
        if cand >= limit:
            limit = cand
            break
    row = _http_get(
        "https://api.binance.com/api/v3/depth",
        {"symbol": sym, "limit": limit},
    )
    bids = [{"price": _f(p), "qty": _f(q)} for p, q in (row.get("bids") or [])[:depth]]
    asks = [{"price": _f(p), "qty": _f(q)} for p, q in (row.get("asks") or [])[:depth]]
    bids = [x for x in bids if x["price"] is not None]
    asks = [x for x in asks if x["price"] is not None]
    return {
        "source": "binance",
        "instrument": normalize_instrument(inst),
        "bids": bids,
        "asks": asks,
    }


# ── Kraken ─────────────────────────────────────────────────────────────────


def _kraken_ticker(inst: str) -> Dict[str, Any]:
    pair = _kraken_pair(inst)
    body = _http_get("https://api.kraken.com/0/public/Ticker", {"pair": pair})
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    result = body.get("result") or {}
    if not result:
        raise RuntimeError("kraken empty ticker")
    # key is internal pair name
    row = next(iter(result.values()))
    # a=ask [price, whole lot, lot], b=bid, c=last trade [price, lot]
    last = _f((row.get("c") or [None])[0])
    bid = _f((row.get("b") or [None])[0])
    ask = _f((row.get("a") or [None])[0])
    high = _f((row.get("h") or [None, None])[1] or (row.get("h") or [None])[0])
    low = _f((row.get("l") or [None, None])[1] or (row.get("l") or [None])[0])
    vol = _f((row.get("v") or [None, None])[1] or (row.get("v") or [None])[0])
    return {
        "source": "kraken",
        "instrument": normalize_instrument(inst),
        "last": last,
        "bid": bid,
        "ask": ask,
        "high": high,
        "low": low,
        "change": None,
        "volume": vol,
        "volume_value": None,
        "ts": int(time.time() * 1000),
    }


def _kraken_candles(inst: str, timeframe: str, count: int) -> Dict[str, Any]:
    interval = _KRAKEN_TF.get(timeframe) or _KRAKEN_TF.get(timeframe.lower())
    if not interval:
        raise ValueError(f"kraken unsupported timeframe: {timeframe}")
    pair = _kraken_pair(inst)
    body = _http_get(
        "https://api.kraken.com/0/public/OHLC",
        {"pair": pair, "interval": interval},
    )
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    result = body.get("result") or {}
    rows = []
    for k, v in result.items():
        if k == "last":
            continue
        if isinstance(v, list):
            rows = v
            break
    # each: [time, open, high, low, close, vwap, volume, count]
    candles: List[Dict[str, Any]] = []
    for r in rows[-max(5, min(int(count), 720)) :]:
        candles.append(
            {
                "t": int(r[0]) * 1000,  # kraken seconds → ms
                "o": _f(r[1]),
                "h": _f(r[2]),
                "l": _f(r[3]),
                "c": _f(r[4]),
                "v": _f(r[6]),
            }
        )
    return {
        "source": "kraken",
        "instrument": normalize_instrument(inst),
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles,
    }


# ── Coinbase ───────────────────────────────────────────────────────────────


def _coinbase_ticker(inst: str) -> Dict[str, Any]:
    product = _coinbase_product(inst)
    row = _http_get(f"https://api.exchange.coinbase.com/products/{product}/ticker")
    last = _f(row.get("price") or row.get("last"))
    return {
        "source": "coinbase",
        "instrument": normalize_instrument(inst),
        "last": last,
        "bid": _f(row.get("bid")),
        "ask": _f(row.get("ask")),
        "high": None,
        "low": None,
        "change": None,
        "volume": _f(row.get("volume")),
        "volume_value": None,
        "ts": int(time.time() * 1000),
    }


def _coinbase_candles(inst: str, timeframe: str, count: int) -> Dict[str, Any]:
    gran = _COINBASE_TF.get(timeframe) or _COINBASE_TF.get(timeframe.lower())
    if not gran:
        raise ValueError(f"coinbase unsupported timeframe: {timeframe}")
    product = _coinbase_product(inst)
    # Coinbase returns max 300 candles; most-recent first sometimes
    rows = _http_get(
        f"https://api.exchange.coinbase.com/products/{product}/candles",
        {"granularity": gran},
    )
    candles: List[Dict[str, Any]] = []
    for r in rows:
        # [ time, low, high, open, close, volume ]
        candles.append(
            {
                "t": int(r[0]) * 1000,
                "o": _f(r[3]),
                "h": _f(r[2]),
                "l": _f(r[1]),
                "c": _f(r[4]),
                "v": _f(r[5]),
            }
        )
    candles.sort(key=lambda x: x["t"] or 0)
    candles = candles[-max(5, min(int(count), 300)) :]
    return {
        "source": "coinbase",
        "instrument": normalize_instrument(inst),
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles,
    }


# ── public facade with fallback ────────────────────────────────────────────

_TICKER_CHAIN = (_binance_ticker, _kraken_ticker, _coinbase_ticker)
_CANDLE_CHAIN = (_binance_candles, _kraken_candles, _coinbase_candles)


def get_ticker(symbol: str, use_cache: bool = True) -> Dict[str, Any]:
    inst = normalize_instrument(symbol)
    if use_cache:
        try:
            from services import market_store

            hit = market_store.get_cached_ticker(inst, max_age=4.0)
            if hit:
                return hit
        except Exception:
            pass
    errors: List[str] = []
    for fn in _TICKER_CHAIN:
        try:
            row = fn(inst)
            try:
                from services import market_store

                market_store.cache_ticker(inst, row)
            except Exception:
                pass
            return row
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {exc}")
    # last resort: crypto.com public
    try:
        from services import cdc_market

        row = cdc_market.get_ticker(inst)
        try:
            from services import market_store

            market_store.cache_ticker(inst, row)
        except Exception:
            pass
        return row
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cdc: {exc}")
    raise RuntimeError("all free ticker sources failed: " + " | ".join(errors[:4]))


def get_candles(
    symbol: str,
    timeframe: str = "1m",
    count: int = 100,
    use_cache: bool = True,
) -> Dict[str, Any]:
    inst = normalize_instrument(symbol)
    tf = timeframe.strip()
    count = max(5, min(int(count), 1000))
    if use_cache:
        try:
            from services import market_store

            hit = market_store.get_cached_candles(inst, tf, count)
            if hit and hit.get("candles"):
                return hit
        except Exception:
            pass
    errors: List[str] = []
    for fn in _CANDLE_CHAIN:
        try:
            pack = fn(inst, tf, count)
            if pack.get("candles"):
                try:
                    from services import market_store

                    market_store.upsert_candles(
                        inst, tf, pack["candles"], source=str(pack.get("source") or "network")
                    )
                except Exception:
                    pass
                return pack
            errors.append(f"{fn.__name__}: empty")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {exc}")
    try:
        from services import cdc_market

        pack = cdc_market.get_candles(inst, tf, count)
        try:
            from services import market_store

            market_store.upsert_candles(
                inst, tf, pack.get("candles") or [], source=str(pack.get("source") or "cdc")
            )
        except Exception:
            pass
        return pack
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cdc: {exc}")
    # stale cache fallback (any age) for resilience
    try:
        from services import market_store

        # force-read without freshness by raw SQL path: get_cached with relaxed
        market_store.init_db()
        import sqlite3

        with market_store._LOCK:
            c = market_store._conn()
            try:
                rows = c.execute(
                    """
                    SELECT t,o,h,l,c,v,source FROM candles
                    WHERE instrument=? AND timeframe=?
                    ORDER BY t DESC LIMIT ?
                    """,
                    (inst, tf, count),
                ).fetchall()
                if rows:
                    candles = [
                        {
                            "t": int(r["t"]),
                            "o": r["o"],
                            "h": r["h"],
                            "l": r["l"],
                            "c": r["c"],
                            "v": r["v"],
                        }
                        for r in reversed(rows)
                    ]
                    return {
                        "source": "sqlite_stale",
                        "instrument": inst,
                        "timeframe": tf,
                        "count": len(candles),
                        "candles": candles,
                        "cached": True,
                        "stale": True,
                    }
            finally:
                c.close()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"stale_cache: {exc}")
    raise RuntimeError("all free candle sources failed: " + " | ".join(errors[:5]))


def get_book(symbol: str, depth: int = 10) -> Dict[str, Any]:
    inst = normalize_instrument(symbol)
    errors: List[str] = []
    try:
        return _binance_book(inst, depth)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"binance: {exc}")
    try:
        from services import cdc_market

        return cdc_market.get_book(inst, depth)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cdc: {exc}")
    raise RuntimeError("book sources failed: " + " | ".join(errors))


def list_sources() -> Dict[str, Any]:
    return {
        "sources": [
            {
                "id": "binance",
                "url": "https://api.binance.com",
                "auth": "none",
                "min_tf": "1m",
                "notes": "Primary free feed — klines 1m–1M",
            },
            {
                "id": "kraken",
                "url": "https://api.kraken.com",
                "auth": "none",
                "min_tf": "1m",
                "notes": "OHLC interval=1 minute",
            },
            {
                "id": "coinbase",
                "url": "https://api.exchange.coinbase.com",
                "auth": "none",
                "min_tf": "1m",
                "notes": "granularity=60 seconds; USDT pairs mapped to USD",
            },
            {
                "id": "crypto.com",
                "url": "https://api.crypto.com/exchange/v1/public",
                "auth": "none (cdcx optional)",
                "min_tf": "1m",
                "notes": "Last-resort fallback",
            },
        ],
        "timeframes": list(_BINANCE_TF.keys()),
        "default_primary": "binance",
    }


def probe() -> Dict[str, Any]:
    """Quick connectivity check for each free source (1m BTC)."""
    out: Dict[str, Any] = {}
    for name, fn in (
        ("binance", lambda: _binance_candles("BTC_USDT", "1m", 3)),
        ("kraken", lambda: _kraken_candles("BTC_USDT", "1m", 3)),
        ("coinbase", lambda: _coinbase_candles("BTC_USD", "1m", 3)),
    ):
        t0 = time.perf_counter()
        try:
            pack = fn()
            out[name] = {
                "ok": True,
                "count": pack.get("count"),
                "last_close": (pack.get("candles") or [{}])[-1].get("c"),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        except Exception as exc:  # noqa: BLE001
            out[name] = {
                "ok": False,
                "error": str(exc)[:200],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
    return out
