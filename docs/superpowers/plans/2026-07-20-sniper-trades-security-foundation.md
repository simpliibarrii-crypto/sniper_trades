# Sniper Trades Security Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Sniper Trades so it is local-only by default, authenticates all private or mutating operations, binds live execution to a single-use action challenge, limits abuse, and proves the behavior through deterministic tests and CI.

**Architecture:** Add small security services around the existing FastAPI application instead of rewriting trading logic. Configuration validation establishes safe operating modes; an in-memory session registry and FastAPI dependencies enforce operator authority; a hash-chained JSONL audit log and action-bound challenge store protect live paths; bounded stream leases and fixed-window rate limits constrain local abuse. The current single-process architecture remains intact.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, pydantic-settings, pytest, FastAPI TestClient/httpx, standard-library `secrets`, `hashlib`, `hmac`, `threading`, `pathlib`, JSONL, GitHub Actions, Ruff, Bandit, pip-audit, Gitleaks.

## Global Constraints

- Default bind address is exactly `127.0.0.1`; LAN exposure requires `SNIPER_BIND_MODE=lan`.
- LAN mode must reject missing or fewer-than-20-character operator secrets.
- LAN mode must reject wildcard, omitted, or local-only CORS origins.
- Public intelligence stays usable without authentication; private state, paid model calls, and writes require an operator session.
- Cookie-authenticated writes require `X-CSRF-Token`; Bearer-authenticated API writes do not.
- Session absolute lifetime defaults to 1,800 seconds; idle timeout defaults to 900 seconds; maximum sessions defaults to 8.
- The fixed phrase `CONFIRM LIVE` alone never authorizes a real order.
- A live challenge is single-use, session-bound, action-digest-bound, and consumed before the exchange adapter is called.
- Audit records never contain operator secrets, raw session tokens, CSRF tokens, confirmation phrases, API keys, seed phrases, private keys, or unrestricted request bodies.
- Audit-chain failure, challenge-store failure, or authorization failure denies live execution while preserving read-only use.
- LAN mode over plain HTTP is documented as trusted-network convenience, not protection against an active network attacker.
- No OAuth, multi-user accounts, passkeys, biometric authentication, TLS termination, distributed sessions, or public-internet deployment are added in this plan.
- Keep lean runtime dependencies; security primitives use the Python standard library.
- Existing risk, alerts, market, Grok, integration, copy-trade, and core tests must continue passing.

---

## File Structure

### New runtime files

- `services/security.py`: operator-secret validation, opaque session issuance, token hashing, expiry, revocation, and session status.
- `services/rate_limit.py`: deterministic in-memory fixed-window limits.
- `services/security_audit.py`: redacted, hash-chained JSONL audit writes and verification.
- `services/live_authorization.py`: canonical action normalization, digesting, challenge issuance, expiry, and atomic consumption.
- `services/stream_limits.py`: bounded SSE leases with guaranteed release.
- `security_dependencies.py`: FastAPI request identity, cookie/Bearer extraction, CSRF enforcement, client keys, and stable security errors.

### New tests

- `tests/test_security_config.py`
- `tests/test_security_sessions.py`
- `tests/test_rate_limit.py`
- `tests/test_security_audit.py`
- `tests/test_live_authorization.py`
- `tests/test_security_api.py`
- `tests/test_stream_limits.py`

### Modified runtime files

- `config.py`: safe defaults and validated security settings.
- `schemas.py`: authentication, challenge, and live-submission contracts.
- `main.py`: security runtime initialization, auth routes, route policies, stable errors, audit calls, and stream leases.
- `services/grok_live.py`: explicit `allow_remote` gate so unauthenticated streams cannot spend a configured xAI key.
- `services/copy_trade.py`: preserve paper behavior while accepting live authorization only from the route layer.
- `ui/index.html`: operator login/status panel, CSRF-aware fetch wrapper, and challenge review flow.
- `README.md`: safe local start, LAN warnings, auth flow, and migration notes.

### New project files

- `.env.example`: safe configuration examples without secrets.
- `requirements-dev.txt`: deterministic test and security tooling.
- `.github/workflows/security.yml`: tests, lint, formatting, Bandit, pip-audit, and secret scanning.

---

### Task 1: Safe operating modes and development toolchain

**Files:**
- Modify: `config.py`
- Create: `tests/test_security_config.py`
- Create: `.env.example`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: `Settings.bind_mode`, `Settings.host`, `Settings.operator_secret`, `Settings.read_only`, `Settings.cors_origin_list`, session/stream/audit limits used by later tasks.
- Consumes: existing `get_settings()` caching behavior and `SNIPER_` environment prefix.

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_security_config.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import Settings


def make_settings(**values):
    return Settings(_env_file=None, **values)


def test_local_mode_is_safe_by_default():
    settings = make_settings()
    assert settings.bind_mode == "local"
    assert settings.host == "127.0.0.1"
    assert settings.cors_origin_list == [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]
    assert settings.read_only is True


def test_lan_mode_requires_strong_secret():
    with pytest.raises(ValidationError, match="at least 20 characters"):
        make_settings(
            bind_mode="lan",
            operator_secret="too-short",
            cors_origins="http://192.168.1.20:8000",
        )


def test_lan_mode_rejects_wildcard_cors():
    with pytest.raises(ValidationError, match="exact non-wildcard origins"):
        make_settings(
            bind_mode="lan",
            operator_secret="x" * 20,
            cors_origins="*",
        )


def test_lan_mode_rejects_local_only_origins():
    with pytest.raises(ValidationError, match="LAN origin"):
        make_settings(
            bind_mode="lan",
            operator_secret="x" * 20,
            cors_origins="http://127.0.0.1:8000,http://localhost:8000",
        )


def test_lan_mode_derives_wildcard_bind_only_after_validation():
    settings = make_settings(
        bind_mode="lan",
        operator_secret="x" * 20,
        cors_origins="http://192.168.1.20:8000",
    )
    assert settings.host == "0.0.0.0"
    assert settings.read_only is False
```

- [ ] **Step 2: Add deterministic development dependencies**

Create `requirements-dev.txt`:

```text
-r requirements-core.txt
pytest>=8.2,<9
ruff>=0.5,<1
bandit>=1.7.9,<2
pip-audit>=2.7,<3
```

Run:

```bash
python -m pip install -r requirements-dev.txt
pytest tests/test_security_config.py -q
```

Expected: collection fails or tests fail because the new settings do not exist.

- [ ] **Step 3: Implement validated settings**

Replace the `Settings` class in `config.py` with fields and validation matching this shape:

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_ORIGINS = (
    "http://127.0.0.1:8000",
    "http://localhost:8000",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SNIPER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RavenTrade Core · Sniper Trades"
    version: str = "6.5.0"
    bind_mode: Literal["local", "lan"] = "local"
    host: str = ""
    port: int = 8000
    cors_origins: str = ",".join(LOCAL_ORIGINS)
    operator_secret: SecretStr | None = None
    session_ttl_seconds: int = 1800
    session_idle_seconds: int = 900
    max_operator_sessions: int = 8
    max_sse_per_client: int = 3
    max_sse_total: int = 12
    audit_log_path: Path = Path.home() / ".local/share/sniper_trades/security_audit.jsonl"
    max_sessions: int = 48
    history_cap: int = 64
    broadcast_top_k: int = 7
    access_log: bool = False
    xai_api_key: str = ""
    xai_model: str = "grok-4-1-fast-non-reasoning"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def read_only(self) -> bool:
        return self.operator_secret is None

    @model_validator(mode="after")
    def validate_security_mode(self) -> "Settings":
        if not self.host:
            self.host = "0.0.0.0" if self.bind_mode == "lan" else "127.0.0.1"
        origins = self.cors_origin_list
        if self.bind_mode == "local":
            if "*" in origins:
                raise ValueError("local mode does not allow wildcard CORS")
            return self
        secret = self.operator_secret.get_secret_value() if self.operator_secret else ""
        if len(secret) < 20:
            raise ValueError("LAN mode requires an operator secret of at least 20 characters")
        if not origins or "*" in origins:
            raise ValueError("LAN mode requires exact non-wildcard origins")
        if set(origins).issubset(set(LOCAL_ORIGINS)):
            raise ValueError("LAN mode requires at least one explicit LAN origin")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Add safe environment examples**

Create `.env.example`:

```dotenv
# Safe default: accessible only on this computer.
SNIPER_BIND_MODE=local
SNIPER_CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

