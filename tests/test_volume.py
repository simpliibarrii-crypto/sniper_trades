"""Dependency-free tests for Raven predictive volume pressure."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.raven_trader import predictive_volume_pressure


def _bars(last_direction: str = "up", last_volume: float = 30.0):
    rows = []
    price = 100.0
    for index in range(30):
        open_px = price
        price += 0.2
        rows.append(
            {
                "t": index,
                "o": open_px,
                "h": price + 0.1,
                "l": open_px - 0.1,
                "c": price,
                "v": 10 + index % 2,
            }
        )
    if last_direction == "down":
        rows[-1].update({"o": price + 1, "h": price + 1.1, "l": price - 1, "c": price - 0.9})
    rows[-1]["v"] = last_volume
    return rows


def test_expanding_buy_volume_reads_bullish() -> None:
    result = predictive_volume_pressure(_bars("up", 35), "1h")
    assert result["projected_ratio"] > 2
    assert result["pressure_score"] > 20
    assert result["bias"] == "bullish pressure"


def test_expanding_sell_volume_reads_bearish() -> None:
    result = predictive_volume_pressure(_bars("down", 35), "1h")
    assert result["projected_ratio"] > 2
    assert result["pressure_score"] < -20
    assert result["bias"] == "bearish pressure"


if __name__ == "__main__":
    test_expanding_buy_volume_reads_bullish()
    test_expanding_sell_volume_reads_bearish()
    print("VOLUME_TESTS_OK")
