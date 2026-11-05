"""File-backed rotating refresh credentials for the dev-login browser session.

The access JWT stays short-lived; this store keeps an opaque, rotating refresh
credential per login ("chain"). Only SHA-256 hashes are persisted. The absolute
expiry is fixed at login and carried over unchanged on every rotation.
Presenting an already-rotated credential revokes the whole chain, except the
immediately previous credential within REUSE_GRACE_SECONDS of its rotation
(shared cookie jar across browser tabs): that returns a grant with token=None.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

REFRESH_TOKEN_PREFIX = "pdr1."
DEFAULT_REFRESH_TTL_SECONDS = 2592000  # 30 days
_MAX_REVOKED_RETAINED = 200
# Multi-tab tolerance: the credential replaced by the latest rotation is still
# accepted (without rotating) for this long; anything else reused revokes the chain.
REUSE_GRACE_SECONDS = 60


def refresh_ttl_seconds() -> int:
    try:
        value = int(os.getenv("PANTHEON_BFF_DEV_LOGIN_REFRESH_TTL_SECONDS", str(DEFAULT_REFRESH_TTL_SECONDS)))
    except ValueError:
        value = DEFAULT_REFRESH_TTL_SECONDS
    return value if value > 0 else DEFAULT_REFRESH_TTL_SECONDS


def is_refresh_token(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(REFRESH_TOKEN_PREFIX)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)


class RefreshRejected(Exception):
    """Raised when a refresh credential cannot be used; ``reason`` is stable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RefreshGrant:
    token: Optional[str]  # None on the reuse-grace path (no rotation happened)
    chain_id: str
    identity: str
    client_id: str
    roles: tuple
    tenant_id: str
    expires_at: int  # absolute refresh expiry, epoch seconds


class RefreshSessionStore:
    def __init__(self, path: str, *, clock=None) -> None:
        self._path = path
        # Late-bound so tests can patch time.time in this module.
        self._clock = clock or (lambda: time.time())
        self._lock = threading.Lock()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if not isinstance(payload.get("chains"), dict):
            payload["chains"] = {}
        if not isinstance(payload.get("hashes"), dict):
            payload["hashes"] = {}
        return payload

    def _write(self, payload: Dict[str, Any]) -> None:
        now = self._clock()
        chains = payload["chains"]
        for chain_id in [c for c, r in chains.items() if r.get("expires_at", 0) <= now]:
            del chains[chain_id]
        revoked = sorted((c for c, r in chains.items() if r.get("revoked")), key=lambda c: chains[c].get("revoked_at", 0))
        for chain_id in revoked[:-_MAX_REVOKED_RETAINED]:
            del chains[chain_id]
        payload["hashes"] = {h: c for h, c in payload["hashes"].items() if c in chains}
        directory = os.path.dirname(self._path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".refresh-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def issue(self, *, identity: str, client_id: str, roles, tenant_id: str, ttl_seconds: Optional[int] = None) -> RefreshGrant:
        now = int(self._clock())
        expires_at = now + int(ttl_seconds or refresh_ttl_seconds())
        token = _new_token()
        chain_id = f"rc-{uuid.uuid4().hex}"
        with self._lock:
            payload = self._read()
            payload["chains"][chain_id] = {
                "identity": identity, "client_id": client_id, "roles": sorted(roles), "tenant_id": tenant_id,
                "created_at": now, "expires_at": expires_at, "rotated_at": now, "revoked": False,
                "current_hash": _hash(token),
            }
            payload["hashes"][_hash(token)] = chain_id
            self._write(payload)
        return RefreshGrant(token, chain_id, identity, client_id, tuple(sorted(roles)), tenant_id, expires_at)

    def rotate(self, token: str, *, validate=None) -> RefreshGrant:
        """Consume ``token`` and return its successor, or raise RefreshRejected.

        ``validate(record)`` may raise RefreshRejected; it runs before rotation.
        A reused (already rotated) credential revokes the whole chain.
        """
        now = int(self._clock())
        digest = _hash(str(token or ""))
        with self._lock:
            payload = self._read()
            chain_id = payload["hashes"].get(digest)
            record = payload["chains"].get(chain_id) if chain_id else None
            if record is None:
                raise RefreshRejected("unknown")
            if record.get("revoked"):
                raise RefreshRejected("revoked")
            if record.get("expires_at", 0) <= now:
                raise RefreshRejected("expired")
            if (
                record.get("current_hash") != digest
                and record.get("previous_hash") == digest
                and 0 <= now - int(record.get("rotated_at", 0)) <= REUSE_GRACE_SECONDS
            ):
                if validate is not None:
                    validate(copy.deepcopy(record))
                return RefreshGrant(None, chain_id, record["identity"], record["client_id"], tuple(record["roles"]), record["tenant_id"], int(record["expires_at"]))
            if record.get("current_hash") != digest:
                record["revoked"] = True
                record["revoked_at"] = now
                record["revoked_reason"] = "reuse_detected"
                self._write(payload)
                raise RefreshRejected("reuse_detected")
            if validate is not None:
                validate(copy.deepcopy(record))
            new_token = _new_token()
            record["previous_hash"] = digest
            record["current_hash"] = _hash(new_token)
            record["rotated_at"] = now
            payload["hashes"][record["current_hash"]] = chain_id
            self._write(payload)
            return RefreshGrant(new_token, chain_id, record["identity"], record["client_id"], tuple(record["roles"]), record["tenant_id"], int(record["expires_at"]))

    def revoke_chain(self, chain_id: str, reason: str = "logout") -> bool:
        with self._lock:
            payload = self._read()
            record = payload["chains"].get(str(chain_id or ""))
            if record is None or record.get("revoked"):
                return False
            record.update(revoked=True, revoked_at=int(self._clock()), revoked_reason=reason)
            self._write(payload)
            return True

    def revoke_token(self, token: str, reason: str = "logout") -> bool:
        with self._lock:
            payload = self._read()
            chain_id = payload["hashes"].get(_hash(str(token or "")))
        return self.revoke_chain(chain_id, reason) if chain_id else False


REFRESH_COOKIE_NAME = "pantheon_refresh_token"
REFRESH_COOKIE_PATH = "/bff/auth"
ACCESS_COOKIE_NAME = "pantheon_session"


def set_access_cookie(response: Any, token: str, max_age: int) -> None:
    response.set_cookie(ACCESS_COOKIE_NAME, token, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax")


def set_refresh_cookie(response: Any, token: str, expires_at: int, *, now: Optional[float] = None) -> None:
    max_age = max(1, int(expires_at - (time.time() if now is None else now)))
    response.set_cookie(REFRESH_COOKIE_NAME, token, max_age=max_age, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="lax")


def delete_refresh_cookie(response: Any) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="lax")


def iso_utc(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
