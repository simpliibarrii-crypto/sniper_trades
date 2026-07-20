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
    AlertCreate,
    BookOut,
    CandlesOut,
    CopySignalIn,
    FollowerCreate,
    GrokCommentIn,
    HealthOut,
    LeaderCreate,
    ReadyOut,
    ResearchQuery,
    ResearchResponse,
    RiskCalculationIn,
    RiskCalculationOut,
    SessionListOut,
    SignalCreate,
    TickerOut,
)
from agents.raven_trader import LIVE_SNIPER_TRADER_PROMPT, TRADER_SYSTEM_PROMPT, raven_analyze
from services import (
    alerts_store,
    dex_intel,
    finance_news,
    free_market,
    grok_live,
    integrations,
    paper_portfolio,
    pipeline,
    solana_wallet,
)
from services.copy_trade import get_engine
from services.raven_market_pack import build_market_pack
from services.risk import calculate_position_size
from services.trade_intel import parse_trade_intent
import os

_UI = Path(__file__).resolve().parent / "ui" / "index.html"
_UI_DIR = Path(__file__).resolve().parent / "ui"
_settings = get_settings()

# Propagate settings key into env for grok_live helper
if _settings.xai_api_key and not os.environ.get("XAI_API_KEY"):
    os.environ["XAI_API_KEY"] = _settings.xai_api_key
if _settings.xai_model:
    os.environ.setdefault("SNIPER_XAI_MODEL", _settings.xai_model)


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
    allow_credentials=_settings.cors_origin_list != ["*"],
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
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
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
            "1. Cognitive Core Status (evidence, bias, counter-case)",
            "2. Market Horizon Scan",
            "3. Shadow Network Intel",
            "4. Tool Talons Deployed (live indicator readings)",
            "5. Decision Forge (position, invalidation, worst case)",
            "6. Wallet Nest Status",
            "7. Next Flight Path (Live Sniper Verdict)",
        ],
    }


