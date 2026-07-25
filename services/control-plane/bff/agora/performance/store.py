"""Transactional durable store for governed performance suggestions.

SQLite is intentionally used for the BFF-owned disposition ledger: the file
lives under ``BFF_DATA_DIR`` by default, survives process restarts, and gives
idempotency reservation, suggestion CAS, receipt, and audit append one atomic
transaction.  It stores no broker command or execution authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from .models import AdjustmentSuggestion, SuggestionActionReceipt


STORE_PATH_ENV = "PANTHEON_BFF_AGORA_PERFORMANCE_STORE_PATH"


class PerformanceSuggestionError(RuntimeError):
    """Base error for governed suggestion state."""


class PerformanceSuggestionNotFound(PerformanceSuggestionError):
    """The suggestion or receipt is absent from the caller's exact scope."""


class PerformanceSuggestionConflict(PerformanceSuggestionError):
    """CAS, terminal-state, or idempotency conflict."""


class PerformanceSuggestionStore:
    """Durable suggestion source/disposition and receipt ledger."""

    def __init__(self, path: Optional[str] = None) -> None:
        resolved = path or os.getenv(STORE_PATH_ENV)
        if not resolved:
            resolved = str(
                Path(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff"))
                / "agora_performance.sqlite3"
            )
        self.path = str(Path(resolved).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS performance_suggestions (
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    suggestion_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, owner_user_id, suggestion_id)
                );
                CREATE INDEX IF NOT EXISTS performance_suggestions_scope_idx
                    ON performance_suggestions
                    (tenant_id, owner_user_id, strategy_id, period);

                CREATE TABLE IF NOT EXISTS performance_action_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    suggestion_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE (tenant_id, owner_user_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS performance_audit_events (
                    audit_event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    suggestion_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def request_hash(
        *,
        strategy_id: str,
        suggestion_id: str,
        action: str,
        expected_version: int,
        reason: Optional[str],
    ) -> str:
        payload = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
            "strategy_id": strategy_id,
            "suggestion_id": suggestion_id,
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def upsert_suggestion(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        suggestion: AdjustmentSuggestion,
    ) -> Dict[str, Any]:
        """Persist a source-owned suggestion; no public BFF route calls this."""
        record = suggestion.model_dump(mode="json")
        updated_at = suggestion.updated_at or suggestion.as_of
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO performance_suggestions
                    (tenant_id,owner_user_id,suggestion_id,strategy_id,period,
                     status,version,record_json,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT (tenant_id,owner_user_id,suggestion_id) DO UPDATE SET
                    strategy_id=excluded.strategy_id,
                    period=excluded.period,
                    status=excluded.status,
                    version=excluded.version,
                    record_json=excluded.record_json,
                    updated_at=excluded.updated_at
                """,
                (
                    tenant_id,
                    owner_user_id,
                    suggestion.suggestion_id,
                    suggestion.strategy_id,
                    suggestion.period,
                    suggestion.status,
                    suggestion.version,
                    json.dumps(record, sort_keys=True),
                    updated_at,
                ),
            )
            conn.commit()
        return record

    def list_suggestions(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        strategy_id: str,
        period: str,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_json FROM performance_suggestions
                WHERE tenant_id=? AND owner_user_id=? AND strategy_id=? AND period=?
                ORDER BY updated_at DESC, suggestion_id ASC
                """,
                (tenant_id, owner_user_id, strategy_id, period),
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def get_receipt(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        receipt_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT receipt_json FROM performance_action_receipts
                WHERE tenant_id=? AND owner_user_id=? AND receipt_id=?
                """,
                (tenant_id, owner_user_id, receipt_id),
            ).fetchone()
        return json.loads(row["receipt_json"]) if row else None

    def list_audit_events(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        suggestion_id: str,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_json FROM performance_audit_events
                WHERE tenant_id=? AND owner_user_id=? AND suggestion_id=?
                ORDER BY recorded_at, audit_event_id
                """,
                (tenant_id, owner_user_id, suggestion_id),
            ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

    def act(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        strategy_id: str,
        suggestion_id: str,
        action: str,
        expected_version: int,
        reason: Optional[str],
        actor_id: str,
        idempotency_key: str,
        recorded_at: str,
    ) -> tuple[Dict[str, Any], bool]:
        request_hash = self.request_hash(
            strategy_id=strategy_id,
            suggestion_id=suggestion_id,
            action=action,
            expected_version=expected_version,
            reason=reason,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = conn.execute(
                """
                SELECT request_hash,receipt_json FROM performance_action_receipts
                WHERE tenant_id=? AND owner_user_id=? AND idempotency_key=?
                """,
                (tenant_id, owner_user_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if replay["request_hash"] != request_hash:
                    conn.rollback()
                    raise PerformanceSuggestionConflict(
                        "idempotency key reused with a different suggestion action"
                    )
                receipt = json.loads(replay["receipt_json"])
                receipt["idempotent_replay"] = True
                conn.rollback()
                return receipt, True

            row = conn.execute(
                """
                SELECT record_json,status,version FROM performance_suggestions
                WHERE tenant_id=? AND owner_user_id=? AND suggestion_id=?
                  AND strategy_id=?
                """,
                (tenant_id, owner_user_id, suggestion_id, strategy_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise PerformanceSuggestionNotFound("suggestion not found")
            previous_status = str(row["status"])
            previous_version = int(row["version"])
            if previous_version != expected_version:
                conn.rollback()
                raise PerformanceSuggestionConflict(
                    "suggestion version changed; refetch before retrying"
                )
            if previous_status != "proposed":
                conn.rollback()
                raise PerformanceSuggestionConflict(
                    f"suggestion is already terminal in status {previous_status}"
                )

            next_status = {
                "apply": "applied",
                "reject": "rejected",
                "return_to_workshop": "returned_to_workshop",
            }.get(action)
            if next_status is None:
                conn.rollback()
                raise PerformanceSuggestionConflict("unsupported suggestion action")

            suggestion = json.loads(row["record_json"])
            suggestion.update(
                {
                    "status": next_status,
                    "version": previous_version + 1,
                    "updated_at": recorded_at,
                }
            )
            validated_suggestion = AdjustmentSuggestion.model_validate(suggestion)
            suggestion = validated_suggestion.model_dump(mode="json")
            receipt_id = f"agperf-receipt-{uuid.uuid4().hex}"
            audit_event_id = f"agperf-audit-{uuid.uuid4().hex}"
            receipt = SuggestionActionReceipt(
                receipt_id=receipt_id,
                audit_event_id=audit_event_id,
                suggestion_id=suggestion_id,
                strategy_id=strategy_id,
                action=action,
                previous_status=previous_status,
                status=next_status,
                previous_version=previous_version,
                version=previous_version + 1,
                actor_id=actor_id,
                reason=reason,
                recorded_at=recorded_at,
                authoritative_readback=validated_suggestion,
            ).model_dump(mode="json")
            audit = {
                "audit_event_id": audit_event_id,
                "event_type": "agora.performance.suggestion.disposition",
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "strategy_id": strategy_id,
                "suggestion_id": suggestion_id,
                "receipt_id": receipt_id,
                "actor_id": actor_id,
                "action": action,
                "previous_status": previous_status,
                "status": next_status,
                "previous_version": previous_version,
                "version": previous_version + 1,
                "reason": reason,
                "recorded_at": recorded_at,
                "execution_authority": "none",
                "no_order_route_proof": "agora_suggestion_state_only",
            }
            conn.execute(
                """
                UPDATE performance_suggestions
                SET status=?,version=?,record_json=?,updated_at=?
                WHERE tenant_id=? AND owner_user_id=? AND suggestion_id=?
                  AND strategy_id=? AND version=?
                """,
                (
                    next_status,
                    previous_version + 1,
                    json.dumps(suggestion, sort_keys=True),
                    recorded_at,
                    tenant_id,
                    owner_user_id,
                    suggestion_id,
                    strategy_id,
                    previous_version,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                conn.rollback()
                raise PerformanceSuggestionConflict(
                    "suggestion version changed during disposition"
                )
            conn.execute(
                """
                INSERT INTO performance_action_receipts
                    (receipt_id,tenant_id,owner_user_id,suggestion_id,strategy_id,
                     idempotency_key,request_hash,receipt_json,recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    receipt_id,
                    tenant_id,
                    owner_user_id,
                    suggestion_id,
                    strategy_id,
                    idempotency_key,
                    request_hash,
                    json.dumps(receipt, sort_keys=True),
                    recorded_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO performance_audit_events
                    (audit_event_id,tenant_id,owner_user_id,suggestion_id,
                     receipt_id,event_json,recorded_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    audit_event_id,
                    tenant_id,
                    owner_user_id,
                    suggestion_id,
                    receipt_id,
                    json.dumps(audit, sort_keys=True),
                    recorded_at,
                ),
            )
            conn.commit()
            return receipt, False
