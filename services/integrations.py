"""Central registry and health snapshot for Sniper Trades integrations.

The registry separates server connections, browser-wallet connections, local tools,
and external review handoffs. It never exposes credentials and never reports a
trading destination as connected merely because its public market API works.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

from services import cdc_market, dex_intel, finance_news, free_market, grok_live, solana_wallet


_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "market_stack",
        "name": "Live Market Stack",
        "category": "Market data",
        "purpose": "Prices, OHLCV candles, volume, spread, and order-book depth.",
        "mode": "automatic fallback",
        "priority": 1,
        "default_status": "ready",
        "capabilities": ["1m+ candles", "ticker", "order book", "multi-exchange fallback"],
        "providers": ["Binance", "Kraken", "Coinbase", "Crypto.com public"],
        "data_shared": "Public market requests only; no account data.",
        "setup": "No setup required.",
        "action": "focus_market",
        "action_label": "Open market deck",
    },
    {
        "id": "crypto_news",
        "name": "Crypto News Mesh",
        "category": "Intelligence",
        "purpose": "Crypto headlines plus macro news that can change crypto risk.",
        "mode": "read only",
        "priority": 2,
        "default_status": "ready",
        "capabilities": ["crypto RSS", "macro RSS", "deduplication", "live deck updates"],
        "providers": ["CoinDesk", "Cointelegraph", "Yahoo", "Federal Reserve", "BBC", "CNBC"],
        "data_shared": "Public feed requests only.",
        "setup": "No setup required.",
        "action": "focus_news",
        "action_label": "View news",
    },
    {
        "id": "dex_screener",
        "name": "DEX Screener Radar",
        "category": "Discovery",
        "purpose": "Find new Solana token profiles and inspect active liquidity pairs.",
        "mode": "research only",
        "priority": 3,
        "default_status": "ready",
        "capabilities": ["new profiles", "pair search", "liquidity", "volume", "risk flags"],
        "providers": ["DEX Screener"],
        "data_shared": "Public token and pair lookups only.",
        "setup": "No setup required. Every token remains unvetted.",
        "action": "focus_discovery",
        "action_label": "Open token radar",
    },
    {
        "id": "solana_rpc",
        "name": "Solana Read-Only RPC",
        "category": "Wallet data",
        "purpose": "Read SOL and classic SPL balances for an approved public address.",
        "mode": "read only",
        "priority": 4,
        "default_status": "ready",
        "capabilities": ["SOL balance", "SPL balances", "network health"],
        "providers": ["Solana mainnet RPC"],
        "data_shared": "Only the public wallet address the user approves.",
        "setup": "Connect Phantom or use the documented public-address endpoint.",
        "action": "connect_phantom",
        "action_label": "Connect Phantom",
    },
    {
        "id": "phantom",
        "name": "Phantom Wallet",
        "category": "Wallet data",
        "purpose": "Approve a public address and sign any DEX transaction in the wallet itself.",
        "mode": "browser controlled",
        "priority": 5,
        "default_status": "client_required",
        "capabilities": ["explicit connect", "self-custody", "human signature"],
        "providers": ["Phantom browser or mobile wallet"],
        "data_shared": "Public address after approval; no seed phrase or private key.",
        "setup": "Install Phantom or open the app inside Phantom mobile.",
        "action": "connect_phantom",
        "action_label": "Connect Phantom",
    },
    {
        "id": "jupiter",
        "name": "Jupiter Swap Review",
        "category": "Execution",
        "purpose": "Review a Solana swap route, price impact, and wallet simulation.",
        "mode": "external handoff",
        "priority": 6,
        "default_status": "review_only",
        "capabilities": ["route review", "quote review", "wallet simulation", "user signature"],
        "providers": ["Jupiter"],
        "data_shared": "The selected public token route after the user opens Jupiter.",
        "setup": "Use Trade Now, acknowledge the risks, then review and sign separately.",
        "action": "trade_now",
        "action_label": "Open Trade Now",
    },
    {
        "id": "tradingview",
        "name": "TradingView Chart Handoff",
        "category": "Analysis",
        "purpose": "Open the selected market in TradingView for deeper chart work.",
        "mode": "external handoff",
        "priority": 7,
        "default_status": "available",
        "capabilities": ["advanced chart handoff", "selected symbol"],
        "providers": ["TradingView"],
        "data_shared": "Selected public market symbol only.",
        "setup": "No setup required for public charts.",
        "action": "open_tradingview",
        "action_label": "Open chart",
    },
    {
        "id": "raven_tools",
        "name": "Raven Analysis Tools",
        "category": "Analysis",
        "purpose": "Run multi-timeframe indicators and the projected volume-pressure heuristic.",
        "mode": "local compute",
        "priority": 8,
        "default_status": "active",
        "capabilities": ["RSI", "EMA", "ATR", "MACD", "Bollinger", "VWAP", "volume pressure"],
        "providers": ["Local Raven engine"],
        "data_shared": "Nothing leaves the app beyond public source requests.",
        "setup": "Start the live deck.",
        "action": "focus_market",
        "action_label": "Start analysis",
    },
    {
        "id": "grok_xai",
        "name": "Grok Live Co-Pilot (xAI)",
        "category": "Analysis",
        "purpose": "Live model commentary on each deck tick via xAI OpenAI-compatible API.",
        "mode": "optional API",
        "priority": 8,
        "default_status": "client_required",
        "capabilities": ["live brief", "bias check", "news-aware comment", "local fallback"],
        "providers": ["xAI / SpaceXAI", "Local fallback brief"],
        "data_shared": "Public market context + headlines only; API key stays server-side.",
        "setup": "export XAI_API_KEY=... or SNIPER_XAI_API_KEY (console.x.ai).",
        "action": "focus_market",
        "action_label": "Open flight deck",
    },
    {
        "id": "risk_engine",
        "name": "Defense Risk Engine",
        "category": "Safety",
        "purpose": "Calculate deterministic loss-at-stop sizing with a hard 2% ceiling.",
        "mode": "local compute",
        "priority": 9,
        "default_status": "active",
        "capabilities": ["position size", "loss at stop", "risk/reward", "notional cap"],
        "providers": ["Local deterministic calculator"],
        "data_shared": "Nothing; values stay in the running app.",
        "setup": "Enter equity, entry, stop, and optional target.",
        "action": "focus_risk",
        "action_label": "Open calculator",
    },
    {
        "id": "paper_ledger",
        "name": "Paper Trading Ledger",
        "category": "Execution",
        "purpose": "Practice leader/follower signals without sending exchange orders.",
        "mode": "paper default",
        "priority": 10,
        "default_status": "active",
        "capabilities": ["paper fills", "leaders", "followers", "notional limits"],
        "providers": ["Local paper ledger"],
        "data_shared": "Local paper state only.",
        "setup": "Create a leader and paper follower.",
        "action": "open_paper",
        "action_label": "Open paper trading",
    },
    {
        "id": "crypto_com_execution",
        "name": "Crypto.com Exchange Execution",
        "category": "Execution",
        "purpose": "Optional exchange-order path through cdcx with dry-run and typed confirmation gates.",
        "mode": "locked by default",
        "priority": 11,
        "default_status": "locked",
        "capabilities": ["dry run", "typed live confirmation", "no withdrawals"],
        "providers": ["Crypto.com Exchange via cdcx"],
        "data_shared": "Order details only after explicit confirmation; credentials stay server-side.",
        "setup": "Install cdcx and configure exchange credentials outside the UI. Test dry-run first.",
        "action": "open_paper",
        "action_label": "Review execution gates",
    },
]


def _probe_market() -> Dict[str, Any]:
    ticker = free_market.get_ticker("BTC_USDT")
    source = str(ticker.get("source") or "public fallback")
    return {
        "status": "connected",
        "detail": f"Public fallback chain responding through {source}",
        "health": {"selected_source": source, "instrument": ticker.get("instrument")},
    }


def _probe_news() -> Dict[str, Any]:
    payload = finance_news.get_news(limit=3, force=False)
    count = int(payload.get("count") or 0)
    errors = payload.get("errors") or []
    status = "degraded" if count and errors else "connected" if count else "offline"
    return {
        "status": status,
        "detail": f"{count} recent headlines sampled" + (f"; {len(errors)} feed issue(s)" if errors else ""),
        "health": {"sample_count": count, "feed_errors": len(errors)},
    }


def _probe_dex() -> Dict[str, Any]:
    payload = dex_intel.probe()
    count = int(payload.get("profile_count") or 0)
    return {
        "status": "connected" if payload.get("ok") else "offline",
        "detail": f"DEX discovery responding with {count} current profiles",
        "health": payload,
    }


def _probe_solana() -> Dict[str, Any]:
    payload = solana_wallet.rpc_health()
    return {
        "status": "connected" if payload.get("ok") else "offline",
        "detail": payload.get("detail") or "Solana RPC health checked",
        "health": payload,
    }


_PROBES: Dict[str, Callable[[], Dict[str, Any]]] = {
    "market_stack": _probe_market,
    "crypto_news": _probe_news,
    "dex_screener": _probe_dex,
    "solana_rpc": _probe_solana,
}


def _safe_probe(probe: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        result = probe()
    except Exception as exc:  # noqa: BLE001
        result = {"status": "offline", "detail": str(exc)[:180], "health": {}}
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def integration_snapshot(probe: bool = False) -> Dict[str, Any]:
    """Return UI-safe integration metadata and optional live health results."""
    items = [{**item, "status": item["default_status"], "detail": item["setup"]} for item in _CATALOG]
    by_id = {item["id"]: item for item in items}

    if probe:
        with ThreadPoolExecutor(max_workers=len(_PROBES)) as pool:
            futures = {pool.submit(_safe_probe, fn): integration_id for integration_id, fn in _PROBES.items()}
            for future in as_completed(futures):
                by_id[futures[future]].update(future.result())

    cdcx = cdc_market.cdcx_available()
    by_id["crypto_com_execution"].update(
        {
            "status": "dry_run_ready" if cdcx else "locked",
            "detail": (
                "cdcx detected; dry-run is available, while live orders still require exact confirmation."
                if cdcx
                else "cdcx is not installed; live exchange execution remains unavailable."
            ),
            "health": {"cdcx_installed": cdcx, "withdrawals_supported": False},
        }
    )
    g = grok_live.grok_status()
    if "grok_xai" in by_id:
        by_id["grok_xai"].update(
            {
                "status": "connected" if g.get("configured") else "ready",
                "detail": (
                    f"Live Grok via {g.get('model')} (api.x.ai)"
                    if g.get("configured")
                    else g.get("hint") or "Local fallback active until XAI_API_KEY is set"
                ),
                "health": g,
            }
        )

    items.sort(key=lambda item: (item["priority"], item["name"]))
    ready_states = {"active", "available", "connected", "dry_run_ready", "ready", "review_only"}
    return {
        "scope": "actual Sniper Trades runtime integrations; development connectors are excluded",
        "probe_ran": probe,
        "items": items,
        "summary": {
            "total": len(items),
            "ready_or_available": sum(1 for item in items if item["status"] in ready_states),
            "attention": sum(1 for item in items if item["status"] in {"degraded", "offline"}),
            "wallet_connected": False,
            "live_execution_enabled": False,
        },
        "safety": {
            "private_keys_received": False,
            "automatic_dex_execution": False,
            "withdrawal_path": False,
            "live_order_requires_explicit_confirmation": True,
        },
        "ts": int(time.time() * 1000),
    }
