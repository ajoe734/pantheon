"""File-backed inbox for assistant DevTaskPacket supervisor pickup.

The Web API emits signed packets but does not execute shell. Repo-local
automation can queue those packets into this inbox; the supervisor drains it
through the verifier-backed dispatcher and its installed governed status
runtime binding.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from .dev_bridge_admission import admission_record_path, load_admission_record
from .dev_bridge_dispatcher import (
    _admission_provenance,
    _open_regular_fence_file,
    _release_dispatch_fence,
    _try_acquire_dispatch_fence,
    dispatch_task_packet,
)
from .dev_bridge_models import BridgeDispatchRequest, DevTaskPacket
from .dev_bridge_signer import (
    has_seen_packet,
    packet_digest,
    replay_record,
    verify_packet,
)


DEFAULT_INBOX_DIR = ".orchestrator/assistant-dev-packets"
PROCESSING_CLAIM_SCHEMA = "pantheon.assistant-dev-packet-processing-claim.v1"
PROCESSING_RETRY_SCHEMA = "pantheon.assistant-dev-packet-retry.v1"
PROCESSING_CLAIM_TTL_SECONDS = 300.0
RETRY_BASE_SECONDS = 0.25
RETRY_MAX_SECONDS = 5.0
FAILED_RECOVERY_SCHEMA = "pantheon.assistant-dev-packet-failed-recovery.v1"
FAILED_RECOVERY_REARM_SCHEMA = (
    "pantheon.assistant-dev-packet-failed-recovery-rearm.v1"
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_root(repo_root: Optional[str] = None) -> Path:
    if repo_root:
        return Path(repo_root).resolve()
    env_root = os.environ.get("PANTHEON_STATUS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def _inbox_root(repo_root: Optional[str] = None, inbox_dir: Optional[str] = None) -> Path:
    configured = inbox_dir or os.environ.get("PANTHEON_ASSISTANT_DEV_PACKET_INBOX") or DEFAULT_INBOX_DIR
    path = Path(configured)
    if not path.is_absolute():
        path = _repo_root(repo_root) / path
    return path.resolve()


def _safe_packet_id(packet_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(packet_id or "").strip()).strip("._-")
    if not safe:
        raise ValueError("packetId is required")
    return safe[:160]


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


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_directory(path.parent)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
            tmp_path = Path(fh.name)
        try:
            parent_stat = path.parent.stat()
            os.chown(tmp_path, parent_stat.st_uid, parent_stat.st_gid)
        except OSError:
            pass
        tmp_path.chmod(0o664)
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    _ensure_directory(path.parent)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_optional_json(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _recovery_path(inbox: Path, packet_path: Path) -> Path:
    return inbox / "recoveries" / packet_path.name


def _recovery_rearm_directory(inbox: Path, packet_path: Path) -> Path:
    return inbox / "recovery-rearms" / packet_path.stem


def _recovery_rearm_path(
    inbox: Path,
    packet_path: Path,
    attempt: int,
) -> Path:
    return _recovery_rearm_directory(inbox, packet_path) / f"{attempt:06d}.json"


def _recovery_identity(packet: DevTaskPacket) -> Dict[str, Any]:
    signature = (
        packet.signature.model_dump(mode="json", by_alias=True)
        if packet.signature is not None
        else None
    )
    return {
        "packet_id": packet.packet_id,
        "packet_digest": packet_digest(packet),
        "signature": signature,
        "signed_provenance": _admission_provenance(packet),
    }


def _validate_packet_leaf(
    path: Path,
    *,
    packet_id: str,
    expected_identity: Mapping[str, Any] | None = None,
    key_store: Optional[Dict[str, bytes]] = None,
) -> tuple[DevTaskPacket, Dict[str, Any]]:
    packet = packet_from_payload(_read_json(path))
    verify_packet(packet, key_store=key_store)
    if packet.packet_id != packet_id:
        raise ValueError(
            f"Bridge failed recovery leaf {path} contains packet id "
            f"{packet.packet_id!r}, not {packet_id!r}"
        )
    identity = _recovery_identity(packet)
    if expected_identity is not None and identity != expected_identity:
        raise ValueError(
            f"Packet id {packet_id!r} recovery identity does not match its "
            "signed packet digest, task specs, or provenance"
        )
    return packet, identity


def _validate_recovery_record(
    record: Mapping[str, Any],
    *,
    packet_id: str,
    identity: Mapping[str, Any],
) -> str:
    if record.get("schema") != FAILED_RECOVERY_SCHEMA:
        raise ValueError("Bridge failed recovery record schema is unsupported")
    if record.get("packet_id") != packet_id:
        raise ValueError("Bridge failed recovery record packet id mismatch")
    if record.get("identity") != identity:
        raise ValueError(
            "Bridge failed recovery record signed identity or provenance mismatch"
        )
    state = str(record.get("state") or "").strip()
    if state not in {"prepared", "queued"}:
        raise ValueError("Bridge failed recovery record state is unsupported")
    return state


def _validate_recovery_receipt_payload(
    receipt: Mapping[str, Any],
    *,
    packet_id: str,
    packet_digest_value: str,
    expected_status: str,
) -> Dict[str, Any]:
    if str(receipt.get("packetId") or "") != packet_id:
        raise ValueError("Bridge failed recovery receipt packet id mismatch")
    status = str(receipt.get("status") or "")
    if expected_status == "failed":
        if status not in {"failed", "error"}:
            raise ValueError(
                "Bridge failed recovery requires an exact failed/error receipt"
            )
    elif status != "processed":
        raise ValueError("Bridge completed recovery receipt is not processed")
    result = receipt.get("result")
    audit_refs = result.get("auditRefs") if isinstance(result, Mapping) else None
    observed_digest = (
        str(audit_refs.get("packetDigest") or "")
        if isinstance(audit_refs, Mapping)
        else ""
    )
    result_packet_id = (
        str(result.get("packetId") or "") if isinstance(result, Mapping) else ""
    )
    if result_packet_id != packet_id or observed_digest != packet_digest_value:
        raise ValueError(
            "Bridge failed recovery receipt does not bind the exact signed packet"
        )
    return dict(receipt)


def _validate_recovery_receipt(
    receipt_path: Path,
    *,
    packet_id: str,
    packet_digest_value: str,
    expected_status: str,
) -> Dict[str, Any]:
    return _validate_recovery_receipt_payload(
        _read_json(receipt_path),
        packet_id=packet_id,
        packet_digest_value=packet_digest_value,
        expected_status=expected_status,
    )


def _rearm_attempt(record: Mapping[str, Any]) -> int:
    raw_attempt = record.get("rearm_attempt", 0)
    if isinstance(raw_attempt, bool):
        raise ValueError("Bridge failed recovery retry evidence is malformed")
    try:
        attempt = int(raw_attempt)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Bridge failed recovery retry evidence is malformed"
        ) from exc
    if attempt < 0 or str(raw_attempt).strip() != str(attempt):
        raise ValueError("Bridge failed recovery retry evidence is malformed")
    return attempt


def _next_rearm_recovery_record(
    previous: Mapping[str, Any],
    *,
    attempt: int,
    evidence_path: Path,
    previous_sha256: str,
    receipt_sha256: str,
    prepared_at: str,
) -> Dict[str, Any]:
    return {
        **previous,
        "state": "queued",
        "rearm_attempt": attempt,
        "rearmed_at": prepared_at,
        "last_rearm": {
            "attempt": attempt,
            "evidence_path": str(evidence_path),
            "previous_recovery_sha256": previous_sha256,
            "failed_receipt_sha256": receipt_sha256,
            "prepared_at": prepared_at,
        },
    }


def _prepare_rearm_evidence(
    *,
    recovery: Mapping[str, Any],
    receipt: Mapping[str, Any],
    identity: Mapping[str, Any],
    packet_id: str,
    source_path: Path,
    target_path: Path,
    evidence_path: Path,
    attempt: int,
) -> Dict[str, Any]:
    prepared_at = _now()
    previous = dict(recovery)
    failed_receipt = dict(receipt)
    previous_sha256 = _canonical_json_sha256(previous)
    receipt_sha256 = _canonical_json_sha256(failed_receipt)
    next_recovery = _next_rearm_recovery_record(
        previous,
        attempt=attempt,
        evidence_path=evidence_path,
        previous_sha256=previous_sha256,
        receipt_sha256=receipt_sha256,
        prepared_at=prepared_at,
    )
    return {
        "schema": FAILED_RECOVERY_REARM_SCHEMA,
        "state": "prepared",
        "packet_id": packet_id,
        "identity": dict(identity),
        "attempt": attempt,
        "source_path": str(source_path),
        "target_path": str(target_path),
        "previous_recovery": previous,
        "previous_recovery_sha256": previous_sha256,
        "current_failed_receipt": failed_receipt,
        "current_failed_receipt_sha256": receipt_sha256,
        "next_recovery": next_recovery,
        "next_recovery_sha256": _canonical_json_sha256(next_recovery),
        "prepared_at": prepared_at,
    }


def _rearm_evidence_records(
    inbox: Path,
    packet_path: Path,
) -> list[tuple[Path, Dict[str, Any]]]:
    directory = _recovery_rearm_directory(inbox, packet_path)
    if not directory.exists():
        return []
    records: list[tuple[Path, Dict[str, Any]]] = []
    for path in sorted(item for item in directory.iterdir() if item.is_file()):
        if not re.fullmatch(r"[0-9]{6}\.json", path.name):
            raise ValueError(
                "Bridge failed recovery retry evidence has an ambiguous filename"
            )
        records.append((path, _read_json(path)))
    return records


def _validate_rearm_evidence_chain(
    *,
    inbox: Path,
    packet_path: Path,
    recovery: Mapping[str, Any],
    packet_id: str,
    identity: Mapping[str, Any],
) -> tuple[Path, Dict[str, Any]] | None:
    records = _rearm_evidence_records(inbox, packet_path)
    if not records:
        if _rearm_attempt(recovery) != 0 or recovery.get("last_rearm") is not None:
            raise ValueError(
                "Bridge failed recovery retry evidence is missing or stale"
            )
        return None

    previous_next: Mapping[str, Any] | None = None
    prepared: tuple[Path, Dict[str, Any]] | None = None
    for expected_attempt, (path, evidence) in enumerate(records, start=1):
        if evidence.get("schema") != FAILED_RECOVERY_REARM_SCHEMA:
            raise ValueError("Bridge failed recovery retry evidence schema is unsupported")
        if evidence.get("packet_id") != packet_id or evidence.get("identity") != identity:
            raise ValueError(
                "Bridge failed recovery retry evidence identity or provenance mismatch"
            )
        if evidence.get("attempt") != expected_attempt:
            raise ValueError("Bridge failed recovery retry evidence sequence is ambiguous")
        if path != _recovery_rearm_path(inbox, packet_path, expected_attempt):
            raise ValueError("Bridge failed recovery retry evidence path is ambiguous")
        if evidence.get("source_path") != str(packet_path):
            raise ValueError("Bridge failed recovery retry evidence source path mismatch")
        target_path = inbox / "pending" / packet_path.name
        if evidence.get("target_path") != str(target_path):
            raise ValueError("Bridge failed recovery retry evidence target path mismatch")

        previous = evidence.get("previous_recovery")
        next_recovery = evidence.get("next_recovery")
        failed_receipt = evidence.get("current_failed_receipt")
        if not all(
            isinstance(item, Mapping)
            for item in (previous, next_recovery, failed_receipt)
        ):
            raise ValueError("Bridge failed recovery retry evidence is malformed")
        assert isinstance(previous, Mapping)
        assert isinstance(next_recovery, Mapping)
        assert isinstance(failed_receipt, Mapping)
        if evidence.get("previous_recovery_sha256") != _canonical_json_sha256(previous):
            raise ValueError(
                "Bridge failed recovery retry evidence previous digest mismatch"
            )
        if evidence.get("current_failed_receipt_sha256") != _canonical_json_sha256(
            failed_receipt
        ):
            raise ValueError(
                "Bridge failed recovery retry evidence receipt digest mismatch"
            )
        if evidence.get("next_recovery_sha256") != _canonical_json_sha256(next_recovery):
            raise ValueError("Bridge failed recovery retry evidence next digest mismatch")

        _validate_recovery_record(
            previous,
            packet_id=packet_id,
            identity=identity,
        )
        _validate_recovery_record(
            next_recovery,
            packet_id=packet_id,
            identity=identity,
        )
        if _rearm_attempt(previous) != expected_attempt - 1:
            raise ValueError("Bridge failed recovery retry evidence sequence is stale")
        if previous_next is not None and previous != previous_next:
            raise ValueError("Bridge failed recovery retry evidence chain is stale")
        _validate_recovery_receipt_payload(
            failed_receipt,
            packet_id=packet_id,
            packet_digest_value=str(identity["packet_digest"]),
            expected_status="failed",
        )
        prepared_at = str(evidence.get("prepared_at") or "")
        if not prepared_at:
            raise ValueError("Bridge failed recovery retry evidence is malformed")
        expected_next = _next_rearm_recovery_record(
            previous,
            attempt=expected_attempt,
            evidence_path=path,
            previous_sha256=str(evidence["previous_recovery_sha256"]),
            receipt_sha256=str(evidence["current_failed_receipt_sha256"]),
            prepared_at=prepared_at,
        )
        if next_recovery != expected_next:
            raise ValueError("Bridge failed recovery retry evidence next record mismatch")

        state = str(evidence.get("state") or "")
        if state == "prepared":
            if prepared is not None or expected_attempt != len(records):
                raise ValueError(
                    "Bridge failed recovery retry evidence prepared-record ambiguity"
                )
            prepared = (path, evidence)
        elif state == "queued":
            if not str(evidence.get("queued_at") or ""):
                raise ValueError("Bridge failed recovery retry evidence is malformed")
        else:
            raise ValueError("Bridge failed recovery retry evidence state is unsupported")
        previous_next = next_recovery

    assert previous_next is not None
    if prepared is None:
        if recovery != previous_next:
            raise ValueError("Bridge failed recovery retry evidence chain is stale")
    else:
        evidence = prepared[1]
        if recovery not in (
            evidence["previous_recovery"],
            evidence["next_recovery"],
        ):
            raise ValueError("Bridge failed recovery retry evidence chain is stale")
    return prepared


def _validate_admission_and_replay_collisions(
    packet: DevTaskPacket,
    *,
    repo_root: str,
) -> tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
    digest = packet_digest(packet)
    expected_path = admission_record_path(
        repo_root=repo_root,
        packet_id=packet.packet_id,
        packet_digest=digest,
    )
    directory = expected_path.parent
    if directory.exists():
        conflicting = sorted(
            path
            for path in directory.glob(f"{_safe_packet_id(packet.packet_id)}--*.json")
            if path != expected_path
        )
        if conflicting:
            raise ValueError(
                f"Packet id {packet.packet_id!r} has a conflicting admission record"
            )
    admission = load_admission_record(
        repo_root=repo_root,
        packet_id=packet.packet_id,
        packet_digest=digest,
        expected_provenance=_admission_provenance(packet),
    )
    replay = replay_record(packet.packet_id, repo_root=repo_root)
    if replay is not None:
        replay_digest = str(replay.get("digest") or "").strip()
        if replay_digest != digest:
            raise ValueError(
                f"Packet id {packet.packet_id!r} has a conflicting replay record"
            )
        if admission is None:
            raise ValueError(
                f"Packet id {packet.packet_id!r} has replay state without exact admission"
            )
    return admission, replay


def _claim_path(inbox: Path, packet_path: Path) -> Path:
    return inbox / "claims" / packet_path.name


def _processing_fence_path(inbox: Path, packet_path: Path) -> Path:
    return inbox / "claims" / f"{packet_path.name}.lock"


def _try_acquire_processing_fence(inbox: Path, packet_path: Path) -> int | None:
    """Keep an alive drainer authoritative even after JSON claim expiry."""

    path = _processing_fence_path(inbox, packet_path)
    descriptor = _open_regular_fence_file(
        inbox,
        ("claims",),
        path.name,
        description="Bridge inbox processing fence",
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _release_processing_fence(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _retry_path(inbox: Path, packet_path: Path) -> Path:
    return inbox / "retries" / packet_path.name


def _packet_identity(path: Path) -> tuple[DevTaskPacket, Dict[str, str]]:
    packet = packet_from_payload(_read_json(path))
    return packet, {
        "packet_id": packet.packet_id,
        "packet_digest": packet_digest(packet),
    }


def _identity_matches(payload: Mapping[str, Any], identity: Mapping[str, str]) -> bool:
    return all(str(payload.get(key) or "") == value for key, value in identity.items())


def _epoch(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _processing_claim_owned(
    inbox: Path,
    packet_path: Path,
    claim_token: str,
) -> bool:
    claim = _read_optional_json(_claim_path(inbox, packet_path))
    return bool(claim and str(claim.get("claim_token") or "") == claim_token)


def _release_processing_claim(
    inbox: Path,
    packet_path: Path,
    claim_token: str,
) -> bool:
    claim_path = _claim_path(inbox, packet_path)
    if not _processing_claim_owned(inbox, packet_path, claim_token):
        return False
    claim_path.unlink(missing_ok=True)
    return True


def _clear_processing_metadata(
    inbox: Path,
    packet_path: Path,
    claim_token: str,
) -> None:
    with _file_lock(inbox / ".queue.lock"):
        if not _processing_claim_owned(inbox, packet_path, claim_token):
            return
        _retry_path(inbox, packet_path).unlink(missing_ok=True)
        _release_processing_claim(inbox, packet_path, claim_token)


def _schedule_processing_retry(
    inbox: Path,
    packet_path: Path,
    claim_token: str,
    identity: Mapping[str, str],
) -> Dict[str, Any] | None:
    """Persist exact-identity exponential backoff and release the short claim."""

    with _file_lock(inbox / ".queue.lock"):
        if not _processing_claim_owned(inbox, packet_path, claim_token):
            return None
        retry_path = _retry_path(inbox, packet_path)
        previous = _read_optional_json(retry_path)
        previous_attempt = (
            int(previous.get("attempt") or 0)
            if previous and _identity_matches(previous, identity)
            else 0
        )
        attempt = previous_attempt + 1
        exponent = min(attempt - 1, 16)
        delay_seconds = min(
            RETRY_MAX_SECONDS,
            RETRY_BASE_SECONDS * (2**exponent),
        )
        now = datetime.now(timezone.utc)
        retry = {
            "schema": PROCESSING_RETRY_SCHEMA,
            **identity,
            "attempt": attempt,
            "delay_seconds": delay_seconds,
            "scheduled_at": now.isoformat().replace("+00:00", "Z"),
            "next_attempt_at": (now + timedelta(seconds=delay_seconds))
            .isoformat()
            .replace("+00:00", "Z"),
            "next_attempt_epoch": time.time() + delay_seconds,
        }
        _write_json_atomic(retry_path, retry)
        _release_processing_claim(inbox, packet_path, claim_token)
        return retry


def _extract_packet_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload.get("packet"), Mapping):
        return payload["packet"]
    if isinstance(payload.get("taskPacket"), Mapping):
        return payload["taskPacket"]
    meta = payload.get("meta")
    if isinstance(meta, Mapping) and isinstance(meta.get("taskPacket"), Mapping):
        return meta["taskPacket"]
    data = payload.get("data")
    if isinstance(data, Mapping) and ("packetId" in data or "packet_id" in data):
        return data
    if "packetId" in payload or "packet_id" in payload:
        return payload
    raise ValueError("Could not find DevTaskPacket payload in inbox item")


def packet_from_payload(payload: Mapping[str, Any]) -> DevTaskPacket:
    return DevTaskPacket(**_extract_packet_payload(payload))


def queue_task_packet(
    packet: DevTaskPacket,
    *,
    repo_root: Optional[str] = None,
    inbox_dir: Optional[str] = None,
    key_store: Optional[Dict[str, bytes]] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify and queue *packet* for supervisor pickup.

    Returns a small receipt. Duplicate pending/processing/processed/failed
    packet IDs and durable receipts are not overwritten.
    """
    root = _repo_root(repo_root)
    inbox = _inbox_root(str(root), inbox_dir)
    verify_packet(packet, key_store=key_store)
    safe_id = _safe_packet_id(packet.packet_id)
    queued_at = _now()
    envelope = {
        "version": "pantheon.assistant.dev-packet-inbox.v1",
        "queuedAt": queued_at,
        "source": source or "repo_local",
        "taskPacket": packet.model_dump(mode="json", by_alias=True),
    }
    paths = {
        "pending": inbox / "pending" / f"{safe_id}.json",
        "processing": inbox / "processing" / f"{safe_id}.json",
        "processed": inbox / "processed" / f"{safe_id}.json",
        "failed": inbox / "failed" / f"{safe_id}.json",
        "receipt": inbox / "receipts" / f"{safe_id}.json",
    }
    with _file_lock(inbox / ".queue.lock"):
        if has_seen_packet(packet.packet_id, repo_root=str(root)):
            return {
                "status": "replay_rejected",
                "packetId": packet.packet_id,
                "queued": False,
                "inbox": str(inbox),
            }
        existing = [name for name, path in paths.items() if path.exists()]
        if existing:
            return {
                "status": "duplicate",
                "packetId": packet.packet_id,
                "queued": False,
                "existing": existing[0],
                "inbox": str(inbox),
            }

        _write_json_atomic(paths["pending"], envelope)
    return {
        "status": "queued",
        "packetId": packet.packet_id,
        "queued": True,
        "queuedAt": queued_at,
        "path": str(paths["pending"]),
        "inbox": str(inbox),
        "taskCount": len(packet.tasks),
    }


