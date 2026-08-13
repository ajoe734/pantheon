"""Ed25519 signing, verification, and replay protection for local dev packets.

ASST-INTEG-006 — owned by Claude2.

Security guarantees:
- Canonical payload is computed from the packet with the signature field
  stripped, keys sorted, no indentation (deterministic JSON).
- Only local development tooling reads the private signing key. Product BFF
  processes receive neither this key nor this module.
- Replay protection is file-backed: each verified packet_id is appended to
  a newline-delimited seen-ids file.  Duplicate packet_ids are rejected
  before task materialisation.
"""
from __future__ import annotations

import fcntl
import base64
import binascii
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .dev_bridge_models import DevTaskPacket, PacketSignature

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

PRIVATE_KEY_ENV = "BRIDGE_SIGNING_PRIVATE_KEY"
PRIVATE_KEY_ID_ENV = "BRIDGE_SIGNING_KEY_ID"
PUBLIC_KEYS_ENV = "BRIDGE_SIGNING_PUBLIC_KEYS_JSON"
_ALGORITHM = "Ed25519"

# Default replay store path relative to repo root.
_DEFAULT_REPLAY_STORE = ".orchestrator/dev-bridge-seen-packets.txt"


def _decode_key(value: str, *, label: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        decoded = bytes.fromhex(text)
    except ValueError:
        try:
            decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"{label} is neither hex nor base64url") from exc
    if len(decoded) != 32:
        raise ValueError(f"{label} must decode to exactly 32 bytes")
    return decoded


def _test_seed(value: bytes) -> bytes:
    """Keep legacy test key stores deterministic without weakening production."""

    return hashlib.sha256(value).digest()


def _signing_key(
    key_store: Optional[Dict[str, bytes]] = None,
    key_id: str = "assistant-bridge-dev",
) -> Ed25519PrivateKey:
    if key_store and key_id in key_store:
        return Ed25519PrivateKey.from_private_bytes(_test_seed(key_store[key_id]))
    configured_id = str(os.environ.get(PRIVATE_KEY_ID_ENV) or "").strip()
    if not configured_id or configured_id != key_id:
        raise ValueError(f"{PRIVATE_KEY_ID_ENV} must exactly match key_id={key_id!r}")
    return Ed25519PrivateKey.from_private_bytes(
        _decode_key(os.environ.get(PRIVATE_KEY_ENV, ""), label=PRIVATE_KEY_ENV)
    )


def public_key_environment(
    key_store: Optional[Dict[str, bytes]] = None,
) -> str:
    """Return canonical trusted-public-key JSON for subprocess verification."""

    if key_store:
        encoded: dict[str, str] = {}
        for key_id, value in key_store.items():
            public = Ed25519PrivateKey.from_private_bytes(
                _test_seed(value)
            ).public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            encoded[str(key_id)] = base64.urlsafe_b64encode(public).decode().rstrip("=")
        return json.dumps(encoded, sort_keys=True, separators=(",", ":"))
    raw = str(os.environ.get(PUBLIC_KEYS_ENV) or "").strip()
    if not raw:
        raise ValueError(f"{PUBLIC_KEYS_ENV} is required")
    return raw


def _verification_keys(
    key_store: Optional[Dict[str, bytes]] = None,
) -> dict[str, Ed25519PublicKey]:
    try:
        payload = json.loads(public_key_environment(key_store))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{PUBLIC_KEYS_ENV} is invalid JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"{PUBLIC_KEYS_ENV} must contain at least one key")
    return {
        str(key_id): Ed25519PublicKey.from_public_bytes(
            _decode_key(str(value), label=f"bridge public key {key_id}")
        )
        for key_id, value in payload.items()
    }


def validate_signing_key_pair() -> None:
    """Fail closed unless the active local private key matches its public map."""

    key_id = str(os.environ.get(PRIVATE_KEY_ID_ENV) or "").strip()
    private_key = _signing_key(None, key_id)
    configured_public = _verification_keys().get(key_id)
    if configured_public is None:
        raise ValueError(f"{PUBLIC_KEYS_ENV} does not contain active key {key_id!r}")
    derived = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    configured = configured_public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if derived != configured:
        raise ValueError("bridge signing private key does not match active public key")


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


def packet_digest(packet: DevTaskPacket) -> str:
    """Return the stable SHA-256 digest used to detect packet-id collisions."""

    return hashlib.sha256(_canonical_payload(packet)).hexdigest()


# ---------------------------------------------------------------------------
# Public sign / verify API
# ---------------------------------------------------------------------------

