# Sniper Trades Security and Reliability Foundation

**Date:** 2026-07-20  
**Status:** Draft for written-spec review  
**Repository:** `simpliibarrii-crypto/sniper_trades`  
**Target branch for implementation:** `feature/security-foundation`

## 1. Purpose

Sniper Trades currently combines public market intelligence, local AI analysis, paid or local model commentary, alerts, paper copy-trading, wallet inspection, and guarded live-order submission in one FastAPI service. The application is useful on a phone over a local network, but its present defaults expose the service on every network interface, allow wildcard CORS, and protect live submission with a confirmation phrase rather than an authenticated operator identity.

This design establishes a secure control plane before additional wallet, DEX, or exchange capabilities are added. It preserves the fast local experience while making network exposure, private operator state, state changes, paid model use, and live execution explicit, authenticated, auditable, and testable.

## 2. Goals

1. Bind to localhost by default and require an explicit LAN mode for phone access.
2. Require authenticated operator sessions for private state, paid model calls, and every state-changing endpoint.
3. Separate public intelligence, private operator state, paper-trading authority, and live-execution authority.
4. Replace phrase-only live confirmation with a short-lived, single-use challenge bound to one operator session and one action.
5. Reduce cross-site request forgery, brute-force authentication, challenge replay, request tampering, and uncontrolled SSE connection growth.
6. Record security-sensitive actions in a hash-chained integrity log without storing secrets.
7. Return stable public error responses while preserving diagnostic detail in local logs.
8. Add automated security, API, and regression checks in GitHub Actions.
9. Keep the implementation lean enough for the repository's local-first and low-resource profile.

## 3. Threat model and security limits

This phase is designed to protect against:

- accidental exposure caused by permissive bind and CORS defaults
- unauthenticated users on the same network invoking private or mutating routes
- browser-based cross-origin writes
- brute-force operator-secret attempts
- reuse of an old live confirmation for a different or repeated action
- accidental leakage of secrets through errors, logs, or audit records
- simple resource exhaustion through excessive SSE streams or protected-route requests

This phase does not claim to protect against:

- an active attacker who can observe or modify trusted-LAN HTTP traffic
- a compromised browser, device, operating system, or server process
- theft of an authenticated session token
- malicious exchange or upstream provider behavior
- public internet deployment
- cryptographically authenticated audit history stored outside the machine

LAN mode without TLS is a trusted-network convenience mode. It improves authorization and request integrity at the application layer, but the operator secret, session cookie, and responses still travel over HTTP unless the user places the service behind a correctly configured TLS endpoint. TLS and device pairing are future work.

The live-action challenge is an intent, replay, and payload-binding control. It is not multi-factor authentication and must not be described as biometric, passkey, or second-factor protection.

## 4. Non-goals

- Multi-user accounts, cloud identity providers, OAuth, or social login.
- Custody of private keys, seed phrases, or wallet signing material.
- Automatic live trading without a separate user-approved action.
- Internet-facing production hosting.
- Full replacement of inline UI scripts or removal of every permissive CSP directive in this phase.
- Regulatory certification, brokerage compliance, or claims of financial safety.
- Durable distributed sessions across multiple server workers.
- Signed or externally anchored audit records.
- Defense against an active network attacker when LAN mode uses plain HTTP.

## 5. Operating modes

### 5.1 Local mode

Local mode is the default.

- Bind address: `127.0.0.1`.
- Default allowed origins: `http://127.0.0.1:8000` and `http://localhost:8000`.
- Public intelligence endpoints remain available without an operator session.
- Private, paid, and state-changing endpoints remain disabled until an operator secret is configured and a session is authenticated.
- The server must not silently widen its bind address or CORS policy.

### 5.2 LAN mode

LAN mode is an explicit opt-in for phone access on the same trusted network.

- Enabled with `SNIPER_BIND_MODE=lan`.
- Bind address becomes `0.0.0.0` unless an explicit host is supplied.
- Startup fails closed unless `SNIPER_OPERATOR_SECRET` is at least 20 characters.
- Startup fails closed when CORS origins are wildcarded, omitted, or still set to local-only defaults.
- The operator must list exact origins, including the LAN host or IP used by the phone.
- Forwarded client-IP headers are ignored unless a future trusted-proxy mode is explicitly configured.
- Documentation states that LAN mode is not intended for port forwarding or public internet exposure.

