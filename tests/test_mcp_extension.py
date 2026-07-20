"""Safety and response-contract tests for the MCP catalog and Chrome bridge."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main
from services.mcp_registry import catalog


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers or []})


def test_mcp_catalog_is_opt_in_and_credential_free() -> None:
    payload = catalog()
    assert payload["safety"]["auto_install"] is False
    assert payload["safety"]["auto_start_wallet_mcp"] is False
    assert all(item["default_enabled"] is False for item in payload["items"] if item["kind"].endswith("mcp"))
    assert "private" not in json.dumps(payload).lower() or payload["safety"]["private_keys_received"] is False


def test_extension_snapshot_omits_private_jspace() -> None:
    market = {
        "instrument": "BTC_USDT",
        "ticker": {"last": 101.0, "bid": 100.9, "ask": 101.1, "source": "fixture"},
        "timeframes": {"1m": {"candles": [{"t": 1, "o": 100, "h": 102, "l": 99, "c": 101, "v": 10}]}},
    }
    raven = {
        "jspace": "must not leave the server",
        "trade_decision": {"direction": "Long", "conviction": 71, "entry": 101.0},
        "verdict": {"verdict": "Long", "conviction": 71},
        "summary": "Fixture setup",
        "analyses": {"1m": {"bias_label": "bullish", "bias_score": 1.2, "rsi": 55, "atr": 2, "tools": [{"tool": "RSI", "reading": "55"}]}},
    }
    with patch.object(main, "build_market_pack", return_value=market), patch.object(main, "raven_analyze", return_value=raven):
        payload = asyncio.run(main.extension_snapshot("BTC_USDT", "1m"))
    assert payload["decision"]["qualified"] is True
    assert payload["execution"]["browser_can_submit_orders"] is False
    assert "jspace" not in payload
    assert payload["evidence"]["tools"][0]["tool"] == "RSI"


def test_live_submission_has_server_side_lock() -> None:
    original_enabled = main._settings.live_trading_enabled
    original_token = main._settings.control_token
    try:
        main._settings.live_trading_enabled = False
        main._settings.control_token = ""
        try:
            main._authorize_live_submission(_request())
        except main.HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("disabled live submission must fail")

        main._settings.live_trading_enabled = True
        main._settings.control_token = "test-control-token"
        try:
            main._authorize_live_submission(_request())
        except main.HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("missing control token must fail")

        main._authorize_live_submission(
            _request([(b"x-sniper-control-token", b"test-control-token")])
        )
    finally:
        main._settings.live_trading_enabled = original_enabled
        main._settings.control_token = original_token


def test_extension_manifest_has_local_read_only_scope() -> None:
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert set(manifest["host_permissions"]) == {
        "http://127.0.0.1/*",
        "http://localhost/*",
    }
    assert "tabs" not in manifest.get("permissions", [])
    assert "webRequest" not in manifest.get("permissions", [])


if __name__ == "__main__":
    test_mcp_catalog_is_opt_in_and_credential_free()
    test_extension_snapshot_omits_private_jspace()
    test_live_submission_has_server_side_lock()
    test_extension_manifest_has_local_read_only_scope()
    print("BRIDGE_TESTS_OK")
