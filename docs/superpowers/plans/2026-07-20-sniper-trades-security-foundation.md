# Sniper Trades Security Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Sniper Trades local-only by default, authenticate private and mutating operations, bind every real trade to one short-lived action-specific authorization, constrain local abuse, and prove the behavior through deterministic tests and CI.

**Architecture:** Keep the current single-process FastAPI and copy-trade architecture. Add focused standard-library security services for sessions, limits, audit integrity, live challenges, and stream leases; expose them through reusable FastAPI dependencies; then apply those dependencies to existing routes without moving trading logic into the authentication layer.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, pydantic-settings, pytest, FastAPI TestClient/httpx, standard-library `secrets`, `hashlib`, `threading`, `pathlib`, JSONL, GitHub Actions, Ruff, Bandit, pip-audit, Gitleaks.

## Global Constraints

- Default bind address is exactly `127.0.0.1`.
- LAN exposure requires `SNIPER_BIND_MODE=lan`, an operator secret of at least 20 characters, and exact non-local CORS origins.
- LAN mode over plain HTTP is trusted-network convenience, not protection against an active network attacker.
- Public market intelligence remains usable without authentication.
- Private state, paid model calls, warm research sessions, and every mutation require an operator session.
- Cookie-authenticated writes require `X-CSRF-Token`; Bearer-authenticated writes do not.
- Browser sessions use an HttpOnly, SameSite=Strict cookie. Browser code never persists operator or authorization material.
- Session absolute lifetime defaults to 1,800 seconds; idle timeout defaults to 900 seconds; maximum sessions defaults to 8.
- `CONFIRM LIVE` by itself never authorizes a real order.
- A live challenge is single-use, session-bound, action-digest-bound, and consumed before the exchange adapter runs.
- Challenge authorization is not MFA, biometric authentication, passkey protection, or defense against a stolen authenticated session.
- Audit records exclude secrets, raw tokens, CSRF values, confirmation phrases, API keys, seed phrases, private keys, and unrestricted request bodies.
- Audit-chain failure, challenge-store failure, or authorization failure denies live execution while preserving read-only use.
- SSE defaults are 3 streams per client key and 12 total.
- Security primitives add no runtime dependency beyond the existing lean stack.
- Existing risk, alert, market, Grok, integration, copy-trade, and core tests must remain green.

---

## File Map

### Create

- `services/security.py`: operator sessions.
- `services/rate_limit.py`: fixed-window request limits.
- `services/security_audit.py`: redacted hash-chained JSONL audit.
- `services/live_authorization.py`: canonical action digests and one-time challenges.
- `services/stream_limits.py`: bounded SSE leases.
- `security_dependencies.py`: FastAPI authentication, CSRF, client identity, and stable security errors.
- `tests/test_security_config.py`
- `tests/test_security_sessions.py`
- `tests/test_rate_limit.py`
- `tests/test_security_audit.py`
- `tests/test_live_authorization.py`
- `tests/test_security_api.py`
- `tests/test_stream_limits.py`
- `.env.example`
- `requirements-dev.txt`
- `.github/workflows/security.yml`

### Modify

- `config.py`
- `schemas.py`
- `main.py`
- `services/grok_live.py`
- `services/copy_trade.py`
- `ui/index.html`
- `README.md`

---

### Task 1: Secure operating modes

**Files:**
- Modify: `config.py`
- Create: `tests/test_security_config.py`
- Create: `.env.example`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: `Settings.bind_mode`, `Settings.host`, `Settings.operator_secret`, `Settings.read_only`, `Settings.cors_origin_list`, session limits, stream limits, and audit path.
- Consumes: existing `SNIPER_` environment prefix and `get_settings()` cache.

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_security_config.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import Settings


def settings(**values):
    return Settings(_env_file=None, **values)


def test_local_defaults_are_loopback_and_read_only():
    current = settings()
    assert current.bind_mode == "local"
    assert current.host == "127.0.0.1"
    assert current.cors_origin_list == [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]
    assert current.read_only is True


def test_lan_requires_twenty_character_secret():
    with pytest.raises(ValidationError, match="at least 20 characters"):
        settings(
            bind_mode="lan",
            operator_secret="short",
            cors_origins="http://192.168.1.20:8000",
        )


def test_lan_rejects_wildcard_cors():
    with pytest.raises(ValidationError, match="exact non-wildcard origins"):
        settings(
            bind_mode="lan",
            operator_secret="x" * 20,
            cors_origins="*",
        )


def test_lan_rejects_local_only_origins():
    with pytest.raises(ValidationError, match="explicit LAN origin"):
        settings(
            bind_mode="lan",
            operator_secret="x" * 20,
            cors_origins="http://127.0.0.1:8000,http://localhost:8000",
        )


def test_valid_lan_mode_derives_wildcard_bind():
    current = settings(
        bind_mode="lan",
        operator_secret="x" * 20,
        cors_origins="http://192.168.1.20:8000",
    )
    assert current.host == "0.0.0.0"
    assert current.read_only is False
```

- [ ] **Step 2: Add development dependencies and verify RED**

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

Expected: tests fail because the security settings are absent.

- [ ] **Step 3: Implement validated settings**

Replace `config.py` with:

```python
"""App configuration with secure, explicit network modes."""

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
SNIPER_BIND_MODE=local
SNIPER_CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

# Leave unset for read-only mode.
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
# SNIPER_OPERATOR_SECRET=replace-with-generated-secret

