"""Dependency-free tests for deterministic risk sizing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.risk import calculate_position_size


def test_long_position_size() -> None:
    result = calculate_position_size(10_000, 1, 100, 95, 110)
    assert result["side"] == "long"
    assert result["risk_amount"] == 100
    assert result["quantity"] == 20
    assert result["notional"] == 2_000
    assert result["risk_reward"] == 2


def test_short_position_size() -> None:
    result = calculate_position_size(5_000, 0.5, 100, 102, 96)
    assert result["side"] == "short"
    assert result["risk_amount"] == 25
    assert result["quantity"] == 12.5
    assert result["risk_reward"] == 2


def test_notional_cap_reduces_risk() -> None:
    result = calculate_position_size(10_000, 1, 100, 99, 102, max_notional=1_000)
    assert result["capped"] is True
    assert result["notional"] == 1_000
    assert result["risk_amount"] == 10
    assert result["risk_percent"] == 0.1


def test_invalid_stop() -> None:
    try:
        calculate_position_size(10_000, 1, 100, 100)
    except ValueError as exc:
        assert "differ" in str(exc)
    else:
        raise AssertionError("equal entry and stop must fail")


def test_risk_above_defense_ceiling_fails() -> None:
    try:
        calculate_position_size(10_000, 2.01, 100, 95)
    except ValueError as exc:
        assert "at most 2" in str(exc)
    else:
        raise AssertionError("risk above 2% must fail")


if __name__ == "__main__":
    test_long_position_size()
    test_short_position_size()
    test_notional_cap_reduces_risk()
    test_invalid_stop()
    test_risk_above_defense_ceiling_fails()
    print("RISK_TESTS_OK")
