# Sniper Trades Security and Reliability Foundation

**Date:** 2026-07-20  
**Status:** Approved design  
**Repository:** `simpliibarrii-crypto/sniper_trades`  
**Target branch for implementation:** `feature/security-foundation`

## 1. Purpose

Sniper Trades currently combines public market intelligence, local AI analysis, alerts, paper copy-trading, wallet inspection, and guarded live-order submission in one FastAPI service. The application is useful on a phone over a local network, but its present defaults expose the service on every network interface, allow wildcard CORS, and protect live submission with a confirmation phrase rather than an authenticated operator identity.

This design establishes a secure control plane before additional wallet, DEX, or exchange capabilities are added. It preserves the fast local experience while making network exposure, state changes, and live execution explicit, authenticated, auditable, and testable.

## 2. Goals

1. Bind to localhost by default and require an explicit LAN mode for phone access.
2. Require authenticated operator sessions for every state-changing endpoint.
3. Separate read-only intelligence, paper-trading authority, and live-execution authority.
4. Replace phrase-only live confirmation with a short-lived, single-use challenge bound to one operator session and one action.
5. Prevent cross-site request forgery, brute-force authentication, challenge replay, and uncontrolled SSE connection growth.
6. Record security-sensitive actions in an append-only, tamper-evident audit log without storing secrets.
7. Return stable public error responses while preserving diagnostic detail in local logs.
8. Add automated security, API, and regression checks in GitHub Actions.
9. Keep the implementation lean enough for the repository's local-first and low-resource profile.

## 3. Non-goals

- Multi-user accounts, cloud identity providers, OAuth, or social login.
- Custody of private keys, seed phrases, or wallet signing material.
- Automatic live trading without a separate user-approved action.
- Internet-facing production hosting.
- Full replacement of inline UI scripts or removal of every permissive CSP directive in this phase.
- Regulatory certification, brokerage compliance, or claims of financial safety.
- Durable distributed sessions across multiple server workers.

## 4. Operating modes

### 4.1 Local mode

Local mode is the default.

- Bind address: `127.0.0.1`.
- Default allowed origins: `http://127.0.0.1:8000` and `http://localhost:8000`.
- Read-only endpoints remain available without an operator session.
- State-changing endpoints remain disabled until an operator secret is configured and a session is authenticated.
- The server must not silently widen its bind address or CORS policy.

### 4.2 LAN mode

LAN mode is an explicit opt-in for phone access on the same trusted network.

- Enabled with `SNIPER_BIND_MODE=lan`.
- Bind address becomes `0.0.0.0` unless an explicit host is supplied.
- Startup must fail closed unless `SNIPER_OPERATOR_SECRET` is set to a sufficiently strong value.
- Startup must fail closed when CORS origins are wildcarded or omitted.
- The operator must list exact origins, including the host or LAN IP used by the phone.
- Documentation must state that LAN mode is not intended for port forwarding or public internet exposure.

### 4.3 Read-only mode

When no operator secret is configured, the application starts in read-only mode.

- Public market, news, integration-status, health, and deterministic analysis endpoints continue to function.
- Every state-changing endpoint returns `503 operator_auth_not_configured`.
- The UI displays a visible read-only status rather than presenting controls that appear operational.

## 5. Trust and permission model

The system has three authority levels.

### 5.1 Public reader

May access read-only market data, news, health, readiness, integration status, DEX discovery, public-wallet snapshots, static UI assets, and non-mutating analysis.

### 5.2 Authenticated operator

May create and delete alerts, manage warm research sessions, register paper leaders and followers, emit paper signals, copy paper signals, and inspect security status.

### 5.3 Live-action authorization

Live execution is not a persistent role. It is a one-action capability created by a short-lived challenge. An authenticated operator must request a challenge and complete it for a specific action. The resulting authorization is valid only once, only for the bound session, only for the bound action digest, and only before expiry.

This prevents a stolen session or replayed request from turning an old confirmation into a new trade.

## 6. Authentication design

