from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from typing import Any, Dict, List, Optional


class ProposalConflict(Exception):
    pass


class ProposalStore:
    """Process-safe proposal store interface; records are immutable revision snapshots."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: Dict[str, List[Dict[str, Any]]] = {}
        self._idempotency: Dict[str, tuple[str, Dict[str, Any]]] = {}

    @staticmethod
    def etag(record: Dict[str, Any]) -> str:
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()
        return '"' + hashlib.sha256(raw).hexdigest() + '"'

    def create(self, record: Dict[str, Any], key: str) -> Dict[str, Any]:
        fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
        scope_key = f"{record['tenant_id']}:{record['owner_user_id']}:{key}"
        with self._lock:
            replay = self._idempotency.get(scope_key)
            if replay:
                if replay[0] != fingerprint:
                    raise ProposalConflict("idempotency key reused with a different payload")
                return copy.deepcopy(replay[1])
            saved = copy.deepcopy(record)
            self._records[saved["proposal_id"]] = [saved]
            self._idempotency[scope_key] = (fingerprint, saved)
            return copy.deepcopy(saved)

    def get(self, proposal_id: str, tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rows = self._records.get(proposal_id, [])
            if not rows or rows[-1]["tenant_id"] != tenant_id or rows[-1]["owner_user_id"] != user_id:
                return None
            return copy.deepcopy(rows[-1])

    def history(self, proposal_id: str, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._records.get(proposal_id, [])
            if not rows or rows[-1]["tenant_id"] != tenant_id or rows[-1]["owner_user_id"] != user_id:
                return []
            return copy.deepcopy(rows)

    def append(self, proposal_id: str, expected_etag: str, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            rows = self._records.get(proposal_id, [])
            if not rows or self.etag(rows[-1]) != expected_etag:
                raise ProposalConflict("proposal ETag is stale")
            rows.append(copy.deepcopy(record))
            return copy.deepcopy(record)

