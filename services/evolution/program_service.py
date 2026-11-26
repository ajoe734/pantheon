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

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple

from services.evolution.models import (
    BarFrequencyMutationError,
    EvolutionProgram,
    ProgramStatus,
    reject_bar_frequency_mutation,
)
from services.evolution.program_store import (
    ProgramStore,
    ProgramStoreConflictError,
    ProgramStoreDivergentReplayError,
)
from services.governance.approval_authority import (
    ApprovalEvidence,
    ApprovalInvalid,
    ApprovalUnavailable,
)

PROGRAM_APPROVAL_TARGET_TYPE = "evolution_proposal"

# Automated identities. They may propose and dispatch; they can never approve
# or reject a program, on any route.
SYSTEM_PRINCIPALS = frozenset({"evolution-daily-sweep", "evolution-dispatch-worker"})


class ProgramApprovalRefused(ValueError):
    """The governance evidence does not authorize this program transition.

    ``code`` is a stable machine-readable reason; each refusal path has its own.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class ProgramApprovalUnavailable(RuntimeError):
    """Governance could not be reached; the transition fails closed."""


def is_system_principal(actor_id: Optional[str]) -> bool:
    return str(actor_id or "").strip().lower() in SYSTEM_PRINCIPALS


def program_content_digest(
    program: Mapping[str, Any],
    decision_fingerprints: Mapping[str, str],
) -> str:
    """Digest of what the human is being asked to approve.

    Excludes the revision (carried separately) so the digest changes only when
    the program's tenant, identity or linked decisions' immutable content do.
    """
    source_ids = sorted(str(item) for item in (program.get("source_decision_ids") or []))
    body = {
        "tenant_id": str(program.get("tenant_id") or ""),
        "program_id": str(program.get("program_id") or ""),
        "source_decisions": [
            {"decision_id": decision_id, "fingerprint": decision_fingerprints.get(decision_id)}
            for decision_id in source_ids
        ],
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def review_revision(program: Mapping[str, Any]) -> int:
    """Revision the program will hold while ``under_review``.

    Submitting for review is itself a revision bump, so the approval binds the
    post-submit revision and any later mutation makes the approval stale.
    """
    return int(program.get("revision") or 0) + 1


def approval_binding(
    program: Mapping[str, Any],
    decision_fingerprints: Mapping[str, str],
    *,
    revision: int,
) -> Dict[str, Any]:
    """The exact fields a governance request must carry for this program."""
    program_id = str(program.get("program_id") or "")
    return {
        "tenant_id": str(program.get("tenant_id") or ""),
        "target_type": PROGRAM_APPROVAL_TARGET_TYPE,
        "target_id": program_id,
        "target_version": str(revision),
        "proposal_id": program_id,
        "proposal_revision": revision,
        "proposal_content_digest": program_content_digest(program, decision_fingerprints),
    }


class ApprovalReaderLike(Protocol):
    def get(self, decision_id: str) -> ApprovalEvidence:
        ...


def _field(evidence: ApprovalEvidence, name: str) -> Any:
    return getattr(evidence, name, None)


def _check_binding(evidence: ApprovalEvidence, binding: Mapping[str, Any]) -> None:
    if evidence.tenant_id != binding["tenant_id"]:
        raise ProgramApprovalRefused("approval_tenant_mismatch", "approval belongs to another tenant")
    if evidence.target_type != binding["target_type"]:
        raise ProgramApprovalRefused(
            "approval_target_type_mismatch",
            f"approval target_type must be {binding['target_type']}",
        )
    if evidence.target_id != binding["target_id"] or _field(evidence, "proposal_id") not in (
        None,
        binding["proposal_id"],
    ):
        raise ProgramApprovalRefused("approval_program_mismatch", "approval is bound to another program")
    if evidence.target_version != binding["target_version"] or _field(
        evidence, "proposal_revision"
    ) != binding["proposal_revision"]:
        raise ProgramApprovalRefused(
            "approval_revision_stale",
            "approval is bound to a different program revision",
        )
    if _field(evidence, "proposal_content_digest") != binding["proposal_content_digest"]:
        raise ProgramApprovalRefused(
            "approval_content_mismatch",
            "approval does not cover the program's current source decisions",
        )


class ProgramApprovalVerifier:
    """Reads governance evidence by exact id and checks it against a binding."""

    def __init__(self, reader_factory: Callable[[], ApprovalReaderLike]) -> None:
        self._reader_factory = reader_factory

    def _read(self, approval_id: str) -> ApprovalEvidence:
        if not str(approval_id or "").strip():
            raise ProgramApprovalRefused("approval_missing", "a governance approval id is required")
        try:
            return self._reader_factory().get(approval_id)
        except ApprovalUnavailable as exc:
            raise ProgramApprovalUnavailable(str(exc)) from exc
        except ApprovalInvalid as exc:
            raise ProgramApprovalRefused(
                "approval_missing", f"governance has no readable approval {approval_id!r}"
            ) from exc

    def require_pending_request(self, approval_id: str, binding: Mapping[str, Any]) -> ApprovalEvidence:
        """Used at submit: the request exists, is bound correctly and is undecided."""
        evidence = self._read(approval_id)
        _check_binding(evidence, binding)
        if evidence.decision_state == "decided":
            raise ProgramApprovalRefused(
                "approval_already_decided", "the governance request was already decided"
            )
        return evidence

    def require_human_approval(self, approval_id: str, binding: Mapping[str, Any]) -> ApprovalEvidence:
        """Used at approve: a current, unconditional, human-decided approval."""
        return require_human_approval_evidence(self._read(approval_id), binding)


def require_human_approval_evidence(
    evidence: ApprovalEvidence, binding: Mapping[str, Any]
) -> ApprovalEvidence:
    """The one check for "a human approved exactly this program"."""
    _check_binding(evidence, binding)
    if evidence.decision_state != "decided":
        raise ProgramApprovalRefused("approval_pending", "the governance request is not decided yet")
    if evidence.decision == "rejected":
        raise ProgramApprovalRefused("approval_rejected", "the governance request was rejected")
    if evidence.decision != "approved":
        raise ProgramApprovalRefused("approval_not_approved", "the governance request is not approved")
    if is_system_principal(evidence.actor_id):
        raise ProgramApprovalRefused(
            "approval_not_human", "a system identity cannot approve a program"
        )
    try:
        evidence.require_valid()
    except ApprovalInvalid as exc:
        message = str(exc)
        code = "approval_expired" if "expir" in message.lower() else "approval_invalid"
        raise ProgramApprovalRefused(code, message) from exc
    return evidence


def decision_fingerprints_for(
    decision_ids: Iterable[str], fingerprint_of: Callable[[str], Optional[str]]
) -> Dict[str, str]:
    return {decision_id: fingerprint_of(decision_id) for decision_id in decision_ids}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


AUTO_QUALIFIED_ORIGIN = "auto_qualified"


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


class ProgramForbiddenError(PermissionError):
    """The caller may never perform this action (maps to HTTP 403)."""


class ProgramApprovalError(ValueError):
    """Governance evidence refused the transition (maps to HTTP 409).

    ``code`` is the stable refusal reason (``approval_missing``,
    ``approval_rejected``, ``approval_expired``, ``approval_tenant_mismatch``,
    ``approval_program_mismatch``, ``approval_revision_stale`` ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class ProgramUnavailableError(RuntimeError):
    """A dependency the transition needs fails closed (maps to HTTP 503)."""


class DecisionAdvanceRefused(Exception):
    """A linked EvolutionDecision could not take the requested step."""

    def __init__(self, decision_id: str, reason: str) -> None:
        super().__init__(f"decision {decision_id} cannot advance: {reason}")
        self.decision_id = decision_id
        self.reason = reason


class DecisionGateway(Protocol):
    """The program owner's only door to EvolutionDecisions.

    Implemented by ``services/evolution/main.py`` on top of the *existing*
    review / approve / reject paths and the existing dispatch outbox -- there is
    no second dispatcher.
    """

    def fingerprint(self, decision_id: str, tenant_id: str) -> Optional[str]:
        ...

    def risk_level(self, decision_id: str, tenant_id: str) -> Optional[str]:
        ...

    def advance(self, decision_id: str, *, tenant_id: str, actor_id: str, actor_role: str,
                approval_id: str) -> Dict[str, Any]:
        ...

    def reject(self, decision_id: str, *, tenant_id: str, actor_id: str, actor_role: str,
               reason: str) -> Dict[str, Any]:
        ...


# One lifecycle policy: which statuses admit an action and which roles may run
# it.  ``allowed_actions`` (read projection) and ``execute_action`` (owner
# enforcement) both read this table.
APPROVER_ROLES = frozenset({"approver", "admin", "live_owner_approver"})
APPROVER_ONLY_ACTIONS = frozenset({
    "approve_program", "reject_program", "retire_program", "freeze_generation",
    "unfreeze_generation", "promote_candidate_paper", "promote_candidate_live",
    "approve_mutation", "reject_mutation",
})
HUMAN_ONLY_ACTIONS = frozenset({"approve_program", "reject_program"})
ACTION_STATUSES: Dict[str, frozenset] = {
    "submit_evolution_review": frozenset({"draft"}),
    "approve_program": frozenset({"under_review"}),
    "reject_program": frozenset({"under_review"}),
    "pause_program": frozenset({"active"}),
    "resume_program": frozenset({"paused"}),
    "complete_program": frozenset({"active"}),
    "retire_program": frozenset({"completed", "stopped"}),
    "stop": frozenset({"active", "paused"}),
}


def allowed_actions(program: Dict[str, Any], actor_role: str, actor_id: str = "") -> List[str]:
    """Lifecycle actions the caller may attempt on this program right now."""
    status = str(program.get("status") or "").lower()
    role = str(actor_role or "").strip().lower()
    result = []
    awaiting_request = status == "under_review" and not (program.get("review") or {}).get("approval_id")
    for action, statuses in ACTION_STATUSES.items():
        if status not in statuses and not (action == "submit_evolution_review" and awaiting_request):
            continue
        if action == "submit_evolution_review" and is_system_principal(actor_id) and awaiting_request:
            continue
        if action in APPROVER_ONLY_ACTIONS and role not in APPROVER_ROLES:
            continue
        if action in HUMAN_ONLY_ACTIONS and is_system_principal(actor_id):
            continue
        result.append(action)
    return result


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

    def __init__(
        self,
        store: ProgramStore,
        *,
        approval_verifier: Optional[ProgramApprovalVerifier] = None,
        decisions: Optional[DecisionGateway] = None,
    ) -> None:
        self._store = store
        self._verifier = approval_verifier
        self._decisions = decisions

    def _binding(self, program: Dict[str, Any], *, revision: int) -> Dict[str, Any]:
        fingerprints: Dict[str, Any] = {}
        for decision_id in program.get("source_decision_ids") or []:
            fingerprints[decision_id] = (
                self._decisions.fingerprint(decision_id, str(program.get("tenant_id") or ""))
                if self._decisions is not None
                else None
            )
        return approval_binding(program, fingerprints, revision=revision)

    def approval_binding_for(self, *, tenant_id: str, program_id: str) -> Dict[str, Any]:
        """What a governance request for this program's next review must carry."""
        program = self.get_program(tenant_id=tenant_id, program_id=program_id)
        if program is None:
            raise ProgramNotFoundError(program_id)
        for decision_id in program.get("source_decision_ids") or []:
            if self._decisions is None or self._decisions.fingerprint(
                decision_id, str(program.get("tenant_id") or "")
            ) is None:
                raise ProgramConflictError(
                    f"source decision {decision_id} is not readable; cannot bind an approval"
                )
        binding = self._binding(program, revision=review_revision(program))
        # The governance request is decided at the highest risk of any linked
        # decision (a manual program with no decisions is medium).
        order = ["low", "medium", "high", "critical"]
        risks = [self._decisions.risk_level(d, binding["tenant_id"])
                 for d in program.get("source_decision_ids") or []] if self._decisions else []
        binding["risk_level"] = max([r for r in risks if r in order] or ["medium"], key=order.index)
        return binding

    def _require_verifier(self) -> ProgramApprovalVerifier:
        if self._verifier is None:
            raise ProgramUnavailableError(
                "governance approval verification is not configured "
                "(EVOLUTION_GOVERNANCE_BASE_URL); program approval fails closed"
            )
        return self._verifier

    @staticmethod
    def _is_replay(program: Dict[str, Any], action: str, key: Optional[str]) -> bool:
        return bool(key) and any(
            r.get("idempotency_key") == key and r.get("action_id") == action
            for r in program.get("action_receipts") or []
        )

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

    def create_auto_qualified_program(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        name: str,
        decision_id: str,
        qualification: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        """Create the one program a qualified daily-sweep decision earns.

        This is deliberately a separate method from :meth:`create_program`:
        the public create API accepts only ``name``, so no API caller can set
        ``origin``/``source_decision_ids``/``qualification`` or mark a program
        qualified. The idempotency key is derived from the decision, so a
        repeated sweep replays the committed program instead of creating a
        second one.
        """
        clean_tenant = str(tenant_id or "").strip()
        clean_actor = str(actor_id or "").strip()
        clean_name = str(name or "").strip()
        clean_decision = str(decision_id or "").strip()
        if not clean_tenant:
            raise ProgramValidationError("tenant_id is required")
        if not clean_actor:
            raise ProgramValidationError("actor_id is required")
        if not clean_name:
            raise ProgramValidationError("name must be a non-empty string")
        if not clean_decision:
            raise ProgramValidationError("decision_id is required")
        if not isinstance(qualification, dict) or not qualification.get("policy_version"):
            raise ProgramValidationError("qualification record with policy_version is required")

        program_id = f"evp-{uuid.uuid4().hex}"
        request_fingerprint = {
            "operation": "create_auto_qualified_program",
            "tenant_id": clean_tenant,
            "actor_id": clean_actor,
            "decision_id": clean_decision,
        }

        def factory() -> Dict[str, Any]:
            now = utc_now_iso()
            return EvolutionProgram(
                program_id=program_id,
                tenant_id=clean_tenant,
                created_by=clean_actor,
                name=clean_name,
                status=ProgramStatus.DRAFT,
                revision=1,
                created_at=now,
                updated_at=now,
                origin=AUTO_QUALIFIED_ORIGIN,
                source_decision_ids=[clean_decision],
                qualification=dict(qualification),
            ).to_dict()

        try:
            return self._store.create_with_receipt(
                tenant_id=clean_tenant,
                actor_id=clean_actor,
                idempotency_key=f"auto-qualify:{clean_decision}",
                request_fingerprint=request_fingerprint,
                program_factory=factory,
            )
        except ProgramStoreDivergentReplayError as exc:
            raise ProgramDivergentReplayError(exc.idempotency_key) from exc
        except ProgramStoreConflictError as exc:
            raise ProgramConflictError(program_id) from exc

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

    # -- Run linkage -----------------------------------------------------

    def link_run(
        self,
        *,
        tenant_id: str,
        decision_id: str,
        run_id: str,
        downstream_kind: str,
        actor_id: str,
    ) -> List[Dict[str, Any]]:
        """Link the real downstream run id from a dispatch receipt into every
        active program that lists the decision.  Idempotent: a run already
        linked is not linked twice."""
        clean_tenant = str(tenant_id or "").strip()
        clean_run = str(run_id or "").strip()
        if not clean_run:
            raise ProgramValidationError("run_id is required")
        linked: List[Dict[str, Any]] = []
        for program in self.list_programs(tenant_id=clean_tenant):
            if decision_id not in (program.get("source_decision_ids") or []):
                continue
            if any(link.get("run_id") == clean_run for link in program.get("run_links") or []):
                continue

            def mutate(base: Dict[str, Any]) -> Dict[str, Any]:
                updated = dict(base)
                links = list(updated.get("run_links") or [])
                if any(link.get("run_id") == clean_run for link in links):
                    return updated
                links.append({
                    "run_id": clean_run,
                    "decision_id": decision_id,
                    "downstream_kind": downstream_kind,
                    "linked_at": utc_now_iso(),
                })
                updated["run_links"] = links
                updated["run_ids"] = list(dict.fromkeys([*(updated.get("run_ids") or []), clean_run]))
                updated["revision"] = int(base.get("revision") or 0) + 1
                updated["updated_at"] = utc_now_iso()
                updated["updated_by"] = actor_id
                return updated

            try:
                committed, _ = self._store.patch_metadata_cas(
                    tenant_id=clean_tenant,
                    actor_id=actor_id,
                    program_id=program["program_id"],
                    idempotency_key=f"link-run:{clean_run}",
                    request_fingerprint={"operation": "link_run", "run_id": clean_run,
                                         "program_id": program["program_id"]},
                    base_snapshot=program,
                    mutate=mutate,
                    namespace="link_program_run",
                )
            except ProgramStoreConflictError as exc:
                raise ProgramConflictError(program["program_id"]) from exc
            linked.append(committed)
        return linked

    def _prepare_transition(
        self,
        program: Dict[str, Any],
        *,
        action: str,
        actor_id: str,
        actor_role: str,
        payload: Dict[str, Any],
        note: Optional[str],
    ) -> Dict[str, Any]:
        """Governance and decision side effects that must hold *before* the
        program transition commits.  Any refusal leaves the program unchanged."""
        status = str(program.get("status") or "").lower()
        program_id = str(program.get("program_id") or "")
        tenant_id = str(program.get("tenant_id") or "")
        if action == "submit_evolution_review":
            awaiting = status == ProgramStatus.UNDER_REVIEW.value and not (program.get("review") or {}).get("approval_id")
            if status != ProgramStatus.DRAFT.value and not awaiting:
                raise ProgramConflictError(
                    f"Cannot submit review for program in status {status!r}; must be 'draft'"
                )
            approval_id = str(payload.get("approval_id") or "").strip()
            if not approval_id:
                if (is_system_principal(actor_id) and program.get("origin") == AUTO_QUALIFIED_ORIGIN
                        and status == ProgramStatus.DRAFT.value):
                    # The qualifier has no human credential, so it cannot file the
                    # Governance request.  It surfaces the program for review; a
                    # human submit then files and attaches the request.
                    return {"approval_id": None, "content_digest": None}
                raise ProgramApprovalError(
                    "approval_missing",
                    "submit_evolution_review requires the governance approval request id; "
                    "the owner does not generate one",
                )
            if is_system_principal(actor_id):
                raise ProgramForbiddenError(
                    f"System identity {actor_id!r} cannot attach a governance approval request"
                )
            binding = self.approval_binding_for(tenant_id=tenant_id, program_id=program_id)
            try:
                self._require_verifier().require_pending_request(approval_id, binding)
            except ProgramApprovalRefused as exc:
                raise ProgramApprovalError(exc.code, str(exc)) from exc
            except ProgramApprovalUnavailable as exc:
                raise ProgramUnavailableError(str(exc)) from exc
            return {"approval_id": approval_id, "content_digest": binding["proposal_content_digest"]}

        if action == "approve_program":
            if status != ProgramStatus.UNDER_REVIEW.value:
                raise ProgramConflictError(
                    f"Cannot approve program in status {status!r}; must be 'under_review'"
                )
            review = program.get("review") or {}
            approval_id = str(review.get("approval_id") or "").strip()
            supplied = str(payload.get("approval_id") or "").strip()
            if supplied and approval_id and supplied != approval_id:
                raise ProgramApprovalError(
                    "approval_program_mismatch",
                    "approval id is not the request this program was submitted with",
                )
            binding = self._binding(program, revision=int(program.get("revision") or 0))
            try:
                evidence = self._require_verifier().require_human_approval(approval_id, binding)
            except ProgramApprovalRefused as exc:
                raise ProgramApprovalError(exc.code, str(exc)) from exc
            except ProgramApprovalUnavailable as exc:
                raise ProgramUnavailableError(str(exc)) from exc
            return {
                "approval_id": approval_id,
                "approved_by": evidence.actor_id,
                # The decisions advance as the human governance decider, on the
                # same approval evidence -- not as the activating caller.
                "decisions": self._advance_decisions(
                    program,
                    actor_id=str(evidence.actor_id),
                    actor_role=str(evidence.actor_role),
                    approval_id=approval_id,
                ),
            }

        if action == "reject_program":
            if status != ProgramStatus.UNDER_REVIEW.value:
                raise ProgramConflictError(
                    f"Cannot reject program in status {status!r}; must be 'under_review'"
                )
            reason = str(payload.get("reason") or note or "").strip()
            if not reason:
                raise ProgramValidationError("reason is required to reject a program")
            return {
                "reason": reason,
                "decisions": self._reject_decisions(
                    program,
                    actor_id=actor_id,
                    actor_role=str(payload.get("decision_actor_role") or actor_role),
                    reason=reason,
                ),
            }
        return {}

    def _gateway(self) -> DecisionGateway:
        if self._decisions is None:
            raise ProgramUnavailableError("decision gateway is not configured")
        return self._decisions

    def _advance_decisions(self, program, *, actor_id, actor_role, approval_id) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for decision_id in program.get("source_decision_ids") or []:
            try:
                results.append(self._gateway().advance(
                    decision_id,
                    tenant_id=str(program.get("tenant_id") or ""),
                    actor_id=actor_id,
                    actor_role=actor_role,
                    approval_id=approval_id,
                ))
            except DecisionAdvanceRefused as exc:
                raise ProgramConflictError(
                    f"Program stays under_review: decision {exc.decision_id} cannot advance: {exc.reason}"
                ) from exc
        return results

    def _reject_decisions(self, program, *, actor_id, actor_role, reason) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for decision_id in program.get("source_decision_ids") or []:
            try:
                results.append(self._gateway().reject(
                    decision_id,
                    tenant_id=str(program.get("tenant_id") or ""),
                    actor_id=actor_id,
                    actor_role=actor_role,
                    reason=reason,
                ))
            except DecisionAdvanceRefused as exc:
                raise ProgramConflictError(
                    f"Program stays under_review: decision {exc.decision_id} cannot be rejected: {exc.reason}"
                ) from exc
        return results

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

        # Role and identity checks (one policy: APPROVER_ONLY_ACTIONS /
        # HUMAN_ONLY_ACTIONS, shared with ``allowed_actions``).
        if clean_action in HUMAN_ONLY_ACTIONS and is_system_principal(clean_actor):
            raise ProgramForbiddenError(
                f"System identity {clean_actor!r} can never {clean_action} a program"
            )
        if clean_action in APPROVER_ONLY_ACTIONS and clean_role not in APPROVER_ROLES:
            raise ProgramValidationError(
                f"Action {action_id!r} requires approver role; actor holds {clean_role!r}"
            )

        known_actions = APPROVER_ONLY_ACTIONS.union({
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
        if clean_action == "create_mutation_rule":
            try:
                reject_bar_frequency_mutation(payload)
            except BarFrequencyMutationError as exc:
                raise ProgramValidationError(str(exc)) from exc

        effects: Dict[str, Any] = {}
        if not self._is_replay(current, clean_action, idempotency_key):
            effects = self._prepare_transition(
                current,
                action=clean_action,
                actor_id=clean_actor,
                actor_role=clean_role,
                payload=payload,
                note=note,
            )

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
                attach = (
                    base_status == ProgramStatus.UNDER_REVIEW.value
                    and not (base.get("review") or {}).get("approval_id")
                )
                if base_status != ProgramStatus.DRAFT.value and not attach:
                    raise ProgramConflictError(
                        f"Cannot submit review for program in status {base_status!r}; must be 'draft'"
                    )
                target_status = ProgramStatus.UNDER_REVIEW.value
                details = {
                    "approval_id": effects.get("approval_id"),
                    "approval_revision": base_rev + 1,
                    "approval_content_digest": effects.get("content_digest"),
                    "source_decision_ids": list(base.get("source_decision_ids") or []),
                    "config_snapshot_revision": base_rev,
                    "note": note or "Submitted for evolution review; awaiting a human governance decision",
                }

            elif clean_action == "approve_program":
                if base_status != ProgramStatus.UNDER_REVIEW.value:
                    raise ProgramConflictError(
                        f"Cannot approve program in status {base_status!r}; must be 'under_review'"
                    )
                target_status = ProgramStatus.ACTIVE.value
                linked = effects.get("decisions") or []
                details = {
                    "approval_id": effects.get("approval_id"),
                    "approval_decided_by": effects.get("approved_by"),
                    "activated_revision": base_rev + 1,
                    "decisions": linked,
                    "work_started": bool(linked),
                    "note": note or (
                        "Program approved by a human governance decision; linked decisions "
                        "prepared for the existing dispatch outbox"
                        if linked
                        else "Program approved and activated; no source decisions, so no work "
                        "started and no run was created"
                    ),
                }

            elif clean_action == "reject_program":
                if base_status != ProgramStatus.UNDER_REVIEW.value:
                    raise ProgramConflictError(
                        f"Cannot reject program in status {base_status!r}; must be 'under_review'"
                    )
                target_status = ProgramStatus.RETIRED.value
                details = {
                    "reason": effects.get("reason"),
                    "rejected_decisions": effects.get("decisions") or [],
                    "terminal": True,
                    "note": note or "Program review rejected by the operator; retired without activation",
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

            if clean_action == "submit_evolution_review":
                updated["review"] = {
                    "approval_id": details["approval_id"],
                    "approval_revision": details["approval_revision"],
                    "submitted_by": clean_actor,
                    "submitted_at": now,
                }
            elif clean_action == "approve_program":
                updated["approval"] = {
                    "approval_id": details["approval_id"],
                    "approved_by": details["approval_decided_by"],
                    "activated_by": clean_actor,
                    "activated_at": now,
                    "decisions": details["decisions"],
                }
            elif clean_action == "reject_program":
                updated["rejection"] = {
                    "reason": details["reason"],
                    "rejected_by": clean_actor,
                    "rejected_at": now,
                    "decisions": details["rejected_decisions"],
                }
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