A recommended secret is generated outside the application with a command such as `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 5.3 Read-only mode

When no operator secret is configured, the application starts in read-only mode.

- Public market, news, integration-status, health, static UI, public-wallet, and deterministic local analysis endpoints continue to function.
- Private, paid, and state-changing endpoints return `503 operator_auth_not_configured`.
- The UI displays a visible read-only status rather than presenting controls that appear operational.

## 6. Trust and permission model

The system has four practical authority categories.

### 6.1 Public reader

May access public market data, public news, health, readiness, safe integration status, DEX discovery, caller-supplied public-wallet snapshots, static UI assets, and non-mutating local analysis that does not consume a paid provider or preserve private session state.

### 6.2 Authenticated private reader

May inspect alerts, warm sessions, paper portfolio state, copy-trade state, audit status, and other operator-specific information.

### 6.3 Authenticated operator

May create and delete alerts, manage warm research sessions, invoke configured paid model commentary, register paper leaders and followers, emit paper signals, copy paper signals, and manage the current operator session.

### 6.4 Live-action authorization

Live execution is not a persistent role. It is a one-action capability created by a short-lived challenge. An authenticated operator must request and complete a challenge for a specific action. The authorization is valid only once, only for the bound session, only for the bound action digest, and only before expiry.

This limits replay and payload substitution. It does not compensate for a stolen authenticated session or a hostile network.

## 7. Authentication design

### 7.1 Operator secret

- The operator secret is supplied through `SNIPER_OPERATOR_SECRET` or an equivalent protected local environment source.
- Any configured secret must be at least 20 characters.
- It is never committed, returned by an API, written to the integrity log, or embedded in the frontend.
- Comparison uses `secrets.compare_digest` or an equivalent constant-time operation.
- LAN startup rejects missing, weak, placeholder, or wildcard security settings.

### 7.2 Session creation

`POST /auth/session`

Request fields include the operator secret and `transport`, which is either `cookie` or `bearer`.

Common behavior:

- Apply a strict authentication-attempt rate limit before comparison.
- Create a cryptographically random opaque session token after successful comparison.
- Store only a keyed hash of the token in the in-memory session registry.
- Use `Cache-Control: no-store` on authentication responses.
- Record success or failure without recording the supplied secret.

Cookie transport, used by the browser UI:

- Set an `HttpOnly`, `SameSite=Strict` session cookie.
- Set `Secure` when the request is served over HTTPS.
- Return a CSRF token in the response body.
- Do not return the opaque session token to browser JavaScript.

Bearer transport, used by explicit API clients:

- Return the opaque session token once in the response body.
- Do not set the session cookie.
- Do not return a CSRF token because Bearer requests do not use cookie authentication.

### 7.3 Session lifecycle

- Absolute lifetime: 30 minutes by default.
- Idle timeout: 15 minutes by default.
- Maximum concurrent sessions: 8 by default.
- Session deletion immediately invalidates the token.
- Server restart invalidates all sessions.
- Session records contain creation time, last-seen time, a random session identifier, token hash, CSRF hash for cookie sessions, and a process-salted client key used only for rate limiting and diagnostics.
- Client keys are not treated as an authentication factor because IP addresses and user-agent values can change or be spoofed.

Endpoints:

- `GET /auth/status`
- `POST /auth/session`
- `DELETE /auth/session`

For a valid cookie session, `GET /auth/status` may return the current CSRF token or rotate and replace it so a browser refresh can recover without storing the token in local storage.

### 7.4 CSRF protection

Cookie-authenticated private or state-changing requests must send the session's CSRF token in `X-CSRF-Token`.

- The server compares the header token to the session record using constant-time comparison.
- Bearer-authenticated API requests do not require CSRF because browsers do not attach the authorization header automatically.
- Exact-origin validation remains enabled as an additional browser boundary.
- Logout through cookie transport also requires CSRF.

## 8. Live-action challenge design

### 8.1 Challenge creation

`POST /auth/live-challenges`

The authenticated operator submits a normalized action description containing:

- operation type
- signal identifier or leader identifier when applicable
- instrument
- side
- quantity or notional
- order type
- destination adapter
- any price or slippage fields that materially affect execution

The server canonicalizes the action and calculates an action digest. It returns:

- challenge ID
- short random code
- exact confirmation phrase, for example `CONFIRM LIVE R7K4Q2`
- expiration timestamp
- human-readable action summary

Defaults:

- expiry: 60 seconds
- maximum outstanding challenges: 3 per session
- challenge creation limit: 5 per minute per session

### 8.2 Challenge verification

A live submission request includes the challenge ID and exact confirmation phrase.

The server verifies that the challenge:

- exists and is unexpired
- belongs to the authenticated session
- has not been consumed
- matches the canonical action digest
- matches the confirmation phrase using constant-time comparison

The server marks the challenge consumed before invoking the exchange adapter. Consumption is atomic inside the single-process challenge store. A failed downstream order does not make the challenge reusable.

### 8.3 Compatibility

The current `confirm_live` field may remain temporarily for UI compatibility, but the fixed phrase `CONFIRM LIVE` is no longer sufficient. Live requests without a valid challenge return `403 live_challenge_required`.

## 9. Endpoint policy

### 9.1 Public endpoints

- `GET /health`
- `GET /ready`
- `GET /trader/prompt`
- `GET /news`
- `GET /news/sources`
- `GET /integrations` with secret values excluded
- `GET /grok/status` with secret values excluded
- public `GET /market/*` routes
- `GET /wallet/solana/{address}` for the caller-supplied public address
- `GET /sniper/live`
- `GET /live/deck` only when its configured commentary path cannot consume a paid provider; otherwise the paid commentary event is disabled for anonymous streams
- static UI, manifest, and service-worker assets

### 9.2 Authenticated private-read endpoints

- `GET /alerts`
- `GET /sessions`
- `GET /portfolio/paper`
- `GET /copy/state`
- protected audit-integrity status
- any integration detail that reveals operator-specific configuration beyond a safe boolean status

### 9.3 Authenticated operator endpoints

- `POST /grok/comment`
- `POST /research/search`, because the current route can create or update warm session state and consume compute
- `POST /alerts`
- `DELETE /alerts/{alert_id}`
- `DELETE /sessions/{session_id}`
- `POST /copy/leaders`
- `POST /copy/followers`
- paper-mode `POST /copy/signals`
- paper-mode `POST /copy/signals/{signal_id}/copy`
- live-challenge creation
- session deletion

### 9.4 Live-challenge-protected actions

Any path capable of invoking a real exchange or transaction adapter requires both an authenticated operator session and a valid one-time live challenge.

Route classification is centralized in reusable FastAPI dependencies. A route that becomes private, paid, stateful, or mutating during implementation must be moved to the appropriate protected category rather than left public by convenience.

## 10. Rate limiting and connection controls

A lean in-memory limiter is sufficient for this local single-process service.

- Authentication attempts: 5 attempts per 10 minutes per process-salted client key.
- Private reads: 120 requests per minute per session.
- State-changing requests: 60 requests per minute per session.
- Live-challenge creation: 5 challenges per minute per session.
- Live submissions: 3 attempts per minute per session.
- SSE streams: maximum 3 concurrent streams per client key and 12 total by default.

The client key uses the directly connected peer address and a bounded user-agent value with a process-random salt. `X-Forwarded-For` and similar headers are ignored by default.

Rate-limit responses return `429 rate_limited` with `Retry-After` where applicable. Limiter state is intentionally ephemeral and resets on restart.

## 11. Security integrity log

Security-sensitive actions are written to JSONL under the existing local application data directory.

Each record includes:

- UTC timestamp
- server-generated request ID
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

Events include authentication success and failure, logout, rate-limit denial, protected-route denial, challenge creation, challenge consumption, paper action, live-action attempt, and integrity verification failure.

A verification function checks ordering and hash linkage during startup and through a protected diagnostic endpoint. Chain failure places live execution into a fail-closed state until the operator resolves or rotates the file.

This hash chain detects accidental edits, truncation, and unsophisticated tampering relative to the local file state. Because it is neither signed nor externally anchored, an attacker with full filesystem control could rewrite the entire log and recompute its hashes. Documentation must call it an integrity log, not an immutable or compliance-grade audit ledger.

## 12. Error handling and observability

### 12.1 Public errors

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

### 12.2 Request IDs

The server always generates its own bounded request ID. An incoming `X-Request-Id` is not trusted as the canonical log identifier. A sanitized client-supplied identifier may be recorded separately when useful, but it cannot replace the server-generated ID or inject control characters into logs and headers.

### 12.3 Local diagnostics

Server logs retain structured diagnostic context with server-generated request IDs. Sensitive fields are redacted before logging. The request ID is the correlation key for integrity and application logs.

### 12.4 Safe failure behavior

- Authentication subsystem unavailable: deny private reads and state changes.
- Integrity-log verification failure: allow public read-only use, deny live actions.
- Challenge store failure: deny live actions.
- Exchange adapter failure: consume the challenge, record failure, return a stable error.
- Rate limiter failure: deny authentication and live execution; public market access may remain available.
- Paid model provider failure: return a stable upstream error without provider secrets or raw payloads.

## 13. Configuration changes

The settings model gains explicit fields such as:

- `bind_mode: Literal["local", "lan"] = "local"`
- `host: str = "127.0.0.1"`
- `cors_origins: str` with local-only defaults
- `operator_secret: SecretStr | None`
- `session_ttl_seconds: int = 1800`
- `session_idle_seconds: int = 900`
- `max_operator_sessions: int = 8`
- `live_challenge_ttl_seconds: int = 60`
- `max_live_challenges_per_session: int = 3`
- `max_sse_per_client: int = 3`
- `max_sse_total: int = 12`
- `integrity_log_path`

Configuration validation rejects unsafe combinations, especially LAN mode with a weak or missing secret, wildcard CORS, or local-only origins.

## 14. Component boundaries

### `services/security.py`

Owns operator-secret validation, session issuance, token hashing, session expiry, CSRF validation, and session lookup.

### `services/live_authorization.py`

Owns canonical action digests, challenge creation, expiry, single-use consumption, and challenge verification.

### `services/rate_limit.py`

Owns in-memory windows or token buckets and exposes narrow named limit checks.

### `services/security_audit.py`

Owns redaction, hash-chained JSONL writes, integrity verification, and event schemas. The filename may retain `security_audit.py`, but public documentation calls the output an integrity log rather than a compliance audit ledger.

### `services/stream_limits.py`

Owns bounded SSE leases and guarantees release when a stream exits, is cancelled, or raises an exception.

### `security_dependencies.py`

Defines reusable FastAPI dependencies for optional session lookup, required private access, required operator access, CSRF checks, and live-action authorization.

The route layer in `main.py` calls these interfaces rather than implementing security logic inline.

## 15. UI behavior

The existing single-page interface gains a compact operator-security panel.

- Displays `READ ONLY`, `OPERATOR`, or `LIVE CHALLENGE ACTIVE` status.
- Prompts for the operator secret without persisting it.
- Uses cookie transport and keeps the session token in an HttpOnly cookie.
- Stores only the CSRF token in memory, not local storage.
- Recovers or rotates the CSRF token through authenticated status after a refresh.
- Hides or disables private and state-changing controls while unauthenticated.
- Shows a review card containing the exact live action before requesting a challenge.
- Requires the challenge-specific phrase before live submission.
- Clears challenge state immediately after an attempt or expiry.
- Labels the challenge as a one-action confirmation, not MFA.
- Never asks for wallet seed phrases or private keys.

## 16. Testing strategy

### 16.1 Unit tests

- session token generation and hashed storage
- cookie and Bearer transport separation
- absolute and idle expiry
- constant-time validation paths
- CSRF verification and rotation
- process-salted client-key behavior without trusting forwarded headers
- action canonicalization and digest stability
- challenge expiry, mismatch, outstanding-cap, and single-use behavior
- limiter windows
- integrity-chain verification and redaction
- SSE lease acquisition and release on normal exit, cancellation, and exception

### 16.2 API tests

Using FastAPI's test client:

- local default host and safe CORS configuration
- LAN startup rejection without a 20-character secret
- LAN startup rejection with wildcard or local-only CORS
- private-read and state-changing route denial without authentication
- paid Grok route denial without authentication
- research route denial without authentication
- successful cookie login, CSRF recovery, and logout
- successful Bearer login without setting a cookie
- cookie plus CSRF success and failure
- Bearer-token success
- brute-force rate limiting
- private portfolio and copy-state reads allowed for an operator
- paper action allowed with operator authority
- live action denied with the legacy fixed phrase alone
- live action allowed with a matching challenge
- replayed challenge denied
- challenge from another session denied
- altered order payload denied because its digest no longer matches
- integrity failure places live paths into fail-closed mode
- SSE limits deny excess connections and release leases after disconnect
- anonymous live deck does not consume a paid provider
- public read-only endpoints remain available
- raw upstream exceptions and untrusted request IDs are not reflected unsafely

### 16.3 Regression tests

Existing risk, alert, integration, Grok, volume, market, and core tests must continue passing. New security tests avoid external network calls and use deterministic fakes for paid providers, market adapters, and live-order adapters.

## 17. Continuous integration

Add a GitHub Actions workflow that runs on pull requests and pushes to `main` and implementation branches.

Required checks:

1. Install the lean core and development dependencies.
2. Run `pytest -q`.
3. Run `ruff check` and formatting verification.
4. Run `bandit` against application Python files with documented exclusions only.
5. Run `pip-audit` against the dependency set.
6. Run a maintained secret-scanning action.

The workflow is designed to become a required status check. Actual merge blocking depends on repository branch-protection settings and will be configured or documented separately if the connector permissions permit it. Network-dependent integration tests remain separate and non-blocking until deterministic fixtures are available.

## 18. Documentation changes

Update the README and create or update `.env.example` to include:

- safe localhost quick start
- explicit LAN setup
- 20-character minimum secret requirement
- exact-origin CORS examples
- trusted-LAN and plain-HTTP limitations
- cookie and Bearer authentication flows
- public, private, paper, and live authorization boundaries
- integrity-log location and verification limitations
- warning against port forwarding or public deployment
- migration note explaining that the fixed `CONFIRM LIVE` phrase is deprecated

## 19. Migration and rollout

### Phase 1: secure defaults

- local bind default
- validated CORS configuration
- read-only fallback without an operator secret
- startup validation for LAN mode

### Phase 2: operator sessions and route classification

- cookie and Bearer authentication
- CSRF support
- protect private reads, paid routes, and state-changing routes
- update UI state

### Phase 3: live authorization and integrity logging

- one-time action-bound challenges
- challenge-protected live execution
- hash-chained integrity events
- fail-closed live behavior

### Phase 4: abuse controls and CI

- rate limits
- SSE connection bounds
- stable error envelope and server-generated request IDs
- security test suite and GitHub Actions
- documentation migration

Each phase leaves the application in a usable, safer state and never temporarily exposes live execution through a weaker compatibility path.

## 20. Acceptance criteria

The implementation is complete when all of the following are true:

- A default launch is reachable only from the local device.
- LAN mode cannot start with wildcard CORS, local-only CORS, or without a 20-character operator secret.
- Every private-read, paid, and state-changing endpoint rejects anonymous requests.
- Cookie-authenticated protected requests reject missing or incorrect CSRF tokens.
- Browser JavaScript never receives the opaque cookie-session token.
- Bearer login does not set an authentication cookie.
- The old fixed phrase cannot authorize live execution.
- A live challenge cannot be replayed, transferred between sessions, or used with a modified action.
- Documentation states that the challenge is not MFA and LAN HTTP does not resist hostile network interception.
- Integrity records exclude secrets and verify as an intact hash chain.
- Documentation does not call the local hash chain immutable or compliance-grade.
- Live execution fails closed when authorization or integrity verification is unavailable.
- Anonymous live streams cannot invoke a paid commentary provider.
- SSE connections are bounded and released correctly.
- Existing functionality and tests remain intact.
- New security tests and CI checks pass.
- README instructions describe the actual implemented behavior without overstating production readiness.

## 21. Future work

After this foundation is implemented and verified, later designs may address TLS for LAN use, device pairing, hardware-backed passkeys, signed mobile companion requests, persistent encrypted sessions, stricter CSP without inline scripts, exchange-specific scopes, externally anchored audit receipts, and shared security-event contracts across Raven AI, Home for AI, Hermes Edge, OpenClinical AI, and JSpace Chain.
