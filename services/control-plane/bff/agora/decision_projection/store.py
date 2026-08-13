"""Durable, tenant-isolated store for owner-scoped decision events."""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional

from .models import DecisionEventRecord


class DecisionEventStore:
    """Store for projected decision events supporting restart recovery and strict tenant isolation."""

    def __init__(self, storage_filepath: Optional[str] = None) -> None:
        self._filepath = storage_filepath
        self._lock = threading.RLock()
        self._events_by_id: Dict[str, DecisionEventRecord] = {}
        # Keyed by (tenant_id, user_id, idempotency_key)
        self._idempotency_index: Dict[tuple[str, str, str], str] = {}

        if self._filepath and os.path.exists(self._filepath):
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._filepath:
            return
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    record = DecisionEventRecord.model_validate(item)
                    self._events_by_id[record.decision_event_id] = record
                    key = (record.tenant_id, record.user_id, record.idempotency_key)
                    self._idempotency_index[key] = record.decision_event_id
        except Exception:
            pass  # Fail safe on empty/corrupt store file

    def _flush_to_disk(self) -> None:
        if not self._filepath:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self._filepath)), exist_ok=True)
        dump_data = [rec.model_dump() for rec in self._events_by_id.values()]
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, indent=2, ensure_ascii=False)

    def save_event(self, event: DecisionEventRecord) -> DecisionEventRecord:
        with self._lock:
            key = (event.tenant_id, event.user_id, event.idempotency_key)
            self._events_by_id[event.decision_event_id] = event
            self._idempotency_index[key] = event.decision_event_id
            self._flush_to_disk()
            return event

    def get_event(
        self, tenant_id: str, user_id: str, decision_event_id: str
    ) -> Optional[DecisionEventRecord]:
        with self._lock:
            rec = self._events_by_id.get(decision_event_id)
            if rec is None:
                return None
            # Strict tenant isolation
            if rec.tenant_id != tenant_id or rec.user_id != user_id:
                return None
            return rec

    def get_by_idempotency_key(
        self, tenant_id: str, user_id: str, idempotency_key: str
    ) -> Optional[DecisionEventRecord]:
        with self._lock:
            key = (tenant_id, user_id, idempotency_key)
            event_id = self._idempotency_index.get(key)
            if event_id is None:
                return None
            return self.get_event(tenant_id, user_id, event_id)

    def list_events(
        self,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[DecisionEventRecord]:
        with self._lock:
            matched = []
            for rec in self._events_by_id.values():
                if rec.tenant_id == tenant_id and rec.user_id == user_id:
                    if strategy_id is None or rec.strategy_id == strategy_id:
                        matched.append(rec)
            # Sort newest first
            matched.sort(key=lambda x: x.created_at, reverse=True)
            return matched[:limit]
