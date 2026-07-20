# Sniper Trades · J-Space

**RavenTrade Core / Sniper Trades** — live crypto intelligence console for **phone + desktop**.

Multi-TF free market data (1m+), Raven J-Space, Grok live co-pilot (xAI when keyed), risk calculator, paper copy-trade, DEX discovery, finance news, price alerts, and PWA install shell.

### Mobile + desktop

- Responsive flight deck with bottom tab bar on phones (`viewport-fit=cover`, safe-area)
- Installable PWA: `/manifest.webmanifest` + `/sw.js`
- Server binds `0.0.0.0:8000` by default — open `http://<your-lan-ip>:8000` from a phone

### Grok live updates

```bash
export XAI_API_KEY=...          # https://console.x.ai
# optional: export SNIPER_XAI_MODEL=grok-4-1-fast-non-reasoning
./scripts/run.sh
```

- `GET /grok/status` — configured?
- `POST /grok/comment` — one-shot brief
- Live deck SSE event `grok` every N ticks (local fallback if no key)

### Features people asked for (shipped)

| Ask | Where |
|-----|--------|
| Live prices / 1m charts | Free market stack + Live Deck |
| AI explanation | J-Space + Grok co-pilot |
| Risk / position size | `/risk/calculate` + UI |
| Paper trading | Copy-trade ledger |
| Alerts | Local + server `/alerts` |
| Watchlist | `/market/radar` |
| News | Free RSS mesh |
| Portfolio view | `/portfolio/paper` |
| Connections health | `/integrations?probe=true` |

## v7.0 private Raven terminal + local bridge

- Owner-only Sites terminal with synchronized market, worldwide news, DEX discovery, auditable tool contributions, and exact plan markers
- Manifest V3 Chrome extension in `extension/` for a read-only localhost signal badge and evidence snapshot
- `GET /extension/snapshot` returns one bounded analysis payload without private model internals or order permissions
- `GET /mcp/catalog` separates native MCPs from ordinary public APIs and keeps wallet-capable MCPs disabled by default
- Kraken CLI native MCP is catalogued for market, account-read-only, and paper services; it is never installed or started automatically
- Phantom MCP is catalogued as wallet-capable and requires separate manual configuration and human signing
- Live copy orders now require all three gates: typed `CONFIRM LIVE`, `SNIPER_LIVE_TRADING_ENABLED=true`, and the `X-Sniper-Control-Token` header

### Chrome extension

Start the server, open `chrome://extensions`, enable Developer mode, then load
`extension/` as an unpacked extension. The bridge only has `localhost` and
`127.0.0.1` host access; it has no wallet, exchange, arbitrary-site, or order
permission. See `extension/README.md` for the exact flow.

## Raven console foundation

- Unique purple-and-white cyber-noir dashboard with a responsive Raven analysis flow
- Live order book, multi-timeframe tool telemetry, and projected volume-pressure logic
- Liquid-coin watchlist plus new Solana token-profile and DEX-pair discovery
- Injected Phantom connection for user-approved, read-only public-wallet balances
- Guarded Trade Now ticket that hands swaps to Jupiter for quote review and wallet signature
- Deterministic loss-at-stop risk calculator with a hard 2% defense ceiling
- Paper-first copy trading; real submission requires the exact phrase `CONFIRM LIVE`
- Security response headers and an honest auth status (2FA/biometric auth is not yet implemented)
- J-Space exposes concise evidence, confirmation-bias checks, and counter-cases—not private chain-of-thought
- A centralized Connections Center showing actual runtime providers, live health, setup steps, privacy scope, and the safest next action

The app never requests private keys or seed phrases. DEX discovery is unvetted
research data, and opening Jupiter does not mean a transaction was submitted.

### Live Sniper response format

1. **Cognitive Core Status** — evidence, confirmation bias, counter-case
2. **Market Horizon Scan** — price, volatility, and multi-timeframe structure
3. **Shadow Network Intel** — crypto news, DEX discovery, and supported on-chain context
4. **Tool Talons Deployed** — live indicator and projected volume-pressure readings
5. **Decision Forge** — entry / SL / TP / size / invalidation / worst case
6. **Wallet Nest Status** — connected public data or explicitly disconnected
7. **Next Flight Path** — Hold / Long / Short / Exit + guarded next action

