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

import fcntl
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

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


def packet_digest(packet: DevTaskPacket) -> str:
    """Return the stable SHA-256 digest used to detect packet-id collisions."""

    return hashlib.sha256(_canonical_payload(packet)).hexdigest()


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
