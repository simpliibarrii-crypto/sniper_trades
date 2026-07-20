"""
Embedded SQLite market ledger + hash-chained movement blocks.

Speeds charts by serving OHLCV from local disk (stale-while-revalidate);
documents each refresh as a linked block (blockchain-style provenance).
No external Postgres required — ships inside the app and handles local traffic.
Optional: set SNIPER_DATABASE_URL later for hosted Postgres; SQLite is default.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_DB = Path.home() / ".local" / "share" / "sniper_trades" / "market_ledger.db"
_CONN: Optional[sqlite3.Connection] = None
_INITED = False

# Soft TTL: serve as "fresh" without network. Longer than bar period for speed.
_SOFT: Dict[str, float] = {
    "1m": 90.0,
    "3m": 120.0,
    "5m": 180.0,
    "15m": 300.0,
    "30m": 450.0,
    "1h": 600.0,
    "2h": 900.0,
    "4h": 1200.0,
    "6h": 1800.0,
    "12h": 2400.0,
    "1d": 3600.0,
    "1D": 3600.0,
    "1w": 7200.0,
    "1W": 7200.0,
}

# Hard TTL: after this, still serve as stale for instant paint; network refresh preferred.
_HARD: Dict[str, float] = {
    "1m": 3600.0,
    "3m": 7200.0,
    "5m": 10_800.0,
    "15m": 21_600.0,
    "30m": 43_200.0,
    "1h": 86_400.0,
    "2h": 86_400.0,
    "4h": 172_800.0,
    "6h": 172_800.0,
    "12h": 259_200.0,
    "1d": 604_800.0,
    "1D": 604_800.0,
    "1w": 1_209_600.0,
    "1W": 1_209_600.0,
}


def db_path() -> str:
    return str(_DB)


def _soft_sec(timeframe: str) -> float:
    return float(_SOFT.get(timeframe, _SOFT.get(timeframe.lower(), 180.0)))


def _hard_sec(timeframe: str) -> float:
    return float(_HARD.get(timeframe, _HARD.get(timeframe.lower(), 86_400.0)))


def _conn() -> sqlite3.Connection:
    """Reuse a single WAL connection (thread-safe via RLock)."""
    global _CONN
    if _CONN is not None:
        return _CONN
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA cache_size=-8000")  # ~8MB page cache
    c.execute("PRAGMA busy_timeout=5000")
    _CONN = c
    return c


def init_db() -> None:
    global _INITED
    with _LOCK:
        if _INITED:
            return
        c = _conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
              instrument TEXT NOT NULL,
              timeframe TEXT NOT NULL,
              t INTEGER NOT NULL,
              o REAL, h REAL, l REAL, c REAL, v REAL,
              source TEXT,
              updated_at REAL NOT NULL,
              PRIMARY KEY (instrument, timeframe, t)
            );
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
              ON candles(instrument, timeframe, t DESC);

            CREATE TABLE IF NOT EXISTS tickers (
              instrument TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chain_blocks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              prev_hash TEXT NOT NULL,
              block_hash TEXT NOT NULL,
              instrument TEXT NOT NULL,
              timeframe TEXT NOT NULL,
              bar_count INTEGER NOT NULL,
              first_t INTEGER,
              last_t INTEGER,
              merkle_root TEXT NOT NULL,
              source TEXT,
              created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chain_inst
              ON chain_blocks(instrument, created_at DESC);
            """
        )
        c.commit()
        _INITED = True


