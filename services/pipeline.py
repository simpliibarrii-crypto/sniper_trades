"""Research pipeline — RavenTrader + J-Space + CDC market + provenance."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from agents.orchestrator import build_raven_orchestrator
from agents.raven_trader import LIVE_SNIPER_TRADER_PROMPT, TRADER_SYSTEM_PROMPT, raven_analyze
from blockchain.anchor import SovereignProvenanceEngine
from config import get_settings
from jspace.core import JSpaceWorkspace
from services.raven_market_pack import build_market_pack
from services.trade_intel import build_structured_plan, parse_trade_intent, synthesize_report
from token_economy.meter import ComputeCreditLedger

_sessions: "OrderedDict[str, JSpaceWorkspace]" = OrderedDict()
_ledger = ComputeCreditLedger()
_orchestrator = None


def warm() -> None:
    global _orchestrator
    _orchestrator = build_raven_orchestrator()
    _orchestrator.invoke({"query": "warmup", "session_id": "__warmup__"})


def shutdown() -> None:
    _sessions.clear()


def session_count() -> int:
    return len(_sessions)


def list_sessions() -> list[Dict[str, Any]]:
    out = []
    for sid, ws in _sessions.items():
        out.append(
            {
                "session_id": sid,
                "nodes": ws.graph.number_of_nodes(),
                "edges": ws.graph.number_of_edges(),
                "history_len": len(ws.history),
            }
        )
    return out


def drop_session(session_id: str) -> bool:
    return _sessions.pop(session_id, None) is not None


def _get_session(session_id: str) -> Optional[JSpaceWorkspace]:
    ws = _sessions.get(session_id)
    if ws is not None:
        _sessions.move_to_end(session_id)
    return ws


def _put_session(session_id: str, workspace: JSpaceWorkspace) -> None:
    settings = get_settings()
    _sessions[session_id] = workspace
    _sessions.move_to_end(session_id)
    while len(_sessions) > settings.max_sessions:
        _sessions.popitem(last=False)


def run_research(
    query: str,
    session_id: str,
    reuse: bool = True,
    include_counterfactual: bool = True,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    settings = get_settings()
    orch = _orchestrator or build_raven_orchestrator()

    intent = parse_trade_intent(query)
    prev = _get_session(session_id) if reuse else None
    # Multi-turn continuity: inherit symbol/TF from warm session when omitted
    if prev is not None:
        try:
            if not intent.get("symbol_explicit") and "SYM" in prev.graph.nodes:
                lab = prev.graph.nodes["SYM"]["concept"].label
                if ":" in lab:
                    inherited = lab.split(":", 1)[1].strip().split()[0]
                    if inherited:
                        intent["primary_symbol"] = inherited
                        intent["symbols"] = [inherited]
            if not intent.get("timeframe_explicit") and "TF" in prev.graph.nodes:
                tlab = prev.graph.nodes["TF"]["concept"].label
                if ":" in tlab:
                    intent["timeframe"] = tlab.split(":", 1)[1].strip()
            if intent.get("stance") == "neutral" and "STANCE" in prev.graph.nodes:
                slab = prev.graph.nodes["STANCE"]["concept"].label
                if ":" in slab:
                    prev_stance = slab.split(":", 1)[1].strip()
                    if prev_stance and prev_stance != "neutral":
                        intent["stance"] = prev_stance
        except Exception:
            pass

    initial: Dict[str, Any] = {
        "query": query,
        "session_id": session_id,
        "intent": intent,
    }
    if prev is not None:
        initial["workspace"] = prev

    final_output = orch.invoke(initial)
    workspace: JSpaceWorkspace = final_output["workspace"]

    # Enrich with structured trade nodes
    sym = intent["primary_symbol"]
    workspace.ignite_concept(
        "SYM",
        f"Symbol focus: {sym}",
        confidence=0.99,
        metadata={"symbols": intent["symbols"]},
    )
    workspace.ignite_concept(
        "TF",
        f"Timeframe: {intent['timeframe']}",
        confidence=0.9,
    )
    workspace.ignite_concept(
        "STANCE",
        f"Stance: {intent['stance']}",
        confidence=0.85,
    )
    workspace.ignite_concept(
        "SNIPER",
        "Sniper Trades live persona active",
        confidence=1.0,
        metadata={"prompt_bytes": len(LIVE_SNIPER_TRADER_PROMPT or TRADER_SYSTEM_PROMPT)},
    )
    workspace.add_reasoning_chain("Q1", "SYM", "focuses")
    workspace.add_reasoning_chain("SYM", "TF", "viewed_on")
    workspace.add_reasoning_chain("SYM", "STANCE", "bias")
    workspace.add_reasoning_chain("E1", "SYM", "grounds")
    workspace.add_reasoning_chain("SNIPER", "SYM", "analyzes")

    nodes = workspace.broadcast_workspace(top_k=settings.broadcast_top_k)

    # Live Crypto.com multi-TF pack → RavenTrader decision
    raven: Optional[Dict[str, Any]] = None
    market_err: Optional[str] = None
    data_sources: list = []
    try:
        market = build_market_pack(sym, primary_tf=intent["timeframe"])
        data_sources = market.get("data_sources") or []
        raven = raven_analyze(intent, market, nodes)
        plan = raven["plan"]
        text = raven["result_text"]
        src_note = ",".join(data_sources) or (market.get("ticker") or {}).get("source") or "free"
        workspace.ignite_concept(
            "MKT",
            f"Free feed [{src_note}] {market.get('instrument')} last={ (market.get('ticker') or {}).get('last') }",
            confidence=0.95,
            metadata={
                "instrument": market.get("instrument"),
                "sources": data_sources,
                "feed": market.get("feed"),
            },
        )
        workspace.add_reasoning_chain("SNIPER", "MKT", "uses_feed")
        nodes = workspace.broadcast_workspace(top_k=settings.broadcast_top_k)
    except Exception as exc:  # noqa: BLE001
        market_err = str(exc)
        plan = build_structured_plan(intent, nodes)
        text = final_output.get("output_text") or synthesize_report(intent, plan)
        text = (
            f"1. **jspace (Live Internal Thoughts)**:\n"
            f"Market feed unavailable ({market_err}). Falling back to intent-only scaffold.\n"
            f"Bias check: without live OHLCV I cannot claim sniper confluence — "
            f"treat this as research notes only.\n\n"
            f"2. **Active TradingView Analysis**:\n"
            f"- Tools offline until free 1m+ candles return.\n\n"
            f"3. **Current Strategy Position**:\n"
            f"- Flat — no entry until feed recovers · Symbol: {sym} · TF: {intent['timeframe']}\n\n"
            f"4. **Live Sniper Verdict**: **Hold** · conviction 10% (feed down)\n"
            f"Do not size risk until Sniper Trades has free 1m+ candles.\n\n"
            f"---\n{text}"
        )

    counterfactual = None
    if include_counterfactual:
        branch = workspace.inject_counterfactual(
            "CF1",
            f"{sym} invalidates setup / regime flips against plan",
        )
        counterfactual = {
            "description": f"If {sym} invalidates: cut risk, preserve cash, re-map structure",
            "nodes": branch.broadcast_workspace(top_k=4),
            "version": branch.version_counter,
        }

    _put_session(session_id, workspace)
    root = SovereignProvenanceEngine.compute_merkle_root(nodes)
    anchor = SovereignProvenanceEngine.anchor_to_stellar_network(root, "")
    metrics = _ledger.log_inference_session(
        session_id,
        tokens_generated=max(1, len(query.split()) * 4 + (len(text) // 8)),
        accepted_drafts=max(1, len(nodes)),
        baseline_ms=(time.perf_counter() - t0) * 1000,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    out: Dict[str, Any] = {
        "status": "complete",
        "session_id": session_id,
        "trader": "Sniper Trades",
        "result_text": text,
        "plan": plan,
        "jspace_active_nodes": nodes,
        "counterfactual": counterfactual,
        "provenance": {"merkle_root": root, "anchor": anchor},
        "metrics": metrics,
        "latency_ms": elapsed_ms,
    }
    if raven:
        out["jspace_thoughts"] = raven.get("jspace")
        out["tv_analysis"] = raven.get("tv_analysis")
        out["strategy_position"] = raven.get("strategy_position")
        out["verdict"] = raven.get("verdict")
        out["trade_decision"] = raven.get("trade_decision")
        out["summary"] = raven.get("summary")
        out["mtf_analyses"] = raven.get("analyses")
    if data_sources:
        out["data_sources"] = data_sources
    if market_err:
        out["market_error"] = market_err
    return out
