# Sniper Trades · J-Space

Local sovereign reasoning workspace powered by **Sniper Trades** live strategy: multi-TF free market data (1m+), explicit J-Space graph, TradingView-style tool stack, copy-trade, live chart, and provenance.

### Live Sniper response format

1. **jspace (Live Internal Thoughts)** — real-time reasoning, sniper opportunity, bias + counter  
2. **Active TradingView Analysis** — every tool reading, levels, confluences, tool flips  
3. **Current Strategy Position** — entry / SL / TP / size / next action  
4. **Live Sniper Verdict** — Hold / Long / Short / Exit + conviction %  

System prompt: `GET /trader/prompt` → `LIVE_SNIPER_TRADER_PROMPT`  
Live sniper: `GET /sniper/live?symbol=BTC_USDT&timeframe=1m`  
**Live deck** (jspace + chart + news): `GET /live/deck?symbol=BTC_USDT&timeframe=1m`  
News: `GET /news?limit=24` (free RSS — CoinDesk, Cointelegraph, Yahoo, Fed, BBC, CNBC)## Quick start

```bash
cd sniper_trades
source .venv/bin/activate
pip install -r requirements-core.txt
./scripts/run.sh
```

Open **http://127.0.0.1:8000**

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Console UI (Research · Live Chart · Copy Trade) |
| GET | `/health` | Liveness |
| GET | `/ready` | Readiness + session count |
| POST | `/research/search` | Run J-Space research |
| GET | `/sessions` | List warm sessions |
| DELETE | `/sessions/{id}` | Drop a session |
| GET | `/market/sources` | Free feeds list + connectivity probe |
| GET | `/market/ticker` | Free ticker — Binance→Kraken→Coinbase→CDC |
| GET | `/market/candles` | OHLCV from **1m** up (`?timeframe=1m&count=180`) |
| GET | `/market/book` | Order book depth |
| GET | `/market/stream` | SSE live ticker feed |

### Free market data (no API key)

| Source | Min TF | Notes |
|--------|--------|--------|
| **Binance** public | 1m | Primary — klines up to 1000 bars |
| **Kraken** public | 1m | OHLC fallback |
| **Coinbase** Exchange | 1m | USDT pairs mapped to USD |
| Crypto.com public / cdcx | 1m | Last-resort fallback |

RavenTrader multi-TF stack: `1m, 5m, 15m, 1h, 4h, 1D`.
| GET | `/copy/state` | Copy-trade ledger snapshot |
| POST | `/copy/leaders` | Register a leader |
| POST | `/copy/followers` | Register a follower (paper default) |
| POST | `/copy/signals` | Emit signal + auto-copy to followers |
| POST | `/copy/signals/{id}/copy` | Re-copy an existing signal |

```bash
curl -s http://127.0.0.1:8000/research/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"long BTC 4h accumulation","session_id":"me","reuse_session":true}'

# Live market
curl -s 'http://127.0.0.1:8000/market/ticker?symbol=BTC_USDT'
curl -sN 'http://127.0.0.1:8000/market/stream?symbol=BTC_USDT&interval_ms=2000'

# Copy trade (paper)
curl -s -X POST http://127.0.0.1:8000/copy/leaders \
  -H 'Content-Type: application/json' \
  -d '{"name":"Iron Grid","symbols":["BTC_USDT"]}'
```

### Copy-trade safety

- Followers default to **paper** ledger under `~/.local/share/sniper_trades/copy_trade.json`
- **Live** mode uses `cdcx trade order --dry-run` unless `confirm_live=true`
- No withdraw paths; live still needs funded Exchange keys

## Smoothness profile

- Lean deps (`requirements-core.txt`) — no torch/vllm by default
- `ORJSON` + GZip + `uvloop`/`httptools`
- Sync pipeline in `asyncio.to_thread` (non-blocking)
- LRU session cache + orchestrator warmup
- Bounded J-Space history

## Tests

```bash
python tests/test_core.py
```

## Optional heavy deps

```bash
pip install -r requirements-heavy.txt
```
