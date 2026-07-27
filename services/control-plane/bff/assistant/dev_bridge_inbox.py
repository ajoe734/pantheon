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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from .dev_bridge_dispatcher import dispatch_task_packet
from .dev_bridge_models import BridgeDispatchRequest, DevTaskPacket
from .dev_bridge_signer import has_seen_packet, verify_packet


DEFAULT_INBOX_DIR = ".orchestrator/assistant-dev-packets"


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


def _claim_processing_files(inbox: Path, max_items: Optional[int]) -> list[Path]:
    with _file_lock(inbox / ".queue.lock"):
        processing = list(_processing_files(inbox))
        if max_items is not None:
            processing = processing[:max_items]
        remaining = None if max_items is None else max(0, max_items - len(processing))
        pending = list(_pending_files(inbox))
        if remaining is not None:
            pending = pending[:remaining]
        for path in pending:
            processing.append(_move(path, inbox / "processing"))
        return processing


def drain_task_packet_inbox(
    *,
    repo_root: Optional[str] = None,
    inbox_dir: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Drain queued packets through the verifier-backed governed dispatcher."""
    root = _repo_root(repo_root)
    inbox = _inbox_root(str(root), inbox_dir)
    max_items = max(0, int(limit)) if limit is not None else None
    processed: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []

    # One drain process owns processing at a time. Queue writers use a separate
    # short-held lock and remain available while ai_status subprocesses run.
    with _file_lock(inbox / ".drain.lock"):
        files = list(_pending_files(inbox)) if dry_run else _claim_processing_files(inbox, max_items)
        if dry_run and max_items is not None:
            files = files[:max_items]

        for path in files:
            receipt_path = inbox / "receipts" / path.name
            if not dry_run and receipt_path.exists():
                # A crash may occur after the durable receipt but before the
                # processing->processed rename. Finalize without redispatch.
                try:
                    recovered = _read_json(receipt_path)
                    target = _finalize_processing(path, _receipt_target_dir(inbox, recovered))
                    recovered = dict(recovered)
                    recovered["archivedPath"] = str(target)
                    recovered["recoveredFromReceipt"] = True
                    if recovered.get("status") == "error":
                        errors.append(recovered)
                    else:
                        processed.append(recovered)
                except Exception as exc:
                    errors.append(
                        {
                            "path": str(path),
                            "status": "error",
                            "error": f"receipt recovery failed: {exc}",
                            "recoveredFromReceipt": True,
                        }
                    )
                continue

            receipt: Dict[str, Any] = {
                "path": str(path),
                "drainedAt": _now(),
                "dryRun": dry_run,
            }
            try:
                payload = _read_json(path)
                packet = packet_from_payload(payload)
                receipt["packetId"] = packet.packet_id
                result = dispatch_task_packet(
                    BridgeDispatchRequest(packet=packet, repoRoot=str(root), dryRun=dry_run)
                )
                receipt["result"] = result.model_dump(mode="json", by_alias=True)
                if result.retryable and not dry_run:
                    # Admission/replay-store durability is not terminal. Keep
                    # the claimed processing item in place and write no receipt
                    # so the next supervisor tick retries it automatically.
                    receipt["status"] = "retryable"
                    receipt["retryable"] = True
                elif (
                    result.replay_rejected
                    and not dry_run
                    and not result.errors
                    and result.admission_record is not None
                    and result.admission_status == "admitted_replay"
                ):
                    # The packet was already durably admitted by a previous
                    # dispatch, but this processing item still needs its local
                    # receipt/archive commit (for example after receipt fsync
                    # failed). Preserve replay evidence while closing the
                    # admitted inbox work as successfully processed.
                    receipt["status"] = "processed"
                    receipt["recoveredFromReplay"] = True
                elif result.replay_rejected:
                    # A seen row without an exact durable admission (including
                    # legacy id-only rows) is not evidence of successful work.
                    receipt["status"] = "failed" if not dry_run else "replay_rejected"
                    receipt["nonAdmittedReplay"] = True
                elif result.errors:
                    receipt["status"] = "failed"
                else:
                    receipt["status"] = "dry_run" if dry_run else "processed"
            except Exception as exc:
                receipt["status"] = "error"
                receipt["error"] = str(exc)

            if dry_run:
                (errors if receipt["status"] == "error" else processed).append(receipt)
                continue

            if receipt.get("retryable") is True:
                errors.append(receipt)
                continue

            target_dir = _receipt_target_dir(inbox, receipt)
            receipt["archivedPath"] = str(target_dir / path.name)
            try:
                # Receipt durability is the commit point. A later move failure
                # is recovered on the next supervisor tick without redispatch.
                _write_json_atomic(receipt_path, receipt)
            except Exception as exc:
                receipt["persistenceError"] = str(exc)
                errors.append(receipt)
                continue

            try:
                receipt["archivedPath"] = str(_finalize_processing(path, target_dir))
            except Exception as exc:
                receipt["recoveryError"] = str(exc)
                errors.append(receipt)
                continue

            if receipt["status"] == "error":
                errors.append(receipt)
            else:
                processed.append(receipt)

    return {
        "status": "drained",
        "inbox": str(inbox),
        "processedCount": len(processed),
        "errorCount": len(errors) + sum(1 for item in processed if item.get("status") == "failed"),
        "dryRun": dry_run,
        "packets": processed,
        "errors": errors,
    }
