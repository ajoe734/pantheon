"""HMAC-SHA256 signing, verification, and replay protection for dev task packets.

ASST-INTEG-006 — owned by Claude2.

Security guarantees:
- Canonical payload is computed from the packet with the signature field
  stripped, keys sorted, no indentation (deterministic JSON).
- Signing key is read from BRIDGE_SIGNING_KEY env var or the optional
  key_store argument.  If absent in dev/test contexts the signer falls back
  to a constant dev-only key and logs a clear warning.
- Replay protection is file-backed: each verified packet_id is appended to
  a newline-delimited seen-ids file.  Duplicate packet_ids are rejected
  before task materialisation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Dict, Optional

from .dev_bridge_models import DevTaskPacket, PacketSignature

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

_DEV_KEY = b"pantheon-bridge-dev-key-not-for-prod"
_KEY_ENV = "BRIDGE_SIGNING_KEY"
_ALGORITHM = "HMAC-SHA256"

# Default replay store path relative to repo root.
_DEFAULT_REPLAY_STORE = ".orchestrator/dev-bridge-seen-packets.txt"


def _signing_key(key_store: Optional[Dict[str, bytes]] = None, key_id: str = "assistant-bridge-dev") -> bytes:
    """Return the signing key for *key_id*.

    Priority:
    1. key_store argument (tests / runtime injection)
    2. BRIDGE_SIGNING_KEY environment variable (hex-encoded)
    3. Dev-only fallback (logs warning)
    """
    if key_store and key_id in key_store:
        return key_store[key_id]
    env_val = os.environ.get(_KEY_ENV)
    if env_val:
        try:
            return bytes.fromhex(env_val)
        except ValueError:
            return env_val.encode()
    import warnings
    warnings.warn(
        f"BRIDGE_SIGNING_KEY not set — using dev-only key for key_id={key_id!r}. "
        "Never use in production.",
        stacklevel=3,
    )
    return _DEV_KEY


# ---------------------------------------------------------------------------
# Canonical payload
# ---------------------------------------------------------------------------

def _canonical_payload(packet: DevTaskPacket) -> bytes:
    """Return the canonical bytes to sign.

    The signature field is excluded from the payload so the signature covers
    the packet content but not itself.
    """
    data = packet.model_dump(by_alias=False, mode="json")
    data.pop("signature", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


# ---------------------------------------------------------------------------
# Public sign / verify API
# ---------------------------------------------------------------------------

def sign_packet(
    packet: DevTaskPacket,
    *,
    key_id: str = "assistant-bridge-dev",
    key_store: Optional[Dict[str, bytes]] = None,
) -> DevTaskPacket:
    """Return a copy of *packet* with a valid HMAC-SHA256 signature attached."""
    key = _signing_key(key_store, key_id)
    payload = _canonical_payload(packet)
    mac = hmac.new(key, payload, hashlib.sha256).hexdigest()
    signed = packet.model_copy(
        update={"signature": PacketSignature(keyId=key_id, algorithm=_ALGORITHM, value=mac)}
    )
    return signed


def verify_packet(
    packet: DevTaskPacket,
    *,
    key_store: Optional[Dict[str, bytes]] = None,
) -> None:
    """Verify the packet signature.

    Raises ValueError when:
    - The packet has no signature.
    - The signature algorithm is not HMAC-SHA256.
    - The MAC does not match (constant-time comparison used).
    """
    sig = packet.signature
    if sig is None:
        raise ValueError("Packet has no signature")
    if sig.algorithm != _ALGORITHM:
        raise ValueError(f"Unsupported signature algorithm: {sig.algorithm!r}")

    key = _signing_key(key_store, sig.key_id)
    payload = _canonical_payload(packet)
    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, sig.value):
        raise ValueError("Packet signature verification failed")


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------

def _replay_store_path(repo_root: Optional[str] = None) -> Path:
    if repo_root:
        return Path(repo_root) / _DEFAULT_REPLAY_STORE
    root = os.environ.get("PANTHEON_STATUS_ROOT")
    if root:
        return Path(root) / _DEFAULT_REPLAY_STORE
    return Path(_DEFAULT_REPLAY_STORE)


def has_seen_packet(packet_id: str, *, repo_root: Optional[str] = None) -> bool:
    """Return True if *packet_id* was already dispatched (replay detected)."""
    store = _replay_store_path(repo_root)
    if not store.exists():
        return False
    seen = store.read_text(encoding="utf-8").splitlines()
    return packet_id in set(seen)


def mark_packet_seen(packet_id: str, *, repo_root: Optional[str] = None) -> None:
    """Append *packet_id* to the replay store so future dispatches are rejected."""
    store = _replay_store_path(repo_root)
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as fh:
        fh.write(packet_id + "\n")
