"""Row-backed stand-in for PostgresJsonOwnerStore used by Agora servant tests.

Instances share one process-wide table registry keyed by table name, so building a second
``AgoraWriteService`` over it models a restarted process reading the same Postgres rows.
It understands only the predicates ``AgoraStore`` emits for servant conversations
(``payload ->> 'k' = %s`` joined by ``AND``).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_EQ = re.compile(r"^payload ->> '(\w+)' = %s$")


class FakeOwnerTable:
    tables: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def __init__(self, *, dsn: str, table: str, owner_service: str, bootstrap: bool = True) -> None:
        self.table = table
        self.owner_service = owner_service
        self.tables.setdefault(table, {})

    @classmethod
    def reset(cls) -> None:
        cls.tables = {}

    @property
    def rows(self) -> Dict[str, Dict[str, Any]]:
        return self.tables[self.table]

    @staticmethod
    def _clone(value: Any) -> Any:
        return json.loads(json.dumps(value, sort_keys=True))

    def put(self, record_id: str, payload: Dict[str, Any]) -> None:
        self.rows[record_id] = self._clone(payload)

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        row = self.rows.get(record_id)
        return self._clone(row) if row is not None else None

    def list_all(self, **_kw: Any) -> List[Dict[str, Any]]:
        return [self._clone(r) for r in self.rows.values()]

    def insert_if_absent(self, record_id: str, payload: Dict[str, Any], **_kw: Any) -> Tuple[bool, Dict[str, Any]]:
        if record_id in self.rows:
            return False, self._clone(self.rows[record_id])
        self.rows[record_id] = self._clone(payload)
        return True, self._clone(payload)

    def compare_and_set(
        self, record_id: str, expected_payload: Optional[Dict[str, Any]], payload: Dict[str, Any], **_kw: Any
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        current = self.rows.get(record_id)
        if expected_payload is None:
            if current is not None:
                return False, self._clone(current)
        elif current is None or self._clone(current) != self._clone(expected_payload):
            return False, self._clone(current) if current is not None else None
        self.rows[record_id] = self._clone(payload)
        return True, self._clone(payload)

    def delete_if_matches(self, record_id: str, expected_payload: Dict[str, Any]) -> bool:
        current = self.rows.get(record_id)
        if current is None or self._clone(current) != self._clone(expected_payload):
            return False
        del self.rows[record_id]
        return True

    def select_where(self, where_sql: str, params: tuple = (), *, tail_sql: str = "") -> List[Dict[str, Any]]:
        rest = list(params)
        preds = []
        for clause in [c.strip() for c in where_sql.split(" AND ")]:
            match = _EQ.match(clause)
            if not match:  # pragma: no cover - fail loudly on an unknown predicate
                raise AssertionError(f"fake owner table does not understand: {clause!r}")
            key, value = match.group(1), rest.pop(0)
            preds.append(lambda p, key=key, value=value: p.get(key) == value)
        return [self._clone(p) for p in self.rows.values() if all(f(p) for f in preds)]
