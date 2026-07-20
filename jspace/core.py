"""Sovereign J-Space reasoning workspace (smooth, bounded state)."""

from __future__ import annotations

import copy
import time
from typing import Any, Dict, List, Optional

import networkx as nx

# Cap history so long sessions stay responsive
_MAX_HISTORY = 64


class JSpaceConcept:
    __slots__ = (
        "concept_id",
        "label",
        "metadata",
        "ignition_score",
        "confidence_score",
        "evidence_pointers",
        "updated_at",
    )

    def __init__(
        self,
        concept_id: str,
        label: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.concept_id = concept_id
        self.label = label
        self.metadata = metadata or {}
        self.ignition_score = 1.0
        self.confidence_score = 1.0
        self.evidence_pointers: List[Any] = []
        self.updated_at = time.time()


class JSpaceWorkspace:
    """
    Explicit, interpretable higher-order global workspace:
    concept ignition, compression broadcast, counterfactual branches.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.graph = nx.DiGraph()
        self.history: List[Dict[str, Any]] = []
        self.version_counter = 0

    def ignite_concept(
        self,
        concept_id: str,
        label: str,
        confidence: float = 1.0,
        metadata: Optional[dict] = None,
    ) -> None:
        if not self.graph.has_node(concept_id):
            concept = JSpaceConcept(concept_id, label, metadata)
            concept.confidence_score = confidence
            self.graph.add_node(concept_id, concept=concept)
        else:
            node = self.graph.nodes[concept_id]["concept"]
            node.ignition_score += 0.5
            node.updated_at = time.time()
            if label:
                node.label = label
            if confidence is not None:
                node.confidence_score = max(node.confidence_score, confidence)
        self._record_state(f"Ignite concept: {concept_id}")

    def add_reasoning_chain(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
    ) -> None:
        if self.graph.has_node(source_id) and self.graph.has_node(target_id):
            self.graph.add_edge(
                source_id, target_id, relation=relation, weight=weight
            )
            self._record_state(f"Connect reasoning: {source_id} -> {target_id}")

    def broadcast_workspace(self, top_k: int = 7) -> List[Dict[str, Any]]:
        """Compress active concepts for downstream agents / UI."""
        concepts: List[Dict[str, Any]] = []
        for node_id, data in self.graph.nodes(data=True):
            concept: JSpaceConcept = data["concept"]
            concepts.append(
                {
                    "id": node_id,
                    "label": concept.label,
                    "ignition": round(concept.ignition_score, 4),
                    "confidence": round(concept.confidence_score, 4),
                    "evidence": concept.evidence_pointers,
                }
            )
        concepts.sort(key=lambda x: x["ignition"], reverse=True)
        return concepts[:top_k]

    def inject_counterfactual(
        self, hypothetical_node_id: str, description: str
    ) -> "JSpaceWorkspace":
        """Branch workspace for speculative hypotheses (deep-copy)."""
        branched = copy.deepcopy(self)
        branched.version_counter += 1
        branched.ignite_concept(
            hypothetical_node_id,
            label=f"HYPOTHETICAL: {description}",
            confidence=0.5,
            metadata={
                "type": "counterfactual",
                "parent_version": self.version_counter,
            },
        )
        return branched

    def _record_state(self, action: str) -> None:
        # Lightweight snapshot — avoid dumping full edge payloads every time
        snapshot = {
            "version": self.version_counter,
            "timestamp": time.time(),
            "action": action,
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
        }
        self.history.append(snapshot)
        if len(self.history) > _MAX_HISTORY:
            # Drop oldest half to keep appends amortized O(1)-ish
            self.history = self.history[-(_MAX_HISTORY // 2) :]
