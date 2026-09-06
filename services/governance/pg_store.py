from __future__ import annotations

import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import ApprovalDecision, ApprovalDecisionStore, DecisionOutcome, DecisionState, TargetType  # type: ignore

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

_UTC = datetime.timezone.utc


class ApprovalCommandConflict(ValueError):
    pass


class ApprovalCommandNotFound(ValueError):
    pass


class PostgresApprovalDecisionStore:
    """Postgres owner store for governance approval decisions."""

    def __init__(
        self,
        dsn: str,
        table: str = "governance.approval_decisions",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="governance-svc",
            bootstrap=bootstrap,
        )

        self._receipts = PostgresJsonOwnerStore(
            dsn=dsn, table=table + "_receipts", owner_service="governance-svc", bootstrap=bootstrap,
        )

    def execute_command(self, *, command: dict, mutate, audit_store) -> dict:
        """Commit decision CAS, immutable response receipt and audit in one transaction.

        Receipt reservation uses the same foundation CAS as Registry. No
        independently committed put, and no in-memory result escapes commit.
        """
        if not isinstance(audit_store, PostgresGovernanceAuditStore):
            raise RuntimeError("Approval commands require a PostgreSQL audit owner")
        if audit_store._records.dsn != self._records.dsn:
            raise RuntimeError("Approval audit and decision must share one database")
        import hashlib
        encoded = json.dumps(command, sort_keys=True, separators=(",", ":"))
        request_digest = hashlib.sha256(encoded.encode()).hexdigest()
        scope = [command['tenant_id'], command['actor_id'], command['idempotency_key']]
        receipt_id = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
        with self._records.transaction() as conn:
            reserved, receipt = self._receipts.compare_and_set(
                receipt_id, None, {"request_digest": request_digest}, conn=conn,
            )
            if not reserved:
                if receipt is None or receipt.get('request_digest') != request_digest:
                    raise ApprovalCommandConflict('Idempotency key has different command content')
                return receipt['response']
            # Use the merged transaction-aware read API, without a second connection.
            base = next((row for row in self._records.list_all(conn=conn)
                         if row['decision_id'] == command['decision_id']), None)
            if base is not None and base.get('tenant_id') != command['tenant_id']:
                raise ApprovalCommandNotFound('Approval decision not found')
            if (base.get('version', 0) if base else 0) != command['expected_version']:
                raise ApprovalCommandConflict('Approval base version is stale')
            decision = mutate(ApprovalDecision.from_dict(base) if base else None)
            decision.version = command['expected_version'] + 1
            decision.event_id = str(uuid.uuid4())
            payload = decision.to_dict()
            changed, _ = self._records.compare_and_set(
                decision.decision_id, base, payload, conn=conn,
            )
            if not changed:
                raise ApprovalCommandConflict('Competing approval command changed the base')
            event = {
                'event_id': decision.event_id, 'event_type': 'approval_' + command['operation'],
                'decision_id': decision.decision_id, 'tenant_id': command['tenant_id'],
                'actor_id': command['actor_id'], 'actor_role': command['actor_role'],
                'version': decision.version, 'request_digest': request_digest,
                'timestamp': datetime.datetime.now(_UTC).isoformat(),
            }
            inserted, _ = audit_store._records.compare_and_set(
                decision.event_id, None, event, conn=conn,
            )
            if not inserted:
                raise ApprovalCommandConflict('Audit event identity collision')
            finished, _ = self._receipts.compare_and_set(
                receipt_id, {'request_digest': request_digest},
                {'request_digest': request_digest, 'command': command, 'response': payload,
                 'event': event}, conn=conn,
            )
            if not finished:
                raise ApprovalCommandConflict('Receipt reservation changed')
        return payload

    def put(self, decision: ApprovalDecision) -> None:
        self._records.put(decision.decision_id, decision.to_dict())

    def get(self, decision_id: str) -> ApprovalDecision | None:
        payload = self._records.get(decision_id)
        return ApprovalDecision.from_dict(payload) if payload else None

    def list_all(self) -> list[ApprovalDecision]:
        return [ApprovalDecision.from_dict(record) for record in self._records.list_all()]

    def find_by_target(self, target_type: str, target_id: str) -> list[ApprovalDecision]:
        return [
            decision
            for decision in self.list_all()
            if decision.target_id == target_id
            and (
                decision.target_type == target_type
                or (
                    isinstance(decision.target_type, TargetType)
                    and decision.target_type.value == target_type
                )
            )
        ]

    def find_latest_approved(self, target_type: str, target_id: str) -> ApprovalDecision | None:
        candidates = [
            decision
            for decision in self.find_by_target(target_type, target_id)
            if decision.decision_state == DecisionState.DECIDED
            and decision.decision
            in (DecisionOutcome.APPROVED, DecisionOutcome.APPROVED_WITH_CONDITIONS)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda decision: decision.decided_at or "", reverse=True)
        return candidates[0]


