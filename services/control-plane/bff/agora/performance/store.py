"""Transactional durable store for governed performance suggestions.

SQLite is intentionally used for the BFF-owned disposition ledger: the file
lives under ``BFF_DATA_DIR`` by default, survives process restarts, and gives
idempotency reservation, suggestion CAS, receipt, and audit append one atomic
transaction.  It stores no broker command or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .models import AdjustmentSuggestion, SuggestionActionReceipt

logger = logging.getLogger(__name__)


STORE_PATH_ENV = "PANTHEON_BFF_AGORA_PERFORMANCE_STORE_PATH"


class PerformanceSuggestionError(RuntimeError):
    """Base error for governed suggestion state."""


class PerformanceSuggestionNotFound(PerformanceSuggestionError):
    """The suggestion or receipt is absent from the caller's exact scope."""


class PerformanceSuggestionConflict(PerformanceSuggestionError):
    """CAS, terminal-state, or idempotency conflict."""


class PerformanceSuggestionStore:
    """Durable suggestion source/disposition and receipt ledger."""

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        incidents_api_url: Optional[str] = None,
    ) -> None:
        self.incidents_api_url = (
            incidents_api_url
            or os.getenv("PANTHEON_INCIDENTS_API_URL")
            or os.getenv("PANTHEON_INCIDENTS_URL")
        )
        if self.incidents_api_url:
            self.incidents_api_url = self.incidents_api_url.strip().rstrip("/")
        resolved = path or os.getenv(STORE_PATH_ENV)
        if not resolved:
            resolved = str(
                Path(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff"))
                / "agora_performance.sqlite3"
            )
        self.path = str(Path(resolved).expanduser().resolve())
        self._bootstrapped = False
        if not self.incidents_api_url:
            self._bootstrap()

    def _raw_connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _connect(self) -> sqlite3.Connection:
        if not self._bootstrapped:
            self._bootstrap()
        return self._raw_connect()

    def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        with self._raw_connect() as conn:
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

                CREATE TABLE IF NOT EXISTS performance_published_events (
                    topic TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (topic, entity_id)
                );
                """
            )

    def is_event_published(self, topic: str, entity_id: str) -> bool:
        """Check if an event for the given topic and entity_id was already published."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM performance_published_events WHERE topic = ? AND entity_id = ?",
                (topic, entity_id),
            ).fetchone()
            return row is not None

    def mark_event_published(
        self,
        topic: str,
        entity_id: str,
        payload: Dict[str, Any],
        published_at: Optional[str] = None,
    ) -> None:
        """Durably record that an event was published to prevent duplicate publications."""
        now_str = published_at or datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO performance_published_events (topic, entity_id, published_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(topic, entity_id) DO NOTHING
                """,
                (topic, entity_id, now_str, payload_json),
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
        if suggestion.correlation_id:
            record["correlation_id"] = suggestion.correlation_id
        if suggestion.provenance and suggestion.provenance.correlation_id:
            record.setdefault("provenance", {})["correlation_id"] = suggestion.provenance.correlation_id
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

    def get_suggestion(
        self,
        tenant_id: str,
        strategy_id: Optional[str] = None,
        suggestion_id: Optional[str] = None,
        *,
        owner_user_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        t_id = tenant_id or kwargs.get("tenant_id", "")
        s_id = strategy_id or kwargs.get("strategy_id")
        sugg_id = suggestion_id or kwargs.get("suggestion_id")
        u_id = owner_user_id or kwargs.get("owner_user_id")

        if self.incidents_api_url and sugg_id:
            query_params: Dict[str, str] = {}
            if t_id:
                query_params["tenant_id"] = t_id
            if s_id:
                query_params["strategy_id"] = s_id
            if u_id:
                query_params["owner_user_id"] = u_id
            qs = urllib.parse.urlencode(query_params)
            url = f"{self.incidents_api_url}/api/incidents/agora/performance/suggestions/{sugg_id}"
            if qs:
                url = f"{url}?{qs}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        return data.get("suggestion", data)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                logger.warning("Failed querying incidents service for suggestion %s: %s", sugg_id, exc)
            except Exception as exc:
                logger.warning("Failed querying incidents service for suggestion %s: %s", sugg_id, exc)

        clauses = ["tenant_id = ?"]
        params: List[Any] = [t_id]
        if u_id:
            clauses.append("owner_user_id = ?")
            params.append(u_id)
        if s_id:
            clauses.append("strategy_id = ?")
            params.append(s_id)
        if sugg_id:
            clauses.append("suggestion_id = ?")
            params.append(sugg_id)

        query = f"SELECT record_json FROM performance_suggestions WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return json.loads(row["record_json"]) if row else None

    def list_suggestions(
        self,
        tenant_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        *,
        owner_user_id: Optional[str] = None,
        period: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        t_id = tenant_id or kwargs.get("tenant_id", "")
        s_id = strategy_id or kwargs.get("strategy_id")
        u_id = owner_user_id or kwargs.get("owner_user_id")
        p = period or kwargs.get("period")

        if self.incidents_api_url:
            query_params = {}
            if t_id:
                query_params["tenant_id"] = t_id
            if s_id:
                query_params["strategy_id"] = s_id
            if u_id:
                query_params["owner_user_id"] = u_id
            if p:
                query_params["period"] = p
            qs = urllib.parse.urlencode(query_params)
            url = f"{self.incidents_api_url}/api/incidents/agora/performance/suggestions"
            if qs:
                url = f"{url}?{qs}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict) and "suggestions" in data:
                        return data["suggestions"]
                    elif isinstance(data, list):
                        return data
            except Exception as exc:
                logger.warning("Failed querying incidents service for performance suggestions: %s", exc)

        clauses = ["tenant_id = ?"]
        params: List[Any] = [t_id]
        if u_id:
            clauses.append("owner_user_id = ?")
            params.append(u_id)
        if s_id:
            clauses.append("strategy_id = ?")
            params.append(s_id)
        if p:
            clauses.append("period = ?")
            params.append(p)

        query = f"""
            SELECT record_json FROM performance_suggestions
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, suggestion_id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def get_receipt(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        receipt_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self.incidents_api_url:
            query_params = {}
            if tenant_id:
                query_params["tenant_id"] = tenant_id
            if owner_user_id:
                query_params["owner_user_id"] = owner_user_id
            qs = urllib.parse.urlencode(query_params)
            url = f"{self.incidents_api_url}/api/incidents/agora/performance/action-receipts/{receipt_id}"
            if qs:
                url = f"{url}?{qs}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        return data.get("receipt", data)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                logger.warning("Failed querying receipt %s from incidents service: %s", receipt_id, exc)
            except Exception as exc:
                logger.warning("Failed querying receipt %s from incidents service: %s", receipt_id, exc)

        clauses = ["receipt_id=?"]
        params: List[Any] = [receipt_id]
        if tenant_id:
            clauses.append("tenant_id=?")
            params.append(tenant_id)
        if owner_user_id:
            clauses.append("owner_user_id=?")
            params.append(owner_user_id)
        query = f"SELECT receipt_json FROM performance_action_receipts WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return json.loads(row["receipt_json"]) if row else None

    def list_audit_events(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        suggestion_id: str,
    ) -> List[Dict[str, Any]]:
        if self.incidents_api_url:
            query_params = {
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
            }
            qs = urllib.parse.urlencode(query_params)
            url = f"{self.incidents_api_url}/api/incidents/agora/performance/suggestions/{suggestion_id}/audit-events?{qs}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict) and "audit_events" in data:
                        return data["audit_events"]
                    elif isinstance(data, list):
                        return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return []
                logger.warning("Failed querying audit events from incidents service: %s", exc)
            except Exception as exc:
                logger.warning("Failed querying audit events from incidents service: %s", exc)

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
        if self.incidents_api_url:
            url = f"{self.incidents_api_url}/api/incidents/agora/performance/suggestions/{suggestion_id}/actions"
            body = {
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "strategy_id": strategy_id,
                "action": action,
                "expected_version": expected_version,
                "reason": reason,
                "actor_id": actor_id,
                "recorded_at": recorded_at,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    receipt = data.get("receipt", data)
                    idempotent_replay = bool(data.get("idempotent_replay", False))
                    return receipt, idempotent_replay
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8")
                err_msg = ""
                try:
                    err_json = json.loads(err_body)
                    err_msg = err_json.get("detail", err_msg)
                except Exception:
                    err_msg = err_body
                if exc.code == 404:
                    raise PerformanceSuggestionNotFound(err_msg or "suggestion not found")
                elif exc.code == 409:
                    raise PerformanceSuggestionConflict(err_msg or "suggestion conflict")
                raise

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
            if validated_suggestion.correlation_id:
                suggestion["correlation_id"] = validated_suggestion.correlation_id
            if validated_suggestion.provenance and validated_suggestion.provenance.correlation_id:
                suggestion.setdefault("provenance", {})["correlation_id"] = validated_suggestion.provenance.correlation_id
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
