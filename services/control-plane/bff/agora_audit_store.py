"""Durable Agora audit-event writer used by BFF mutation paths.

The read-surface ports are intentionally read-only.  Agora mutations therefore
use this small, append-only store for their audit records rather than reaching
back into the retired ``ReadSurfaceStore``.  The default path lives under
``BFF_DATA_DIR`` (a persistent deployment volume) and can be overridden for
tests or isolated deployments with ``PANTHEON_BFF_AGORA_AUDIT_STORE_PATH``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
import threading
import uuid
from typing import Any, Dict, List, Optional


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _storage_disabled(path: Any) -> bool:
    return str(path or "").strip().lower() in {"off", "false", "disabled", "none", ":memory:"}


class AgoraAuditStore:
    """Append-only JSONL audit store with read-after-restart semantics."""

    def __init__(self, storage_path: Optional[str] = None) -> None:
        configured = storage_path
        if configured is None:
            configured = os.getenv("PANTHEON_BFF_AGORA_AUDIT_STORE_PATH")
        if configured is None:
            configured = str(Path(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")) / "agora-audit-events.jsonl")
        self._storage_path = str(configured)
        self._lock = threading.Lock()
        self._memory_events: List[Dict[str, Any]] = []

    @property
    def storage_path(self) -> str:
        return self._storage_path

    def _read_events(self) -> List[Dict[str, Any]]:
        if _storage_disabled(self._storage_path):
            return copy.deepcopy(self._memory_events)
        path = Path(self._storage_path)
        if not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(value, dict):
                        events.append(value)
        except OSError:
            return []
        return events

    @staticmethod
    def _canonical_record(event: Dict[str, Any]) -> Dict[str, Any]:
        payload = copy.deepcopy(dict(event))
        event_id = str(
            payload.get("auditId")
            or payload.get("eventId")
            or payload.get("event_id")
            or payload.get("id")
            or f"aud-agora-{uuid.uuid4().hex[:12]}"
        )
        recorded_at = str(
            payload.get("recordedAt")
            or payload.get("recorded_at")
            or payload.get("timestamp")
            or _utc_now_rfc3339()
        )
        action = str(payload.get("action") or payload.get("action_type") or payload.get("event_type") or "agora.audit")
        actor = payload.get("actorId") or payload.get("actor_id") or payload.get("actor")
        target_type = payload.get("targetType") or payload.get("target_type")
        target_id = payload.get("targetId") or payload.get("target_id") or payload.get("entity_id")
        payload.update(
            {
                "auditId": event_id,
                "eventId": event_id,
                "id": event_id,
                "entry_id": event_id,
                "recordedAt": recorded_at,
                "timestamp": recorded_at,
                "action": action,
                "action_type": action,
                "actor": actor,
                "actorId": actor,
                "actor_id": actor,
                "targetType": target_type,
                "target_type": target_type,
                "targetId": target_id,
                "target_id": target_id,
                "source": "agora_audit_store",
            }
        )
        return payload

    def record_agora_audit_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        record = self._canonical_record(event)
        with self._lock:
            if _storage_disabled(self._storage_path):
                self._memory_events.append(copy.deepcopy(record))
            else:
                path = Path(self._storage_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        return copy.deepcopy(record)

    def list_agora_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        events = self._read_events()
        allowed_actions = {str(value).strip() for value in action_types or [] if str(value).strip()}
        filtered: List[Dict[str, Any]] = []
        for event in events:
            if actor and str(event.get("actor") or event.get("actorId") or "") != str(actor):
                continue
            action = str(event.get("action_type") or event.get("action") or "")
            if allowed_actions and action not in allowed_actions:
                continue
            if target_type and str(event.get("target_type") or event.get("targetType") or "") != str(target_type):
                continue
            timestamp = _parse_timestamp(event.get("timestamp") or event.get("recordedAt"))
            if from_ts is not None and (timestamp is None or timestamp < from_ts):
                continue
            if to_ts is not None and (timestamp is None or timestamp > to_ts):
                continue
            filtered.append(copy.deepcopy(event))
        return filtered
