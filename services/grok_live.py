"""
Grok (xAI / SpaceXAI) live commentary for Sniper Trades.

Uses OpenAI-compatible xAI API when XAI_API_KEY or SNIPER_XAI_API_KEY is set.
Falls back to a local structured brief from Raven analysis so the deck never blanks.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from agents.raven_trader import LIVE_SNIPER_TRADER_PROMPT

_DEFAULT_MODEL = "grok-4-1-fast-non-reasoning"
_BASE = "https://api.x.ai/v1"


def _api_key() -> Optional[str]:
    return (
        os.environ.get("SNIPER_XAI_API_KEY")
        or os.environ.get("XAI_API_KEY")
        or os.environ.get("GROK_API_KEY")
        or None
    )


def _model() -> str:
    return os.environ.get("SNIPER_XAI_MODEL") or os.environ.get("XAI_MODEL") or _DEFAULT_MODEL


def grok_status() -> Dict[str, Any]:
    key = _api_key()
    return {
        "provider": "xAI / SpaceXAI (OpenAI-compatible)",
        "base_url": _BASE,
        "model": _model(),
        "configured": bool(key),
        "mode": "live_api" if key else "local_fallback",
        "hint": None
        if key
        else "Set XAI_API_KEY or SNIPER_XAI_API_KEY for live Grok commentary (https://console.x.ai)",
    }


def _local_brief(context: Dict[str, Any]) -> str:
    """Deterministic fallback when no xAI key — still useful on the live deck."""
    ticker = context.get("ticker") or {}
    verdict = context.get("verdict") or {}
    sp = context.get("strategy_position") or {}
    mtf = context.get("mtf_analyses") or {}
    news = context.get("news_headlines") or []
    instrument = context.get("instrument") or ticker.get("instrument") or "—"
    last = ticker.get("last")
    v = verdict.get("verdict") or sp.get("side") or "Hold"
    conv = verdict.get("conviction")
    mtf_bits = []
    for k in list(mtf)[:6]:
        label = (mtf.get(k) or {}).get("bias_label", "?")
        mtf_bits.append(f"{k}:{label}")
    mtf_str = ", ".join(mtf_bits) if mtf_bits else "n/a"
    lines = [
        f"[local Grok fallback] {instrument} last={last} → **{v}**"
        + (f" ({conv}% conviction)" if conv is not None else ""),
        f"MTF: {mtf_str}",
        f"Next: {sp.get('next_action') or verdict.get('one_liner') or 'reassess on next candle'}",
    ]
    if news:
        lines.append("News pulse: " + " | ".join(str(n)[:70] for n in news[:3]))
    lines.append(
        "Bias check: without live Grok API this is rule-based only — set XAI_API_KEY for model commentary."
    )
    return "\n".join(lines)


def _build_user_prompt(context: Dict[str, Any]) -> str:
    compact = {
        "instrument": context.get("instrument"),
        "timeframe": context.get("timeframe"),
        "ticker": {
            k: (context.get("ticker") or {}).get(k)
            for k in ("last", "bid", "ask", "change", "volume", "source")
        },
        "verdict": context.get("verdict"),
        "strategy_position": {
            k: (context.get("strategy_position") or {}).get(k)
            for k in (
                "side",
                "entry",
                "stop_loss",
                "take_profit_1",
                "position_size_pct",
                "risk_reward",
                "next_action",
            )
        },
        "mtf_analyses": context.get("mtf_analyses"),
        "jspace_excerpt": (context.get("jspace") or "")[:900],
        "tv_excerpt": (context.get("tv_analysis") or "")[:700],
        "news_headlines": (context.get("news_headlines") or [])[:5],
        "ts": context.get("ts") or time.time(),
    }
    return (
        "Live market tick for Sniper Trades. Produce a SHORT live update (max ~12 lines):\n"
        "1) What price is doing right now\n"
        "2) Whether the sniper verdict still holds\n"
        "3) One risk / bias check\n"
        "4) One concrete next action (paper first)\n"
        "No hype. No invented fills. Prefer Hold if edge is thin.\n\n"
        f"CONTEXT_JSON:\n{json.dumps(compact, default=str)[:6000]}"
    )


def generate_live_comment(context: Dict[str, Any], timeout: float = 25.0) -> Dict[str, Any]:
    """
    Return { text, source, model, latency_ms }.
    source: grok_api | local_fallback | error
    """
    t0 = time.perf_counter()
    key = _api_key()
    model = _model()
    if not key:
        text = _local_brief(context)
        return {
            "text": text,
            "source": "local_fallback",
            "model": None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "configured": False,
        }

    system = (
        (LIVE_SNIPER_TRADER_PROMPT or "")[:2500]
        + "\n\nYou are connected as the LIVE deck co-pilot. Keep replies tight and actionable."
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": _build_user_prompt(context)},
        ],
        "temperature": 0.35,
        "max_tokens": 500,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices") or []
        text = ""
        if choices:
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
        if not text:
            text = _local_brief(context)
            source = "local_fallback"
        else:
            source = "grok_api"
        return {
            "text": text,
            "source": source,
            "model": model,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "configured": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "text": _local_brief(context) + f"\n[Grok API error: {str(exc)[:160]}]",
            "source": "error",
            "model": model,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "configured": True,
            "error": str(exc)[:240],
        }


def headlines_from_news(news_payload: Optional[Dict[str, Any]]) -> List[str]:
    items = (news_payload or {}).get("items") or []
    out: List[str] = []
    for it in items[:6]:
        title = (it.get("title") or "").strip()
        src = (it.get("source") or "").strip()
        if title:
            out.append(f"{src}: {title}" if src else title)
    return out
