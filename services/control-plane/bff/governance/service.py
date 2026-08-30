"""Governance domain application service.

The BFF composition root supplies the already-established typed read ports and
command adapters.  This module owns governance-specific validation and
projection logic; it deliberately does not create a second persistence owner.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PageSlice = Callable[[Sequence[Any], Optional[str], int], Tuple[List[Any], Optional[str]]]
SubmitAction = Callable[..., Any]


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def page_slice(
    items: Sequence[Any], page_token: Optional[str], page_size: int
) -> Tuple[List[Any], Optional[str]]:
    try:
        start = max(0, int(page_token or 0))
    except (TypeError, ValueError):
        start = 0
    end = start + page_size
    return list(items[start:end]), str(end) if end < len(items) else None


def split_csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    values = [part.strip() for part in value.split(",") if part.strip()]
    return values or None


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def record_id(record: Mapping[str, Any], *fields: str) -> str:
    for field in fields:
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


def record_time(record: Mapping[str, Any]) -> str:
    for field in (
        "occurred_at",
        "submitted_at",
        "created_at",
        "decided_at",
        "updated_at",
        "timestamp",
    ):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


def count_by(records: Iterable[Mapping[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(record.get(field) or "unknown").strip().lower() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _identity_operator_id(identity: Any) -> str:
    return str(getattr(identity, "operator_id", None) or getattr(identity, "id", None) or "operator")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class GovernanceService:
    """Application service over existing governance and consultation ports."""

    _CONSULT_TARGET_TYPES = {"persona", "committee", "red_team"}
    _CONSULT_PRIORITIES = {"low", "normal", "high", "urgent"}
    _CONSULTATION_TYPES = {
        "strategy_review",
        "redteam",
        "red_team",
        "data_leakage",
        "execution_risk",
        "capital_pool",
        "incident",
        "persona_policy",
    }
    _PENDING_APPROVAL_STATES = {
        "pending",
        "in_review",
        "proposed",
        "under_review",
        "reviewed",
    }
    _DECISIONS = {
        "approve",
        "reject",
        "request_revision",
        "request_changes",
        "escalate",
        "freeze",
    }

    def __init__(
        self,
        read_store: Any,
        *,
        utc_now: Callable[[], str] = utc_now_rfc3339,
        page_slice_fn: PageSlice = page_slice,
        submit_action: Optional[SubmitAction] = None,
        publish_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        get_interventions: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self.read_store = read_store
        self.utc_now = utc_now
        self.page_slice = page_slice_fn
        self.submit_action = submit_action
        self.publish_event = publish_event
        self.get_interventions = get_interventions or (lambda: [])
        self._created_approvals: Dict[str, Dict[str, Any]] = {}
        self._idempotency: Dict[str, Dict[str, Any]] = {}

    def _call(self, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
        method = getattr(self.read_store, name, None)
        if not callable(method):
            return copy.deepcopy(default)
        return method(*args, **kwargs)

    def dataset_source(self, dataset: str) -> str:
        source = self._call("dataset_source", dataset, default="ok")
        return str(source or "ok")

    # Approval decisions -------------------------------------------------

    def list_approval_decisions(
        self, *, outcome: Optional[str] = None, state: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            records = self._call(
                "list_approval_decisions",
                outcome=outcome,
                state=state,
                include_fixture_pack=False,
                default=[],
            )
        except TypeError:
            records = self._call("list_approval_decisions", default=[])
        items = [copy.deepcopy(item) for item in (records or [])]
        items.extend(copy.deepcopy(list(self._created_approvals.values())))
        if outcome:
            requested = {part.lower() for part in split_csv(outcome) or []}
            items = [
                item
                for item in items
                if str(item.get("outcome") or item.get("decision") or "").lower() in requested
            ]
        if state:
            requested = {part.lower() for part in split_csv(state) or []}
            items = [
                item
                for item in items
                if str(item.get("decision_state") or item.get("state") or item.get("status") or "").lower()
                in requested
            ]
        seen: set[str] = set()
        result: List[Dict[str, Any]] = []
        for item in items:
            item_id = record_id(item, "decision_id", "id", "item_id")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            result.append(item)
        return result

    def get_approval_detail(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Typed replacement for the former generic ``/bff/approvals/{id}`` alias."""
        clean_id = str(approval_id or "").strip()
        if not clean_id:
            return None
        if clean_id in self._created_approvals:
            return copy.deepcopy(self._created_approvals[clean_id])
        decision = self._call("get_approval_decision", clean_id, default=None)
        if decision is None:
            decision = self._call("get_approval_decision_by_id", clean_id, default=None)
        if decision is not None:
            return copy.deepcopy(decision)
        return next(
            (
                copy.deepcopy(item)
                for item in self.list_approval_decisions()
                if record_id(item, "decision_id", "id", "item_id") == clean_id
            ),
            None,
        )

    def create_approval_decision(
        self,
        payload: Mapping[str, Any],
        *,
        identity: Any,
        idempotency_key: str,
        dry_run: bool,
        correlation_id: str,
    ) -> Dict[str, Any]:
        plan_id = str(payload.get("plan_id") or "").strip()
        decision = str(payload.get("decision") or "").strip().lower()
        memo = str(payload.get("memo") or "").strip()
        if not plan_id:
            raise ValueError("plan_id")
        if decision not in {"approve", "reject"}:
            raise ValueError("decision")
        if len(memo) < 8:
            raise ValueError("memo")

        request_hash = stable_json_hash(
            {"plan_id": plan_id, "decision": decision, "memo": memo}
        )
        existing = self._idempotency.get(idempotency_key)
        if existing:
            if existing["request_hash"] != request_hash:
                raise RuntimeError("idempotency_conflict")
            return copy.deepcopy(existing["result"])

        decided_at = self.utc_now()
        decision_id = str(payload.get("decision_id") or payload.get("id") or uuid.uuid4())
        record = {
            "id": decision_id,
            "decision_id": decision_id,
            "plan_id": plan_id,
            "decision": decision,
            "outcome": "approved" if decision == "approve" else "rejected",
            "decision_state": "decided",
            "memo": memo,
            "approver_id": _identity_operator_id(identity),
            "decided_at": decided_at,
        }
        data = {
            "status": "accepted",
            "commandId": decision_id,
            "command_id": decision_id,
            "plan_id": plan_id,
            "decision": decision,
            "approver_id": record["approver_id"],
            "approverId": record["approver_id"],
            "decided_at": decided_at,
            "decidedAt": decided_at,
        }
        result = {
            "data": data,
            "meta": {
                "snapshot_at": decided_at,
                "dryRun": dry_run,
                "correlationId": correlation_id,
                "evidenceKind": "approval.decide",
            },
        }
        if not dry_run:
            self._created_approvals[decision_id] = record
            self._idempotency[idempotency_key] = {
                "request_hash": request_hash,
                "result": copy.deepcopy(result),
            }
        return result

    # Consultation requests, committees, and memos ---------------------

    @staticmethod
    def required_text(payload: Mapping[str, Any], field: str) -> str:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise ValueError(field)
        return value

    def validate_consult_request(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        target_type = self.required_text(payload, "target_type").lower()
        if target_type not in self._CONSULT_TARGET_TYPES:
            raise ValueError("target_type")
        priority = str(payload.get("priority") or "normal").strip().lower()
        if priority not in self._CONSULT_PRIORITIES:
            raise ValueError("priority")
        consultation_type = str(payload.get("consultation_type") or "strategy_review").strip().lower()
        if consultation_type not in self._CONSULTATION_TYPES:
            raise ValueError("consultation_type")
        context_refs = payload.get("context_refs") or []
        if not isinstance(context_refs, list) or any(not isinstance(ref, dict) for ref in context_refs):
            raise ValueError("context_refs")
        return {
            "from_persona_id": self.required_text(payload, "from_persona_id"),
            "target_type": target_type,
            "target_ref": self.required_text(payload, "target_ref"),
            "task": self.required_text(payload, "task"),
            "context_refs": copy.deepcopy(context_refs),
            "priority": priority,
            "consultation_type": consultation_type,
        }
    def create_consult_request(self, payload: Mapping[str, Any], identity: Any) -> Dict[str, Any]:
        fields = self.validate_consult_request(payload)
        created = self._call(
            "create_consult_request",
            **fields,
            actor_id=_identity_operator_id(identity),
            created_at=self.utc_now(),
            default=None,
        )
        if created is None:
            created_at = self.utc_now()
            request_id = str(payload.get("request_id") or uuid.uuid4())
            created = {
                **fields,
                "request_id": request_id,
                "status": "created",
                "created_at": created_at,
                "linked_session_id": None,
                "request_to_session_status": "pending",
                "allowedActions": {"canCancel": True},
            }
        return copy.deepcopy(created)

    def list_consult_requests(
        self,
        *,
        status: Optional[str],
        target_type: Optional[str],
        consultation_type: Optional[str],
    ) -> List[Dict[str, Any]]:
        statuses = split_csv(status)
        return list(
            self._call(
                "list_consult_requests",
                statuses=statuses,
                target_type=target_type or None,
                consultation_type=consultation_type or None,
                default=[],
            )
            or []
        )

    def get_consult_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_consult_request", request_id, default=None)

    def cancel_consult_request(self, request_id: str, identity: Any) -> Optional[Dict[str, Any]]:
        return self._call(
            "cancel_consult_request",
            request_id,
            actor_id=_identity_operator_id(identity),
            canceled_at=self.utc_now(),
            default=None,
        )

    def list_committees(
        self, *, status: Optional[str], page_token: Optional[str], page_size: int
    ) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
        statuses = split_csv(status)
        try:
            records = self._call("list_committees", statuses=statuses, default=[])
        except TypeError:
            records = self._call("list_committees", default=[])
        items = list(records or [])
        if statuses:
            allowed = {value.lower() for value in statuses}
            items = [item for item in items if str(item.get("status") or item.get("consensus_state") or "").lower() in allowed]
        page, token = self.page_slice(items, page_token, page_size)
        return page, token, len(items)

    def get_committee(self, committee_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_committee", committee_id, default=None)

    def list_consult_memos(
        self, *, status: Optional[str], page_token: Optional[str], page_size: int
    ) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
        statuses = split_csv(status)
        records = list(self._call("list_consult_memos", statuses=statuses, default=[]) or [])
        page, token = self.page_slice(records, page_token, page_size)
        return page, token, len(records)

    def get_consult_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_consult_memo", memo_id, default=None)

    def consultation_workbench(self) -> Dict[str, Any]:
        requests = self.list_consult_requests(status=None, target_type=None, consultation_type=None)
        committees, _, committee_count = self.list_committees(status=None, page_token=None, page_size=200)
        memos, _, memo_count = self.list_consult_memos(status=None, page_token=None, page_size=200)
        snapshot_at = self.utc_now()
        return {
            "data": {
                "id": "consultation-workbench",
                "requests": requests,
                "committees": committees,
                "memos": memos,
                "summary": {
                    "request_count": len(requests),
                    "committee_count": committee_count,
                    "memo_count": memo_count,
                },
            },
            "meta": {"snapshot_at": snapshot_at},
        }

    # Review queues and audit ------------------------------------------

    def list_review_queue(
        self,
        *,
        item_types: Optional[List[str]] = None,
        risk_levels: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return list(
            self._call(
                "list_governance_review_queue_items",
                item_types=item_types,
                risk_levels=risk_levels,
                statuses=statuses,
                default=[],
            )
            or []
        )

    def list_approval_queue(self, **filters: Any) -> List[Dict[str, Any]]:
        try:
            records = self._call("list_approval_queue_items", default=[], **filters)
        except TypeError:
            records = self._call("list_approval_queue_items", default=[])
        return list(records or [])

    def list_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Any = None,
        to_ts: Any = None,
    ) -> List[Dict[str, Any]]:
        try:
            records = self._call(
                "list_governance_audit_events",
                actor=actor,
                action_types=action_types,
                target_type=target_type,
                from_ts=from_ts,
                to_ts=to_ts,
                include_fixture_pack=False,
                default=[],
            )
        except TypeError:
            records = self._call("list_governance_audit_events", default=[])
        return list(records or [])

    def mutation_review(self, decision_id: str) -> Optional[Dict[str, Any]]:
        decision = self._call("get_evolution_decision_by_id", decision_id, default=None)
        if decision is None:
            decision = self._call("get_evolution_decision", decision_id, default=None)
        if decision is None:
            return None
        approval = self.get_approval_detail(str(decision.get("approval_decision_id") or ""))
        linked_incident_id = str(decision.get("linked_incident_id") or decision.get("incident_ref") or "")
        linked_postmortem_id = str(decision.get("linked_postmortem_id") or "")
        incident = self._call("get_incident", linked_incident_id, default=None) if linked_incident_id else None
        postmortem = self._call("get_postmortem", linked_postmortem_id, default=None) if linked_postmortem_id else None
        payload = copy.deepcopy(decision)
        payload.update(
            {
                "decision_id": record_id(decision, "decision_id", "id"),
                "target_type": decision.get("target_type") or "artifact",
                "target_id": decision.get("target_id") or decision.get("artifact_id"),
                "target_version": decision.get("target_version") or decision.get("artifact_version") or "unknown",
                "action_type": decision.get("action_type") or "mutation",
                "decision_state": decision.get("decision_state") or decision.get("status") or "pending",
                "risk_level": decision.get("risk_level") or "unknown",
                "created_at": decision.get("created_at") or self.utc_now(),
                "approval_decision": approval,
                "linked_incident": incident,
                "linked_postmortem": postmortem,
                "meta": {
                    "snapshot_at": self.utc_now(),
                    "surfaces": {"mutation_review": "fresh"},
                },
            }
        )
        return payload

    # Consultation read surfaces --------------------------------------

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_persona", persona_id, default=None)

    def list_consultations_for_persona(self, persona_id: str, **filters: Any) -> Optional[List[Dict[str, Any]]]:
        return self._call("list_consultations_for_persona", persona_id, default=None, **filters)

    def get_consultation(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_consultation", session_id, default=None)

    def get_consultation_participants(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        return self._call("get_consultation_participants", session_id, default=None)

    def get_consultation_outcome(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_consultation_outcome", session_id, default=None)

    def get_consultation_evidence(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        return self._call("get_consultation_evidence", session_id, default=None)

    def get_consult_transcript(self, session_id: str, **filters: Any) -> Optional[Dict[str, Any]]:
        return self._call("get_consult_transcript", session_id, default=None, **filters)

    def get_consult_policy(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_consult_policy", persona_id, default=None)

    # Compatibility surfaces ------------------------------------------

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        return [
            item
            for item in self.list_approval_queue()
            if str(item.get("decision_state") or item.get("state") or item.get("status") or "").lower()
            in self._PENDING_APPROVAL_STATES
        ]

    def approval_evidence(self, approval_id: str) -> Optional[List[Dict[str, Any]]]:
        decision = self.get_approval_detail(approval_id)
        if decision is None:
            return None
        refs = decision.get("evidence_refs") or decision.get("evidence") or []
        return copy.deepcopy(list(refs))

    def get_review(self, review_id: str) -> Optional[Dict[str, Any]]:
        return next(
            (
                copy.deepcopy(item)
                for item in self.list_review_queue()
                if record_id(item, "item_id", "review_id", "id") == review_id
            ),
            None,
        )

    async def submit_governance_action(
        self,
        *,
        action_kind: str,
        target_id: str,
        action_id: str,
        payload: Mapping[str, Any],
        identity: Any,
        idempotency_key: str,
    ) -> Any:
        if self.submit_action is not None:
            return await _maybe_await(
                self.submit_action(
                    action_kind=action_kind,
                    target_id=target_id,
                    action_id=action_id,
                    payload=dict(payload),
                    identity=identity,
                    idempotency_key=idempotency_key,
                )
            )
        request_hash = stable_json_hash(
            {
                "action_kind": action_kind,
                "target_id": target_id,
                "action_id": action_id,
                "payload": payload,
            }
        )
        existing = self._idempotency.get(idempotency_key)
        if existing:
            if existing["request_hash"] != request_hash:
                raise RuntimeError("idempotency_conflict")
            return copy.deepcopy(existing["result"])
        command_id = str(uuid.uuid4())
        result = {
            "status": "accepted",
            "data": {
                "command_id": command_id,
                "commandId": command_id,
                "target_id": target_id,
                "action": action_id,
            },
            "meta": {
                "snapshot_at": self.utc_now(),
                "idempotency": {"idempotencyKey": idempotency_key, "replayed": False},
                "actor_id": _identity_operator_id(identity),
            },
        }
        self._idempotency[idempotency_key] = {
            "request_hash": request_hash,
            "result": copy.deepcopy(result),
        }
        return result

    def validate_decision(self, payload: Mapping[str, Any]) -> str:
        decision = str(payload.get("decision") or "").strip().lower()
        if not decision:
            if str(payload.get("rejection_reason") or "").strip():
                decision = "reject"
            elif str(payload.get("revision_notes") or "").strip():
                decision = "request_revision"
            else:
                decision = "approve"
        if decision not in self._DECISIONS:
            raise ValueError("decision")
        if decision == "reject" and not str(payload.get("rejection_reason") or "").strip():
            raise ValueError("rejection_reason")
        if decision in {"request_revision", "request_changes"} and not str(payload.get("revision_notes") or "").strip():
            raise ValueError("revision_notes")
        return decision

    def governance_ledger(
        self,
        *,
        source_type: Optional[str],
        status: Optional[str],
        q: str,
        page_token: Optional[str],
        page_size: int,
    ) -> Dict[str, Any]:
        entries_by_id: Dict[str, Dict[str, Any]] = {}
        for dataset, records in (
            ("approval_queue_items", self.list_approval_queue()),
            ("approval_decisions", self.list_approval_decisions()),
        ):
            for item in records:
                decision_id = record_id(item, "decision_id", "item_id", "id")
                if not decision_id:
                    continue
                state = str(
                    item.get("decision_state")
                    or item.get("state")
                    or item.get("outcome")
                    or item.get("decision")
                    or "unknown"
                )
                entry = {
                    "id": f"ledger-approval-{decision_id}",
                    "entry_id": f"ledger-approval-{decision_id}",
                    "source_type": "approval",
                    "source_dataset": dataset,
                    "event_type": "approval.pending" if state.lower() in self._PENDING_APPROVAL_STATES else "approval.decision",
                    "status": state,
                    "actor": item.get("submitted_by") or item.get("actor_id") or item.get("created_by"),
                    "target_type": item.get("decision_type") or item.get("target_type") or "ApprovalDecision",
                    "target_id": decision_id,
                    "occurred_at": record_time(item) or None,
                    "title": f"Approval: {item.get('decision_type') or item.get('target_type') or 'ApprovalDecision'}",
                    "summary": item.get("rationale") or item.get("reason"),
                    "href": f"/bff/approvals/{decision_id}",
                    "evidence_refs": copy.deepcopy(item.get("evidence_refs") or []),
                }
                entries_by_id.setdefault(entry["id"], entry)
        for item in self.get_interventions() or []:
            intervention_id = record_id(item, "intervention_id", "id")
            if not intervention_id:
                continue
            entries_by_id[f"ledger-intervention-{intervention_id}"] = {
                "id": f"ledger-intervention-{intervention_id}",
                "entry_id": f"ledger-intervention-{intervention_id}",
                "source_type": "intervention",
                "source_dataset": "v5_interventions",
                "event_type": f"intervention.{str(item.get('status') or 'unknown').lower()}",
                "status": item.get("status") or "unknown",
                "actor": item.get("triggered_by") or item.get("actor") or item.get("owner"),
                "target_type": item.get("target_type") or "Intervention",
                "target_id": item.get("target_id") or intervention_id,
                "occurred_at": record_time(item) or None,
                "title": f"Intervention: {item.get('kind') or item.get('type') or 'intervention'}",
                "summary": item.get("description") or item.get("summary") or item.get("reason"),
                "href": f"/bff/v5/interventions/{intervention_id}",
                "evidence_refs": copy.deepcopy(item.get("evidence_refs") or []),
            }
        for event in self.list_audit_events():
            event_id = record_id(event, "entry_id", "id", "auditId")
            haystack = " ".join(
                str(value or "")
                for value in (
                    event.get("action_type"),
                    event.get("event_type"),
                    event.get("target_type"),
                    (event.get("metadata") or {}).get("route") if isinstance(event.get("metadata"), dict) else None,
                )
            ).lower()
            audit_source = "override" if "override" in haystack else "intervention" if "intervention" in haystack else "approval" if ("approval" in haystack or "approve" in haystack) else None
            if not event_id or audit_source is None:
                continue
            entries_by_id[f"ledger-audit-{event_id}"] = {
                "id": f"ledger-audit-{event_id}",
                "entry_id": f"ledger-audit-{event_id}",
                "source_type": audit_source,
                "source_dataset": "governance_audit_events",
                "event_type": event.get("action_type") or event.get("event_type") or f"{audit_source}.audit",
                "status": event.get("outcome") or event.get("status"),
                "actor": event.get("actor"),
                "target_type": event.get("target_type"),
                "target_id": event.get("target_id") or event.get("entity_id"),
                "occurred_at": record_time(event) or None,
                "title": f"{audit_source.replace('_', ' ').title()} audit",
                "summary": event.get("reason"),
                "href": "/bff/audit",
                "evidence_refs": copy.deepcopy(event.get("evidence_refs") or []),
            }

        allowed_sources = {value.lower() for value in split_csv(source_type) or []}
        allowed_statuses = {value.lower() for value in split_csv(status) or []}
        needle = q.strip().lower()
        entries = []
        for entry in entries_by_id.values():
            if allowed_sources and str(entry.get("source_type") or "").lower() not in allowed_sources:
                continue
            if allowed_statuses and str(entry.get("status") or "").lower() not in allowed_statuses:
                continue
            if needle and needle not in " ".join(str(value or "") for value in entry.values()).lower():
                continue
            entries.append(entry)
        entries.sort(key=lambda entry: (str(entry.get("occurred_at") or ""), str(entry.get("id") or "")), reverse=True)
        total = len(entries)
        page_items, next_token = self.page_slice(entries, page_token, page_size)
        source_counts = count_by(entries, "source_type")
        return {
            "data": {
                "id": "management-governance-ledger",
                "items": page_items,
                "summary": {
                    "ledger_count": total,
                    "returned_ledger_count": len(page_items),
                    "approval_count": source_counts.get("approval", 0),
                    "intervention_count": source_counts.get("intervention", 0),
                    "override_count": source_counts.get("override", 0),
                    "by_source_type": source_counts,
                    "by_status": count_by(entries, "status"),
                    "by_event_type": count_by(entries, "event_type"),
                    "latest_at": entries[0].get("occurred_at") if entries else None,
                    "policy": "read_only_governance_ledger",
                },
                "policy": "read_only_governance_ledger",
            },
            "page_info": {
                "next_page_token": next_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": self.utc_now(),
                "policy": "read_only_governance_ledger",
                "filters": {"source_type": source_type, "status": status, "q": q},
            },
        }
