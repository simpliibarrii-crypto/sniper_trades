"""Tests for Grok fallback, alerts store, and paper portfolio helpers."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import alerts_store, grok_live, paper_portfolio


def test_grok_status_and_local_comment():
    st = grok_live.grok_status()
    assert "configured" in st
    assert st["provider"]
    out = grok_live.generate_live_comment(
        {
            "instrument": "BTC_USDT",
            "timeframe": "1m",
            "ticker": {"last": 65000.0, "source": "test"},
            "verdict": {"verdict": "Hold", "conviction": 40, "one_liner": "stand aside"},
            "strategy_position": {"next_action": "wait"},
            "mtf_analyses": {"1m": {"bias_label": "RANGE / MIXED"}},
            "jspace": "test jspace",
            "tv_analysis": "test tv",
            "news_headlines": ["CoinDesk: test headline"],
        }
    )
    assert out["text"]
    assert out["source"] in ("local_fallback", "grok_api", "error")


def test_alerts_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        alerts_store._STORE = Path(d) / "alerts.json"
        row = alerts_store.add_alert("BTC_USDT", "above", 100.0, "unit")
        assert row["id"].startswith("A_")
        listed = alerts_store.list_alerts()
        assert listed["count"] == 1
        fired = alerts_store.evaluate_prices({"BTC_USDT": 101.0})
        assert len(fired) == 1
        assert fired[0]["triggered"] is True


def test_paper_portfolio_shape():
    snap = paper_portfolio.portfolio_snapshot()
    assert "cash_total" in snap
    assert "open_positions" in snap
    assert "equity_mark" in snap


if __name__ == "__main__":
    test_grok_status_and_local_comment()
    test_alerts_roundtrip()
    test_paper_portfolio_shape()
    print("GROK_ALERTS_OK")