def sign_packet(
    packet: DevTaskPacket,
    *,
    key_id: str | None = None,
    key_store: Optional[Dict[str, bytes]] = None,
) -> DevTaskPacket:
    """Return a copy of *packet* with a valid Ed25519 signature attached."""
    if key_store is None:
        active_key_id = str(os.environ.get(PRIVATE_KEY_ID_ENV) or "").strip()
        if not active_key_id:
            raise ValueError(f"{PRIVATE_KEY_ID_ENV} is required")
        validate_signing_key_pair()
    else:
        active_key_id = str(key_id or "assistant-bridge-dev")
    key = _signing_key(key_store, active_key_id)
    payload = _canonical_payload(packet)
    signature = base64.urlsafe_b64encode(key.sign(payload)).decode().rstrip("=")
    signed = packet.model_copy(
        update={"signature": PacketSignature(keyId=active_key_id, algorithm=_ALGORITHM, value=signature)}
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
    - The signature algorithm is not Ed25519.
    - The signature does not match a trusted public key.
    """
    sig = packet.signature
    if sig is None:
        raise ValueError("Packet has no signature")
    if sig.algorithm != _ALGORITHM:
        raise ValueError(f"Unsupported signature algorithm: {sig.algorithm!r}")

    key = _verification_keys(key_store).get(sig.key_id)
    if key is None:
        raise ValueError(f"Untrusted packet signature key: {sig.key_id!r}")
    payload = _canonical_payload(packet)
    try:
        signature = base64.urlsafe_b64decode(
            sig.value + "=" * (-len(sig.value) % 4)
        )
        key.verify(signature, payload)
    except (ValueError, binascii.Error, InvalidSignature):
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


def _replay_lock_path(repo_root: Optional[str] = None) -> Path:
    store = _replay_store_path(repo_root)
    return store.with_name(f"{store.name}.lock")


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_directory(path: Path) -> None:
    """Create *path* and durably link each new directory from its parent."""

    missing: list[Path] = []
    cursor = path
    while not cursor.exists() and cursor.parent != cursor:
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


@contextmanager
def packet_replay_lock(*, repo_root: Optional[str] = None) -> Iterator[None]:
    """Serialize packet replay check-and-mark across processes."""

    lock_path = _replay_lock_path(repo_root)
    _ensure_directory(lock_path.parent)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _replay_records_unlocked(repo_root: Optional[str] = None) -> Dict[str, Optional[str]]:
    store = _replay_store_path(repo_root)
    if not store.exists():
        return {}
    records: Dict[str, Optional[str]] = {}
    for raw_line in store.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                packet_id = str(payload.get("packet_id") or "").strip()
                digest = str(payload.get("digest") or "").strip() or None
                if packet_id:
                    records.setdefault(packet_id, digest)
                continue
        # Backward compatibility for the original one-id-per-line store.
        records.setdefault(line, None)
    return records


def replay_record(
    packet_id: str,
    *,
    repo_root: Optional[str] = None,
    lock_held: bool = False,
) -> Optional[Dict[str, Optional[str]]]:
    def _read() -> Optional[Dict[str, Optional[str]]]:
        records = _replay_records_unlocked(repo_root)
        if packet_id not in records:
            return None
        return {"packet_id": packet_id, "digest": records[packet_id]}

    if lock_held:
        return _read()
    with packet_replay_lock(repo_root=repo_root):
        return _read()


def has_seen_packet(packet_id: str, *, repo_root: Optional[str] = None) -> bool:
    """Return True if *packet_id* was already dispatched (replay detected)."""
    return replay_record(packet_id, repo_root=repo_root) is not None


def mark_packet_seen(
    packet_id: str,
    *,
    repo_root: Optional[str] = None,
    digest: Optional[str] = None,
    lock_held: bool = False,
) -> None:
    """Durably record a successful packet dispatch exactly once.

    Reusing a packet id for a different payload fails closed when both sides
    carry a digest. Legacy id-only replay rows remain readable.
    """

    def _mark() -> None:
        existing = replay_record(packet_id, repo_root=repo_root, lock_held=True)
        if existing is not None:
            existing_digest = str(existing.get("digest") or "").strip() or None
            if existing_digest and digest and existing_digest != digest:
                raise ValueError(
                    f"Packet id {packet_id!r} is already bound to a different payload"
                )
            return
        store = _replay_store_path(repo_root)
        _ensure_directory(store.parent)
        payload = {"packet_id": packet_id, "digest": digest}
        with store.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_directory(store.parent)

    if lock_held:
        _mark()
        return
    with packet_replay_lock(repo_root=repo_root):
        _mark()
