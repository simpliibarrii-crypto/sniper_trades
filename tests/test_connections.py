"""Connection-registry contract tests; no network required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.integrations import integration_snapshot


def test_connection_catalog_is_safe_and_user_ready():
    payload = integration_snapshot(probe=False)
    items = payload["items"]
    ids = {item["id"] for item in items}
    assert len(ids) == len(items)
    assert {
        "market_stack",
        "crypto_news",
        "dex_screener",
        "solana_rpc",
        "phantom",
        "jupiter",
        "raven_tools",
        "risk_engine",
        "paper_ledger",
        "crypto_com_execution",
    }.issubset(ids)
    assert payload["safety"]["private_keys_received"] is False
    assert payload["safety"]["automatic_dex_execution"] is False
    assert payload["summary"]["live_execution_enabled"] is False
    assert all(item["purpose"] and item["setup"] and item["action_label"] for item in items)


if __name__ == "__main__":
    test_connection_catalog_is_safe_and_user_ready()
    print("CONNECTION_TESTS_OK")