### 6.1 Operator secret

- The operator secret is supplied through `SNIPER_OPERATOR_SECRET` or an equivalent protected local environment source.
- It is never committed, returned by an API, written to the audit log, or embedded in the frontend.
- Comparison uses `secrets.compare_digest` or an equivalent constant-time operation.
- A minimum strength rule is enforced at startup for LAN mode.

### 6.2 Session creation

`POST /auth/session`

- Accepts the operator secret over JSON.
- Applies a strict authentication-attempt rate limit before comparison.
- On success, creates a cryptographically random opaque session token.
- Stores only a hash of the token in the in-memory session registry.
- Returns an `HttpOnly`, `SameSite=Strict` session cookie for the browser UI.
- Returns a CSRF token in the response body for browser requests.
- API clients may use the opaque token as a Bearer token instead of a cookie.
- Authentication responses use `Cache-Control: no-store`.

### 6.3 Session lifecycle

- Absolute lifetime: 30 minutes by default.
- Idle timeout: 15 minutes by default.
- Maximum concurrent sessions: 8 by default.
- Session deletion immediately invalidates the token.
- Server restart invalidates all sessions.
- Session records contain creation time, last-seen time, a random session identifier, and a privacy-preserving client fingerprint hash. They do not store secrets.

Endpoints:

- `GET /auth/status`
- `POST /auth/session`
- `DELETE /auth/session`

### 6.4 CSRF protection

Cookie-authenticated state-changing requests must send the session's CSRF token in `X-CSRF-Token`.

- The server compares the header token to the session record using constant-time comparison.
- Bearer-authenticated API requests do not require CSRF because browsers do not attach the authorization header automatically.
- Origin validation remains enabled as an additional browser boundary.

## 7. Live-action challenge design

### 7.1 Challenge creation

`POST /auth/live-challenges`

The authenticated operator submits a normalized action description such as:

- operation type
- signal identifier or leader identifier
- instrument
- side
- quantity or notional
- order type
- destination adapter

The server canonicalizes the action and calculates an action digest. It then returns:

- challenge ID
- short random code
- exact confirmation phrase, for example `CONFIRM LIVE R7K4Q2`
- expiration timestamp
- human-readable action summary

### 7.2 Challenge verification

A live submission request includes the challenge ID and exact confirmation phrase.

The server verifies that the challenge:

- exists and is unexpired
- belongs to the authenticated session
- has not been consumed
- matches the canonical action digest
- matches the confirmation phrase using constant-time comparison

The server marks the challenge consumed before invoking the exchange adapter. Consumption is atomic inside the single-process challenge store. A failed downstream order does not make the challenge reusable.

### 7.3 Compatibility

The current `confirm_live` field may remain temporarily for UI compatibility, but the fixed phrase `CONFIRM LIVE` is no longer sufficient. Live requests without a valid challenge return `403 live_challenge_required`.

## 8. Endpoint policy

### 8.1 Public read-only endpoints

Examples include:

- `/health`
- `/ready`
- `/trader/prompt`
- `/news`
- `/news/sources`
- `/integrations`
- `/grok/status`
- `/market/*` read routes
- `/wallet/solana/{address}`
- `/sniper/live`
- `/live/deck`
- `/research/search` when configured as non-persistent analysis

### 8.2 Operator-protected endpoints

- `POST /alerts`
- `DELETE /alerts/{alert_id}`
- `DELETE /sessions/{session_id}`
- `POST /copy/leaders`
- `POST /copy/followers`
- `POST /copy/signals`
- `POST /copy/signals/{signal_id}/copy`
- live-challenge creation and session deletion

If an analysis route mutates durable or cross-request state, it must be classified as operator-protected during implementation rather than left public by convenience.

### 8.3 Live-challenge-protected actions

Any path capable of invoking a real exchange or transaction adapter requires both an authenticated operator session and a valid one-time live challenge.

## 9. Rate limiting and connection controls

A lean in-memory limiter is sufficient for this local single-process service.

