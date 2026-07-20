"""Core unit tests — no server required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.orchestrator import build_raven_orchestrator
from agents.raven_trader import TRADER_SYSTEM_PROMPT, analyze_timeframe, raven_analyze
from blockchain.anchor import SovereignProvenanceEngine
from jspace.core import JSpaceWorkspace
from services.pipeline import run_research, warm
from services.trade_intel import parse_trade_intent


def test_jspace_ignite_and_broadcast():
    ws = JSpaceWorkspace("t1")
    ws.ignite_concept("A", "Alpha", 1.0)
    ws.ignite_concept("B", "Beta", 0.8)
    ws.add_reasoning_chain("A", "B", "leads_to")
    out = ws.broadcast_workspace()
    assert len(out) >= 2
    assert out[0]["id"] in ("A", "B")


def test_parse_intent_btc_long():
    intent = parse_trade_intent("long BTC 4h accumulation")
    assert intent["primary_symbol"] == "BTC"
    assert intent["timeframe"] == "4h"
    assert intent["stance"] == "long_bias"


def test_orchestrator_invoke():
    orch = build_raven_orchestrator()
    out = orch.invoke({"query": "ETH swing long", "session_id": "test"})
    assert "output_text" in out
    assert out["workspace"].graph.has_node("Q1")


def test_pipeline_research():
    warm()
    r = run_research("short SOL 1h", "test-sess", reuse=True, include_counterfactual=True)
    assert r["status"] == "complete"
    assert r["plan"]["symbol"] == "SOL"
    assert r["latency_ms"] >= 0
    assert r["provenance"]["merkle_root"]
    assert r["counterfactual"] is not None


def test_merkle_stable():
    nodes = [{"id": "a", "label": "x", "ignition": 1, "confidence": 1, "evidence": []}]
    a = SovereignProvenanceEngine.compute_merkle_root(nodes)
    b = SovereignProvenanceEngine.compute_merkle_root(nodes)
    assert a == b


def test_raven_prompt_and_analyze():
    assert "Sniper Trades" in TRADER_SYSTEM_PROMPT
    assert "jspace" in TRADER_SYSTEM_PROMPT.lower()
    assert "Live Sniper Verdict" in TRADER_SYSTEM_PROMPT
    # synthetic uptrend candles
    candles = []
    px = 100.0
    for i in range(80):
        px += 0.4
        candles.append({"t": i, "o": px - 0.2, "h": px + 0.5, "l": px - 0.5, "c": px, "v": 10 + i % 3})
    tf = analyze_timeframe(candles, "1h")
    assert tf["last"] == candles[-1]["c"]
    assert tf["tools"]
    intent = {"primary_symbol": "BTC", "timeframe": "1h", "stance": "long_bias"}
    market = {
        "instrument": "BTC_USDT",
        "ticker": {"last": candles[-1]["c"], "source": "test"},
        "timeframes": {"1h": {"candles": candles}},
    }
    out = raven_analyze(intent, market, [])
    assert "jspace" in out
    assert out["trade_decision"]["direction"]
    assert out.get("verdict")
    assert "Live Sniper Verdict" in out["result_text"]
    assert "Active TradingView Analysis" in out["result_text"]


if __name__ == "__main__":
    test_jspace_ignite_and_broadcast()
    test_parse_intent_btc_long()
    test_orchestrator_invoke()
    test_pipeline_research()
    test_merkle_stable()
    test_raven_prompt_and_analyze()
    print("ALL_TESTS_OK")