# Trusted-LAN phone access, still plain HTTP:
# SNIPER_BIND_MODE=lan
# SNIPER_CORS_ORIGINS=http://192.168.1.20:8000
# SNIPER_OPERATOR_SECRET=replace-with-at-least-20-characters

# Optional paid xAI commentary. Never commit the real value.
# SNIPER_XAI_API_KEY=
SNIPER_XAI_MODEL=grok-4-1-fast-non-reasoning
```

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/test_security_config.py -q
python -m compileall config.py
git add config.py tests/test_security_config.py .env.example requirements-dev.txt
git commit -m "feat: enforce secure operating modes"
```

Expected: tests pass and compileall exits zero.

---

### Task 2: Session and rate-limit primitives

**Files:**
- Create: `services/security.py`
- Create: `services/rate_limit.py`
- Create: `tests/test_security_sessions.py`
- Create: `tests/test_rate_limit.py`

**Interfaces:**
- Produces: `SessionStore`, `IssuedSession`, `SessionPrincipal`, `FixedWindowLimiter`, and `RateLimitDecision`.
- Consumes: limits from `Settings`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_security_sessions.py`:

```python
import hashlib

import pytest

from services.security import AuthenticationError, SessionCapacityError, SessionStore


def test_tokens_are_hashed_and_sessions_authenticate():
    store = SessionStore("correct horse battery staple", 30, 10, 2)
    issued = store.create("correct horse battery staple", "client-a", now=100.0)
    assert issued.token not in repr(store._sessions)
    assert hashlib.sha256(issued.token.encode()).hexdigest() in store._sessions
    principal = store.authenticate(issued.token, now=105.0)
    assert principal is not None
    assert principal.session_id == issued.principal.session_id


def test_wrong_secret_idle_absolute_revocation_and_capacity():
    store = SessionStore("correct horse battery staple", 30, 10, 1)
    with pytest.raises(AuthenticationError):
        store.create("wrong", "client-a", now=0.0)
    first = store.create("correct horse battery staple", "client-a", now=0.0)
    with pytest.raises(SessionCapacityError):
        store.create("correct horse battery staple", "client-b", now=1.0)
    assert store.authenticate(first.token, now=11.0) is None
    second = store.create("correct horse battery staple", "client-b", now=20.0)
    assert store.authenticate(second.token, now=49.0) is not None
    assert store.authenticate(second.token, now=51.0) is None
    third = store.create("correct horse battery staple", "client-c", now=60.0)
    assert store.revoke(third.token) is True
    assert store.authenticate(third.token, now=61.0) is None
```

Create `tests/test_rate_limit.py`:

```python
from services.rate_limit import FixedWindowLimiter


def test_fixed_window_limit_retry_and_bucket_isolation():
    limiter = FixedWindowLimiter()
    assert limiter.check("a", 2, 10, now=0).allowed
    assert limiter.check("a", 2, 10, now=1).allowed
    denied = limiter.check("a", 2, 10, now=2)
    assert denied.allowed is False
    assert denied.retry_after == 8
    assert limiter.check("b", 2, 10, now=2).allowed
    assert limiter.check("a", 2, 10, now=10).allowed
```

Run:

```bash
pytest tests/test_security_sessions.py tests/test_rate_limit.py -q
```

Expected: imports fail.

- [ ] **Step 2: Implement the session store**

Create `services/security.py`:

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
        expired = [
            key
            for key, record in self._sessions.items()
            if now >= record.expires_at or now - record.last_seen_at > self._idle_seconds
        ]
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
            self._sessions[self._hash(token)] = _SessionRecord(
                session_id=session_id,
                csrf_token=csrf,
                created_at=current,
                last_seen_at=current,
                expires_at=current + self._ttl_seconds,
                fingerprint_hash=self._hash(fingerprint),
            )
            return IssuedSession(
                token=token,
                principal=SessionPrincipal(session_id, csrf, current, current + self._ttl_seconds),
            )

    def authenticate(self, token: str, now: float | None = None) -> SessionPrincipal | None:
        if not token:
            return None
        current = time.time() if now is None else now
        with self._lock:
            self._purge(current)
            record = self._sessions.get(self._hash(token))
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

- [ ] **Step 3: Implement the limiter**

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

- [ ] **Step 4: Verify and commit**

```bash
pytest tests/test_security_sessions.py tests/test_rate_limit.py -q
python -m compileall services/security.py services/rate_limit.py
git add services/security.py services/rate_limit.py tests/test_security_sessions.py tests/test_rate_limit.py
git commit -m "feat: add operator sessions and rate limits"
```

Expected: all tests pass.

---

### Task 3: Audit integrity and live challenges

**Files:**
- Create: `services/security_audit.py`
- Create: `services/live_authorization.py`
- Create: `tests/test_security_audit.py`
- Create: `tests/test_live_authorization.py`
- Modify: `schemas.py`

**Interfaces:**
- Produces: `SecurityAuditLog`, `AuditVerification`, `canonicalize_action`, `LiveChallengeStore`, `LiveChallengeView`, and authentication/challenge schemas.
- Consumes: audit path, authenticated session IDs, and normalized route actions.

- [ ] **Step 1: Write failing tests**

Create `tests/test_security_audit.py`:

```python
import json

from services.security_audit import SecurityAuditLog