@app.post("/risk/calculate", response_model=RiskCalculationOut)
async def risk_calculate(payload: RiskCalculationIn):
    """Calculate loss-at-stop position size; never places an order."""
    try:
        return calculate_position_size(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.get("/integrations")
async def integration_list(probe: bool = Query(False)):
    """One safe, UI-ready registry for all actual app tools and connections."""
    snap = await asyncio.to_thread(integrations.integration_snapshot, probe)
    # Attach Grok connection status into summary for Connections panel
    g = grok_live.grok_status()
    snap["grok"] = g
    return snap


@app.get("/grok/status")
async def grok_status():
    return grok_live.grok_status()


@app.post("/grok/comment")
async def grok_comment(payload: GrokCommentIn):
    """One-shot Grok live brief for an instrument (API or local fallback)."""
    try:
        market = await asyncio.to_thread(
            build_market_pack, payload.instrument, payload.timeframe
        )
        intent = parse_trade_intent(
            f"{payload.query} {payload.instrument} {payload.timeframe}"
        )
        intent["primary_symbol"] = payload.instrument.split("_")[0].upper()
        intent["timeframe"] = payload.timeframe
        raven = await asyncio.to_thread(raven_analyze, intent, market, [], None)
        news = None
        headlines: list = []
        if payload.include_news:
            news = await asyncio.to_thread(finance_news.get_news, 8, None, False)
            headlines = grok_live.headlines_from_news(news)
        ctx = {
            "instrument": market.get("instrument"),
            "timeframe": payload.timeframe,
            "ticker": market.get("ticker"),
            "verdict": raven.get("verdict"),
            "strategy_position": raven.get("strategy_position"),
            "mtf_analyses": raven.get("analyses"),
            "jspace": raven.get("jspace"),
            "tv_analysis": raven.get("tv_analysis"),
            "news_headlines": headlines,
            "ts": time.time(),
        }
        comment = await asyncio.to_thread(grok_live.generate_live_comment, ctx)
        return {
            "comment": comment,
            "verdict": raven.get("verdict"),
            "summary": raven.get("summary"),
            "instrument": market.get("instrument"),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/alerts")
async def alerts_list():
    return await asyncio.to_thread(alerts_store.list_alerts)


@app.post("/alerts")
async def alerts_create(payload: AlertCreate):
    try:
        row = await asyncio.to_thread(
            alerts_store.add_alert,
            payload.instrument,
            payload.direction,
            payload.target,
            payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row


@app.delete("/alerts/{alert_id}")
async def alerts_delete(alert_id: str):
    ok = await asyncio.to_thread(alerts_store.remove_alert, alert_id)
    return {"deleted": ok, "id": alert_id}


@app.get("/portfolio/paper")
async def portfolio_paper():
    """Mark-to-market paper portfolio from the copy-trade ledger."""
    try:
        return await asyncio.to_thread(paper_portfolio.portfolio_snapshot)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/manifest.webmanifest")
async def web_manifest():
    path = _UI_DIR / "manifest.webmanifest"
    if path.is_file():
        return FileResponse(path, media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="manifest missing")


@app.get("/sw.js")
async def service_worker():
    path = _UI_DIR / "sw.js"
    if path.is_file():
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="sw missing")


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
                    "chart_overlay": raven.get("chart_overlay"),
                    "trade_decision": raven.get("trade_decision"),
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
    grok_every: int = Query(2, ge=1, le=30),
):
    """
    Unified live deck SSE:
      hello | ticker | candles | sniper | grok | news | alerts | portfolio | error
    Chart moves + AI jspace + Grok live brief + finance news.
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
            f"data: {json.dumps({'deck': True, 'instrument': inst, 'timeframe': timeframe, 'grok': grok_live.grok_status()})}\n\n"
        )
        prior: Optional[dict] = None
        tick = 0
        last_news: Optional[dict] = None
        while True:
            tick += 1
            try:
                ticker = await asyncio.to_thread(free_market.get_ticker, inst)
                yield f"event: ticker\ndata: {json.dumps(ticker)}\n\n"

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
                    "chart_overlay": raven.get("chart_overlay"),
                    "trade_decision": raven.get("trade_decision"),
                    "ts": time.time(),
                }
                yield f"event: sniper\ndata: {json.dumps(sniper_payload)}\n\n"

                # Server-side multi-alert evaluation
                px = (market.get("ticker") or ticker or {}).get("last")
                if px is not None:
                    fired = await asyncio.to_thread(
                        alerts_store.evaluate_prices, {inst: float(px)}
                    )
                    if fired:
                        yield f"event: alerts\ndata: {json.dumps({'fired': fired})}\n\n"

                if tick == 1 or tick % news_every == 0:
                    last_news = await asyncio.to_thread(
                        finance_news.get_news, 20, None, tick == 1
                    )
                    yield f"event: news\ndata: {json.dumps(last_news)}\n\n"

                # Grok live commentary (API or local fallback)
                if tick == 1 or tick % grok_every == 0:
                    ctx = {
                        "instrument": market.get("instrument"),
                        "timeframe": timeframe,
                        "ticker": market.get("ticker") or ticker,
                        "verdict": raven.get("verdict"),
                        "strategy_position": raven.get("strategy_position"),
                        "mtf_analyses": raven.get("analyses"),
                        "jspace": raven.get("jspace"),
                        "tv_analysis": raven.get("tv_analysis"),
                        "news_headlines": grok_live.headlines_from_news(last_news),
                        "ts": time.time(),
                    }
                    comment = await asyncio.to_thread(
                        grok_live.generate_live_comment, ctx
                    )
                    yield f"event: grok\ndata: {json.dumps(comment)}\n\n"

                if tick == 1 or tick % 4 == 0:
                    port = await asyncio.to_thread(paper_portfolio.portfolio_snapshot)
                    yield f"event: portfolio\ndata: {json.dumps(port)}\n\n"
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


@app.get("/market/radar")
async def market_radar(
    symbols: str = Query(
        "BTC_USDT,ETH_USDT,SOL_USDT,DOGE_USDT,XRP_USDT,ADA_USDT",
        max_length=240,
    ),
):
    """Parallel crypto-only watchlist snapshot; individual failures stay isolated."""
    requested = [
        free_market.normalize_instrument(item)
        for item in symbols.split(",")
        if item.strip()
    ][:12]
    requested = list(dict.fromkeys(requested))

    async def fetch_one(instrument: str):
        try:
            return await asyncio.to_thread(free_market.get_ticker, instrument)
        except Exception as exc:  # noqa: BLE001
            return {"instrument": instrument, "error": str(exc)[:180]}

    rows = await asyncio.gather(*(fetch_one(item) for item in requested))
    return {
        "scope": "configured liquid-market watchlist; use DEX discovery for new tokens",
        "items": rows,
        "ts": int(time.time() * 1000),
    }


@app.get("/market/discovery")
async def market_discovery(
    chain: str = Query("solana", max_length=24),
    limit: int = Query(12, ge=1, le=24),
):
    """Newest active DEX profiles. Discovery only: tokens are not safety-vetted."""
    if chain.lower() != "solana":
        raise HTTPException(status_code=400, detail="only solana discovery is enabled")
    try:
        return await asyncio.to_thread(dex_intel.latest_solana_tokens, limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/market/dex/search")
async def market_dex_search(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(12, ge=1, le=24),
):
    """Search DEX pairs by symbol, name, or public token address."""
    try:
        return await asyncio.to_thread(dex_intel.search_pairs, q, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/wallet/solana/{address}")
async def wallet_solana(address: str):
    """Read a user-approved public address. No keys, signing, or persistence."""
    try:
        return await asyncio.to_thread(solana_wallet.wallet_snapshot, address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    if payload.confirm_live and payload.confirmation_text != "CONFIRM LIVE":
        raise HTTPException(
            status_code=400,
            detail="Type CONFIRM LIVE to authorize real order submission",
        )
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
    if confirm and (not payload or payload.confirmation_text != "CONFIRM LIVE"):
        raise HTTPException(
            status_code=400,
            detail="Type CONFIRM LIVE to authorize real order submission",
        )
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
