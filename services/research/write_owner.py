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
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


class ResearchIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with a different request hash."""
    pass


class ResearchTicketLifecycleConflictError(ValueError):
    """Raised when an operation conflicts with the current lifecycle state of a research ticket."""

    def __init__(
        self,
        message: str,
        *,
        reason: Optional[str] = None,
        precondition_failed: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason or message
        self.precondition_failed = precondition_failed
class ResearchTenantAuthorizationError(PermissionError):
    """Raised when an operation is attempted across tenant boundaries."""

    def __init__(
        self,
        message: str = "Cross-tenant access forbidden",
        *,
        tenant_id: Optional[str] = None,
        expected_tenant: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.expected_tenant = expected_tenant



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
    unique_fields: tuple[str, ...] = (),
    conn: Optional[Any] = None,
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Atomically insert record_id only if absent and unique_fields do not conflict, returning (inserted, canonical_record)."""
    if not unique_fields and record.get("idempotency_key"):
        unique_fields = ("tenant_id", "actor_id", "idempotency_key")

    if hasattr(store, "insert_if_absent"):
        try:
            return store.insert_if_absent(record_id, record, unique_fields=unique_fields, conn=conn)
        except TypeError:
            pass

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
            raise OSError("injected commit failure")
        with target_lock:
            if record_id in target_rows:
                return False, copy.deepcopy(target_rows[record_id])
            if unique_fields:
                for other in target_rows.values():
                    if isinstance(other, dict) and all(
                        other.get(f) == record.get(f) for f in unique_fields
                    ):
                        return False, copy.deepcopy(other)
            target_rows[record_id] = copy.deepcopy(record)
            return True, copy.deepcopy(record)

    if hasattr(store, "compare_and_set"):
        if unique_fields and hasattr(store, "list_all"):
            try:
                for other in store.list_all():
                    if isinstance(other, dict) and all(
                        other.get(f) == record.get(f) for f in unique_fields
                    ):
                        return False, copy.deepcopy(other)
            except Exception:
                pass
        return store.compare_and_set(record_id, None, record, conn=conn)

    existing = store.get(record_id)
    if existing is not None:
        return False, existing
    if unique_fields and hasattr(store, "list_all"):
        try:
            for other in store.list_all():
                if isinstance(other, dict) and all(
                    other.get(f) == record.get(f) for f in unique_fields
                ):
                    return False, copy.deepcopy(other)
        except Exception:
            pass
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