def get_cached_candles(
    instrument: str,
    timeframe: str,
    count: int = 120,
    *,
    allow_stale: bool = False,
    max_age: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return ledger candles if present.

    allow_stale=False → only soft-TTL fresh hits.
    allow_stale=True  → any rows within hard TTL (or max_age).
    """
    init_db()
    count = max(5, min(int(count), 1000))
    soft = _soft_sec(timeframe)
    hard = max_age if max_age is not None else _hard_sec(timeframe)
    with _LOCK:
        c = _conn()
        rows = c.execute(
            """
            SELECT t,o,h,l,c,v,source,updated_at FROM candles
            WHERE instrument=? AND timeframe=?
            ORDER BY t DESC LIMIT ?
            """,
            (instrument, timeframe, max(count, 5)),
        ).fetchall()
        if not rows or len(rows) < min(8, max(3, count // 4)):
            return None
        newest_upd = max(float(r["updated_at"] or 0) for r in rows)
        age = time.time() - newest_upd
        fresh = age <= soft
        if not fresh and not allow_stale:
            return None
        if age > hard and not allow_stale:
            return None
        # allow_stale can still refuse ancient junk beyond 7× hard
        if age > hard * 7:
            return None
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
        ][-count:]
        src = rows[0]["source"] or "sqlite_ledger"
        return {
            "source": f"{src}+ledger" if fresh else "sqlite_stale",
            "instrument": instrument,
            "timeframe": timeframe,
            "count": len(candles),
            "candles": candles,
            "cached": True,
            "stale": not fresh,
            "cache_age_s": round(age, 2),
            "engine": "sqlite_ledger",
        }


def upsert_candles(
    instrument: str,
    timeframe: str,
    candles: List[Dict[str, Any]],
    source: str = "network",
) -> Dict[str, Any]:
    """Write candles and append a hash-linked movement block."""
    if not candles:
        return {"written": 0, "block": None}
    init_db()
    now = time.time()
    with _LOCK:
        c = _conn()
        for row in candles:
            t = row.get("t")
            if t is None:
                continue
            t = int(t)
            c.execute(
                """
                INSERT INTO candles(instrument,timeframe,t,o,h,l,c,v,source,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(instrument,timeframe,t) DO UPDATE SET
                  o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c,
                  v=excluded.v, source=excluded.source, updated_at=excluded.updated_at
                """,
                (
                    instrument,
                    timeframe,
                    t,
                    row.get("o"),
                    row.get("h"),
                    row.get("l"),
                    row.get("c"),
                    row.get("v"),
                    source,
                    now,
                ),
            )
        prev = c.execute(
            "SELECT block_hash FROM chain_blocks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev["block_hash"] if prev else ("0" * 64)
        material = json.dumps(
            [
                {
                    "t": r.get("t"),
                    "o": r.get("o"),
                    "h": r.get("h"),
                    "l": r.get("l"),
                    "c": r.get("c"),
                    "v": r.get("v"),
                }
                for r in candles
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        merkle = hashlib.sha256(material.encode()).hexdigest()
        first_t = int(candles[0]["t"]) if candles[0].get("t") is not None else None
        last_t = int(candles[-1]["t"]) if candles[-1].get("t") is not None else None
        payload = f"{prev_hash}|{instrument}|{timeframe}|{merkle}|{first_t}|{last_t}|{len(candles)}|{now}"
        block_hash = hashlib.sha256(payload.encode()).hexdigest()
        c.execute(
            """
            INSERT INTO chain_blocks(
              prev_hash, block_hash, instrument, timeframe, bar_count,
              first_t, last_t, merkle_root, source, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                prev_hash,
                block_hash,
                instrument,
                timeframe,
                len(candles),
                first_t,
                last_t,
                merkle,
                source,
                now,
            ),
        )
        c.commit()
        return {
            "written": len(candles),
            "block": {
                "block_hash": block_hash,
                "prev_hash": prev_hash,
                "merkle_root": merkle,
                "instrument": instrument,
                "timeframe": timeframe,
                "bar_count": len(candles),
                "created_at": now,
            },
        }


def cache_ticker(instrument: str, payload: Dict[str, Any]) -> None:
    init_db()
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO tickers(instrument, payload, updated_at) VALUES(?,?,?)
            ON CONFLICT(instrument) DO UPDATE SET
              payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (instrument, json.dumps(payload), time.time()),
        )
        c.commit()


def get_cached_ticker(instrument: str, max_age: float = 8.0) -> Optional[Dict[str, Any]]:
    init_db()
    with _LOCK:
        c = _conn()
        try:
            row = c.execute(
                "SELECT payload, updated_at FROM tickers WHERE instrument=?",
                (instrument,),
            ).fetchone()
            if not row:
                return None
            age = time.time() - float(row["updated_at"])
            if age > max_age:
                return None
            data = json.loads(row["payload"])
            data["cached"] = True
            data["cache_age_s"] = round(age, 2)
            return data
        except Exception:
            return None


