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
    """A stale ``expected_revision`` precondition failed (maps to HTTP 409).
    No partial write was made."""

    def __init__(self, program_id: str) -> None:
        super().__init__(f"Evolution program {program_id} was modified concurrently; refetch and retry.")
        self.program_id = program_id


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
