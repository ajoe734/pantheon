"""File-backed inbox for assistant DevTaskPacket supervisor pickup.

The Web API emits signed packets but does not execute shell.  Repo-local
automation can queue those packets into this inbox; the supervisor drains it
through the existing verifier-backed dispatcher.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

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


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=True)
        fh.write("\n")
        tmp_name = fh.name
    Path(tmp_name).replace(path)


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

    Returns a small receipt.  Duplicate pending/processed/failed packet IDs are
    not overwritten.
    """
    root = _repo_root(repo_root)
    inbox = _inbox_root(str(root), inbox_dir)
    verify_packet(packet, key_store=key_store)
    if has_seen_packet(packet.packet_id, repo_root=str(root)):
        return {
            "status": "replay_rejected",
            "packetId": packet.packet_id,
            "queued": False,
            "inbox": str(inbox),
        }

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
        "processed": inbox / "processed" / f"{safe_id}.json",
        "failed": inbox / "failed" / f"{safe_id}.json",
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


def _move(path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}-{int(datetime.now(timezone.utc).timestamp())}{path.suffix}"
    shutil.move(str(path), str(target))
    return target


def drain_task_packet_inbox(
    *,
    repo_root: Optional[str] = None,
    inbox_dir: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Drain queued packets through dispatch_task_packet()."""
    root = _repo_root(repo_root)
    inbox = _inbox_root(str(root), inbox_dir)
    max_items = max(0, int(limit)) if limit is not None else None
    processed: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []

    files = list(_pending_files(inbox))
    if max_items is not None:
        files = files[:max_items]

    for path in files:
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
            result_payload = result.model_dump(mode="json", by_alias=True)
            receipt["result"] = result_payload
            if result.replay_rejected:
                receipt["status"] = "replay_rejected"
            elif result.errors:
                receipt["status"] = "failed"
            else:
                receipt["status"] = "dry_run" if dry_run else "processed"

            if not dry_run:
                target_dir = inbox / ("failed" if receipt["status"] == "failed" else "processed")
                receipt["archivedPath"] = str(_move(path, target_dir))
                _write_json_atomic(inbox / "receipts" / path.name, receipt)
            processed.append(receipt)
        except Exception as exc:
            receipt["status"] = "error"
            receipt["error"] = str(exc)
            if not dry_run:
                try:
                    receipt["archivedPath"] = str(_move(path, inbox / "failed"))
                finally:
                    _write_json_atomic(inbox / "receipts" / path.name, receipt)
            errors.append(receipt)

    return {
        "status": "drained",
        "inbox": str(inbox),
        "processedCount": len(processed),
        "errorCount": len(errors) + sum(1 for item in processed if item.get("status") == "failed"),
        "dryRun": dry_run,
        "packets": processed,
        "errors": errors,
    }