class JsonlGovernanceAuditStore:
    def __init__(self, audit_log_path: str) -> None:
        self.audit_log_path = Path(audit_log_path)

    def append_event(
        self,
        *,
        event_type: str,
        decision_id: str,
        actor_id: str | None,
        actor_role: str | None,
        target_type: str | None,
        target_id: str | None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        try:
            from .audit_log import append_audit_event
        except ImportError:
            from audit_log import append_audit_event  # type: ignore

        return append_audit_event(
            event_type=event_type,
            decision_id=decision_id,
            actor_id=actor_id,
            actor_role=actor_role,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            audit_log_path=str(self.audit_log_path),
        )

    def list_events(self, *, decision_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            if not self.audit_log_path.exists():
                return events
            with self.audit_log_path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if decision_id and event.get("decision_id") != decision_id:
                        continue
                    events.append(event)
        except OSError:
            return []
        events.reverse()
        return events[:limit]


class PostgresGovernanceAuditStore:
    """Postgres owner store for governance audit events."""

    def __init__(
        self,
        dsn: str,
        table: str = "governance.audit_events",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="governance-svc",
            bootstrap=bootstrap,
        )

    def append_event(
        self,
        *,
        event_type: str,
        decision_id: str,
        actor_id: str | None,
        actor_role: str | None,
        target_type: str | None,
        target_id: str | None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "decision_id": decision_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "target_type": target_type,
            "target_id": target_id,
            "detail": detail or {},
            "timestamp": datetime.datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }
        self._records.put(event_id, event)
        return event_id

    def list_events(self, *, decision_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        events = self._records.list_all()
        if decision_id:
            events = [event for event in events if event.get("decision_id") == decision_id]
        events.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
        return events[:limit]


def build_approval_decision_store(storage_path: str) -> ApprovalDecisionStore | PostgresApprovalDecisionStore:
    backend = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower()
    if backend in ("", "json"):
        return ApprovalDecisionStore(storage_path=storage_path)
    if backend != "postgres":
        raise ValueError("GOVERNANCE_STORE_BACKEND must be json or postgres")
    dsn = os.getenv("GOVERNANCE_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("GOVERNANCE_STORE_DSN or DATABASE_URL is required for Postgres governance store")
    table = os.getenv("GOVERNANCE_STORE_TABLE", "governance.approval_decisions")
    bootstrap = os.getenv("GOVERNANCE_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return PostgresApprovalDecisionStore(dsn=dsn, table=table, bootstrap=bootstrap)


def build_governance_audit_store(
    audit_log_path: str,
) -> JsonlGovernanceAuditStore | PostgresGovernanceAuditStore:
    backend = os.getenv("GOVERNANCE_AUDIT_BACKEND") or os.getenv("GOVERNANCE_STORE_BACKEND", "json")
    backend = backend.strip().lower()
    if backend in ("", "json", "jsonl"):
        return JsonlGovernanceAuditStore(audit_log_path)
    if backend != "postgres":
        raise ValueError("GOVERNANCE_AUDIT_BACKEND must be jsonl or postgres")
    dsn = os.getenv("GOVERNANCE_AUDIT_DSN") or os.getenv("GOVERNANCE_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("GOVERNANCE_AUDIT_DSN, GOVERNANCE_STORE_DSN, or DATABASE_URL is required")
    table = os.getenv("GOVERNANCE_AUDIT_TABLE", "governance.audit_events")
    bootstrap = os.getenv("GOVERNANCE_AUDIT_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return PostgresGovernanceAuditStore(dsn=dsn, table=table, bootstrap=bootstrap)
