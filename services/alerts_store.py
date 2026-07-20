"""Persistent multi-symbol price alerts (local JSON)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_STORE = Path.home() / ".local" / "share" / "sniper_trades" / "alerts.json"


def _load() -> List[Dict[str, Any]]:
    if not _STORE.is_file():
        return []
    try:
        raw = json.loads(_STORE.read_text())
        return list(raw.get("alerts") or [])
    except Exception:
        return []


def _save(alerts: List[Dict[str, Any]]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps({"alerts": alerts[-200:], "updated_at": time.time()}, indent=2))


def list_alerts() -> Dict[str, Any]:
    with _LOCK:
        alerts = _load()
    return {"count": len(alerts), "alerts": alerts}


def add_alert(
    instrument: str,
    direction: str,
    target: float,
    note: str = "",
) -> Dict[str, Any]:
    direction = direction.lower().strip()
    if direction not in ("above", "below"):
        raise ValueError("direction must be above or below")
    target = float(target)
    if target <= 0:
        raise ValueError("target must be > 0")
    inst = instrument.strip().upper().replace("-", "_").replace("/", "_")
    if "_" not in inst:
        inst = f"{inst}_USDT"
    row = {
        "id": "A_" + uuid.uuid4().hex[:10],
        "instrument": inst,
        "direction": direction,
        "target": target,
        "note": (note or "")[:120],
        "active": True,
        "triggered": False,
        "created_at": time.time(),
        "triggered_at": None,
        "last_price": None,
    }
    with _LOCK:
        alerts = _load()
        alerts.append(row)
        _save(alerts)
    return row


def remove_alert(alert_id: str) -> bool:
    with _LOCK:
        alerts = _load()
        n = len(alerts)
        alerts = [a for a in alerts if a.get("id") != alert_id]
        _save(alerts)
        return len(alerts) < n


def clear_triggered() -> int:
    with _LOCK:
        alerts = _load()
        keep = [a for a in alerts if not a.get("triggered")]
        removed = len(alerts) - len(keep)
        _save(keep)
        return removed


def evaluate_prices(prices: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    prices: instrument -> last price
    Returns newly triggered alerts (also persists state).
    """
    fired: List[Dict[str, Any]] = []
    with _LOCK:
        alerts = _load()
        changed = False
        for a in alerts:
            if not a.get("active") or a.get("triggered"):
                continue
            inst = a.get("instrument")
            px = prices.get(inst)
            if px is None:
                continue
            a["last_price"] = px
            changed = True
            hit = (a["direction"] == "above" and px >= float(a["target"])) or (
                a["direction"] == "below" and px <= float(a["target"])
            )
            if hit:
                a["triggered"] = True
                a["active"] = False
                a["triggered_at"] = time.time()
                fired.append(dict(a))
        if changed or fired:
            _save(alerts)
    return fired
