"""Deterministic position sizing used by the API and premium console."""

from __future__ import annotations

from typing import Any, Dict, Optional


def calculate_position_size(
    equity: float,
    risk_percent: float,
    entry: float,
    stop: float,
    target: Optional[float] = None,
    max_notional: Optional[float] = None,
) -> Dict[str, Any]:
    """Size a position from loss-at-stop, optionally enforcing a notional cap."""
    equity = float(equity)
    risk_percent = float(risk_percent)
    entry = float(entry)
    stop = float(stop)
    target = float(target) if target is not None else None
    max_notional = float(max_notional) if max_notional is not None else None

    if equity <= 0 or entry <= 0 or stop <= 0:
        raise ValueError("equity, entry, and stop must be greater than zero")
    if not 0 < risk_percent <= 2:
        raise ValueError("risk_percent must be greater than 0 and at most 2")
    if entry == stop:
        raise ValueError("entry and stop must differ")
    if max_notional is not None and max_notional <= 0:
        raise ValueError("max_notional must be greater than zero")

    side = "long" if stop < entry else "short"
    unit_risk = abs(entry - stop)
    requested_risk = equity * risk_percent / 100
    quantity = requested_risk / unit_risk
    notional = quantity * entry
    capped = bool(max_notional is not None and notional > max_notional)
    if capped:
        notional = float(max_notional)
        quantity = notional / entry

    actual_risk = quantity * unit_risk
    reward_amount = None
    risk_reward = None
    warnings = []
    if target is not None:
        reward_per_unit = (target - entry) if side == "long" else (entry - target)
        if reward_per_unit <= 0:
            warnings.append(f"Target is on the wrong side of entry for a {side} setup.")
        else:
            reward_amount = quantity * reward_per_unit
            risk_reward = reward_per_unit / unit_risk
            if risk_reward < 1.5:
                warnings.append("Risk/reward is below the 1.5 minimum sniper threshold.")

    leverage = notional / equity
    if risk_percent > 1:
        warnings.append("Risk above the 1% defense default requires exceptional conviction.")
    if leverage > 1:
        warnings.append("Position notional exceeds account equity; leverage or margin is implied.")
    if capped:
        warnings.append("Quantity was reduced to respect the notional cap.")

    return {
        "side": side,
        "equity": round(equity, 2),
        "risk_percent": round((actual_risk / equity) * 100, 4),
        "risk_amount": round(actual_risk, 2),
        "entry": entry,
        "stop": stop,
        "target": target,
        "quantity": round(quantity, 8),
        "notional": round(notional, 2),
        "position_percent": round((notional / equity) * 100, 2),
        "effective_leverage": round(leverage, 3),
        "reward_amount": round(reward_amount, 2) if reward_amount is not None else None,
        "risk_reward": round(risk_reward, 2) if risk_reward is not None else None,
        "capped": capped,
        "warnings": warnings,
    }