def test_audit_verifies_redacts_and_detects_tampering(tmp_path):
    path = tmp_path / "security.jsonl"
    audit = SecurityAuditLog(path)
    audit.append(
        "auth.success",
        "allowed",
        request_id="req-1",
        session_id="OS_1",
        action={"instrument": "BTC_USDT", "operator_secret": "never-log-me"},
    )
    assert audit.verify().ok is True
    assert "never-log-me" not in path.read_text()
    assert "[REDACTED]" in path.read_text()
    row = json.loads(path.read_text())
    row["outcome"] = "changed"
    path.write_text(json.dumps(row) + "\n")
    assert audit.verify().ok is False
    assert audit.healthy is False
```

Create `tests/test_live_authorization.py`:

```python
import pytest

from services.live_authorization import ChallengeError, LiveChallengeStore, canonicalize_action

ACTION = {
    "operation": "copy.emit_signal",
    "leader_id": "L_1",
    "instrument": "BTC_USDT",
    "side": "BUY",
    "order_type": "MARKET",
    "notional_usd": 25,
}


def test_digest_normalizes_key_order_and_numeric_types():
    reordered = dict(reversed(list(ACTION.items())))
    float_value = {**ACTION, "notional_usd": 25.0}
    assert canonicalize_action(ACTION)[0] == canonicalize_action(reordered)[0]
    assert canonicalize_action(ACTION)[0] == canonicalize_action(float_value)[0]


def test_challenge_is_single_use_session_action_and_time_bound():
    store = LiveChallengeStore(ttl_seconds=10)
    challenge = store.create("OS_a", ACTION, now=0.0)
    with pytest.raises(ChallengeError, match="session"):
        store.consume("OS_b", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=1.0)
    with pytest.raises(ChallengeError, match="action"):
        store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, {**ACTION, "notional_usd": 50}, now=2.0)
    with pytest.raises(ChallengeError, match="phrase"):
        store.consume("OS_a", challenge.challenge_id, "CONFIRM LIVE WRONG", ACTION, now=3.0)
    assert store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=4.0)
    with pytest.raises(ChallengeError, match="consumed"):
        store.consume("OS_a", challenge.challenge_id, challenge.confirmation_phrase, ACTION, now=5.0)
    expired = store.create("OS_a", ACTION, now=20.0)
    with pytest.raises(ChallengeError, match="expired"):
        store.consume("OS_a", expired.challenge_id, expired.confirmation_phrase, ACTION, now=31.0)
```

Run:

```bash
pytest tests/test_security_audit.py tests/test_live_authorization.py -q
```

Expected: imports fail.

- [ ] **Step 2: Implement the audit log**

Create `services/security_audit.py`:

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
        return {
            str(key): "[REDACTED]" if str(key).lower() in REDACT_KEYS else redact(item)
            for key, item in value.items()
        }
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
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def verify(self) -> AuditVerification:
        previous = GENESIS_HASH
        records = 0
        if not self.path.exists():
            self.healthy = True
            return AuditVerification(True, 0, previous)
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
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

- [ ] **Step 3: Implement action normalization and challenge consumption**

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
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return format(float(value), ".12g")
    if isinstance(value, str):
        return value.strip()
    return value


def canonicalize_action(action: Mapping[str, Any]) -> tuple[str, str]:
    canonical = json.dumps(_normalize(dict(action)), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest(), canonical


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
    def _hash_phrase(phrase: str) -> str:
        return hashlib.sha256(phrase.encode()).hexdigest()

    def create(self, session_id: str, action: Mapping[str, Any], now: float | None = None) -> LiveChallengeView:
        current = time.time() if now is None else now
        digest, canonical = canonicalize_action(action)
        phrase = f"CONFIRM LIVE {secrets.token_hex(3).upper()}"
        challenge_id = "LC_" + secrets.token_hex(10)
        expires_at = current + self.ttl_seconds
        with self._lock:
            self._records[challenge_id] = _ChallengeRecord(
                session_id=session_id,
                phrase_hash=self._hash_phrase(phrase),
                action_digest=digest,
                expires_at=expires_at,
            )
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
            if not secrets.compare_digest(record.phrase_hash, self._hash_phrase(phrase)):
                raise ChallengeError("challenge phrase mismatch")
            record.consumed = True
            return digest
```

- [ ] **Step 4: Add API contracts**

In `schemas.py`, import `Literal` and add:

```python
class AuthSessionIn(BaseModel):
    operator_secret: str = Field(..., min_length=1, max_length=512)
    delivery: Literal["cookie", "bearer"] = "cookie"


class AuthSessionOut(BaseModel):
    authenticated: bool
    csrf_token: Optional[str] = None
    session_id: str
    expires_at: float
    access_token: Optional[str] = None
    token_type: Optional[str] = None


class AuthStatusOut(BaseModel):
    configured: bool
    authenticated: bool
    mode: str
    session_id: Optional[str] = None
    expires_at: Optional[float] = None
    csrf_token: Optional[str] = None


class LiveChallengeIn(BaseModel):
    action: Dict[str, Any]


class LiveChallengeOut(BaseModel):
    challenge_id: str
    confirmation_phrase: str
    expires_at: float
    action_digest: str
    action_summary: str
```

Change `SignalCreate` and `CopySignalIn` live fields to:

