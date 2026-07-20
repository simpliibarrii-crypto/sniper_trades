"""Read-only Solana wallet snapshot for an explicitly supplied public address."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List

import httpx

_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
_BASE58_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def validate_address(address: str) -> str:
    value = address.strip()
    if not _BASE58_ADDRESS.fullmatch(value):
        raise ValueError("invalid Solana public address")
    return value


def _rpc(method: str, params: List[Any], timeout: float = 12) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(_RPC_URL, json=payload)
        response.raise_for_status()
        body = response.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return body.get("result")


def rpc_health() -> Dict[str, Any]:
    """Probe public Solana RPC health without sending a wallet address."""
    result = _rpc("getHealth", [], timeout=5)
    value = result if isinstance(result, str) else None
    ok = value == "ok"
    return {
        "ok": ok,
        "detail": "Solana mainnet RPC is healthy" if ok else f"Solana RPC returned {value!r}",
        "network": "mainnet-beta",
        "read_only": True,
    }


def wallet_snapshot(address: str) -> Dict[str, Any]:
    address = validate_address(address)
    balance = _rpc("getBalance", [address, {"commitment": "confirmed"}]) or {}
    token_result = _rpc(
        "getTokenAccountsByOwner",
        [
            address,
            {"programId": _TOKEN_PROGRAM},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    ) or {}
    tokens = []
    for row in token_result.get("value") or []:
        info = (((row.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        amount = info.get("tokenAmount") or {}
        ui_amount = amount.get("uiAmount")
        if ui_amount is None:
            try:
                ui_amount = int(amount.get("amount") or 0) / (10 ** int(amount.get("decimals") or 0))
            except (TypeError, ValueError):
                ui_amount = 0
        if float(ui_amount or 0) <= 0:
            continue
        tokens.append(
            {
                "mint": info.get("mint"),
                "amount": float(ui_amount),
                "decimals": amount.get("decimals"),
            }
        )
    tokens.sort(key=lambda row: row["amount"], reverse=True)
    return {
        "address": address,
        "sol_balance": round(float(balance.get("value") or 0) / 1_000_000_000, 9),
        "tokens": tokens[:100],
        "read_only": True,
        "stored": False,
        "rpc": "solana-mainnet",
        "ts": int(time.time() * 1000),
    }