def queue_payload(
    payload: Mapping[str, Any],
    *,
    repo_root: Optional[str] = None,
    inbox_dir: Optional[str] = None,
    key_store: Optional[Dict[str, bytes]] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return queue_task_packet(
        packet_from_payload(payload),
        repo_root=repo_root,
        inbox_dir=inbox_dir,
        key_store=key_store,
        source=source,
    )


def recover_failed_task_packet(
    packet_id: str,
    *,
    repo_root: Optional[str] = None,
    inbox_dir: Optional[str] = None,
    key_store: Optional[Dict[str, bytes]] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Requeue one exact signed failed leaf through a durable recovery record.

    The whole decision and failed-to-pending transition runs under the inbox
    queue lock plus the packet's processing/dispatch fences.  A durable
    `prepared` record is written before rename so a crash on either side of
    the rename can be resumed without accepting an unregistered pending leaf.
    """

    requested_id = str(packet_id or "").strip()
    if not requested_id:
        raise ValueError("packetId is required for failed recovery")
    root = _repo_root(repo_root)
    inbox = _inbox_root(str(root), inbox_dir)
    leaf = Path(f"{_safe_packet_id(requested_id)}.json")
    paths = {
        name: inbox / name / leaf.name
        for name in ("pending", "processing", "processed", "failed", "receipts")
    }
    recovery_path = _recovery_path(inbox, paths["failed"])

    with _file_lock(inbox / ".queue.lock"):
        processing_fence = _try_acquire_processing_fence(inbox, paths["failed"])
        if processing_fence is None:
            raise ValueError(
                f"Packet id {requested_id!r} is fenced by a live inbox drainer"
            )
        dispatch_fence: int | None = None
        try:
            dispatch_fence = _try_acquire_dispatch_fence(
                str(root),
                requested_id,
            )
            if dispatch_fence is None:
                raise ValueError(
                    f"Packet id {requested_id!r} is fenced by a live dispatcher"
                )

            recovery = _read_optional_json(recovery_path)
            queue_states = [
                name
                for name in ("pending", "processing", "processed")
                if paths[name].exists()
            ]
            if len(queue_states) > 1:
                raise ValueError(
                    f"Packet id {requested_id!r} has conflicting queue states: "
                    + ", ".join(queue_states)
                )

            failed_path = paths["failed"]
            if not failed_path.exists():
                if recovery is None:
                    conflict = queue_states[0] if queue_states else "missing"
                    raise ValueError(
                        f"Packet id {requested_id!r} has no recoverable failed leaf "
                        f"(observed {conflict})"
                    )
                if not queue_states:
                    raise ValueError(
                        f"Packet id {requested_id!r} recovery record has no packet leaf"
                    )
                state_name = queue_states[0]
                packet, identity = _validate_packet_leaf(
                    paths[state_name],
                    packet_id=requested_id,
                    expected_identity=recovery.get("identity"),
                    key_store=key_store,
                )
                recovery_state = _validate_recovery_record(
                    recovery,
                    packet_id=requested_id,
                    identity=identity,
                )
                receipt = _validate_recovery_receipt(
                    paths["receipts"],
                    packet_id=requested_id,
                    packet_digest_value=str(identity["packet_digest"]),
                    expected_status=(
                        "processed" if state_name == "processed" else "failed"
                    ),
                )
                admission, replay = _validate_admission_and_replay_collisions(
                    packet,
                    repo_root=str(root),
                )
                if state_name == "processed" and (
                    admission is None or replay is None
                ):
                    raise ValueError(
                        f"Packet id {requested_id!r} processed recovery is not "
                        "bound to exact admission and replay state"
                    )
                if recovery_state == "prepared":
                    completed = {
                        **recovery,
                        "state": "queued",
                        "recovered_at": _now(),
                    }
                    _write_json_atomic(recovery_path, completed)
                return {
                    "status": (
                        "already_completed"
                        if state_name == "processed"
                        else "already_recovered"
                    ),
                    "packetId": requested_id,
                    "packetDigest": identity["packet_digest"],
                    "queueState": state_name,
                    "receiptSha256": _canonical_json_sha256(receipt),
                    "recoveryPath": str(recovery_path),
                    "inbox": str(inbox),
                }

            if queue_states:
                raise ValueError(
                    f"Packet id {requested_id!r} has conflicting failed and "
                    f"{queue_states[0]} leaves"
                )

            packet, identity = _validate_packet_leaf(
                failed_path,
                packet_id=requested_id,
                key_store=key_store,
            )
            receipt = _validate_recovery_receipt(
                paths["receipts"],
                packet_id=requested_id,
                packet_digest_value=str(identity["packet_digest"]),
                expected_status="failed",
            )
            _validate_admission_and_replay_collisions(
                packet,
                repo_root=str(root),
            )

            if recovery is not None:
                recovery_state = _validate_recovery_record(
                    recovery,
                    packet_id=requested_id,
                    identity=identity,
                )
                if recovery_state != "prepared":
                    raise ValueError(
                        f"Packet id {requested_id!r} has a queued recovery record "
                        "but remains in failed storage"
                    )
            else:
                recovery = {
                    "schema": FAILED_RECOVERY_SCHEMA,
                    "state": "prepared",
                    "packet_id": requested_id,
                    "identity": identity,
                    "source": source or "operator_exact_failed_recovery",
                    "source_path": str(failed_path),
                    "target_path": str(paths["pending"]),
                    "failed_receipt_sha256": _canonical_json_sha256(receipt),
                    "prepared_at": _now(),
                }
                _write_json_atomic(recovery_path, recovery)

            for metadata_path in (
                _claim_path(inbox, failed_path),
                _retry_path(inbox, failed_path),
            ):
                metadata = _read_optional_json(metadata_path)
                if metadata is None:
                    continue
                metadata_identity = {
                    "packet_id": metadata.get("packet_id"),
                    "packet_digest": metadata.get("packet_digest"),
                }
                expected_metadata_identity = {
                    "packet_id": requested_id,
                    "packet_digest": identity["packet_digest"],
                }
                if metadata_identity != expected_metadata_identity:
                    raise ValueError(
                        f"Packet id {requested_id!r} has conflicting "
                        f"{metadata_path.parent.name} metadata"
                    )
                metadata_path.unlink(missing_ok=True)
                _fsync_directory(metadata_path.parent)

            _ensure_directory(paths["pending"].parent)
            os.replace(failed_path, paths["pending"])
            _fsync_directory(failed_path.parent)
            _fsync_directory(paths["pending"].parent)
            completed = {
                **recovery,
                "state": "queued",
                "recovered_at": _now(),
            }
            _write_json_atomic(recovery_path, completed)
            return {
                "status": "recovered",
                "packetId": requested_id,
                "packetDigest": identity["packet_digest"],
                "queueState": "pending",
                "receiptSha256": _canonical_json_sha256(receipt),
                "recoveryPath": str(recovery_path),
                "path": str(paths["pending"]),
                "inbox": str(inbox),
            }
        finally:
            if dispatch_fence is not None:
                _release_dispatch_fence(dispatch_fence)
            _release_processing_fence(processing_fence)


def _pending_files(inbox: Path) -> Iterable[Path]:
    pending = inbox / "pending"
    if not pending.exists():
        return []
    return sorted(path for path in pending.glob("*.json") if path.is_file())


def _processing_files(inbox: Path) -> Iterable[Path]:
    processing = inbox / "processing"
    if not processing.exists():
        return []
    return sorted(path for path in processing.glob("*.json") if path.is_file())


def _move(path: Path, target_dir: Path) -> Path:
    _ensure_directory(target_dir)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}-{int(datetime.now(timezone.utc).timestamp())}{path.suffix}"
    os.replace(path, target)
    _fsync_directory(path.parent)
    if target.parent != path.parent:
        _fsync_directory(target.parent)
    return target


def _finalize_processing(path: Path, target_dir: Path) -> Path:
    """Move a processing item once, or discard a stale duplicate safely."""

    _ensure_directory(target_dir)
    target = target_dir / path.name
    if target.exists():
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return target
    return _move(path, target_dir)


def _receipt_target_dir(inbox: Path, receipt: Mapping[str, Any]) -> Path:
    return inbox / ("failed" if receipt.get("status") in {"failed", "error"} else "processed")


def _claim_processing_files(
    inbox: Path,
    max_items: Optional[int],
) -> list[tuple[Path, str, int]]:
    """Claim processing items briefly without holding a lock during dispatch."""

    with _file_lock(inbox / ".queue.lock"):
        processing = list(_processing_files(inbox))
        pending = list(_pending_files(inbox))
        for path in pending:
            processing.append(_move(path, inbox / "processing"))
        claims: list[tuple[Path, str, int]] = []
        now = time.time()
        for path in processing:
            if max_items is not None and len(claims) >= max_items:
                break
            fence = _try_acquire_processing_fence(inbox, path)
            if fence is None:
                continue
            claimed = False
            try:
                try:
                    _packet, identity = _packet_identity(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    # Invalid packet content is still claimed so the ordinary
                    # drain error path can durably fail it instead of hot-looping.
                    identity = {
                        "packet_id": path.stem,
                        "packet_digest": "invalid",
                    }
                retry_path = _retry_path(inbox, path)
                try:
                    retry = _read_optional_json(retry_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    retry = None
                if retry is not None:
                    # Unsigned retry metadata is advisory only.  A forged or stale
                    # identity cannot suppress a valid signed packet.
                    if _identity_matches(retry, identity):
                        next_attempt = _epoch(retry.get("next_attempt_epoch"))
                        if next_attempt is not None and next_attempt > now:
                            continue
                    else:
                        retry_path.unlink(missing_ok=True)

                claim_path = _claim_path(inbox, path)
                try:
                    existing = _read_optional_json(claim_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    existing = None
                if existing is not None and _identity_matches(existing, identity):
                    expires_at = _epoch(existing.get("expires_at_epoch"))
                    if expires_at is not None and expires_at > now:
                        continue

                claim_token = os.urandom(24).hex()
                _write_json_atomic(
                    claim_path,
                    {
                        "schema": PROCESSING_CLAIM_SCHEMA,
                        **identity,
                        "claim_token": claim_token,
                        "claimed_at": _now(),
                        "claimed_at_epoch": now,
                        "expires_at_epoch": now + PROCESSING_CLAIM_TTL_SECONDS,
                        "owner_pid": os.getpid(),
                    },
                )
                claims.append((path, claim_token, fence))
                claimed = True
            finally:
                if not claimed:
                    _release_processing_fence(fence)
        return claims


def drain_task_packet_inbox(
    *,
    repo_root: Optional[str] = None,
    inbox_dir: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    dispatch_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Drain queued packets through the verifier-backed governed dispatcher."""
    root = _repo_root(repo_root)
    inbox = _inbox_root(str(root), inbox_dir)
    max_items = max(0, int(limit)) if limit is not None else None
    processed: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []

    # Queue/claim locks protect only short local file transitions.  No inbox
    # lock is held while the governed status subprocess or canonical readback
    # runs.  Exact processing claims keep concurrent drainers from applying the
    # same item, and expire so a crashed drainer is recoverable.
    claimed_files = (
        [(path, "", None) for path in list(_pending_files(inbox))]
        if dry_run
        else _claim_processing_files(inbox, max_items)
    )
    if dry_run and max_items is not None:
        claimed_files = claimed_files[:max_items]

    for path, claim_token, processing_fence in claimed_files:
        receipt_path = inbox / "receipts" / path.name
        existing_receipt = not dry_run and receipt_path.exists()
        identity: Dict[str, str] | None = None
        receipt: Dict[str, Any] = {
            "path": str(path),
            "drainedAt": _now(),
            "dryRun": dry_run,
        }
        try:
            packet, identity = _packet_identity(path)
            receipt["packetId"] = packet.packet_id
            result = dispatch_task_packet(
                BridgeDispatchRequest(
                    packet=packet,
                    repoRoot=str(root),
                    dryRun=dry_run,
                ),
                runtime_env=dispatch_env,
            )
            receipt["result"] = result.model_dump(mode="json", by_alias=True)
            if result.retryable and not dry_run:
                receipt["status"] = "retryable"
                receipt["retryable"] = True
            elif (
                result.replay_rejected
                and not dry_run
                and not result.errors
                and result.admission_record is not None
                and result.admission_status == "admitted_replay"
            ):
                receipt["status"] = "processed"
                receipt["recoveredFromReplay"] = True
                if existing_receipt:
                    # A receipt is never trusted on its own.  This flag means
                    # the exact signed packet, durable admission, and canonical
                    # active/archive readback were all revalidated first.
                    receipt["recoveredFromReceipt"] = True
            elif result.replay_rejected:
                receipt["status"] = "failed" if not dry_run else "replay_rejected"
                receipt["nonAdmittedReplay"] = True
                if existing_receipt:
                    receipt["invalidExistingReceipt"] = True
            elif result.errors:
                receipt["status"] = "failed"
            else:
                receipt["status"] = "dry_run" if dry_run else "processed"
        except Exception as exc:
            receipt["status"] = "error"
            receipt["error"] = str(exc)
        try:
            if dry_run:
                (errors if receipt["status"] == "error" else processed).append(receipt)
                continue

            if receipt.get("retryable") is True:
                try:
                    retry = _schedule_processing_retry(
                        inbox,
                        path,
                        claim_token,
                        identity or {
                            "packet_id": path.stem,
                            "packet_digest": "invalid",
                        },
                    )
                except Exception as exc:
                    receipt["retryPersistenceError"] = str(exc)
                    with _file_lock(inbox / ".queue.lock"):
                        _release_processing_claim(inbox, path, claim_token)
                else:
                    if retry is not None:
                        receipt["retry"] = retry
                errors.append(receipt)
                continue

            target_dir = _receipt_target_dir(inbox, receipt)
            receipt["archivedPath"] = str(target_dir / path.name)
            try:
                # Receipt durability is the commit point, but recovery still
                # revalidates admission and canonical readback before trusting it.
                _write_json_atomic(receipt_path, receipt)
            except Exception as exc:
                receipt["persistenceError"] = str(exc)
                try:
                    _schedule_processing_retry(
                        inbox,
                        path,
                        claim_token,
                        identity or {
                            "packet_id": path.stem,
                            "packet_digest": "invalid",
                        },
                    )
                except Exception:
                    with _file_lock(inbox / ".queue.lock"):
                        _release_processing_claim(inbox, path, claim_token)
                errors.append(receipt)
                continue

            try:
                receipt["archivedPath"] = str(_finalize_processing(path, target_dir))
            except Exception as exc:
                receipt["recoveryError"] = str(exc)
                _clear_processing_metadata(inbox, path, claim_token)
                errors.append(receipt)
                continue

            _clear_processing_metadata(inbox, path, claim_token)
            if receipt["status"] == "error":
                errors.append(receipt)
            else:
                processed.append(receipt)
        finally:
            # Keep the live drainer authoritative through receipt durability,
            # archive/finalize, and claim/retry metadata cleanup.  JSON claim
            # expiry is crash recovery metadata, not an overlap permission.
            _release_processing_fence(processing_fence)

    return {
        "status": "drained",
        "inbox": str(inbox),
        "processedCount": len(processed),
        "errorCount": len(errors) + sum(1 for item in processed if item.get("status") == "failed"),
        "dryRun": dry_run,
        "packets": processed,
        "errors": errors,
    }