# Leave unset for read-only mode. Generate with:
# python -c "import secrets; print(secrets.token_urlsafe(32))"
# SNIPER_OPERATOR_SECRET=replace-with-generated-secret

# Trusted-LAN phone access requires all three lines below and remains plain HTTP.
# SNIPER_BIND_MODE=lan
# SNIPER_CORS_ORIGINS=http://192.168.1.20:8000
# SNIPER_OPERATOR_SECRET=replace-with-at-least-20-characters

# Optional paid xAI commentary. Never commit the real value.
# SNIPER_XAI_API_KEY=
SNIPER_XAI_MODEL=grok-4-1-fast-non-reasoning
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
pytest tests/test_security_config.py -q
python -m compileall config.py
```

Expected: all configuration tests pass and compileall exits zero.

Commit:

```bash
git add config.py tests/test_security_config.py .env.example requirements-dev.txt
git commit -m "feat: enforce secure operating modes"
```

---

### Task 2: Operator sessions and fixed-window rate limits

**Files:**
- Create: `services/security.py`
- Create: `services/rate_limit.py`
- Create: `tests/test_security_sessions.py`
- Create: `tests/test_rate_limit.py`

**Interfaces:**
- Produces: `SessionStore`, `IssuedSession`, `SessionPrincipal`, `FixedWindowLimiter`, `RateLimitDecision`.
- Consumes: `Settings.session_ttl_seconds`, `Settings.session_idle_seconds`, and `Settings.max_operator_sessions`.

- [ ] **Step 1: Write failing session tests**

Create `tests/test_security_sessions.py`:

```python
from __future__ import annotations

import hashlib

import pytest

from services.security import AuthenticationError, SessionCapacityError, SessionStore


def test_session_store_hashes_tokens_and_authenticates():
    store = SessionStore("correct horse battery staple", ttl_seconds=30, idle_seconds=10, max_sessions=2)
    issued = store.create("correct horse battery staple", "client-a", now=100.0)
    assert issued.token not in repr(store._sessions)
    assert hashlib.sha256(issued.token.encode()).hexdigest() in store._sessions
    principal = store.authenticate(issued.token, now=105.0)
    assert principal.session_id == issued.principal.session_id
    assert principal.csrf_token == issued.principal.csrf_token


def test_wrong_secret_is_rejected():
    store = SessionStore("correct horse battery staple", 30, 10, 2)
    with pytest.raises(AuthenticationError):
        store.create("wrong", "client-a", now=100.0)


def test_idle_and_absolute_expiry_are_enforced():
    store = SessionStore("correct horse battery staple", ttl_seconds=30, idle_seconds=10, max_sessions=2)
    idle = store.create("correct horse battery staple", "a", now=100.0)
    assert store.authenticate(idle.token, now=111.0) is None
    absolute = store.create("correct horse battery staple", "b", now=200.0)
    assert store.authenticate(absolute.token, now=229.0) is not None
    assert store.authenticate(absolute.token, now=231.0) is None


def test_revocation_and_capacity():
    store = SessionStore("correct horse battery staple", 60, 60, 1)
    first = store.create("correct horse battery staple", "a", now=1.0)
    with pytest.raises(SessionCapacityError):
        store.create("correct horse battery staple", "b", now=2.0)
    assert store.revoke(first.token) is True
    assert store.authenticate(first.token, now=3.0) is None
```

- [ ] **Step 2: Write failing limiter tests**

Create `tests/test_rate_limit.py`:

```python
from services.rate_limit import FixedWindowLimiter


def test_fixed_window_limit_and_retry_after():
    limiter = FixedWindowLimiter()
    assert limiter.check("login:client-a", limit=2, window_seconds=10, now=0).allowed
    assert limiter.check("login:client-a", limit=2, window_seconds=10, now=1).allowed
    denied = limiter.check("login:client-a", limit=2, window_seconds=10, now=2)
    assert denied.allowed is False
    assert denied.retry_after == 8
    assert limiter.check("login:client-a", limit=2, window_seconds=10, now=10).allowed


def test_buckets_are_isolated():
    limiter = FixedWindowLimiter()
    limiter.check("a", 1, 10, now=0)
    assert limiter.check("a", 1, 10, now=1).allowed is False
    assert limiter.check("b", 1, 10, now=1).allowed is True
```

Run:

```bash
pytest tests/test_security_sessions.py tests/test_rate_limit.py -q
```

Expected: import failures because the modules do not exist.

- [ ] **Step 3: Implement the session store**

Create `services/security.py` with these public contracts:

```python
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass


class AuthenticationError(ValueError):
    pass


class SessionCapacityError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionPrincipal:
    session_id: str
    csrf_token: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class IssuedSession:
    token: str
    principal: SessionPrincipal


@dataclass
class _SessionRecord:
    session_id: str
    token_hash: str
    csrf_token: str
    created_at: float
    last_seen_at: float
    expires_at: float
    fingerprint_hash: str


class SessionStore:
    def __init__(self, operator_secret: str | None, ttl_seconds: int, idle_seconds: int, max_sessions: int):
        self._operator_secret = operator_secret or ""
        self._ttl_seconds = ttl_seconds
        self._idle_seconds = idle_seconds
        self._max_sessions = max_sessions
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(self._operator_secret)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _purge(self, now: float) -> None:
        expired = [key for key, record in self._sessions.items() if now >= record.expires_at or now - record.last_seen_at > self._idle_seconds]
        for key in expired:
            self._sessions.pop(key, None)

    def create(self, provided_secret: str, fingerprint: str, now: float | None = None) -> IssuedSession:
        current = time.time() if now is None else now
        if not self.configured or not secrets.compare_digest(provided_secret, self._operator_secret):
            raise AuthenticationError("invalid operator secret")
        with self._lock:
            self._purge(current)
            if len(self._sessions) >= self._max_sessions:
                raise SessionCapacityError("operator session capacity reached")
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            session_id = "OS_" + secrets.token_hex(8)
            token_hash = self._hash(token)
            record = _SessionRecord(
                session_id=session_id,
                token_hash=token_hash,
                csrf_token=csrf,
                created_at=current,
                last_seen_at=current,
                expires_at=current + self._ttl_seconds,
                fingerprint_hash=self._hash(fingerprint),
            )
            self._sessions[token_hash] = record
            return IssuedSession(token, SessionPrincipal(session_id, csrf, current, record.expires_at))

    def authenticate(self, token: str, now: float | None = None) -> SessionPrincipal | None:
        if not token:
            return None
        current = time.time() if now is None else now
        token_hash = self._hash(token)
        with self._lock:
            self._purge(current)
            record = self._sessions.get(token_hash)
            if record is None:
                return None
            record.last_seen_at = current
            return SessionPrincipal(record.session_id, record.csrf_token, record.created_at, record.expires_at)

    def revoke(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(self._hash(token), None) is not None

    def active_count(self, now: float | None = None) -> int:
        current = time.time() if now is None else now
        with self._lock:
            self._purge(current)
            return len(self._sessions)
```

- [ ] **Step 4: Implement the fixed-window limiter**

Create `services/rate_limit.py`:

```python
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


class FixedWindowLimiter:
    def __init__(self):
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, limit: int, window_seconds: int, now: float | None = None) -> RateLimitDecision:
        current = time.time() if now is None else now
        with self._lock:
            start, count = self._windows.get(bucket, (current, 0))
            if current - start >= window_seconds:
                start, count = current, 0
            if count >= limit:
                retry = max(1, math.ceil(window_seconds - (current - start)))
                self._windows[bucket] = (start, count)
                return RateLimitDecision(False, retry)
            self._windows[bucket] = (start, count + 1)
            return RateLimitDecision(True, 0)
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
pytest tests/test_security_sessions.py tests/test_rate_limit.py -q
python -m compileall services/security.py services/rate_limit.py
```

Expected: all tests pass.

Commit:

```bash
git add services/security.py services/rate_limit.py tests/test_security_sessions.py tests/test_rate_limit.py
git commit -m "feat: add operator sessions and rate limits"
```

---

### Task 3: Hash-chained security audit log

**Files:**
- Create: `services/security_audit.py`
- Create: `tests/test_security_audit.py`

**Interfaces:**
- Produces: `SecurityAuditLog.append()`, `SecurityAuditLog.verify()`, `AuditVerification`, and `SecurityAuditLog.healthy`.
- Consumes: `Settings.audit_log_path` and security event metadata from later route tasks.

- [ ] **Step 1: Write failing audit tests**

Create `tests/test_security_audit.py`:

```python
from __future__ import annotations