- Authentication attempts: 5 attempts per 10 minutes per privacy-preserving client key.
- State-changing requests: 60 requests per minute per session.
- Live-challenge creation: 5 challenges per minute per session.
- Live submissions: 3 attempts per minute per session.
- SSE streams: maximum 3 concurrent streams per client key and 12 total by default.

Rate-limit responses return `429 rate_limited` with `Retry-After` where applicable. Limiter state is intentionally ephemeral and resets on restart.

## 10. Security audit log

Security-sensitive actions are written to a JSONL log under the existing local application data directory.

Each record includes:

- UTC timestamp
- request ID
- event type
- outcome
- session identifier hash or anonymous marker
- action/resource summary
- action digest when applicable
- previous record hash
- current record hash

The log never includes:

- operator secrets
- raw session or CSRF tokens
- confirmation phrases
- API keys
- wallet seed phrases or private keys
- full request bodies when they may contain sensitive values

Events include authentication success and failure, logout, rate-limit denial, protected-route denial, challenge creation, challenge consumption, paper action, live-action attempt, and audit verification failure.

A small verification function confirms the hash chain during startup and through a protected diagnostic endpoint. Chain failure places live execution into a fail-closed state until the operator resolves or rotates the audit file.

## 11. Error handling and observability

### 11.1 Public errors

Security and state-changing endpoints return stable machine-readable errors:

```json
{
  "error": {
    "code": "live_challenge_required",
    "message": "A valid one-time live authorization is required.",
    "request_id": "..."
  }
}
```

Raw exception messages, provider payloads, filesystem paths, and secrets must not be returned to clients.

### 11.2 Local diagnostics

Server logs retain structured diagnostic context with request IDs. Sensitive fields are redacted before logging. Existing `X-Request-Id` support remains and becomes the correlation key for audit and application logs.

### 11.3 Safe failure behavior

- Authentication subsystem unavailable: deny state changes.
- Audit integrity failure: allow read-only use, deny live actions.
- Challenge store failure: deny live actions.
- Exchange adapter failure: consume the challenge, record failure, return a stable error.
- Rate limiter failure: deny authentication and live execution; read-only market access may remain available.

## 12. Configuration changes

The settings model gains explicit fields such as:

- `bind_mode: Literal["local", "lan"] = "local"`
- `host: str = "127.0.0.1"`
- `cors_origins: str` with local-only defaults
- `operator_secret: SecretStr | None`
- `session_ttl_seconds`
- `session_idle_seconds`
- `max_operator_sessions`
- `max_sse_per_client`
- `max_sse_total`
- `audit_log_path`

Configuration validation must reject unsafe combinations, especially LAN mode with a weak or missing secret and wildcard CORS.

## 13. Component boundaries

### `services/security.py`

Owns operator-secret validation, session issuance, token hashing, session expiry, CSRF validation, and authentication dependencies.

### `services/live_authorization.py`

Owns canonical action digests, challenge creation, expiry, single-use consumption, and challenge verification.

### `services/rate_limit.py`

Owns in-memory windows or token buckets and exposes narrow named limit checks.

### `services/security_audit.py`

Owns redaction, hash-chained JSONL writes, integrity verification, and event schemas.

### `services/stream_limits.py`

Owns bounded SSE leases and guarantees release when a stream exits or is cancelled.

### `security_dependencies.py`

Defines reusable FastAPI dependencies for optional session lookup, required operator access, CSRF checks, and live-action authorization.

The route layer in `main.py` should call these interfaces rather than implementing security logic inline.

## 14. UI behavior

The existing single-page interface gains a compact operator-security panel.

- Displays `READ ONLY`, `OPERATOR`, or `LIVE CHALLENGE ACTIVE` status.
- Prompts for the operator secret without persisting it.
- Keeps the session in an HttpOnly cookie.
- Stores only the CSRF token in memory, not local storage.
- Hides or disables state-changing controls while unauthenticated.
- Shows a review card containing the exact live action before requesting a challenge.
- Requires the challenge-specific phrase before live submission.
- Clears challenge state immediately after an attempt or expiry.
- Never asks for wallet seed phrases or private keys.