def chain_tip(limit: int = 20, instrument: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    with _LOCK:
        c = _conn()
        if instrument:
            rows = c.execute(
                """
                SELECT id, prev_hash, block_hash, instrument, timeframe, bar_count,
                       first_t, last_t, merkle_root, source, created_at
                FROM chain_blocks WHERE instrument=?
                ORDER BY id DESC LIMIT ?
                """,
                (instrument, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """
                SELECT id, prev_hash, block_hash, instrument, timeframe, bar_count,
                       first_t, last_t, merkle_root, source, created_at
                FROM chain_blocks ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        blocks = [dict(r) for r in rows]
        ok = True
        for i in range(len(blocks) - 1):
            newer, older = blocks[i], blocks[i + 1]
            if newer.get("prev_hash") != older.get("block_hash"):
                if newer.get("id") == older.get("id") + 1:
                    ok = False
        tip = blocks[0] if blocks else None
        counts = c.execute("SELECT COUNT(*) AS n FROM candles").fetchone()
        return {
            "ok": True,
            "integrity_ok": ok,
            "db_path": str(_DB),
            "candle_rows": int(counts["n"] if counts else 0),
            "blocks": blocks,
            "tip": tip,
            "engine": "sqlite_hash_chain",
            "note": (
                "Local blockchain-style ledger: each candle refresh is a hash-linked "
                "block (prev_hash → block_hash + merkle of OHLC). Embedded SQLite "
                "handles app traffic — no free hosted Postgres required."
            ),
        }


def stats() -> Dict[str, Any]:
    init_db()
    with _LOCK:
        c = _conn()
        candles = c.execute("SELECT COUNT(*) AS n FROM candles").fetchone()["n"]
        blocks = c.execute("SELECT COUNT(*) AS n FROM chain_blocks").fetchone()["n"]
        instruments = c.execute(
            "SELECT COUNT(DISTINCT instrument) AS n FROM candles"
        ).fetchone()["n"]
        tip = c.execute(
            "SELECT block_hash, instrument, timeframe, created_at FROM chain_blocks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "candles": candles,
            "blocks": blocks,
            "instruments": instruments,
            "db_path": str(_DB),
            "engine": "sqlite_wal",
            "tip_hash": (tip["block_hash"][:16] + "…") if tip else None,
            "tip_instrument": tip["instrument"] if tip else None,
            "postgres_url_set": bool(os.environ.get("SNIPER_DATABASE_URL") or os.environ.get("DATABASE_URL")),
            "note": "Embedded SQLite ledger (Postgres URL optional; not required).",
        }


def warmup_symbols(
    pairs: Optional[List[tuple]] = None,
) -> Dict[str, Any]:
    """
    Prefetch common symbols/TFs into the ledger so first chart paint is local.
    pairs: list of (symbol, timeframe) — network fetch via free_market.
    """
    from services import free_market  # local import avoids cycle at module load

    targets = pairs or [
        ("BTC_USDT", "1m"),
        ("BTC_USDT", "5m"),
        ("BTC_USDT", "15m"),
        ("BTC_USDT", "1h"),
        ("ETH_USDT", "1m"),
        ("ETH_USDT", "15m"),
        ("SOL_USDT", "1m"),
    ]
    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    for sym, tf in targets:
        try:
            pack = free_market.get_candles(sym, tf, 120, use_cache=True)
            results.append(
                {
                    "instrument": pack.get("instrument") or sym,
                    "timeframe": tf,
                    "count": pack.get("count"),
                    "source": pack.get("source"),
                    "cached": pack.get("cached"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"instrument": sym, "timeframe": tf, "error": str(exc)})
        try:
            free_market.get_ticker(sym)
        except Exception:
            pass
    return {
        "ok": True,
        "warmed": len([r for r in results if not r.get("error")]),
        "ms": round((time.perf_counter() - t0) * 1000, 1),
        "items": results,
        "stats": stats(),
    }


# init on import
try:
    init_db()
except Exception:
    pass