```python
confirm_live: bool = False
challenge_id: str = Field(default="", max_length=64)
confirmation_text: str = Field(default="", max_length=64)
```

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/test_security_audit.py tests/test_live_authorization.py -q
python -m compileall services/security_audit.py services/live_authorization.py schemas.py
git add services/security_audit.py services/live_authorization.py tests/test_security_audit.py tests/test_live_authorization.py schemas.py
git commit -m "feat: add audit integrity and live challenges"
```

Expected: all tests pass.

---

### Task 4: FastAPI authentication and route policy

**Files:**
- Create: `security_dependencies.py`
- Create: `tests/test_security_api.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `SecurityRuntime`, `optional_operator`, `require_operator`, `require_write`, `/auth/status`, `/auth/session`, `DELETE /auth/session`, and `/auth/live-challenges`.
- Consumes: Tasks 1-3 services and schemas.

- [ ] **Step 1: Write failing API tests**

Create `tests/test_security_api.py`:

```python
from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient


def load_app(monkeypatch, tmp_path, secret: str | None = None):
    monkeypatch.setenv("SNIPER_BIND_MODE", "local")
    monkeypatch.setenv("SNIPER_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    if secret is None:
        monkeypatch.delenv("SNIPER_OPERATOR_SECRET", raising=False)
    else:
        monkeypatch.setenv("SNIPER_OPERATOR_SECRET", secret)
    for name in ("main", "config"):
        sys.modules.pop(name, None)
    module = importlib.import_module("main")
    monkeypatch.setattr(module.alerts_store, "list_alerts", lambda: [])
    monkeypatch.setattr(
        module.alerts_store,
        "add_alert",
        lambda instrument, direction, target, note: {
            "id": "A_1", "instrument": instrument, "direction": direction,
            "target": target, "note": note,
        },
    )
    return TestClient(module.app), module


def cookie_login(client: TestClient, secret: str) -> str:
    response = client.post(
        "/auth/session",
        json={"operator_secret": secret, "delivery": "cookie"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_read_only_keeps_health_and_denies_private_state(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path)
    assert client.get("/health").status_code == 200
    denied = client.get("/copy/state")
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "operator_auth_not_configured"


def test_cookie_login_status_refresh_and_csrf(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, _ = load_app(monkeypatch, tmp_path, secret)
    csrf = cookie_login(client, secret)
    status = client.get("/auth/status")
    assert status.json()["authenticated"] is True
    assert status.json()["csrf_token"] == csrf
    assert status.headers["Cache-Control"] == "no-store"
    assert client.post("/alerts", json={"direction": "above", "target": 100}).status_code == 403
    accepted = client.post(
        "/alerts",
        json={"direction": "above", "target": 100},
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 200


def test_bearer_delivery_returns_token_and_skips_csrf(monkeypatch, tmp_path):
    secret = "correct horse battery staple"
    client, _ = load_app(monkeypatch, tmp_path, secret)
    login = client.post(
        "/auth/session",
        json={"operator_secret": secret, "delivery": "bearer"},
    ).json()
    assert login["access_token"]
    response = client.post(
        "/alerts",
        json={"direction": "above", "target": 101},
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert response.status_code == 200


def test_login_rate_limit(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path, "correct horse battery staple")
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

Expected: failures because the auth layer is absent.

- [ ] **Step 2: Implement dependencies**

Create `security_dependencies.py`:

```python
from __future__ import annotations

import secrets
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
    streams: object | None = None


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    agent = request.headers.get("user-agent", "")[:120]
    return f"{host}|{agent}"


def api_error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


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


def require_write(request: Request) -> SessionPrincipal:
    principal = require_operator(request)
    _, delivery = raw_token(request)
    if delivery == "cookie":
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied, principal.csrf_token):
            raise api_error(403, "csrf_failed", "A valid CSRF token is required.")
    decision = runtime(request).limiter.check(f"write:{principal.session_id}", 60, 60)
    if not decision.allowed:
        raise api_error(
            429,
            "rate_limited",
            "Write rate limit exceeded.",
            {"Retry-After": str(decision.retry_after)},
        )
    return principal
```

- [ ] **Step 3: Initialize runtime and error envelope**

In `main.py`, after creating `app`:

```python
from security_dependencies import (
    SESSION_COOKIE,
    SecurityRuntime,
    api_error,
    client_key,
    optional_operator,
    raw_token,
    request_id,
    require_operator,
    require_write,
)
from services.live_authorization import ChallengeError, LiveChallengeStore
from services.rate_limit import FixedWindowLimiter
from services.security import AuthenticationError, SessionCapacityError, SessionStore
from services.security_audit import SecurityAuditLog

