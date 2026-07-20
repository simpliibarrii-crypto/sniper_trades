"""
Grok live commentary for Sniper Trades.

Auth order (prefer Grok Build session over console API key credits):
  1) Grok Build OIDC session in ~/.grok/auth.json  (grok login / subscription)
  2) XAI_API_KEY / SNIPER_XAI_API_KEY / GROK_API_KEY  (console.x.ai credits)
  3) Local structured brief (always available)

API: OpenAI-compatible https://api.x.ai/v1/chat/completions
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from agents.raven_trader import LIVE_SNIPER_TRADER_PROMPT

_DEFAULT_MODEL = "grok-4.5"
_FALLBACK_MODEL = "grok-4-1-fast-non-reasoning"
_BASE = "https://api.x.ai/v1"
_AUTH_PATH = Path.home() / ".grok" / "auth.json"


def _console_api_key() -> Optional[str]:
    return (
        os.environ.get("SNIPER_XAI_API_KEY")
        or os.environ.get("XAI_API_KEY")
        or os.environ.get("GROK_API_KEY")
        or None
    )


def _parse_expires_at(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        # ISO with optional nanos
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # trim fractional seconds to microseconds
        if "." in s:
            head, rest = s.split(".", 1)
            frac = ""
            tz = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tz = rest[i:]
                    break
            frac = (frac + "000000")[:6]
            s = f"{head}.{frac}{tz}"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _grok_build_token() -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Return (access_token, meta) from Grok Build auth.json OIDC session.
    Prefer non-expired entries; allow slight skew.
    """
    meta: Dict[str, Any] = {"path": str(_AUTH_PATH), "found": False}
    if not _AUTH_PATH.is_file():
        meta["hint"] = "Run `grok login` to authenticate Grok Build"
        return None, meta
    try:
        data = json.loads(_AUTH_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        meta["error"] = str(exc)[:120]
        return None, meta
    if not isinstance(data, dict) or not data:
        return None, meta

    now = time.time()
    best: Optional[Tuple[str, Dict[str, Any]]] = None
    for _k, entry in data.items():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if not token or not isinstance(token, str):
            continue
        exp = _parse_expires_at(entry.get("expires_at"))
        # skip if expired more than 2 minutes ago
        if exp is not None and exp < now - 120:
            continue
        info = {
            "auth_mode": entry.get("auth_mode") or "oidc",
            "email": entry.get("email"),
            "team_id": entry.get("team_id"),
            "expires_at": entry.get("expires_at"),
            "source": "grok_build_session",
        }
        best = (token, info)
        # prefer oidc
        if (entry.get("auth_mode") or "").lower() == "oidc":
            break
    if not best:
        meta["hint"] = "Grok Build session expired — run `grok login`"
        meta["found"] = True
        meta["expired"] = True
        return None, meta
    token, info = best
    meta.update(info)
    meta["found"] = True
    return token, meta


def _auth() -> Tuple[Optional[str], str, Dict[str, Any]]:
    """
    Return (bearer_token, auth_source, meta).
    auth_source: grok_build | api_key | none
    """
    prefer = (os.environ.get("SNIPER_GROK_AUTH") or "build_first").strip().lower()
    build_tok, build_meta = _grok_build_token()
    console = _console_api_key()

    if prefer in ("build", "build_first", "grok_build", "session"):
        if build_tok:
            return build_tok, "grok_build", build_meta
        if console and prefer == "build_first":
            return console, "api_key", {"fallback_from": "grok_build", **build_meta}
        return None, "none", build_meta

    if prefer in ("api", "api_key", "console"):
        if console:
            return console, "api_key", {}
        if build_tok:
            return build_tok, "grok_build", build_meta
        return None, "none", build_meta

    # default: build first
    if build_tok:
        return build_tok, "grok_build", build_meta
    if console:
        return console, "api_key", {}
    return None, "none", build_meta


def _model(auth_source: str = "none") -> str:
    explicit = os.environ.get("SNIPER_XAI_MODEL") or os.environ.get("XAI_MODEL")
    if explicit:
        return explicit
    # Grok Build sessions work best with current default models
    if auth_source == "grok_build":
        return _DEFAULT_MODEL
    return os.environ.get("SNIPER_CONSOLE_MODEL") or _FALLBACK_MODEL


def grok_status() -> Dict[str, Any]:
    token, source, meta = _auth()
    return {
        "provider": "xAI / Grok Build",
        "base_url": _BASE,
        "model": _model(source),
        "configured": bool(token),
        "mode": (
            "grok_build_session"
            if source == "grok_build"
            else "live_api"
            if source == "api_key"
            else "local_fallback"
        ),
        "auth_source": source,
        "session_email": meta.get("email"),
        "session_expires_at": meta.get("expires_at"),
        "hint": None
        if token
        else (
            meta.get("hint")
            or "Run `grok login` (Grok Build) or set XAI_API_KEY with console credits"
        ),
    }


def _local_brief(context: Dict[str, Any]) -> str:
    """Deterministic fallback when no Grok auth — still useful on the live deck."""
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
        f"[local brief] {instrument} last={last} → **{v}**"
        + (f" ({conv}% conviction)" if conv is not None else ""),
        f"MTF: {mtf_str}",
        f"Next: {sp.get('next_action') or verdict.get('one_liner') or 'reassess on next candle'}",
    ]
    if news:
        lines.append("News pulse: " + " | ".join(str(n)[:70] for n in news[:3]))
    lines.append(
        "Bias check: rule-based only — connect Grok Build (`grok login`) for model commentary."
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


def _chat(token: str, model: str, system: str, user: str, timeout: float) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.35,
        "max_tokens": 500,
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return (msg.get("content") or "").strip()


def generate_live_comment(context: Dict[str, Any], timeout: float = 25.0) -> Dict[str, Any]:
    """
    Return { text, source, model, latency_ms, auth_source }.
    source: grok_build | grok_api | local_fallback | error
    """
    t0 = time.perf_counter()
    token, auth_source, meta = _auth()
    model = _model(auth_source)

    if not token:
        text = _local_brief(context)
        return {
            "text": text,
            "source": "local_fallback",
            "model": None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "configured": False,
            "auth_source": auth_source,
            "hint": meta.get("hint"),
        }

    system = (
        (LIVE_SNIPER_TRADER_PROMPT or "")[:2500]
        + "\n\nYou are connected as the LIVE deck co-pilot via Grok Build. "
        "Keep replies tight and actionable. Paper-first; never invent fills."
    )
    user = _build_user_prompt(context)

    try:
        text = _chat(token, model, system, user, timeout)
        # Retry with fallback model if empty / some teams only allow certain ids
        if not text and model != _FALLBACK_MODEL:
            text = _chat(token, _FALLBACK_MODEL, system, user, timeout)
            model = _FALLBACK_MODEL
        if not text:
            return {
                "text": _local_brief(context),
                "source": "local_fallback",
                "model": model,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "configured": True,
                "auth_source": auth_source,
            }
        return {
            "text": text,
            "source": "grok_build" if auth_source == "grok_build" else "grok_api",
            "model": model,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "configured": True,
            "auth_source": auth_source,
            "session_email": meta.get("email"),
        }
    except Exception as exc:  # noqa: BLE001
        # If build session failed, try console key once
        console = _console_api_key()
        if auth_source == "grok_build" and console:
            try:
                alt_model = os.environ.get("SNIPER_CONSOLE_MODEL") or _FALLBACK_MODEL
                text = _chat(console, alt_model, system, user, timeout)
                if text:
                    return {
                        "text": text,
                        "source": "grok_api",
                        "model": alt_model,
                        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                        "configured": True,
                        "auth_source": "api_key",
                        "note": "fell back from grok_build session",
                    }
            except Exception:
                pass
        return {
            "text": _local_brief(context) + f"\n[Grok error: {str(exc)[:160]}]",
            "source": "error",
            "model": model,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "configured": True,
            "auth_source": auth_source,
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
