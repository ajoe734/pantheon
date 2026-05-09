from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, Optional


class SessionLifecycleStore:
    """Small file-backed store for BFF session lifecycle overrides."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(path):
            self._write_unlocked(self._blank())

    @staticmethod
    def _blank() -> Dict[str, Any]:
        return {"sessions": {}, "idempotency": {}}

    def _read_unlocked(self) -> Dict[str, Any]:
        if not os.path.exists(self._path):
            payload = self._blank()
            self._write_unlocked(payload)
            return payload
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError):
            payload = self._blank()
        if not isinstance(payload, dict):
            payload = self._blank()
        if not isinstance(payload.get("sessions"), dict):
            payload["sessions"] = {}
        if not isinstance(payload.get("idempotency"), dict):
            payload["idempotency"] = {}
        return payload

    def _write_unlocked(self, payload: Dict[str, Any]) -> None:
        payload = copy.deepcopy(payload)
        if not isinstance(payload.get("sessions"), dict):
            payload["sessions"] = {}
        if not isinstance(payload.get("idempotency"), dict):
            payload["idempotency"] = {}
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")

    def get_session(self, session_key: str) -> Dict[str, Any]:
        clean_key = str(session_key or "").strip()
        if not clean_key:
            return {}
        with self._lock:
            payload = self._read_unlocked()
            session = payload["sessions"].get(clean_key)
            return copy.deepcopy(session) if isinstance(session, dict) else {}

    def upsert_session(self, session_key: str, patch: Dict[str, Any], *, now: str) -> Dict[str, Any]:
        clean_key = str(session_key or "").strip()
        if not clean_key:
            raise ValueError("session_key must be non-empty")
        if not isinstance(patch, dict):
            raise ValueError("session patch must be an object")
        with self._lock:
            payload = self._read_unlocked()
            current = payload["sessions"].get(clean_key)
            if not isinstance(current, dict):
                current = {}
            current.setdefault("created_at", now)
            current.update(copy.deepcopy(patch))
            current["updated_at"] = now
            payload["sessions"][clean_key] = current
            self._write_unlocked(payload)
            return copy.deepcopy(current)

    def get_idempotency(self, record_key: str) -> Optional[Dict[str, Any]]:
        clean_key = str(record_key or "").strip()
        if not clean_key:
            return None
        with self._lock:
            payload = self._read_unlocked()
            record = payload["idempotency"].get(clean_key)
            return copy.deepcopy(record) if isinstance(record, dict) else None

    def put_idempotency(
        self,
        record_key: str,
        *,
        request_hash: str,
        result: Dict[str, Any],
        now: str,
    ) -> None:
        clean_key = str(record_key or "").strip()
        if not clean_key:
            return
        with self._lock:
            payload = self._read_unlocked()
            payload["idempotency"][clean_key] = {
                "request_hash": request_hash,
                "result": copy.deepcopy(result),
                "created_at": now,
            }
            self._write_unlocked(payload)

    def clear(self) -> None:
        with self._lock:
            self._write_unlocked(self._blank())