## 15. Testing strategy

### 15.1 Unit tests

- session token generation and hashed storage
- absolute and idle expiry
- constant-time validation paths
- CSRF verification
- action canonicalization and digest stability
- challenge expiry, mismatch, and single-use behavior
- limiter windows
- audit-chain verification and redaction
- SSE lease acquisition and release

### 15.2 API tests

Using FastAPI's test client:

- local default host and safe CORS configuration
- LAN startup rejection without a strong secret
- state-changing route denial without authentication
- successful login and logout
- cookie plus CSRF success and failure
- Bearer-token success
- brute-force rate limiting
- paper action allowed with operator authority
- live action denied with the legacy fixed phrase alone
- live action allowed with a matching challenge
- replayed challenge denied
- challenge from another session denied
- altered order payload denied because its digest no longer matches
- audit failure places live paths into fail-closed mode
- SSE limits deny excess connections and release leases after disconnect
- public read-only endpoints remain available

### 15.3 Regression tests

Existing risk, alert, integration, Grok, volume, market, and core tests must continue passing. New security tests must avoid external network calls.

## 16. Continuous integration

Add a GitHub Actions workflow that runs on pull requests and pushes to the protected branches.

Required checks:

1. Install the lean core and development dependencies.
2. Run `pytest -q`.
3. Run `ruff check` and formatting verification.
4. Run `bandit` against application Python files with documented exclusions only.
5. Run `pip-audit` against the dependency set.
6. Run a secret scan using a maintained GitHub Action.

A failure in any required check blocks merge. Network-dependent integration tests remain separate and non-blocking until deterministic fixtures are available.

## 17. Documentation changes

Update the README and `.env.example` to include:

- safe localhost quick start
- explicit LAN setup
- strong-secret requirements
- exact-origin CORS examples
- authentication flow for browser and API clients
- paper versus live authorization boundaries
- audit-log location and verification behavior
- warning against port forwarding or public deployment
- migration note explaining that the fixed `CONFIRM LIVE` phrase is deprecated

## 18. Migration and rollout

### Phase 1: secure defaults

- local bind default
- validated CORS configuration
- read-only fallback without an operator secret
- startup validation for LAN mode

### Phase 2: operator sessions

- authentication endpoints
- cookie, Bearer, and CSRF support
- protect all state-changing routes
- update UI state

### Phase 3: live authorization and audit

- one-time action-bound challenges
- challenge-protected live execution
- hash-chained audit events
- fail-closed live behavior

### Phase 4: abuse controls and CI

- rate limits
- SSE connection bounds
- stable error envelope
- security test suite and GitHub Actions
- documentation migration

Each phase must leave the application in a usable, safer state and must not temporarily expose live execution through a weaker compatibility path.

## 19. Acceptance criteria

The design is complete when all of the following are true:

- A default launch is reachable only from the local device.
- LAN mode cannot start with wildcard CORS or without a strong operator secret.
- Every state-changing endpoint rejects anonymous requests.
- Cookie-authenticated writes reject missing or incorrect CSRF tokens.
- The old fixed phrase cannot authorize live execution.
- A live challenge cannot be replayed, transferred between sessions, or used with a modified action.
- Audit records exclude secrets and verify as an intact hash chain.
- Live execution fails closed when authorization or audit integrity is unavailable.
- SSE connections are bounded and released correctly.
- Existing functionality and tests remain intact.
- New security tests and CI checks pass.
- README instructions describe the actual implemented behavior without overstating production readiness.

## 20. Future work

After this foundation is implemented and verified, later designs may address TLS for LAN use, device pairing, hardware-backed passkeys, signed mobile companion requests, persistent encrypted sessions, stricter CSP without inline scripts, exchange-specific scopes, and shared security-event contracts across Raven AI, Home for AI, Hermes Edge, OpenClinical AI, and JSpace Chain.
