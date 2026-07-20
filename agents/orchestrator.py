"""LangGraph orchestrator — RavenTrader persona, trade-aware synthesis."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from agents.raven_trader import LIVE_SNIPER_TRADER_PROMPT, TRADER_SYSTEM_PROMPT
from jspace.core import JSpaceWorkspace
from services.trade_intel import build_structured_plan, parse_trade_intent, synthesize_report


def initialize_jspace_node(state: Dict[str, Any]) -> Dict[str, Any]:
    workspace = state.get("workspace")
    if workspace is None:
        workspace = JSpaceWorkspace(session_id=state.get("session_id", "default"))
    query = state.get("query", "")
    intent = state.get("intent") or parse_trade_intent(query)
    state["intent"] = intent
    state["system_prompt"] = LIVE_SNIPER_TRADER_PROMPT or TRADER_SYSTEM_PROMPT
    workspace.ignite_concept("Q1", f"Trade Query: {query}", confidence=1.0)
    state["workspace"] = workspace
    return state


def parallel_retrieval_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    workspace: JSpaceWorkspace = state["workspace"]
    intent = state.get("intent") or parse_trade_intent(state.get("query", ""))
    _ = workspace.broadcast_workspace()

    workspace.ignite_concept(
        "E1",
        f"Local ground truth scaffold for {intent.get('primary_symbol', 'BTC')}",
        confidence=0.95,
        metadata={"source": "on_device", "type": "scaffold", "trader": "Sniper Trades"},
    )
    # Attach lightweight evidence pointer (no external IO in graph step)
    try:
        node = workspace.graph.nodes["E1"]["concept"]
        node.evidence_pointers = [
            {"kind": "rule", "ref": "liquidity_first"},
            {"kind": "rule", "ref": "predefine_invalidation"},
            {"kind": "rule", "ref": "edge_over_fomo"},
            {"kind": "rule", "ref": "high_probability_only"},
            {"kind": "persona", "ref": "Sniper Trades"},
            {"kind": "intent", "ref": intent},
        ]
    except KeyError:
        pass

    workspace.add_reasoning_chain("Q1", "E1", relation="supported_by")
    state["workspace"] = workspace
    state["intent"] = intent
    return state


def cited_synthesis_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    workspace: JSpaceWorkspace = state["workspace"]
    intent = state.get("intent") or parse_trade_intent(state.get("query", ""))
    nodes = workspace.broadcast_workspace(top_k=7)
    plan = build_structured_plan(intent, nodes)
    state["plan"] = plan
    # Full RavenTrader text is assembled in pipeline after live CDC pack
    state["output_text"] = synthesize_report(intent, plan)
    return state


@lru_cache(maxsize=1)
def build_raven_orchestrator():
    workflow = StateGraph(dict)
    workflow.add_node("init_jspace", initialize_jspace_node)
    workflow.add_node("parallel_retrieval", parallel_retrieval_agent)
    workflow.add_node("cited_synthesis", cited_synthesis_agent)

    workflow.set_entry_point("init_jspace")
    workflow.add_edge("init_jspace", "parallel_retrieval")
    workflow.add_edge("parallel_retrieval", "cited_synthesis")
    workflow.add_edge("cited_synthesis", END)

    return workflow.compile()
