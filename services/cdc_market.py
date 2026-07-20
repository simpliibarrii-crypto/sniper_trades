"""Crypto.com Exchange market data — cdcx first, public REST fallback."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Any, Dict, List, Optional

import httpx

PUBLIC_REST = "https://api.crypto.com/exchange/v1/public"
_CDCX = shutil.which("cdcx") or os.path.expanduser("~/.local/bin/cdcx")


def _nest(data: Any) -> Any:
    while isinstance(data, dict) and "data" in data:
        data = data["data"]
    return data


def _run_cdcx(args: List[str], timeout: float = 20.0) -> Any:
    if not _CDCX or not os.path.isfile(_CDCX):
        raise RuntimeError("cdcx not found")
    env = os.environ.copy()
    # load credentials if present
    cred = os.path.expanduser("~/.config/crypto-com/credentials.env")
    if os.path.isfile(cred):
        with open(cred) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :]
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    proc = subprocess.run(
        [_CDCX, *args, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "cdcx failed")
    return json.loads(proc.stdout)


def _http_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{PUBLIC_REST}/{path.lstrip('/')}"
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, params=params or {})
        r.raise_for_status()
        body = r.json()
    if isinstance(body, dict) and body.get("code") not in (None, 0, "0"):
        raise RuntimeError(body.get("message") or str(body))
    return body


def normalize_instrument(symbol: str) -> str:
    s = symbol.strip().upper().replace("-", "_").replace("/", "_")
    if s.endswith("USDT") and "_" not in s:
        s = s[:-4] + "_USDT"
    if s.endswith("USD") and "_" not in s and not s.endswith("USDT"):
        s = s[:-3] + "_USD"
    if "_" not in s:
        s = f"{s}_USDT"
    return s


def get_ticker(symbol: str) -> Dict[str, Any]:
    inst = normalize_instrument(symbol)
    try:
        raw = _run_cdcx(["market", "ticker", inst])
        row = _nest(raw)
        if isinstance(row, list):
            row = row[0] if row else {}
        return {
            "source": "cdcx",
            "instrument": row.get("i") or inst,
            "last": _f(row.get("k") or row.get("a") or row.get("last")),
            "bid": _f(row.get("b") or row.get("best_bid")),
            "ask": _f(row.get("a") or row.get("best_ask")),
            "high": _f(row.get("h") or row.get("high")),
            "low": _f(row.get("l") or row.get("low")),
            "change": _f(row.get("c") or row.get("change")),
            "volume": _f(row.get("v") or row.get("volume")),
            "volume_value": _f(row.get("vv") or row.get("volume_value")),
            "ts": row.get("t") or row.get("timestamp"),
        }
    except Exception:
        body = _http_get("get-tickers", {"instrument_name": inst})
        data = _nest(body)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if isinstance(data, list):
            data = data[0] if data else {}
        return {
            "source": "public_rest",
            "instrument": data.get("i") or data.get("instrument_name") or inst,
            "last": _f(data.get("k") or data.get("a") or data.get("last")),
            "bid": _f(data.get("b")),
            "ask": _f(data.get("a")),
            "high": _f(data.get("h")),
            "low": _f(data.get("l")),
            "change": _f(data.get("c")),
            "volume": _f(data.get("v")),
            "volume_value": _f(data.get("vv")),
            "ts": data.get("t"),
        }


def get_candles(
    symbol: str,
    timeframe: str = "1h",
    count: int = 100,
) -> Dict[str, Any]:
    inst = normalize_instrument(symbol)
    tf = timeframe.strip()
    count = max(5, min(int(count), 300))
    candles: List[Dict[str, Any]] = []
    source = "cdcx"
    try:
        raw = _run_cdcx(
            ["market", "candlestick", inst, "--timeframe", tf, "--count", str(count)]
        )
        rows = _nest(raw)
        if isinstance(rows, list):
            for r in rows:
                candles.append(_candle_row(r))
    except Exception:
        source = "public_rest"
        # Crypto.com public: instrument_name + timeframe
        body = _http_get(
            "get-candlestick",
            {"instrument_name": inst, "timeframe": tf},
        )
        rows = _nest(body)
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("candles") or []
        if isinstance(rows, list):
            for r in rows[-count:]:
                candles.append(_candle_row(r))

    candles = [c for c in candles if c.get("t") is not None]
    candles.sort(key=lambda x: x["t"])
    return {
        "source": source,
        "instrument": inst,
        "timeframe": tf,
        "count": len(candles),
        "candles": candles,
    }


def get_book(symbol: str, depth: int = 10) -> Dict[str, Any]:
    inst = normalize_instrument(symbol)
    depth = max(1, min(int(depth), 50))
    try:
        raw = _run_cdcx(["market", "book", inst, "--depth", str(depth)])
        book = _nest(raw)
        if isinstance(book, list):
            book = book[0] if book else {}
        return {
            "source": "cdcx",
            "instrument": inst,
            "bids": _levels(book.get("bids") or []),
            "asks": _levels(book.get("asks") or []),
        }
    except Exception:
        body = _http_get(
            "get-book",
            {"instrument_name": inst, "depth": depth},
        )
        book = _nest(body)
        if isinstance(book, list):
            book = book[0] if book else {}
        if isinstance(book, dict) and "data" in book:
            book = book["data"][0] if isinstance(book["data"], list) else book["data"]
        return {
            "source": "public_rest",
            "instrument": inst,
            "bids": _levels(book.get("bids") or []),
            "asks": _levels(book.get("asks") or []),
        }


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _candle_row(r: Any) -> Dict[str, Any]:
    if isinstance(r, dict):
        return {
            "t": r.get("t") or r.get("timestamp"),
            "o": _f(r.get("o") or r.get("open")),
            "h": _f(r.get("h") or r.get("high")),
            "l": _f(r.get("l") or r.get("low")),
            "c": _f(r.get("c") or r.get("close")),
            "v": _f(r.get("v") or r.get("volume")),
        }
    if isinstance(r, (list, tuple)) and len(r) >= 5:
        return {
            "t": r[0],
            "o": _f(r[1]),
            "h": _f(r[2]),
            "l": _f(r[3]),
            "c": _f(r[4]),
            "v": _f(r[5]) if len(r) > 5 else None,
        }
    return {"t": None}


def _levels(rows: list) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    for r in rows:
        if isinstance(r, dict):
            px = _f(r.get("price") or r.get("p") or r.get(0))
            qty = _f(r.get("qty") or r.get("q") or r.get("quantity") or r.get(1))
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            px, qty = _f(r[0]), _f(r[1])
        else:
            continue
        if px is not None and qty is not None:
            out.append({"price": px, "qty": qty})
    return out


@lru_cache(maxsize=1)
def cdcx_available() -> bool:
    return bool(_CDCX and os.path.isfile(_CDCX))
