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

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


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


class MeetingActionError(ValueError):
    """Owner refusal with an HTTP-safe reason; never a fallback to legacy writes."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        action_evidence_reader: Optional[Any] = None,
    ) -> None:
        self._action_evidence_reader = action_evidence_reader
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
            table=tickets_table or ("research.research_tickets" if self.schema == "research" else f"{self.schema}.research_tickets"),
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
            table=notes_table or ("research.research_notes" if self.schema == "research" else f"{self.schema}.research_notes"),
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
            **cls._action_projection(ticket),
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
            **cls._action_projection(ticket),
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
        meeting_action: Optional[Dict[str, Any]] = None,
        context: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if meeting_action is not None:
            return self._create_meeting_ticket(title=title, description=description, priority=priority,
                       owner=owner, actor_id=actor_id, meeting_action=meeting_action,
                       context=context, ticket_id=ticket_id)
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("title is required")
        clean_priority = str(priority or "medium").strip().lower()
        clean_owner = str(owner or "").strip()
        clean_actor = str(actor_id or "").strip() or clean_owner or "system"
        timestamp = created_at or _utc_now_rfc3339()

        if ticket_id:
            tid = str(ticket_id).strip()
            if tid.startswith("rt-action-"):
                self._action_error("Reserved meeting ticket namespace", 400)
        else:
            existing_tickets = self._tickets_store.list_all()
            date_prefix = timestamp[:10].replace("-", "")
            idx = len(existing_tickets) + 1
            tid = f"rt-{date_prefix}-{idx:03d}"
            existing_ids = {str(t.get("ticket_id") or "") for t in existing_tickets}
            while tid in existing_ids:
                idx += 1
                tid = f"rt-{date_prefix}-{idx:03d}"

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
        existing = self._tickets_store.get(tid)
        if existing and existing.get("meeting_action"):
            self._action_error("Meeting work cannot be replaced by an ordinary ticket")
        # Ordinary RW-01 callers retain their existing get/put store contract.
        # Meeting assignments use their reserved namespace and atomic path above.
        self._tickets_store.put(tid, record)
        return self._project_ticket_detail(record)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        ticket = self._tickets_store.get(str(ticket_id))
        if ticket is None or not isinstance(ticket, dict):
            return None

        if ticket.get("meeting_action"):
            return self._patch_meeting_ticket(ticket, patch=patch, actor_id=actor_id, context=context)
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

    def get_research_ticket(self, ticket_id: Optional[str], *, context: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        ticket = self._tickets_store.get(str(ticket_id))
        if isinstance(ticket, dict) and ticket.get("meeting_action") and not self._action_visible(ticket, context):
            self._action_auth(context)
            return None
        return self._project_ticket_detail(ticket) if isinstance(ticket, dict) else None

    def list_research_tickets(
        self,
        *,
        statuses: Optional[Sequence[str]] = None,
        owner: Optional[str] = None,
        context: Optional[Any] = None,
        meeting_linked: bool = False,
        meeting_type: Optional[str] = None,
        subject_ref: Optional[str] = None,
        action_states: Optional[Sequence[str]] = None,
        due_before: Optional[str] = None,
        submitted_after: Optional[str] = None,
        updated_after: Optional[str] = None,
        human_actionable: bool = False,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Any:
        if meeting_linked:
            return self._list_meeting_tickets(context=context, statuses=statuses, owner=owner,
                meeting_type=meeting_type, subject_ref=subject_ref, action_states=action_states,
                due_before=due_before, submitted_after=submitted_after, updated_after=updated_after,
                human_actionable=human_actionable, page_size=page_size, page_token=page_token)
        if human_actionable:
            self._action_error("Meeting filters require meeting_linked=true", 400)
        tickets = [t for t in self._tickets_store.list_all() if not t.get("meeting_action")]
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

    # Meeting obligations remain rows in the existing RW-01 owner table.
    _ACTION_MAX_BYTES = 262144
    _ACTION_HISTORY_MAX = 256
    _ACTION_PER_MEETING_MAX = 8
    _ACTION_NOTES_PER_TICKET_MAX = 128
    _ACTION_INPUT_MAX_BYTES = 16384
    _ACTION_CONTROLLERS = frozenset({"consultation-svc", "persona-pool-cycle"})
    _ACTION_STATES = {
        "start": ({"open", "returned"}, "in_progress"),
        "request_input": ({"open", "in_progress", "returned"}, "waiting_for_input"),
        "provide_input": ({"waiting_for_input"}, "in_progress"),
        "submit": ({"open", "in_progress", "returned"}, "submitted"),
        "return": ({"submitted"}, "returned"),
        "accept": ({"submitted"}, "accepted"),
    }

    @staticmethod
    def _action_error(message: str, status_code: int = 409) -> None:
        raise MeetingActionError(message, status_code)

    @classmethod
    def _action_text(cls, value: Any, field: str, limit: int = 2000) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            cls._action_error(f"{field} must be nonempty text (max {limit} chars)", 400)
        return value.strip()

    @staticmethod
    def _action_digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    @classmethod
    def _action_auth(cls, context: Any, actor_id: Optional[str] = None) -> Any:
        if context is None or not context.tenant_id or not context.actor_id:
            cls._action_error("Authenticated tenant and actor required", 401)
        if actor_id is not None and actor_id != context.actor_id:
            cls._action_error("actor_id must match authenticated principal", 403)
        return context

    @staticmethod
    def _action_operator(context: Any) -> bool:
        return bool(context.roles & {"operator", "admin"})

    @classmethod
    def _action_controller(cls, context: Any, actor: str) -> bool:
        return context.actor_id == actor and bool(context.roles & {"service", "viewer"})

    @classmethod
    def _action_visible(cls, ticket: Dict[str, Any], context: Any) -> bool:
        if context is None or ticket.get("tenant_id") != context.tenant_id:
            return False
        return (cls._action_operator(context)
                or any(cls._action_controller(context, actor) for actor in cls._ACTION_CONTROLLERS)
                or context.actor_id in {ticket.get("owner"), ticket.get("reviewer_id"), ticket.get("required_actor")})

    @classmethod
    def _action_projection(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        # Return only owner-written action fields; preserve old RW-01 projections.
        if not ticket.get("meeting_action"):
            return {}
        keys = ("tenant_id", "meeting_action", "action_state", "revision", "evidence_refs",
                "submitted_at", "reviewed_at", "reviewer_id", "required_actor", "input_request",
                "human_input", "review_decision")
        out = {k: json.loads(json.dumps(ticket.get(k))) for k in keys}
        out.update(action_id=ticket["ticket_id"], assignee=ticket["owner"],
                   assignee_kind=ticket["meeting_action"]["assignee_kind"])
        out["allowedActions"] = {"canEdit": False, "canClose": False, "canArchive": False}
        return out

    def _bounded_action_insert(self, store: Any, record_id: str, payload: Dict[str, Any], *,
                               group_sql: str, group_values: Sequence[str], cap: int) -> Any:
        # One transaction-scoped group lock serializes new origins/results across
        # processes. Exact retries remain readable even after the count cap.
        with store.transaction() as conn:
            lock = "meeting-action:" + store.table_name + ":" + self._action_digest(list(group_values))
            store.advisory_xact_lock(lock, conn=conn)
            row = conn.execute(f"SELECT payload FROM {store.table} WHERE record_id = %s", (record_id,)).fetchone()
            if row:
                return False, store._decode_payload(row[0])
            count = conn.execute(f"SELECT count(*) FROM {store.table} WHERE {group_sql}", tuple(group_values)).fetchone()[0]
            if count >= cap:
                self._action_error("Meeting action/evidence count capacity reached; existing obligations preserved")
            return store.compare_and_set(record_id, None, payload, conn=conn)

    def _create_meeting_ticket(self, *, title: str, description: str, priority: str,
                               owner: str, actor_id: str, meeting_action: Dict[str, Any],
                               context: Any, ticket_id: Optional[str]) -> Dict[str, Any]:
        ctx = self._action_auth(context, actor_id)
        if not (self._action_operator(ctx) or (ctx.actor_id == "consultation-svc" and "service" in ctx.roles)):
            self._action_error("Only meeting owner or operator can assign meeting work", 403)
        if not isinstance(meeting_action, dict):
            self._action_error("meeting_action must be an object", 400)
        required = {"meeting_id", "source_action_id", "meeting_type", "subject_ref", "assignee_kind",
                    "deliverable", "acceptance_criterion", "reviewer_id"}
        if set(meeting_action) - required - {"due_at", "next_review", "source_refs"}:
            self._action_error("Unknown meeting_action fields", 400)
        action = {k: self._action_text(meeting_action.get(k), k, 2000 if k in
                  {"deliverable", "acceptance_criterion"} else 256) for k in required}
        if action["assignee_kind"] not in {"persona", "human"}:
            self._action_error("assignee_kind must be persona or human", 400)
        due = meeting_action.get("due_at")
        if due is not None:
            parsed = _parse_rfc3339(due)
            if parsed is None or parsed.tzinfo is None:
                self._action_error("due_at requires an RFC3339 timestamp with timezone", 400)
            action["due_at"] = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if meeting_action.get("next_review") is not None:
            action["next_review"] = self._action_text(meeting_action["next_review"], "next_review", 256)
        if not action.get("due_at") and not action.get("next_review"):
            self._action_error("due_at or explicitly identified next_review required", 400)
        refs = meeting_action.get("source_refs", [])
        if not isinstance(refs, list) or len(refs) > 8 or len(json.dumps(refs).encode()) > 4096:
            self._action_error("source_refs exceeds bounded reference budget", 400)
        action["source_refs"] = refs
        assignment = {"meeting_action": action, "tenant_id": ctx.tenant_id,
                      "title": self._action_text(title, "title", 256),
                      "description": str(description or ""), "priority": priority,
                      "owner": self._action_text(owner, "owner", 256)}
        if len(json.dumps(assignment).encode()) > self._ACTION_INPUT_MAX_BYTES:
            self._action_error("Assignment exceeds 16 KiB", 400)
        tid = "rt-action-" + self._action_digest([ctx.tenant_id, action["meeting_id"], action["source_action_id"]])[:32]
        if ticket_id is not None and ticket_id != tid:
            self._action_error("ticket_id must equal deterministic action identity", 400)
        timestamp = _utc_now_rfc3339()
        record = {**assignment, "ticket_id": tid, "status": "open", "action_state": "open",
                  "created_at": timestamp, "updated_at": timestamp, "closed_at": None,
                  "archived_at": None, "linked_experiments": [], "linked_artifacts": [],
                  "evidence_refs": [], "revision": 0, "reviewer_id": action["reviewer_id"],
                  "required_actor": assignment["owner"], "assignment_digest": self._action_digest(assignment),
                  "lifecycle_history": [{"from_status": None, "to_status": "open",
                      "transitioned_at": timestamp, "transitioned_by": actor_id}]}
        inserted, canonical = self._bounded_action_insert(self._tickets_store, tid, record,
            group_sql="payload->>'tenant_id' = %s AND payload->'meeting_action'->>'meeting_id' = %s",
            group_values=[ctx.tenant_id, action["meeting_id"]], cap=self._ACTION_PER_MEETING_MAX)
        if not inserted and (not canonical or canonical.get("assignment_digest") != record["assignment_digest"]):
            self._action_error("Conflicting replay of adopted action")
        return self._project_ticket_detail(canonical)

    def _resolve_action_evidence(self, refs: Any, ticket: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            self._action_error("1..8 evidence_refs required", 400)
        out = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"kind", "artifact_id"}:
                self._action_error("Evidence requires only kind and artifact_id", 400)
            if not isinstance(ref["artifact_id"], str) or len(ref["artifact_id"]) > 256:
                self._action_error("Invalid artifact_id", 400)
            # Readers are injected by the service, never by HTTP payloads.
            if self._action_evidence_reader is None:
                self._action_error("Evidence owner unavailable", 503)
            saved = self._action_evidence_reader(ref, ticket["tenant_id"], ticket["ticket_id"])
            if not isinstance(saved, dict) or saved.get("tenant_id") != ticket["tenant_id"] or not saved.get("checksum"):
                self._action_error("Evidence not found in authorized tenant scope", 403)
            out.append({**ref, "checksum": saved["checksum"], "tenant_id": ticket["tenant_id"]})
        if len(json.dumps(out).encode()) > 4096:
            self._action_error("Evidence metadata exceeds 4 KiB", 400)
        return out

    def _patch_meeting_ticket(self, ticket: Dict[str, Any], *, patch: Dict[str, Any],
                              actor_id: str, context: Any) -> Dict[str, Any]:
        ctx = self._action_auth(context, actor_id)
        if not self._action_visible(ticket, ctx):
            self._action_error("Research ticket not found", 404)
        if len(json.dumps(patch).encode()) > self._ACTION_INPUT_MAX_BYTES:
            self._action_error("Action command exceeds 16 KiB", 400)
        action = patch.get("action")
        if not isinstance(action, str) or action not in self._ACTION_STATES:
            self._action_error("Meeting work requires an explicit owner action; status PATCH is forbidden", 400)
        fields = {"action", "expected_revision", "idempotency_key"}
        fields |= {"request_input": {"reason", "human_actor_id"}, "provide_input": {"evidence_refs", "reason"},
                   "submit": {"evidence_refs"}, "return": {"reason", "review_source"},
                   "accept": {"reason", "review_source"}}.get(action, set())
        if set(patch) - fields:
            self._action_error("Unsupported meeting command fields", 400)
        key = self._action_text(patch.get("idempotency_key"), "idempotency_key", 256)
        digest = self._action_digest([actor_id, patch])
        # Retry is checked before revision/state and never creates a second history event.
        for event in ticket["lifecycle_history"]:
            if event.get("idempotency_key") == key and event.get("transitioned_by") == actor_id:
                if event.get("command_digest") != digest:
                    self._action_error("Conflicting command replay")
                return self._project_ticket_detail(ticket)
        if type(patch.get("expected_revision")) is not int or patch["expected_revision"] != ticket["revision"]:
            self._action_error("Stale expected_revision")
        prev = ticket["action_state"]
        allowed, target = self._ACTION_STATES[action]
        if prev not in allowed:
            self._action_error(f"Cannot {action} from {prev}")
        if action in {"return", "accept"}:
            authorized = self._action_operator(ctx) or (ctx.actor_id == ticket["reviewer_id"] and
                         bool(ctx.roles & {"reviewer", "governance_reviewer", "service"}))
        elif action == "provide_input":
            authorized = self._action_operator(ctx) or (ctx.actor_id == ticket["required_actor"] and
                         bool(ctx.roles & {"operator", "approver", "human"}))
        else:
            authorized = (ctx.actor_id == ticket["owner"] or self._action_operator(ctx)
                          or (ticket["meeting_action"]["assignee_kind"] == "persona"
                              and self._action_controller(ctx, "persona-pool-cycle")))
        if not authorized:
            self._action_error("Actor cannot perform this ticket action", 403)
        updated = json.loads(json.dumps(ticket))
        now = _utc_now_rfc3339()
        if action == "request_input":
            human = self._action_text(patch.get("human_actor_id"), "human_actor_id", 256)
            updated["input_request"] = {"reason": self._action_text(patch.get("reason"), "reason"),
                                        "human_actor_id": human, "requested_at": now}
            updated["required_actor"] = human
        elif action in {"submit", "provide_input"}:
            evidence = self._resolve_action_evidence(patch.get("evidence_refs"), ticket)
            if action == "submit":
                updated["evidence_refs"] = evidence
                updated["linked_artifacts"] = [r["artifact_id"] for r in evidence]
                updated["submitted_at"] = now
                updated["required_actor"] = ticket["reviewer_id"]
            else:
                updated["human_input"] = {"evidence_refs": evidence, "provided_by": actor_id, "provided_at": now}
                updated["required_actor"] = ticket["owner"]
        elif action in {"return", "accept"}:
            reason = self._action_text(patch.get("reason"), "reason")
            source = self._action_text(patch.get("review_source"), "review_source", 256)
            # Re-read the real scoped owner on acceptance; caller assertions do not suffice.
            if action == "accept":
                refs = [{"kind": r["kind"], "artifact_id": r["artifact_id"]} for r in ticket["evidence_refs"]]
                if self._resolve_action_evidence(refs, ticket) != ticket["evidence_refs"]:
                    self._action_error("Submitted evidence changed; corrected submission required")
                updated["closed_at"] = now
            updated["review_decision"] = {"decision": action, "reason": reason, "review_source": source,
                                           "reviewed_by": actor_id, "reviewed_at": now,
                                           "submission_revision": ticket["revision"]}
            updated["reviewed_at"] = now
            updated["required_actor"] = None if action == "accept" else ticket["owner"]
        else:
            updated["required_actor"] = ticket["owner"]
        updated.update(action_state=target, status="closed" if target == "accepted" else "in_progress",
                       updated_at=now, revision=ticket["revision"] + 1)
        event = {"from_status": ticket["status"], "to_status": updated["status"],
                 "from_action_state": prev, "to_action_state": target, "action": action,
                 "transitioned_at": now, "transitioned_by": actor_id, "accountable_owner": ticket["owner"],
                 "idempotency_key": key, "command_digest": digest, "revision": updated["revision"],
                 "evidence_refs": updated.get("evidence_refs"), "human_input": updated.get("human_input"),
                 "input_request": updated.get("input_request"), "review_decision": updated.get("review_decision")}
        updated["lifecycle_history"].append(event)
        if len(updated["lifecycle_history"]) > self._ACTION_HISTORY_MAX or len(json.dumps(updated).encode()) > self._ACTION_MAX_BYTES:
            self._action_error("Ticket history capacity reached; preserve obligation for owner intervention")
        ok, _ = self._tickets_store.compare_and_set(ticket["ticket_id"], ticket, updated)
        if not ok:
            # A simultaneous exact retry may have won. Re-read once; no stale overwrite.
            current = self._tickets_store.get(ticket["ticket_id"])
            for e in (current or {}).get("lifecycle_history", []):
                if e.get("transitioned_by") == actor_id and e.get("idempotency_key") == key and e.get("command_digest") == digest:
                    return self._project_ticket_detail(current)
            self._action_error("Concurrent ticket update; read current revision before retry")
        return self._project_ticket_detail(updated)

    def _list_meeting_tickets(self, *, context: Any, statuses: Any, owner: Any,
                             meeting_type: Any, subject_ref: Any, action_states: Any,
                             due_before: Any, submitted_after: Any, updated_after: Any,
                             human_actionable: bool, page_size: int, page_token: Optional[str]) -> Dict[str, Any]:
        ctx = self._action_auth(context)
        if not 1 <= page_size <= 100:
            self._action_error("page_size must be 1..100", 400)
        for name, value in (("due_before", due_before), ("submitted_after", submitted_after), ("updated_after", updated_after)):
            if value and (_parse_rfc3339(value) is None or _parse_rfc3339(value).tzinfo is None):
                self._action_error(f"Invalid {name}", 400)
        # Bound SQL result rows and bytes before crossing the database boundary.
        clauses = ["payload->>'tenant_id' = %s", "payload->'meeting_action' IS NOT NULL"]
        params: List[Any] = [ctx.tenant_id]
        if not (self._action_operator(ctx) or any(self._action_controller(ctx, a) for a in self._ACTION_CONTROLLERS)):
            clauses.append("(payload->>'owner' = %s OR payload->>'reviewer_id' = %s OR payload->>'required_actor' = %s)")
            params.extend([ctx.actor_id] * 3)
        if human_actionable:
            # Apply before keyset pagination/LIMIT so unrelated Persona work
            # cannot starve the human page. This never broadens visibility.
            clauses.append("(payload->>'action_state' = 'waiting_for_input' OR "
                           "(payload->'meeting_action'->>'assignee_kind' = 'human' AND "
                           "payload->>'action_state' IN ('open', 'in_progress', 'returned')))")
        for expr, val in (("payload->>'owner'", owner), ("payload->'meeting_action'->>'meeting_type'", meeting_type),
                          ("payload->'meeting_action'->>'subject_ref'", subject_ref)):
            if val:
                clauses.append(f"{expr} = %s")
                params.append(val)
        for field, values in (("status", statuses), ("action_state", action_states)):
            if values:
                clauses.append(f"payload->>'{field}' = ANY(%s)")
                params.append(list(values))
        for expr, op, value in (("payload->'meeting_action'->>'due_at'", "<=", due_before),
                                 ("payload->>'submitted_at'", ">=", submitted_after),
                                 ("payload->>'updated_at'", ">=", updated_after)):
            if value:
                clauses.append(f"({expr})::timestamptz {op} %s::timestamptz")
                params.append(value)
        scope = [ctx.tenant_id, ctx.actor_id, sorted(ctx.roles), statuses, owner,
                 meeting_type, subject_ref, action_states, due_before, submitted_after, updated_after]
        if human_actionable:
            scope.append("human_actionable")
        fingerprint = self._action_digest(scope)[:24]
        if page_token:
            if len(page_token) > 128:
                self._action_error("Invalid page_token", 400)
            try:
                token_scope, anchor = page_token.split(":", 1)
                if token_scope != fingerprint or not anchor.startswith("rt-action-") or len(anchor) != 42:
                    raise ValueError()
            except ValueError:
                self._action_error("Invalid or differently scoped page_token", 400)
            clauses.append("record_id > %s")
            params.append(anchor)
        params.append(page_size + 1)
        with self._tickets_store._connect() as conn:
            rows = conn.execute(f"SELECT payload FROM {self._tickets_store.table} WHERE {' AND '.join(clauses)} ORDER BY record_id ASC LIMIT %s", tuple(params)).fetchall()
        tickets = [self._tickets_store._decode_payload(r[0] if isinstance(r, tuple) else r["payload"]) for r in rows]
        items = [self._project_ticket_summary(t) for t in tickets[:page_size]]
        return {"data": items, "page_info": {"next_page_token": f"{fingerprint}:{tickets[page_size - 1]['ticket_id']}" if len(tickets) > page_size else None}}

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
        }

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
    ) -> Dict[str, Any]:
        clean_ticket_id = str(ticket_id or "").strip()
        clean_exp_name = str(experiment_name or "").strip()
        if not clean_exp_name:
            raise ValueError("experiment_name is required")
        timestamp = queued_at or _utc_now_rfc3339()

        if experiment_id:
            exp_id = str(experiment_id).strip()
        else:
            existing_experiments = self._experiments_store.list_all()
            date_prefix = timestamp[:10].replace("-", "")
            idx = len(existing_experiments) + 1
            exp_id = f"exp-{date_prefix}-{idx:03d}"
            existing_ids = {str(e.get("experiment_id") or "") for e in existing_experiments}
            while exp_id in existing_ids:
                idx += 1
                exp_id = f"exp-{date_prefix}-{idx:03d}"

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
        }

        if clean_ticket_id:
            ticket = self._tickets_store.get(clean_ticket_id)
            # Meeting work only changes through authenticated CAS actions.
            # Legacy experiment creation remains supported without touching its obligation.
            if ticket and isinstance(ticket, dict) and not ticket.get("meeting_action"):
                linked = list(ticket.get("linked_experiments") or [])
                if exp_id not in linked:
                    linked.append(exp_id)
                    ticket["linked_experiments"] = linked
                    ticket["updated_at"] = timestamp
                    self._tickets_store.put(clean_ticket_id, ticket)

        record["allowedActions"] = self._rw04_allowed_actions(record)
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

    _RETRY_LINK_CAS_ATTEMPTS = 32

    @staticmethod
    def _keyed_retry_experiment_id(parent_id: str, actor: str, key: str) -> str:
        """Deterministic retry id scoped to parent + effective actor + key."""
        scope = json.dumps([parent_id, actor, key], ensure_ascii=True)
        return f"exp-retry-{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:24]}"

    def _build_retry_record(
        self,
        exp: Dict[str, Any],
        *,
        new_exp_id: str,
        actor_id: Optional[str],
        timestamp: str,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        attempt_number = int(exp.get("attempt_number") or 1) + 1
        parent_id = exp["experiment_id"]
        root_id = exp.get("root_experiment_id") or parent_id
        new_record: Dict[str, Any] = {
            "experiment_id": new_exp_id,
            "ticket_id": exp.get("ticket_id", ""),
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
            "created_by": actor_id or exp.get("created_by"),
            "idempotency_key": idempotency_key,
        }
        new_record["allowedActions"] = self._rw04_allowed_actions(new_record)
        return new_record

    @staticmethod
    def _require_retry_scope(
        record: Dict[str, Any], parent_id: str, actor: str, key: str
    ) -> None:
        """Reject a deterministic-id hit that is not this parent/actor/key's retry."""
        if (
            record.get("parent_experiment_id") != parent_id
            or str(record.get("created_by") or "") != actor
            or record.get("idempotency_key") != key
        ):
            raise ValueError(
                f"experiment id {record.get('experiment_id')} is occupied by an unrelated record"
            )

    def _link_retry_to_ticket(
        self, ticket_id: str, new_exp_id: str, timestamp: str, *, conn: Any
    ) -> None:
        """Append the retry to the ticket's links via CAS inside ``conn``'s transaction."""
        ticket = self._tickets_store.get(ticket_id)
        for _ in range(self._RETRY_LINK_CAS_ATTEMPTS):
            if not isinstance(ticket, dict) or ticket.get("meeting_action"):
                return
            linked = list(ticket.get("linked_experiments") or [])
            if new_exp_id in linked:
                return
            updated = dict(ticket)
            updated["linked_experiments"] = linked + [new_exp_id]
            updated["updated_at"] = timestamp
            ok, current = self._tickets_store.compare_and_set(ticket_id, ticket, updated, conn=conn)
            if ok:
                return
            ticket = current
        raise RuntimeError(f"could not link retry {new_exp_id} to ticket {ticket_id}: contention")

    def _retry_research_experiment_keyed(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str],
        timestamp: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Idempotent retry: one attempt record + ticket link per parent/actor/key.

        The advisory lock serializes concurrent replays of the same scope; the
        attempt insert and ticket link share one transaction so a failure
        leaves neither behind.
        """
        parent_key = str(experiment_id)
        with self._experiments_store.transaction() as conn:
            parent = self._experiments_store.get(parent_key)
            if parent is None or not isinstance(parent, dict):
                return None
            actor = str(actor_id or parent.get("created_by") or "")
            new_exp_id = self._keyed_retry_experiment_id(parent_key, actor, idempotency_key)
            self._experiments_store.advisory_xact_lock(f"research-retry:{new_exp_id}", conn=conn)

            existing = self._experiments_store.get(new_exp_id)
            if isinstance(existing, dict):
                self._require_retry_scope(existing, parent_key, actor, idempotency_key)
            else:
                # Attempts persisted before keyed ids carry numeric ids; match by scope.
                existing = next(
                    (
                        e
                        for e in self._experiments_store.list_all()
                        if isinstance(e, dict)
                        and e.get("parent_experiment_id") == parent_key
                        and e.get("idempotency_key") == idempotency_key
                        and str(e.get("created_by") or "") == actor
                    ),
                    None,
                )
            if isinstance(existing, dict):
                return self._project_experiment_detail(existing)

            parent = self._experiments_store.get(parent_key)
            status = str((parent or {}).get("status") or "").strip().lower()
            if status not in self._RW04_RETRYABLE_STATUSES:
                return None

            new_record = self._build_retry_record(
                parent,
                new_exp_id=new_exp_id,
                actor_id=actor_id,
                timestamp=timestamp,
                idempotency_key=idempotency_key,
            )
            inserted, canonical = self._experiments_store.compare_and_set(
                new_exp_id, None, new_record, conn=conn
            )
            if not inserted:
                canonical = canonical if isinstance(canonical, dict) else new_record
                self._require_retry_scope(canonical, parent_key, actor, idempotency_key)
                return self._project_experiment_detail(canonical)
            ticket_id = parent.get("ticket_id")
            if ticket_id:
                self._link_retry_to_ticket(ticket_id, new_exp_id, timestamp, conn=conn)
            return self._project_experiment_detail(new_record)

    def retry_research_experiment(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str] = None,
        requested_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        timestamp = requested_at or _utc_now_rfc3339()
        clean_key = str(idempotency_key or "").strip()
        if clean_key:
            return self._retry_research_experiment_keyed(
                experiment_id,
                actor_id=actor_id,
                timestamp=timestamp,
                idempotency_key=clean_key,
            )

        exp = self._experiments_store.get(str(experiment_id))
        if exp is None or not isinstance(exp, dict):
            return None
        status = str(exp.get("status") or "").strip().lower()
        if status not in self._RW04_RETRYABLE_STATUSES:
            return None

        date_prefix = timestamp[:10].replace("-", "")
        with self._experiments_store.transaction() as conn:
            existing_experiments = self._experiments_store.list_all()
            idx = len(existing_experiments) + 1
            existing_ids = {str(e.get("experiment_id") or "") for e in existing_experiments}
            while True:
                new_exp_id = f"exp-{date_prefix}-{idx:03d}"
                idx += 1
                if new_exp_id in existing_ids:
                    continue
                new_record = self._build_retry_record(
                    exp,
                    new_exp_id=new_exp_id,
                    actor_id=actor_id,
                    timestamp=timestamp,
                    idempotency_key=idempotency_key,
                )
                inserted, _ = self._experiments_store.compare_and_set(
                    new_exp_id, None, new_record, conn=conn
                )
                if inserted:
                    break
            ticket_id = exp.get("ticket_id")
            if ticket_id:
                self._link_retry_to_ticket(ticket_id, new_exp_id, timestamp, conn=conn)
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
        return self._project_experiment_detail(exp) if isinstance(exp, dict) else None

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        experiments = self._experiments_store.list_all()
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
    def _create_meeting_note(self, note: Dict[str, Any], context: Any) -> Dict[str, Any]:
        ctx = self._action_auth(context)
        if any(str(k).startswith("_meeting_") for k in note):
            self._action_error("Reserved evidence binding fields", 400)
        if note.get("tenant_id", ctx.tenant_id) != ctx.tenant_id or note.get("actor_id", ctx.actor_id) != ctx.actor_id:
            self._action_error("Evidence actor/tenant mismatch", 403)
        if set(note) - {"meeting_ticket_id", "idempotency_key", "title", "body", "source_refs", "tenant_id", "actor_id"}:
            self._action_error("Unsupported meeting note fields", 400)
        ticket = self._tickets_store.get(str(note["meeting_ticket_id"]))
        if not ticket or not ticket.get("meeting_action") or not self._action_visible(ticket, ctx):
            self._action_error("Research ticket not found", 404)
        if ticket["action_state"] == "accepted":
            self._action_error("Accepted assignment is immutable")
        if not (ctx.actor_id in {ticket["owner"], ticket["required_actor"]} or self._action_operator(ctx)
                or (ticket["meeting_action"]["assignee_kind"] == "persona" and self._action_controller(ctx, "persona-pool-cycle"))):
            self._action_error("Actor cannot supply this assignment's evidence", 403)
        key = self._action_text(note.get("idempotency_key"), "idempotency_key", 256)
        title = self._action_text(note.get("title"), "title", 256)
        body = self._action_text(note.get("body"), "body", 12000)
        source_refs = note.get("source_refs", [])
        if not isinstance(source_refs, list) or len(source_refs) > 8:
            self._action_error("source_refs must contain at most 8 references", 400)
        if len(json.dumps(note).encode()) > self._ACTION_INPUT_MAX_BYTES:
            self._action_error("Meeting note exceeds 16 KiB", 400)
        nid = "note-action-" + self._action_digest([ctx.tenant_id, ticket["ticket_id"], ctx.actor_id, key])[:32]
        payload = {"note_id": nid, "id": nid, "title": title, "body": body, "source_refs": source_refs,
                   "_meeting_binding": {"tenant_id": ctx.tenant_id, "ticket_id": ticket["ticket_id"], "actor_id": ctx.actor_id}}
        payload["_meeting_digest"] = self._action_digest(payload)
        payload["created_at"] = payload["updated_at"] = _utc_now_rfc3339()
        if len(json.dumps(payload).encode()) > self._ACTION_INPUT_MAX_BYTES:
            self._action_error("Meeting note record exceeds 16 KiB", 400)
        ok, current = self._bounded_action_insert(self._notes_store, nid, payload,
            group_sql="payload->'_meeting_binding'->>'tenant_id' = %s AND payload->'_meeting_binding'->>'ticket_id' = %s",
            group_values=[ctx.tenant_id, ticket["ticket_id"]], cap=self._ACTION_NOTES_PER_TICKET_MAX)
        if not ok and (not current or current.get("_meeting_digest") != payload["_meeting_digest"]):
            self._action_error("Conflicting evidence note replay")
        return current

    def create_research_note(self, note: Dict[str, Any], *, context: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        if not isinstance(note, dict):
            return None
        if "meeting_ticket_id" in note:
            return self._create_meeting_note(note, context)
        if any(str(k).startswith("_meeting_") for k in note):
            self._action_error("Reserved evidence binding fields", 400)
        payload = json.loads(json.dumps(note))
        note_id = str(payload.get("note_id") or payload.get("id") or "").strip()
        if note_id.startswith("note-action-"):
            self._action_error("Reserved meeting note namespace", 400)
        if not note_id:
            existing_notes = self._notes_store.list_all()
            now_iso = _utc_now_rfc3339()
            date_prefix = now_iso[:10].replace("-", "")
            idx = len(existing_notes) + 1
            note_id = f"note-{date_prefix}-{idx:03d}"
            payload["note_id"] = note_id
            payload["id"] = note_id

        if not payload.get("created_at"):
            payload["created_at"] = _utc_now_rfc3339()
        if not payload.get("updated_at"):
            payload["updated_at"] = payload.get("created_at")

        existing = self._notes_store.get(note_id)
        if existing and existing.get("_meeting_binding"):
            self._action_error("Meeting evidence is immutable")
        # KW-02 keeps its original store contract; meeting-bound evidence is
        # inserted atomically by _create_meeting_note in its reserved namespace.
        self._notes_store.put(note_id, payload)
        return json.loads(json.dumps(payload))

    def get_research_note(self, note_id: Optional[str], *, context: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        if not note_id:
            return None
        note = self._notes_store.get(str(note_id))
        if isinstance(note, dict) and note.get("_meeting_binding"):
            self._action_auth(context)
            ticket = self._tickets_store.get(note["_meeting_binding"]["ticket_id"])
            if not ticket or not self._action_visible(ticket, context):
                return None
        return json.loads(json.dumps(note)) if isinstance(note, dict) else None

    def list_research_notes(self) -> List[Dict[str, Any]]:
        notes = [n for n in self._notes_store.list_all() if not n.get("_meeting_binding")]
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
