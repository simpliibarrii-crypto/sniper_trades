"""
Sniper Trades — J-Space Core API (polished)
Smooth local UX: ORJSON, GZip, session cache, thread-offloaded pipeline, pro UI.
Copy-trade + Crypto.com live market chart feed.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, ORJSONResponse, StreamingResponse

from config import get_settings
from schemas import (
    BookOut,
    CandlesOut,
    CopySignalIn,
    FollowerCreate,
    HealthOut,
    LeaderCreate,
    ReadyOut,
    ResearchQuery,
    ResearchResponse,
    SessionListOut,
    SignalCreate,
    TickerOut,
)
from agents.raven_trader import LIVE_SNIPER_TRADER_PROMPT, TRADER_SYSTEM_PROMPT, raven_analyze
from services import finance_news, free_market, pipeline
from services.copy_trade import get_engine
from services.raven_market_pack import build_market_pack
from services.trade_intel import parse_trade_intent

_UI = Path(__file__).resolve().parent / "ui" / "index.html"
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline.warm()
    get_engine()  # load copy-trade ledger early
    yield
    pipeline.shutdown()


app = FastAPI(
    title=_settings.app_name,
    version=_settings.version,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=400)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_header(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.2f}"
    response.headers["X-Request-Id"] = request.headers.get(
        "x-request-id", uuid.uuid4().hex[:12]
    )
    response.headers["X-App-Version"] = _settings.version
    return response


@app.get("/health", response_model=HealthOut)
async def health():
    return HealthOut(
        ok=True,
        service="sniper-trades-jspace",
        version=_settings.version,
    )


@app.get("/trader/prompt")
async def trader_prompt():
    """Sniper Trades live system prompt — ready to copy into any AI agent."""
    return {
        "name": "Sniper Trades",
        "version": _settings.version,
        "prompt": LIVE_SNIPER_TRADER_PROMPT or TRADER_SYSTEM_PROMPT,
        "format": [
            "1. jspace (Live Internal Thoughts)",
            "2. Active TradingView Analysis",
            "3. Current Strategy Position",
            "4. Live Sniper Verdict",
        ],
    }


@app.get("/news")
async def news_list(
    limit: int = Query(24, ge=1, le=80),
    category: Optional[str] = Query(None, max_length=32),
):
    """Free finance / crypto news (RSS aggregate)."""
    try:
        return await asyncio.to_thread(finance_news.get_news, limit, category, False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/news/sources")
async def news_sources():
    return finance_news.list_news_sources()


@app.get("/sniper/live")
async def sniper_live(
    symbol: str = Query("BTC_USDT", max_length=32),
    timeframe: str = Query("1m", max_length=8),
    interval_ms: int = Query(8000, ge=3000, le=60_000),
    query: str = Query("live sniper", max_length=200),
):
    """
    SSE live sniper stream: full strategy analysis on each tick using free 1m+ feeds.
    Events: hello, sniper (full analysis), error.
    """

    async def gen() -> AsyncIterator[str]:
        intent_base = parse_trade_intent(f"{query} {symbol} {timeframe}")
        intent_base["primary_symbol"] = (
            symbol.split("_")[0].upper() if "_" in symbol else symbol.upper()
        )
        intent_base["timeframe"] = timeframe
        yield (
            "event: hello\n"
            f"data: {json.dumps({'trader': 'Sniper Trades', 'symbol': symbol, 'timeframe': timeframe})}\n\n"
        )
        prior: Optional[dict] = None
        while True:
            try:
                market = await asyncio.to_thread(
                    build_market_pack, symbol, timeframe
                )
                raven = await asyncio.to_thread(
                    raven_analyze, intent_base, market, [], prior
                )
                sp = raven.get("strategy_position") or {}
                if sp.get("side") in ("Long", "Short"):
                    prior = {
                        "side": sp["side"],
                        "stop_loss": sp.get("stop_loss"),
                        "take_profit_1": sp.get("take_profit_1"),
                    }
                # chart candles for live chart moves
                candles_pack = (market.get("timeframes") or {}).get(timeframe) or {}
                payload = {
                    "trader": "Sniper Trades",
                    "instrument": market.get("instrument"),
                    "data_sources": market.get("data_sources"),
                    "ticker": market.get("ticker"),
                    "timeframe": timeframe,
                    "candles": (candles_pack.get("candles") or [])[-120:],
                    "jspace": raven.get("jspace"),
                    "tv_analysis": raven.get("tv_analysis"),
                    "strategy_position": raven.get("strategy_position"),
                    "verdict": raven.get("verdict"),
                    "summary": raven.get("summary"),
                    "result_text": raven.get("result_text"),
                    "mtf_analyses": raven.get("analyses"),
                    "ts": time.time(),
                }
                yield f"event: sniper\ndata: {json.dumps(payload)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(interval_ms / 1000.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/live/deck")
async def live_deck(
    symbol: str = Query("BTC_USDT", max_length=32),
    timeframe: str = Query("1m", max_length=8),
    interval_ms: int = Query(5000, ge=2000, le=60_000),
    query: str = Query("live sniper", max_length=200),
    news_every: int = Query(3, ge=1, le=20),
):
    """
    Unified live deck SSE:
      hello | ticker | candles | sniper (jspace) | news | error
    Chart moves + AI jspace + finance news in one stream.
    """

    async def gen() -> AsyncIterator[str]:
        inst = free_market.normalize_instrument(symbol)
        intent_base = parse_trade_intent(f"{query} {symbol} {timeframe}")
        intent_base["primary_symbol"] = (
            inst.split("_")[0].upper() if "_" in inst else inst.upper()
        )
        intent_base["timeframe"] = timeframe
        yield (
            "event: hello\n"
            f"data: {json.dumps({'deck': True, 'instrument': inst, 'timeframe': timeframe})}\n\n"
        )
        prior: Optional[dict] = None
        tick = 0
        while True:
            tick += 1
            try:
                # Fast path: ticker every tick for chart price pulse
                ticker = await asyncio.to_thread(free_market.get_ticker, inst)
                yield f"event: ticker\ndata: {json.dumps(ticker)}\n\n"

                # Full pack + sniper every tick (chart + jspace)
                market = await asyncio.to_thread(build_market_pack, inst, timeframe)
                pack = (market.get("timeframes") or {}).get(timeframe) or {}
                candles = (pack.get("candles") or [])[-120:]
                yield f"event: candles\ndata: {json.dumps({'instrument': inst, 'timeframe': timeframe, 'source': pack.get('source'), 'candles': candles})}\n\n"

                raven = await asyncio.to_thread(
                    raven_analyze, intent_base, market, [], prior
                )
                sp = raven.get("strategy_position") or {}
                if sp.get("side") in ("Long", "Short"):
                    prior = {
                        "side": sp["side"],
                        "stop_loss": sp.get("stop_loss"),
                        "take_profit_1": sp.get("take_profit_1"),
                    }
                sniper_payload = {
                    "trader": "Sniper Trades",
                    "instrument": market.get("instrument"),
                    "data_sources": market.get("data_sources"),
                    "ticker": market.get("ticker") or ticker,
                    "timeframe": timeframe,
                    "candles": candles,
                    "jspace": raven.get("jspace"),
                    "tv_analysis": raven.get("tv_analysis"),
                    "strategy_position": raven.get("strategy_position"),
                    "verdict": raven.get("verdict"),
                    "summary": raven.get("summary"),
                    "result_text": raven.get("result_text"),
                    "mtf_analyses": raven.get("analyses"),
                    "ts": time.time(),
                }
                yield f"event: sniper\ndata: {json.dumps(sniper_payload)}\n\n"

                # News on first tick and every N ticks
                if tick == 1 or tick % news_every == 0:
                    news = await asyncio.to_thread(
                        finance_news.get_news, 20, None, tick == 1
                    )
                    yield f"event: news\ndata: {json.dumps(news)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(interval_ms / 1000.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/ready", response_model=ReadyOut)
async def ready():
    return ReadyOut(
        ready=True,
        sessions=pipeline.session_count(),
        version=_settings.version,
    )


@app.get("/", response_class=HTMLResponse)
async def ui_home():
    if _UI.is_file():
        return FileResponse(_UI, media_type="text/html; charset=utf-8")
    return HTMLResponse("<h1>Sniper Trades</h1><p>UI missing — API still up.</p>")


@app.get("/sessions", response_model=SessionListOut)
async def sessions():
    items = pipeline.list_sessions()
    return SessionListOut(sessions=items, count=len(items))


@app.delete("/sessions/{session_id}")
async def drop_session(session_id: str):
    return {
        "dropped": pipeline.drop_session(session_id),
        "session_id": session_id,
    }


@app.post("/research/search", response_model=ResearchResponse)
async def run_generative_search(payload: ResearchQuery):
    try:
        result = await asyncio.to_thread(
            pipeline.run_research,
            payload.query.strip(),
            payload.session_id.strip() or "default_session",
            payload.reuse_session,
            payload.include_counterfactual,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Free public market data (Binance / Kraken / Coinbase / CDC) — 1m capable
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/market/sources")
async def market_sources():
    """List free online feeds and probe connectivity."""
    info = free_market.list_sources()
    try:
        info["probe"] = await asyncio.to_thread(free_market.probe)
    except Exception as exc:  # noqa: BLE001
        info["probe_error"] = str(exc)
    return info


@app.get("/market/ticker", response_model=TickerOut)
async def market_ticker(symbol: str = Query("BTC_USDT", max_length=32)):
    try:
        data = await asyncio.to_thread(free_market.get_ticker, symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data


@app.get("/market/candles", response_model=CandlesOut)
async def market_candles(
    symbol: str = Query("BTC_USDT", max_length=32),
    timeframe: str = Query("1m", max_length=8),
    count: int = Query(120, ge=5, le=1000),
):
    try:
        data = await asyncio.to_thread(
            free_market.get_candles, symbol, timeframe, count
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data


@app.get("/market/book", response_model=BookOut)
async def market_book(
    symbol: str = Query("BTC_USDT", max_length=32),
    depth: int = Query(10, ge=1, le=50),
):
    try:
        data = await asyncio.to_thread(free_market.get_book, symbol, depth)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data


@app.get("/market/stream")
async def market_stream(
    symbol: str = Query("BTC_USDT", max_length=32),
    interval_ms: int = Query(2000, ge=500, le=30_000),
):
    """SSE live ticker feed from free public APIs (polled, default Binance)."""

    async def gen() -> AsyncIterator[str]:
        inst = free_market.normalize_instrument(symbol)
        yield f"event: hello\ndata: {json.dumps({'instrument': inst, 'feed': 'free_public'})}\n\n"
        while True:
            try:
                tick = await asyncio.to_thread(free_market.get_ticker, inst)
                payload = json.dumps(tick)
                yield f"event: ticker\ndata: {payload}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(interval_ms / 1000.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Copy trade
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/copy/state")
async def copy_state():
    return await asyncio.to_thread(get_engine().list_state)


@app.post("/copy/leaders")
async def copy_register_leader(payload: LeaderCreate):
    try:
        leader = await asyncio.to_thread(
            get_engine().register_leader, payload.name, payload.symbols
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from dataclasses import asdict

    return asdict(leader)


@app.post("/copy/followers")
async def copy_register_follower(payload: FollowerCreate):
    try:
        follower = await asyncio.to_thread(
            get_engine().register_follower,
            payload.name,
            payload.leader_id,
            payload.size_multiplier,
            payload.max_notional_usd,
            payload.mode,
            payload.starting_cash,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from dataclasses import asdict

    return asdict(follower)


@app.post("/copy/signals")
async def copy_emit_signal(payload: SignalCreate):
    try:
        result = await asyncio.to_thread(
            get_engine().emit_signal,
            payload.leader_id,
            payload.instrument,
            payload.side,
            payload.order_type,
            payload.quantity,
            payload.notional_usd,
            payload.price,
            payload.note,
            payload.auto_copy,
            payload.confirm_live,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.post("/copy/signals/{signal_id}/copy")
async def copy_signal(signal_id: str, payload: Optional[CopySignalIn] = None):
    confirm = payload.confirm_live if payload else False
    try:
        fills = await asyncio.to_thread(
            get_engine().copy_signal, signal_id, confirm
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"signal_id": signal_id, "fills": fills}


if __name__ == "__main__":
    import uvicorn

    try:
        import uvloop  # noqa: F401

        loop = "uvloop"
    except ImportError:
        loop = "asyncio"
    try:
        import httptools  # noqa: F401

        http = "httptools"
    except ImportError:
        http = "auto"

    print(f"Launching {_settings.app_name} v{_settings.version} (smooth profile)...")
    uvicorn.run(
        "main:app",
        host=_settings.host,
        port=_settings.port,
        reload=False,
        loop=loop,
        http=http,
        log_level="info",
        access_log=_settings.access_log,
        timeout_keep_alive=30,
        workers=1,
    )