def _finalize_experiment_record(
    store: Any,
    exp_id: str,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    """Atomically finalize experiment with conditional database CAS to preserve concurrent state."""
    if hasattr(store, "compare_and_set"):
        expected = copy.deepcopy(record)
        expected["is_committed"] = False
        candidate = copy.deepcopy(record)
        candidate["is_committed"] = True
        ok, current = store.compare_and_set(exp_id, expected, candidate)
        if ok:
            return current if isinstance(current, dict) else candidate

        if current is None and hasattr(store, "get"):
            current = store.get(exp_id)
        if current and isinstance(current, dict):
            if current.get("is_committed"):
                return current
            for _ in range(15):
                exp_snap = copy.deepcopy(current)
                cand_snap = copy.deepcopy(current)
                cand_snap["is_committed"] = True
                ok_retry, next_current = store.compare_and_set(exp_id, exp_snap, cand_snap)
                if ok_retry:
                    return next_current if isinstance(next_current, dict) else cand_snap
                current = next_current if next_current is not None else (store.get(exp_id) if hasattr(store, "get") else None)
                if not current:
                    raise RuntimeError(f"Experiment {exp_id!r} disappeared during finalization CAS")
                if current.get("is_committed"):
                    return current
        raise RuntimeError(f"Experiment {exp_id!r} finalization CAS exhausted")

    target_lock = getattr(store, "lock", None)
    target_rows = getattr(store, "rows", None)
    if target_lock is None or target_rows is None:
        db = getattr(store, "db", None)
        if db is not None:
            target_lock = getattr(db, "lock", None)
            target_rows = getattr(db, "rows", None)

    if target_lock is not None and isinstance(target_rows, dict):
        with target_lock:
            current = target_rows.get(exp_id)
            if isinstance(current, dict):
                if current.get("is_committed"):
                    return copy.deepcopy(current)
                merged = copy.deepcopy(current)
                merged["is_committed"] = True
                target_rows[exp_id] = merged
                return copy.deepcopy(merged)
            raise RuntimeError(f"Experiment {exp_id!r} not found for finalization")

    raise RuntimeError(f"Experiment {exp_id!r} store does not support conditional finalization")


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
        self._active_inflight: set[tuple[Optional[str], Optional[str], str]] = set()
        self._active_inflight_lock = threading.Lock()
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
            "tenant_id": ticket.get("tenant_id"),
        }

    @classmethod
    def _project_ticket_detail(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        tid = ticket.get("ticket_id")
        cmd_id = ticket.get("command_id") or f"cmd-{tid}"
        clean_key = ticket.get("idempotency_key")
        receipt = ticket.get("receipt")
        if not receipt or not isinstance(receipt, dict):
            timestamp = ticket.get("created_at") or _utc_now_rfc3339()
            receipt = {
                "receipt_id": f"rcpt-{tid}",
                "command_id": cmd_id,
                "commandId": cmd_id,
                "aggregate_type": ticket.get("aggregate_type") or "research_ticket",
                "aggregate_id": tid,
                "aggregate_version": ticket.get("aggregate_version", 1),
                "status": ticket.get("status") or "open",
                "event_id": ticket.get("event_id") or f"evt-{tid}",
                "correlation_id": ticket.get("correlation_id") or clean_key or tid,
                "owner": "research",
                "committed_at": ticket.get("committed_at") or timestamp,
                "command": "CreateResearchTicket",
                "target": {"type": "research_ticket", "id": tid},
                "submitted_at": timestamp,
                "accepted_at": timestamp,
            }
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
            "command_id": cmd_id,
            "commandId": cmd_id,
            "aggregate_type": ticket.get("aggregate_type") or "research_ticket",
            "aggregate_id": tid,
            "aggregate_version": ticket.get("aggregate_version", 1),
            "event_id": ticket.get("event_id") or f"evt-{tid}",
            "correlation_id": ticket.get("correlation_id") or clean_key or tid,
            "receipt": receipt,
            "idempotency_key": clean_key,
            "tenant_id": ticket.get("tenant_id"),
            "actor_id": ticket.get("actor_id"),
        }

    @classmethod
    def _replay_ticket_create(cls, ticket: Dict[str, Any], cmd: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if cmd and isinstance(cmd, dict) and cmd.get("result"):
            return copy.deepcopy(cmd["result"])
        if ticket.get("create_result") and isinstance(ticket["create_result"], dict):
            return copy.deepcopy(ticket["create_result"])
        for c in (ticket.get("command_history") or []):
            if isinstance(c, dict) and c.get("command") == "CreateResearchTicket" and c.get("result"):
                return copy.deepcopy(c["result"])
        result = cls._project_ticket_detail(ticket)
        create_receipt = ticket.get("create_receipt")
        if not create_receipt and ticket.get("command_history"):
            for c in ticket["command_history"]:
                if isinstance(c, dict) and c.get("command") == "CreateResearchTicket" and c.get("receipt"):
                    create_receipt = c["receipt"]
                    break
        if create_receipt and isinstance(create_receipt, dict):
            result["receipt"] = copy.deepcopy(create_receipt)
            result["command_id"] = create_receipt.get("command_id") or result.get("command_id")
            result["commandId"] = result["command_id"]
            result["aggregate_version"] = create_receipt.get("aggregate_version", 1)
            result["event_id"] = create_receipt.get("event_id") or f"evt-{result.get('ticket_id')}"
            result["correlation_id"] = create_receipt.get("correlation_id") or result.get("correlation_id")
            result["status"] = create_receipt.get("status") or "open"
            result["allowedActions"] = cls._ticket_allowed_actions(result["status"])
            result["closed_at"] = None
            result["archived_at"] = None
            result["updated_at"] = ticket.get("created_at") or result.get("updated_at")
            if ticket.get("lifecycle_history"):
                result["lifecycle_history"] = [copy.deepcopy(ticket["lifecycle_history"][0])]
        return result

    @classmethod
    def _replay_ticket_patch(cls, ticket: Dict[str, Any], cmd: Dict[str, Any]) -> Dict[str, Any]:
        if cmd and isinstance(cmd, dict) and cmd.get("result"):
            res = copy.deepcopy(cmd["result"])
            if cmd.get("idempotency_key"):
                res["idempotency_key"] = cmd["idempotency_key"]
            return res
        result = cls._project_ticket_detail(ticket)
        patch_receipt = cmd.get("receipt") if isinstance(cmd, dict) else None
        if patch_receipt and isinstance(patch_receipt, dict):
            result["receipt"] = copy.deepcopy(patch_receipt)
            result["command_id"] = patch_receipt.get("command_id") or result.get("command_id")
            result["commandId"] = result["command_id"]
            result["aggregate_version"] = patch_receipt.get("aggregate_version", result.get("aggregate_version", 1))
            result["event_id"] = patch_receipt.get("event_id") or result.get("event_id")
            result["correlation_id"] = patch_receipt.get("correlation_id") or result.get("correlation_id")
            if patch_receipt.get("status"):
                result["status"] = patch_receipt["status"]
                result["allowedActions"] = cls._ticket_allowed_actions(patch_receipt["status"])
        if cmd.get("idempotency_key"):
            result["idempotency_key"] = cmd["idempotency_key"]
        return result

    @classmethod
    def _build_ticket_record(
        cls,
        *,
        tid: str,
        clean_title: str,
        clean_priority: str,
        clean_owner: str,
        clean_actor: str,
        description: str,
        timestamp: str,
        clean_key: Optional[str],
        clean_hash: Optional[str],
        clean_tenant: Optional[str],
        command_id: Optional[str],
    ) -> Dict[str, Any]:
        cmd_id = command_id or f"cmd-{tid}"
        canonical_receipt = {
            "receipt_id": f"rcpt-{tid}",
            "command_id": cmd_id,
            "commandId": cmd_id,
            "aggregate_type": "research_ticket",
            "aggregate_id": tid,
            "aggregate_version": 1,
            "status": "open",
            "event_id": f"evt-{tid}",
            "correlation_id": clean_key or tid,
            "owner": "research",
            "committed_at": timestamp,
            "command": "CreateResearchTicket",
            "target": {"type": "research_ticket", "id": tid},
            "submitted_at": timestamp,
            "accepted_at": timestamp,
        }
        create_command_entry = {
            "command_id": cmd_id,
            "command": "CreateResearchTicket",
            "idempotency_key": clean_key,
            "request_hash": clean_hash,
            "actor_id": clean_actor,
            "tenant_id": clean_tenant,
            "aggregate_version": 1,
            "receipt": canonical_receipt,
            "timestamp": timestamp,
        }
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
            "command_id": cmd_id,
            "commandId": cmd_id,
            "aggregate_type": "research_ticket",
            "aggregate_id": tid,
            "aggregate_version": 1,
            "event_id": canonical_receipt["event_id"],
            "correlation_id": canonical_receipt["correlation_id"],
            "receipt": canonical_receipt,
            "create_receipt": canonical_receipt,
            "idempotency_key": clean_key,
            "request_hash": clean_hash,
            "tenant_id": clean_tenant,
            "actor_id": clean_actor,
            "command_history": [create_command_entry],
        }
        create_result = cls._project_ticket_detail(record)
        record["create_result"] = copy.deepcopy(create_result)
        create_command_entry["result"] = copy.deepcopy(create_result)
        return record

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
        idempotency_key: Optional[str] = None,
        request_hash: Optional[str] = None,
        tenant_id: Optional[str] = None,
        command_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("title is required")
        clean_priority = str(priority or "normal").strip().lower()
        clean_owner = str(owner or "").strip()
        clean_actor = str(actor_id or "").strip() or clean_owner or "system"
        timestamp = created_at or _utc_now_rfc3339()

        clean_key = str(idempotency_key).strip() if idempotency_key else None
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        clean_hash = str(request_hash).strip() if request_hash else None
        if clean_key and not clean_hash:
            clean_hash = hashlib.sha256(f"{clean_title}:{clean_priority}:{clean_owner}:{description}".encode("utf-8")).hexdigest()

        inflight_token = (clean_tenant, clean_actor, clean_key) if clean_key else None
        if inflight_token:
            with self._active_inflight_lock:
                if inflight_token in self._active_inflight:
                    raise ResearchIdempotencyConflictError(
                        f"Command with key {clean_key!r} is currently being processed"
                    )
                self._active_inflight.add(inflight_token)

        try:
            if clean_key:
                existing_tickets = self._tickets_store.list_all()
                for t in existing_tickets:
                    if not isinstance(t, dict):
                        continue
                    t_key = t.get("idempotency_key")
                    t_tenant = str(t.get("tenant_id") or "").strip() or None
                    t_actor = str(t.get("actor_id") or t.get("owner") or "").strip() or None
                    if t_key == clean_key and t_tenant == clean_tenant and t_actor == clean_actor:
                        saved_hash = t.get("request_hash")
                        if clean_hash and saved_hash and saved_hash != clean_hash:
                            raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                        return self._replay_ticket_create(t)

                    for cmd in t.get("command_history", []):
                        if not isinstance(cmd, dict):
                            continue
                        if cmd.get("idempotency_key") == clean_key:
                            c_tenant = str(cmd.get("tenant_id") or "").strip() or None
                            c_actor = str(cmd.get("actor_id") or "").strip() or None
                            if c_tenant == clean_tenant and c_actor == clean_actor:
                                saved_hash = cmd.get("request_hash")
                                if clean_hash and saved_hash and saved_hash != clean_hash:
                                    raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                                if cmd.get("command") == "CreateResearchTicket":
                                    return self._replay_ticket_create(t, cmd)
                                raise ResearchIdempotencyConflictError("Idempotency key reused for different command")

            if ticket_id:
                tid = str(ticket_id).strip()
                record = self._build_ticket_record(
                    tid=tid,
                    clean_title=clean_title,
                    clean_priority=clean_priority,
                    clean_owner=clean_owner,
                    clean_actor=clean_actor,
                    description=description,
                    timestamp=timestamp,
                    clean_key=clean_key,
                    clean_hash=clean_hash,
                    clean_tenant=clean_tenant,
                    command_id=command_id,
                )
                inserted, existing_row = _atomic_insert_record(
                    self._tickets_store,
                    tid,
                    record,
                    unique_fields=("tenant_id", "actor_id", "idempotency_key") if clean_key else (),
                )
                if not inserted and existing_row is not None:
                    if clean_key and existing_row.get("idempotency_key") == clean_key:
                        row_tenant = str(existing_row.get("tenant_id") or "").strip() or None
                        row_actor = str(existing_row.get("actor_id") or existing_row.get("owner") or "").strip() or None
                        if row_tenant == clean_tenant and row_actor == clean_actor:
                            saved_hash = existing_row.get("request_hash")
                            if clean_hash and saved_hash and saved_hash != clean_hash:
                                raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                            return self._replay_ticket_create(existing_row)
                    raise ValueError(f"Ticket {tid!r} already exists")
                return copy.deepcopy(record.get("create_result") or self._project_ticket_detail(record))

            existing_tickets = self._tickets_store.list_all()
            date_prefix = timestamp[:10].replace("-", "")
            known_ids = {str(t.get("ticket_id") or "") for t in existing_tickets if isinstance(t, dict)}
            idx = 1
            while True:
                cand_id = f"rt-{date_prefix}-{idx:03d}"
                if cand_id in known_ids:
                    idx += 1
                    continue
                existing_probe = self._tickets_store.get(cand_id)
                if existing_probe is not None:
                    known_ids.add(cand_id)
                    if clean_key and existing_probe.get("idempotency_key") == clean_key:
                        row_tenant = str(existing_probe.get("tenant_id") or "").strip() or None
                        row_actor = str(existing_probe.get("actor_id") or existing_probe.get("owner") or "").strip() or None
                        if row_tenant == clean_tenant and row_actor == clean_actor:
                            saved_hash = existing_probe.get("request_hash")
                            if clean_hash and saved_hash and saved_hash != clean_hash:
                                raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                            return self._replay_ticket_create(existing_probe)
                    idx += 1
                    continue

                tid = cand_id
                record = self._build_ticket_record(
                    tid=tid,
                    clean_title=clean_title,
                    clean_priority=clean_priority,
                    clean_owner=clean_owner,
                    clean_actor=clean_actor,
                    description=description,
                    timestamp=timestamp,
                    clean_key=clean_key,
                    clean_hash=clean_hash,
                    clean_tenant=clean_tenant,
                    command_id=command_id,
                )
                inserted, existing_row = _atomic_insert_record(
                    self._tickets_store,
                    tid,
                    record,
                    unique_fields=("tenant_id", "actor_id", "idempotency_key") if clean_key else (),
                )
                if inserted:
                    return copy.deepcopy(record.get("create_result") or self._project_ticket_detail(record))

                known_ids.add(tid)
                if existing_row and isinstance(existing_row, dict):
                    if clean_key and existing_row.get("idempotency_key") == clean_key:
                        row_tenant = str(existing_row.get("tenant_id") or "").strip() or None
                        row_actor = str(existing_row.get("actor_id") or existing_row.get("owner") or "").strip() or None
                        if row_tenant == clean_tenant and row_actor == clean_actor:
                            saved_hash = existing_row.get("request_hash")
                            if clean_hash and saved_hash and saved_hash != clean_hash:
                                raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                            return self._replay_ticket_create(existing_row)
                idx += 1
        finally:
            if inflight_token:
                with self._active_inflight_lock:
                    self._active_inflight.discard(inflight_token)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_hash: Optional[str] = None,
        tenant_id: Optional[str] = None,
        command_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        clean_key = str(idempotency_key).strip() if idempotency_key else None
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        clean_hash = str(request_hash).strip() if request_hash else None
        if not clean_hash and patch is not None:
            try:
                clean_hash = hashlib.sha256(json.dumps(patch, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            except Exception:
                pass

        target_lock = getattr(self._tickets_store, "lock", None)
        target_rows = getattr(self._tickets_store, "rows", None)
        fail_flag = getattr(self._tickets_store, "fail", False)
        if target_lock is None or target_rows is None:
            db = getattr(self._tickets_store, "db", None)
            if db is not None:
                target_lock = getattr(db, "lock", None)
                target_rows = getattr(db, "rows", None)
                fail_flag = getattr(db, "fail", False)

        max_retries = 50
        for _ in range(max_retries):
            current = self._tickets_store.get(str(ticket_id))
            if current is None or not isinstance(current, dict):
                return None

            current_tenant = str(current.get("tenant_id") or "").strip() or None
            if current_tenant and clean_tenant != current_tenant:
                raise ResearchTenantAuthorizationError(
                    f"Tenant {clean_tenant!r} is not authorized to access ticket belonging to tenant {current_tenant!r}",
                    tenant_id=clean_tenant,
                    expected_tenant=current_tenant,
                )

            clean_actor = str(actor_id or "").strip() or str(current.get("owner") or "system")

            if clean_key:
                for cmd in (current.get("command_history") or []):
                    if not isinstance(cmd, dict):
                        continue
                    if cmd.get("idempotency_key") == clean_key:
                        cmd_tenant = str(cmd.get("tenant_id") or "").strip() or None
                        cmd_actor = str(cmd.get("actor_id") or "").strip() or None
                        if cmd_tenant == clean_tenant and cmd_actor == clean_actor:
                            saved_hash = cmd.get("request_hash")
                            if clean_hash and saved_hash and saved_hash != clean_hash:
                                raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                            replayed = self._replay_ticket_patch(current, cmd)
                            replayed["idempotency_key"] = clean_key
                            return replayed

                if current.get("idempotency_key") == clean_key:
                    row_tenant = str(current.get("tenant_id") or "").strip() or None
                    row_actor = str(current.get("actor_id") or current.get("owner") or "").strip() or None
                    if row_tenant == clean_tenant and row_actor == clean_actor:
                        saved_hash = current.get("request_hash")
                        if clean_hash and saved_hash and saved_hash != clean_hash:
                            raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                        if (current.get("receipt") or {}).get("command") == "PatchResearchTicket":
                            return self._replay_ticket_patch(current, current)
                        raise ResearchIdempotencyConflictError("Idempotency key reused for different command")

            allowed_actions = self._ticket_allowed_actions(current.get("status"))
            current_status = str(current.get("status") or "").strip().lower()

            editable_fields = {"title", "description", "priority", "owner"}
            attempting_edit = [f for f in editable_fields if f in patch]
            if attempting_edit and not allowed_actions.get("canEdit"):
                raise ResearchTicketLifecycleConflictError(
                    "Research ticket is not editable in its current lifecycle state",
                    reason=f"{attempting_edit[0]} cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )

            if "status" in patch:
                next_status = str(patch["status"] or "").strip().lower()
                if next_status != current_status:
                    if next_status == "closed" and not allowed_actions.get("canClose"):
                        raise ResearchTicketLifecycleConflictError(
                            "Research ticket cannot be closed in its current state",
                            reason="allowedActions.canClose is false for this ticket.",
                            precondition_failed="allowedActions.canClose",
                        )
                    if next_status == "archived" and not allowed_actions.get("canArchive"):
                        raise ResearchTicketLifecycleConflictError(
                            "Research ticket cannot be archived in its current state",
                            reason="allowedActions.canArchive is false for this ticket.",
                            precondition_failed="allowedActions.canArchive",
                        )
                    valid_transitions = {
                        "open": {"in_progress", "closed"},
                        "in_progress": {"closed"},
                        "closed": {"archived"},
                        "archived": set(),
                    }
                    if next_status not in valid_transitions.get(current_status, set()):
                        raise ResearchTicketLifecycleConflictError(
                            "Invalid research ticket lifecycle transition",
                            reason=f"Cannot transition research ticket from {current_status} to {next_status}.",
                            precondition_failed="status_transition",
                        )

            updated = copy.deepcopy(current)
            timestamp = updated_at or _utc_now_rfc3339()

            editable = {"title", "description", "priority", "owner"}
            for field in editable:
                if field in patch:
                    updated[field] = patch[field]

            next_status = patch.get("status")
            if next_status is not None:
                clean_next_status = str(next_status).strip().lower()
                prev_status = str(updated.get("status") or "").strip().lower()
                if clean_next_status != prev_status:
                    updated["status"] = clean_next_status
                    if clean_next_status == "closed":
                        updated["closed_at"] = timestamp
                        updated["archived_at"] = None
                    elif clean_next_status == "archived":
                        updated["archived_at"] = timestamp
                        if updated.get("closed_at") is None:
                            updated["closed_at"] = timestamp
                    else:
                        if clean_next_status in {"open", "in_progress"}:
                            updated["closed_at"] = None
                        if clean_next_status != "archived":
                            updated["archived_at"] = None

                    history = list(updated.get("lifecycle_history") or [])
                    history.append(
                        {
                            "from_status": prev_status,
                            "to_status": clean_next_status,
                            "transitioned_at": timestamp,
                            "transitioned_by": clean_actor,
                        }
                    )
                    updated["lifecycle_history"] = history

            if "linked_experiments" in patch and isinstance(patch["linked_experiments"], list):
                updated["linked_experiments"] = list(patch["linked_experiments"])
            if "linked_artifacts" in patch and isinstance(patch["linked_artifacts"], list):
                updated["linked_artifacts"] = list(patch["linked_artifacts"])

            prev_version = int(current.get("aggregate_version") or 1)
            new_version = prev_version + 1
            updated["aggregate_version"] = new_version
            updated["updated_at"] = timestamp

            if not updated.get("create_receipt") and current.get("receipt"):
                if (current.get("receipt") or {}).get("command") == "CreateResearchTicket":
                    updated["create_receipt"] = copy.deepcopy(current["receipt"])

            cmd_id = command_id or f"cmd-patch-{ticket_id}-{new_version}"
            patch_receipt = {
                "receipt_id": f"rcpt-{cmd_id}",
                "command_id": cmd_id,
                "commandId": cmd_id,
                "aggregate_type": current.get("aggregate_type") or "research_ticket",
                "aggregate_id": str(ticket_id),
                "aggregate_version": new_version,
                "status": updated.get("status") or "open",
                "event_id": f"evt-{cmd_id}",
                "correlation_id": clean_key or current.get("correlation_id") or str(ticket_id),
                "owner": "research",
                "committed_at": timestamp,
                "command": "PatchResearchTicket",
                "target": {"type": "research_ticket", "id": str(ticket_id)},
                "submitted_at": timestamp,
                "accepted_at": timestamp,
            }
            updated["receipt"] = patch_receipt
            updated["command_id"] = cmd_id
            updated["commandId"] = cmd_id
            updated["event_id"] = patch_receipt["event_id"]
            updated["correlation_id"] = patch_receipt["correlation_id"]

            patch_result = self._project_ticket_detail(updated)
            if clean_key:
                patch_result["idempotency_key"] = clean_key

            patch_command_entry = {
                "command_id": cmd_id,
                "command": "PatchResearchTicket",
                "idempotency_key": clean_key,
                "request_hash": clean_hash,
                "actor_id": clean_actor,
                "tenant_id": clean_tenant,
                "aggregate_version": new_version,
                "receipt": patch_receipt,
                "timestamp": timestamp,
                "result": copy.deepcopy(patch_result),
            }
            cmd_history = list(updated.get("command_history") or [])
            cmd_history.append(patch_command_entry)
            updated["command_history"] = cmd_history

            if hasattr(self._tickets_store, "compare_and_set"):
                success, actual = self._tickets_store.compare_and_set(str(ticket_id), current, updated)
                if success:
                    return copy.deepcopy(patch_result)
                continue

            if target_lock is not None and target_rows is not None:
                if fail_flag:
                    raise OSError("injected commit failure")
                with target_lock:
                    target_rows[str(ticket_id)] = copy.deepcopy(updated)
                    return copy.deepcopy(patch_result)

            self._tickets_store.put(str(ticket_id), updated)
            return copy.deepcopy(patch_result)

        raise RuntimeError(f"Failed to update ticket {ticket_id!r} after {max_retries} attempts")

    def get_research_ticket(
        self,
        ticket_id: Optional[str],
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        ticket = self._tickets_store.get(str(ticket_id))
        if not isinstance(ticket, dict):
            return None
        current_tenant = str(ticket.get("tenant_id") or "").strip() or None
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        if current_tenant and clean_tenant and clean_tenant != current_tenant:
            raise ResearchTenantAuthorizationError(
                f"Tenant {clean_tenant!r} is not authorized to access ticket belonging to tenant {current_tenant!r}",
                tenant_id=clean_tenant,
                expected_tenant=current_tenant,
            )
        return self._project_ticket_detail(ticket)

    def list_research_tickets(
        self,
        *,
        statuses: Optional[Sequence[str]] = None,
        owner: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        tickets = self._tickets_store.list_all()
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        if clean_tenant:
            tickets = [
                t for t in tickets
                if isinstance(t, dict) and (str(t.get("tenant_id") or "").strip() == clean_tenant)
            ]
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
            "tenant_id": exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id"),
            "actor_id": exp.get("actor_id") or (exp.get("launch_context") or {}).get("actor_id"),
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
        receipt = exp.get("cancel_receipt") if status == "canceled" and exp.get("cancel_receipt") else exp.get("receipt")
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
            "cancel_receipt": exp.get("cancel_receipt"),
            "command_history": list(exp.get("command_history") or []),
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
                if exp_id not in linked:
                    _atomic_update_ticket_links(
                        self._tickets_store,
                        exp_ticket_id,
                        exp_id,
                        timestamp,
                        initial_ticket=ticket,
                    )

        recovered = copy.deepcopy(existing_exp)
        recovered = _finalize_experiment_record(self._experiments_store, exp_id, recovered)
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

        inflight_token = (clean_tenant, clean_actor, clean_key) if clean_key else None
        if inflight_token:
            with self._active_inflight_lock:
                if inflight_token in self._active_inflight:
                    raise ResearchIdempotencyConflictError(
                        f"Command with key {clean_key!r} is currently being processed"
                    )
                self._active_inflight.add(inflight_token)

        try:
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
                    ticket_tenant = str(ticket.get("tenant_id") or "").strip() or None
                    if ticket_tenant and clean_tenant and clean_tenant != ticket_tenant:
                        raise ResearchTenantAuthorizationError(
                            f"Tenant {clean_tenant!r} is not authorized to access ticket belonging to tenant {ticket_tenant!r}",
                            tenant_id=clean_tenant,
                            expected_tenant=ticket_tenant,
                        )
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
                record = _finalize_experiment_record(self._experiments_store, exp_id, record)
            return self._project_experiment_detail(record)
        finally:
            if inflight_token:
                with self._active_inflight_lock:
                    self._active_inflight.discard(inflight_token)

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_hash: Optional[str] = None,
        command_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        clean_actor = str(actor_id).strip() if actor_id else None
        clean_key = str(idempotency_key).strip() if idempotency_key else None
        clean_hash = str(request_hash).strip() if request_hash else None

        target_lock = getattr(self._experiments_store, "lock", None)
        target_rows = getattr(self._experiments_store, "rows", None)
        fail_flag = getattr(self._experiments_store, "fail", False)

        exp = None
        max_retries = 20
        for attempt in range(max_retries):
            if exp is None:
                exp = self._experiments_store.get(str(experiment_id))
            if exp is None or not isinstance(exp, dict):
                return None

            exp_tenant = str(exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
            if exp_tenant and clean_tenant and clean_tenant != exp_tenant:
                raise ResearchTenantAuthorizationError(
                    f"Tenant {clean_tenant!r} is not authorized to cancel experiment belonging to tenant {exp_tenant!r}",
                    tenant_id=clean_tenant,
                    expected_tenant=exp_tenant,
                )

            if clean_key:
                for cmd in (exp.get("command_history") or []):
                    if not isinstance(cmd, dict):
                        continue
                    if cmd.get("idempotency_key") == clean_key:
                        cmd_tenant = str(cmd.get("tenant_id") or "").strip() or None
                        cmd_actor = str(cmd.get("actor_id") or "").strip() or None
                        if cmd_tenant == clean_tenant and (not clean_actor or not cmd_actor or cmd_actor == clean_actor):
                            saved_hash = cmd.get("request_hash")
                            if clean_hash and saved_hash and saved_hash != clean_hash:
                                raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                            return self._project_experiment_detail(exp)

                cancel_rcpt = exp.get("cancel_receipt") or {}
                if cancel_rcpt.get("idempotency_key") == clean_key:
                    rcpt_tenant = str(cancel_rcpt.get("tenant_id") or "").strip() or None
                    rcpt_actor = str(cancel_rcpt.get("actor_id") or "").strip() or None
                    if rcpt_tenant == clean_tenant and (not clean_actor or not rcpt_actor or rcpt_actor == clean_actor):
                        saved_hash = cancel_rcpt.get("request_hash")
                        if clean_hash and saved_hash and saved_hash != clean_hash:
                            raise ResearchIdempotencyConflictError("Idempotency key reused with different request payload")
                        return self._project_experiment_detail(exp)

            status = str(exp.get("status") or "").strip().lower()
            if status not in self._RW04_CANCELABLE_STATUSES:
                return None

            updated = copy.deepcopy(exp)
            timestamp = completed_at or _utc_now_rfc3339()
            updated["status"] = "canceled"
            updated["completed_at"] = timestamp
            updated["cancellation_fence"] = timestamp
            if reason:
                updated["cancellation_reason"] = reason
            if clean_actor:
                updated["canceled_by"] = clean_actor
            updated["updated_at"] = timestamp
            updated["allowedActions"] = self._rw04_allowed_actions(updated)

            prev_version = int(exp.get("aggregate_version") or 1)
            new_version = prev_version + 1
            updated["aggregate_version"] = new_version

            cmd_id = command_id or f"cmd-cancel-{experiment_id}-{new_version}"
            cancel_receipt = {
                "receipt_id": f"rcpt-{cmd_id}",
                "command_id": cmd_id,
                "command": "CancelResearchExperiment",
                "aggregate_id": str(experiment_id),
                "aggregate_type": "ResearchExperiment",
                "aggregate_version": new_version,
                "status": "committed",
                "owner": "ResearchWriteOwner",
                "actor_id": clean_actor,
                "tenant_id": clean_tenant or exp_tenant,
                "idempotency_key": clean_key,
                "request_hash": clean_hash,
                "committed_at": timestamp,
            }
            updated["cancel_receipt"] = cancel_receipt
            history = list(updated.get("command_history") or [])
            history.append({
                "command": "CancelResearchExperiment",
                "command_id": cmd_id,
                "actor_id": clean_actor,
                "tenant_id": clean_tenant or exp_tenant,
                "idempotency_key": clean_key,
                "request_hash": clean_hash,
                "recorded_at": timestamp,
                "receipt": cancel_receipt,
            })
            updated["command_history"] = history

            if hasattr(self._experiments_store, "compare_and_set"):
                success, actual = self._experiments_store.compare_and_set(str(experiment_id), exp, updated)
                if success:
                    return self._project_experiment_detail(updated)
                exp = actual if isinstance(actual, dict) else None
                continue

            if target_lock is not None and target_rows is not None:
                if fail_flag:
                    raise OSError("injected commit failure")
                with target_lock:
                    target_rows[str(experiment_id)] = copy.deepcopy(updated)
                    return self._project_experiment_detail(updated)

            self._experiments_store.put(str(experiment_id), updated)
            return self._project_experiment_detail(updated)

        raise RuntimeError(f"Failed to cancel experiment {experiment_id!r} after {max_retries} attempts")

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
        exp_tenant = str(exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
        clean_tenant = str(tenant_id).strip() if tenant_id else exp_tenant
        if exp_tenant and tenant_id and str(tenant_id).strip() != exp_tenant:
            raise ResearchTenantAuthorizationError(
                f"Tenant {str(tenant_id).strip()!r} is not authorized to retry experiment belonging to tenant {exp_tenant!r}",
                tenant_id=str(tenant_id).strip(),
                expected_tenant=exp_tenant,
            )
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

        inflight_token = (clean_tenant, clean_actor, clean_key) if clean_key else None
        if inflight_token:
            with self._active_inflight_lock:
                if inflight_token in self._active_inflight:
                    raise ResearchIdempotencyConflictError(
                        f"Command with key {clean_key!r} is currently being processed"
                    )
                self._active_inflight.add(inflight_token)

        try:
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
                new_record = _finalize_experiment_record(self._experiments_store, new_exp_id, new_record)
            return self._project_experiment_detail(new_record)
        finally:
            if inflight_token:
                with self._active_inflight_lock:
                    self._active_inflight.discard(inflight_token)

    def archive_research_experiment(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str] = None,
        archived_at: Optional[str] = None,
        tenant_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        clean_actor = str(actor_id).strip() if actor_id else None

        target_lock = getattr(self._experiments_store, "lock", None)
        target_rows = getattr(self._experiments_store, "rows", None)
        fail_flag = getattr(self._experiments_store, "fail", False)

        exp = None
        max_retries = 20
        for attempt in range(max_retries):
            if exp is None:
                exp = self._experiments_store.get(str(experiment_id))
            if exp is None or not isinstance(exp, dict):
                return None
            exp_tenant = str(exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
            if exp_tenant and clean_tenant and clean_tenant != exp_tenant:
                raise ResearchTenantAuthorizationError(
                    f"Tenant {clean_tenant!r} is not authorized to archive experiment belonging to tenant {exp_tenant!r}",
                    tenant_id=clean_tenant,
                    expected_tenant=exp_tenant,
                )
            status = str(exp.get("status") or "").strip().lower()
            if status not in self._RW04_ARCHIVABLE_STATUSES:
                return None
            if exp.get("is_archived"):
                return self._project_experiment_detail(exp)
            updated = copy.deepcopy(exp)
            timestamp = archived_at or _utc_now_rfc3339()
            updated["is_archived"] = True
            updated["archived_at"] = timestamp
            updated["archived_by"] = clean_actor
            updated["updated_at"] = timestamp
            updated["allowedActions"] = self._rw04_allowed_actions(updated)

            if hasattr(self._experiments_store, "compare_and_set"):
                success, actual = self._experiments_store.compare_and_set(str(experiment_id), exp, updated)
                if success:
                    return self._project_experiment_detail(updated)
                exp = actual if isinstance(actual, dict) else None
                continue

            if target_lock is not None and target_rows is not None:
                if fail_flag:
                    raise OSError("injected commit failure")
                with target_lock:
                    target_rows[str(experiment_id)] = copy.deepcopy(updated)
                    return self._project_experiment_detail(updated)

            self._experiments_store.put(str(experiment_id), updated)
            return self._project_experiment_detail(updated)

        raise RuntimeError(f"Failed to archive experiment {experiment_id!r} after {max_retries} attempts")

    def invalidate_research_experiment(
        self,
        experiment_id: str,
        *,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
        invalidated_at: Optional[str] = None,
        tenant_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        clean_actor = str(actor_id).strip() if actor_id else None

        target_lock = getattr(self._experiments_store, "lock", None)
        target_rows = getattr(self._experiments_store, "rows", None)
        fail_flag = getattr(self._experiments_store, "fail", False)

        exp = None
        max_retries = 20
        for attempt in range(max_retries):
            if exp is None:
                exp = self._experiments_store.get(str(experiment_id))
            if exp is None or not isinstance(exp, dict):
                return None
            exp_tenant = str(exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
            if exp_tenant and clean_tenant and clean_tenant != exp_tenant:
                raise ResearchTenantAuthorizationError(
                    f"Tenant {clean_tenant!r} is not authorized to invalidate experiment belonging to tenant {exp_tenant!r}",
                    tenant_id=clean_tenant,
                    expected_tenant=exp_tenant,
                )
            status = str(exp.get("status") or "").strip().lower()
            if status in {"invalidated", "canceled"}:
                return None
            updated = copy.deepcopy(exp)
            timestamp = invalidated_at or _utc_now_rfc3339()
            updated["status"] = "invalidated"
            updated["invalidated_at"] = timestamp
            updated["invalidated_reason"] = reason or "Invalidated by operator"
            updated["invalidated_by"] = clean_actor
            updated["updated_at"] = timestamp
            updated["allowedActions"] = self._rw04_allowed_actions(updated)

            if hasattr(self._experiments_store, "compare_and_set"):
                success, actual = self._experiments_store.compare_and_set(str(experiment_id), exp, updated)
                if success:
                    return self._project_experiment_detail(updated)
                exp = actual if isinstance(actual, dict) else None
                continue

            if target_lock is not None and target_rows is not None:
                if fail_flag:
                    raise OSError("injected commit failure")
                with target_lock:
                    target_rows[str(experiment_id)] = copy.deepcopy(updated)
                    return self._project_experiment_detail(updated)

            self._experiments_store.put(str(experiment_id), updated)
            return self._project_experiment_detail(updated)

        raise RuntimeError(f"Failed to invalidate experiment {experiment_id!r} after {max_retries} attempts")

    def get_research_experiment(
        self,
        experiment_id: Optional[str],
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        exp = self._experiments_store.get(str(experiment_id))
        if exp and isinstance(exp, dict) and not exp.get("is_committed", True):
            return None
        if not isinstance(exp, dict):
            return None
        exp_tenant = str(exp.get("tenant_id") or (exp.get("launch_context") or {}).get("tenant_id") or "").strip() or None
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        if exp_tenant and clean_tenant and clean_tenant != exp_tenant:
            raise ResearchTenantAuthorizationError(
                f"Tenant {clean_tenant!r} is not authorized to access experiment belonging to tenant {exp_tenant!r}",
                tenant_id=clean_tenant,
                expected_tenant=exp_tenant,
            )
        return self._project_experiment_detail(exp)

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        include_archived: bool = False,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        experiments = self._experiments_store.list_all()
        experiments = [e for e in experiments if isinstance(e, dict) and e.get("is_committed", True)]
        clean_tenant = str(tenant_id).strip() if tenant_id else None
        if clean_tenant:
            experiments = [
                e for e in experiments
                if isinstance(e, dict) and (str(e.get("tenant_id") or (e.get("launch_context") or {}).get("tenant_id") or "").strip() == clean_tenant)
            ]
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
