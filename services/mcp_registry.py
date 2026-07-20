"""Auditable MCP and local-bridge catalog.

This module intentionally distinguishes MCP servers from ordinary public APIs.
Nothing is installed, started, or granted wallet authority automatically.
"""

from __future__ import annotations

import shutil
import time
from typing import Any, Dict, List


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def catalog() -> Dict[str, Any]:
    """Return a credential-free catalog suitable for the UI and extension."""
    items: List[Dict[str, Any]] = [
        {
            "id": "kraken_cli_mcp",
            "name": "Kraken CLI native MCP",
            "kind": "native_mcp",
            "status": "available" if _command_available("kraken") else "not_installed",
            "default_enabled": False,
            "free_without_credentials": ["market data", "paper trading"],
            "capabilities": ["market", "account read-only", "paper trading"],
            "boundary": "Start outside the browser with market + paper services first.",
            "execution": "disabled by default",
            "official_docs": "https://docs.kraken.com/home/mcp",
        },
        {
            "id": "phantom_mcp",
            "name": "Phantom MCP",
            "kind": "wallet_mcp",
            "status": "manual_configuration_required",
            "default_enabled": False,
            "free_without_credentials": [],
            "capabilities": ["wallet portfolio", "swap intent", "sign and send"],
            "boundary": "Use a separate agent wallet; signing and swaps are never auto-enabled.",
            "execution": "human wallet approval required",
            "official_docs": "https://docs.phantom.com/",
        },
        {
            "id": "raven_chrome_bridge",
            "name": "Raven localhost Chrome bridge",
            "kind": "local_bridge",
            "status": "ready",
            "default_enabled": True,
            "free_without_credentials": ["market snapshot", "signal badge", "tool evidence"],
            "capabilities": ["one-shot analysis", "paper status", "server health"],
            "boundary": "Read-only localhost endpoints; no browser wallet or private-key permission.",
            "execution": "none",
            "official_docs": None,
        },
    ]

    direct_feeds = [
        {
            "id": "public_market_fallbacks",
            "name": "Binance · Kraken · Coinbase · Crypto.com public feeds",
            "kind": "direct_api",
            "note": "Free public APIs, not MCP servers.",
        },
        {
            "id": "dex_screener",
            "name": "DEX Screener discovery",
            "kind": "direct_api",
            "note": "Read-only public token and pair research; results remain unvetted.",
        },
        {
            "id": "global_news_mesh",
            "name": "Worldwide crypto and macro news mesh",
            "kind": "direct_api",
            "note": "Read-only RSS and public headline feeds, normalized to UTC.",
        },
    ]

    return {
        "items": items,
        "direct_feeds": direct_feeds,
        "safety": {
            "auto_install": False,
            "auto_start_wallet_mcp": False,
            "private_keys_received": False,
            "browser_live_order_permission": False,
        },
        "ts": int(time.time() * 1000),
    }