import json

from services.security_audit import SecurityAuditLog


def test_audit_chain_verifies_and_redacts(tmp_path):
    path = tmp_path / "security.jsonl"
    audit = SecurityAuditLog(path)
    audit.append(
        "auth.success",
        "allowed",
        request_id="req-1",
        session_id="OS_abc",
        action={"instrument": "BTC_USDT", "operator_secret": "never-log-me"},
    )
    audit.append("challenge.consumed", "allowed", request_id="req-2", action_digest="abc123")
    result = audit.verify()
    assert result.ok is True
    assert result.records == 2
    raw = path.read_text()
    assert "never-log-me" not in raw
    assert "[REDACTED]" in raw


def test_tampering_marks_log_unhealthy(tmp_path):
    path = tmp_path / "security.jsonl"
    audit = SecurityAuditLog(path)
    audit.append("auth.success", "allowed", request_id="req-1")
    row = json.loads(path.read_text())
    row["outcome"] = "changed"
    path.write_text(json.dumps(row) + "\n")
    result = audit.verify()
    assert result.ok is False
    assert audit.healthy is False
```

Run:

```bash
pytest tests/test_security_audit.py -q
```

Expected: import failure.

- [ ] **Step 2: Implement append, redaction, and verification**

Create `services/security_audit.py` with these behaviors:

```python
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REDACT_KEYS = {
    "operator_secret", "secret", "token", "session_token", "csrf_token",
    "confirmation_text", "confirmation_phrase", "api_key", "private_key", "seed_phrase",
}
GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    records: int
    last_hash: str
    error: str | None = None


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): ("[REDACTED]" if str(key).lower() in REDACT_KEYS else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class SecurityAuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.healthy = self.verify().ok

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def verify(self) -> AuditVerification:
        previous = GENESIS_HASH
        records = 0
        if not self.path.exists():
            self.healthy = True
            return AuditVerification(True, 0, previous)
        try:
            for raw_line in self.path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                current_hash = row.pop("record_hash")
                if row.get("previous_hash") != previous or self._digest(row) != current_hash:
                    self.healthy = False
                    return AuditVerification(False, records, previous, "hash chain mismatch")
                previous = current_hash
                records += 1
        except Exception as exc:
            self.healthy = False
            return AuditVerification(False, records, previous, str(exc)[:160])
        self.healthy = True
        return AuditVerification(True, records, previous)

    def append(
        self,
        event_type: str,
        outcome: str,
        *,
        request_id: str,
        session_id: str | None = None,
        action: Mapping[str, Any] | None = None,
        action_digest: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            verification = self.verify()
            if not verification.ok:
                raise RuntimeError("security audit chain is not healthy")
            row = {
                "timestamp": time.time(),
                "request_id": request_id,
                "event_type": event_type,
                "outcome": outcome,
                "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest() if session_id else None,
                "action": redact(dict(action or {})),
                "action_digest": action_digest,
                "previous_hash": verification.last_hash,
            }
            row["record_hash"] = self._digest(row)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.healthy = True
            return row
```

- [ ] **Step 3: Verify and commit**

Run:

```bash
pytest tests/test_security_audit.py -q
python -m compileall services/security_audit.py
```

Expected: all tests pass.

Commit:

```bash
git add services/security_audit.py tests/test_security_audit.py
git commit -m "feat: add tamper-evident security audit"
```

---

### Task 4: Single-use action-bound live challenges

**Files:**
- Create: `services/live_authorization.py`
- Create: `tests/test_live_authorization.py`
- Modify: `schemas.py`

**Interfaces:**
- Produces: `canonicalize_action()`, `LiveChallengeStore.create()`, `LiveChallengeStore.consume()`, `LiveChallengeView`.
- Consumes: authenticated `session_id` and normalized action dictionaries from route handlers.

- [ ] **Step 1: Write failing challenge tests**

Create `tests/test_live_authorization.py`:

```python
from __future__ import annotations

import pytest

from services.live_authorization import ChallengeError, LiveChallengeStore, canonicalize_action

ACTION = {
    "operation": "copy.emit_signal",
    "leader_id": "L_1",
    "instrument": "BTC_USDT",
    "side": "BUY",
    "order_type": "MARKET",
    "notional_usd": 25.0,
}


def test_action_digest_is_order_independent():
    digest_a, _ = canonicalize_action(ACTION)
    digest_b, _ = canonicalize_action(dict(reversed(list(ACTION.items()))))
    assert digest_a == digest_b


def test_challenge_is_session_action_and_time_bound():
    store = LiveChallengeStore(ttl_seconds=60)
    challenge = store.create("OS_a", ACTION, now=100.0)
    assert challenge.confirmation_phrase.startswith("CONFIRM LIVE ")
    with pytest.raises(ChallengeError, match="session"):
        store.consume("OS_b", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=101.0)
    altered = {**ACTION, "notional_usd": 50.0}
    with pytest.raises(ChallengeError, match="action"):
        store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, altered, now=102.0)
    assert store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=103.0)
    with pytest.raises(ChallengeError, match="consumed"):
        store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=104.0)


def test_expired_and_wrong_phrase_are_rejected():
    store = LiveChallengeStore(ttl_seconds=10)
    challenge = store.create("OS_a", ACTION, now=0.0)
    with pytest.raises(ChallengeError, match="phrase"):
        store.consume("OS_a", challenge.challenge_id, "CONFIRM LIVE WRONG", ACTION, now=1.0)
    with pytest.raises(ChallengeError, match="expired"):
        store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=11.0)
```

Run:

```bash
pytest tests/test_live_authorization.py -q
```

Expected: import failure.

- [ ] **Step 2: Implement canonicalization and atomic consumption**

Create `services/live_authorization.py`:

```python
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping


class ChallengeError(ValueError):
    pass


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        return format(value, ".12g")
    if isinstance(value, str):
        return value.strip()
    return value


