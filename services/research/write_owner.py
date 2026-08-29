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


class ResearchWriteOwner:
    """Authoritative durable write owner for the Research domain."""

    _RW04_CANCELABLE_STATUSES = frozenset({"queued", "running"})

    def __init__(
        self,
        *,
        dsn: str,
        schema: str = "research",
        bootstrap: bool = True,
        tickets_table: Optional[str] = None,
        experiments_table: Optional[str] = None,
        notes_table: Optional[str] = None,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for ResearchWriteOwner")
        self.dsn = dsn
        self.schema = schema.strip() or "research"
        self._tickets_store = PostgresJsonOwnerStore(
            dsn=dsn,
            table=tickets_table or f"{self.schema}.research_tickets",
            owner_service="research-svc",
            bootstrap=bootstrap,
        )
        self._experiments_store = PostgresJsonOwnerStore(
            dsn=dsn,
            table=experiments_table or f"{self.schema}.research_experiments",
            owner_service="research-svc",
            bootstrap=bootstrap,
        )
        self._notes_store = PostgresJsonOwnerStore(
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
            "status": status,
            "stage": exp.get("stage"),
            "framework": exp.get("framework") or run_config.get("backend"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "strategy_id": strategy_id,
            "linked_strategy_id": strategy_id,
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id"),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
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
            "status": status,
            "stage": exp.get("stage"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
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
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
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
            "allowedActions": {"canCancel": True},
        }

        if clean_ticket_id:
            ticket = self._tickets_store.get(clean_ticket_id)
            if ticket and isinstance(ticket, dict):
                linked = list(ticket.get("linked_experiments") or [])
                if exp_id not in linked:
                    linked.append(exp_id)
                    ticket["linked_experiments"] = linked
                    ticket["updated_at"] = timestamp
                    self._tickets_store.put(clean_ticket_id, ticket)

        self._experiments_store.put(exp_id, record)
        return self._project_experiment_detail(record)

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
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
        exp["allowedActions"] = {"canCancel": False}
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
    ) -> List[Dict[str, Any]]:
        experiments = self._experiments_store.list_all()
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
            idx = len(existing_notes) + 1
            note_id = f"note-{date_prefix}-{idx:03d}"
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
