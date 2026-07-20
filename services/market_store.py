"""
Embedded SQLite market ledger + hash-chained movement blocks.

Speeds charts by serving recent OHLCV from local disk; documents each
refresh as a linked block (blockchain-style provenance) for accuracy audits.
No external Postgres required — ships inside the app.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.RLock()
_DB = Path.home() / ".local" / "share" / "sniper_trades" / "market_ledger.db"

# Freshness windows (seconds) before re-fetching from network
_FRESH: Dict[str, float] = {
    "1m": 25.0,
    "3m": 45.0,
    "5m": 60.0,
    "15m": 120.0,
    "30m": 180.0,
    "1h": 300.0,
    "2h": 400.0,
    "4h": 600.0,
    "6h": 800.0,
    "12h": 1200.0,
    "1d": 1800.0,
    "1D": 1800.0,
    "1w": 3600.0,
    "1W": 3600.0,
}


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db() -> None:
    with _LOCK:
        c = _conn()
        try:
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
        finally:
            c.close()


def _fresh_sec(timeframe: str) -> float:
    return float(_FRESH.get(timeframe, _FRESH.get(timeframe.lower(), 90.0)))


def get_cached_candles(
    instrument: str,
    timeframe: str,
    count: int = 120,
) -> Optional[Dict[str, Any]]:
    """Return cache hit if we have enough bars and the tip is fresh."""
    init_db()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                """
                SELECT t,o,h,l,c,v,source,updated_at FROM candles
                WHERE instrument=? AND timeframe=?
                ORDER BY t DESC LIMIT ?
                """,
                (instrument, timeframe, max(count, 5)),
            ).fetchall()
            if not rows or len(rows) < min(10, count // 2):
                return None
            newest_upd = max(float(r["updated_at"] or 0) for r in rows)
            if time.time() - newest_upd > _fresh_sec(timeframe):
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
                "source": f"{src}+ledger",
                "instrument": instrument,
                "timeframe": timeframe,
                "count": len(candles),
                "candles": candles,
                "cached": True,
                "cache_age_s": round(time.time() - newest_upd, 2),
            }
        finally:
            c.close()


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
        try:
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
            # chain tip
            prev = c.execute(
                "SELECT block_hash FROM chain_blocks ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev["block_hash"] if prev else ("0" * 64)
            # merkle of this batch
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
        finally:
            c.close()


def cache_ticker(instrument: str, payload: Dict[str, Any]) -> None:
    init_db()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """
                INSERT INTO tickers(instrument, payload, updated_at) VALUES(?,?,?)
                ON CONFLICT(instrument) DO UPDATE SET
                  payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (instrument, json.dumps(payload), time.time()),
            )
            c.commit()
        finally:
            c.close()


def get_cached_ticker(instrument: str, max_age: float = 5.0) -> Optional[Dict[str, Any]]:
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
            if time.time() - float(row["updated_at"]) > max_age:
                return None
            data = json.loads(row["payload"])
            data["cached"] = True
            return data
        except Exception:
            return None
        finally:
            c.close()


def chain_tip(limit: int = 20, instrument: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    with _LOCK:
        c = _conn()
        try:
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
            # verify link integrity on returned set (newest→older)
            ok = True
            for i in range(len(blocks) - 1):
                # blocks[i] should have prev_hash == blocks[i+1].block_hash when sequential
                newer, older = blocks[i], blocks[i + 1]
                if newer.get("prev_hash") != older.get("block_hash"):
                    # not necessarily adjacent if filtered — only check when ids consecutive
                    if newer.get("id") == older.get("id") + 1:
                        ok = False
            tip = blocks[0] if blocks else None
            counts = c.execute(
                "SELECT COUNT(*) AS n FROM candles"
            ).fetchone()
            return {
                "ok": True,
                "integrity_ok": ok,
                "db_path": str(_DB),
                "candle_rows": int(counts["n"] if counts else 0),
                "blocks": blocks,
                "tip": tip,
                "engine": "sqlite_hash_chain",
                "note": "Local blockchain-style ledger: each candle refresh is a linked block.",
            }
        finally:
            c.close()


def stats() -> Dict[str, Any]:
    init_db()
    with _LOCK:
        c = _conn()
        try:
            candles = c.execute("SELECT COUNT(*) AS n FROM candles").fetchone()["n"]
            blocks = c.execute("SELECT COUNT(*) AS n FROM chain_blocks").fetchone()["n"]
            instruments = c.execute(
                "SELECT COUNT(DISTINCT instrument) AS n FROM candles"
            ).fetchone()["n"]
            return {
                "candles": candles,
                "blocks": blocks,
                "instruments": instruments,
                "db_path": str(_DB),
            }
        finally:
            c.close()


# init on import
try:
    init_db()
except Exception:
    pass