def canonicalize_action(action: Mapping[str, Any]) -> tuple[str, str]:
    canonical = json.dumps(_normalize(dict(action)), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical


@dataclass(frozen=True)
class LiveChallengeView:
    challenge_id: str
    confirmation_phrase: str
    expires_at: float
    action_digest: str
    action_summary: str


@dataclass
class _ChallengeRecord:
    session_id: str
    phrase_hash: str
    action_digest: str
    expires_at: float
    consumed: bool = False


class LiveChallengeStore:
    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = ttl_seconds
        self._records: dict[str, _ChallengeRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _phrase_hash(phrase: str) -> str:
        return hashlib.sha256(phrase.encode("utf-8")).hexdigest()

    def create(self, session_id: str, action: Mapping[str, Any], now: float | None = None) -> LiveChallengeView:
        current = time.time() if now is None else now
        digest, canonical = canonicalize_action(action)
        code = secrets.token_hex(3).upper()
        phrase = f"CONFIRM LIVE {code}"
        challenge_id = "LC_" + secrets.token_hex(10)
        expires_at = current + self.ttl_seconds
        with self._lock:
            self._records[challenge_id] = _ChallengeRecord(session_id, self._phrase_hash(phrase), digest, expires_at)
        return LiveChallengeView(challenge_id, phrase, expires_at, digest, canonical[:240])

    def consume(
        self,
        session_id: str,
        challenge_id: str,
        phrase: str,
        action: Mapping[str, Any],
        now: float | None = None,
    ) -> str:
        current = time.time() if now is None else now
        digest, _ = canonicalize_action(action)
        with self._lock:
            record = self._records.get(challenge_id)
            if record is None:
                raise ChallengeError("challenge not found")
            if record.consumed:
                raise ChallengeError("challenge already consumed")
            if current >= record.expires_at:
                raise ChallengeError("challenge expired")
            if not secrets.compare_digest(record.session_id, session_id):
                raise ChallengeError("challenge session mismatch")
            if not secrets.compare_digest(record.action_digest, digest):
                raise ChallengeError("challenge action mismatch")
            if not secrets.compare_digest(record.phrase_hash, self._phrase_hash(phrase)):
                raise ChallengeError("challenge phrase mismatch")
            record.consumed = True
            return digest
```

- [ ] **Step 3: Add API schemas**

Append these models to `schemas.py` and extend `SignalCreate` and `CopySignalIn`:

```python
class AuthSessionIn(BaseModel):
    operator_secret: str = Field(..., min_length=1, max_length=512)


class AuthSessionOut(BaseModel):
    authenticated: bool
    csrf_token: str
    session_id: str
    expires_at: float


class AuthStatusOut(BaseModel):
    configured: bool
    authenticated: bool
    mode: str
    session_id: Optional[str] = None
    expires_at: Optional[float] = None


class LiveChallengeIn(BaseModel):
    action: Dict[str, Any]


class LiveChallengeOut(BaseModel):
    challenge_id: str
    confirmation_phrase: str
    expires_at: float
    action_digest: str
    action_summary: str
```

Change the live fields to:

```python
confirm_live: bool = False
challenge_id: str = Field(default="", max_length=64)
confirmation_text: str = Field(default="", max_length=64)
```

The descriptions must state that `challenge_id` and the challenge-specific phrase are required for live execution.

- [ ] **Step 4: Verify and commit**

Run:

```bash
pytest tests/test_live_authorization.py -q
python -m compileall services/live_authorization.py schemas.py
```

Expected: all tests pass.

Commit:

```bash
git add services/live_authorization.py tests/test_live_authorization.py schemas.py
git commit -m "feat: add action-bound live challenges"
```

---

### Task 5: FastAPI security dependencies and authentication routes

**Files:**
- Create: `security_dependencies.py`
- Create: `tests/test_security_api.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `SecurityRuntime`, `optional_operator()`, `require_operator()`, `require_csrf()`, `client_key()`, and auth endpoints.
- Consumes: `SessionStore`, `FixedWindowLimiter`, `SecurityAuditLog`, `LiveChallengeStore`, and settings from Tasks 1-4.

- [ ] **Step 1: Write failing API tests for read-only, login, CSRF, and private reads**

Create `tests/test_security_api.py` with environment-isolated app loading:

```python
from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient


def load_app(monkeypatch, tmp_path, *, secret: str | None = None):
    monkeypatch.setenv("SNIPER_BIND_MODE", "local")
    monkeypatch.setenv("SNIPER_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    if secret is None:
        monkeypatch.delenv("SNIPER_OPERATOR_SECRET", raising=False)
    else:
        monkeypatch.setenv("SNIPER_OPERATOR_SECRET", secret)
    for name in ["main", "config"]:
        sys.modules.pop(name, None)
    module = importlib.import_module("main")
    return TestClient(module.app), module


def login(client: TestClient, secret: str):
    response = client.post("/auth/session", json={"operator_secret": secret})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_read_only_mode_denies_private_and_mutating_routes(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path)
    assert client.get("/health").status_code == 200
    denied = client.get("/copy/state")
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "operator_auth_not_configured"
    assert client.post("/alerts", json={"direction": "above", "target": 100}).status_code == 503


def test_cookie_login_requires_csrf_for_writes(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, _ = load_app(monkeypatch, tmp_path, secret=secret)
    csrf = login(client, secret)
    assert client.get("/copy/state").status_code == 200
    missing = client.post("/alerts", json={"direction": "above", "target": 100})
    assert missing.status_code == 403
    accepted = client.post(
        "/alerts",
        json={"direction": "above", "target": 100},
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 200


def test_bearer_login_does_not_require_csrf(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, module = load_app(monkeypatch, tmp_path, secret=secret)
    issued = module.app.state.security.sessions.create(secret, "test-client")
    response = client.post(
        "/alerts",
        json={"direction": "above", "target": 101},
        headers={"Authorization": f"Bearer {issued.token}"},
    )
    assert response.status_code == 200


def test_login_rate_limit(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path, secret="correct horse battery staple")
    for _ in range(5):
        assert client.post("/auth/session", json={"operator_secret": "wrong"}).status_code == 401
    denied = client.post("/auth/session", json={"operator_secret": "wrong"})
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) > 0
```

Run:

```bash
pytest tests/test_security_api.py -q
```

Expected: failures because auth routes and dependencies are absent.

- [ ] **Step 2: Implement reusable FastAPI security dependencies**

Create `security_dependencies.py` with this public shape:

```python
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from services.live_authorization import LiveChallengeStore
from services.rate_limit import FixedWindowLimiter
from services.security import SessionPrincipal, SessionStore
from services.security_audit import SecurityAuditLog

SESSION_COOKIE = "sniper_operator"


@dataclass
class SecurityRuntime:
    sessions: SessionStore
    limiter: FixedWindowLimiter
    audit: SecurityAuditLog
    challenges: LiveChallengeStore


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    agent = request.headers.get("user-agent", "")[:120]
    return f"{host}|{agent}"


def api_error(status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def runtime(request: Request) -> SecurityRuntime:
    return request.app.state.security


def raw_token(request: Request) -> tuple[str, str]:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), "bearer"
    return request.cookies.get(SESSION_COOKIE, ""), "cookie"


def optional_operator(request: Request) -> SessionPrincipal | None:
    token, _ = raw_token(request)
    return runtime(request).sessions.authenticate(token)


def require_operator(request: Request) -> SessionPrincipal:
    security = runtime(request)
    if not security.sessions.configured:
        raise api_error(503, "operator_auth_not_configured", "Operator authentication is not configured.")
    principal = optional_operator(request)
    if principal is None:
        raise api_error(401, "operator_auth_required", "An authenticated operator session is required.")
    return principal


def require_csrf(request: Request, principal: SessionPrincipal) -> None:
    _, auth_mode = raw_token(request)
    if auth_mode == "bearer":
        return
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or not __import__("secrets").compare_digest(supplied, principal.csrf_token):
        raise api_error(403, "csrf_failed", "A valid CSRF token is required.")


def require_write(request: Request) -> SessionPrincipal:
    principal = require_operator(request)
    require_csrf(request, principal)
    decision = runtime(request).limiter.check(f"write:{principal.session_id}", 60, 60)
    if not decision.allowed:
        raise api_error(429, "rate_limited", "Write rate limit exceeded.", headers={"Retry-After": str(decision.retry_after)})
    return principal
```

- [ ] **Step 3: Initialize the security runtime and stable request IDs**

In `main.py`:

1. Import `AuthSessionIn`, `AuthSessionOut`, `AuthStatusOut`, `LiveChallengeIn`, and `LiveChallengeOut`.
2. Import the security services and dependency helpers.
3. Build the runtime before application startup:

```python
_operator_secret = _settings.operator_secret.get_secret_value() if _settings.operator_secret else None
_security = SecurityRuntime(
    sessions=SessionStore(
        _operator_secret,
        _settings.session_ttl_seconds,
        _settings.session_idle_seconds,
        _settings.max_operator_sessions,
    ),
    limiter=FixedWindowLimiter(),
    audit=SecurityAuditLog(_settings.audit_log_path),
    challenges=LiveChallengeStore(ttl_seconds=60),
)
app.state.security = _security
```

4. Change the timing middleware so it assigns `request.state.request_id` before `call_next` and always uses that value in the response header.
5. Add an `HTTPException` handler that converts dictionary details to this envelope without leaking raw internals:

```python
@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_failed", "message": str(exc.detail)}
    return ORJSONResponse(
        status_code=exc.status_code,
        content={"error": {**detail, "request_id": request_id(request)}},
        headers=exc.headers,
    )
```

- [ ] **Step 4: Add authentication and challenge routes**

Add these routes before private application routes:

```python
@app.get("/auth/status", response_model=AuthStatusOut)
async def auth_status(request: Request):
    principal = optional_operator(request)
    return AuthStatusOut(
        configured=_security.sessions.configured,
        authenticated=principal is not None,
        mode=_settings.bind_mode,
        session_id=principal.session_id if principal else None,
        expires_at=principal.expires_at if principal else None,
    )


@app.post("/auth/session", response_model=AuthSessionOut)
async def auth_create(payload: AuthSessionIn, request: Request):
    key = client_key(request)
    decision = _security.limiter.check(f"login:{key}", 5, 600)
    if not decision.allowed:
        _security.audit.append("auth.rate_limited", "denied", request_id=request_id(request))
        raise api_error(429, "rate_limited", "Too many authentication attempts.", headers={"Retry-After": str(decision.retry_after)})
    if not _security.sessions.configured:
        raise api_error(503, "operator_auth_not_configured", "Operator authentication is not configured.")
    try:
        issued = _security.sessions.create(payload.operator_secret, key)
    except AuthenticationError:
        _security.audit.append("auth.failure", "denied", request_id=request_id(request))
        raise api_error(401, "invalid_operator_secret", "The operator secret was not accepted.")
    except SessionCapacityError:
        raise api_error(503, "session_capacity_reached", "Operator session capacity has been reached.")
    _security.audit.append("auth.success", "allowed", request_id=request_id(request), session_id=issued.principal.session_id)
    response = ORJSONResponse(AuthSessionOut(
        authenticated=True,
        csrf_token=issued.principal.csrf_token,
        session_id=issued.principal.session_id,
        expires_at=issued.principal.expires_at,
    ).model_dump())
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=_settings.session_ttl_seconds,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.delete("/auth/session")
async def auth_delete(request: Request):
    principal = require_write(request)
    token, _ = raw_token(request)
    revoked = _security.sessions.revoke(token)
    _security.audit.append("auth.logout", "allowed", request_id=request_id(request), session_id=principal.session_id)
    response = ORJSONResponse({"revoked": revoked})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/auth/live-challenges", response_model=LiveChallengeOut)
async def live_challenge_create(payload: LiveChallengeIn, request: Request):
    principal = require_write(request)
    decision = _security.limiter.check(f"challenge:{principal.session_id}", 5, 60)
    if not decision.allowed:
        raise api_error(429, "rate_limited", "Live-challenge rate limit exceeded.", headers={"Retry-After": str(decision.retry_after)})
    challenge = _security.challenges.create(principal.session_id, payload.action)
    _security.audit.append(
        "challenge.created",
        "allowed",
        request_id=request_id(request),
        session_id=principal.session_id,
        action=payload.action,
        action_digest=challenge.action_digest,
    )
    return LiveChallengeOut(**challenge.__dict__)
```

- [ ] **Step 5: Apply route policy**

Use `request: Request` plus `require_operator` or `require_write` in `main.py`:

- Private reads: `GET /alerts`, `GET /portfolio/paper`, `GET /copy/state`, `GET /sessions` use `require_operator(request)`.
- Paid call: `POST /grok/comment` uses `require_write(request)`.
- Mutations: alert create/delete, session delete, copy leader/follower/signal/copy routes use `require_write(request)`.
- `POST /research/search` uses `require_write(request)` because the current pipeline maintains warm session state.
- Public read-only market, news, risk calculation, DEX discovery, caller-supplied public-wallet inspection, health, readiness, static UI, and trader prompt remain public.
- `GET /integrations` returns the safe catalog publicly but omits probe details unless authenticated.

For each protected denial and accepted mutation, append an audit event with request ID, session ID, resource summary, and outcome. Do not pass `payload.model_dump()` directly when it contains confirmation text; construct a safe action dictionary.

- [ ] **Step 6: Verify and commit**

Run:

```bash
pytest tests/test_security_api.py -q
pytest tests/test_core.py tests/test_connections.py tests/test_grok_alerts.py tests/test_integrations.py tests/test_risk.py tests/test_volume.py -q
```

Expected: all tests pass without external network calls.

Commit:

```bash
git add security_dependencies.py tests/test_security_api.py main.py
git commit -m "feat: enforce operator authentication on private routes"
```

---

### Task 6: Bind real copy-trade execution to consumed challenges

**Files:**
- Modify: `main.py`
- Modify: `services/copy_trade.py`
- Modify: `tests/test_security_api.py`

**Interfaces:**
- Consumes: `LiveChallengeStore.consume()`, authenticated principal, `SignalCreate.challenge_id`, and `CopySignalIn.challenge_id`.
- Produces: live execution paths that cannot be authorized by the legacy fixed phrase.

- [ ] **Step 1: Add failing live-authorization API tests**

Append tests that replace network-dependent execution with a fake engine:

```python
class FakeEngine:
    def __init__(self):
        self.live_calls = 0

    def emit_signal(self, *args, **kwargs):
        if kwargs.get("confirm_live"):
            self.live_calls += 1
        return {"signal": {"signal_id": "S_1"}, "fills": []}

    def copy_signal(self, signal_id, confirm_live=False):
        if confirm_live:
            self.live_calls += 1
        return []


def live_action(payload):
    return {
        "operation": "copy.emit_signal",
        "leader_id": payload["leader_id"],
        "instrument": payload["instrument"],
        "side": payload["side"],
        "order_type": payload["order_type"],
        "quantity": payload.get("quantity"),
        "notional_usd": payload.get("notional_usd"),
        "price": payload.get("price"),
    }


def test_legacy_phrase_cannot_authorize_live(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, module = load_app(monkeypatch, tmp_path, secret=secret)
    csrf = login(client, secret)
    fake = FakeEngine()
    monkeypatch.setattr(module, "get_engine", lambda: fake)
    payload = {
        "leader_id": "L_1", "instrument": "BTC_USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usd": 25, "confirm_live": True,
        "confirmation_text": "CONFIRM LIVE",
    }
    response = client.post("/copy/signals", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "live_challenge_required"
    assert fake.live_calls == 0


def test_matching_challenge_is_consumed_before_live_call(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, module = load_app(monkeypatch, tmp_path, secret=secret)
    csrf = login(client, secret)
    fake = FakeEngine()
    monkeypatch.setattr(module, "get_engine", lambda: fake)
    payload = {
        "leader_id": "L_1", "instrument": "BTC_USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usd": 25, "confirm_live": True,
    }
    challenge = client.post(
        "/auth/live-challenges",
        json={"action": live_action(payload)},
        headers={"X-CSRF-Token": csrf},
    ).json()
    payload.update({
        "challenge_id": challenge["challenge_id"],
        "confirmation_text": challenge["confirmation_phrase"],
    })
    first = client.post("/copy/signals", json=payload, headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200
    assert fake.live_calls == 1
    replay = client.post("/copy/signals", json=payload, headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 403
    assert fake.live_calls == 1


def test_modified_payload_invalidates_challenge(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, module = load_app(monkeypatch, tmp_path, secret=secret)
    csrf = login(client, secret)
    fake = FakeEngine()
    monkeypatch.setattr(module, "get_engine", lambda: fake)
    payload = {
        "leader_id": "L_1", "instrument": "BTC_USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usd": 25, "confirm_live": True,
    }
    challenge = client.post(
        "/auth/live-challenges",
        json={"action": live_action(payload)},
        headers={"X-CSRF-Token": csrf},
    ).json()
    payload.update({
        "notional_usd": 50,
        "challenge_id": challenge["challenge_id"],
        "confirmation_text": challenge["confirmation_phrase"],
    })
    response = client.post("/copy/signals", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403
    assert fake.live_calls == 0
```

Run the three tests and confirm they fail before route changes.

- [ ] **Step 2: Centralize exact action construction**

Add helpers in `main.py`:

```python
def emit_signal_action(payload: SignalCreate) -> dict:
    return {
        "operation": "copy.emit_signal",
        "leader_id": payload.leader_id.strip(),
        "instrument": free_market.normalize_instrument(payload.instrument),
        "side": payload.side.upper().strip(),
        "order_type": payload.order_type.upper().strip(),
        "quantity": payload.quantity,
        "notional_usd": payload.notional_usd,
        "price": payload.price,
    }


def copy_existing_action(signal_id: str) -> dict:
    return {"operation": "copy.copy_signal", "signal_id": signal_id.strip()}
```

The UI and API challenge request must use these exact fields. Note and `auto_copy` are excluded because they do not alter the exchange order parameters; `confirm_live`, challenge ID, and phrase are authorization metadata and are also excluded.

- [ ] **Step 3: Consume authorization before calling the engine**

In both live-capable routes:

```python
principal = require_write(request)
confirm = payload.confirm_live
if confirm:
    if not payload.challenge_id or not payload.confirmation_text:
        raise api_error(403, "live_challenge_required", "A valid one-time live authorization is required.")
    if not _security.audit.healthy:
        raise api_error(503, "audit_integrity_failed", "Live execution is disabled because audit integrity is unavailable.")
    action = emit_signal_action(payload)  # or copy_existing_action(signal_id)
    try:
        digest = _security.challenges.consume(
            principal.session_id,
            payload.challenge_id,
            payload.confirmation_text,
            action,
        )
    except ChallengeError as exc:
        _security.audit.append(
            "challenge.denied", "denied", request_id=request_id(request),
            session_id=principal.session_id, action=action,
        )
        raise api_error(403, "live_challenge_invalid", str(exc))
    _security.audit.append(
        "challenge.consumed", "allowed", request_id=request_id(request),
        session_id=principal.session_id, action=action, action_digest=digest,
    )
```

Only after this block may `get_engine().emit_signal(..., confirm_live=True)` or `.copy_signal(..., confirm_live=True)` run. A provider failure does not restore the challenge.

- [ ] **Step 4: Remove phrase authority from the copy engine boundary**

Keep `confirm_live: bool` in `services/copy_trade.py` because it selects dry-run versus real adapter execution, but update its module documentation and comments to state:

- the engine trusts only the authenticated route layer to pass `confirm_live=True`
- direct library callers are responsible for an equivalent authorization boundary
- the fixed phrase is not checked inside the engine and is not an authorization mechanism

Do not add secrets, cookies, or FastAPI imports to the engine.

- [ ] **Step 5: Verify and commit**

Run:

```bash
pytest tests/test_security_api.py -q
pytest tests/test_core.py tests/test_risk.py -q
```

Expected: legacy phrase, replay, cross-session, and modified-payload paths are denied; paper behavior still passes.

Commit:

```bash
git add main.py services/copy_trade.py tests/test_security_api.py
git commit -m "feat: require one-time authorization for live copy trades"
```

---

### Task 7: Bound SSE streams and gate paid Grok usage

**Files:**
- Create: `services/stream_limits.py`
- Create: `tests/test_stream_limits.py`
- Modify: `services/grok_live.py`
- Modify: `main.py`
- Modify: `tests/test_grok_alerts.py`

**Interfaces:**
- Produces: `StreamLeasePool.acquire()` context manager and `generate_live_comment(..., allow_remote=False)`.
- Consumes: client keys, stream limits from settings, and optional authenticated principals.

- [ ] **Step 1: Write failing stream and remote-gate tests**

Create `tests/test_stream_limits.py`:

```python
import pytest

from services.stream_limits import StreamLimitError, StreamLeasePool


def test_stream_limits_and_release():
    pool = StreamLeasePool(max_per_client=1, max_total=2)
    with pool.acquire("client-a"):
        with pytest.raises(StreamLimitError):
            with pool.acquire("client-a"):
                pass
        with pool.acquire("client-b"):
            assert pool.total == 2
    assert pool.total == 0
    with pool.acquire("client-a"):
        assert pool.total == 1
```

Append to `tests/test_grok_alerts.py`:

```python
def test_remote_grok_requires_explicit_allow(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "configured-but-must-not-be-used")
    called = False

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("remote client must not be created")

    monkeypatch.setattr(grok_live.httpx, "Client", ForbiddenClient)
    result = grok_live.generate_live_comment({"instrument": "BTC_USDT"}, allow_remote=False)
    assert result["source"] == "local_fallback"
    assert called is False
```

Run both files and confirm failures.

- [ ] **Step 2: Implement stream leases**

Create `services/stream_limits.py`:

```python
from __future__ import annotations

import threading
from contextlib import contextmanager


class StreamLimitError(RuntimeError):
    pass


class StreamLeasePool:
    def __init__(self, max_per_client: int, max_total: int):
        self.max_per_client = max_per_client
        self.max_total = max_total
        self._clients: dict[str, int] = {}
        self.total = 0
        self._lock = threading.Lock()

    @contextmanager
    def acquire(self, client_key: str):
        with self._lock:
            client_count = self._clients.get(client_key, 0)
            if self.total >= self.max_total or client_count >= self.max_per_client:
                raise StreamLimitError("SSE connection limit reached")
            self.total += 1
            self._clients[client_key] = client_count + 1
        try:
            yield
        finally:
            with self._lock:
                self.total = max(0, self.total - 1)
                remaining = self._clients.get(client_key, 1) - 1
                if remaining <= 0:
                    self._clients.pop(client_key, None)
                else:
                    self._clients[client_key] = remaining
```

Add `streams: StreamLeasePool` to `SecurityRuntime` and initialize it from settings.

- [ ] **Step 3: Make remote model spending explicit**

Change the Grok signature:

```python
def generate_live_comment(
    context: Dict[str, Any],
    timeout: float = 25.0,
    *,
    allow_remote: bool = False,
) -> Dict[str, Any]:
```

Before creating the HTTP client:

```python
if not key or not allow_remote:
    text = _local_brief(context)
    return {
        "text": text,
        "source": "local_fallback",
        "model": None,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "configured": bool(key),
        "remote_allowed": allow_remote,
    }
```

`POST /grok/comment` passes `allow_remote=True` only after `require_write`. `GET /live/deck` obtains `principal = optional_operator(request)` before constructing its generator and passes `allow_remote=principal is not None`. Unauthenticated live decks remain useful but deterministic and free.

- [ ] **Step 4: Wrap every SSE generator in a lease**

Add `request: Request` to `/market/stream`, `/sniper/live`, and `/live/deck`. Compute `stream_key = client_key(request)` before returning `StreamingResponse`. Inside each async generator:

```python
async def gen() -> AsyncIterator[str]:
    try:
        with _security.streams.acquire(stream_key):
            # existing hello and polling loop remain inside this block
            ...
    except StreamLimitError:
        yield 'event: error\ndata: {"error":"stream_limit_reached"}\n\n'
```

Replace the ellipsis with the route's existing generator body during implementation; do not duplicate or reorder market-analysis logic. The lease must surround the entire loop so cancellation and disconnect trigger the context manager's `finally` release.

- [ ] **Step 5: Verify and commit**

Run:

```bash
pytest tests/test_stream_limits.py tests/test_grok_alerts.py tests/test_security_api.py -q
```

Expected: stream counts release, excess streams are rejected, and unauthenticated code never constructs an xAI client.

Commit:

```bash
git add services/stream_limits.py services/grok_live.py main.py security_dependencies.py tests/test_stream_limits.py tests/test_grok_alerts.py
git commit -m "feat: bound live streams and gate paid commentary"
```

---

### Task 8: Operator security panel and challenge review UI

**Files:**
- Modify: `ui/index.html`
- Modify: `tests/test_security_api.py`

**Interfaces:**
- Consumes: `/auth/status`, `/auth/session`, `DELETE /auth/session`, `/auth/live-challenges`, CSRF header rules, and challenge fields.
- Produces: honest `READ ONLY`, `OPERATOR`, and `LIVE CHALLENGE ACTIVE` states; all existing writes use a single authenticated fetch wrapper.

- [ ] **Step 1: Add a UI smoke test for security controls**

Append:

```python
def test_ui_contains_security_controls(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path, secret="correct horse battery staple")
    html = client.get("/").text
    assert 'id="securityStatus"' in html
    assert 'id="operatorSecret"' in html
    assert 'id="operatorLogin"' in html
    assert 'id="operatorLogout"' in html
    assert 'id="liveChallengePhrase"' in html
    assert "X-CSRF-Token" in html
    assert "localStorage.setItem('sniper_operator" not in html
```

Run the test and confirm failure.

- [ ] **Step 2: Add the compact security panel**

In `ui/index.html`, place this card near the existing Connections or header controls:

```html
<section class="card" id="operatorSecurity">
  <div class="section-head">
    <div>
      <p class="eyebrow">Operator security</p>
      <h3>Execution authority</h3>
    </div>
    <span class="pill" id="securityStatus">READ ONLY</span>
  </div>
  <p class="hint" id="securityHint">Private state and trade controls require an authenticated local operator.</p>
  <div class="field">
    <label for="operatorSecret">Operator secret</label>
    <input id="operatorSecret" type="password" autocomplete="current-password" maxlength="512" />
  </div>
  <div class="row">
    <button class="primary" id="operatorLogin" type="button">Unlock operator mode</button>
    <button class="ghost" id="operatorLogout" type="button" hidden>Lock session</button>
  </div>
  <div id="liveChallengeCard" hidden>
    <p class="hint" id="liveActionSummary"></p>
    <label for="liveChallengePhrase">Type the one-time phrase exactly</label>
    <input id="liveChallengePhrase" autocomplete="off" maxlength="64" />
    <p class="hint" id="liveChallengeExpiry"></p>
  </div>
</section>
```

Do not persist the operator secret, cookie value, CSRF token, challenge phrase, or challenge ID in `localStorage` or IndexedDB.

- [ ] **Step 3: Add one authenticated request wrapper**

In the existing script state:

```javascript
const security = {
  authenticated: false,
  configured: false,
  csrfToken: "",
  challenge: null,
};

async function apiFetch(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && security.csrfToken) {
    headers.set("X-CSRF-Token", security.csrfToken);
  }
  const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || { code: "request_failed", message: `Request failed (${response.status})` };
    throw new Error(`${error.code}: ${error.message}`);
  }
  return payload;
}
```

Replace every state-changing `fetch()` call with `apiFetch()`. Read-only calls may also use it for consistency. Ensure event streams continue using same-origin cookies automatically.

- [ ] **Step 4: Implement login, logout, and honest control states**

```javascript
async function refreshSecurityStatus() {
  const status = await apiFetch("/auth/status");
  security.configured = status.configured;
  security.authenticated = status.authenticated;
  document.getElementById("securityStatus").textContent = status.authenticated ? "OPERATOR" : "READ ONLY";
  document.getElementById("operatorLogin").hidden = status.authenticated;
  document.getElementById("operatorLogout").hidden = !status.authenticated;
  document.querySelectorAll("[data-requires-operator]").forEach((node) => {
    node.disabled = !status.authenticated;
  });
}

async function loginOperator() {
  const input = document.getElementById("operatorSecret");
  const result = await apiFetch("/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator_secret: input.value }),
  });
  security.csrfToken = result.csrf_token;
  input.value = "";
  await refreshSecurityStatus();
}

async function logoutOperator() {
  await apiFetch("/auth/session", { method: "DELETE" });
  security.csrfToken = "";
  security.challenge = null;
  await refreshSecurityStatus();
}
```

Attach click handlers and call `refreshSecurityStatus()` at startup. Mark alert, session, paper-copy, paid-Grok, and live-action controls with `data-requires-operator`.

- [ ] **Step 5: Implement exact action review and challenge use**

Before any live submission, build the exact action object matching Task 6, display a human-readable summary, and request a challenge:

```javascript
async function requestLiveChallenge(action) {
  const challenge = await apiFetch("/auth/live-challenges", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  security.challenge = challenge;
  document.getElementById("liveChallengeCard").hidden = false;
  document.getElementById("liveActionSummary").textContent = challenge.action_summary;
  document.getElementById("liveChallengeExpiry").textContent = `Expires ${new Date(challenge.expires_at * 1000).toLocaleTimeString()}`;
  document.getElementById("securityStatus").textContent = "LIVE CHALLENGE ACTIVE";
  return challenge;
}
```

The final live request includes:

```javascript
{
  ...tradePayload,
  confirm_live: true,
  challenge_id: security.challenge.challenge_id,
  confirmation_text: document.getElementById("liveChallengePhrase").value,
}
```

Immediately after any attempt, success or failure:

```javascript
security.challenge = null;
document.getElementById("liveChallengePhrase").value = "";
document.getElementById("liveChallengeCard").hidden = true;
await refreshSecurityStatus();
```

Paper trading must never request a live challenge.

- [ ] **Step 6: Verify and commit**

Run:

```bash
pytest tests/test_security_api.py -q
```

Manually verify in a browser:

1. No secret: `READ ONLY`; writes disabled; market deck works.
2. Correct secret: `OPERATOR`; private state and paper actions work.
3. Live action: exact action review appears; fixed phrase alone fails; challenge phrase succeeds once.
4. Page refresh: HttpOnly cookie restores authentication status, but CSRF token is intentionally lost; user logs in again before the next write rather than reading a token from storage.

Commit:

```bash
git add ui/index.html tests/test_security_api.py
git commit -m "feat: add operator security controls to trading UI"
```

---

### Task 9: Stable errors, CI security gates, and truthful documentation

**Files:**
- Modify: `main.py`
- Create: `.github/workflows/security.yml`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `tests/test_security_api.py`

**Interfaces:**
- Consumes: all security services and route policies from Tasks 1-8.
- Produces: merge-blocking verification and documentation matching actual behavior.

- [ ] **Step 1: Add error-redaction and audit-failure tests**

Append:

```python
def test_security_errors_use_stable_envelope(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path, secret="correct horse battery staple")
    response = client.get("/copy/state")
    assert response.status_code == 401
    payload = response.json()["error"]
    assert payload["code"] == "operator_auth_required"
    assert payload["request_id"]
    assert "Traceback" not in response.text


def test_audit_failure_disables_live_but_not_health(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, module = load_app(monkeypatch, tmp_path, secret=secret)
    csrf = login(client, secret)
    module.app.state.security.audit.healthy = False
    assert client.get("/health").status_code == 200
    payload = {
        "leader_id": "L_1", "instrument": "BTC_USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usd": 25, "confirm_live": True,
        "challenge_id": "LC_fake", "confirmation_text": "CONFIRM LIVE FAKE",
    }
    response = client.post("/copy/signals", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "audit_integrity_failed"
```

- [ ] **Step 2: Remove raw exception leakage from protected and upstream routes**

For protected, wallet, DEX, market, news, and model routes, log the original exception with the request ID and return a stable code such as:

- `market_upstream_failed`
- `news_upstream_failed`
- `wallet_lookup_failed`
- `dex_lookup_failed`
- `model_commentary_failed`
- `copy_trade_failed`

Do not include `str(exc)` in the client message. Keep validation errors narrowly worded when they contain no provider payload, filesystem path, or secret. Add a final application exception handler that logs the request ID and returns:

```json
{
  "error": {
    "code": "internal_error",
    "message": "The request could not be completed.",
    "request_id": "..."
  }
}
```

- [ ] **Step 3: Add the GitHub Actions workflow**

Create `.github/workflows/security.yml`:

```yaml
name: Security and tests

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -r requirements-dev.txt
      - run: pytest -q
      - run: ruff check .
      - run: ruff format --check .
      - run: bandit -q -r agents blockchain jspace services main.py config.py schemas.py security_dependencies.py
      - run: pip-audit -r requirements-core.txt

  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

If Ruff reports existing unrelated formatting debt, format only files touched by this plan and add narrow per-file exclusions in `pyproject.toml`; do not blanket-ignore entire rule families. If no `pyproject.toml` exists, create one with target version `py310`, line length `100`, and test-file allowances only for `S101` in Bandit rather than Ruff.

- [ ] **Step 4: Update README with implemented commands and boundaries**

Add these exact operational sections:

```markdown
## Secure local start

Sniper Trades binds to `127.0.0.1:8000` by default. Without `SNIPER_OPERATOR_SECRET`, the app starts read-only: public market intelligence remains available, while private state, paid model calls, alerts, paper-copy changes, session changes, and live actions are denied.

Generate a secret and start operator mode:

```bash
export SNIPER_OPERATOR_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
./scripts/run.sh
```

## Trusted-LAN phone access

LAN mode is explicit and requires an exact phone-facing origin:

```bash
export SNIPER_BIND_MODE=lan
export SNIPER_CORS_ORIGINS=http://192.168.1.20:8000
export SNIPER_OPERATOR_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
./scripts/run.sh
```

LAN mode uses ordinary HTTP unless you provide TLS separately. Do not port-forward it, expose it to the public internet, or use it on an untrusted network.

## Live authorization

`CONFIRM LIVE` by itself no longer authorizes a real order. Live execution requires an authenticated operator session and a short-lived, single-use challenge bound to the exact order payload. The challenge is consumed before the exchange adapter runs, including when the downstream order fails.
```

Also document the audit path, read-only fallback, private route categories, logout behavior, paid Grok gate, and migration from v6.4.

- [ ] **Step 5: Run full verification**

Run:

```bash
pytest -q
ruff check .
ruff format --check .
bandit -q -r agents blockchain jspace services main.py config.py schemas.py security_dependencies.py
pip-audit -r requirements-core.txt
python -m compileall agents blockchain jspace services main.py config.py schemas.py security_dependencies.py
```

Expected:

- all tests pass
- Ruff exits zero
- formatting check exits zero
- Bandit has no unreviewed high- or medium-severity findings
- pip-audit has no known vulnerability without a documented, version-pinned remediation decision
- compileall exits zero

- [ ] **Step 6: Commit**

```bash
git add main.py README.md .env.example .github/workflows/security.yml tests/test_security_api.py pyproject.toml
git commit -m "ci: enforce security verification and document safe operation"
```

If `pyproject.toml` was not needed, omit it from `git add`.

---

### Task 10: Final review, pull request, and merge gate

**Files:**
- Review all files changed by Tasks 1-9.

**Interfaces:**
- Consumes: complete implementation and test evidence.
- Produces: a reviewable pull request with no unsupported security claims.

- [ ] **Step 1: Review the branch diff for scope and secret leakage**

Run:

```bash
git diff main...HEAD --stat
git diff main...HEAD -- . ':!docs/superpowers/plans/*'
git grep -nE '(sk-[A-Za-z0-9]|xai-[A-Za-z0-9]|seed phrase|private key|SNIPER_OPERATOR_SECRET=.{20,})' HEAD -- . ':!.env.example'
```

Expected: only planned security, tests, UI, CI, and documentation changes; grep returns no committed credential.

- [ ] **Step 2: Re-run the complete verification suite from a clean environment**

```bash
python -m venv .venv-review
source .venv-review/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
pytest -q
ruff check .
ruff format --check .
bandit -q -r agents blockchain jspace services main.py config.py schemas.py security_dependencies.py
pip-audit -r requirements-core.txt
```

Expected: every command exits zero.

- [ ] **Step 3: Confirm acceptance criteria manually**

Verify these exact behaviors:

1. Default launch listens only on `127.0.0.1`.
2. LAN launch fails with no secret, a short secret, wildcard CORS, or local-only origins.
3. Read-only launch keeps public market and health routes available.
4. Anonymous private reads and all writes are denied.
5. Cookie writes fail without CSRF; Bearer writes succeed without CSRF.
6. Login, challenge, write, live-attempt, and denial events are present in the verified audit chain without secrets.
7. `CONFIRM LIVE` alone fails.
8. Matching live challenge works once and is consumed before adapter invocation.
9. Replay, altered action, wrong session, expired phrase, or audit failure denies live execution.
10. Unauthenticated live decks never spend a configured xAI key.
11. SSE connection counts are bounded and released after disconnect.
12. UI never stores operator or authorization material in persistent browser storage.

- [ ] **Step 4: Open a pull request**

```bash
git push -u origin feature/security-foundation
gh pr create \
  --base main \
  --head feature/security-foundation \
  --title "Security foundation for Sniper Trades" \
  --body "Implements secure localhost defaults, explicit LAN validation, operator sessions, CSRF, private-route policy, one-time action-bound live authorization, hash-chained audit, bounded SSE, paid-Grok gating, tests, and CI. LAN HTTP remains trusted-network convenience and is not presented as protection against an active network attacker."
```

- [ ] **Step 5: Run two-stage review before merge**

1. Run a requirements review against `docs/superpowers/specs/2026-07-20-sniper-trades-security-foundation-design.md`.
2. Run CodeRabbit or an equivalent code-quality/security review on the pull request.
3. Resolve valid findings with focused commits and rerun the full verification suite.
4. Do not merge while required CI is failing, the audit/live tests are skipped, or the PR description overstates LAN security, MFA, financial safety, or production readiness.
