"""Pure safety tests for DEX discovery and public-wallet validation."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.dex_intel import risk_flags
from services.solana_wallet import validate_address


def test_new_thin_pair_is_never_marked_safe() -> None:
    flags = risk_flags(
        {
            "liquidity": {"usd": 12_000},
            "volume": {"h24": 500_000},
            "priceChange": {"m5": 32},
            "pairCreatedAt": int(time.time() * 1000) - 3_600_000,
        },
        profiled=True,
    )
    assert "unvetted token" in flags
    assert "very thin liquidity" in flags
    assert "pool under 24h old" in flags
    assert "extreme 5m move" in flags


def test_solana_address_validation() -> None:
    address = "So11111111111111111111111111111111111111112"
    assert validate_address(address) == address
    try:
        validate_address("not-a-wallet")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid address must fail")


if __name__ == "__main__":
    test_new_thin_pair_is_never_marked_safe()
    test_solana_address_validation()
    print("INTEGRATION_TESTS_OK")
