"""Ed25519 grant signer for the execution grant issuer.

OPS-EXECUTION-MFA-ISSUER-001.
Signs execution authorization grants conforming to execution_authorization.py
using a dedicated Ed25519 signing key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

EXECUTION_GRANT_PURPOSE = "pantheon.execution.mfa"
EXECUTION_GRANT_CAPABILITY = "assistant.canonical.execute"
MAX_GRANT_FRESHNESS_SECONDS = 300
DEFAULT_GRANT_FRESHNESS_SECONDS = 120
DEFAULT_RUN_TTL_SECONDS = 1800
MAX_RUN_TTL_SECONDS = 24 * 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


class Ed25519GrantSigner:
    """Manages the private Ed25519 key and signs execution-authorization grants."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey | bytes | str | Path,
        key_id: str,
    ) -> None:
        if not key_id or not key_id.strip():
            raise ValueError("key_id must be non-empty")
        self.key_id = key_id.strip()

        if isinstance(private_key, Ed25519PrivateKey):
            self._private_key = private_key
        elif isinstance(private_key, bytes) and private_key.strip().startswith(b"-----BEGIN"):
            # Raw PEM bytes read by a caller that already performed strict
            # file-safety checks (see secure_io.read_private_file_strict).
            loaded = load_pem_private_key(private_key, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise ValueError("Provided PEM does not contain an Ed25519 private key")
            self._private_key = loaded
        elif isinstance(private_key, Path) or (
            isinstance(private_key, str) and (Path(private_key).is_file() or "\n" in private_key)
        ):
            pem_bytes = (
                Path(private_key).read_bytes()
                if isinstance(private_key, Path) or (isinstance(private_key, str) and Path(private_key).is_file())
                else private_key.encode("utf-8")
            )
            loaded = load_pem_private_key(pem_bytes, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise ValueError("Provided PEM does not contain an Ed25519 private key")
            self._private_key = loaded
        elif isinstance(private_key, (bytes, str)):
            raw_bytes = (
                base64.urlsafe_b64decode(private_key + "=" * (-len(private_key) % 4))
                if isinstance(private_key, str)
                else private_key
            )
            if len(raw_bytes) != 32:
                raise ValueError(f"Raw Ed25519 private key must be 32 bytes, got {len(raw_bytes)}")
            self._private_key = Ed25519PrivateKey.from_private_bytes(raw_bytes)
        else:
            raise TypeError("Unsupported private_key type")

        self._public_key: Ed25519PublicKey = self._private_key.public_key()
        self._raw_public_bytes: bytes = self._public_key.public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw,
        )

    @property
    def public_key_base64url(self) -> str:
        """Return base64url-encoded public key for orchestrator config trust root."""
        return _b64(self._raw_public_bytes)

    @property
    def public_key_fingerprint(self) -> str:
        """Return sha256 hex digest of raw public key bytes."""
        return hashlib.sha256(self._raw_public_bytes).hexdigest()

    def sign_grant(
        self,
        *,
        task_id: str,
        generation: int,
        policy: Mapping[str, Any],
        actor_uid: str,
        now: datetime | None = None,
        freshness_seconds: int = DEFAULT_GRANT_FRESHNESS_SECONDS,
        run_ttl_seconds: int = DEFAULT_RUN_TTL_SECONDS,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        """Construct and cryptographically sign one execution-authorization grant."""
        if not task_id or not task_id.strip():
            raise ValueError("task_id is required")
        if not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if not isinstance(policy, Mapping):
            raise ValueError("policy is required")
        if not actor_uid or not actor_uid.strip():
            raise ValueError("actor_uid is required")

        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        bounded_freshness = min(max(1, freshness_seconds), MAX_GRANT_FRESHNESS_SECONDS)
        expires_at = current_time + timedelta(seconds=bounded_freshness)
        bounded_run_ttl = min(max(1, run_ttl_seconds), MAX_RUN_TTL_SECONDS)
        grant_nonce = nonce.strip() if nonce and nonce.strip() else secrets.token_hex(16)

        resources_list = sorted({str(r).strip() for r in policy.get("resources", []) if str(r).strip()})

        grant_body: dict[str, Any] = {
            "task_id": task_id.strip(),
            "generation": generation,
            "policy_digest": str(policy.get("policy_digest") or "").strip(),
            "repository": str(policy.get("repository") or "").strip(),
            "environment": str(policy.get("environment") or "").strip(),
            "resources": resources_list,
            "action_scope": str(policy.get("action_scope") or "execute").strip(),
            "purpose": EXECUTION_GRANT_PURPOSE,
            "capability": EXECUTION_GRANT_CAPABILITY,
            "audience": task_id.strip(),
            "mfa_verified": True,
            "mfa_actor": actor_uid.strip(),
            "nonce": grant_nonce,
            "issued_at": current_time.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "run_ttl_seconds": bounded_run_ttl,
        }

        canonical_bytes = _canonical_json(grant_body)
        raw_signature = self._private_key.sign(canonical_bytes)

        signed_grant = deepcopy(grant_body)
        signed_grant["signature"] = {
            "key_id": self.key_id,
            "algorithm": "Ed25519",
            "value": _b64(raw_signature),
        }

        return signed_grant
