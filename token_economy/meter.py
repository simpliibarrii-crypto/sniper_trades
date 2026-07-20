"""Compute credit metering — simple, append-friendly."""

from __future__ import annotations

import time
from typing import Any, Dict


class ComputeCreditLedger:
    def __init__(self) -> None:
        self.session_metrics: Dict[str, Dict[str, Any]] = {}

    def log_inference_session(
        self,
        session_id: str,
        tokens_generated: int,
        accepted_drafts: int,
        baseline_ms: float,
    ) -> Dict[str, Any]:
        tokens_generated = max(1, int(tokens_generated))
        accepted_drafts = max(0, int(accepted_drafts))
        efficiency_gain = (accepted_drafts / tokens_generated) * 100.0
        row = {
            "timestamp": time.time(),
            "tokens_generated": tokens_generated,
            "accepted_drafts": accepted_drafts,
            "dspark_efficiency_pct": round(min(100.0, efficiency_gain), 2),
            "compute_cost_credits": round(tokens_generated * 0.1, 4),
            "baseline_ms": round(float(baseline_ms), 2),
        }
        self.session_metrics[session_id] = row
        return row