System prompt: `GET /trader/prompt` → `LIVE_SNIPER_TRADER_PROMPT`  
Live sniper: `GET /sniper/live?symbol=BTC_USDT&timeframe=1m`  
**Live deck** (jspace + chart + news): `GET /live/deck?symbol=BTC_USDT&timeframe=1m`  
News: `GET /news?limit=24` (free RSS — CoinDesk, Cointelegraph, Yahoo, Fed, BBC, CNBC)

Connections: `GET /integrations?probe=true` (safe catalog + live provider health)

## Quick start

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
| GET | `/` | Console UI (Flight Deck · Deep Scan · Paper/Exchange · Connections) |
| GET | `/health` | Liveness |
| GET | `/ready` | Readiness + session count |
| GET | `/integrations` | Runtime connection catalog; add `?probe=true` for live health |
| GET | `/mcp/catalog` | Opt-in MCP catalog and direct-feed boundaries |
| GET | `/extension/snapshot` | Read-only Chrome bridge snapshot; no order capability |
| POST | `/research/search` | Run J-Space research |
| GET | `/sessions` | List warm sessions |
| DELETE | `/sessions/{id}` | Drop a session |
| GET | `/market/sources` | Free feeds list + connectivity probe |
| GET | `/market/ticker` | Free ticker — Binance→Kraken→Coinbase→CDC |
| GET | `/market/candles` | OHLCV from **1m** up (`?timeframe=1m&count=180`) |
| GET | `/market/book` | Order book depth |
| GET | `/market/stream` | SSE live ticker feed |
| GET | `/market/radar` | Parallel liquid-crypto watchlist snapshot |
| GET | `/market/discovery` | Latest active Solana token profiles from DEX Screener |
| GET | `/market/dex/search` | Search DEX pairs by token, symbol, or public address |
| GET | `/wallet/solana/{address}` | Read-only SOL/SPL snapshot for an approved public address |
| POST | `/risk/calculate` | Loss-at-stop position sizing; never places an order |
| GET | `/copy/state` | Copy-trade ledger snapshot |
| POST | `/copy/leaders` | Register a leader |
| POST | `/copy/followers` | Register a follower (paper default) |
| POST | `/copy/signals` | Emit signal + auto-copy to followers |
| POST | `/copy/signals/{id}/copy` | Re-copy an existing signal |

### Free market data (no API key)

| Source | Min TF | Notes |
|--------|--------|--------|
| **Binance** public | 1m | Primary — klines up to 1000 bars |
| **Kraken** public | 1m | OHLC fallback |
| **Coinbase** Exchange | 1m | USDT pairs mapped to USD |
| Crypto.com public / cdcx | 1m | Last-resort fallback |

RavenTrader multi-TF stack: `1m, 5m, 15m, 1h, 4h, 1D`.

### Connection organization

| Layer | Runtime providers | Safety boundary |
|---|---|---|
| Market data | Binance → Kraken → Coinbase → Crypto.com public | Public requests; automatic fallback |
| Intelligence | Crypto/market/macro RSS + DEX Screener | Read-only research; new tokens remain unvetted |
| Analysis | Raven indicators, projected volume, risk engine | Local compute; forecasts are heuristic |
| Wallet | Phantom + Solana RPC | Public address only after explicit approval |
| DEX action | Jupiter handoff | Quote review and separate wallet signature required |
| Exchange action | Paper ledger + optional cdcx | Paper default; dry-run and exact live confirmation gates |

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
- **Live** mode uses `cdcx trade order --dry-run` unless all live locks are satisfied: `confirm_live=true`, `confirmation_text="CONFIRM LIVE"`, `SNIPER_LIVE_TRADING_ENABLED=true`, and the correct `X-Sniper-Control-Token`
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