_operator_secret = _settings.operator_secret.get_secret_value() if _settings.operator_secret else None
app.state.security = SecurityRuntime(
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
```

Change timing middleware to assign `request.state.request_id` before `call_next`. Add:

```python
@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {
        "code": "request_failed",
        "message": str(exc.detail),
    }
    return ORJSONResponse(
        status_code=exc.status_code,
        content={"error": {**detail, "request_id": request_id(request)}},
        headers=exc.headers,
    )
```

- [ ] **Step 4: Add auth and challenge routes**

Implement:

```python
@app.get("/auth/status", response_model=AuthStatusOut)
async def auth_status(request: Request, response: Response):
    principal = optional_operator(request)
    response.headers["Cache-Control"] = "no-store"
    return AuthStatusOut(
        configured=app.state.security.sessions.configured,
        authenticated=principal is not None,
        mode=_settings.bind_mode,
        session_id=principal.session_id if principal else None,
        expires_at=principal.expires_at if principal else None,
        csrf_token=principal.csrf_token if principal else None,
    )


@app.post("/auth/session", response_model=AuthSessionOut)
async def auth_create(payload: AuthSessionIn, request: Request):
    security = app.state.security
    decision = security.limiter.check(f"login:{client_key(request)}", 5, 600)
    if not decision.allowed:
        raise api_error(429, "rate_limited", "Too many authentication attempts.", {"Retry-After": str(decision.retry_after)})
    if not security.sessions.configured:
        raise api_error(503, "operator_auth_not_configured", "Operator authentication is not configured.")
    try:
        issued = security.sessions.create(payload.operator_secret, client_key(request))
    except AuthenticationError:
        security.audit.append("auth.failure", "denied", request_id=request_id(request))
        raise api_error(401, "invalid_operator_secret", "The operator secret was not accepted.")
    except SessionCapacityError:
        raise api_error(503, "session_capacity_reached", "Operator session capacity has been reached.")
    security.audit.append("auth.success", "allowed", request_id=request_id(request), session_id=issued.principal.session_id)
    body = AuthSessionOut(
        authenticated=True,
        csrf_token=issued.principal.csrf_token if payload.delivery == "cookie" else None,
        session_id=issued.principal.session_id,
        expires_at=issued.principal.expires_at,
        access_token=issued.token if payload.delivery == "bearer" else None,
        token_type="bearer" if payload.delivery == "bearer" else None,
    )
    result = ORJSONResponse(body.model_dump())
    result.headers["Cache-Control"] = "no-store"
    if payload.delivery == "cookie":
        result.set_cookie(
            SESSION_COOKIE,
            issued.token,
            httponly=True,
            secure=False,
            samesite="strict",
            max_age=_settings.session_ttl_seconds,
            path="/",
        )
    return result


@app.delete("/auth/session")
async def auth_delete(request: Request):
    principal = require_write(request)
    token, _ = raw_token(request)
    revoked = app.state.security.sessions.revoke(token)
    app.state.security.audit.append("auth.logout", "allowed", request_id=request_id(request), session_id=principal.session_id)
    result = ORJSONResponse({"revoked": revoked})
    result.delete_cookie(SESSION_COOKIE, path="/")
    result.headers["Cache-Control"] = "no-store"
    return result


@app.post("/auth/live-challenges", response_model=LiveChallengeOut)
async def live_challenge_create(payload: LiveChallengeIn, request: Request):
    principal = require_write(request)
    operation = str(payload.action.get("operation", ""))
    if operation not in {"copy.emit_signal", "copy.copy_signal"}:
        raise api_error(400, "unsupported_live_action", "This live action is not supported.")
    security = app.state.security
    decision = security.limiter.check(f"challenge:{principal.session_id}", 5, 60)
    if not decision.allowed:
        raise api_error(429, "rate_limited", "Live-challenge rate limit exceeded.", {"Retry-After": str(decision.retry_after)})
    challenge = security.challenges.create(principal.session_id, payload.action)
    security.audit.append(
        "challenge.created",
        "allowed",
        request_id=request_id(request),
        session_id=principal.session_id,
        action=payload.action,
        action_digest=challenge.action_digest,
    )
    return LiveChallengeOut(**challenge.__dict__)
```

Import `Response` and the new schemas.

- [ ] **Step 5: Apply route policy**

Add `request: Request` and call:

- `require_operator(request)` in `GET /alerts`, `GET /portfolio/paper`, `GET /copy/state`, and `GET /sessions`.
- `require_write(request)` in alert create/delete, copy leader/follower/signal/copy, session deletion, `POST /grok/comment`, and `POST /research/search`.
- For `GET /integrations`, allow public catalog output but include live probes only when `optional_operator(request)` is not `None`.
- Leave health, readiness, trader prompt, market reads, news reads, risk calculation, DEX discovery, and caller-supplied public-wallet reads public.

Append audit events for accepted and denied mutations without passing raw request bodies containing authorization fields.

- [ ] **Step 6: Verify and commit**

```bash
pytest tests/test_security_api.py -q
pytest tests/test_core.py tests/test_connections.py tests/test_grok_alerts.py tests/test_integrations.py tests/test_risk.py tests/test_volume.py -q
git add security_dependencies.py tests/test_security_api.py main.py
git commit -m "feat: enforce operator authentication on private routes"
```

Expected: all tests pass without external network access.

---

### Task 5: Challenge-protected live copy trading

**Files:**
- Modify: `main.py`
- Modify: `services/copy_trade.py`
- Modify: `tests/test_security_api.py`

**Interfaces:**
- Produces: exact server action builders and challenge consumption before any `confirm_live=True` engine call.
- Consumes: `LiveChallengeStore.consume`, `SignalCreate`, `CopySignalIn`, and authenticated principals.

- [ ] **Step 1: Add failing live-path tests**

Append:

```python
class FakeEngine:
    def __init__(self):
        self.live_calls = 0

    def emit_signal(
        self,
        leader_id,
        instrument,
        side,
        order_type,
        quantity,
        notional_usd,
        price,
        note,
        auto_copy,
        confirm_live,
    ):
        if confirm_live:
            self.live_calls += 1
        return {"signal": {"signal_id": "S_1"}, "fills": []}

    def copy_signal(self, signal_id, confirm_live=False):
        if confirm_live:
            self.live_calls += 1
        return []


def emit_action(payload):
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


def test_fixed_phrase_is_denied(monkeypatch, tmp_path):
    client, module = load_app(monkeypatch, tmp_path, "correct horse battery staple")
    csrf = cookie_login(client, "correct horse battery staple")
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


def test_challenge_succeeds_once_and_modified_payload_fails(monkeypatch, tmp_path):
    client, module = load_app(monkeypatch, tmp_path, "correct horse battery staple")
    csrf = cookie_login(client, "correct horse battery staple")
    fake = FakeEngine()
    monkeypatch.setattr(module, "get_engine", lambda: fake)
    payload = {
        "leader_id": "L_1", "instrument": "BTC_USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usd": 25, "confirm_live": True,
    }
    challenge = client.post(
        "/auth/live-challenges",
        json={"action": emit_action(payload)},
        headers={"X-CSRF-Token": csrf},
    ).json()
    authorized = {
        **payload,
        "challenge_id": challenge["challenge_id"],
        "confirmation_text": challenge["confirmation_phrase"],
    }
    assert client.post("/copy/signals", json=authorized, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert fake.live_calls == 1
    assert client.post("/copy/signals", json=authorized, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert fake.live_calls == 1
    second = client.post(
        "/auth/live-challenges",
        json={"action": emit_action(payload)},
        headers={"X-CSRF-Token": csrf},
    ).json()
    altered = {
        **payload,
        "notional_usd": 50,
        "challenge_id": second["challenge_id"],
        "confirmation_text": second["confirmation_phrase"],
    }
    assert client.post("/copy/signals", json=altered, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert fake.live_calls == 1
```

Run the new tests and confirm failure.

- [ ] **Step 2: Add exact server action builders**

In `main.py`:

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

- [ ] **Step 3: Consume before adapter invocation**

In both live-capable routes, after `principal = require_write(request)`:

```python
if payload.confirm_live:
    if not payload.challenge_id or not payload.confirmation_text:
        raise api_error(403, "live_challenge_required", "A valid one-time live authorization is required.")
    security = app.state.security
    if not security.audit.healthy:
        raise api_error(503, "audit_integrity_failed", "Live execution is disabled because audit integrity is unavailable.")
    action = emit_signal_action(payload)
    try:
        digest = security.challenges.consume(
            principal.session_id,
            payload.challenge_id,
            payload.confirmation_text,
            action,
        )
    except ChallengeError as exc:
        raise api_error(403, "live_challenge_invalid", str(exc))
    security.audit.append(
        "challenge.consumed",
        "allowed",
        request_id=request_id(request),
        session_id=principal.session_id,
        action=action,
        action_digest=digest,
    )
```

Use `copy_existing_action(signal_id)` in `/copy/signals/{signal_id}/copy`. Only after the block may the engine receive `confirm_live=True`. Provider failure never restores a challenge.

- [ ] **Step 4: Clarify the engine boundary**

Update `services/copy_trade.py` module documentation to state that:

```text
The FastAPI route layer owns authentication and one-time live authorization.
The engine's confirm_live flag only selects dry-run versus adapter submission.
Direct library callers must provide an equivalent authorization boundary.
```

Do not import FastAPI, cookies, session stores, or secrets into the engine.

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/test_security_api.py tests/test_core.py tests/test_risk.py -q
git add main.py services/copy_trade.py tests/test_security_api.py
git commit -m "feat: require one-time authorization for live copy trades"
```

Expected: fixed phrase, replay, altered payload, wrong session, expired challenge, and audit failure are denied.

---

### Task 6: Bounded SSE and paid-provider gating

**Files:**
- Create: `services/stream_limits.py`
- Create: `tests/test_stream_limits.py`
- Modify: `services/grok_live.py`
- Modify: `security_dependencies.py`
- Modify: `main.py`
- Modify: `tests/test_grok_alerts.py`

**Interfaces:**
- Produces: `StreamLeasePool`, `leased_events`, and `generate_live_comment(..., allow_remote=False)`.
- Consumes: stream settings, client keys, and optional operator sessions.

- [ ] **Step 1: Write failing tests**

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
def test_configured_remote_provider_is_not_used_without_authority(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "configured-but-forbidden")
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

Run both files and confirm failure.

- [ ] **Step 2: Implement stream leases and async wrapper**

Create `services/stream_limits.py`:

```python
from __future__ import annotations

import threading
from collections.abc import AsyncIterator
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
            current = self._clients.get(client_key, 0)
            if self.total >= self.max_total or current >= self.max_per_client:
                raise StreamLimitError("SSE connection limit reached")
            self.total += 1
            self._clients[client_key] = current + 1
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


async def leased_events(source: AsyncIterator[str], pool: StreamLeasePool, key: str) -> AsyncIterator[str]:
    try:
        with pool.acquire(key):
            async for event in source:
                yield event
    except StreamLimitError:
        yield 'event: error\ndata: {"error":"stream_limit_reached"}\n\n'
```

Change `SecurityRuntime.streams` to `StreamLeasePool` and initialize it from settings.

- [ ] **Step 3: Gate paid Grok calls explicitly**

Change the signature in `services/grok_live.py`:

```python
def generate_live_comment(
    context: Dict[str, Any],
    timeout: float = 25.0,
    *,
    allow_remote: bool = False,
) -> Dict[str, Any]:
```

Replace the current no-key branch with:

```python
if not key or not allow_remote:
    return {
        "text": _local_brief(context),
        "source": "local_fallback",
        "model": None,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "configured": bool(key),
        "remote_allowed": allow_remote,
    }
```

`POST /grok/comment` passes `allow_remote=True` only after `require_write`. `/live/deck` captures `principal = optional_operator(request)` before creating its source generator and passes `allow_remote=principal is not None`.

- [ ] **Step 4: Wrap existing stream generators without rewriting their bodies**

For `/market/stream`, `/sniper/live`, and `/live/deck`:

1. Add `request: Request` to the route signature.
2. Rename the existing inner `gen` function to a route-specific name such as `market_events`, `sniper_events`, or `deck_events`.
3. Leave every existing yield, loop, sleep, and market-analysis statement inside that renamed function unchanged, except the paid-Grok call from Step 3.
4. Return:

```python
return StreamingResponse(
    leased_events(route_specific_events(), app.state.security.streams, client_key(request)),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    },
)
```

Replace `route_specific_events` with the exact renamed function in each route.

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/test_stream_limits.py tests/test_grok_alerts.py tests/test_security_api.py -q
git add services/stream_limits.py services/grok_live.py security_dependencies.py main.py tests/test_stream_limits.py tests/test_grok_alerts.py
git commit -m "feat: bound live streams and gate paid commentary"
```

Expected: leases release after exit and unauthenticated code never creates an xAI client.

---

### Task 7: Operator and live-review UI

**Files:**
- Modify: `ui/index.html`
- Modify: `tests/test_security_api.py`

**Interfaces:**
- Consumes: auth status/login/logout, CSRF status refresh, live challenge creation, and challenge fields.
- Produces: `READ ONLY`, `OPERATOR`, and `LIVE CHALLENGE ACTIVE` states plus one authenticated request wrapper.

- [ ] **Step 1: Add failing UI smoke test**

Append:

```python
def test_ui_contains_nonpersistent_security_controls(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path, "correct horse battery staple")
    html = client.get("/").text
    for marker in (
        'id="securityStatus"',
        'id="operatorSecret"',
        'id="operatorLogin"',
        'id="operatorLogout"',
        'id="liveChallengePhrase"',
        "X-CSRF-Token",
    ):
        assert marker in html
    assert "localStorage.setItem('sniper_operator" not in html
    assert 'localStorage.setItem("sniper_operator' not in html
```

Run and confirm failure.

- [ ] **Step 2: Add the security card**

Insert near the Connections or header controls:

```html
<section class="card" id="operatorSecurity">
  <div class="section-head">
    <div><p class="eyebrow">Operator security</p><h3>Execution authority</h3></div>
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

- [ ] **Step 3: Add authenticated request and status refresh**

Add to the existing script:

```javascript
const security = { authenticated: false, configured: false, csrfToken: "", challenge: null };

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

async function refreshSecurityStatus() {
  const status = await apiFetch("/auth/status");
  security.configured = status.configured;
  security.authenticated = status.authenticated;
  security.csrfToken = status.csrf_token || "";
  document.getElementById("securityStatus").textContent = status.authenticated ? "OPERATOR" : "READ ONLY";
  document.getElementById("operatorLogin").hidden = status.authenticated;
  document.getElementById("operatorLogout").hidden = !status.authenticated;
  document.querySelectorAll("[data-requires-operator]").forEach((node) => {
    node.disabled = !status.authenticated;
  });
}
```

Replace mutating `fetch` calls with `apiFetch`. EventSource remains same-origin and sends the cookie automatically.

- [ ] **Step 4: Add login, logout, and challenge review**

```javascript
async function loginOperator() {
  const input = document.getElementById("operatorSecret");
  await apiFetch("/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator_secret: input.value, delivery: "cookie" }),
  });
  input.value = "";
  await refreshSecurityStatus();
}

async function logoutOperator() {
  await apiFetch("/auth/session", { method: "DELETE" });
  security.csrfToken = "";
  security.challenge = null;
  await refreshSecurityStatus();
}

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

function clearLiveChallenge() {
  security.challenge = null;
  document.getElementById("liveChallengePhrase").value = "";
  document.getElementById("liveChallengeCard").hidden = true;
}
```

The final live request includes `challenge_id` and `confirmation_text`. Call `clearLiveChallenge()` in a `finally` block after every live attempt. Paper mode never requests a challenge. Attach login/logout handlers and call `refreshSecurityStatus()` on startup.

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/test_security_api.py -q
git add ui/index.html tests/test_security_api.py
git commit -m "feat: add operator security controls to trading UI"
```

Manual checks: page refresh restores cookie status and obtains CSRF from `/auth/status`; no sensitive value appears in localStorage or IndexedDB.

---

### Task 8: Stable errors, CI, and documentation

**Files:**
- Modify: `main.py`
- Create: `.github/workflows/security.yml`
- Modify: `README.md`
- Modify: `tests/test_security_api.py`
- Create when absent: `pyproject.toml`

**Interfaces:**
- Produces: stable client errors, merge-blocking checks, and documentation matching implemented behavior.
- Consumes: all previous tasks.

- [ ] **Step 1: Add failing stable-error and audit-failure tests**

Append:

```python
def test_security_error_envelope(monkeypatch, tmp_path):
    client, _ = load_app(monkeypatch, tmp_path, "correct horse battery staple")
    response = client.get("/copy/state")
    error = response.json()["error"]
    assert response.status_code == 401
    assert error["code"] == "operator_auth_required"
    assert error["request_id"]
    assert "Traceback" not in response.text


def test_audit_failure_disables_live_not_health(monkeypatch, tmp_path):
    client, module = load_app(monkeypatch, tmp_path, "correct horse battery staple")
    csrf = cookie_login(client, "correct horse battery staple")
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

- [ ] **Step 2: Remove raw upstream exception leakage**

For protected, wallet, DEX, market, news, and model routes, log the original exception with request ID and return stable codes:

```python
raise api_error(502, "market_upstream_failed", "Market data is temporarily unavailable.")
raise api_error(502, "news_upstream_failed", "News data is temporarily unavailable.")
raise api_error(502, "wallet_lookup_failed", "The public wallet lookup could not be completed.")
raise api_error(502, "dex_lookup_failed", "DEX discovery could not be completed.")
raise api_error(502, "model_commentary_failed", "Model commentary could not be completed.")
raise api_error(400, "copy_trade_failed", "The copy-trade request was rejected.")
```

Add a final exception handler returning `internal_error` with request ID and no raw exception text.

- [ ] **Step 3: Add CI configuration**

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

If absent, create `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.ruff.format]
quote-style = "double"
```

- [ ] **Step 4: Update README**

Add exact sections covering:

```markdown
## Secure local start

Sniper Trades binds to `127.0.0.1:8000` by default. Without `SNIPER_OPERATOR_SECRET`, public intelligence remains available while private state, paid model calls, writes, and live actions are denied.

```bash
export SNIPER_OPERATOR_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
./scripts/run.sh
```

## Trusted-LAN phone access

```bash
export SNIPER_BIND_MODE=lan
export SNIPER_CORS_ORIGINS=http://192.168.1.20:8000
export SNIPER_OPERATOR_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
./scripts/run.sh
```

LAN mode remains ordinary HTTP unless TLS is supplied separately. Do not port-forward it, expose it publicly, or use it on an untrusted network.

## Live authorization

`CONFIRM LIVE` alone no longer authorizes a real order. Real submission requires an authenticated operator and a short-lived, single-use challenge bound to the exact order payload. The challenge is consumed before the exchange adapter runs, including when the downstream order fails.
```

Also document the audit path, browser versus Bearer login, CSRF behavior, paid-Grok gate, read-only fallback, and v6.4 migration.

- [ ] **Step 5: Verify and commit**

```bash
pytest -q
ruff check .
ruff format --check .
bandit -q -r agents blockchain jspace services main.py config.py schemas.py security_dependencies.py
pip-audit -r requirements-core.txt
python -m compileall agents blockchain jspace services main.py config.py schemas.py security_dependencies.py
git add main.py README.md .github/workflows/security.yml tests/test_security_api.py pyproject.toml
git commit -m "ci: enforce security verification and document safe operation"
```

If `pyproject.toml` already existed, modify it narrowly. Expected: every command exits zero and no unreviewed medium/high Bandit finding remains.

---

### Task 9: Final verification and pull request

**Files:**
- Review every file changed by Tasks 1-8.

**Interfaces:**
- Produces: a reviewable PR with verified behavior and no unsupported security claims.
- Consumes: implementation branch and design spec.

- [ ] **Step 1: Review scope and secrets**

```bash
git diff main...HEAD --stat
git diff main...HEAD -- . ':!docs/superpowers/plans/*'
git grep -nE '(sk-[A-Za-z0-9]|xai-[A-Za-z0-9]|seed phrase|private key|SNIPER_OPERATOR_SECRET=.{20,})' HEAD -- . ':!.env.example'
```

Expected: only planned security, tests, UI, CI, and documentation changes; no credential match.

- [ ] **Step 2: Verify in a clean environment**

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

- [ ] **Step 3: Manually verify acceptance criteria**

1. Default launch listens only on `127.0.0.1`.
2. Unsafe LAN combinations fail startup.
3. Read-only launch preserves public market and health routes.
4. Anonymous private reads and all writes are denied.
5. Cookie writes require CSRF; Bearer writes do not.
6. Page refresh recovers CSRF through authenticated `/auth/status` without persistent browser storage.
7. Audit chain verifies and excludes secrets.
8. Fixed phrase alone fails.
9. Matching challenge succeeds once and is consumed before adapter invocation.
10. Replay, altered payload, wrong session, expiry, or audit failure denies live execution.
11. Unauthenticated live decks never spend the xAI key.
12. SSE counts are bounded and released after disconnect.

- [ ] **Step 4: Open PR and require review**

```bash
git push -u origin feature/security-foundation
gh pr create \
  --base main \
  --head feature/security-foundation \
  --title "Security foundation for Sniper Trades" \
  --body "Adds secure localhost defaults, explicit LAN validation, operator sessions, CSRF, private-route policy, one-time action-bound live authorization, hash-chained audit, bounded SSE, paid-Grok gating, tests, and CI. LAN HTTP remains trusted-network convenience and is not presented as MFA or protection against an active network attacker."
```

Run a requirements review against `docs/superpowers/specs/2026-07-20-sniper-trades-security-foundation-design.md`, then run CodeRabbit. Resolve valid findings with focused commits and rerun all checks before merge.
