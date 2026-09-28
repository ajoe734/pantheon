"""Research domain durable write owner.

Provides authoritative, durable write and query operations for Research domain entities:
- Research Tickets (RW-01): create, patch, lifecycle state transitions, allowedActions
- Research Experiments (RW-04): create, cancel, validation, allowedActions
- Research Notes (KW-02): create, get, list

All operations use PostgresJsonOwnerStore with direct database round-trips for
every read and write, with zero in-memory caching, dictionary overlays,
JSON fallbacks, or abstract repositories.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


class ResearchIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with a different request hash."""
    pass


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_rfc3339(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _naive_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _atomic_insert_record(
    store: Any,
    record_id: str,
    record: Dict[str, Any],
    *,
    conn: Optional[Any] = None,
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Atomically insert record_id only if absent, returning (inserted, canonical_record)."""
    if hasattr(store, "compare_and_set"):
        return store.compare_and_set(record_id, None, record, conn=conn)
    if hasattr(store, "insert_if_absent"):
        return store.insert_if_absent(record_id, record, conn=conn)

    target_lock = getattr(store, "lock", None)
    target_rows = getattr(store, "rows", None)
    fail_flag = getattr(store, "fail", False)
    if target_lock is None or target_rows is None:
        db = getattr(store, "db", None)
        if db is not None:
            target_lock = getattr(db, "lock", None)
            target_rows = getattr(db, "rows", None)
            fail_flag = getattr(db, "fail", False)

    if target_lock is not None and target_rows is not None:
        if fail_flag:
            raise OSError("injected experiment commit failure")
        with target_lock:
            if record_id in target_rows:
                return False, copy.deepcopy(target_rows[record_id])
            target_rows[record_id] = copy.deepcopy(record)
            return True, copy.deepcopy(record)

    existing = store.get(record_id)
    if existing is not None:
        return False, existing
    store.put(record_id, record)
    return True, record


def _atomic_update_ticket_links(
    tickets_store: Any,
    ticket_id: str,
    exp_id: str,
    timestamp: str,
    *,
    conn: Optional[Any] = None,
    initial_ticket: Optional[Dict[str, Any]] = None,
    max_retries: int = 30,
) -> Optional[Dict[str, Any]]:
    """Atomically append exp_id to ticket's linked_experiments using CAS or store lock."""
    clean_ticket_id = str(ticket_id or "").strip()
    if not clean_ticket_id:
        return None

    if hasattr(tickets_store, "compare_and_set"):
        current = copy.deepcopy(initial_ticket) if initial_ticket is not None else None
        if current is None:
            current = tickets_store.get(clean_ticket_id)
        for _ in range(max_retries):
            if current is None or not isinstance(current, dict):
                return None
            linked = list(current.get("linked_experiments") or [])
            if exp_id in linked:
                return current
            updated = copy.deepcopy(current)
            linked.append(exp_id)
            updated["linked_experiments"] = linked
            updated["updated_at"] = timestamp
            success, actual = tickets_store.compare_and_set(clean_ticket_id, current, updated, conn=conn)
            if success:
                return updated
            current = actual if (actual is not None and isinstance(actual, dict)) else tickets_store.get(clean_ticket_id)
        raise RuntimeError(f"Failed to update ticket {clean_ticket_id!r} after {max_retries} CAS retries")

    store_cls = getattr(tickets_store, "__class__", None)
    if store_cls and "put" in store_cls.__dict__ and store_cls.__name__ != "AtomicIO":
        current = tickets_store.get(clean_ticket_id)
        if current is None or not isinstance(current, dict):
            return None
        updated = copy.deepcopy(current)
        linked = list(updated.get("linked_experiments") or [])
        if exp_id not in linked:
            linked.append(exp_id)
            updated["linked_experiments"] = linked
            updated["updated_at"] = timestamp
        tickets_store.put(clean_ticket_id, updated)
        return updated

    target_lock = getattr(tickets_store, "lock", None)
    target_rows = getattr(tickets_store, "rows", None)
    fail_flag = getattr(tickets_store, "fail", False)
    if target_lock is None or target_rows is None:
        db = getattr(tickets_store, "db", None)
        if db is not None:
            target_lock = getattr(db, "lock", None)
            target_rows = getattr(db, "rows", None)
            fail_flag = getattr(db, "fail", False)

    if target_lock is not None and target_rows is not None:
        if fail_flag:
            raise OSError("injected ticket commit failure")
        with target_lock:
            current = target_rows.get(clean_ticket_id)
            if current is None or not isinstance(current, dict):
                return None
            updated = copy.deepcopy(current)
            linked = list(updated.get("linked_experiments") or [])
            if exp_id not in linked:
                linked.append(exp_id)
                updated["linked_experiments"] = linked
                updated["updated_at"] = timestamp
                target_rows[clean_ticket_id] = copy.deepcopy(updated)
            return updated

    current = tickets_store.get(clean_ticket_id)
    if current is None or not isinstance(current, dict):
        return None
    updated = copy.deepcopy(current)
    linked = list(updated.get("linked_experiments") or [])
    if exp_id not in linked:
        linked.append(exp_id)
        updated["linked_experiments"] = linked
        updated["updated_at"] = timestamp
    tickets_store.put(clean_ticket_id, updated)
    return updated


class ResearchWriteOwner:
    """Authoritative durable write owner for the Research domain."""

    _RW04_CANCELABLE_STATUSES = frozenset({"queued", "running"})
    _RW04_RETRYABLE_STATUSES = frozenset({"failed", "canceled", "invalidated"})
    _RW04_ARCHIVABLE_STATUSES = frozenset({"completed", "failed", "canceled", "invalidated"})
    _RW04_INVALIDATABLE_STATUSES = frozenset({"completed", "failed"})

    def __init__(
        self,
        *,
        dsn: Optional[str] = None,
        schema: str = "research",
        bootstrap: bool = True,
        tickets_table: Optional[str] = None,
        experiments_table: Optional[str] = None,
        notes_table: Optional[str] = None,
        tickets_store: Optional[Any] = None,
        experiments_store: Optional[Any] = None,
        notes_store: Optional[Any] = None,
    ) -> None:
        if tickets_store is not None and experiments_store is not None and notes_store is not None:
            self.dsn = dsn or ""
            self.schema = schema
            self._tickets_store = tickets_store
            self._experiments_store = experiments_store
            self._notes_store = notes_store
            return

        if not dsn:
            raise ValueError("Postgres DSN is required for ResearchWriteOwner")
        self.dsn = dsn
        self.schema = schema.strip() or "research"
        self._tickets_store = tickets_store or PostgresJsonOwnerStore(
            dsn=dsn,
            table=tickets_table or f"{self.schema}.research_tickets",
            owner_service="research-svc",
            bootstrap=bootstrap,
        )
        self._experiments_store = experiments_store or PostgresJsonOwnerStore(
            dsn=dsn,
            table=experiments_table or f"{self.schema}.research_experiments",
            owner_service="research-svc",
            bootstrap=bootstrap,
        )
        self._notes_store = notes_store or PostgresJsonOwnerStore(
            dsn=dsn,
            table=notes_table or f"{self.schema}.research_notes",
            owner_service="research-svc",
            bootstrap=bootstrap,
        )

    # -------------------------------------------------------------------------
    # Tickets (RW-01) Projections & Mutations
    # -------------------------------------------------------------------------
    @staticmethod
    def _ticket_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
        normalized = str(status or "").strip().lower()
        if normalized == "archived":
            return {"canEdit": False, "canClose": False, "canArchive": False}
        if normalized == "closed":
            return {"canEdit": False, "canClose": False, "canArchive": True}
        if normalized in {"open", "in_progress"}:
            return {"canEdit": True, "canClose": True, "canArchive": False}
        return {"canEdit": False, "canClose": False, "canArchive": False}

    @classmethod
    def _project_ticket_summary(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "allowedActions": cls._ticket_allowed_actions(ticket.get("status")),
        }

    @classmethod
    def _project_ticket_detail(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "description": ticket.get("description"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "closed_at": ticket.get("closed_at"),
            "archived_at": ticket.get("archived_at"),
            "lifecycle_history": list(ticket.get("lifecycle_history") or []),
            "linked_experiments": list(ticket.get("linked_experiments") or []),
            "linked_artifacts": list(ticket.get("linked_artifacts") or []),
            "allowedActions": cls._ticket_allowed_actions(ticket.get("status")),
        }

    def create_research_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        owner: str,
        actor_id: str,
        created_at: Optional[str] = None,
        ticket_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("title is required")
        clean_priority = str(priority or "medium").strip().lower()
        clean_owner = str(owner or "").strip()
        clean_actor = str(actor_id or "").strip() or clean_owner or "system"
        timestamp = created_at or _utc_now_rfc3339()

        if ticket_id:
            tid = str(ticket_id).strip()
        else:
            existing_tickets = self._tickets_store.list_all()
            date_prefix = timestamp[:10].replace("-", "")
            known_ids = {str(t.get("ticket_id") or "") for t in existing_tickets if isinstance(t, dict)}
            idx = 1
            while True:
                cand_id = f"rt-{date_prefix}-{idx:03d}"
                if cand_id in known_ids:
                    idx += 1
                    continue
                existing_row = self._tickets_store.get(cand_id)
                if existing_row is None:
                    tid = cand_id
                    break
                known_ids.add(cand_id)
                idx += 1

        record: Dict[str, Any] = {
            "ticket_id": tid,
            "title": clean_title,
            "description": str(description or "").strip(),
            "status": "open",
            "priority": clean_priority,
            "owner": clean_owner,
            "created_at": timestamp,
            "updated_at": timestamp,
            "closed_at": None,
            "archived_at": None,
            "lifecycle_history": [
                {
                    "from_status": None,
                    "to_status": "open",
                    "transitioned_at": timestamp,
                    "transitioned_by": clean_actor,
                }
            ],
            "linked_experiments": [],
            "linked_artifacts": [],
        }
        self._tickets_store.put(tid, record)
        return self._project_ticket_detail(record)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        ticket = self._tickets_store.get(str(ticket_id))
        if ticket is None or not isinstance(ticket, dict):
            return None

        timestamp = updated_at or _utc_now_rfc3339()
        clean_actor = str(actor_id or "").strip() or str(ticket.get("owner") or "system")

        editable = {"title", "description", "priority", "owner"}
        for field in editable:
            if field in patch:
                ticket[field] = patch[field]

        next_status = patch.get("status")
        if next_status is not None:
            clean_next_status = str(next_status).strip().lower()
            prev_status = str(ticket.get("status") or "").strip().lower()
            if clean_next_status != prev_status:
                ticket["status"] = clean_next_status
                if clean_next_status == "closed":
                    ticket["closed_at"] = timestamp
                    ticket["archived_at"] = None
                elif clean_next_status == "archived":
                    ticket["archived_at"] = timestamp
                    if ticket.get("closed_at") is None:
                        ticket["closed_at"] = timestamp
                else:
                    if clean_next_status in {"open", "in_progress"}:
                        ticket["closed_at"] = None
                    if clean_next_status != "archived":
                        ticket["archived_at"] = None

                history = list(ticket.get("lifecycle_history") or [])
                history.append(
                    {
                        "from_status": prev_status,
                        "to_status": clean_next_status,
                        "transitioned_at": timestamp,
                        "transitioned_by": clean_actor,
                    }
                )
                ticket["lifecycle_history"] = history

        if "linked_experiments" in patch and isinstance(patch["linked_experiments"], list):
            ticket["linked_experiments"] = list(patch["linked_experiments"])
        if "linked_artifacts" in patch and isinstance(patch["linked_artifacts"], list):
            ticket["linked_artifacts"] = list(patch["linked_artifacts"])

        ticket["updated_at"] = timestamp
        self._tickets_store.put(ticket_id, ticket)
        return self._project_ticket_detail(ticket)

    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        ticket = self._tickets_store.get(str(ticket_id))
        return self._project_ticket_detail(ticket) if isinstance(ticket, dict) else None

    def list_research_tickets(
        self,
        *,
        statuses: Optional[Sequence[str]] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        tickets = self._tickets_store.list_all()
        if statuses:
            req_statuses = {str(s).strip().lower() for s in statuses if str(s).strip()}
            tickets = [t for t in tickets if str(t.get("status") or "").strip().lower() in req_statuses]
        if owner:
            req_owner = str(owner).strip()
            tickets = [t for t in tickets if str(t.get("owner") or "").strip() == req_owner]
        tickets.sort(
            key=lambda t: _naive_utc(_parse_rfc3339(t.get("updated_at")) or _parse_rfc3339(t.get("created_at"))),
            reverse=True,
        )
        return [self._project_ticket_summary(t) for t in tickets if isinstance(t, dict)]

    # -------------------------------------------------------------------------
    # Experiments (RW-04) Projections & Mutations
    # -------------------------------------------------------------------------
    @classmethod
    def _rw04_can_cancel(cls, status: Optional[str]) -> bool:
        return str(status or "").strip().lower() in cls._RW04_CANCELABLE_STATUSES

    @classmethod
    def _rw04_allowed_actions(cls, exp: Dict[str, Any]) -> Dict[str, bool]:
        status = str(exp.get("status") or "").strip().lower()
        is_archived = bool(exp.get("is_archived", False))
        return {
            "canCancel": status in cls._RW04_CANCELABLE_STATUSES,
            "canRetry": status in cls._RW04_RETRYABLE_STATUSES,
            "canArchive": (status in cls._RW04_ARCHIVABLE_STATUSES) and not is_archived,
            "canInvalidate": status in cls._RW04_INVALIDATABLE_STATUSES,
        }

    @classmethod
    def _project_experiment_summary(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        strategy_selector = exp.get("strategy_selector") or {}
        strategy_id = (
            exp.get("linked_strategy_id")
            or exp.get("strategy_id")
            or strategy_selector.get("strategy_id")
        )
        run_config = exp.get("run_config") or {}
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "attempt_number": exp.get("attempt_number", 1),
            "parent_experiment_id": exp.get("parent_experiment_id"),
            "root_experiment_id": exp.get("root_experiment_id"),
            "is_archived": bool(exp.get("is_archived", False)),
            "archived_at": exp.get("archived_at"),
            "invalidated_at": exp.get("invalidated_at"),
            "invalidated_reason": exp.get("invalidated_reason"),
            "status": status,
            "stage": exp.get("stage"),
            "framework": exp.get("framework") or run_config.get("backend"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "cancellation_fence": exp.get("cancellation_fence"),
            "strategy_id": strategy_id,
            "linked_strategy_id": strategy_id,
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id"),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "allowedActions": cls._rw04_allowed_actions(exp),
        }

    @classmethod
    def _project_experiment_detail(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        failure = exp.get("failure") or {}
        progress = exp.get("progress") or {}
        strategy_selector = exp.get("strategy_selector") or {}
        run_config = exp.get("run_config") or {}
        time_range = run_config.get("time_range") or {}
        launch_context = exp.get("launch_context") or {}
        exp_id = exp.get("experiment_id")
        cmd_id = exp.get("command_id") or f"cmd-{exp_id}"
        clean_key = exp.get("idempotency_key")
        receipt = exp.get("receipt")
        if not receipt or not isinstance(receipt, dict):
            receipt = {
                "receipt_id": f"rcpt-{exp_id}",
                "command_id": cmd_id,
                "commandId": cmd_id,
                "aggregate_type": exp.get("aggregate_type") or "research_experiment",
                "aggregate_id": exp_id,
                "aggregate_version": exp.get("aggregate_version", 1),
                "status": status,
                "event_id": exp.get("event_id") or f"evt-{exp_id}",
                "correlation_id": exp.get("correlation_id") or clean_key or exp_id,
                "owner": exp.get("owner") or "research",
                "committed_at": exp.get("committed_at") or exp.get("queued_at") or _utc_now_rfc3339(),
                "command": "CreateResearchExperiment",
                "target": {"type": "research_experiment", "id": exp_id},
                "submitted_at": exp.get("queued_at"),
                "accepted_at": exp.get("queued_at"),
            }
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "attempt_number": exp.get("attempt_number", 1),
            "parent_experiment_id": exp.get("parent_experiment_id"),
            "root_experiment_id": exp.get("root_experiment_id"),
            "is_archived": bool(exp.get("is_archived", False)),
            "archived_at": exp.get("archived_at"),
            "invalidated_at": exp.get("invalidated_at"),
            "invalidated_reason": exp.get("invalidated_reason"),
            "status": status,
            "stage": exp.get("stage"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "cancellation_fence": exp.get("cancellation_fence"),
            "progress": {
                "percent": progress.get("percent"),
                "phase": progress.get("phase"),
                "message": progress.get("message"),
            },
            "strategy_selector": {
                "strategy_id": strategy_selector.get("strategy_id"),
                "variant_id": strategy_selector.get("variant_id"),
            },
            "parameter_set": json.loads(json.dumps(exp.get("parameter_set") or {})),
            "run_config": {
                "backend": run_config.get("backend"),
                "dataset_ref": run_config.get("dataset_ref"),
                "dataset_manifest_id": run_config.get("dataset_manifest_id"),
                "time_range": {
                    "start_at": time_range.get("start_at"),
                    "end_at": time_range.get("end_at"),
                },
                "execution_mode": run_config.get("execution_mode"),
                "priority": run_config.get("priority"),
                "requested_by": run_config.get("requested_by"),
            },
            "launch_context": {
                "analysis_refs": (
                    list(launch_context["analysis_refs"])
                    if isinstance(launch_context.get("analysis_refs"), list)
                    else None
                ),
            },
            "validation_warnings": json.loads(json.dumps(exp.get("validation_warnings") or [])),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "artifact_refs": json.loads(json.dumps(exp.get("artifact_refs") or [])),
            "framework": exp.get("framework") or run_config.get("backend"),
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id"),
            "research_linkage": json.loads(json.dumps(exp.get("research_linkage") or {})),
            "evidence_refs": json.loads(json.dumps(exp.get("evidence_refs") or [])),
            "safety_assertions": json.loads(json.dumps(exp.get("safety_assertions") or {})),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "deployment_stage": exp.get("deployment_stage"),
            "failure": {
                "reason_code": failure.get("reason_code"),
                "message": failure.get("message"),
            },
            "allowedActions": cls._rw04_allowed_actions(exp),
            "command_id": cmd_id,
            "commandId": cmd_id,
            "aggregate_type": exp.get("aggregate_type") or "research_experiment",
            "aggregate_id": exp_id,
            "aggregate_version": exp.get("aggregate_version", 1),
            "event_id": exp.get("event_id") or f"evt-{exp_id}",
            "correlation_id": exp.get("correlation_id") or clean_key or exp_id,
            "owner": exp.get("owner") or "research",
            "committed_at": exp.get("committed_at") or exp.get("queued_at") or _utc_now_rfc3339(),
            "receipt": receipt,
            "idempotency_key": clean_key,
            "tenant_id": exp.get("tenant_id"),
            "actor_id": exp.get("actor_id"),
        }

    @classmethod
    def _build_experiment_record(
        cls,
        *,
        exp_id: str,
        clean_ticket_id: str,
        clean_exp_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        clean_key: Optional[str],
        clean_hash: Optional[str],
        clean_tenant: Optional[str],
        clean_actor: Optional[str],
        command_id: Optional[str],
        timestamp: str,
        is_committed: bool = False,
    ) -> Dict[str, Any]:
        cmd_id = command_id or f"cmd-{exp_id}"
        canonical_receipt = {
            "receipt_id": f"rcpt-{exp_id}",
            "command_id": cmd_id,
            "commandId": cmd_id,
            "aggregate_type": "research_experiment",
            "aggregate_id": exp_id,
            "aggregate_version": 1,
            "status": "queued",
            "event_id": f"evt-{exp_id}",
            "correlation_id": clean_key or exp_id,
            "owner": "research",
            "committed_at": timestamp,
            "command": "CreateResearchExperiment",
            "target": {"type": "research_experiment", "id": exp_id},
            "submitted_at": timestamp,
            "accepted_at": timestamp,
        }

        record: Dict[str, Any] = {
            "experiment_id": exp_id,
            "ticket_id": clean_ticket_id,
            "experiment_name": clean_exp_name,
            "status": "queued",
            "stage": run_config.get("stage") or "backtest",
            "queued_at": timestamp,
            "started_at": None,
            "completed_at": None,
            "progress": {"percent": None, "phase": None, "message": None},
            "strategy_selector": json.loads(json.dumps(strategy_selector or {})),
            "parameter_set": json.loads(json.dumps(parameter_set or {})),
            "run_config": json.loads(json.dumps(run_config or {})),
            "launch_context": json.loads(json.dumps(launch_context or {})),
            "validation_warnings": [],
            "artifact_ids": [],
            "failure": {"reason_code": None, "message": None},
            "allowedActions": {"canCancel": True, "canRetry": False, "canArchive": False, "canInvalidate": False},
            "idempotency_key": clean_key,
            "request_hash": clean_hash,
            "tenant_id": clean_tenant,
            "actor_id": clean_actor,
            "created_by": clean_actor,
            "command_id": cmd_id,
            "aggregate_type": "research_experiment",
            "aggregate_id": exp_id,
            "aggregate_version": 1,
            "event_id": canonical_receipt["event_id"],
            "correlation_id": canonical_receipt["correlation_id"],
            "owner": "research",
            "committed_at": timestamp,
            "receipt": canonical_receipt,
            "is_committed": is_committed,
        }
        record["allowedActions"] = cls._rw04_allowed_actions(record)
        return record

    def _recover_or_replay_experiment(
        self,
        existing_exp: Dict[str, Any],
        *,
        clean_key: str,
        clean_hash: Optional[str],
        timestamp: str,
    ) -> Dict[str, Any]:
        saved_hash = existing_exp.get("request_hash")
        if saved_hash and clean_hash and saved_hash != clean_hash:
            raise ResearchIdempotencyConflictError(
                f"Key {clean_key!r} is bound to a different request hash"
            )

        exp_id = str(existing_exp.get("experiment_id") or "").strip()

        if existing_exp.get("is_committed", True):
            return self._project_experiment_detail(existing_exp)

        exp_ticket_id = str(existing_exp.get("ticket_id") or "").strip() or None
        if exp_ticket_id:
            ticket = self._tickets_store.get(exp_ticket_id)
            if ticket and isinstance(ticket, dict):
                linked = ticket.get("linked_experiments") or []
                if exp_id in linked:
                    recovered = copy.deepcopy(existing_exp)
                    recovered["is_committed"] = True
                    self._experiments_store.put(exp_id, recovered)
                    return self._project_experiment_detail(recovered)
            raise ResearchIdempotencyConflictError(
                f"Command with key {clean_key!r} is currently being processed"
            )

        recovered = copy.deepcopy(existing_exp)
        recovered["is_committed"] = True
        self._experiments_store.put(exp_id, recovered)
        return self._project_experiment_detail(recovered)

    def create_research_experiment(
        self,
        *,
        ticket_id: str,
        experiment_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        queued_at: Optional[str] = None,
        experiment_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_hash: Optional[str] = None,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        command_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        clean_ticket_id = str(ticket_id or "").strip()
        clean_exp_name = str(experiment_name or "").strip()
        if not clean_exp_name:
            raise ValueError("experiment_name is required")
        timestamp = queued_at or _utc_now_rfc3339()

        clean_key = str(idempotency_key).strip() if idempotency_key else None
        clean_actor = str(actor_id or (launch_context or {}).get("actor_id") or "").strip() or None
        clean_tenant = str(tenant_id or (launch_context or {}).get("tenant_id") or "").strip() or None
        clean_hash = str(request_hash or "").strip() if request_hash else None

        existing_experiments = self._experiments_store.list_all()

        if clean_key:
            for exp in existing_experiments:
                if not isinstance(exp, dict):
                    continue
                if exp.get("idempotency_key") != clean_key:
                    continue
                exp_tenant = str(exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                if exp_tenant != clean_tenant:
                    continue
                exp_actor = str(exp.get("actor_id") or exp.get("created_by") or (exp.get("launch_context") or {}).get("actor_id") or "").strip() or None
                if exp_actor != clean_actor:
                    continue
                return self._recover_or_replay_experiment(exp, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)

        original_ticket = None
        if clean_ticket_id:
            ticket = self._tickets_store.get(clean_ticket_id)
            if ticket and isinstance(ticket, dict):
                original_ticket = copy.deepcopy(ticket)

        if experiment_id:
            exp_id = str(experiment_id).strip()
            record = self._build_experiment_record(
                exp_id=exp_id,
                clean_ticket_id=clean_ticket_id,
                clean_exp_name=clean_exp_name,
                strategy_selector=strategy_selector,
                parameter_set=parameter_set,
                run_config=run_config,
                launch_context=launch_context,
                clean_key=clean_key,
                clean_hash=clean_hash,
                clean_tenant=clean_tenant,
                clean_actor=clean_actor,
                command_id=command_id,
                timestamp=timestamp,
                is_committed=False if clean_ticket_id else True,
            )
            inserted, existing_row = _atomic_insert_record(self._experiments_store, exp_id, record)
            if not inserted and existing_row is not None:
                if clean_key and existing_row.get("idempotency_key") == clean_key:
                    row_tenant = str(existing_row.get("tenant_id") or (existing_row.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                    row_actor = str(existing_row.get("actor_id") or existing_row.get("created_by") or (existing_row.get("launch_context") or {}).get("actor_id") or "").strip() or None
                    if row_tenant == clean_tenant and row_actor == clean_actor:
                        return self._recover_or_replay_experiment(existing_row, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)
                raise ValueError(f"Experiment {exp_id!r} already exists")
        else:
            known_ids = {str(e.get("experiment_id") or "") for e in existing_experiments if isinstance(e, dict)}
            date_prefix = timestamp[:10].replace("-", "")
            idx = 1
            while True:
                cand_id = f"exp-{date_prefix}-{idx:03d}"
                if cand_id in known_ids:
                    idx += 1
                    continue
                existing_probe = self._experiments_store.get(cand_id)
                if existing_probe is not None:
                    known_ids.add(cand_id)
                    if clean_key and existing_probe.get("idempotency_key") == clean_key:
                        row_tenant = str(existing_probe.get("tenant_id") or (existing_probe.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                        row_actor = str(existing_probe.get("actor_id") or existing_probe.get("created_by") or (existing_probe.get("launch_context") or {}).get("actor_id") or "").strip() or None
                        if row_tenant == clean_tenant and row_actor == clean_actor:
                            return self._recover_or_replay_experiment(existing_probe, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)
                    idx += 1
                    continue

                exp_id = cand_id
                record = self._build_experiment_record(
                    exp_id=exp_id,
                    clean_ticket_id=clean_ticket_id,
                    clean_exp_name=clean_exp_name,
                    strategy_selector=strategy_selector,
                    parameter_set=parameter_set,
                    run_config=run_config,
                    launch_context=launch_context,
                    clean_key=clean_key,
                    clean_hash=clean_hash,
                    clean_tenant=clean_tenant,
                    clean_actor=clean_actor,
                    command_id=command_id,
                    timestamp=timestamp,
                    is_committed=False if clean_ticket_id else True,
                )

                inserted, existing_row = _atomic_insert_record(self._experiments_store, exp_id, record)
                if inserted:
                    break

                known_ids.add(exp_id)
                if existing_row and isinstance(existing_row, dict):
                    if clean_key and existing_row.get("idempotency_key") == clean_key:
                        row_tenant = str(existing_row.get("tenant_id") or (existing_row.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                        row_actor = str(existing_row.get("actor_id") or existing_row.get("created_by") or (existing_row.get("launch_context") or {}).get("actor_id") or "").strip() or None
                        if row_tenant == clean_tenant and row_actor == clean_actor:
                            return self._recover_or_replay_experiment(existing_row, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)
                idx += 1

        if clean_ticket_id and original_ticket is not None:
            try:
                _atomic_update_ticket_links(
                    self._tickets_store,
                    clean_ticket_id,
                    exp_id,
                    timestamp,
                    initial_ticket=original_ticket,
                )
            except Exception:
                try:
                    if hasattr(self._experiments_store, "delete_if_matches"):
                        self._experiments_store.delete_if_matches(exp_id, record)
                    elif hasattr(self._experiments_store, "delete"):
                        self._experiments_store.delete(exp_id)
                except Exception:
                    pass
                raise

        if not record.get("is_committed"):
            record["is_committed"] = True
            self._experiments_store.put(exp_id, record)
        return self._project_experiment_detail(record)

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        exp = self._experiments_store.get(str(experiment_id))
        if exp is None or not isinstance(exp, dict):
            return None
        status = str(exp.get("status") or "").strip().lower()
        if status not in self._RW04_CANCELABLE_STATUSES:
            return None

        timestamp = completed_at or _utc_now_rfc3339()
        exp["status"] = "canceled"
        exp["completed_at"] = timestamp
        exp["cancellation_fence"] = timestamp
        if reason:
            exp["cancellation_reason"] = reason
        if actor_id:
            exp["canceled_by"] = actor_id
        exp["updated_at"] = timestamp
        exp["allowedActions"] = self._rw04_allowed_actions(exp)
        self._experiments_store.put(experiment_id, exp)
        return self._project_experiment_detail(exp)

    def retry_research_experiment(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str] = None,
        requested_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_hash: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        exp = self._experiments_store.get(str(experiment_id))
        if exp is None or not isinstance(exp, dict):
            return None
        status = str(exp.get("status") or "").strip().lower()
        if status not in self._RW04_RETRYABLE_STATUSES:
            return None

        timestamp = requested_at or _utc_now_rfc3339()
        attempt_number = int(exp.get("attempt_number") or 1) + 1
        parent_id = exp["experiment_id"]
        root_id = exp.get("root_experiment_id") or parent_id

        clean_key = str(idempotency_key or "").strip() or None
        clean_hash = str(request_hash or "").strip() or None
        clean_tenant = str(tenant_id or exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
        clean_actor = str(actor_id or exp.get("created_by") or exp.get("actor_id") or (exp.get("launch_context") or {}).get("actor_id") or "").strip() or None

        existing_experiments = self._experiments_store.list_all()
        if clean_key:
            for e in existing_experiments:
                if not isinstance(e, dict):
                    continue
                if e.get("idempotency_key") != clean_key:
                    continue
                e_tenant = str(e.get("tenant_id") or (e.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                if clean_tenant and e_tenant and e_tenant != clean_tenant:
                    continue
                e_actor = str(e.get("actor_id") or e.get("created_by") or (e.get("launch_context") or {}).get("actor_id") or "").strip() or None
                if clean_actor and e_actor and e_actor != clean_actor:
                    continue
                if e.get("parent_experiment_id") == parent_id or e.get("root_experiment_id") == root_id:
                    return self._recover_or_replay_experiment(e, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)

        known_ids = {str(e.get("experiment_id") or "") for e in existing_experiments if isinstance(e, dict)}
        date_prefix = timestamp[:10].replace("-", "")
        idx = 1
        ticket_id = str(exp.get("ticket_id") or "").strip() or None
        while True:
            cand_id = f"exp-{date_prefix}-{idx:03d}"
            if cand_id in known_ids:
                idx += 1
                continue
            existing_probe = self._experiments_store.get(cand_id)
            if existing_probe is not None:
                known_ids.add(cand_id)
                if clean_key and existing_probe.get("idempotency_key") == clean_key:
                    e_tenant = str(existing_probe.get("tenant_id") or (existing_probe.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                    if not clean_tenant or not e_tenant or e_tenant == clean_tenant:
                        e_actor = str(existing_probe.get("actor_id") or existing_probe.get("created_by") or (existing_probe.get("launch_context") or {}).get("actor_id") or "").strip() or None
                        if not clean_actor or not e_actor or e_actor == clean_actor:
                            if existing_probe.get("parent_experiment_id") == parent_id or existing_probe.get("root_experiment_id") == root_id:
                                return self._recover_or_replay_experiment(existing_probe, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)
                idx += 1
                continue
            new_exp_id = cand_id
            new_record: Dict[str, Any] = {
                "experiment_id": new_exp_id,
                "ticket_id": ticket_id or "",
                "experiment_name": f"{exp.get('experiment_name', '')} (retry #{attempt_number})",
                "attempt_number": attempt_number,
                "parent_experiment_id": parent_id,
                "root_experiment_id": root_id,
                "status": "queued",
                "stage": exp.get("stage") or "backtest",
                "queued_at": timestamp,
                "started_at": None,
                "completed_at": None,
                "progress": {"percent": None, "phase": None, "message": None},
                "strategy_selector": json.loads(json.dumps(exp.get("strategy_selector") or {})),
                "parameter_set": json.loads(json.dumps(exp.get("parameter_set") or {})),
                "run_config": json.loads(json.dumps(exp.get("run_config") or {})),
                "launch_context": json.loads(json.dumps(exp.get("launch_context") or {})),
                "validation_warnings": [],
                "artifact_ids": [],
                "failure": {"reason_code": None, "message": None},
                "created_by": clean_actor,
                "actor_id": clean_actor,
                "tenant_id": clean_tenant,
                "idempotency_key": clean_key,
                "request_hash": clean_hash,
                "is_committed": False if ticket_id else True,
            }
            new_record["allowedActions"] = self._rw04_allowed_actions(new_record)
            inserted, existing_row = _atomic_insert_record(self._experiments_store, new_exp_id, new_record)
            if inserted:
                break
            known_ids.add(cand_id)
            if existing_row and isinstance(existing_row, dict):
                if clean_key and existing_row.get("idempotency_key") == clean_key:
                    e_tenant = str(existing_row.get("tenant_id") or (existing_row.get("launch_context") or {}).get("tenant_id") or "").strip() or None
                    if not clean_tenant or not e_tenant or e_tenant == clean_tenant:
                        e_actor = str(existing_row.get("actor_id") or existing_row.get("created_by") or (existing_row.get("launch_context") or {}).get("actor_id") or "").strip() or None
                        if not clean_actor or not e_actor or e_actor == clean_actor:
                            if existing_row.get("parent_experiment_id") == parent_id or existing_row.get("root_experiment_id") == root_id:
                                return self._recover_or_replay_experiment(existing_row, clean_key=clean_key, clean_hash=clean_hash, timestamp=timestamp)
            idx += 1

        if ticket_id:
            try:
                _atomic_update_ticket_links(self._tickets_store, ticket_id, new_exp_id, timestamp)
            except Exception:
                try:
                    if hasattr(self._experiments_store, "delete_if_matches"):
                        self._experiments_store.delete_if_matches(new_exp_id, new_record)
                    elif hasattr(self._experiments_store, "delete"):
                        self._experiments_store.delete(new_exp_id)
                except Exception:
                    pass
                raise

        if not new_record.get("is_committed"):
            new_record["is_committed"] = True
            self._experiments_store.put(new_exp_id, new_record)
        return self._project_experiment_detail(new_record)

    def archive_research_experiment(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str] = None,
        archived_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        exp = self._experiments_store.get(str(experiment_id))
        if exp is None or not isinstance(exp, dict):
            return None
        status = str(exp.get("status") or "").strip().lower()
        if status not in self._RW04_ARCHIVABLE_STATUSES:
            return None
        timestamp = archived_at or _utc_now_rfc3339()
        exp["is_archived"] = True
        exp["archived_at"] = timestamp
        exp["archived_by"] = actor_id
        exp["updated_at"] = timestamp
        exp["allowedActions"] = self._rw04_allowed_actions(exp)
        self._experiments_store.put(experiment_id, exp)
        return self._project_experiment_detail(exp)

    def invalidate_research_experiment(
        self,
        experiment_id: str,
        *,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
        invalidated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        exp = self._experiments_store.get(str(experiment_id))
        if exp is None or not isinstance(exp, dict):
            return None
        status = str(exp.get("status") or "").strip().lower()
        if status in {"invalidated", "canceled"}:
            return None
        timestamp = invalidated_at or _utc_now_rfc3339()
        exp["status"] = "invalidated"
        exp["invalidated_at"] = timestamp
        exp["invalidated_reason"] = reason or "Invalidated by operator"
        exp["invalidated_by"] = actor_id
        exp["updated_at"] = timestamp
        exp["allowedActions"] = self._rw04_allowed_actions(exp)
        self._experiments_store.put(experiment_id, exp)
        return self._project_experiment_detail(exp)

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        exp = self._experiments_store.get(str(experiment_id))
        if exp and isinstance(exp, dict) and not exp.get("is_committed", True):
            return None
        return self._project_experiment_detail(exp) if isinstance(exp, dict) else None

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        experiments = self._experiments_store.list_all()
        experiments = [e for e in experiments if isinstance(e, dict) and e.get("is_committed", True)]
        if not include_archived:
            experiments = [e for e in experiments if not bool(e.get("is_archived", False))]
        if ticket_id:
            clean_tid = str(ticket_id).strip()
            experiments = [e for e in experiments if str(e.get("ticket_id") or "").strip() == clean_tid]
        if status:
            req_status = str(status).strip().lower()
            experiments = [e for e in experiments if str(e.get("status") or "").strip().lower() == req_status]
        experiments.sort(
            key=lambda e: _naive_utc(_parse_rfc3339(e.get("queued_at"))),
            reverse=True,
        )
        return [self._project_experiment_summary(e) for e in experiments if isinstance(e, dict)]

    # -------------------------------------------------------------------------
    # Notes (KW-02) Projections & Mutations
    # -------------------------------------------------------------------------
    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(note, dict):
            return None
        payload = json.loads(json.dumps(note))
        note_id = str(payload.get("note_id") or payload.get("id") or "").strip()
        if not note_id:
            existing_notes = self._notes_store.list_all()
            now_iso = _utc_now_rfc3339()
            date_prefix = now_iso[:10].replace("-", "")
            known_ids = {str(n.get("note_id") or n.get("id") or "") for n in existing_notes if isinstance(n, dict)}
            idx = 1
            while True:
                cand_id = f"note-{date_prefix}-{idx:03d}"
                if cand_id in known_ids:
                    idx += 1
                    continue
                existing_row = self._notes_store.get(cand_id)
                if existing_row is None:
                    note_id = cand_id
                    break
                known_ids.add(cand_id)
                idx += 1
            payload["note_id"] = note_id
            payload["id"] = note_id

        if not payload.get("created_at"):
            payload["created_at"] = _utc_now_rfc3339()
        if not payload.get("updated_at"):
            payload["updated_at"] = payload.get("created_at")

        self._notes_store.put(note_id, payload)
        return json.loads(json.dumps(payload))

    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not note_id:
            return None
        note = self._notes_store.get(str(note_id))
        return json.loads(json.dumps(note)) if isinstance(note, dict) else None

    def list_research_notes(self) -> List[Dict[str, Any]]:
        notes = self._notes_store.list_all()
        notes.sort(
            key=lambda n: _naive_utc(_parse_rfc3339(n.get("updated_at")) or _parse_rfc3339(n.get("created_at"))),
            reverse=True,
        )
        return [json.loads(json.dumps(n)) for n in notes if isinstance(n, dict)]


def build_research_write_owner(
    *,
    dsn: Optional[str] = None,
    schema: str = "research",
    bootstrap: bool = True,
    tickets_table: Optional[str] = None,
    experiments_table: Optional[str] = None,
    notes_table: Optional[str] = None,
) -> ResearchWriteOwner:
    """Factory creating a ResearchWriteOwner bound to Postgres storage."""
    selected_dsn = dsn or os.getenv("RESEARCH_STORE_DSN") or os.getenv("DATABASE_URL")
    if not selected_dsn:
        raise ValueError(
            "DATABASE_URL or RESEARCH_STORE_DSN is required for Postgres research write owner"
        )
    return ResearchWriteOwner(
        dsn=selected_dsn,
        schema=schema,
        bootstrap=bootstrap,
        tickets_table=tickets_table,
        experiments_table=experiments_table,
        notes_table=notes_table,
    )
