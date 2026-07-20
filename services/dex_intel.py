"""Read-only DEX discovery for new Solana tokens and meme-coin research.

No transaction is created here. DEX Screener data is discovery metadata, not
an endorsement or a safety audit; the UI always labels new profiles as unvetted.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List

import httpx

_BASE = "https://api.dexscreener.com"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "RavenTrade-Core/6.2 (read-only market discovery)",
}
_CACHE: Dict[str, tuple[float, Any]] = {}
_LOCK = threading.RLock()


def _cached_json(path: str, ttl: int = 45, timeout: float = 12) -> Any:
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(path)
        if cached and now - cached[0] < ttl:
            return cached[1]
    with httpx.Client(timeout=timeout, headers=_HEADERS, follow_redirects=True) as client:
        response = client.get(f"{_BASE}{path}")
        response.raise_for_status()
        payload = response.json()
    with _LOCK:
        _CACHE[path] = (now, payload)
    return payload


def probe() -> Dict[str, Any]:
    """Fast, read-only availability check without pair enrichment."""
    payload = _cached_json("/token-profiles/latest/v1", ttl=20, timeout=5)
    count = len(payload) if isinstance(payload, list) else 0
    return {"ok": isinstance(payload, list), "profile_count": count}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def risk_flags(pair: Dict[str, Any], *, profiled: bool = False) -> List[str]:
    """Return deterministic warnings for a DEX pair; never labels a token safe."""
    flags = ["unvetted token"] if profiled else []
    liquidity = _number((pair.get("liquidity") or {}).get("usd"))
    volume = _number((pair.get("volume") or {}).get("h24"))
    move_5m = _number((pair.get("priceChange") or {}).get("m5"))
    created_ms = _number(pair.get("pairCreatedAt"))
    age_hours = (
        max(0.0, (time.time() * 1000 - created_ms) / 3_600_000)
        if created_ms
        else None
    )
    if liquidity is None:
        flags.append("liquidity unknown")
    elif liquidity < 25_000:
        flags.append("very thin liquidity")
    elif liquidity < 100_000:
        flags.append("thin liquidity")
    if age_hours is not None and age_hours < 24:
        flags.append("pool under 24h old")
    if move_5m is not None and abs(move_5m) >= 20:
        flags.append("extreme 5m move")
    if liquidity and volume and volume / liquidity > 15:
        flags.append("high churn")
    return list(dict.fromkeys(flags))


def _best_pairs(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for pair in rows:
        base = pair.get("baseToken") or {}
        address = str(base.get("address") or "")
        if not address:
            continue
        old = best.get(address)
        liq = _number((pair.get("liquidity") or {}).get("usd")) or 0.0
        old_liq = _number(((old or {}).get("liquidity") or {}).get("usd")) or 0.0
        if old is None or liq > old_liq:
            best[address] = pair
    return best


def _normalize_pair(pair: Dict[str, Any], profile: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    address = str(base.get("address") or (profile or {}).get("tokenAddress") or "")
    created_ms = _number(pair.get("pairCreatedAt"))
    age_hours = (
        round(max(0.0, (time.time() * 1000 - created_ms) / 3_600_000), 1)
        if created_ms
        else None
    )
    return {
        "chain_id": str(pair.get("chainId") or "solana"),
        "dex_id": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "token_address": address,
        "symbol": base.get("symbol") or "NEW",
        "name": base.get("name") or (profile or {}).get("description") or "Unlabelled token",
        "quote_symbol": quote.get("symbol") or "—",
        "price_usd": _number(pair.get("priceUsd")),
        "liquidity_usd": _number((pair.get("liquidity") or {}).get("usd")),
        "volume_24h": _number((pair.get("volume") or {}).get("h24")),
        "change_5m": _number((pair.get("priceChange") or {}).get("m5")),
        "change_1h": _number((pair.get("priceChange") or {}).get("h1")),
        "change_24h": _number((pair.get("priceChange") or {}).get("h24")),
        "age_hours": age_hours,
        "risk_flags": risk_flags(pair, profiled=profile is not None),
        "research_url": f"https://dexscreener.com/solana/{pair.get('pairAddress')}",
    }


def latest_solana_tokens(limit: int = 12) -> Dict[str, Any]:
    """Enrich the newest Solana token profiles with their deepest known pair."""
    limit = max(1, min(int(limit), 24))
    profiles_raw = _cached_json("/token-profiles/latest/v1")
    profiles = [
        row
        for row in (profiles_raw if isinstance(profiles_raw, list) else [])
        if str(row.get("chainId") or "").lower() == "solana" and row.get("tokenAddress")
    ][:30]
    addresses = list(dict.fromkeys(str(row["tokenAddress"]) for row in profiles))
    pairs: List[Dict[str, Any]] = []
    if addresses:
        # DEX Screener accepts up to 30 comma-separated addresses on this route.
        joined = ",".join(addresses[:30])
        rows = _cached_json(f"/tokens/v1/solana/{joined}")
        if isinstance(rows, list):
            pairs = rows
    best = _best_pairs(pairs)
    profile_by_address = {str(row["tokenAddress"]): row for row in profiles}
    items = [
        _normalize_pair(pair, profile_by_address.get(address))
        for address, pair in best.items()
    ]
    items.sort(
        key=lambda row: (
            row.get("age_hours") is None,
            row.get("age_hours") if row.get("age_hours") is not None else 1e9,
            -(row.get("liquidity_usd") or 0),
        )
    )
    return {
        "source": "dexscreener",
        "chain": "solana",
        "scope": "latest token profiles with an active pair; not the entire market",
        "items": items[:limit],
        "unvetted": True,
        "ts": int(time.time() * 1000),
    }


def search_pairs(query: str, limit: int = 12) -> Dict[str, Any]:
    query = query.strip()[:80]
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(limit), 24))
    encoded = httpx.QueryParams({"q": query})
    payload = _cached_json(f"/latest/dex/search?{encoded}", ttl=20)
    rows = payload.get("pairs") if isinstance(payload, dict) else []
    items = [_normalize_pair(row) for row in (rows or [])[:limit]]
    return {"source": "dexscreener", "query": query, "items": items, "ts": int(time.time() * 1000)}
