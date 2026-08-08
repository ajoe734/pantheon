"""File-backed inbox for assistant DevTaskPacket supervisor pickup.

The Web API emits signed packets but does not execute shell. Repo-local
automation can queue those packets into this inbox; the supervisor drains it
through the verifier-backed dispatcher and its installed governed status
runtime binding.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from .dev_bridge_dispatcher import dispatch_task_packet, _open_regular_fence_file
from .dev_bridge_models import BridgeDispatchRequest, DevTaskPacket
from .dev_bridge_signer import has_seen_packet, packet_digest, verify_packet


DEFAULT_INBOX_DIR = ".orchestrator/assistant-dev-packets"
PROCESSING_CLAIM_SCHEMA = "pantheon.assistant-dev-packet-processing-claim.v1"
PROCESSING_RETRY_SCHEMA = "pantheon.assistant-dev-packet-retry.v1"
PROCESSING_CLAIM_TTL_SECONDS = 300.0
RETRY_BASE_SECONDS = 0.25
RETRY_MAX_SECONDS = 5.0
FRESH_ADMISSION_RESERVE = 1


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


def _processing_admission_candidates(
    inbox: Path,
    processing: Iterable[Path],
    *,
    now: float,
) -> list[tuple[Path, Dict[str, str]]]:
    """Order due processing files with a bounded first-attempt reserve.

    Retry metadata remains advisory: it can only identify a packet as a due
    retry after its exact packet identity matches.  Authentication, replay
    protection, and dispatch fencing still occur in ``dispatch_task_packet``
    after this short queue-lock phase.

    A bounded drain reserves one slot for a packet with no matching retry
    metadata whenever due retries are also present.  Without that reserve,
    alphabetically earlier retry files can consume every ``limit`` slot on
    every tick and permanently starve a newer signed packet in ``processing``.
    """

    fresh: list[tuple[Path, Dict[str, str]]] = []
    due_retries: list[tuple[Path, Dict[str, str]]] = []
    for path in processing:
        try:
            _packet, identity = _packet_identity(path)
        except (OSError, ValueError, json.JSONDecodeError):
            # Invalid packet content is still allowed through the ordinary
            # drain error path so it can be terminally failed instead of
            # hot-looping.
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
                due_retries.append((path, identity))
                continue
            retry_path.unlink(missing_ok=True)
        fresh.append((path, identity))

    if fresh and due_retries:
        # One fresh packet must receive an attempt in every bounded drain;
        # remaining capacity remains available to due retries.  If a selected
        # item loses its live fence below, later candidates backfill the slot.
        return [*fresh[:FRESH_ADMISSION_RESERVE], *due_retries, *fresh[FRESH_ADMISSION_RESERVE:]]
    return [*fresh, *due_retries]


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
        candidates = _processing_admission_candidates(inbox, processing, now=now)
        for path, identity in candidates:
            if max_items is not None and len(claims) >= max_items:
                break
            fence = _try_acquire_processing_fence(inbox, path)
            if fence is None:
                continue
            claimed = False
            try:
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
