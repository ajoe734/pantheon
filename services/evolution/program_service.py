"""Program aggregate owner service (U8A).

Wraps :mod:`services.evolution.program_store` with the business-level
validation the contract requires: create always starts ``draft`` with a
server-generated identity (never a client-supplied ``status``/``actor``/
``tenant`` override), and the metadata PATCH allowlist is ``name`` only,
CAS-guarded on an explicit ``expected_revision`` precondition.

U8A does not implement real lifecycle transitions (submit_evolution_review,
approve_program, pause_program, resume_program, complete_program,
retire_program, stop, freeze_generation, promote_candidate_paper/live,
approve_mutation/reject_mutation) — see
docs/operations/bff-upstream-v2-20260911/decisions/evolution-lifecycle.md §3/§4.
Those are U8B's obligation; this service exposes no method for them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.evolution.models import EvolutionProgram, ProgramStatus
from services.evolution.program_store import (
    ProgramStore,
    ProgramStoreConflictError,
    ProgramStoreDivergentReplayError,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ProgramValidationError(ValueError):
    """A create/patch request failed input validation (maps to HTTP 422)."""


class ProgramNotFoundError(LookupError):
    """No program with this id is visible to this tenant (maps to HTTP 404,
    non-disclosing: identical for "does not exist" and "exists under a
    different tenant")."""

    def __init__(self, program_id: str) -> None:
        super().__init__(f"Evolution program not found: {program_id}")
        self.program_id = program_id


class ProgramConflictError(ValueError):
    """A stale ``expected_revision`` precondition or invalid transition failed
    (maps to HTTP 409). No partial write was made."""

    def __init__(self, message_or_program_id: str) -> None:
        msg = str(message_or_program_id)
        if not (" " in msg or "Cannot" in msg or "frozen" in msg or "status" in msg or "runs" in msg):
            msg = f"Evolution program {message_or_program_id} was modified concurrently; refetch and retry."
        super().__init__(msg)
        self.program_id = message_or_program_id


class ProgramDivergentReplayError(ValueError):
    """The same idempotency key was reused with a different request (maps to
    HTTP 409 — a divergent replay, not a silently accepted second version)."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"idempotency key {idempotency_key!r} was already used for a different request"
        )
        self.idempotency_key = idempotency_key


class ProgramService:
    """Owner service for the Program aggregate. See module docstring."""

    def __init__(self, store: ProgramStore) -> None:
        self._store = store

    # -- Create --------------------------------------------------------

    def create_program(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        name: str,
        idempotency_key: Optional[str] = None,
        legacy_params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        clean_tenant = str(tenant_id or "").strip()
        clean_actor = str(actor_id or "").strip()
        clean_name = str(name or "").strip()
        if not clean_tenant:
            raise ProgramValidationError("tenant_id is required")
        if not clean_actor:
            raise ProgramValidationError("actor_id is required")
        if not clean_name:
            raise ProgramValidationError("name must be a non-empty string")

        program_id = f"evp-{uuid.uuid4().hex}"
        request_fingerprint = {
            "operation": "create_program",
            "tenant_id": clean_tenant,
            "actor_id": clean_actor,
            "name": clean_name,
        }

        def factory() -> Dict[str, Any]:
            now = utc_now_iso()
            program = EvolutionProgram(
                program_id=program_id,
                tenant_id=clean_tenant,
                created_by=clean_actor,
                name=clean_name,
                status=ProgramStatus.DRAFT,
                revision=1,
                created_at=now,
                updated_at=now,
                legacy_params=dict(legacy_params or {}),
                run_ids=[],
                candidate_ids=[],
            )
            return program.to_dict()

        try:
            committed, replayed = self._store.create_with_receipt(
                tenant_id=clean_tenant,
                actor_id=clean_actor,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                program_factory=factory,
            )
        except ProgramStoreDivergentReplayError as exc:
            raise ProgramDivergentReplayError(exc.idempotency_key) from exc
        except ProgramStoreConflictError as exc:
            raise ProgramConflictError(program_id) from exc
        return committed, replayed

    # -- Read ------------------------------------------------------------

    def get_program(self, *, tenant_id: str, program_id: str) -> Optional[Dict[str, Any]]:
        clean_tenant = str(tenant_id or "").strip()
        clean_id = str(program_id or "").strip()
        if not clean_id:
            return None
        record = self._store.get(clean_id)
        if record is None:
            return None
        if str(record.get("tenant_id") or "") != clean_tenant:
            # Non-disclosing: a foreign-tenant program looks identical to a
            # missing one to this caller.
            return None
        return record

    def list_programs(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        clean_tenant = str(tenant_id or "").strip()
        return [
            record
            for record in self._store.list_all()
            if str(record.get("tenant_id") or "") == clean_tenant
        ]

    # -- Metadata PATCH (name only) --------------------------------------

    def patch_program_name(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        program_id: str,
        name: str,
        expected_revision: int,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        clean_tenant = str(tenant_id or "").strip()
        clean_actor = str(actor_id or "").strip()
        clean_id = str(program_id or "").strip()
        clean_name = str(name or "").strip()
        if not clean_tenant:
            raise ProgramValidationError("tenant_id is required")
        if not clean_actor:
            raise ProgramValidationError("actor_id is required")
        if not clean_name:
            raise ProgramValidationError("name must be a non-empty string")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ProgramValidationError("expected_revision must be an integer")

        current = self.get_program(tenant_id=clean_tenant, program_id=clean_id)
        if current is None:
            raise ProgramNotFoundError(clean_id)
        if int(current.get("revision") or 0) != expected_revision:
            raise ProgramConflictError(clean_id)

        request_fingerprint = {
            "operation": "patch_program_name",
            "tenant_id": clean_tenant,
            "program_id": clean_id,
            "expected_revision": expected_revision,
            "name": clean_name,
        }

        def mutate(base: Dict[str, Any]) -> Dict[str, Any]:
            updated = dict(base)
            updated["name"] = clean_name
            updated["revision"] = int(base.get("revision") or 0) + 1
            updated["updated_at"] = utc_now_iso()
            updated["updated_by"] = clean_actor
            return updated

        try:
            committed, replayed = self._store.patch_metadata_cas(
                tenant_id=clean_tenant,
                actor_id=clean_actor,
                program_id=clean_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                base_snapshot=current,
                mutate=mutate,
            )
        except ProgramStoreDivergentReplayError as exc:
            raise ProgramDivergentReplayError(exc.idempotency_key) from exc
        except ProgramStoreConflictError as exc:
            raise ProgramConflictError(clean_id) from exc
        return committed, replayed

    # -- Real Lifecycle Action Execution (U8B) ----------------------------

    def execute_action(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        actor_role: str = "operator",
        program_id: str,
        action_id: str,
        expected_revision: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        clean_tenant = str(tenant_id or "").strip()
        clean_actor = str(actor_id or "").strip()
        clean_role = str(actor_role or "operator").strip().lower()
        clean_id = str(program_id or "").strip()
        clean_action = str(action_id or "").strip().lower()
        payload = dict(payload or {})

        if not clean_tenant:
            raise ProgramValidationError("tenant_id is required")
        if not clean_actor:
            raise ProgramValidationError("actor_id is required")
        if not clean_id:
            raise ProgramValidationError("program_id is required")
        if not clean_action:
            raise ProgramValidationError("action_id is required")

        current = self.get_program(tenant_id=clean_tenant, program_id=clean_id)
        if current is None:
            raise ProgramNotFoundError(clean_id)

        current_rev = int(current.get("revision") or 0)
        if expected_revision is not None and current_rev != expected_revision:
            raise ProgramConflictError(
                f"Stale revision precondition for {clean_id}: expected {expected_revision}, current {current_rev}"
            )

        # Role checks
        approver_roles = {"approver", "admin", "live_owner_approver"}
        approver_actions = {
            "approve_program",
            "retire_program",
            "freeze_generation",
            "unfreeze_generation",
            "promote_candidate_paper",
            "promote_candidate_live",
            "approve_mutation",
            "reject_mutation",
        }
        if clean_action in approver_actions and clean_role not in approver_roles:
            raise ProgramValidationError(
                f"Action {action_id!r} requires approver role; actor holds {clean_role!r}"
            )

        known_actions = approver_actions.union({
            "submit_evolution_review",
            "pause_program",
            "resume_program",
            "complete_program",
            "stop",
            "create_constraint",
            "create_fitness_formula",
            "create_mutation_rule",
        })
        if clean_action not in known_actions:
            raise ProgramValidationError(f"Unsupported program action: {action_id!r}")

        now = utc_now_iso()
        receipt_id = f"rcpt-{uuid.uuid4().hex[:12]}"

        request_fingerprint = {
            "operation": "execute_program_action",
            "tenant_id": clean_tenant,
            "program_id": clean_id,
            "action_id": clean_action,
            "actor_id": clean_actor,
            "actor_role": clean_role,
            "payload": payload,
        }

        receipt_holder: Dict[str, Any] = {}

        def mutate(base: Dict[str, Any]) -> Dict[str, Any]:
            base_status_raw = base.get("status")
            base_status = (
                base_status_raw.value
                if isinstance(base_status_raw, ProgramStatus)
                else str(base_status_raw or "")
            ).lower()
            base_rev = int(base.get("revision") or 0)
            if expected_revision is not None and base_rev != expected_revision:
                raise ProgramConflictError(
                    f"Stale revision precondition for {clean_id}: expected {expected_revision}, current {base_rev}"
                )

            target_status = base_status
            details: Dict[str, Any] = {}

            # Map canonical action semantics
            if clean_action == "submit_evolution_review":
                if base_status != ProgramStatus.DRAFT.value:
                    raise ProgramConflictError(
                        f"Cannot submit review for program in status {base_status!r}; must be 'draft'"
                    )
                target_status = ProgramStatus.UNDER_REVIEW.value
                details = {
                    "review_id": f"rev-{uuid.uuid4().hex[:12]}",
                    "config_snapshot_revision": base_rev,
                    "note": note or "Submitted for evolution review",
                }

            elif clean_action == "approve_program":
                if base_status != ProgramStatus.UNDER_REVIEW.value:
                    raise ProgramConflictError(
                        f"Cannot approve program in status {base_status!r}; must be 'under_review'"
                    )
                target_status = ProgramStatus.ACTIVE.value
                details = {
                    "approval_id": payload.get("approval_id") or f"app-{uuid.uuid4().hex[:12]}",
                    "activated_revision": base_rev + 1,
                    "note": note or "Program approved and activated; no implicit job or deployment",
                }

            elif clean_action == "pause_program":
                if base_status != ProgramStatus.ACTIVE.value:
                    raise ProgramConflictError(
                        f"Cannot pause program in status {base_status!r}; must be 'active'"
                    )
                target_status = ProgramStatus.PAUSED.value
                details = {
                    "drain_active_runs": True,
                    "note": note or "Halted new generation admission while current runs drain (D1)",
                }

            elif clean_action == "resume_program":
                if base_status == ProgramStatus.STOPPED.value:
                    raise ProgramConflictError(
                        "Cannot resume stopped program; stop cancelled nonterminal runs and cannot be reversed by resume (D1)"
                    )
                if base_status != ProgramStatus.PAUSED.value:
                    raise ProgramConflictError(
                        f"Cannot resume program in status {base_status!r}; must be 'paused'"
                    )
                target_status = ProgramStatus.ACTIVE.value
                details = {
                    "resumed_attempts": True,
                    "revive_cancelled": False,
                    "note": note or "Scheduled eligible new attempts; cancelled runs remain cancelled (D1)",
                }

            elif clean_action == "complete_program":
                if base_status != ProgramStatus.ACTIVE.value:
                    raise ProgramConflictError(
                        f"Cannot complete program in status {base_status!r}; must be 'active'"
                    )
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                active_runs = payload.get("active_runs") or sub.get("active_runs") or payload.get("unresolved_runs") or sub.get("unresolved_runs") or []
                active_count = int(payload.get("active_runs_count") or sub.get("active_runs_count") or 0)
                if active_runs or active_count > 0:
                    raise ProgramConflictError(
                        "Cannot complete program with unresolved active runs; all runs must reach terminal status"
                    )
                target_status = ProgramStatus.COMPLETED.value
                details = {
                    "completion_evidence": payload.get("completion_evidence") or sub.get("completion_evidence") or "All runs reached terminal outcomes",
                    "note": note or "Program marked completed with terminal evidence",
                }

            elif clean_action == "retire_program":
                if base_status not in (ProgramStatus.COMPLETED.value, ProgramStatus.STOPPED.value):
                    raise ProgramConflictError(
                        f"Cannot retire program in status {base_status!r}; must be 'completed' or 'stopped'"
                    )
                target_status = ProgramStatus.RETIRED.value
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                details = {
                    "retirement_audit_ref": payload.get("retirement_audit_ref") or sub.get("retirement_audit_ref") or f"ret-{uuid.uuid4().hex[:12]}",
                    "terminal": True,
                    "note": note or "Program retired; terminal state, no strategy or runtime bindings altered",
                }

            elif clean_action == "stop":
                if base_status not in (ProgramStatus.ACTIVE.value, ProgramStatus.PAUSED.value):
                    raise ProgramConflictError(
                        f"Cannot stop program in status {base_status!r}; must be 'active' or 'paused'"
                    )
                target_status = ProgramStatus.STOPPED.value
                details = {
                    "cancelled_runs": True,
                    "note": note or "All nonterminal runs cancelled immediately (D1)",
                }

            elif clean_action == "freeze_generation":
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                generation_id = payload.get("generation_id") or sub.get("generation_id") or payload.get("id") or sub.get("id") or f"gen-{uuid.uuid4().hex[:8]}"
                details = {
                    "generation_id": generation_id,
                    "frozen_at": now,
                    "frozen_by": clean_actor,
                    "reason": payload.get("reason") or sub.get("reason") or note or "Generation frozen (D2)",
                }

            elif clean_action == "unfreeze_generation":
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                generation_id = payload.get("generation_id") or sub.get("generation_id") or payload.get("id") or sub.get("id") or f"gen-{uuid.uuid4().hex[:8]}"
                details = {
                    "generation_id": generation_id,
                    "unfrozen_at": now,
                    "unfrozen_by": clean_actor,
                    "reason": payload.get("reason") or sub.get("reason") or note or "Generation unfrozen (D2)",
                }

            elif clean_action == "promote_candidate_paper":
                if bool(base.get("is_frozen")):
                    raise ProgramConflictError("Cannot promote candidate while generation is frozen (D2)")
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                candidate_id = str(payload.get("candidate_id") or sub.get("candidate_id") or "").strip()
                if not candidate_id:
                    raise ProgramValidationError("candidate_id is required for candidate promotion")
                run_id = str(payload.get("run_id") or sub.get("run_id") or "").strip() or None
                artifact_id = str(payload.get("artifact_id") or sub.get("artifact_id") or "").strip() or None
                artifact_version = str(payload.get("artifact_version") or sub.get("artifact_version") or "").strip() or None
                artifact_digest = str(
                    payload.get("artifact_digest")
                    or sub.get("artifact_digest")
                    or payload.get("digest")
                    or sub.get("digest")
                    or ""
                ).strip() or None
                approval_id = str(payload.get("approval_id") or sub.get("approval_id") or "").strip() or None
                details = {
                    "promotion_id": f"prm-{uuid.uuid4().hex[:8]}",
                    "stage": "paper",
                    "candidate_id": candidate_id,
                    "run_id": run_id,
                    "artifact_id": artifact_id,
                    "artifact_version": artifact_version,
                    "artifact_digest": artifact_digest,
                    "approval_id": approval_id,
                    "promoted_at": now,
                    "promoted_by": clean_actor,
                }

            elif clean_action == "promote_candidate_live":
                if bool(base.get("is_frozen")):
                    raise ProgramConflictError("Cannot promote candidate while generation is frozen (D2)")
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                candidate_id = str(payload.get("candidate_id") or sub.get("candidate_id") or "").strip()
                if not candidate_id:
                    raise ProgramValidationError("candidate_id is required for candidate promotion")
                run_id = str(payload.get("run_id") or sub.get("run_id") or "").strip() or None
                artifact_id = str(payload.get("artifact_id") or sub.get("artifact_id") or "").strip() or None
                artifact_version = str(payload.get("artifact_version") or sub.get("artifact_version") or "").strip() or None
                artifact_digest = str(
                    payload.get("artifact_digest")
                    or sub.get("artifact_digest")
                    or payload.get("digest")
                    or sub.get("digest")
                    or ""
                ).strip() or None
                approval_id = str(payload.get("approval_id") or sub.get("approval_id") or "").strip() or None
                details = {
                    "promotion_id": f"prm-{uuid.uuid4().hex[:8]}",
                    "stage": "live",
                    "candidate_id": candidate_id,
                    "run_id": run_id,
                    "artifact_id": artifact_id,
                    "artifact_version": artifact_version,
                    "artifact_digest": artifact_digest,
                    "approval_id": approval_id,
                    "promoted_at": now,
                    "promoted_by": clean_actor,
                    "capital_authority": "none",
                    "runtime_authority": "none",
                    "note": "Live promotion review intent recorded; strictly no capital or live trading authorized",
                }

            elif clean_action == "approve_mutation":
                if bool(base.get("is_frozen")):
                    raise ProgramConflictError("Cannot approve mutation while generation is frozen (D2)")
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                mutation_id = str(
                    payload.get("mutation_id")
                    or sub.get("mutation_id")
                    or payload.get("decision_id")
                    or sub.get("decision_id")
                    or ""
                ).strip()
                if not mutation_id:
                    raise ProgramValidationError("mutation_id is required for approve_mutation")
                details = {
                    "mutation_id": mutation_id,
                    "decision": "approved",
                    "approved_at": now,
                    "approved_by": clean_actor,
                }

            elif clean_action == "reject_mutation":
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                mutation_id = str(
                    payload.get("mutation_id")
                    or sub.get("mutation_id")
                    or payload.get("decision_id")
                    or sub.get("decision_id")
                    or ""
                ).strip()
                if not mutation_id:
                    raise ProgramValidationError("mutation_id is required for reject_mutation")
                reason = payload.get("reason") or sub.get("reason") or "Mutation rejected by approver"
                details = {
                    "mutation_id": mutation_id,
                    "decision": "rejected",
                    "rejected_at": now,
                    "rejected_by": clean_actor,
                    "reason": reason,
                }

            elif clean_action == "create_constraint":
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                details = {
                    "id": payload.get("id") or sub.get("id") or f"cst-{uuid.uuid4().hex[:8]}",
                    "name": str(payload.get("name") or sub.get("name") or ""),
                    "scope": str(payload.get("scope") or sub.get("scope") or "global"),
                    "operator": str(payload.get("operator") or sub.get("operator") or "<="),
                    "value": payload.get("value") if payload.get("value") is not None else sub.get("value"),
                    "penalty_weight": payload.get("penalty_weight", sub.get("penalty_weight", 1.0)),
                    "enabled": bool(payload.get("enabled", sub.get("enabled", True))),
                    "created_at": now,
                }

            elif clean_action == "create_fitness_formula":
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                details = {
                    "id": payload.get("id") or sub.get("id") or f"fit-{uuid.uuid4().hex[:8]}",
                    "expression": str(payload.get("expression") or sub.get("expression") or ""),
                    "metrics": list(payload.get("metrics") or sub.get("metrics") or []),
                    "applied_scope": str(payload.get("applied_scope") or sub.get("applied_scope") or "generation"),
                    "created_at": now,
                }

            elif clean_action == "create_mutation_rule":
                sub = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                details = {
                    "id": payload.get("id") or sub.get("id") or f"mutr-{uuid.uuid4().hex[:8]}",
                    "scope": str(payload.get("scope") or sub.get("scope") or "all"),
                    "expression": str(payload.get("expression") or sub.get("expression") or ""),
                    "rate": float(payload.get("rate") if payload.get("rate") is not None else sub.get("rate", 0.05)),
                    "risk": str(payload.get("risk") or sub.get("risk") or "low"),
                    "enabled": bool(payload.get("enabled", sub.get("enabled", True))),
                    "created_at": now,
                }

            receipt = {
                "receipt_id": receipt_id,
                "idempotency_key": idempotency_key,
                "program_id": clean_id,
                "action_id": clean_action,
                "status": target_status,
                "executed_at": now,
                "actor_id": clean_actor,
                "actor_role": clean_role,
                "prior_status": base_status,
                "new_status": target_status,
                "revision": base_rev + 1,
                "details": details,
            }
            receipt_holder["receipt"] = receipt
            receipt_holder["details"] = details
            receipt_holder["target_status"] = target_status
            receipt_holder["prior_status"] = base_status

            updated = dict(base)
            new_rev = base_rev + 1
            updated["revision"] = new_rev
            updated["updated_at"] = now
            updated["updated_by"] = clean_actor
            if target_status != base_status:
                updated["status"] = target_status

            if clean_action == "freeze_generation":
                updated["is_frozen"] = True
                frz_list = list(updated.get("freeze_records") or [])
                frz_list.append(details)
                updated["freeze_records"] = frz_list
            elif clean_action == "unfreeze_generation":
                updated["is_frozen"] = False
                frz_list = list(updated.get("freeze_records") or [])
                frz_list.append(details)
                updated["freeze_records"] = frz_list
            elif clean_action in ("promote_candidate_paper", "promote_candidate_live"):
                prom_list = list(updated.get("promotions") or [])
                prom_list.append(details)
                updated["promotions"] = prom_list
            elif clean_action == "create_constraint":
                c_list = list(updated.get("constraints") or [])
                c_list.append(details)
                updated["constraints"] = c_list
            elif clean_action == "create_fitness_formula":
                f_list = list(updated.get("fitness_formulas") or [])
                f_list.append(details)
                updated["fitness_formulas"] = f_list
            elif clean_action == "create_mutation_rule":
                m_list = list(updated.get("mutation_rules") or [])
                m_list.append(details)
                updated["mutation_rules"] = m_list

            rcpt_list = list(updated.get("action_receipts") or [])
            rcpt_list.append(receipt)
            updated["action_receipts"] = rcpt_list
            return updated

        try:
            committed, replayed = self._store.patch_metadata_cas(
                tenant_id=clean_tenant,
                actor_id=clean_actor,
                program_id=clean_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                base_snapshot=current,
                mutate=mutate,
                namespace="execute_program_action",
            )
        except ProgramStoreDivergentReplayError as exc:
            raise ProgramDivergentReplayError(exc.idempotency_key) from exc
        except ProgramStoreConflictError as exc:
            raise ProgramConflictError(clean_id) from exc

        # Find the receipt from committed program or build output
        readback_receipt = receipt_holder.get("receipt") or {}
        if replayed and committed.get("action_receipts"):
            for r in reversed(committed["action_receipts"]):
                if idempotency_key and r.get("idempotency_key") == idempotency_key:
                    readback_receipt = r
                    break
                elif r.get("action_id") == clean_action:
                    readback_receipt = r
                    break

        details = readback_receipt.get("details") or receipt_holder.get("details", {})
        prior_status = readback_receipt.get("prior_status") or receipt_holder.get("prior_status", current.get("status"))
        target_status = readback_receipt.get("new_status") or committed.get("status")

        result_payload = {
            "receipt_id": readback_receipt.get("receipt_id", receipt_id),
            "program_id": clean_id,
            "action_id": clean_action,
            "status": target_status,
            "program_status": committed.get("status", target_status),
            "idempotent_replay": replayed,
            "executed_at": readback_receipt.get("executed_at", now),
            "actor_id": clean_actor,
            "actor_role": clean_role,
            "prior_status": prior_status,
            "new_status": target_status,
            "revision": int(committed.get("revision") or 0),
            "program": committed,
            "readback": committed,
            "receipt": readback_receipt,
            "details": details,
        }
        return result_payload, replayed
