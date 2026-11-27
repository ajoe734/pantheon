"""Restart-safe coordinator for admitting a newly-created Persona to paper.

The BFF owns only the coordination ledger.  Capital, Registry, Governance,
Deployment, and Cron remain the authoritative writers for their objects.  In
particular, this module never creates or guesses a RuntimeBinding: dispatching
a Deployment saga means only that provisioning was admitted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from services.capital.risk_policy import (
    BoundPoolRiskPolicyResolution,
    capital_pool_approval_digest,
    compare_risk_policies,
    discover_approved_profile_risk_policy,
    resolve_pool_risk_policy_binding,
    risk_policy_digest,
    profile_outer_bounds,
    validate_drafted_pool_risk_policy,
)
from services.persona.risk_policy_draft import DraftInputs, DraftResult, make_drafter
from services.registry.strategy_artifact import (
    StrategyArtifactValidationError,
    canonical_market_context,
    classify_market_token,
    create_market_transition_revision,
    dataset_for_bar_frequency,
    strategy_artifact_checksum as _artifact_checksum,
)

from .persona_provisioning import (
    TERMINAL_STATES,
    PersonaProvisioningStore,
    ProvisioningLeaseLost,
    ProvisioningRecord,
    utc_now,
)



FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"
# Governance ApprovalDecisions proposed here are finite: Registry's approval
# verifier requires a current ``expires_at`` and Deployment re-verifies it.
# The bound is anchored to persisted owner timestamps, never to wall-clock at
# retry time, so a response-loss retry composes byte-identical bodies.
APPROVAL_TTL = timedelta(hours=24)
PAPER_RISK_RENEWAL_THRESHOLD = timedelta(hours=8)  # renew when less than this much validity remains
PAPER_RISK_RENEWAL_ATTEMPT_INTERVAL = timedelta(hours=1)  # at most one attempt per pool per hour
PAPER_RISK_RENEWAL_DECISION_INTERVAL = timedelta(hours=12)  # at most one new decision per pool per 12 h
PAPER_RISK_RENEWAL_REFERENCE = "paper_risk_policy_renewal"
MAX_PENDING_SUPERSEDE = 16  # bound of the pending paper policy proposals scanned per pool
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_UNKNOWN_SOURCE_COMMIT = "0" * 40
# Settlement currency of canonical markets whose paper venue settles in one
# currency (TW broker fills are TWD; US equities fill in USD). CRYPTO and FX
# quote per instrument, so they have no market-level currency.
_MARKET_SETTLEMENT_CURRENCY = {"US": "USD", "TW": "TWD"}


class OwnerTransport(Protocol):
    """Synchronous transport over authoritative owner HTTP APIs.

    ``get`` returns ``None`` for HTTP 404.  Other transport or owner failures
    are raised.  Mutation responses are deliberately not trusted: every
    mutation below is followed by an authoritative GET readback.
    """

    def get(self, owner: str, path: str) -> Mapping[str, Any] | None: ...

    def list(self, owner: str, path: str) -> "list[Mapping[str, Any]]": ...

    def post(
        self,
        owner: str,
        path: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def patch(
        self,
        owner: str,
        path: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


ScheduleRegistrar = Callable[[str, str, str], Mapping[str, Any]]
# persona_id -> owner receipt proving zero first-evaluation cron jobs remain.
ScheduleRemover = Callable[[str], Mapping[str, Any]]
# (plan_id, capital_pool_id) -> RuntimeBindings read back by plan and by pool.
RuntimeBindingReader = Callable[[str, str], "list[Mapping[str, Any]]"]
# persona_id -> authoritative Persona readback after archiving (None if absent).
PersonaArchiver = Callable[[str], "Mapping[str, Any] | None"]
# persona_id -> OpenClaw agent projection (own agent, SOUL, memory). Best effort:
# a failure is recorded as a readable degraded state and never blocks paper.
AgentProvisioner = Callable[[str], Mapping[str, Any]]

# RuntimeBinding statuses after which a binding no longer runs. This
# coordinator drives RuntimeBinding retirement; read projections import this
# definition instead of keeping their own copy.
TERMINAL_RUNTIME_BINDING_STATUSES = frozenset({"retired", "failed", "failed_inactive"})


CHAIN_CAPITAL_LABEL_KEYS = frozenset({"chain_id", "task_id", "fixture", "paper_only"})


def default_agent_provisioner(persona_id: str) -> Mapping[str, Any]:
    """Production provisioner: Persona owner record -> adapter trading-agent route.

    Imports are lazy so the coordinator stays importable without the BFF service
    and so tests can substitute the persona owner / adapter HTTP.
    """

    from integrations.openclaw.persona_agent_client import TradingPersonaAgentClient

    from .personas.service import _get_active_write_owner

    writer = _get_active_write_owner()
    getter = getattr(writer, "get_persona", None) if writer else None
    if not callable(getter):
        raise RuntimeError("persona_owner_unavailable_for_agent_provisioning")
    persona = getter(persona_id)
    if not isinstance(persona, Mapping):
        raise RuntimeError("persona_record_missing_for_agent_provisioning")
    client = TradingPersonaAgentClient()
    if not client.configured:
        raise RuntimeError("openclaw_adapter_unconfigured")
    return client.ensure_trading_persona_agent(dict(persona))


def default_persona_owner() -> Any:
    """Production Persona owner port (lazy so the coordinator stays importable)."""

    from .personas.service import _get_active_write_owner

    return _get_active_write_owner()


class PoolDispatchUncertain(Exception):
    """Pool envelope dispatch outcome could not be confirmed by readback."""

    def __init__(
        self, reason: str, receipt: dict[str, Any], *, terminal_rejection: bool = False
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.receipt = receipt
        # True only when an authoritative saga readback shows a terminal
        # failure/abort, which excludes pending execution of the dispatch.
        self.terminal_rejection = terminal_rejection


class PersonaProvisioningCoordinationError(RuntimeError):
    """An owner receipt could not prove the requested provisioning step."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str | None = None,
        absence_source: str | None = None,
        resolution_status: str | None = None,
        owner_decision_request: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.absence_source = absence_source
        self.resolution_status = resolution_status
        self.owner_decision_request = (
            deepcopy(dict(owner_decision_request))
            if isinstance(owner_decision_request, Mapping)
            else None
        )


class _TerminalSagaRejection(PersonaProvisioningCoordinationError):
    """Saga readback proves terminal rejection of the dispatched plan."""


@dataclass(frozen=True)
class ProvisioningIds:
    """Deterministic owner identities for one tenant-scoped Persona."""

    token: str
    capital_pool_id: str
    strategy_id: str
    baseline_registry_id: str
    baseline_approval_decision_id: str
    baseline_strategy_artifact_id: str
    baseline_strategy_artifact_approval_decision_id: str
    registry_id: str
    approval_decision_id: str
    strategy_artifact_id: str
    strategy_artifact_approval_decision_id: str
    persona_capital_binding_id: str
    deployment_plan_id: str
    deployment_saga_id: str
    version: str = "1.0.0"
    baseline_version: str = "0.0.1"

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


def deterministic_provisioning_ids(record: ProvisioningRecord) -> ProvisioningIds:
    """Derive stable IDs without accepting owner IDs from the client payload."""

    identity = json.dumps(
        {"tenant_id": record.tenant_id, "persona_id": record.persona_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    token = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    plan_id = f"plan-persona-paper-{token}"
    return ProvisioningIds(
        token=token,
        capital_pool_id=f"pool-persona-paper-{token}",
        strategy_id=f"strategy-persona-{token}",
        baseline_registry_id=f"reg-strategy-spec-persona-baseline-{token}",
        baseline_approval_decision_id=f"apv-persona-paper-baseline-{token}",
        baseline_strategy_artifact_id=f"artifact-persona-paper-baseline-{token}",
        baseline_strategy_artifact_approval_decision_id=(
            f"apv-persona-paper-artifact-baseline-{token}"
        ),
        registry_id=f"reg-strategy-spec-persona-{token}",
        approval_decision_id=f"apv-persona-paper-{token}",
        strategy_artifact_id=f"artifact-persona-paper-{token}",
        strategy_artifact_approval_decision_id=f"apv-persona-paper-artifact-{token}",
        persona_capital_binding_id=f"pcb-persona-paper-{token}",
        deployment_plan_id=plan_id,
        deployment_saga_id=f"deployment-saga-{plan_id}",
    )


def _path_id(value: str) -> str:
    return quote(value, safe="")


def _stable_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_commit() -> str:
    for name in ("BFF_COMMIT", "GIT_SHA", "PANTHEON_DEPLOY_SHA"):
        value = str(os.environ.get(name) or "").strip()
        if _GIT_SHA_RE.fullmatch(value):
            return value.lower()
    return _UNKNOWN_SOURCE_COMMIT


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PersonaProvisioningCoordinationError(f"{label} did not return an object")
    return deepcopy(dict(value))


def _registry_entry(receipt: Mapping[str, Any]) -> dict[str, Any]:
    wrapped = receipt.get("entry")
    value = wrapped if isinstance(wrapped, Mapping) else receipt
    return _mapping(value, label="Registry readback")


def _parse_owner_time(value: Any, *, label: str) -> datetime:
    """Parse an owner-persisted RFC 3339 timestamp; fail closed on anything else."""

    if not isinstance(value, str) or not value.strip():
        raise PersonaProvisioningCoordinationError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PersonaProvisioningCoordinationError(f"{label} is malformed: {value!r}") from exc
    if parsed.tzinfo is None:
        raise PersonaProvisioningCoordinationError(f"{label} is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def _format_owner_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _approval_expiry(created_at: Any, *, label: str) -> str:
    """Finite approval expiry: exactly ``APPROVAL_TTL`` after an owner timestamp."""

    return _format_owner_time(_parse_owner_time(created_at, label=label) + APPROVAL_TTL)


def _owner_digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonaProvisioningCoordinationError(f"{label} has no checksum")
    return value.strip()


class _PaperGateFailure(Exception):
    """The paper automated gate's Governance review/decide was refused or unavailable."""

    def __init__(self, kind: str, step: str, http_status: int | None, detail: str) -> None:
        super().__init__(f"{kind} at {step}: {detail}")
        self.kind, self.step, self.http_status, self.detail = kind, step, http_status, detail


class PersonaProvisioningCoordinator:
    """Coordinate owner writes as a GET-first, checkpointed paper admission saga."""

    def __init__(
        self,
        *,
        store: PersonaProvisioningStore,
        transport: OwnerTransport,
        schedule_registrar: ScheduleRegistrar,
        lease_owner: str,
        lease_seconds: int = 60,
        actor_id: str = "pantheon-persona-provisioner",
        governance_actor_id: str | None = None,
        schedule_remover: ScheduleRemover | None = None,
        runtime_binding_reader: RuntimeBindingReader | None = None,
        persona_archiver: PersonaArchiver | None = None,
        agent_provisioner: AgentProvisioner | None = None,
        persona_owner_provider: Callable[[], Any] | None = None,
        governance_proposer: OwnerTransport | None = None,
        governance_proposer_actor_id: str | None = None,
        pool_publisher_factory: Callable[[], Any] | None = None,
        risk_policy_drafter: Callable[[DraftInputs], DraftResult] | None = None,
    ) -> None:
        """``governance_actor_id`` is the verified subject of the separately
        issued Governance approval principal.  Strict Governance requires a
        proposal's ``owner_user_id`` and a review/decide ``actor_id`` to equal
        that subject, so only those three fields use it; every other owner
        write keeps ``actor_id``.  Human ``requested_by`` remains audit
        metadata and is never presented as an approval actor.  It defaults
        to ``actor_id`` so a caller that has not wired a distinct approval
        principal keeps one consistent service identity.
        """

        if not lease_owner.strip():
            raise ValueError("lease_owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not str(actor_id or "").strip():
            raise ValueError("actor_id is required")
        if governance_actor_id is not None and not str(governance_actor_id).strip():
            raise ValueError("governance_actor_id must be a non-empty subject when provided")
        self.store = store
        self.transport = transport
        self.schedule_registrar = schedule_registrar
        self.schedule_remover = schedule_remover
        self.runtime_binding_reader = runtime_binding_reader
        self.persona_archiver = persona_archiver
        self.agent_provisioner = agent_provisioner or default_agent_provisioner
        self.persona_owner_provider = persona_owner_provider or default_persona_owner
        self.governance_proposer = governance_proposer
        self.governance_proposer_actor_id = governance_proposer_actor_id
        self.pool_publisher_factory = pool_publisher_factory
        self.risk_policy_drafter = risk_policy_drafter
        self.risk_evidence_reader: Callable[[str, str], Any] | None = None
        self.lease_owner = lease_owner
        self.lease_seconds = lease_seconds
        self.actor_id = actor_id
        self.governance_actor_id = (
            governance_actor_id.strip() if governance_actor_id is not None else actor_id
        )

    def _checkpoint(self, record: ProvisioningRecord) -> ProvisioningRecord:
        """Persist a receipt while renewing this coordinator's lease."""

        return self.store.checkpoint(
            record,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )

    @staticmethod
    def _is_safe_early_failure(record: ProvisioningRecord) -> bool:
        """Allow replay only before binding or deployment ownership can exist."""

        if record.state != "failed" or record.compensation is not None:
            return False
        failed_step = str((record.error or {}).get("failed_step") or "")
        safe_steps = {
            "capital_pool",
            "baseline_strategy_spec_candidate",
            "baseline_approval_proposed",
            "baseline_approval_reviewed",
            "baseline_approval_decided",
            "baseline_strategy_spec_approved",
            "baseline_strategy_artifact_candidate",
            "baseline_strategy_artifact_approval_proposed",
            "baseline_strategy_artifact_approval_reviewed",
            "baseline_strategy_artifact_approval_decided",
            "baseline_strategy_artifact_approved",
            "strategy_spec_candidate",
            "approval_proposed",
            "approval_reviewed",
            "approval_decided",
            "strategy_spec_approved",
            "strategy_artifact_candidate",
            "strategy_artifact_approval_proposed",
            "strategy_artifact_approval_reviewed",
            "strategy_artifact_approval_decided",
            "strategy_artifact_approved",
        }
        if failed_step not in safe_steps:
            return False
        return not any(
            str(key).startswith(("persona_capital_binding", "deployment"))
            for key in record.references
        )

    @staticmethod
    def _needs_compensation_reconciliation(record: ProvisioningRecord) -> bool:
        compensation = record.compensation or {}
        failed_step = str((record.error or {}).get("failed_step") or "")
        may_have_unsafe_side_effect = bool(compensation) or failed_step.startswith(
            ("persona_capital_binding", "deployment", "schedule")
        ) or any(
            str(key).startswith(("persona_capital_binding", "deployment"))
            for key in record.references
        )
        return (
            record.state == "failed"
            and compensation.get("status") != "completed"
            and may_have_unsafe_side_effect
        )

    def coordinate(
        self,
        record: ProvisioningRecord,
        *,
        dry_run: bool = False,
    ) -> ProvisioningRecord:
        """Admit ``record`` to paper or persist a terminal failure.

        ``dry_run`` is a pure preview: no store method, owner transport, or
        schedule registrar is called.  A successful live call remains in
        ``provisioning`` at ``schedule_registered``; runtime and worker
        readbacks are separate lifecycle evidence before ``paper_running``.
        """

        if dry_run:
            return self._preview(record)

        existing = self.store.get(record.tenant_id, record.idempotency_key)
        if existing is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning must be reserved before coordination"
            )
        safe_early_retry = self._is_safe_early_failure(existing)
        if existing.state in TERMINAL_STATES and not safe_early_retry:
            if self._needs_compensation_reconciliation(existing):
                return self._resume_failure_compensation(existing)
            return existing

        active = self.store.acquire(
            record.tenant_id,
            record.idempotency_key,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )
        if active is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning record is leased by another coordinator"
            )

        if safe_early_retry:
            active.state = "provisioning"
            active.current_step = "safe_early_failure_retry_started"
            active.error = None
            active.compensation = None
            active.result = None
            active = self._checkpoint(active)

        failed_step = "capital_pool"
        ids = deterministic_provisioning_ids(active)
        try:
            symbols, _, _ = self._paper_universe(active)
            self._explicit_market(active, symbols)
            active = self._coordinate_capital_pool(active, ids)

            failed_step = "baseline_strategy_spec_candidate"
            active = self._coordinate_strategy_spec(active, ids, baseline=True)

            failed_step = "baseline_approval_proposed"
            active = self._coordinate_approval_proposal(active, ids, baseline=True)

            failed_step = "baseline_approval_reviewed"
            active = self._coordinate_approval_review(active, ids, baseline=True)

            failed_step = "baseline_approval_decided"
            active = self._coordinate_approval_decision(active, ids, baseline=True)

            failed_step = "baseline_strategy_spec_approved"
            active = self._coordinate_registry_approval(active, ids, baseline=True)

            failed_step = "baseline_strategy_artifact_candidate"
            active = self._coordinate_strategy_artifact(active, ids, baseline=True)

            failed_step = "baseline_strategy_artifact_approval_proposed"
            active = self._coordinate_approval_proposal(
                active,
                ids,
                baseline=True,
                strategy_artifact=True,
            )

            failed_step = "baseline_strategy_artifact_approval_reviewed"
            active = self._coordinate_approval_review(
                active,
                ids,
                baseline=True,
                strategy_artifact=True,
            )

            failed_step = "baseline_strategy_artifact_approval_decided"
            active = self._coordinate_approval_decision(
                active,
                ids,
                baseline=True,
                strategy_artifact=True,
            )

            failed_step = "baseline_strategy_artifact_approved"
            active = self._coordinate_registry_approval(
                active,
                ids,
                baseline=True,
                strategy_artifact=True,
            )

            failed_step = "strategy_spec_candidate"
            active = self._coordinate_strategy_spec(active, ids)

            failed_step = "approval_proposed"
            active = self._coordinate_approval_proposal(active, ids)

            failed_step = "approval_reviewed"
            active = self._coordinate_approval_review(active, ids)

            failed_step = "approval_decided"
            active = self._coordinate_approval_decision(active, ids)

            failed_step = "strategy_spec_approved"
            active = self._coordinate_registry_approval(active, ids)

            failed_step = "strategy_artifact_candidate"
            active = self._coordinate_strategy_artifact(active, ids)

            failed_step = "strategy_artifact_approval_proposed"
            active = self._coordinate_approval_proposal(
                active,
                ids,
                strategy_artifact=True,
            )

            failed_step = "strategy_artifact_approval_reviewed"
            active = self._coordinate_approval_review(
                active,
                ids,
                strategy_artifact=True,
            )

            failed_step = "strategy_artifact_approval_decided"
            active = self._coordinate_approval_decision(
                active,
                ids,
                strategy_artifact=True,
            )

            failed_step = "strategy_artifact_approved"
            active = self._coordinate_registry_approval(
                active,
                ids,
                strategy_artifact=True,
            )

            failed_step = "persona_capital_binding_created"
            active = self._coordinate_binding_create(active, ids)

            failed_step = "persona_capital_binding_active"
            active = self._coordinate_binding_activate(active, ids)

            failed_step = "deployment_plan"
            active = self._coordinate_deployment_plan(active, ids)

            failed_step = "deployment_dispatch"
            active = self._coordinate_deployment_dispatch(active, ids)

            failed_step = "schedule_registration"
            active = self._coordinate_schedule(active, ids)

            active.state = "provisioning"
            active.current_step = "schedule_registered"
            active.error = None
            active.compensation = None
            active.result = {
                "status": "dispatch_admitted",
                "lifecycle_state": "provisioning",
                "paper_running": False,
                "persona_id": active.persona_id,
                "capital_pool_id": ids.capital_pool_id,
                "persona_capital_binding_id": ids.persona_capital_binding_id,
                "rollback_registry_id": ids.baseline_strategy_artifact_id,
                "rollback_strategy_spec_registry_id": ids.baseline_registry_id,
                "rollback_approval_decision_id": (
                    ids.baseline_strategy_artifact_approval_decision_id
                ),
                "strategy_spec_registry_id": ids.registry_id,
                "registry_id": ids.strategy_artifact_id,
                "strategy_artifact_id": ids.strategy_artifact_id,
                "approval_decision_id": ids.strategy_artifact_approval_decision_id,
                "deployment_plan_id": ids.deployment_plan_id,
                "deployment_saga_id": ids.deployment_saga_id,
                "first_evaluation_workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
            }
            active = self._checkpoint(active)
        except Exception as exc:  # noqa: BLE001 - downstream boundaries are injected
            active = self._record_failure(active, ids, failed_step=failed_step, error=exc)
        else:
            # Outside the saga: an enrolment problem is a readable readback on
            # the record and never fails or compensates paper admission.
            active = self._enroll_in_place(active)
        return self.store.release(
            active,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )

    def _paper_enrollment(self, record: ProvisioningRecord) -> Any:
        from .persona_paper_enrollment import PersonaPaperEnrollment

        from .persona_paper_enrollment import HttpGovernanceProposer

        ids = deterministic_provisioning_ids(record)
        proposer = self.governance_proposer or HttpGovernanceProposer(
            tenant_id=record.tenant_id
        )
        proposer_id = self.governance_proposer_actor_id or HttpGovernanceProposer.actor_id()
        owner = self.persona_owner_provider()
        if owner is None or not all(
            callable(getattr(owner, name, None))
            for name in (
                "get_persona",
                "advance_lifecycle_governed",
                "get_strategy_pool",
                "seed_strategy_pool",
                "get_harness",
                "initialize_harness",
            )
        ):
            raise RuntimeError("persona_owner_unavailable_for_paper_enrollment")
        if self.runtime_binding_reader is None:
            raise RuntimeError("runtime_binding_reader_unavailable_for_paper_enrollment")
        return PersonaPaperEnrollment(
            tenant_id=record.tenant_id,
            persona_id=record.persona_id,
            capital_pool_id=ids.capital_pool_id,
            persona_capital_binding_id=ids.persona_capital_binding_id,
            deployment_plan_id=ids.deployment_plan_id,
            persona_owner=owner,
            transport=self.transport,
            runtime_binding_reader=self.runtime_binding_reader,
            governance_actor_id=self.governance_actor_id,
            proposer=proposer,
            proposer_actor_id=proposer_id,
            pool_publisher=self._first_publish_callable(record, owner),
        )

    def _first_publish_callable(self, record: ProvisioningRecord, owner: Any) -> Callable[[int], Any]:
        """Seeded-pool publish through the existing ``PersonaPoolPublisher`` (read-before-write)."""

        def publish(pool_version: int) -> Any:
            if self.pool_publisher_factory is not None:
                publisher = self.pool_publisher_factory()
            else:
                from .persona_pool_publisher import (
                    PersonaPoolPublisher,
                    build_publish_records,
                )

                class _CurrentPool:
                    """Seeded v1 is the owner's current pool; any other version is not this publish."""

                    @staticmethod
                    def current(persona_id: str, tenant_id: str) -> Any:
                        value = owner.get_strategy_pool(persona_id, tenant_id=tenant_id)
                        if value is None:
                            from services.control_plane.persona.persona_strategy_pool import PoolNotFound

                            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
                        return dict(value)

                    @classmethod
                    def version(cls, persona_id: str, tenant_id: str, number: int) -> Any:
                        value = cls.current(persona_id, tenant_id)
                        if int(value.get("pool_version") or 0) != int(number):
                            from services.control_plane.persona.persona_strategy_pool import PoolNotFound

                            raise PoolNotFound(f"pool version {number} is not the seeded version")
                        return value

                publisher = PersonaPoolPublisher(
                    coordinator=self, pool_store=_CurrentPool(), records=build_publish_records()
                )
            return publisher.publish(
                tenant_id=record.tenant_id,
                persona_id=record.persona_id,
                requested_by=self.actor_id,
                pool_version=pool_version,
            )

        return publish

    def _is_non_trading_paper_enrollment_record(self, record: ProvisioningRecord) -> bool:
        """True when a provisioning record belongs to a non-trading persona or trader servant paper book."""
        from .personas import fleet_classification as _fleet_cls

        raw_payload = getattr(record, "request_payload", None)
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        raw_result = getattr(record, "result", None)
        result = raw_result if isinstance(raw_result, Mapping) else {}
        raw_references = getattr(record, "references", None)
        references = raw_references if isinstance(raw_references, Mapping) else {}
        to_dict = getattr(record, "to_dict", None)
        record_dict = to_dict() if callable(to_dict) else {}
        if _fleet_cls.is_non_trading_persona(payload) or (
            isinstance(record_dict, Mapping) and _fleet_cls.is_non_trading_persona(record_dict)
        ):
            return True
        for container in (
            payload,
            payload.get("paper_book") if isinstance(payload.get("paper_book"), Mapping) else {},
            result,
            references,
        ):
            if str(container.get("book_kind") or "").strip().lower() == "trader_servant_paper_book":
                return True
        if (
            str(getattr(record, "idempotency_key", None) or "").strip().startswith("trader-paper-book:")
            or str(payload.get("mandate") or "").strip().lower() == "user_private_agora_servant_paper_book"
        ):
            return True
        try:
            provider = getattr(self, "persona_owner_provider", None)
            owner = provider() if callable(provider) else None
            getter = getattr(owner, "get_persona", None) if owner is not None else None
            persona_id = str(getattr(record, "persona_id", None) or "").strip()
            persona = getter(persona_id) if callable(getter) and persona_id else None
        except Exception:
            persona = None
        if isinstance(persona, Mapping):
            if _fleet_cls.is_non_trading_persona(persona):
                return True
            meta = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}
            paper_book = meta.get("paper_book") if isinstance(meta.get("paper_book"), Mapping) else {}
            if (
                str(meta.get("book_kind") or paper_book.get("book_kind") or "").strip().lower()
                == "trader_servant_paper_book"
            ):
                return True
        return False

    def _enroll_in_place(self, record: ProvisioningRecord) -> ProvisioningRecord:
        """Enrol under the lease this coordinator already holds; readback on the ledger."""

        if self._is_non_trading_paper_enrollment_record(record):
            return record
        try:
            outcome = self._paper_enrollment(record).enroll().to_dict()
        except Exception as exc:  # noqa: BLE001 - never blocks paper admission
            outcome = {
                "persona_id": record.persona_id,
                "status": "unavailable",
                "reason": f"{exc.__class__.__name__}: {exc}"[:300],
                "lifecycle_state": None,
                "steps": {},
            }
        previous = record.references.get("paper_enrollment")
        if (
            isinstance(previous, dict)
            and previous.get("status") == "enrolled"
            and outcome.get("status") in {"not_eligible", "refused", "unavailable"}
        ):
            # Never overwrite an enrolled readback; keep why this pass could not finish.
            kept = dict(previous)
            steps = dict(kept.get("steps") or {})
            steps["publish"] = {
                "status": "unfinished",
                "reason": f"{outcome.get('status')}:{outcome.get('reason')}"[:300],
                "attempted_at": outcome.get("attempted_at"),
            }
            kept["steps"] = steps
            kept["attempted_at"] = outcome.get("attempted_at") or kept.get("attempted_at")
            outcome = kept
        if previous == outcome:
            return record
        record.references["paper_enrollment"] = outcome
        return self._checkpoint(record)

    def reconcile_paper_enrollment(self, record: ProvisioningRecord) -> ProvisioningRecord:
        """Provisioning reconcile step: enrol an already paper-provisioned persona.

        Idempotent and safe under restart or concurrent replicas: it takes the
        record's lease (another holder means skip) and every owner write is
        GET-first.  Failed, compensated, retired and unreserved records are not
        enrolled.
        """

        if record.state not in {"provisioning", "succeeded"}:
            return record
        if self._is_non_trading_paper_enrollment_record(record):
            return record
        active = self.store.acquire(
            record.tenant_id,
            record.idempotency_key,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )
        if active is None:
            return record
        try:
            active = self._enroll_in_place(active)
        finally:
            active = self.store.release(
                active, lease_owner=self.lease_owner, lease_seconds=self.lease_seconds
            )
        return active

    def provision_persona_agent(self, persona_id: str) -> dict[str, Any]:
        """Create/update the persona's own OpenClaw agent without gating paper.

        Callers invoke this only after the Persona owner record is persisted
        and the schedule is admitted, so no outcome here can fail or
        compensate the paper admission. Any problem becomes a readable degraded
        state carrying its reason; success carries the agent projection.
        """

        try:
            agent = dict(self.agent_provisioner(persona_id))
            memory = agent.get("memory") if isinstance(agent.get("memory"), Mapping) else {}
            degraded = memory.get("status") == "degraded"
            return {
                "status": "degraded" if degraded else "ready",
                "agent_id": agent.get("agent_id"),
                "agent_status": agent.get("status"),
                "model": agent.get("model"),
                "memory": dict(memory),
                **({"reason": str(memory.get("reason") or "memory_degraded")[:300]} if degraded else {}),
            }
        except Exception as exc:  # noqa: BLE001 - agent provisioning never blocks paper
            return {
                "status": "degraded",
                "reason": f"{exc.__class__.__name__}: {exc}"[:300],
            }

    def _resume_failure_compensation(
        self,
        record: ProvisioningRecord,
    ) -> ProvisioningRecord:
        """Resume only fail-closed work; never replay unsafe forward steps."""

        active = self.store.acquire(
            record.tenant_id,
            record.idempotency_key,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )
        if active is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning failure is leased by another coordinator"
            )
        active.state = "failed"
        failed_step = str((active.error or {}).get("failed_step") or "unknown")
        active = self._compensate_failure(
            active,
            deterministic_provisioning_ids(active),
            failed_step=failed_step,
        )
        return self.store.release(
            active,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )

    def reconcile_failure_compensation(
        self,
        record: ProvisioningRecord,
    ) -> ProvisioningRecord:
        """Resume compensation only; never turn a terminal failure into retry.

        ``coordinate`` deliberately permits safe early provisioning failures to
        be retried by an explicit create replay.  A lifecycle readback worker
        has a narrower mandate: it may finish fail-closed compensation, but it
        must never restart forward provisioning.  Keep that distinction in a
        dedicated public entrypoint so callers cannot accidentally select the
        broader replay semantics.
        """

        existing = self.store.get(record.tenant_id, record.idempotency_key)
        if existing is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning must be reserved before compensation"
            )
        if not self._needs_compensation_reconciliation(existing):
            return existing
        return self._resume_failure_compensation(existing)

    def retire_case(
        self,
        record: ProvisioningRecord,
        *,
        reason: str = "dev paper release case retired",
    ) -> ProvisioningRecord:
        """Retire the whole case this coordinator created; symmetric to create.

        Every step is GET-first and idempotent, uses the owning service's
        existing API, and counts only once an authoritative readback proves
        it.  A step that cannot be proven leaves retirement ``pending`` under
        ``references.retirement`` (the ledger state is not changed) so a
        replay continues from it; ``pending`` never raises.  Only when all
        steps are proven does the ledger become the terminal ``retired``
        state.  The capital pool kill-switch safe mode and Registry strategy
        artifacts/approvals are deliberately left untouched.
        """

        existing = self.store.get(record.tenant_id, record.idempotency_key)
        if existing is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning must be reserved before retirement"
            )
        if existing.state == "retired":
            return existing
        active = self.store.acquire(
            record.tenant_id,
            record.idempotency_key,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )
        if active is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning record is leased by another coordinator"
            )
        ids = deterministic_provisioning_ids(active)
        prior = active.references.get("retirement")
        retirement: dict[str, Any] = dict(prior) if isinstance(prior, Mapping) else {}
        retirement.setdefault("reason", reason)
        retirement.setdefault("started_at", utc_now())
        retirement["status"] = "pending"
        retirement.pop("pending_step", None)
        retirement.pop("pending_reason", None)
        retirement["attempts"] = int(retirement.get("attempts") or 0) + 1
        active.references["retirement"] = retirement
        active.current_step = "retirement_started"
        active = self._checkpoint(active)

        steps: list[tuple[str, Callable[[ProvisioningRecord], tuple[ProvisioningRecord, Any]]]] = [
            ("first_evaluation_schedule", lambda r: (r, self._retire_schedule(r))),
            ("compensation", lambda r: self._retire_compensation(r, ids)),
            ("deployment_saga", lambda r: (r, self._retire_saga(r, ids))),
            ("runtime_bindings", lambda r: (r, self._retire_runtime_bindings(r, ids))),
            ("persona_capital_binding", lambda r: (r, self._retire_pcb(r, ids))),
            ("capital_pool", lambda r: (r, self._retire_pool(r, ids))),
            ("persona", lambda r: (r, self._retire_persona(r))),
        ]
        for name, run in steps:
            try:
                active, receipt = run(active)
            except ProvisioningLeaseLost:
                raise
            except Exception as exc:  # noqa: BLE001 - unproven step stays pending
                retirement = dict(active.references["retirement"])
                retirement["status"] = "pending"
                retirement["pending_step"] = name
                retirement["pending_reason"] = str(exc) or exc.__class__.__name__
                retirement["pending_at"] = utc_now()
                active.references["retirement"] = retirement
                active.current_step = f"retirement_{name}_pending"
                active = self._checkpoint(active)
                return self.store.release(
                    active,
                    lease_owner=self.lease_owner,
                    lease_seconds=self.lease_seconds,
                )
            retirement = dict(active.references["retirement"])
            retirement[name] = {"receipt": deepcopy(receipt), "confirmed_at": utc_now()}
            active.references["retirement"] = retirement
            active.current_step = f"retirement_{name}_confirmed"
            active = self._checkpoint(active)

        retirement = dict(active.references["retirement"])
        retirement["status"] = "retired"
        retirement.pop("pending_step", None)
        retirement.pop("pending_reason", None)
        retirement["retired_at"] = utc_now()
        active.references["retirement"] = retirement
        active.state = "retired"
        active.current_step = "retired"
        active = self._checkpoint(active)
        return self.store.release(
            active,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )

    def _retire_schedule(self, record: ProvisioningRecord) -> Mapping[str, Any]:
        if self.schedule_remover is None:
            raise PersonaProvisioningCoordinationError(
                "first-evaluation schedule remover is not configured"
            )
        receipt = _mapping(
            self.schedule_remover(record.persona_id),
            label="first-evaluation schedule remover",
        )
        if receipt.get("registered") is not False:
            raise PersonaProvisioningCoordinationError(
                "first-evaluation schedule removal lacks zero-owner readback"
            )
        return receipt

    def _retire_compensation(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> tuple[ProvisioningRecord, Any]:
        """Finish an unfinished failure compensation before retiring."""

        if not self._needs_compensation_reconciliation(record):
            return record, {"status": "not_required"}
        failed_step = str((record.error or {}).get("failed_step") or "unknown")
        compensated = self._compensate_failure(record, ids, failed_step=failed_step)
        if (compensated.compensation or {}).get("status") != "completed":
            reason = (compensated.compensation or {}).get("terminal_reason") or (
                "failure compensation is not complete"
            )
            raise PersonaProvisioningCoordinationError(str(reason))
        return compensated, {"status": "completed", "state": compensated.state}

    def _retire_saga(self, record: ProvisioningRecord, ids: ProvisioningIds) -> Mapping[str, Any]:
        saga_path = f"/api/deployment/sagas/{_path_id(ids.deployment_saga_id)}"
        reason = str(record.references["retirement"]["reason"])

        def read() -> dict[str, Any] | None:
            saga = self._owner_get("deployment", saga_path)
            if saga is not None and (
                saga.get("saga_id") != ids.deployment_saga_id
                or saga.get("plan_id") != ids.deployment_plan_id
            ):
                raise PersonaProvisioningCoordinationError(
                    "deployment retirement readback has the wrong saga identity"
                )
            return saga

        def post(action: str, body: Mapping[str, Any]) -> None:
            try:
                self.transport.post("deployment", f"{saga_path}/{action}", dict(body))
            except Exception:  # noqa: BLE001 - response loss reconciled by GET
                pass

        saga = read()
        if saga is None:
            return {"status": "no_saga"}
        status = str(saga.get("status") or "")
        step = str(saga.get("current_step") or "")
        if status in {"awaiting_binding", "awaiting_runtime_load"}:
            post("failure", {"reason": f"{reason}: retired before runtime was active"})
            saga = read()
            raise PersonaProvisioningCoordinationError(
                "deployment saga failure requested; waiting for compensation "
                f"(status={(saga or {}).get('status')!r})"
            )
        if status in {"compensating", "retiring"}:
            raise PersonaProvisioningCoordinationError(
                f"deployment saga is {status}; retirement stays pending"
            )
        if status == "aborted":
            return {"status": "terminal", "saga_status": status}
        if status == "failed":
            command = str((saga.get("compensation") or {}).get("command_type") or "")
            if command == "mark_binding_failed_inactive":
                return {"status": "terminal", "saga_status": status, "command_type": command}
            if step != "compensated":
                raise PersonaProvisioningCoordinationError(
                    f"deployment saga failed at {step!r} and is not yet compensated"
                )
        if status in {"completed", "failed"}:
            post("retire", {"reason": reason})
            saga = read()
            status = str((saga or {}).get("status") or "")
        if status == "retired":
            return {"status": "retired", "saga_status": status}
        raise PersonaProvisioningCoordinationError(
            f"deployment saga retirement is not confirmed (status={status!r})"
        )

    def _retire_runtime_bindings(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> Mapping[str, Any]:
        if self.runtime_binding_reader is None:
            raise PersonaProvisioningCoordinationError(
                "RuntimeBinding reader is not configured"
            )
        bindings = self.runtime_binding_reader(ids.deployment_plan_id, ids.capital_pool_id)
        observed: dict[str, str] = {}
        for binding in bindings or []:
            binding_id = str(binding.get("binding_id") or binding.get("id") or "")
            observed[binding_id] = str(binding.get("status") or "")
        live = {k: v for k, v in observed.items() if v not in TERMINAL_RUNTIME_BINDING_STATUSES}
        if live:
            raise PersonaProvisioningCoordinationError(
                f"RuntimeBindings are not retired: {sorted(live.items())}"
            )
        return {"bindings": observed}

    def _retire_pcb(self, record: ProvisioningRecord, ids: ProvisioningIds) -> Mapping[str, Any]:
        return self._revoke_binding(
            persona_id=record.persona_id,
            pool_id=ids.capital_pool_id,
            binding_id=ids.persona_capital_binding_id,
        )

    def _revoke_binding(
        self,
        *,
        persona_id: str,
        pool_id: str,
        binding_id: str,
    ) -> Mapping[str, Any]:
        path = f"/api/bindings/{_path_id(binding_id)}"
        binding = self._owner_get("capital", path)
        if binding is None:
            return {"status": "absent"}
        self._check_binding_identity(
            binding, persona_id=persona_id, pool_id=pool_id, binding_id=binding_id
        )
        if binding.get("status") not in {"revoked", "expired"}:
            try:
                self.transport.patch(
                    "capital",
                    f"{path}/status",
                    {
                        "actor_id": self.actor_id,
                        "actor_role": "persona.admin",
                        "status": "revoked",
                    },
                )
            except Exception:  # noqa: BLE001 - response loss reconciled by GET
                pass
            binding = self._owner_get("capital", path)
            if binding is None or binding.get("status") not in {"revoked", "expired"}:
                raise PersonaProvisioningCoordinationError(
                    "persona capital binding revoke has no authoritative readback"
                )
            self._check_binding_identity(
                binding, persona_id=persona_id, pool_id=pool_id, binding_id=binding_id
            )
        return {"status": binding.get("status"), "binding_id": binding_id}

    def _retire_pool(self, record: ProvisioningRecord, ids: ProvisioningIds) -> Mapping[str, Any]:
        return self._archive_pool(pool_id=ids.capital_pool_id)

    def _archive_pool(self, *, pool_id: str) -> Mapping[str, Any]:
        # Only the pool status is written; kill-switch safe mode is left as is.
        path = f"/api/capital-pools/{_path_id(pool_id)}"
        pool = self._owner_get("capital", path)
        if pool is None:
            return {"status": "absent"}
        if pool.get("pool_id") not in {None, pool_id}:
            raise PersonaProvisioningCoordinationError(
                "capital pool readback has the wrong identity"
            )
        if pool.get("status") != "archived":
            try:
                self.transport.patch(
                    "capital",
                    f"{path}/status",
                    {
                        "actor_id": self.actor_id,
                        "actor_role": "capital.admin",
                        "status": "archived",
                    },
                )
            except Exception:  # noqa: BLE001 - response loss reconciled by GET
                pass
            pool = self._owner_get("capital", path)
            if pool is None or pool.get("status") != "archived":
                raise PersonaProvisioningCoordinationError(
                    "capital pool archive has no authoritative readback"
                )
        return {"status": "archived", "pool_id": pool_id}

    def _retire_persona(self, record: ProvisioningRecord) -> Mapping[str, Any]:
        if self.persona_archiver is None:
            raise PersonaProvisioningCoordinationError("Persona archiver is not configured")
        persona = self.persona_archiver(record.persona_id)
        if persona is None:
            return {"status": "absent"}
        if persona.get("status") != "archived":
            raise PersonaProvisioningCoordinationError(
                "persona archive has no authoritative readback"
            )
        return {"status": "archived", "persona_id": record.persona_id}

    def transition_legacy_persona_market(
        self,
        record: ProvisioningRecord,
        *,
        market: str | None = None,
        new_version: str = "1.0.1",
    ) -> ProvisioningRecord:
        """Create and approve a governed child revision for a legacy Persona artifact.

        Preserves every byte, checksum, and version of the parent artifact (AC2).
        Traces market authority from persisted owner request or explicit parameter,
        failing closed if unspecified or contradictory (AC3).
        Coordinates low-risk Governance approval and Registry advance under
        zero-capital bounds, updating durable coordination receipts (AC4).
        """
        symbols, _, _ = self._paper_universe(record)

        # 1. Trace and validate market authority from persisted owner request and caller parameter
        persisted_raw = record.request_payload.get("market")
        persisted_market = None
        if persisted_raw is not None and str(persisted_raw).strip():
            try:
                persisted_market = canonical_market_context(str(persisted_raw), symbols=symbols)
            except StrategyArtifactValidationError as exc:
                raise PersonaProvisioningCoordinationError(
                    f"Invalid persisted owner request market context: {exc}"
                ) from exc

        supplied_market = None
        if market is not None:
            if not str(market).strip():
                raise PersonaProvisioningCoordinationError(
                    "Supplied market parameter must not be empty or whitespace"
                )
            try:
                supplied_market = canonical_market_context(str(market), symbols=symbols)
            except StrategyArtifactValidationError as exc:
                raise PersonaProvisioningCoordinationError(
                    f"Invalid supplied market context: {exc}"
                ) from exc

        # Fail closed on contradictory market contexts between persisted request and caller
        if persisted_market is not None and supplied_market is not None:
            if supplied_market != persisted_market:
                raise PersonaProvisioningCoordinationError(
                    f"Contradictory market context: supplied market '{supplied_market}' "
                    f"differs from persisted owner request '{persisted_market}'"
                )
            canonical_market = persisted_market
        elif persisted_market is not None:
            canonical_market = persisted_market
        elif supplied_market is not None:
            canonical_market = supplied_market
        else:
            raise PersonaProvisioningCoordinationError(
                "No authoritative owner market context found for legacy persona transition"
            )

        parent_receipt = record.references.get("strategy_artifact_approved")
        if not parent_receipt:
            raise PersonaProvisioningCoordinationError(
                "Legacy Persona has no approved strategy artifact to transition"
            )
        parent_entry = _registry_entry(parent_receipt)
        parent_artifact = parent_entry.get("metadata", {}).get("strategy_artifact")
        if not isinstance(parent_artifact, Mapping):
            raise PersonaProvisioningCoordinationError(
                "Legacy strategy artifact receipt is missing metadata.strategy_artifact"
            )
        parent_parameters = parent_artifact.get("parameters") or {}
        parent_market_raw = parent_parameters.get("market")
        if parent_market_raw:
            try:
                parent_market = canonical_market_context(str(parent_market_raw), symbols=symbols)
            except StrategyArtifactValidationError as exc:
                raise PersonaProvisioningCoordinationError(
                    f"Invalid parent strategy artifact market context: {exc}"
                ) from exc
            if parent_market == canonical_market:
                return record
            raise PersonaProvisioningCoordinationError(
                f"Contradictory market context: target market '{canonical_market}' "
                f"contradicts parent artifact market '{parent_market}'"
            )

        self._paper_universe(record, market=canonical_market)

        pool_currency = (record.references.get("capital_pool") or {}).get("currency")
        settlement = _MARKET_SETTLEMENT_CURRENCY.get(canonical_market)
        if settlement is None or pool_currency != settlement:
            raise PersonaProvisioningCoordinationError(
                f"Capital pool currency {pool_currency!r} does not settle market "
                f"{canonical_market!r}"
            )

        active = self.store.acquire(
            record.tenant_id,
            record.idempotency_key,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )
        if active is None:
            raise PersonaProvisioningCoordinationError(
                "Persona provisioning record is leased by another coordinator"
            )

        try:
            ids = deterministic_provisioning_ids(active)
            parent_id = str(parent_artifact.get("artifact_id") or ids.strategy_artifact_id).strip()
            child_artifact_id = f"{parent_id}-rev1"
            child_decision_id = f"apv-transition-{ids.token}"
            source_run_id = f"persona-provisioning-transition-{ids.token}"

            child_artifact = create_market_transition_revision(
                parent_artifact,
                market=canonical_market,
                new_artifact_id=child_artifact_id,
                new_version=new_version,
                source_run_ids=[source_run_id],
                parent_registry_id=parent_id,
            )

            candidate_payload = {
                "registry_id": child_artifact_id,
                "artifact_state": "candidate",
                "strategy_artifact": child_artifact,
                "producer_run_id": source_run_id,
                "evaluation_summary": {
                    "admission": "legacy_market_transition",
                    "risk_level": "low",
                    "execution_context": "paper",
                    "capital_scale_pct": 0.0,
                    "source_strategy_spec_id": ids.registry_id,
                },
                "rollback_target": parent_id,
                "metadata": {
                    "tenant_id": active.tenant_id,
                    "persona_id": active.persona_id,
                    "capital_pool_id": ids.capital_pool_id,
                    "persona_capital_binding_id": ids.persona_capital_binding_id,
                    "parent_registry_id": parent_id,
                    "execution_context": "paper",
                    "capital_scale_pct": 0.0,
                    "requested_by": self._requested_by(active),
                    "market": canonical_market,
                    "transition_kind": "legacy_market_addition",
                },
            }

            def validate_candidate(receipt: Mapping[str, Any]) -> None:
                state = str(_registry_entry(receipt).get("artifact_state") or "")
                if state not in {"candidate", "approved"}:
                    raise PersonaProvisioningCoordinationError(
                        "Transition StrategyArtifact create readback is neither candidate nor approved"
                    )

            candidate_receipt = self._create_then_get(
                owner="registry",
                get_path=f"/api/registry/strategy-artifacts/{_path_id(child_artifact_id)}",
                post_path="/api/registry/strategy-artifacts",
                payload=candidate_payload,
                validate=validate_candidate,
            )
            active = self._checkpoint_receipt(
                active,
                step="transition_strategy_artifact_candidate_readback",
                key="transition_strategy_artifact_candidate",
                receipt=candidate_receipt,
            )

            cand_entry = _registry_entry(candidate_receipt)
            candidate_digest = _owner_digest(
                cand_entry.get("checksum"),
                label=f"Transition RegistryEntry {child_artifact_id} readback",
            )
            expires_at = _approval_expiry(
                cand_entry.get("created_at"),
                label=f"Transition RegistryEntry {child_artifact_id} readback created_at",
            )
            proposal_payload = {
                "decision_id": child_decision_id,
                "expected_version": 0,
                "target_type": "registry_entry",
                "target_id": child_artifact_id,
                "target_version": new_version,
                "risk_level": "low",
                "capital_pool_id": ids.capital_pool_id,
                "persona_id": active.persona_id,
                "tenant_id": active.tenant_id,
                "owner_user_id": self.governance_actor_id,
                "proposal_id": f"persona-provisioning-transition-{ids.token}",
                "proposal_revision": 1,
                "proposal_content_digest": active.request_hash,
                "validation_result_digest": _stable_hash(
                    {
                        "registry_id": child_artifact_id,
                        "risk_level": "low",
                        "candidate_digest": candidate_digest,
                    }
                ),
                "candidate_digest": candidate_digest,
                "expires_at": expires_at,
            }

            def validate_proposal(receipt: Mapping[str, Any]) -> None:
                self._validate_approval_identity(
                    receipt,
                    active,
                    ids,
                    registry_id=child_artifact_id,
                    decision_id=child_decision_id,
                    version=new_version,
                    candidate_digest=candidate_digest,
                    expires_at=expires_at,
                )

            proposal_receipt = self._create_then_get(
                owner="governance",
                get_path=f"/api/governance/approvals/{_path_id(child_decision_id)}",
                post_path="/api/governance/approvals",
                payload=proposal_payload,
                validate=validate_proposal,
            )
            active = self._checkpoint_receipt(
                active,
                step="transition_approval_proposed_readback",
                key="transition_approval_proposed",
                receipt=proposal_receipt,
            )

            gov_path = f"/api/governance/approvals/{_path_id(child_decision_id)}"
            review_receipt = self._transition_then_get(
                owner="governance",
                get_path=gov_path,
                post_path=f"{gov_path}/review",
                payload=lambda curr: {
                    "actor_role": "automated_gate",
                    "actor_id": self.governance_actor_id,
                    "expected_version": int((curr or {}).get("version") or 0),
                },
                ready=lambda r: str(r.get("decision_state") or "") in {"under_review", "decided"},
                validate=lambda r: None,
            )

            decide_receipt = self._transition_then_get(
                owner="governance",
                get_path=gov_path,
                post_path=f"{gov_path}/decide",
                payload=lambda curr: {
                    "actor_role": "automated_gate",
                    "actor_id": self.governance_actor_id,
                    "outcome": "approved",
                    "rationale": "Low-risk internal paper Persona market transition",
                    "evidence_refs": [{"ref_type": "registry_entry", "ref_id": child_artifact_id}],
                    "candidate_digest": candidate_digest,
                    "expires_at": expires_at,
                    "expected_version": int((curr or {}).get("version") or 0),
                },
                ready=lambda r: r.get("decision_state") == "decided" and r.get("decision") == "approved",
                validate=lambda r: None,
            )
            active = self._checkpoint_receipt(
                active,
                step="transition_approval_decided_readback",
                key="transition_approval_decided",
                receipt=decide_receipt,
            )

            def build_advance_payload(current: Mapping[str, Any] | None) -> Mapping[str, Any]:
                if current is None:
                    raise PersonaProvisioningCoordinationError(
                        "Registry transition candidate has no readback to advance"
                    )
                entry = _registry_entry(current)
                base = {
                    "approval_decision_id": child_decision_id,
                    "expected_artifact_state": "candidate",
                    "expected_version": str(entry["version"]),
                    "expected_updated_at": entry.get("updated_at"),
                }
                command_key = (
                    f"persona-provisioning:{ids.token}:advance:{child_artifact_id}:approved:"
                    f"{_stable_hash(base)[:16]}"
                )
                return {"target_state": "approved", "command_key": command_key, **base}

            advance_path = f"/api/registry/strategy-artifacts/{_path_id(child_artifact_id)}"
            approved_receipt = self._transition_then_get(
                owner="registry",
                get_path=advance_path,
                post_path=f"{advance_path}/advance",
                payload=build_advance_payload,
                ready=lambda r: _registry_entry(r).get("artifact_state") == "approved",
                validate=lambda r: None,
            )
            active = self._checkpoint_receipt(
                active,
                step="transition_strategy_artifact_approved_readback",
                key="transition_strategy_artifact_approved",
                receipt=approved_receipt,
            )

            if "legacy_strategy_artifact_approved" not in active.references:
                active.references["legacy_strategy_artifact_approved"] = deepcopy(parent_receipt)
            active.references["strategy_artifact_approved"] = deepcopy(approved_receipt)
            if active.result is None:
                active.result = {}
            active.result["strategy_artifact_id"] = child_artifact_id
            active.result["registry_id"] = child_artifact_id
            active.result["approval_decision_id"] = child_decision_id
            active.result["legacy_strategy_artifact_id"] = parent_id
            active.result["market"] = canonical_market
            active = self._checkpoint(active)
            return active
        finally:
            self.store.release(
                active,
                lease_owner=self.lease_owner,
                lease_seconds=self.lease_seconds,
            )

    @staticmethod
    def _registry_tenant(entry: Mapping[str, Any]) -> str | None:
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        value = entry.get("owner_tenant") or metadata.get("tenant_id")
        return str(value).strip() if value else None

    def ensure_chain_paper_capital(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        capital_pool_id: str,
        binding_id: str,
        requested_by: str,
        currency: str | None = None,
        name: str | None = None,
        budget: Any = None,
        mandate: Any = None,
        risk_policy_ref: Any = None,
        strategy_spec_registry_id: str | None = None,
        strategy_artifact_id: str | None = None,
        strategy_artifact_checksum: str | None = None,
        labels: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ensure only the exact chain paper pool and binding under the BFF identity.

        Capital-only: no Registry/Governance write, no approval proposal, review
        or decision, and no id derivation.  The already-approved StrategySpec and
        execution artifact are read back (GET only) and must still be approved
        for ``tenant_id``.  Pool and binding are read before create; a matching
        existing record is adopted unchanged, so its historical writer is kept.
        New records are written by ``self.actor_id`` with ``requested_by`` as
        audit metadata only.  Anything that is not a tenant-owned paper record
        fails with a named error.
        """

        for label, value in (
            ("tenant_id", tenant_id),
            ("persona_id", persona_id),
            ("capital_pool_id", capital_pool_id),
            ("binding_id", binding_id),
            ("requested_by", requested_by),
        ):
            if not str(value or "").strip():
                raise PersonaProvisioningCoordinationError(f"{label} is required")

        registry: dict[str, Any] = {}
        for key, registry_id, path, artifact_type in (
            (
                "strategy_spec",
                strategy_spec_registry_id,
                "/api/registry/strategy-specs/",
                "strategy_spec",
            ),
            (
                "strategy_artifact",
                strategy_artifact_id,
                "/api/registry/strategy-artifacts/",
                "execution_bundle",
            ),
        ):
            if not registry_id:
                continue
            entry = self._owner_get("registry", f"{path}{_path_id(registry_id)}")
            if entry is None:
                raise PersonaProvisioningCoordinationError(
                    f"approved {key} {registry_id} was not found at the Registry owner"
                )
            entry = _registry_entry(entry)
            if (
                entry.get("registry_id") != registry_id
                or entry.get("artifact_type") != artifact_type
                or entry.get("artifact_state") != "approved"
                or self._registry_tenant(entry) != tenant_id
            ):
                raise PersonaProvisioningCoordinationError(
                    f"{key} {registry_id} is not an approved {artifact_type} of tenant {tenant_id}"
                )
            if (
                key == "strategy_artifact"
                and strategy_artifact_checksum
                and entry.get("checksum") != strategy_artifact_checksum
            ):
                raise PersonaProvisioningCoordinationError(
                    f"{key} {registry_id} checksum differs from the frozen checksum"
                )
            registry[key] = {
                "registry_id": registry_id,
                "version": entry.get("version"),
                "checksum": entry.get("checksum"),
            }

        extra_labels = {
            key: value
            for key, value in dict(labels or {}).items()
            if key in CHAIN_CAPITAL_LABEL_KEYS
        }
        pool_path = f"/api/capital-pools/{_path_id(capital_pool_id)}"
        if not currency and self._owner_get("capital", pool_path) is None:
            raise PersonaProvisioningCoordinationError(
                "currency is required to create the chain paper pool"
            )
        pool_base: dict[str, Any] = {
            "actor_id": self.actor_id,
            "actor_role": "admin",
            "pool_id": capital_pool_id,
            "name": name or f"{persona_id} paper pool",
            "owner_id": tenant_id,
            "owner_type": "org",
            "status": "active",
            "currency": currency,
            "budget": budget,
            "risk_policy_ref": risk_policy_ref,
            "single_runtime_enforced": True,
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "tenant_id": tenant_id,
                "persona_id": persona_id,
                "requested_by": requested_by,
                **extra_labels,
            },
        }

        def validate_pool(receipt: Mapping[str, Any]) -> None:
            metadata = receipt.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if (
                receipt.get("pool_id") != capital_pool_id
                or not self._formal_tenant_matches(receipt, tenant_id)
                or receipt.get("status") != "active"
                or receipt.get("single_runtime_enforced") is not True
                or metadata.get("execution_context") != "paper"
            ):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool readback is not an active tenant-owned paper pool"
                )
            if not self._pool_owned_by_chain(
                metadata, persona_id=persona_id, chain_id=extra_labels.get("chain_id")
            ):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool does not belong to this persona or chain; refusing to adopt it"
                )

        pool = self._create_then_get(
            owner="capital",
            get_path=pool_path,
            post_path="/api/capital-pools",
            payload={
                **pool_base,
                "idempotency_key": f"chain-paper-capital:{tenant_id}:{capital_pool_id}",
                "request_hash": _stable_hash(pool_base),
            },
            validate=validate_pool,
        )

        binding_path = f"/api/bindings/{_path_id(binding_id)}"
        binding_base: dict[str, Any] = {
            "actor_id": self.actor_id,
            "actor_role": "admin",
            "binding_id": binding_id,
            "persona_id": persona_id,
            "capital_pool_id": capital_pool_id,
            "role": "paper_owner",
            "allowed_deployment_scope": "paper",
            "mandate": mandate,
            "budget": budget,
            "created_by": self.actor_id,
            "metadata": {
                "tenant_id": tenant_id,
                "requested_by": requested_by,
                **extra_labels,
                **(
                    {"registry_id": strategy_artifact_id}
                    if strategy_artifact_id
                    else {}
                ),
                **(
                    {"strategy_spec_registry_id": strategy_spec_registry_id}
                    if strategy_spec_registry_id
                    else {}
                ),
            },
        }

        def validate_binding(receipt: Mapping[str, Any]) -> None:
            self._check_binding_identity(
                receipt,
                persona_id=persona_id,
                pool_id=capital_pool_id,
                binding_id=binding_id,
            )
            if receipt.get("status") not in {"pending", "active"}:
                raise PersonaProvisioningCoordinationError(
                    "PersonaCapitalBinding readback is not pending/active"
                )

        self._create_then_get(
            owner="capital",
            get_path=binding_path,
            post_path="/api/bindings",
            payload={
                **binding_base,
                "idempotency_key": f"chain-paper-capital:{tenant_id}:{binding_id}",
                "request_hash": _stable_hash(binding_base),
            },
            validate=validate_binding,
        )

        def binding_ready(receipt: Mapping[str, Any]) -> bool:
            return receipt.get("status") == "active"

        def validate_active(receipt: Mapping[str, Any]) -> None:
            self._check_binding_identity(
                receipt,
                persona_id=persona_id,
                pool_id=capital_pool_id,
                binding_id=binding_id,
            )
            if not binding_ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "PersonaCapitalBinding activation readback is not active"
                )

        binding = self._transition_then_get(
            owner="capital",
            get_path=binding_path,
            post_path=f"{binding_path}/activate",
            payload={"actor_id": self.actor_id, "actor_role": "persona.admin"},
            ready=binding_ready,
            validate=validate_active,
        )
        return {
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "capital_pool": pool,
            "persona_capital_binding": binding,
            "registry": registry,
            "writer_actor_id": self.actor_id,
            "requested_by": requested_by,
        }

    @staticmethod
    def _formal_tenant_matches(pool: Mapping[str, Any], tenant_id: str) -> bool:
        # Capital's owner-returned formal tenant_id is the only tenant authority;
        # business owner_id/owner_type and metadata.tenant_id are caller-writable labels.
        formal = pool.get("tenant_id")
        return isinstance(formal, str) and bool(formal.strip()) and formal == tenant_id

    @staticmethod
    def _pool_owned_by_chain(
        metadata: Mapping[str, Any], *, persona_id: str, chain_id: Any
    ) -> bool:
        if metadata.get("persona_id") == persona_id:
            return True
        return bool(chain_id) and metadata.get("chain_id") == chain_id

    def retire_chain_paper_capital(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        capital_pool_id: str,
        binding_id: str,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        """Revoke the chain binding, then archive its pool (binding before pool).

        Only a tenant-owned paper pool proven to belong to this persona or
        chain is touched: by its binding (read back present or already
        revoked), ``metadata.persona_id`` or ``metadata.chain_id``.  Runtime
        dependants and any other non-terminal binding of the pool are checked
        first; every refusal is a named error raised before any write.  An
        absent record is reported, not an error.
        """

        pool_path = f"/api/capital-pools/{_path_id(capital_pool_id)}"
        pool = self._owner_get("capital", pool_path)
        binding = self._owner_get("capital", f"/api/bindings/{_path_id(binding_id)}")
        if binding is not None:
            self._check_binding_identity(
                binding,
                persona_id=persona_id,
                pool_id=capital_pool_id,
                binding_id=binding_id,
            )
        if chain_id and binding is not None:
            binding_meta = binding.get("metadata")
            label = binding_meta.get("chain_id") if isinstance(binding_meta, Mapping) else None
            if label and label != chain_id:
                raise PersonaProvisioningCoordinationError(
                    "PersonaCapitalBinding belongs to another chain; refusing to retire it"
                )
        if pool is not None:
            metadata = pool.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if not self._formal_tenant_matches(pool, tenant_id):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool belongs to another tenant; refusing to retire it"
                )
            if (
                metadata.get("execution_context") != "paper"
                or pool.get("single_runtime_enforced") is not True
            ):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool is not a paper pool; refusing to retire it"
                )
            if chain_id and metadata.get("chain_id") not in (None, "", chain_id):
                # A present binding or a matching persona never authorizes another chain's pool.
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool belongs to another chain; refusing to retire it"
                )
            if binding is None and not self._pool_owned_by_chain(
                metadata, persona_id=persona_id, chain_id=chain_id
            ):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool ownership by this persona or chain is not proven; "
                    "refusing to retire it"
                )

        if self.runtime_binding_reader is None:
            raise PersonaProvisioningCoordinationError(
                "RuntimeBinding reader is not configured"
            )
        live_runtime = {
            str(b.get("binding_id") or b.get("id") or ""): str(b.get("status") or "")
            for b in self.runtime_binding_reader("", capital_pool_id) or []
            if str(b.get("status") or "") not in TERMINAL_RUNTIME_BINDING_STATUSES
        }
        if live_runtime:
            raise PersonaProvisioningCoordinationError(
                f"RuntimeBindings are not retired: {sorted(live_runtime.items())}"
            )
        other_bindings = {
            str(b.get("binding_id") or ""): str(b.get("status") or "")
            for b in self.transport.list(
                "capital", f"/api/bindings?capital_pool_id={_path_id(capital_pool_id)}"
            )
            if b.get("capital_pool_id") == capital_pool_id
            and b.get("binding_id") != binding_id
            and b.get("status") not in {"revoked", "expired"}
        }
        if other_bindings:
            raise PersonaProvisioningCoordinationError(
                f"other PersonaCapitalBindings still reference the pool: {sorted(other_bindings.items())}"
            )

        binding_result = self._revoke_binding(
            persona_id=persona_id, pool_id=capital_pool_id, binding_id=binding_id
        )
        pool_result = self._archive_pool(pool_id=capital_pool_id)
        return {"persona_capital_binding": dict(binding_result), "capital_pool": dict(pool_result)}

    def _preview(self, record: ProvisioningRecord) -> ProvisioningRecord:
        self._capital_pool_currency(record)
        preview = ProvisioningRecord.from_mapping(record.to_dict())
        ids = deterministic_provisioning_ids(preview)
        preview.state = record.state
        preview.current_step = "dry_run"
        preview.result = {
            "status": "dry_run",
            "mutations_performed": False,
            "paper_running": False,
            "ids": ids.to_dict(),
            "steps": [
                "capital_pool",
                "baseline_strategy_spec_candidate",
                "baseline_approval_proposed",
                "baseline_approval_reviewed",
                "baseline_approval_decided",
                "baseline_strategy_spec_approved",
                "baseline_strategy_artifact_candidate",
                "baseline_strategy_artifact_approval_proposed",
                "baseline_strategy_artifact_approval_reviewed",
                "baseline_strategy_artifact_approval_decided",
                "baseline_strategy_artifact_approved",
                "strategy_spec_candidate",
                "approval_proposed",
                "approval_reviewed",
                "approval_decided",
                "strategy_spec_approved",
                "strategy_artifact_candidate",
                "strategy_artifact_approval_proposed",
                "strategy_artifact_approval_reviewed",
                "strategy_artifact_approval_decided",
                "strategy_artifact_approved",
                "persona_capital_binding_created",
                "persona_capital_binding_active",
                "deployment_plan",
                "deployment_dispatch",
                "schedule_registration",
            ],
        }
        return preview

    def _checkpoint_receipt(
        self,
        record: ProvisioningRecord,
        *,
        step: str,
        key: str,
        receipt: Mapping[str, Any],
    ) -> ProvisioningRecord:
        record.current_step = step
        record.references[key] = deepcopy(dict(receipt))
        return self._checkpoint(record)

    @staticmethod
    def _requested_by(record: ProvisioningRecord) -> str | None:
        """Preserve caller identity as context, never as service authority."""

        for field in ("requested_by", "created_by", "owner", "actor_id"):
            value = str(record.request_payload.get(field) or "").strip()
            if value:
                return value
        return None

    def _owner_get(self, owner: str, path: str) -> dict[str, Any] | None:
        value = self.transport.get(owner, path)
        if value is None:
            return None
        return _mapping(value, label=f"{owner} GET {path}")

    def _create_then_get(
        self,
        *,
        owner: str,
        get_path: str,
        post_path: str,
        payload: Mapping[str, Any],
        validate: Callable[[Mapping[str, Any]], None],
    ) -> dict[str, Any]:
        receipt = self._owner_get(owner, get_path)
        mutation_error: Exception | None = None
        if receipt is None:
            try:
                self.transport.post(owner, post_path, deepcopy(dict(payload)))
            except Exception as exc:  # response loss/concurrent create is reconciled by GET
                mutation_error = exc
            receipt = self._owner_get(owner, get_path)
        if receipt is None:
            detail = f" after mutation error: {mutation_error}" if mutation_error else ""
            raise PersonaProvisioningCoordinationError(
                f"{owner} did not persist {get_path}{detail}"
            )
        validate(receipt)
        return receipt

    def _transition_then_get(
        self,
        *,
        owner: str,
        get_path: str,
        post_path: str,
        payload: Mapping[str, Any] | Callable[[Mapping[str, Any] | None], Mapping[str, Any]],
        ready: Callable[[Mapping[str, Any]], bool],
        validate: Callable[[Mapping[str, Any]], None],
        on_mutation_error: Callable[[Exception], None] | None = None,
    ) -> dict[str, Any]:
        receipt = self._owner_get(owner, get_path)
        mutation_error: Exception | None = None
        if receipt is None or not ready(receipt):
            resolved_payload = payload(receipt) if callable(payload) else payload
            try:
                self.transport.post(owner, post_path, deepcopy(dict(resolved_payload)))
            except Exception as exc:  # response loss is reconciled by authoritative GET
                mutation_error = exc
                if on_mutation_error is not None:
                    on_mutation_error(exc)
            receipt = self._owner_get(owner, get_path)
        if receipt is None:
            detail = f" after mutation error: {mutation_error}" if mutation_error else ""
            raise PersonaProvisioningCoordinationError(
                f"{owner} transition has no readback at {get_path}{detail}"
            )
        try:
            validate(receipt)
        except _PaperGateFailure:
            raise
        except Exception as exc:
            if mutation_error is not None:
                raise PersonaProvisioningCoordinationError(
                    f"{owner} transition failed ({mutation_error}) and readback "
                    f"was not ready: {exc}"
                ) from exc
            raise
        return receipt

    # -- risk-officer drafted pool policy (replaces the former hardcoded 2x default) --------------------
    def _drafter(self) -> Callable[[DraftInputs], DraftResult] | None:
        if self.risk_policy_drafter is not None:
            return self.risk_policy_drafter
        if os.getenv("OPENCLAW_GATEWAY_URL", "").strip():
            self.risk_policy_drafter = make_drafter()
        return self.risk_policy_drafter

    @staticmethod
    def _is_legacy_default_policy(policy: Mapping[str, Any], pool_id: str, profile: str) -> bool:
        """A generated per-pool inline policy that lacks single-name cap / cash reserve (the old 2x default).

        Operator-authored catalog policies keep their own identity and are never replaced by a draft.
        """
        policy_id = str(policy.get("risk_policy_id") or "")
        generated = policy_id.startswith(("rp-pool-", "paper-inline-policy:")) or policy_id == f"rp-{pool_id}"
        return generated and validate_drafted_pool_risk_policy(policy, profile=profile)[0] is None

    @staticmethod
    def _next_policy_version(existing: Any, explicit: str | None = None) -> str:
        if explicit:
            return explicit
        raw = str(existing or "").strip().lstrip("vV")
        try:
            return f"{int(float(raw)) + 1}.0" if raw else "1.0"
        except ValueError:
            return "1.0"

    @staticmethod
    def _risk_profile_proposal_id(
        tenant_id: str, pool_id: str, profile: str, policy_id: str, version: str, proposal_key: str = ""
    ) -> str:
        prop_hash = hashlib.sha256(
            (f"{tenant_id}|{pool_id}|{profile}|{policy_id}|{version}"
             + (f"|{proposal_key}" if proposal_key else "")).encode("utf-8")
        ).hexdigest()[:20]
        return f"apv-risk-profile-{prop_hash}"

    def _draft_inputs(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        pool_id: str,
        profile: str,
        budget: Any,
        currency: str,
        request: Mapping[str, Any] | None,
        base_pool: Mapping[str, Any] | None,
        version: str,
    ) -> DraftInputs:
        req = request if isinstance(request, Mapping) else {}
        req_meta = req.get("metadata") if isinstance(req.get("metadata"), Mapping) else {}
        pool_meta = (
            base_pool.get("metadata") if isinstance(base_pool, Mapping) and isinstance(base_pool.get("metadata"), Mapping)
            else {}
        )
        persona: Mapping[str, Any] = {}
        try:
            getter = getattr(self.persona_owner_provider(), "get_persona", None)
            found = getter(persona_id) if callable(getter) else None
            persona = found if isinstance(found, Mapping) else {}
        except Exception:
            persona = {}
        persona_meta = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}

        def first(*values: Any) -> Any:
            return next((v for v in values if v not in (None, "", [], ())), None)

        raw_symbols = first(req.get("symbols"), req_meta.get("symbols"), pool_meta.get("symbols"),
                            persona_meta.get("symbols"), persona.get("symbols"))
        if isinstance(raw_symbols, str):
            raw_symbols = [raw_symbols]
        instruments = tuple(str(x) for x in raw_symbols) if isinstance(raw_symbols, (list, tuple)) else ()
        if not instruments:
            market = first(req.get("market"), req_meta.get("market"), persona_meta.get("market"))
            instruments = (str(market),) if market else ()
        mandate = str(first(req.get("mandate"), req_meta.get("mandate"), persona.get("mandate"),
                            persona_meta.get("mandate"), persona.get("description")) or "")
        raw_evidence = first(pool_meta.get("recent_paper_evidence"), req_meta.get("recent_paper_evidence"))
        evidence = tuple(str(x) for x in raw_evidence) if isinstance(raw_evidence, (list, tuple)) else ()
        reader = getattr(self, "risk_evidence_reader", None)
        if callable(reader):
            try:
                evidence = (*evidence, *(str(x) for x in reader(tenant_id, pool_id)))
            except Exception:
                pass
        return DraftInputs(
            pool_id=pool_id, tenant_id=tenant_id, persona_id=persona_id, profile=profile, budget=budget,
            currency=currency, instruments=instruments, mandate=mandate, evidence=evidence,
            policy_id=f"rp-{pool_id}", version=version,
            bounds=profile_outer_bounds().get(str(profile).strip().lower(), {}),
        )

    def _risk_officer_policy_candidate(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        pool_id: str,
        profile: str | None,
        budget: Any,
        currency: str,
        request: Mapping[str, Any] | None,
        base_pool: Mapping[str, Any] | None,
        version: str,
    ) -> DraftResult:
        """Policy content for a pool with no approved applicable policy.

        Never invents numbers: returns a validated risk-officer draft, or raises so the pool stays pending
        with a readable reason. Idempotent on (pool, profile, input digest): a Governance proposal that already
        carries the draft for that key is reused without another risk-officer turn.
        """
        def pending(reason: str, code: str = "risk_officer_draft_unavailable") -> PersonaProvisioningCoordinationError:
            return PersonaProvisioningCoordinationError(
                f"CapitalPool risk policy pending: {reason} ({code})",
                reason_code=code,
                absence_source="policy_draft_unavailable",
                resolution_status="unresolved_profile_mapping",
            )

        if not profile:
            raise pending("no requested risk profile to draft a policy for", "risk_policy_profile_required")
        inputs = self._draft_inputs(
            tenant_id=tenant_id, persona_id=persona_id, pool_id=pool_id, profile=profile, budget=budget,
            currency=currency, request=request, base_pool=base_pool, version=version,
        )
        proposal_id = self._risk_profile_proposal_id(
            tenant_id, pool_id, profile, inputs.policy_id, version, inputs.input_digest
        )
        if self.transport is not None:
            try:
                existing = self._owner_get("governance", f"/api/governance/approvals/{_path_id(proposal_id)}")
            except Exception:
                existing = None
            meta = existing.get("metadata") if isinstance(existing, Mapping) and isinstance(existing.get("metadata"), Mapping) else {}
            subject = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
            stored = subject.get("risk_officer_draft") if isinstance(subject.get("risk_officer_draft"), Mapping) else {}
            if isinstance(stored.get("policy"), Mapping):
                model, _ = validate_drafted_pool_risk_policy(stored["policy"], profile=profile)
                if model is not None and risk_policy_digest(stored["policy"]) == subject.get("risk_policy_digest"):
                    return DraftResult(True, policy=deepcopy(dict(stored["policy"])),
                                       provenance=dict(stored.get("provenance") or {}))
        drafter = self._drafter()
        if drafter is None:
            raise pending("risk officer drafter is not configured")
        result = drafter(inputs)
        if not result.ok or result.policy is None:
            raise pending(result.reason or "risk officer returned no usable draft")
        return result

    def _draft_candidate_policy_dict(self, draft: DraftResult) -> dict[str, Any]:
        return deepcopy(dict(draft.policy or {}))

    def _governance_approval_reader(self) -> Any:
        coordinator = self

        class _TransportApprovalReader:
            def get(self, decision_id: str) -> dict[str, Any] | None:
                clean_id = str(decision_id or "").strip()
                if not clean_id or coordinator.transport is None:
                    return None
                return coordinator._owner_get(
                    "governance",
                    f"/api/governance/approvals/{_path_id(clean_id)}",
                )

            def list_for_target(
                self,
                *,
                target_type: str = "capital_pool_activation",
                target_id: str,
                decision_state: str | None = "decided",
                max_items: int = 16,
                history: bool = False,
                now: datetime | None = None,
            ) -> list[dict[str, Any]]:
                clean_type = str(target_type or "").strip()
                clean_id = str(target_id or "").strip()
                if not clean_type or not clean_id or coordinator.transport is None:
                    return []
                list_fn = getattr(coordinator.transport, "list", None)
                if not callable(list_fn):
                    return []
                query = f"target_type={_path_id(clean_type)}&target_id={_path_id(clean_id)}"
                if decision_state is not None and str(decision_state).strip():
                    query += f"&decision_state={_path_id(str(decision_state).strip())}"
                try:
                    raw_items = list_fn("governance", f"/api/governance/approvals?{query}")
                except Exception:
                    return []
                if not isinstance(raw_items, list):
                    return []
                from services.governance.approval_authority import bound_candidate_history

                bound = max(1, min(int(max_items or 16), 64))
                matched: list[dict[str, Any]] = []
                overflow = False
                for item in raw_items:
                    if not isinstance(item, Mapping):
                        continue
                    if str(item.get("target_type") or "") != clean_type:
                        continue
                    if str(item.get("target_id") or "") != clean_id:
                        continue
                    if decision_state and str(item.get("decision_state") or "") != decision_state:
                        continue
                    matched.append(deepcopy(dict(item)))
                out = bound_candidate_history(matched, bound=bound, now=now, history=history)
                out.overflow = out.overflow or overflow
                return out

        return _TransportApprovalReader()

    def _route_or_reuse_risk_policy_owner_decision_request(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        pool_id: str,
        requested_profile: str,
        resolution: Any,
        dry_run: bool = False,
        proposal_key: str = "",
        draft: DraftResult | None = None,
        baseline_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if draft is not None and draft.policy:
            proposal_key = proposal_key or str(draft.provenance.get("input_digest") or "")
        base_req = (
            dict(resolution.owner_decision_request)
            if isinstance(getattr(resolution, "owner_decision_request", None), Mapping)
            else {}
        )
        res_status = str(getattr(resolution, "status", None) or "unresolved_profile_mapping")
        res_code = str(getattr(resolution, "reason_code", None) or "risk_policy_profile_mapping_unapproved")
        absence_source = str(
            base_req.get("absence_source")
            or (
                "policy_candidates_ambiguous"
                if res_status == "ambiguous_profile_mapping"
                else "policy_definition_and_approval_absent"
            )
        )
        cand_pol_id = str(
            base_req.get("candidate_risk_policy_id")
            or getattr(resolution, "risk_policy_id", None)
            or f"rp-{pool_id}"
        )
        cand_pol_ver = str(
            base_req.get("candidate_risk_policy_version")
            or getattr(resolution, "risk_policy_version", None)
            or "1.0"
        )
        cand_pol_digest = str(
            base_req.get("candidate_risk_policy_digest")
            or getattr(resolution, "risk_policy_digest", None)
            or ""
        )
        cand_pool_digest = str(base_req.get("candidate_pool_approval_digest") or "")
        if draft is not None and draft.policy:
            cand_pol_id = str(draft.policy.get("risk_policy_id") or cand_pol_id)
            cand_pol_ver = str(draft.policy.get("version") or cand_pol_ver)
            cand_pol_digest = risk_policy_digest(draft.policy)

        if (draft is None or not draft.policy) and cand_pol_digest:
            # A policy that already carries a risk-officer draft in an earlier proposal is re-proposed with that
            # draft, never without it: a draft-less proposal is refused by the paper gate.
            carried = self._carried_risk_officer_draft(
                tenant_id=tenant_id, pool_id=pool_id, requested_profile=requested_profile,
                policy_digest=cand_pol_digest)
            if carried is not None:
                draft = carried
                proposal_key = proposal_key or str(carried.provenance.get("input_digest") or "")

        # Risk direction is computed, never assumed: candidate vs the pool's currently effective policy.
        # ``baseline_policy`` is the policy in effect before the repair; ``resolution`` may already describe
        # the candidate itself, which would compare the draft with itself (always neutral).
        effective = getattr(resolution, "policy", None)
        effective_policy = (
            dict(baseline_policy) if baseline_policy
            else effective.to_dict() if hasattr(effective, "to_dict") else None
        )
        direction = compare_risk_policies(draft.policy if draft is not None and draft.policy else None, effective_policy)

        deterministic_proposal_id = self._risk_profile_proposal_id(
            tenant_id, pool_id, requested_profile, cand_pol_id, cand_pol_ver, proposal_key
        )

        decision_req: dict[str, Any] = {
            "policy_owner": "services/capital/risk_policy.py",
            "governance_surface": "services/control-plane/governance/risk_policy.py",
            "accountable_domain": "capital_risk_policy_owner",
            "absence_source": absence_source,
            "reason_code": res_code,
            "resolution_status": res_status,
            "requested_profile": requested_profile,
            "capital_pool_id": pool_id,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "stage": "paper",
            "candidate_risk_policy_id": cand_pol_id,
            "candidate_risk_policy_version": cand_pol_ver,
            "candidate_risk_policy_digest": cand_pol_digest,
            **(
                {"candidate_pool_approval_digest": cand_pool_digest}
                if cand_pool_digest
                else {}
            ),
            **{
                k: v
                for k, v in base_req.items()
                if k
                in {
                    "ambiguous_policy_ids",
                    "ambiguous_approval_decision_ids",
                    "decision_required",
                }
            },
            "deterministic_proposal_id": deterministic_proposal_id,
            **(
                {"risk_officer_draft": {
                    "provenance": dict(draft.provenance), "policy": deepcopy(dict(draft.policy)),
                }}
                if draft is not None and draft.policy else {}
            ),
            "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
            "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
            "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
            "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
            "end_user_action_required": False,
            "recommended_governance_action": (
                "resolve_ambiguous_capital_pool_risk_policy_candidates"
                if res_status == "ambiguous_profile_mapping"
                else "approve_capital_pool_risk_policy_mapping"
            ),
        }
        if "decision_required" not in decision_req or not decision_req["decision_required"]:
            decision_req["decision_required"] = str(getattr(resolution, "reason", None) or res_code)

        def matches_request(item: Mapping[str, Any]) -> bool:
            meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            subj = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
            profiles = [subj.get(key) for key in ("requested_risk", "requested_profile", "risk_profile")
                        if subj.get(key) not in (None, "")]
            return bool(
                item.get("tenant_id") == tenant_id
                and item.get("target_type") == "capital_pool_activation"
                and item.get("target_id") == pool_id
                and item.get("target_version") == cand_pool_digest
                and subj.get("pool_id") == pool_id
                and subj.get("risk_direction") in {"increase", "reduce", "neutral"}
                and profiles and all(value == requested_profile for value in profiles)
                and subj.get("risk_policy_id") == cand_pol_id
                and subj.get("risk_policy_version") == cand_pol_ver
                and subj.get("risk_policy_digest") == cand_pol_digest
            )

        # GET-first check in Governance to reuse any existing pending or rejected proposal without duplicate writes.
        existing_prop = None
        if self.transport is not None:
            try:
                existing_prop = self._owner_get(
                    "governance",
                    f"/api/governance/approvals/{_path_id(deterministic_proposal_id)}",
                )
            except Exception:
                existing_prop = None
            if existing_prop is None:
                reader = self._governance_approval_reader()
                for item in reader.list_for_target(
                    target_type="capital_pool_activation",
                    target_id=pool_id,
                    decision_state=None,
                    max_items=16,
                    history=True,
                ):
                    if not matches_request(item):
                        continue
                    st = str(item.get("decision_state") or "")
                    if st in {"proposed", "under_review"} or (
                        st == "decided" and str(item.get("decision") or "") == "rejected"
                    ):
                        existing_prop = item
                        break

        if isinstance(existing_prop, Mapping) and not matches_request(existing_prop):
            # Keep deployed IDs for identical requests. A collision is explicit
            # owner evidence, never permission to reuse an unrelated decision.
            decision_req["governance_proposal_status"] = "existing_proposal_request_mismatch"
            decision_req["conflicting_governance_proposal_id"] = existing_prop.get("decision_id")
            return decision_req

        if isinstance(existing_prop, Mapping) and self._is_missing_draft_rejection(existing_prop):
            # The gate refused only because the proposal carried no risk-officer draft. That is a defect of the
            # proposal, not a decision about the policy: it is never a permanent non-approval.
            refused_id = str(existing_prop.get("decision_id") or deterministic_proposal_id)
            if draft is None or not draft.policy:
                decision_req["governance_proposal_id"] = refused_id
                decision_req["governance_proposal_status"] = "missing_draft_rejection_retryable"
                decision_req["paper_gate_reason"] = str(existing_prop.get("rationale") or "")
                return decision_req
            deterministic_proposal_id = self._risk_profile_proposal_id(
                tenant_id, pool_id, requested_profile, cand_pol_id, cand_pol_ver,
                f"{proposal_key}|after-missing-draft:{refused_id}")
            try:
                existing_prop = self._owner_get(
                    "governance", f"/api/governance/approvals/{_path_id(deterministic_proposal_id)}")
            except Exception:
                existing_prop = None
            decision_req["deterministic_proposal_id"] = deterministic_proposal_id
            decision_req["retried_after_missing_draft_rejection"] = refused_id

        if isinstance(existing_prop, Mapping):
            found_id = str(existing_prop.get("decision_id") or deterministic_proposal_id)
            found_state = str(existing_prop.get("decision_state") or "proposed")
            found_outcome = str(existing_prop.get("decision") or "")
            decision_req["governance_proposal_id"] = found_id
            decision_req["governance_proposal_state"] = found_state
            decision_req["human_inbox_action_url"] = f"/management/approvals?approval={found_id}"
            if found_state in {"proposed", "under_review"}:
                decision_req["governance_proposal_status"] = "reused_existing_pending_proposal"
                if not dry_run:
                    self._apply_paper_gate_to_risk_policy_proposal(
                        existing_prop, decision_req, requested_profile=requested_profile)
                    if draft is not None and draft.policy:
                        self._close_superseded_risk_policy_proposals(
                            tenant_id=tenant_id, pool_id=pool_id, keep_id=found_id,
                            requested_profile=requested_profile, matches_request=matches_request,
                            decision_req=decision_req)
                return decision_req
            if found_state == "decided" and found_outcome == "rejected":
                decision_req["governance_proposal_outcome"] = "rejected"
                decision_req["governance_proposal_status"] = "explicit_non_approval_recorded"
                decision_req["absence_source"] = "policy_approval_rejected"
                return decision_req

        # Submit a single deterministic proposal to Governance Human Inbox when a proposer principal is configured.
        proposer = self.governance_proposer
        proposer_actor_id = self.governance_proposer_actor_id
        if proposer is None and not dry_run:
            env_proposer_id = os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID", "").strip()
            has_env_token = bool(
                os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN", "").strip()
                or os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN_FILE", "").strip()
            )
            if env_proposer_id and has_env_token:
                try:
                    from .persona_paper_enrollment import HttpGovernanceProposer

                    proposer = HttpGovernanceProposer(tenant_id=tenant_id)
                    proposer_actor_id = env_proposer_id
                except Exception:
                    proposer = None

        if (
            proposer is not None
            and not dry_run
            and res_status != "ambiguous_profile_mapping"
            and cand_pool_digest
        ):
            eff_proposer_actor = str(
                proposer_actor_id
                or getattr(proposer, "governance_subject", None)
                or self.governance_actor_id
            ).strip()
            proposal_body: dict[str, Any] = {
                "expected_version": 0,
                "decision_id": deterministic_proposal_id,
                "target_type": "capital_pool_activation",
                "target_id": pool_id,
                "target_version": cand_pool_digest,
                # Paper pool, capital scale 0: low risk, decided by the paper automated gate (P-4).
                "risk_level": "low",
                "capital_pool_id": pool_id,
                "persona_id": persona_id,
                "tenant_id": tenant_id,
                "owner_user_id": eff_proposer_actor,
                "expires_at": (datetime.now(timezone.utc) + APPROVAL_TTL - timedelta(minutes=5)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
                "subject": {
                    "pool_id": pool_id,
                    "risk_direction": direction["risk_direction"],
                    "risk_comparison": dict(direction["risk_comparison"]),
                    "requested_risk": requested_profile,
                    "risk_policy_id": cand_pol_id,
                    "risk_policy_version": cand_pol_ver,
                    "risk_policy_digest": cand_pol_digest,
                    "absence_source": absence_source,
                    "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
                    "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
                    **(
                        {"risk_officer_draft": {
                            "provenance": dict(draft.provenance),
                            "policy": deepcopy(dict(draft.policy)),
                        }}
                        if draft is not None and draft.policy else {}
                    ),
                },
            }
            if cand_pol_digest:
                proposal_body["candidate_digest"] = cand_pol_digest
            created_prop = None
            proposal_failure: Exception | None = None
            try:
                created_prop = proposer.post("governance", "/api/governance/approvals", proposal_body)
            except Exception as exc:
                created_prop = None
                proposal_failure = exc
            if self.transport is not None:
                try:
                    readback_prop = self._owner_get(
                        "governance",
                        f"/api/governance/approvals/{_path_id(deterministic_proposal_id)}",
                    )
                    if isinstance(readback_prop, Mapping):
                        created_prop = readback_prop
                except Exception:
                    pass
            if isinstance(created_prop, Mapping) and created_prop.get("decision_id") and matches_request(created_prop):
                prop_id = str(created_prop.get("decision_id"))
                decision_req["governance_proposal_id"] = prop_id
                decision_req["governance_proposal_state"] = str(created_prop.get("decision_state") or "proposed")
                decision_req["governance_proposal_status"] = "proposed_to_governance_human_inbox"
                decision_req["human_inbox_action_url"] = f"/management/approvals?approval={prop_id}"
                self._apply_paper_gate_to_risk_policy_proposal(
                    created_prop, decision_req, requested_profile=requested_profile)
                if draft is not None and draft.policy:
                    self._close_superseded_risk_policy_proposals(
                        tenant_id=tenant_id, pool_id=pool_id, keep_id=prop_id,
                        requested_profile=requested_profile, matches_request=matches_request,
                        decision_req=decision_req)
                return decision_req
            if proposal_failure is not None:
                # A failed proposal is never reported as success: name the owner status and reason.
                from .persona_paper_enrollment import _error_detail, _error_status

                code = _error_status(proposal_failure)
                kind = "proposal_rejected" if code is not None and code < 500 else "proposal_unavailable"
                decision_req["governance_proposal_status"] = f"{kind}:{code}" if code is not None else kind
                decision_req["governance_proposal_error"] = {
                    "owner": "governance", "http_status": code,
                    "reason": _error_detail(proposal_failure) or proposal_failure.__class__.__name__}
                return decision_req

        decision_req["governance_proposal_status"] = "routed_to_open_owner_disposition"
        decision_req["human_inbox_action_url"] = f"/management/approvals?approval={deterministic_proposal_id}"
        return decision_req

    # -- paper automated gate for pool risk-policy proposals (operator decision P-4) ------------------
    def _paper_gate_decide_risk_policy(self, proposal: Mapping[str, Any], *, outcome: str, rationale: str) -> dict[str, Any]:
        """Review then decide through Governance; a refused/lost mutation is raised, never assumed to succeed."""
        decision_id = str(proposal.get("decision_id") or "")
        path = f"/api/governance/approvals/{_path_id(decision_id)}"
        errors: list[Exception] = []

        def not_ready(step: str, want: str) -> Callable[[Mapping[str, Any]], None]:
            def check(receipt: Mapping[str, Any]) -> None:
                state = str(receipt.get("decision_state") or "")
                if step == "decide" and state == "decided" and str(receipt.get("decision") or "") != outcome:
                    raise _PaperGateFailure(
                        "decided_with_different_outcome", step, None,
                        f"Governance readback decided {receipt.get('decision')!r}, expected {outcome!r}")
                ok = state == "decided" if step == "decide" else state in {"under_review", "decided"}
                if not ok:
                    exc = errors[-1] if errors else None
                    from .persona_paper_enrollment import _error_detail, _error_status
                    code = _error_status(exc) if exc is not None else None
                    detail = (_error_detail(exc) or exc.__class__.__name__) if exc is not None else (
                        f"Governance readback is {state!r}, expected {want}")
                    unavailable = exc is not None and (code is None or code >= 500)
                    raise _PaperGateFailure(
                        "gate_unavailable" if unavailable else "gate_refused", step, code, detail)
            return check

        review = self._transition_then_get(
            owner="governance", get_path=path, post_path=f"{path}/review",
            payload=lambda curr: {"actor_role": "automated_gate", "actor_id": self.governance_actor_id,
                                  "expected_version": int((curr or {}).get("version") or 0)},
            ready=lambda r: str(r.get("decision_state") or "") in {"under_review", "decided"},
            validate=not_ready("review", "under_review"), on_mutation_error=errors.append)
        errors.clear()
        return self._transition_then_get(
            owner="governance", get_path=path, post_path=f"{path}/decide",
            payload=lambda curr: {
                "actor_role": "automated_gate", "actor_id": self.governance_actor_id,
                "outcome": outcome, "rationale": rationale,
                "candidate_digest": (curr or review).get("candidate_digest"),
                "expires_at": (curr or review).get("expires_at"),
                "expected_version": int((curr or {}).get("version") or 0)},
            ready=lambda r: r.get("decision_state") == "decided",
            validate=not_ready("decide", "decided"), on_mutation_error=errors.append)

    def _apply_paper_gate_to_risk_policy_proposal(
        self, proposal: Mapping[str, Any], decision_req: dict[str, Any], *, requested_profile: str,
    ) -> None:
        """Approve a paper pool policy inside the outer bounds; otherwise refuse it with the named field.

        Idempotent per proposal (review/decide short-circuit on readback); an unavailable gate leaves the
        proposal pending for the next pass.  No AI or service principal casts a human vote.
        ``paper_gate_status`` says ``decided_*`` only when the Governance readback is decided with that outcome.
        """
        if self.transport is None:
            return
        meta = proposal.get("metadata") if isinstance(proposal.get("metadata"), Mapping) else {}
        subj = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
        draft = subj.get("risk_officer_draft") if isinstance(subj.get("risk_officer_draft"), Mapping) else {}
        policy = draft.get("policy") if isinstance(draft.get("policy"), Mapping) else None
        if policy:
            model, reason = validate_drafted_pool_risk_policy(policy, profile=requested_profile)
            outcome = "approved" if model is not None else "rejected"
            rationale = ("Paper pool risk policy verified inside operator outer bounds (gross_limit, max_leverage, "
                         "max_single_name_weight, min_cash_reserve_pct; P-4 automated gate)"
                         if model is not None else f"Refused by paper gate: {reason}")
        else:
            outcome = "rejected"
            rationale = (f"Refused by paper gate: risk_officer_draft absent; the {requested_profile} profile requires "
                         "gross_limit, max_leverage, max_single_name_weight and min_cash_reserve_pct within the operator outer bounds; "
                         "superseded by the risk-officer draft")
        try:
            receipt = self._paper_gate_decide_risk_policy(proposal, outcome=outcome, rationale=rationale)
        except _PaperGateFailure as exc:
            decision_req["paper_gate_status"] = (
                f"{exc.kind}:{exc.http_status}" if exc.http_status is not None else exc.kind)
            decision_req["paper_gate_error"] = {
                "owner": "governance", "step": exc.step, "http_status": exc.http_status, "reason": exc.detail}
            return
        except Exception as exc:
            decision_req["paper_gate_status"] = "gate_unavailable"
            decision_req["paper_gate_error"] = {
                "owner": "governance", "step": "unknown", "http_status": None,
                "reason": str(exc) or type(exc).__name__}
            return
        decision_req["governance_proposal_state"] = str(receipt.get("decision_state") or "")
        decision_req["governance_proposal_outcome"] = str(receipt.get("decision") or outcome)
        decision_req["paper_gate_status"] = f"decided_{outcome}_by_automated_gate"
        if outcome == "rejected":
            decision_req["paper_gate_reason"] = rationale

    # -- renewal of a gate-approved paper pool risk policy -------------------------------------------
    @staticmethod
    def _is_missing_draft_rejection(item: Mapping[str, Any]) -> bool:
        """True only for an automated-gate refusal whose sole cause is a proposal without the risk-officer draft.

        A human rejection, or a refusal that names a limit, is a decision about the policy and stays final.
        """
        if str(item.get("decision_state") or "") != "decided" or str(item.get("decision") or "") != "rejected":
            return False
        if str(item.get("actor_role") or "") != "automated_gate":
            return False
        meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        subject = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
        draft = subject.get("risk_officer_draft") if isinstance(subject.get("risk_officer_draft"), Mapping) else {}
        if isinstance(draft.get("policy"), Mapping) and draft.get("policy"):
            return False
        return "risk_officer_draft absent" in str(item.get("rationale") or "")

    def _carried_risk_officer_draft(
        self, *, tenant_id: str, pool_id: str, requested_profile: str, policy_digest: str,
    ) -> DraftResult | None:
        """The risk-officer draft an earlier proposal of this very policy carried (same digest), if any."""
        if self.transport is None:
            return None
        try:
            items = self._governance_approval_reader().list_for_target(
                target_type="capital_pool_activation", target_id=pool_id, decision_state=None,
                max_items=MAX_PENDING_SUPERSEDE, history=True)
        except Exception:
            return None
        for item in items:
            if item.get("tenant_id") != tenant_id:
                continue
            meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            subject = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
            if subject.get("requested_risk") != requested_profile:
                continue
            stored = subject.get("risk_officer_draft") if isinstance(subject.get("risk_officer_draft"), Mapping) else {}
            policy = stored.get("policy")
            if isinstance(policy, Mapping) and risk_policy_digest(policy) == policy_digest:
                return DraftResult(True, policy=deepcopy(dict(policy)), provenance=dict(stored.get("provenance") or {}))
        return None

    @staticmethod
    def _renewal_decision_id(previous_decision_id: str) -> str:
        digest = hashlib.sha256(f"paper-risk-renewal|{previous_decision_id}".encode("utf-8")).hexdigest()[:20]
        return f"apv-risk-renew-{digest}"

    def _renewal_proposer(self, tenant_id: str) -> tuple[Any, str]:
        proposer = self.governance_proposer
        actor = self.governance_proposer_actor_id
        if proposer is None:
            env_id = os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID", "").strip()
            has_token = bool(os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN", "").strip()
                             or os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN_FILE", "").strip())
            if env_id and has_token:
                from .persona_paper_enrollment import HttpGovernanceProposer

                proposer, actor = HttpGovernanceProposer(tenant_id=tenant_id), env_id
        return proposer, str(actor or getattr(proposer, "governance_subject", None) or self.governance_actor_id)

    def renew_paper_risk_policy_approval(
        self, *, tenant_id: str, persona_id: str, pool_id: str, now: datetime | None = None,
        threshold: timedelta = PAPER_RISK_RENEWAL_THRESHOLD, force: bool = False,
    ) -> dict[str, Any]:
        """Renew the pool's gate-approved risk-officer draft through the same paper gate before it expires.

        The renewed decision carries the same policy id, version, digest, draft and provenance; no limit changes.
        Its id derives from the previous decision id, so a retry, a restart or a second replica converges on one
        decision.  A policy that no longer validates inside the outer bounds is not renewed (``redraft_requested``
        with the reason).  A pool that never had an approved policy is never drafted here (``not_applicable``).
        Governance unavailable leaves the old approval in place (``governance_unavailable``); it is retried.
        """
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        def out(status: str, **fields: Any) -> dict[str, Any]:
            return {"status": status, "capital_pool_id": pool_id, "checked_at": _format_owner_time(now), **fields}

        pool_path = f"/api/capital-pools/{_path_id(pool_id)}"
        try:
            pool = self._owner_get("capital", pool_path)
        except Exception as exc:
            return out("owner_unavailable", reason=f"capital: {exc}"[:300])
        meta = dict(pool.get("metadata")) if isinstance(pool, Mapping) and isinstance(pool.get("metadata"), Mapping) else {}
        if (
            pool is None
            or not self._formal_tenant_matches(pool, tenant_id)
            or meta.get("execution_context") != "paper"
            or pool.get("single_runtime_enforced") is not True
            or meta.get("persona_id") not in (None, "", persona_id)
        ):
            return out("not_applicable", reason="no single-runtime paper pool for this persona")
        profile = str(meta.get("requested_risk_profile") or "").strip()
        previous_id = str(meta.get("profile_mapping_approval_id") or "").strip()
        if not profile or not previous_id or meta.get("require_approved_profile_mapping") is not True:
            return out("not_applicable", reason="pool has no bound approved risk policy")
        try:
            previous = self._owner_get("governance", f"/api/governance/approvals/{_path_id(previous_id)}")
        except Exception as exc:
            return out("governance_unavailable", approval_id=previous_id, reason=f"governance: {exc}"[:300])
        pmeta = previous.get("metadata") if isinstance(previous, Mapping) and isinstance(previous.get("metadata"), Mapping) else {}
        subject = pmeta.get("subject") if isinstance(pmeta.get("subject"), Mapping) else {}
        stored = subject.get("risk_officer_draft") if isinstance(subject.get("risk_officer_draft"), Mapping) else {}
        policy = stored.get("policy") if isinstance(stored.get("policy"), Mapping) else None
        if (
            previous is None
            or previous.get("tenant_id") != tenant_id
            or previous.get("target_id") != pool_id
            or previous.get("decision_state") != "decided"
            or previous.get("decision") != "approved"
            or previous.get("revoked_at") not in (None, "")
            or previous.get("superseded_by") not in (None, "")
            or subject.get("requested_risk") != profile
            or policy is None
            or risk_policy_digest(policy) != subject.get("risk_policy_digest")
        ):
            return out("not_applicable", approval_id=previous_id,
                       reason="bound approval is not a gate-approved risk-officer draft for this pool")
        try:
            expires = _parse_owner_time(previous.get("expires_at"), label="approval expires_at")
            decided = _parse_owner_time(previous.get("created_at"), label="approval created_at")
        except PersonaProvisioningCoordinationError as exc:
            return out("not_applicable", approval_id=previous_id, reason=str(exc))
        remaining = expires - now
        base = {"approval_id": previous_id, "approval_expires_at": _format_owner_time(expires),
                "remaining_seconds": int(remaining.total_seconds())}
        # The pool has a bound gate-approved policy: anything older it superseded is closed by Governance
        # (idempotent, never raised into the renewal result).
        stale = self.close_superseded_risk_policy_proposals(tenant_id=tenant_id, pool_id=pool_id, keep_id=previous_id)
        if stale["closed"] or stale["failed"]:
            base["superseded_proposals"] = stale
        if remaining >= threshold and not force:
            return out("not_due", **base)
        renewed_id = self._renewal_decision_id(previous_id)
        try:
            existing = self._owner_get("governance", f"/api/governance/approvals/{_path_id(renewed_id)}")
        except Exception as exc:
            return out("governance_unavailable", reason=f"governance: {exc}"[:300], **base)
        if existing is None and now - decided < PAPER_RISK_RENEWAL_DECISION_INTERVAL and remaining > timedelta(0):
            return out("decision_rate_limited", reason="a decision was made for this pool less than 12 hours ago", **base)
        model, invalid = validate_drafted_pool_risk_policy(policy, profile=profile)
        if model is None:
            return out("redraft_requested", reason=f"approved policy no longer validates: {invalid}", **base)

        proposal = existing
        if proposal is None:
            try:
                proposer, proposer_actor = self._renewal_proposer(tenant_id)
            except Exception as exc:
                return out("governance_unavailable", reason=f"proposer: {exc}"[:300], **base)
            if proposer is None:
                return out("governance_unavailable", reason="no governance proposer principal configured", **base)
            digest = risk_policy_digest(policy)
            direction = compare_risk_policies(policy, policy)
            body = {
                "expected_version": 0, "decision_id": renewed_id, "target_type": "capital_pool_activation",
                "target_id": pool_id,
                "target_version": self._pool_with_authoritative_approval_digest(pool, tenant_id=tenant_id)["approval_digest"],
                "risk_level": "low", "capital_pool_id": pool_id, "persona_id": persona_id, "tenant_id": tenant_id,
                "owner_user_id": proposer_actor, "candidate_digest": digest,
                "expires_at": _format_owner_time(now + APPROVAL_TTL - timedelta(minutes=5)),
                "subject": {
                    "pool_id": pool_id, "risk_direction": direction["risk_direction"],
                    "risk_comparison": dict(direction["risk_comparison"]), "requested_risk": profile,
                    "risk_policy_id": subject.get("risk_policy_id"),
                    "risk_policy_version": subject.get("risk_policy_version"), "risk_policy_digest": digest,
                    "absence_source": "paper_approval_renewal", "renewal_of": previous_id,
                    "disposition_task_id": subject.get("disposition_task_id"),
                    "hosted_proof_task_id": subject.get("hosted_proof_task_id"),
                    "risk_officer_draft": {"provenance": dict(stored.get("provenance") or {}),
                                           "policy": deepcopy(dict(policy))},
                },
            }
            try:
                proposer.post("governance", "/api/governance/approvals", body)
            except Exception:
                pass  # a lost response is reconciled by the readback below
            try:
                proposal = self._owner_get("governance", f"/api/governance/approvals/{_path_id(renewed_id)}")
            except Exception as exc:
                return out("governance_unavailable", reason=f"governance: {exc}"[:300], **base)
            if proposal is None:
                return out("governance_unavailable", reason="renewal proposal has no readback", **base)
        gate: dict[str, Any] = {}
        if str(proposal.get("decision_state") or "") != "decided":
            self._apply_paper_gate_to_risk_policy_proposal(proposal, gate, requested_profile=profile)
            try:
                proposal = self._owner_get("governance", f"/api/governance/approvals/{_path_id(renewed_id)}") or proposal
            except Exception:
                pass
        if proposal.get("decision_state") != "decided":
            return out("governance_unavailable", reason=str(gate.get("paper_gate_status") or "gate pending"),
                       gate_error=gate.get("paper_gate_error"),
                       renewed_approval_id=renewed_id, **base)
        if proposal.get("decision") != "approved":
            return out("gate_rejected", renewed_approval_id=renewed_id,
                       reason=str(proposal.get("rationale") or "renewal refused"), **base)
        try:
            self.transport.patch("capital", f"{pool_path}/status", {
                "actor_id": self.actor_id, "actor_role": "capital.admin",
                "status": str(pool.get("status") or "active"),
                "metadata": {"profile_mapping_approval_id": renewed_id,
                             "profile_mapping_renewed_from": previous_id},
            })
            fresh = self._owner_get("capital", pool_path)
        except Exception as exc:
            return out("owner_unavailable", reason=f"capital: {exc}"[:300], renewed_approval_id=renewed_id, **base)
        resolution = resolve_pool_risk_policy_binding(
            capital_pool_id=pool_id, pool=self._pool_with_authoritative_approval_digest(fresh or pool, tenant_id=tenant_id),
            expected_tenant_id=tenant_id, expected_stage="paper", requested_profile=profile,
            require_approved_profile_mapping=True, approval_reader=self._governance_approval_reader(),
            require_active=True, require_limits=("gross_limit",), raise_on_error=False)
        if not resolution.resolved:
            return out("renewal_unresolved", renewed_approval_id=renewed_id, reason=str(resolution.reason), **base)
        return out("renewed", renewed_approval_id=renewed_id, renewed_from=previous_id,
                   approval_id=renewed_id, approval_expires_at=str(proposal.get("expires_at") or ""))

    @staticmethod
    def _renewal_next_check(outcome: Mapping[str, Any], now: datetime) -> datetime:
        status = str(outcome.get("status") or "")
        if status in {"not_due", "renewed"} and outcome.get("approval_expires_at"):
            try:
                due = _parse_owner_time(outcome["approval_expires_at"], label="approval expires_at") - PAPER_RISK_RENEWAL_THRESHOLD
                return max(due, now + timedelta(seconds=1))
            except PersonaProvisioningCoordinationError:
                pass
        return now + PAPER_RISK_RENEWAL_ATTEMPT_INTERVAL

    @staticmethod
    def paper_risk_renewal_due(reference: Any, now: datetime) -> bool:
        """Cheap local check used by the reconcile pass: no owner call until the next check time is reached."""
        if not isinstance(reference, Mapping) or not reference.get("next_check_at"):
            return True
        try:
            return now >= _parse_owner_time(reference["next_check_at"], label="next_check_at")
        except PersonaProvisioningCoordinationError:
            return True

    def reconcile_paper_risk_policy_renewal(
        self, record: ProvisioningRecord, *, now: datetime | None = None,
    ) -> ProvisioningRecord:
        """Reconcile step: renew a succeeded trading persona's paper risk-policy approval before it lapses.

        Lease-guarded (another holder means skip) and idempotent per previous decision, so safe across replicas
        and restarts.  The outcome and next check time live on the ledger record: validity above the threshold
        costs no owner call.
        """
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if record.state != "succeeded" or not self.paper_risk_renewal_due(
                record.references.get(PAPER_RISK_RENEWAL_REFERENCE), now):
            return record
        active = self.store.acquire(
            record.tenant_id, record.idempotency_key, lease_owner=self.lease_owner, lease_seconds=self.lease_seconds)
        if active is None:
            return record
        try:
            if not self.paper_risk_renewal_due(active.references.get(PAPER_RISK_RENEWAL_REFERENCE), now):
                return active
            ids = deterministic_provisioning_ids(active)
            try:
                outcome = self.renew_paper_risk_policy_approval(
                    tenant_id=active.tenant_id, persona_id=active.persona_id, pool_id=ids.capital_pool_id, now=now)
            except Exception as exc:  # noqa: BLE001 - retried; the old approval stays in place
                outcome = {"status": "governance_unavailable", "reason": f"{exc.__class__.__name__}: {exc}"[:300]}
            if outcome.get("status") == "redraft_requested":
                try:
                    redraft = self.repair_paper_pool_risk_policy(
                        tenant_id=active.tenant_id, persona_id=active.persona_id,
                        capital_pool_id=ids.capital_pool_id, requested_by="paper-risk-policy-renewal")
                    outcome["redraft"] = (redraft.get("owner_decision_request") or {}).get("governance_proposal_status")
                except Exception as exc:  # noqa: BLE001
                    outcome["redraft"] = f"redraft_failed: {exc}"[:200]
            previous = active.references.get(PAPER_RISK_RENEWAL_REFERENCE)
            previous = previous if isinstance(previous, Mapping) else {}
            attempted = outcome.get("status") not in {"not_due", "not_applicable"}
            decided = outcome.get("status") in {"renewed", "gate_rejected"}
            active.references[PAPER_RISK_RENEWAL_REFERENCE] = {
                "status": outcome.get("status"),
                "checked_at": _format_owner_time(now),
                "next_check_at": _format_owner_time(self._renewal_next_check(outcome, now)),
                "approval_id": outcome.get("approval_id"),
                "approval_expires_at": outcome.get("approval_expires_at"),
                "last_attempt_at": _format_owner_time(now) if attempted else previous.get("last_attempt_at"),
                "last_decision_at": _format_owner_time(now) if decided else previous.get("last_decision_at"),
                **({"reason": outcome["reason"]} if outcome.get("reason") else {}),
                **({"redraft": outcome["redraft"]} if outcome.get("redraft") else {}),
            }
        finally:
            active = self.store.release(active, lease_owner=self.lease_owner, lease_seconds=self.lease_seconds)
        return active

    def _close_superseded_risk_policy_proposals(
        self, *, tenant_id: str, pool_id: str, keep_id: str, requested_profile: str,
        matches_request: Callable[[Mapping[str, Any]], bool], decision_req: dict[str, Any],
    ) -> None:
        """Close the pool's other open paper policy proposals (e.g. v1.0) that the bound policy superseded."""
        outcome = self.close_superseded_risk_policy_proposals(tenant_id=tenant_id, pool_id=pool_id, keep_id=keep_id)
        if outcome["closed"]:
            decision_req["superseded_proposals_closed"] = outcome["closed"]
        if outcome["failed"]:
            decision_req["superseded_proposals_close_failed"] = outcome["failed"]

    def close_superseded_risk_policy_proposals(
        self, *, tenant_id: str, pool_id: str, keep_id: str | None = None,
    ) -> dict[str, list[Any]]:
        """Ask Governance to close every open paper policy proposal of the pool that its bound policy superseded.

        Governance verifies supersession on its own records (close-only authority, never an approval), so this
        only names the candidates.  At most one transition per stale proposal; an already closed proposal is not
        listed as open and costs nothing.  A failed close is reported in ``failed`` and never raised.
        """
        outcome: dict[str, list[Any]] = {"closed": [], "failed": []}
        if self.transport is None or not self.governance_actor_id:
            return outcome
        try:
            items = self._governance_approval_reader().list_for_target(
                target_type="capital_pool_activation", target_id=pool_id, decision_state=None,
                max_items=MAX_PENDING_SUPERSEDE, history=True)
        except Exception:
            return outcome
        for item in items:
            decision_id = str(item.get("decision_id") or "")
            if (not decision_id or decision_id == keep_id or item.get("tenant_id") != tenant_id
                    or str(item.get("decision_state") or "") not in {"proposed", "under_review"}):
                continue
            path = f"/api/governance/approvals/{_path_id(decision_id)}"
            try:
                current = self._owner_get("governance", path)
                if not isinstance(current, Mapping) or str(current.get("decision_state") or "") not in {
                        "proposed", "under_review"}:
                    continue
                self.transport.post("governance", f"{path}/close-superseded", {
                    "actor_role": "automated_gate", "actor_id": self.governance_actor_id,
                    "expected_version": int(current.get("version") or 0)})
                after = self._owner_get("governance", path)
                if isinstance(after, Mapping) and str(after.get("decision_state") or "") == "superseded":
                    outcome["closed"].append(decision_id)
                else:
                    outcome["failed"].append({"decision_id": decision_id, "reason": "readback is not superseded"})
            except Exception as exc:  # noqa: BLE001 - reported, retried on the next pass
                from .persona_paper_enrollment import _error_detail, _error_status

                outcome["failed"].append({
                    "decision_id": decision_id, "http_status": _error_status(exc),
                    "reason": str(_error_detail(exc) or exc.__class__.__name__)[:200]})
        return outcome

    @staticmethod
    def _verified_requested_profile(*sources: tuple[str, str | None]) -> str:
        present = {name: str(value).strip() for name, value in sources if value is not None and str(value).strip()}
        if not present or any(value not in {"low", "medium", "high"} for value in present.values()):
            code = "requested_profile_source_missing" if not present else "requested_profile_source_invalid"
        elif len(set(present.values())) != 1:
            code = "requested_profile_source_conflict"
        else:
            return next(iter(present.values()))
        raise PersonaProvisioningCoordinationError(
            f"Requested risk profile provenance rejected: {code}; sources={present!r}",
            reason_code=code,
            resolution_status="unresolved_profile_mapping",
        )

    def _persist_requested_profile(
        self, *, pool: Mapping[str, Any], tenant_id: str, persona_id: str,
        pool_id: str, profile: str, dry_run: bool = False,
    ) -> dict[str, Any]:
        meta = pool.get("metadata") if isinstance(pool.get("metadata"), Mapping) else {}
        if (
            pool.get("pool_id") != pool_id
            or not self._formal_tenant_matches(pool, tenant_id)
            or meta.get("persona_id") != persona_id
            or meta.get("execution_context") != "paper"
            or pool.get("single_runtime_enforced") is not True
        ):
            raise PersonaProvisioningCoordinationError("requested_profile_identity_invalid")
        self._verified_requested_profile(("request", profile), ("capital_pool", meta.get("requested_risk_profile")))
        if dry_run or (meta.get("requested_risk_profile") == profile and meta.get("require_approved_profile_mapping") is True):
            return dict(pool)
        path = f"/api/capital-pools/{_path_id(pool_id)}"
        try:
            self.transport.patch("capital", f"{path}/status", {
                "actor_id": self.actor_id,
                "actor_role": "capital.admin",
                "expected_persona_id": persona_id,
                "metadata": {"requested_risk_profile": profile, "require_approved_profile_mapping": True},
            })
        except Exception:
            # A lost response may follow a successful owner commit. Only owner
            # readback can recover it; a missing write still fails closed.
            pass
        readback = self._owner_get("capital", path)
        read_meta = readback.get("metadata") if isinstance(readback, Mapping) else None
        read_meta = read_meta if isinstance(read_meta, Mapping) else {}
        if (
            not isinstance(readback, Mapping)
            or readback.get("pool_id") != pool_id
            or not self._formal_tenant_matches(readback, tenant_id)
            or readback.get("single_runtime_enforced") is not True
            or read_meta.get("execution_context") != "paper"
            or read_meta.get("persona_id") != persona_id
            or read_meta.get("requested_risk_profile") != profile
            or read_meta.get("require_approved_profile_mapping") is not True
        ):
            raise PersonaProvisioningCoordinationError("requested_profile_persistence_readback_failed")
        return dict(readback)

    def _record_risk_profile_context(self, record: ProvisioningRecord) -> tuple[str | None, bool, str | None]:
        """Recover an old omitted request only from its real tenant-owned Persona.

        Do not normalize old ledger payloads or change their idempotency hashes.
        A pool/catalog/strategy default is never evidence of a Persona request.
        """
        profile, required, approval_id = self._extract_request_risk_profile_context(record.request_payload)
        if profile is not None or not required:
            return profile, required, approval_id
        try:
            owner = self.persona_owner_provider()
            getter = getattr(owner, "get_persona", None)
            persona = getter(record.persona_id) if callable(getter) else None
        except Exception:
            persona = None
        if not isinstance(persona, Mapping):
            return profile, required, approval_id
        meta = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}
        prior_error = meta.get("provisioning_error")
        if (isinstance(prior_error, Mapping)
                and prior_error.get("reason_code") == "requested_profile_source_missing"):
            # Failed legacy materialization may carry the old display default.
            # A missing-source failure cannot manufacture provenance for retry.
            return profile, required, approval_id
        ids = deterministic_provisioning_ids(record)
        tenant_values = [v for v in (persona.get("tenant_id"), persona.get("tenantId"),
                                     meta.get("tenant_id"), meta.get("tenantId")) if v not in (None, "")]
        identity_values = [v for v in (persona.get("persona_id"), persona.get("id")) if v not in (None, "")]
        if (not identity_values or any(v != record.persona_id for v in identity_values)
                or not tenant_values or any(v != record.tenant_id for v in tenant_values)
                or meta.get("provisioning_request_hash") not in (None, "", record.request_hash)
                or any(meta.get(k) not in (None, "", ids.capital_pool_id)
                       for k in ("capital_pool_id", "legacy_paper_capital_pool_id", "internal_paper_capital_pool_id"))):
            raise PersonaProvisioningCoordinationError("requested_profile_identity_invalid")
        owner_profile = self._extract_request_risk_profile_context(persona)[0]
        metadata_profile = self._extract_request_risk_profile_context({
            "risk_level": meta.get("risk_level"), "metadata": meta,
        })[0]
        if owner_profile is not None or metadata_profile is not None:
            profile = self._verified_requested_profile(("persona", owner_profile), ("persona.metadata", metadata_profile))
            record.references["requested_risk_profile_provenance"] = {
                "source": "verified_persona_owner", "persona_id": record.persona_id,
                "tenant_id": record.tenant_id, "requested_profile": profile,
            }
        return profile, required, approval_id

    @staticmethod
    def _extract_request_risk_profile_context(
        req: Mapping[str, Any],
        *,
        request_hash: str = "",
    ) -> tuple[str | None, bool, str | None]:
        req_meta = req.get("metadata") if isinstance(req.get("metadata"), Mapping) else {}
        has_explicit_policy = "risk_policy" in req or "risk_policy" in req_meta
        raw_ref = req.get("risk_policy_ref")
        if raw_ref is None or not str(raw_ref).strip():
            raw_ref = req.get("riskPolicyRef") or req_meta.get("risk_policy_ref")
        has_explicit_ref = bool(raw_ref is not None and str(raw_ref).strip())

        risk_profile_map = req.get("risk_profile") if isinstance(req.get("risk_profile"), Mapping) else {}
        camel_profile = req.get("riskProfile") if isinstance(req.get("riskProfile"), Mapping) else {}
        traits = req.get("traits") if isinstance(req.get("traits"), Mapping) else {}
        meta_profile = req_meta.get("risk_profile")
        meta_profile_map = meta_profile if isinstance(meta_profile, Mapping) else {}
        profile_sources = [
            ("request.risk", req.get("risk")),
            ("request.risk_level", req.get("risk_level")),
            ("request.riskLevel", req.get("riskLevel")),
            ("request.risk_profile.risk_level", risk_profile_map.get("risk_level")),
            ("request.risk_profile.riskLevel", risk_profile_map.get("riskLevel")),
            ("request.riskProfile.risk_level", camel_profile.get("risk_level")),
            ("request.riskProfile.riskLevel", camel_profile.get("riskLevel")),
            ("request.metadata.requested_risk_profile", req_meta.get("requested_risk_profile")),
            ("request.metadata.risk_profile", None if isinstance(meta_profile, Mapping) else meta_profile),
            ("request.metadata.risk_profile.risk_level", meta_profile_map.get("risk_level")),
            ("request.metadata.risk_profile.riskLevel", meta_profile_map.get("riskLevel")),
            ("request.metadata.requested_risk", req_meta.get("requested_risk")),
        ]
        # Historical normal requests expressed their risk only in traits.
        # Once a financial profile/policy is explicit, it remains authoritative;
        # the expression trait does not override that independent contract.
        if (not has_explicit_policy and not has_explicit_ref
                and not any(value is not None and str(value).strip() for _, value in profile_sources)):
            profile_sources.append(("request.traits.risk_appetite", traits.get("risk_appetite")))
        requested_profile = (
            PersonaProvisioningCoordinator._verified_requested_profile(*profile_sources)
            if any(value is not None and str(value).strip() for _, value in profile_sources)
            else None
        )
        explicit_require_flag = (
            req.get("require_approved_profile_mapping")
            if "require_approved_profile_mapping" in req
            else req_meta.get("require_approved_profile_mapping")
        )
        if explicit_require_flag is False and requested_profile is None:
            require_approved_mapping = False
        else:
            require_approved_mapping = bool(
                requested_profile is not None
                or (not has_explicit_policy and not has_explicit_ref)
                or explicit_require_flag is True
            )
        candidate_approval_id = (
            str(
                req.get("profile_mapping_approval_id")
                or req.get("approval_decision_id")
                or req_meta.get("profile_mapping_approval_id")
                or req_meta.get("approval_decision_id")
                or ""
            ).strip()
            or None
        )
        return requested_profile, require_approved_mapping, candidate_approval_id

    @staticmethod
    def _candidate_pool_with_approval_digest(
        *,
        pool_id: str,
        tenant_id: str,
        metadata: Mapping[str, Any],
        risk_policy_ref: str | None = None,
        base_pool: Mapping[str, Any] | None = None,
        currency: str | None = None,
        budget: Any = None,
        owner_id: str | None = None,
        owner_type: str | None = None,
        single_runtime_enforced: bool | None = None,
    ) -> dict[str, Any]:
        base = dict(base_pool) if isinstance(base_pool, Mapping) else {}
        candidate: dict[str, Any] = {
            **base,
            "pool_id": pool_id,
            "tenant_id": tenant_id,
            "owner_id": owner_id if owner_id is not None else base.get("owner_id", tenant_id),
            "owner_type": owner_type if owner_type is not None else base.get("owner_type", "org"),
            "currency": currency if currency is not None else base.get("currency", "USD"),
            "budget": budget if (budget is not None or "budget" not in base) else base.get("budget"),
            "risk_policy_ref": risk_policy_ref,
            "single_runtime_enforced": (
                single_runtime_enforced
                if single_runtime_enforced is not None
                else base.get("single_runtime_enforced", True)
            ),
            "metadata": dict(metadata),
        }
        candidate["approval_digest"] = capital_pool_approval_digest(candidate)
        return candidate

    @staticmethod
    def _ambiguous_profile_resolution(
        *,
        pool_id: str,
        tenant_id: str,
        requested_profile: str,
        auto_res: Any,
    ) -> BoundPoolRiskPolicyResolution:
        pol_ids = list(getattr(auto_res, "ambiguous_policy_ids", ()) or ())
        dec_ids = list(getattr(auto_res, "ambiguous_decision_ids", ()) or ())
        reason_text = (
            str(getattr(auto_res, "reason", None) or "").strip()
            or (
                f"Ambiguous risk policy candidates for profile {requested_profile!r} on pool {pool_id!r}: "
                f"policies={pol_ids!r}, decisions={dec_ids!r}; explicit risk_policy_ref or "
                f"single authoritative approval is required."
            )
        )
        owner_decision_req: dict[str, Any] = {
            "policy_owner": "services/capital/risk_policy.py",
            "governance_surface": "services/control-plane/governance/risk_policy.py",
            "accountable_domain": "capital_risk_policy_owner",
            "absence_source": "policy_candidates_ambiguous",
            "requested_profile": requested_profile,
            "capital_pool_id": pool_id,
            "tenant_id": tenant_id,
            "stage": "paper",
            "ambiguous_policy_ids": pol_ids,
            "ambiguous_approval_decision_ids": dec_ids,
            "decision_required": reason_text,
            "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
            "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
            "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
            "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
        }
        return BoundPoolRiskPolicyResolution(
            capital_pool_id=pool_id,
            resolved=False,
            status="ambiguous_profile_mapping",
            source="unresolved",
            policy=None,
            risk_policy_id=None,
            risk_policy_version=None,
            risk_policy_digest=None,
            tenant_id=tenant_id,
            policy_tenant_id=tenant_id,
            stage="paper",
            requested_profile=requested_profile,
            profile_mapping_status="ambiguous_owner_decision_required",
            profile_mapping_approval_id=None,
            effective_limits={},
            reason_code="risk_policy_profile_mapping_ambiguous",
            reason=reason_text,
            allow_new_or_increased_risk=False,
            allow_risk_reducing_actions=True,
            owner_decision_request=owner_decision_req,
        )

    @staticmethod
    def _pool_with_authoritative_approval_digest(
        pool: Mapping[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        hydrated = dict(pool)
        if tenant_id is not None and not hydrated.get("tenant_id"):
            hydrated["tenant_id"] = tenant_id
        if not hydrated.get("approval_digest"):
            hydrated["approval_digest"] = capital_pool_approval_digest(hydrated)
        return hydrated

    def _resolve_requested_pool_risk_policy_with_provenance(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        base_pool: Mapping[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any], bool, str | None]:
        req = record.request_payload
        req_meta = req.get("metadata") if isinstance(req.get("metadata"), Mapping) else {}
        raw_ref = req.get("risk_policy_ref")
        if raw_ref is None or not str(raw_ref).strip():
            raw_ref = req.get("riskPolicyRef") or req_meta.get("risk_policy_ref")
        clean_ref = str(raw_ref).strip() if raw_ref is not None and str(raw_ref).strip() else None

        has_explicit_policy = "risk_policy" in req or "risk_policy" in req_meta
        explicit_policy = req.get("risk_policy") if "risk_policy" in req else req_meta.get("risk_policy")
        requested_profile, require_approved_mapping, candidate_approval_id = (
            self._record_risk_profile_context(record)
        )
        if isinstance(base_pool, Mapping):
            base_meta = base_pool.get("metadata") if isinstance(base_pool.get("metadata"), Mapping) else {}
            pool_profile = self._extract_request_risk_profile_context({"metadata": base_meta})[0]
            if pool_profile is not None:
                self._verified_requested_profile(("request", requested_profile), ("capital_pool", pool_profile))
        explicit_req_version = (
            str(req.get("risk_policy_version") or req_meta.get("risk_policy_version") or "").strip() or None
        )
        currency = self._capital_pool_currency(record)
        budget = req.get("budget")

        if (
            not clean_ref
            and not has_explicit_policy
            and isinstance(base_pool, Mapping)
            and str(base_pool.get("risk_policy_ref") or "").strip()
        ):
            base_meta = base_pool.get("metadata") if isinstance(base_pool.get("metadata"), Mapping) else {}
            if "risk_policy" not in base_meta or base_meta.get("risk_policy") is None:
                base_ref = str(base_pool.get("risk_policy_ref")).strip()
                base_ref_res = resolve_pool_risk_policy_binding(
                    capital_pool_id=ids.capital_pool_id,
                    pool=self._pool_with_authoritative_approval_digest(
                        base_pool,
                        tenant_id=record.tenant_id,
                    ),
                    expected_tenant_id=record.tenant_id,
                    expected_stage="paper",
                    requested_profile=requested_profile,
                    require_approved_profile_mapping=False,
                    approval_decision_id=candidate_approval_id,
                    approval_reader=self._governance_approval_reader(),
                    require_active=True,
                    require_limits=("gross_limit",),
                    raise_on_error=False,
                )
                if not base_ref_res.resolved or base_ref_res.policy is None:
                    raise PersonaProvisioningCoordinationError(
                        f"CapitalPool readback risk policy rejected: {base_ref_res.reason} ({base_ref_res.reason_code})",
                        reason_code=base_ref_res.reason_code,
                        resolution_status=base_ref_res.status,
                    )
                return base_ref, base_ref_res.policy.to_dict(), False, base_ref_res.profile_mapping_approval_id

        if require_approved_mapping and not clean_ref and not has_explicit_policy:
            eff_profile = self._verified_requested_profile(("provisioning_request", requested_profile))
            approval_reader = self._governance_approval_reader()
            auto_res = discover_approved_profile_risk_policy(
                resolved_pool_id=ids.capital_pool_id,
                effective_tenant_id=record.tenant_id,
                clean_stage="paper",
                clean_profile=eff_profile,
                expected_version=explicit_req_version,
                base_pool=base_pool,
                currency=currency,
                budget=budget,
                approval_decision_id=candidate_approval_id,
                approval_reader=approval_reader,
                require_limits=("gross_limit",),
            )
            if auto_res.resolved and auto_res.policy is not None and auto_res.approval_decision_id is not None:
                resolved_policy_dict = (
                    deepcopy(dict(auto_res.raw_policy))
                    if isinstance(auto_res.raw_policy, Mapping)
                    else auto_res.policy.to_dict()
                )
                resolved_ref = auto_res.risk_policy_ref
                resolved_apv_id = auto_res.approval_decision_id
                candidate_pool = self._candidate_pool_with_approval_digest(
                    base_pool=base_pool,
                    pool_id=ids.capital_pool_id,
                    tenant_id=record.tenant_id,
                    currency=currency,
                    budget=budget,
                    risk_policy_ref=resolved_ref,
                    metadata={
                        "tenant_id": record.tenant_id,
                        "requested_risk_profile": eff_profile,
                        "require_approved_profile_mapping": True,
                        "risk_policy": resolved_policy_dict,
                        "profile_mapping_approval_id": resolved_apv_id,
                    },
                )
                verified_res = resolve_pool_risk_policy_binding(
                    capital_pool_id=ids.capital_pool_id,
                    pool=candidate_pool,
                    expected_tenant_id=record.tenant_id,
                    expected_stage="paper",
                    requested_profile=eff_profile,
                    require_approved_profile_mapping=True,
                    approval_decision_id=resolved_apv_id,
                    approval_reader=approval_reader,
                    require_active=True,
                    require_limits=("gross_limit",),
                    raise_on_error=False,
                )
                if verified_res.resolved and verified_res.policy is not None:
                    return (
                        resolved_ref,
                        resolved_policy_dict,
                        True,
                        verified_res.profile_mapping_approval_id or resolved_apv_id,
                    )

            draft_result: DraftResult | None = None
            if auto_res.status == "ambiguous_profile_mapping":
                unmapped = self._ambiguous_profile_resolution(
                    pool_id=ids.capital_pool_id,
                    tenant_id=record.tenant_id,
                    requested_profile=eff_profile,
                    auto_res=auto_res,
                )
            else:
                base_existing_meta = (
                    dict(base_pool.get("metadata"))
                    if isinstance(base_pool, Mapping) and isinstance(base_pool.get("metadata"), Mapping)
                    else {}
                )
                persisted_unmapped_approval_id = (
                    candidate_approval_id
                    or str(
                        base_existing_meta.get("profile_mapping_approval_id")
                        or base_existing_meta.get("approval_decision_id")
                        or ""
                    ).strip()
                    or None
                )
                if auto_res.candidate_policy is not None and not self._is_legacy_default_policy(
                    auto_res.candidate_policy.to_dict(), ids.capital_pool_id, eff_profile
                ):
                    candidate_fallback = auto_res.candidate_policy.to_dict()
                else:
                    draft_result = self._risk_officer_policy_candidate(
                        tenant_id=record.tenant_id,
                        persona_id=record.persona_id,
                        pool_id=ids.capital_pool_id,
                        profile=eff_profile,
                        budget=budget,
                        currency=currency,
                        request=req,
                        base_pool=base_pool,
                        version=self._next_policy_version(
                            (auto_res.candidate_policy.version if auto_res.candidate_policy is not None
                             else (base_existing_meta.get("risk_policy") or {}).get("version")
                             if isinstance(base_existing_meta.get("risk_policy"), Mapping) else None),
                            explicit_req_version,
                        ),
                    )
                    candidate_fallback = self._draft_candidate_policy_dict(draft_result)
                candidate_pool = self._candidate_pool_with_approval_digest(
                    base_pool=base_pool,
                    pool_id=ids.capital_pool_id,
                    tenant_id=record.tenant_id,
                    currency=currency,
                    budget=budget,
                    risk_policy_ref=None,
                    metadata={
                        "tenant_id": record.tenant_id,
                        "requested_risk_profile": eff_profile,
                        "require_approved_profile_mapping": True,
                        "profile_auto_resolved": True,
                        "risk_policy": candidate_fallback,
                        **(
                            {"profile_mapping_approval_id": persisted_unmapped_approval_id}
                            if persisted_unmapped_approval_id
                            else {}
                        ),
                    },
                )
                unmapped = resolve_pool_risk_policy_binding(
                    capital_pool_id=ids.capital_pool_id,
                    pool=candidate_pool,
                    expected_tenant_id=record.tenant_id,
                    expected_stage="paper",
                    requested_profile=eff_profile,
                    require_approved_profile_mapping=True,
                    approval_decision_id=candidate_approval_id,
                    approval_reader=approval_reader,
                    require_active=True,
                    require_limits=("gross_limit",),
                    raise_on_error=False,
                )
            if isinstance(base_pool, Mapping):
                self._persist_requested_profile(
                    pool=base_pool, tenant_id=record.tenant_id, persona_id=record.persona_id,
                    pool_id=ids.capital_pool_id, profile=eff_profile,
                    dry_run=(base_pool.get("status") == "active" and "deployment_dispatch" not in record.references),
                )
            routed_req = self._route_or_reuse_risk_policy_owner_decision_request(
                tenant_id=record.tenant_id,
                persona_id=record.persona_id,
                pool_id=ids.capital_pool_id,
                requested_profile=eff_profile,
                resolution=unmapped,
                draft=draft_result,
            )
            if (
                isinstance(base_pool, Mapping)
                and str(base_pool.get("status") or "") == "active"
                and "deployment_dispatch" not in record.references
            ):
                pool_path = f"/api/capital-pools/{_path_id(ids.capital_pool_id)}"
                try:
                    self.transport.patch(
                        "capital",
                        f"{pool_path}/status",
                        {
                            "actor_id": self.actor_id,
                            "actor_role": "capital.admin",
                            "status": "suspended",
                            "metadata": {
                                "provisional_unresolved_risk_policy": True,
                                "requested_risk_profile": eff_profile,
                                "require_approved_profile_mapping": True,
                            },
                        },
                    )
                except Exception:
                    pass
            raise PersonaProvisioningCoordinationError(
                f"{unmapped.reason} ({unmapped.reason_code})",
                reason_code=unmapped.reason_code,
                absence_source=str(routed_req.get("absence_source") or ""),
                resolution_status=unmapped.status,
                owner_decision_request=routed_req,
            )

        if has_explicit_policy:
            if not isinstance(explicit_policy, Mapping):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool risk policy rejected: explicit risk_policy must be an object (risk_policy_invalid)",
                    reason_code="risk_policy_invalid",
                    resolution_status="invalid",
                )
            candidate_policy = deepcopy(dict(explicit_policy))
            candidate_pool = self._candidate_pool_with_approval_digest(
                base_pool=base_pool,
                pool_id=ids.capital_pool_id,
                tenant_id=record.tenant_id,
                currency=currency,
                budget=budget,
                risk_policy_ref=clean_ref,
                metadata={
                    "tenant_id": record.tenant_id,
                    "risk_policy": candidate_policy,
                    **(
                        {"requested_risk_profile": requested_profile}
                        if requested_profile is not None
                        else {}
                    ),
                    **(
                        {"require_approved_profile_mapping": True}
                        if require_approved_mapping
                        else {}
                    ),
                    **(
                        {"profile_mapping_approval_id": candidate_approval_id}
                        if candidate_approval_id is not None
                        else {}
                    ),
                },
            )
            validation = resolve_pool_risk_policy_binding(
                capital_pool_id=ids.capital_pool_id,
                pool=candidate_pool,
                expected_tenant_id=record.tenant_id,
                expected_stage="paper",
                requested_profile=requested_profile,
                require_approved_profile_mapping=require_approved_mapping,
                approval_decision_id=candidate_approval_id,
                approval_reader=self._governance_approval_reader(),
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
            if not validation.resolved:
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool risk policy rejected: {validation.reason} ({validation.reason_code})",
                    reason_code=validation.reason_code,
                    resolution_status=validation.status,
                )
            return clean_ref, candidate_policy, True, validation.profile_mapping_approval_id

        if clean_ref:
            ref_load = resolve_pool_risk_policy_binding(
                capital_pool_id=ids.capital_pool_id,
                pool={
                    "pool_id": ids.capital_pool_id,
                    "tenant_id": record.tenant_id,
                    "risk_policy_ref": clean_ref,
                    "metadata": {"tenant_id": record.tenant_id},
                },
                expected_tenant_id=record.tenant_id,
                expected_stage="paper",
                expected_version=explicit_req_version,
                requested_profile=requested_profile,
                require_approved_profile_mapping=False,
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
            if not ref_load.resolved or ref_load.policy is None:
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool risk policy rejected: {ref_load.reason} ({ref_load.reason_code})",
                    reason_code=ref_load.reason_code,
                    resolution_status=ref_load.status,
                )
            loaded_policy_dict = ref_load.policy.to_dict()
            candidate_pool = self._candidate_pool_with_approval_digest(
                base_pool=base_pool,
                pool_id=ids.capital_pool_id,
                tenant_id=record.tenant_id,
                currency=currency,
                budget=budget,
                risk_policy_ref=clean_ref,
                metadata={
                    "tenant_id": record.tenant_id,
                    "risk_policy": loaded_policy_dict,
                    **(
                        {"requested_risk_profile": requested_profile}
                        if requested_profile is not None
                        else {}
                    ),
                    **(
                        {"require_approved_profile_mapping": True}
                        if require_approved_mapping
                        else {}
                    ),
                    **(
                        {"profile_mapping_approval_id": candidate_approval_id}
                        if candidate_approval_id is not None
                        else {}
                    ),
                },
            )
            ref_validation = resolve_pool_risk_policy_binding(
                capital_pool_id=ids.capital_pool_id,
                pool=candidate_pool,
                expected_tenant_id=record.tenant_id,
                expected_stage="paper",
                expected_version=explicit_req_version,
                requested_profile=requested_profile,
                require_approved_profile_mapping=require_approved_mapping,
                approval_decision_id=candidate_approval_id,
                approval_reader=self._governance_approval_reader(),
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
            if not ref_validation.resolved or ref_validation.policy is None:
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool risk policy rejected: {ref_validation.reason} ({ref_validation.reason_code})",
                    reason_code=ref_validation.reason_code,
                    resolution_status=ref_validation.status,
                )
            return clean_ref, loaded_policy_dict, True, ref_validation.profile_mapping_approval_id

        # No explicit policy, reference or approved mapping applies. Never invent limits: the pool stays
        # pending until a requested profile is mapped through the risk-officer draft + Governance path.
        raise PersonaProvisioningCoordinationError(
            "CapitalPool risk policy pending: no approved applicable risk policy and no requested risk profile "
            "to draft one for (risk_policy_profile_required)",
            reason_code="risk_policy_profile_required",
            absence_source="policy_definition_and_approval_absent",
            resolution_status="unresolved_profile_mapping",
        )

    def _resolve_requested_pool_risk_policy(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        base_pool: Mapping[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any], bool]:
        ref, policy, is_explicit, _ = self._resolve_requested_pool_risk_policy_with_provenance(
            record,
            ids,
            base_pool=base_pool,
        )
        return ref, policy, is_explicit

    def _coordinate_capital_pool(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        currency = self._capital_pool_currency(record)
        pool_path = f"/api/capital-pools/{_path_id(ids.capital_pool_id)}"
        existing = self._owner_get("capital", pool_path)
        requested_profile, require_approved_mapping, candidate_approval_id = (
            self._record_risk_profile_context(record)
        )
        (
            requested_ref,
            requested_policy,
            has_explicit_policy_request,
            resolved_approval_id,
        ) = self._resolve_requested_pool_risk_policy_with_provenance(
            record,
            ids,
            base_pool=existing,
        )
        effective_approval_id = resolved_approval_id or candidate_approval_id
        effective_persisted_profile = requested_profile
        base_payload: dict[str, Any] = {
            "actor_id": self.actor_id,
            "actor_role": "admin",
            "pool_id": ids.capital_pool_id,
            "name": f"{record.request_payload.get('name') or record.normalized_name} paper pool",
            "owner_id": record.tenant_id,
            "owner_type": "org",
            "status": "active",
            "currency": currency,
            "budget": record.request_payload.get("budget"),
            "risk_policy_ref": requested_ref,
            "single_runtime_enforced": True,
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "tenant_id": record.tenant_id,
                "persona_id": record.persona_id,
                "requested_by": self._requested_by(record),
                "provisioning_request_hash": record.request_hash,
                "risk_policy": requested_policy,
                **(
                    {"requested_risk_profile": effective_persisted_profile}
                    if effective_persisted_profile is not None
                    else {}
                ),
                **(
                    {"require_approved_profile_mapping": True}
                    if require_approved_mapping
                    else {}
                ),
                **(
                    {"profile_mapping_approval_id": effective_approval_id}
                    if effective_approval_id is not None
                    else {}
                ),
            },
        }
        payload = {
            **base_payload,
            "idempotency_key": f"{record.idempotency_key}:capital-pool",
            "request_hash": _stable_hash(base_payload),
        }

        def validate_envelope_identity(receipt: Mapping[str, Any]) -> None:
            if (
                receipt.get("pool_id") != ids.capital_pool_id
                or receipt.get("owner_id") != record.tenant_id
                or receipt.get("owner_type") != "org"
                or receipt.get("single_runtime_enforced") is not True
            ):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool readback does not match the internal paper pool contract"
                )
            if receipt.get("currency") != currency:
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool readback currency {receipt.get('currency')!r} does not match {currency!r}"
                )

        def validate_envelope(receipt: Mapping[str, Any]) -> None:
            validate_envelope_identity(receipt)
            if receipt.get("status") != "active":
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool readback does not match the internal paper pool contract"
                )

        def validate(receipt: Mapping[str, Any]) -> None:
            validate_envelope(receipt)
            receipt_meta = receipt.get("metadata") if isinstance(receipt.get("metadata"), Mapping) else {}
            if "risk_policy" in receipt_meta or receipt.get("risk_policy_ref") or not receipt.get("risk_policy_ref"):
                enforce_mapping_on_readback = bool(
                    require_approved_mapping
                    or receipt_meta.get("require_approved_profile_mapping") is True
                    or bool(str(receipt_meta.get("requested_risk_profile") or "").strip())
                )
                resolution = resolve_pool_risk_policy_binding(
                    capital_pool_id=ids.capital_pool_id,
                    pool=self._pool_with_authoritative_approval_digest(
                        receipt,
                        tenant_id=record.tenant_id,
                    ),
                    expected_tenant_id=record.tenant_id,
                    expected_stage="paper",
                    requested_profile=effective_persisted_profile,
                    require_approved_profile_mapping=True if enforce_mapping_on_readback else False,
                    approval_decision_id=effective_approval_id,
                    approval_reader=self._governance_approval_reader(),
                    require_active=True,
                    require_limits=("gross_limit",),
                    raise_on_error=False,
                )
                if not resolution.resolved:
                    raise PersonaProvisioningCoordinationError(
                        f"CapitalPool readback risk policy rejected: {resolution.reason} ({resolution.reason_code})",
                        reason_code=resolution.reason_code,
                        resolution_status=resolution.status,
                    )
                record.references["capital_pool_risk_policy_resolution"] = resolution.to_dict()
                record.references.pop("risk_policy_owner_decision_request", None)

        if existing is not None:
            validate_envelope_identity(existing)
            existing_status = str(existing.get("status") or "")
            if existing_status not in {"active", "suspended", "provisional", "pending"}:
                validate_envelope(existing)
            existing_meta = dict(existing.get("metadata")) if isinstance(existing.get("metadata"), Mapping) else {}
            enforce_existing_mapping = bool(
                require_approved_mapping
                or existing_meta.get("require_approved_profile_mapping") is True
                or bool(str(existing_meta.get("requested_risk_profile") or "").strip())
            )
            existing_res = resolve_pool_risk_policy_binding(
                capital_pool_id=ids.capital_pool_id,
                pool=self._pool_with_authoritative_approval_digest(
                    existing,
                    tenant_id=record.tenant_id,
                ),
                expected_tenant_id=record.tenant_id,
                expected_stage="paper",
                requested_profile=effective_persisted_profile,
                require_approved_profile_mapping=True if enforce_existing_mapping else False,
                approval_decision_id=effective_approval_id,
                approval_reader=self._governance_approval_reader(),
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
            if existing_res.status == "wrong_tenant":
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool readback risk policy rejected: {existing_res.reason} ({existing_res.reason_code})",
                    reason_code=existing_res.reason_code,
                    resolution_status=existing_res.status,
                )
            has_existing_policy_or_markers = bool(
                ("risk_policy" in existing_meta and existing_meta.get("risk_policy") is not None)
                or existing.get("risk_policy_ref")
                or any(
                    existing_meta.get(k) not in (None, False, "")
                    for k in (
                        "risk_policy_stale",
                        "risk_policy_superseded_by",
                        "risk_policy_expires_at",
                        "risk_policy_effective_from",
                        "expected_risk_policy_version",
                        "expected_risk_policy_digest",
                    )
                )
            )
            needs_policy_patch = existing_status != "active" or bool(
                existing_meta.get("provisional_unresolved_risk_policy")
            )
            if existing_res.status == "missing" and not has_existing_policy_or_markers:
                needs_policy_patch = True
            elif has_explicit_policy_request or require_approved_mapping:
                target_digest = risk_policy_digest(requested_policy)
                if (
                    not existing_res.resolved
                    or existing_res.risk_policy_digest != target_digest
                    or (requested_ref is not None and existing.get("risk_policy_ref") != requested_ref)
                    or (
                        effective_persisted_profile is not None
                        and existing_meta.get("requested_risk_profile") != effective_persisted_profile
                    )
                    or (
                        require_approved_mapping
                        and existing_meta.get("require_approved_profile_mapping") is not True
                    )
                    or (
                        effective_approval_id is not None
                        and existing_meta.get("profile_mapping_approval_id") != effective_approval_id
                    )
                ):
                    needs_policy_patch = True
            if needs_policy_patch:
                patched_meta = {
                    **existing_meta,
                    "risk_policy": deepcopy(requested_policy),
                    **(
                        {"provisional_unresolved_risk_policy": False}
                        if "provisional_unresolved_risk_policy" in existing_meta
                        else {}
                    ),
                    **(
                        {"requested_risk_profile": effective_persisted_profile}
                        if effective_persisted_profile is not None
                        else {}
                    ),
                    **(
                        {"require_approved_profile_mapping": True}
                        if require_approved_mapping
                        else {}
                    ),
                    **(
                        {"profile_mapping_approval_id": effective_approval_id}
                        if effective_approval_id is not None
                        else {}
                    ),
                }
                next_ref = requested_ref if requested_ref is not None else existing.get("risk_policy_ref")
                candidate_patched_pool = self._candidate_pool_with_approval_digest(
                    base_pool=existing,
                    pool_id=ids.capital_pool_id,
                    tenant_id=record.tenant_id,
                    risk_policy_ref=next_ref,
                    metadata=patched_meta,
                )
                patch_validation = resolve_pool_risk_policy_binding(
                    capital_pool_id=ids.capital_pool_id,
                    pool=candidate_patched_pool,
                    expected_tenant_id=record.tenant_id,
                    expected_stage="paper",
                    requested_profile=effective_persisted_profile,
                    require_approved_profile_mapping=True if (require_approved_mapping or enforce_existing_mapping) else False,
                    approval_decision_id=effective_approval_id,
                    approval_reader=self._governance_approval_reader(),
                    require_active=True,
                    require_limits=("gross_limit",),
                    raise_on_error=False,
                )
                if not patch_validation.resolved:
                    raise PersonaProvisioningCoordinationError(
                        f"CapitalPool risk policy rejected: {patch_validation.reason} ({patch_validation.reason_code})",
                        reason_code=patch_validation.reason_code,
                        resolution_status=patch_validation.status,
                    )
                try:
                    self.transport.patch(
                        "capital",
                        f"{pool_path}/status",
                        {
                            "actor_id": self.actor_id,
                            "actor_role": "capital.admin",
                            "status": "active",
                            "risk_policy_ref": next_ref,
                            "metadata": patched_meta,
                        },
                    )
                except Exception:  # noqa: BLE001 - response loss reconciled by authoritative GET
                    pass

        receipt = self._create_then_get(
            owner="capital",
            get_path=pool_path,
            post_path="/api/capital-pools",
            payload=payload,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step="capital_pool_readback",
            key="capital_pool",
            receipt=receipt,
        )

    @staticmethod
    def _paper_universe(
        record: ProvisioningRecord,
        *,
        market: str | None = None,
    ) -> tuple[list[str], str, str]:
        """Symbols, bar frequency and data source shared by the spec and bundle.

        Missing symbols default to SPY only for the legacy absent/US market.
        """

        symbols = record.request_payload.get("symbols")
        if not isinstance(symbols, list) or not all(
            isinstance(item, str) and item.strip() for item in symbols
        ):
            category = classify_market_token(
                market if market is not None else record.request_payload.get("market")
            )
            if category not in (None, "US"):
                raise PersonaProvisioningCoordinationError(
                    f"paper universe symbols are required for market {category}; "
                    "no default universe is assumed"
                )
            symbols = ["SPY"]
        bar_frequency = str(record.request_payload.get("bar_frequency") or "1d")
        # SD_INTRADAY_MINUTE_BARS §6.1: only governed frequencies may be provisioned.
        try:
            dataset_for_bar_frequency(bar_frequency)
        except StrategyArtifactValidationError as exc:
            raise PersonaProvisioningCoordinationError(str(exc)) from exc
        data_source = str(
            record.request_payload.get("data_source")
            or "source-ingest:paper/persona-bootstrap"
        )
        return list(symbols), bar_frequency, data_source

    @staticmethod
    def _explicit_market(
        record: ProvisioningRecord,
        symbols: Any = None,
    ) -> str | None:
        """Extract and validate explicit owner market context from record request payload.

        Returns canonical market ('US', 'TW', 'CRYPTO', 'FX') when specified,
        or None when omitted. Rejects unsupported, non-canonical, or contradictory
        market values.
        """
        raw = record.request_payload.get("market")
        if raw is None:
            return None
        try:
            return canonical_market_context(raw, symbols=symbols)
        except StrategyArtifactValidationError as exc:
            raise PersonaProvisioningCoordinationError(str(exc)) from exc

    @classmethod
    def _capital_pool_currency(cls, record: ProvisioningRecord) -> str:
        """Pool currency from the explicit owner market; never a guess."""

        symbols, _, _ = cls._paper_universe(record)
        market = cls._explicit_market(record, symbols)
        requested = record.request_payload.get("currency")
        if market is None:
            # Legacy market-less request (unchanged): its artifact has no
            # market, so the paper producer fails closed before any fill.
            return str(requested or "USD")
        if requested is not None and (
            not isinstance(requested, str)
            or not requested
            or requested != requested.strip().upper()
        ):
            raise PersonaProvisioningCoordinationError(
                f"currency {requested!r} must be a canonical uppercase code"
            )
        settlement = _MARKET_SETTLEMENT_CURRENCY.get(market)
        if settlement is None:
            if requested is None:
                raise PersonaProvisioningCoordinationError(
                    f"Capital pool currency cannot be determined for market {market!r}; "
                    "the request must state currency"
                )
            return requested
        if requested is not None and requested != settlement:
            raise PersonaProvisioningCoordinationError(
                f"currency {requested!r} contradicts market {market!r} "
                f"settlement currency {settlement!r}"
            )
        return settlement

    def _strategy_spec_payload(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> dict[str, Any]:
        registry_id, _, version, label = self._artifact_values(ids, baseline=baseline)
        name = str(record.request_payload.get("name") or record.normalized_name)
        mandate = str(record.request_payload.get("mandate") or "").strip()
        symbols, bar_frequency, data_source = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)
        # Registry validates inline StrategySpec content against
        # services/control-plane/specs/strategy_spec.schema.json (closed
        # object) and hashes exactly that content, so the spec is composed
        # from persisted request data only: a retry composes the same bytes.
        strategy_spec = {
            "spec_version": "1.0",
            "strategy_id": ids.strategy_id,
            "title": f"{name} zero-capital baseline" if baseline else name,
            "hypothesis": (
                "Holding with zero capital and no orders yields no positions, so this "
                "Persona can fail closed to this baseline at any time."
                if baseline
                else mandate
                or "Close-to-close momentum over the configured paper universe can be "
                "evaluated without live capital."
            ),
            "objective": (
                "Provide the approved zero-capital rollback target for the paper "
                "admission of this Persona."
                if baseline
                else "Admit this Persona to paper-only evaluation with zero live capital."
            ),
            "lifecycle_state": "candidate",
            "market_scope": {
                "symbols": symbols,
                "asset_classes": ["equity"] if baseline else ["equity", "future"],
                "frequency": bar_frequency,
            },
            "data_dependencies": [{"ref": data_source, "kind": "dataset"}],
            "execution_profile": {
                "signal_schema_version": "1.0",
                "quantity_type": "SHARES",
                "rebalance_cadence": f"{bar_frequency} bar close, paper only",
                "execution_mode_hint": "paper",
            },
            "evaluation_plan": {
                "metrics": (
                    ["no_orders", "no_positions", "no_live_capital_side_effects"]
                    if baseline
                    else [
                        "paper_pnl",
                        "max_drawdown",
                        "turnover_rate",
                        "no_live_capital_side_effects",
                    ]
                ),
                "paper_gate": (
                    "ApprovalDecision may approve this target for paper-stage "
                    "deployment only, with capital_scale_pct=0."
                ),
                "live_gate": (
                    "Canary and live activation remain fail-closed; this revision "
                    "never authorizes live capital."
                ),
            },
            "governance": {
                "approval_required": True,
                "policy_id": "persona-paper-admission",
                "risk_profile": "low: paper-only Persona admission, zero live capital",
            },
            "provenance": {
                "source_kind": "workflow",
                "created_at": record.created_at,
                "source_refs": [
                    f"persona-provisioning:{ids.token}",
                    f"persona:{record.persona_id}",
                    f"capital-pool:{ids.capital_pool_id}",
                ],
                "created_by": self.actor_id,
            },
            "metadata": {
                "name": name,
                "version": version,
                "persona_id": record.persona_id,
                "tenant_id": record.tenant_id,
                "capital_pool_id": ids.capital_pool_id,
                "mandate": (
                    "Fail-closed paper baseline: allocate zero capital and emit no orders"
                    if baseline
                    else mandate or None
                ),
                "traits": (
                    {"hard_rules": ["zero_capital", "no_orders", "no_positions"]}
                    if baseline
                    else deepcopy(record.request_payload.get("traits") or {})
                ),
                "requested_by": self._requested_by(record),
                "execution_context": "paper",
                # Both revisions admit the Persona to paper only: the forward
                # revision may emit paper orders, but it carries zero live
                # capital exactly like the fail-closed baseline it is parented to.
                "capital_scale_pct": 0.0,
                "fail_closed_baseline": baseline,
                **({"market": explicit_market} if explicit_market is not None else {}),
            },
        }
        source_run_id = f"persona-provisioning-{label}-{ids.token}"
        lineage: dict[str, Any] = {"source_run_ids": [source_run_id]}
        if not baseline:
            # Registry requires a noninitial StrategySpec revision to name its
            # exact parent entry; the baseline is the only prior revision.
            lineage["parent_registry_ids"] = [ids.baseline_registry_id]
        payload = {
            "registry_id": registry_id,
            "strategy_id": ids.strategy_id,
            "version": version,
            "artifact_state": "candidate",
            "source_seed_id": source_run_id,
            "lineage": lineage,
            "producer_run_id": source_run_id,
            "evaluation_summary": {
                "admission": (
                    "persona_paper_zero_capital_baseline"
                    if baseline
                    else "persona_paper_bootstrap"
                ),
                "risk_level": "low",
                "execution_context": "paper",
                "capital_scale_pct": 0.0,
            },
            "metadata": {
                "tenant_id": record.tenant_id,
                "persona_id": record.persona_id,
                "capital_pool_id": ids.capital_pool_id,
                "execution_context": "paper",
                "capital_scale_pct": 0.0,
                "requested_by": self._requested_by(record),
                "fail_closed_baseline": baseline,
                "rollback_target_registry_id": (
                    None if baseline else ids.baseline_registry_id
                ),
                "provisioning_request_hash": record.request_hash,
                **({"market": explicit_market} if explicit_market is not None else {}),
            },
            "strategy_spec": strategy_spec,
        }
        if not baseline:
            payload["rollback_target"] = ids.baseline_version
        return payload

    @staticmethod
    def _artifact_values(
        ids: ProvisioningIds,
        *,
        baseline: bool,
    ) -> tuple[str, str, str, str]:
        if baseline:
            return (
                ids.baseline_registry_id,
                ids.baseline_approval_decision_id,
                ids.baseline_version,
                "baseline",
            )
        return ids.registry_id, ids.approval_decision_id, ids.version, "forward"

    @staticmethod
    def _strategy_artifact_values(
        ids: ProvisioningIds,
        *,
        baseline: bool,
    ) -> tuple[str, str, str, str]:
        if baseline:
            return (
                ids.baseline_strategy_artifact_id,
                ids.baseline_strategy_artifact_approval_decision_id,
                ids.baseline_version,
                "baseline-artifact",
            )
        return (
            ids.strategy_artifact_id,
            ids.strategy_artifact_approval_decision_id,
            ids.version,
            "forward-artifact",
        )

    def _validate_registry(
        self,
        receipt: Mapping[str, Any],
        ids: ProvisioningIds,
        *,
        state: str,
        registry_id: str | None = None,
        version: str | None = None,
        approval_decision_id: str | None = None,
        artifact_type: str = "strategy_spec",
        expected_market: str | None = None,
    ) -> None:
        entry = _registry_entry(receipt)
        expected_registry_id = registry_id or ids.registry_id
        expected_version = version or ids.version
        if (
            entry.get("registry_id") != expected_registry_id
            or entry.get("artifact_type") != artifact_type
            or entry.get("strategy_id") != ids.strategy_id
            or entry.get("version") != expected_version
            or entry.get("artifact_state") != state
        ):
            raise PersonaProvisioningCoordinationError(
                f"Registry readback does not match the stable {artifact_type} in state {state}"
            )
        if approval_decision_id and entry.get("approval_decision_id") != approval_decision_id:
            raise PersonaProvisioningCoordinationError(
                "Approved RegistryEntry does not cite the canonical ApprovalDecision"
            )
        if artifact_type == "execution_bundle":
            metadata = entry.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            artifact = metadata.get("strategy_artifact")
            if not isinstance(artifact, Mapping):
                raise PersonaProvisioningCoordinationError(
                    "StrategyArtifact RegistryEntry is missing metadata.strategy_artifact"
                )
            if (
                artifact.get("artifact_id") != expected_registry_id
                or artifact.get("strategy_id") != ids.strategy_id
                or artifact.get("version") != expected_version
            ):
                raise PersonaProvisioningCoordinationError(
                    "StrategyArtifact payload identity does not match RegistryEntry"
                )
            if expected_market is not None:
                market_in_meta = metadata.get("market")
                parameters = artifact.get("parameters")
                parameters = parameters if isinstance(parameters, Mapping) else {}
                market_in_params = parameters.get("market")
                if market_in_meta != expected_market or market_in_params != expected_market:
                    raise PersonaProvisioningCoordinationError(
                        f"Registry readback does not match expected market {expected_market!r}"
                    )

    def _coordinate_strategy_spec(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> ProvisioningRecord:
        registry_id, approval_id, version, label = self._artifact_values(
            ids,
            baseline=baseline,
        )
        checkpoint_prefix = "baseline_" if baseline else ""

        def validate(receipt: Mapping[str, Any]) -> None:
            # A restart can revisit this create step after the same entry has
            # already advanced.  Accept only that monotonic successor, never a
            # different or retired entry.
            state = str(_registry_entry(receipt).get("artifact_state") or "")
            if state not in {"candidate", "approved"}:
                raise PersonaProvisioningCoordinationError(
                    "StrategySpec create readback is neither candidate nor approved"
                )
            self._validate_registry(
                receipt,
                ids,
                state=state,
                registry_id=registry_id,
                version=version,
                approval_decision_id=(
                    approval_id if state == "approved" else None
                ),
            )

        receipt = self._create_then_get(
            owner="registry",
            get_path=f"/api/registry/strategy-specs/{_path_id(registry_id)}",
            post_path="/api/registry/strategy-specs",
            payload=self._strategy_spec_payload(record, ids, baseline=baseline),
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=f"{checkpoint_prefix}strategy_spec_candidate_readback",
            key=f"{checkpoint_prefix}strategy_spec_candidate",
            receipt=receipt,
        )

    @staticmethod
    def _strategy_artifact_ref(record: ProvisioningRecord) -> str | None:
        ref = record.request_payload.get("strategy_artifact_ref")
        if ref is None:
            return None
        if not isinstance(ref, str) or not ref.strip():
            raise PersonaProvisioningCoordinationError(
                "strategy_artifact_ref must be a non-empty string when provided"
            )
        return ref.strip()

    def _admitted_artifact_body(
        self,
        record: ProvisioningRecord,
        ref: str,
        *,
        explicit_market: str | None,
    ) -> dict[str, Any]:
        """Return the admitted artifact body, failing closed unless approved."""

        receipt = self._owner_get(
            "registry", f"/api/registry/strategy-artifacts/{_path_id(ref)}"
        )
        if receipt is None:
            raise PersonaProvisioningCoordinationError(
                f"strategy_artifact_ref {ref!r} was not found in Registry"
            )
        entry = _registry_entry(receipt)
        if (
            entry.get("registry_id") != ref
            or entry.get("artifact_type") != "execution_bundle"
            or entry.get("artifact_state") != "approved"
        ):
            raise PersonaProvisioningCoordinationError(
                f"strategy_artifact_ref {ref!r} is not an approved execution_bundle"
            )
        metadata = entry.get("metadata")
        body = metadata.get("strategy_artifact") if isinstance(metadata, Mapping) else None
        if not isinstance(body, Mapping):
            raise PersonaProvisioningCoordinationError(
                f"strategy_artifact_ref {ref!r} is missing metadata.strategy_artifact"
            )
        body = deepcopy(dict(body))
        if explicit_market is not None:
            params = body.get("parameters")
            if not isinstance(params, Mapping) or params.get("market") != explicit_market:
                raise PersonaProvisioningCoordinationError(
                    f"strategy_artifact_ref {ref!r} does not match expected market "
                    f"{explicit_market!r}"
                )
        return body

    def _strategy_artifact_payload(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> dict[str, Any]:
        artifact_id, _, version, label = self._strategy_artifact_values(
            ids,
            baseline=baseline,
        )
        spec_registry_id, _, _, _ = self._artifact_values(ids, baseline=baseline)
        source_run_id = f"persona-provisioning-{label}-{ids.token}"
        positive_action = "HOLD" if baseline else "BUY"
        non_positive_action = "HOLD" if baseline else "SELL"
        symbols, bar_frequency, data_source = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)
        artifact = {
            "artifact_schema_version": "1.0",
            "artifact_id": artifact_id,
            "strategy_id": ids.strategy_id,
            "version": version,
            "algorithm_ref": {
                "engine": "lean",
                "repository": "ajoe734/pantheon",
                "commit": _source_commit(),
                "path": "services/registry/strategy_artifact.py",
                "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
                "signal_interface": (
                    "services.execution.lean_runtime.paper_signal_producer:Strategy"
                ),
                "signal_schema_version": "1.0",
                "logic_interpreter": (
                    "services.registry.strategy_artifact:evaluate_strategy_action"
                ),
            },
            "strategy_logic": {
                "kind": "close_to_close_momentum",
                "lookback_parameter": "lookback_bars",
                "threshold_parameter": "momentum_threshold",
                "positive_action": positive_action,
                "non_positive_action": non_positive_action,
            },
            "parameters": {
                "symbols": symbols,
                "bar_frequency": bar_frequency,
                "data_source": data_source,
                "lookback_bars": 2,
                "momentum_threshold": 0.0,
                "order_quantity": 0 if baseline else 1,
                "quantity_type": "SHARES",
                "zero_momentum_action": non_positive_action,
                **(
                    {
                        "strength_scale_k": 1.0,
                        "strength_floor_momentum": 0.01,
                    }
                    if not baseline
                    else {}
                ),
                **({"market": explicit_market} if explicit_market is not None else {}),
            },
            "mutation_surface": {
                "controls": [
                    {
                        "parameter_key": "lookback_bars",
                        "value_type": "integer",
                        "current_value": 2,
                        "allowed_range": {"min": 2, "max": 60},
                        "step": 1,
                    },
                    {
                        "parameter_key": "momentum_threshold",
                        "value_type": "number",
                        "current_value": 0.0,
                        "allowed_range": {"min": 0.0, "max": 0.05},
                        "step": 0.001,
                    },
                    *(
                        [
                            {
                                "parameter_key": "strength_scale_k",
                                "value_type": "number",
                                "current_value": 1.0,
                                "allowed_range": {"min": 0.25, "max": 4.0},
                                "step": 0.05,
                            },
                            {
                                "parameter_key": "strength_floor_momentum",
                                "value_type": "number",
                                "current_value": 0.01,
                                "allowed_range": {"min": 0.002, "max": 0.05},
                                "step": 0.001,
                            },
                            {
                                "parameter_key": "lever_min_strength",
                                "value_type": "number",
                                "current_value": 0.5,
                                "allowed_range": {"min": 0.0, "max": 1.0},
                                "step": 0.05,
                            },
                            {
                                "parameter_key": "notional_fraction_at_full_strength",
                                "value_type": "number",
                                "current_value": 1.0,
                                "allowed_range": {"min": 0.1, "max": 1.0},
                                "step": 0.05,
                            },
                        ]
                        if not baseline
                        else []
                    ),
                ],
                "immutable_parameters": [
                    "symbols",
                    "bar_frequency",
                    "data_source",
                    "order_quantity",
                    "quantity_type",
                    "zero_momentum_action",
                    *(["market"] if explicit_market is not None else []),
                ],
            },
            "lineage": {
                "source_run_ids": [source_run_id],
                "source_strategy_spec_id": spec_registry_id,
            },
            # No ``binding_intent``: the schema makes its ``observed_*`` fields
            # mandatory and Deployment's promote pipeline substitutes them for
            # a current RuntimeBinding.  At provisioning time nothing is bound
            # or observed, and this coordinator never guesses a RuntimeBinding,
            # runtime or plan, so the optional object is omitted rather than
            # filled with a deterministic ID posing as an observation.  The
            # Persona identity lives in the registry envelope metadata.
            "provenance_refs": [
                "task:LOOP-PROD-PER-001",
                f"persona-provisioning:{ids.token}",
                f"strategy-spec:{spec_registry_id}",
            ],
        }
        if not baseline:
            artifact["lineage"]["parent_registry_ids"] = [ids.baseline_strategy_artifact_id]
            artifact["expression_policy"] = {
                "version": "1.0",
                "kind": "notional_target_v1",
                "parameters": {
                    "lever_min_strength": 0.5,
                    "notional_fraction_at_full_strength": 1.0,
                    "allow_pyramiding": False,
                    "close_before_reverse": True,
                },
            }
        admitted_ref = None if baseline else self._strategy_artifact_ref(record)
        if admitted_ref is not None:
            # Replace only the template strategy content with the admitted
            # artifact's; identity, lineage and provenance stay saga-owned.
            admitted = self._admitted_artifact_body(
                record, admitted_ref, explicit_market=explicit_market
            )
            for key in (
                "algorithm_ref",
                "strategy_logic",
                "parameters",
                "mutation_surface",
                "expression_policy",
            ):
                if key in admitted:
                    artifact[key] = admitted[key]
                else:
                    artifact.pop(key, None)
            artifact["provenance_refs"].append(f"strategy-artifact:{admitted_ref}")
        return {
            "registry_id": artifact_id,
            "artifact_state": "candidate",
            "strategy_artifact": artifact,
            "producer_run_id": source_run_id,
            "evaluation_summary": {
                "admission": (
                    "persona_paper_zero_capital_baseline"
                    if baseline
                    else "persona_paper_bootstrap"
                ),
                "risk_level": "low",
                "execution_context": "paper",
                "capital_scale_pct": 0.0,
                "source_strategy_spec_id": spec_registry_id,
            },
            "rollback_target": ids.baseline_strategy_artifact_id if not baseline else None,
            "metadata": {
                "tenant_id": record.tenant_id,
                "persona_id": record.persona_id,
                "capital_pool_id": ids.capital_pool_id,
                "persona_capital_binding_id": ids.persona_capital_binding_id,
                "source_strategy_spec_registry_id": spec_registry_id,
                "execution_context": "paper",
                "capital_scale_pct": 0.0,
                "requested_by": self._requested_by(record),
                "fail_closed_baseline": baseline,
                "rollback_target_registry_id": (
                    None if baseline else ids.baseline_strategy_artifact_id
                ),
                "provisioning_request_hash": record.request_hash,
                **(
                    {"source_strategy_artifact_ref": admitted_ref}
                    if admitted_ref is not None
                    else {}
                ),
                **({"market": explicit_market} if explicit_market is not None else {}),
            },
        }

    def _coordinate_strategy_artifact(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> ProvisioningRecord:
        artifact_id, approval_id, version, _ = self._strategy_artifact_values(
            ids,
            baseline=baseline,
        )
        checkpoint_prefix = "baseline_" if baseline else ""
        symbols, _, _ = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)

        def validate(receipt: Mapping[str, Any]) -> None:
            state = str(_registry_entry(receipt).get("artifact_state") or "")
            if state not in {"candidate", "approved"}:
                raise PersonaProvisioningCoordinationError(
                    "StrategyArtifact create readback is neither candidate nor approved"
                )
            self._validate_registry(
                receipt,
                ids,
                state=state,
                registry_id=artifact_id,
                version=version,
                approval_decision_id=approval_id if state == "approved" else None,
                artifact_type="execution_bundle",
                expected_market=explicit_market,
            )

        receipt = self._create_then_get(
            owner="registry",
            get_path=f"/api/registry/strategy-artifacts/{_path_id(artifact_id)}",
            post_path="/api/registry/strategy-artifacts",
            payload=self._strategy_artifact_payload(record, ids, baseline=baseline),
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=f"{checkpoint_prefix}strategy_artifact_candidate_readback",
            key=f"{checkpoint_prefix}strategy_artifact_candidate",
            receipt=receipt,
        )

    def _candidate_binding(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool,
        strategy_artifact: bool,
    ) -> tuple[str, str]:
        """Exact (checksum, expires_at) the approval of one RegistryEntry binds to.

        Both values come from the checkpointed authoritative Registry readback
        of the candidate this approval targets, never from the request body
        the coordinator composed: Registry's approval verifier compares the
        decision's ``candidate_digest`` with the durable entry checksum, and
        the same persisted readback is reused on every retry so the approval
        bodies stay deterministic.
        """

        checkpoint_prefix = "baseline_" if baseline else ""
        key = (
            f"{checkpoint_prefix}strategy_artifact_candidate"
            if strategy_artifact
            else f"{checkpoint_prefix}strategy_spec_candidate"
        )
        values = (
            self._strategy_artifact_values(ids, baseline=baseline)
            if strategy_artifact
            else self._artifact_values(ids, baseline=baseline)
        )
        registry_id, _, version, _ = values
        receipt = _mapping(
            record.references.get(key),
            label=f"checkpointed candidate RegistryEntry readback {key}",
        )
        entry = _registry_entry(receipt)
        state = str(entry.get("artifact_state") or "")
        if state not in {"candidate", "approved"}:
            raise PersonaProvisioningCoordinationError(
                f"checkpointed RegistryEntry {registry_id} is neither candidate nor approved"
            )
        self._validate_registry(
            receipt,
            ids,
            state=state,
            registry_id=registry_id,
            version=version,
            artifact_type="execution_bundle" if strategy_artifact else "strategy_spec",
        )
        digest = _owner_digest(
            entry.get("checksum"),
            label=f"RegistryEntry {registry_id} readback",
        )
        expires_at = _approval_expiry(
            entry.get("created_at"),
            label=f"RegistryEntry {registry_id} readback created_at",
        )
        return digest, expires_at

    def _validate_approval_identity(
        self,
        receipt: Mapping[str, Any],
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        registry_id: str,
        decision_id: str,
        version: str,
        candidate_digest: str,
        expires_at: str,
    ) -> None:
        if (
            receipt.get("decision_id") != decision_id
            or receipt.get("target_type") != "registry_entry"
            or receipt.get("target_id") != registry_id
            or receipt.get("target_version") != version
            or receipt.get("risk_level") != "low"
            or receipt.get("tenant_id") != record.tenant_id
            or receipt.get("persona_id") != record.persona_id
            or receipt.get("capital_pool_id") != ids.capital_pool_id
        ):
            raise PersonaProvisioningCoordinationError(
                "ApprovalDecision readback does not match the stable low-risk proposal"
            )
        if receipt.get("owner_user_id") != self.governance_actor_id:
            raise PersonaProvisioningCoordinationError(
                "ApprovalDecision readback is owned by a different Governance principal"
            )
        # A same-ID decision that binds another digest or another expiry was
        # not proposed by this coordination and must never be advanced by it.
        if receipt.get("candidate_digest") != candidate_digest:
            raise PersonaProvisioningCoordinationError(
                "ApprovalDecision readback does not bind the exact RegistryEntry checksum"
            )
        if receipt.get("expires_at") != expires_at:
            raise PersonaProvisioningCoordinationError(
                "ApprovalDecision readback does not carry the exact finite expiry"
            )

    def _coordinate_approval_proposal(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
        strategy_artifact: bool = False,
    ) -> ProvisioningRecord:
        values = (
            self._strategy_artifact_values(ids, baseline=baseline)
            if strategy_artifact
            else self._artifact_values(ids, baseline=baseline)
        )
        registry_id, decision_id, version, label = values
        checkpoint_prefix = "baseline_" if baseline else ""
        checkpoint_key = (
            f"{checkpoint_prefix}strategy_artifact_approval_proposed"
            if strategy_artifact
            else f"{checkpoint_prefix}approval_proposed"
        )
        checkpoint_step = f"{checkpoint_key}_readback"
        candidate_digest, expires_at = self._candidate_binding(
            record,
            ids,
            baseline=baseline,
            strategy_artifact=strategy_artifact,
        )
        payload = {
            "decision_id": decision_id,
            "expected_version": 0,
            "target_type": "registry_entry",
            "target_id": registry_id,
            "target_version": version,
            "risk_level": "low",
            "capital_pool_id": ids.capital_pool_id,
            "persona_id": record.persona_id,
            "tenant_id": record.tenant_id,
            # Strict Governance binds the proposal owner to the verified
            # approval subject.  The human requester is not that principal
            # and stays audit metadata on the owner artifacts instead.
            "owner_user_id": self.governance_actor_id,
            "proposal_id": f"persona-provisioning-{label}-{ids.token}",
            "proposal_revision": 1,
            "proposal_content_digest": record.request_hash,
            "validation_result_digest": _stable_hash(
                {
                    "registry_id": registry_id,
                    "risk_level": "low",
                    "candidate_digest": candidate_digest,
                }
            ),
            "candidate_digest": candidate_digest,
            "expires_at": expires_at,
        }

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_approval_identity(
                receipt,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
                candidate_digest=candidate_digest,
                expires_at=expires_at,
            )
            if receipt.get("decision_state") not in {"proposed", "under_review", "decided"}:
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision proposal readback has an invalid state"
                )

        receipt = self._create_then_get(
            owner="governance",
            get_path=f"/api/governance/approvals/{_path_id(decision_id)}",
            post_path="/api/governance/approvals",
            payload=payload,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=checkpoint_step,
            key=checkpoint_key,
            receipt=receipt,
        )

    def _coordinate_approval_review(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
        strategy_artifact: bool = False,
    ) -> ProvisioningRecord:
        values = (
            self._strategy_artifact_values(ids, baseline=baseline)
            if strategy_artifact
            else self._artifact_values(ids, baseline=baseline)
        )
        registry_id, decision_id, version, _ = values
        checkpoint_prefix = "baseline_" if baseline else ""
        checkpoint_key = (
            f"{checkpoint_prefix}strategy_artifact_approval_reviewed"
            if strategy_artifact
            else f"{checkpoint_prefix}approval_reviewed"
        )
        checkpoint_step = f"{checkpoint_key}_readback"
        get_path = f"/api/governance/approvals/{_path_id(decision_id)}"
        candidate_digest, expires_at = self._candidate_binding(
            record,
            ids,
            baseline=baseline,
            strategy_artifact=strategy_artifact,
        )

        def ready(receipt: Mapping[str, Any]) -> bool:
            return receipt.get("decision_state") in {"under_review", "decided"}

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_approval_identity(
                receipt,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
                candidate_digest=candidate_digest,
                expires_at=expires_at,
            )
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision was not accepted for review"
                )

        def build_payload(current: Mapping[str, Any] | None) -> Mapping[str, Any]:
            # CAS-bind the review transition to the exact proposed-state
            # version this coordinator just observed (the same pre-read
            # ``_transition_then_get`` uses to decide whether to POST), per
            # governance ApprovalCommand's mandatory expected_version
            # (architecture-resumption-sa-sd.md §3.3).  Only a proposal this
            # coordination owns (same digest, expiry and subject) is reviewed.
            if current is None:
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision proposal has no persisted readback to review"
                )
            self._validate_approval_identity(
                current,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
                candidate_digest=candidate_digest,
                expires_at=expires_at,
            )
            return {
                "actor_role": "automated_gate",
                "actor_id": self.governance_actor_id,
                "expected_version": int(current.get("version") or 0),
            }

        receipt = self._transition_then_get(
            owner="governance",
            get_path=get_path,
            post_path=f"{get_path}/review",
            payload=build_payload,
            ready=ready,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=checkpoint_step,
            key=checkpoint_key,
            receipt=receipt,
        )

    def _coordinate_approval_decision(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
        strategy_artifact: bool = False,
    ) -> ProvisioningRecord:
        values = (
            self._strategy_artifact_values(ids, baseline=baseline)
            if strategy_artifact
            else self._artifact_values(ids, baseline=baseline)
        )
        registry_id, decision_id, version, _ = values
        checkpoint_prefix = "baseline_" if baseline else ""
        checkpoint_key = (
            f"{checkpoint_prefix}strategy_artifact_approval_decided"
            if strategy_artifact
            else f"{checkpoint_prefix}approval_decided"
        )
        checkpoint_step = f"{checkpoint_key}_readback"
        get_path = f"/api/governance/approvals/{_path_id(decision_id)}"
        candidate_digest, expires_at = self._candidate_binding(
            record,
            ids,
            baseline=baseline,
            strategy_artifact=strategy_artifact,
        )

        def ready(receipt: Mapping[str, Any]) -> bool:
            return (
                receipt.get("decision_state") == "decided"
                and receipt.get("decision") == "approved"
            )

        def require_current(receipt: Mapping[str, Any]) -> None:
            """Fail closed on an expired proposal instead of renewing it.

            The persisted proposal already carries the finite expiry this
            coordination bound.  Deciding keeps that exact value; it must
            still lie in the future and within ``APPROVAL_TTL`` of the
            approval's own persisted ``created_at``.  An expired proposal is
            never re-proposed or extended by this coordinator.
            """

            now = datetime.now(timezone.utc)
            created_at = _parse_owner_time(
                receipt.get("created_at"),
                label=f"ApprovalDecision {decision_id} created_at",
            )
            expiry = _parse_owner_time(
                receipt.get("expires_at"),
                label=f"ApprovalDecision {decision_id} expires_at",
            )
            if expiry > created_at + APPROVAL_TTL:
                raise PersonaProvisioningCoordinationError(
                    f"ApprovalDecision {decision_id} expiry exceeds the {APPROVAL_TTL} bound"
                )
            if expiry <= now:
                raise PersonaProvisioningCoordinationError(
                    f"ApprovalDecision {decision_id} proposal expired at "
                    f"{receipt.get('expires_at')}; it is not renewed"
                )

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_approval_identity(
                receipt,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
                candidate_digest=candidate_digest,
                expires_at=expires_at,
            )
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision readback is not decided/approved"
                )
            require_current(receipt)

        def build_payload(current: Mapping[str, Any] | None) -> Mapping[str, Any]:
            # CAS-bind the decide transition to the exact under-review-state
            # version this coordinator just observed (same rationale as the
            # review transition above).  The decision restates the exact
            # digest and expiry the persisted proposal binds, so Registry's
            # approval verifier sees one immutable target.
            if current is None:
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision proposal has no persisted readback to decide"
                )
            self._validate_approval_identity(
                current,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
                candidate_digest=candidate_digest,
                expires_at=expires_at,
            )
            require_current(current)
            return {
                "actor_role": "automated_gate",
                "actor_id": self.governance_actor_id,
                "outcome": "approved",
                "rationale": "Low-risk internal paper Persona admission",
                "evidence_refs": [
                    {"ref_type": "registry_entry", "ref_id": registry_id}
                ],
                "candidate_digest": candidate_digest,
                "expires_at": expires_at,
                "expected_version": int(current.get("version") or 0),
            }

        receipt = self._transition_then_get(
            owner="governance",
            get_path=get_path,
            post_path=f"{get_path}/decide",
            payload=build_payload,
            ready=ready,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=checkpoint_step,
            key=checkpoint_key,
            receipt=receipt,
        )

    def _coordinate_registry_approval(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
        strategy_artifact: bool = False,
    ) -> ProvisioningRecord:
        values = (
            self._strategy_artifact_values(ids, baseline=baseline)
            if strategy_artifact
            else self._artifact_values(ids, baseline=baseline)
        )
        registry_id, decision_id, version, _ = values
        checkpoint_prefix = "baseline_" if baseline else ""
        registry_kind = "strategy-artifacts" if strategy_artifact else "strategy-specs"
        artifact_type = "execution_bundle" if strategy_artifact else "strategy_spec"
        checkpoint_key = (
            f"{checkpoint_prefix}strategy_artifact_approved"
            if strategy_artifact
            else f"{checkpoint_prefix}strategy_spec_approved"
        )
        symbols, _, _ = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols) if strategy_artifact else None
        get_path = f"/api/registry/{registry_kind}/{_path_id(registry_id)}"

        def ready(receipt: Mapping[str, Any]) -> bool:
            entry = _registry_entry(receipt)
            return (
                entry.get("artifact_state") == "approved"
                and entry.get("approval_decision_id") == decision_id
            )

        def build_payload(current: Mapping[str, Any] | None) -> Mapping[str, Any]:
            # Registry's strict advance contract: the approver is the
            # Governance decision (never a caller-supplied name), the actor is
            # the verified transport subject (never a body field), and the CAS
            # base is the exact candidate row this coordinator just read from
            # the authoritative GET.  ``command_key`` is derived from that
            # exact operation and base, so a retry of the same transition
            # replays the durable receipt while a changed base is a new
            # command that Registry compares against the true row.
            if current is None:
                raise PersonaProvisioningCoordinationError(
                    f"Registry candidate {registry_id} has no authoritative readback to approve"
                )
            self._validate_registry(
                current,
                ids,
                state="candidate",
                registry_id=registry_id,
                version=version,
                artifact_type=artifact_type,
                expected_market=explicit_market,
            )
            entry = _registry_entry(current)
            updated_at = entry.get("updated_at")
            if not isinstance(updated_at, str) or not updated_at.strip():
                raise PersonaProvisioningCoordinationError(
                    f"Registry candidate {registry_id} readback has no updated_at for CAS"
                )
            base = {
                "approval_decision_id": decision_id,
                "expected_artifact_state": "candidate",
                "expected_version": str(entry["version"]),
                "expected_updated_at": updated_at,
            }
            command_key = (
                f"persona-provisioning:{ids.token}:advance:{registry_id}:approved:"
                f"{_stable_hash(base)[:16]}"
            )
            return {"target_state": "approved", "command_key": command_key, **base}

        receipt = self._transition_then_get(
            owner="registry",
            get_path=get_path,
            post_path=f"{get_path}/advance",
            payload=build_payload,
            ready=ready,
            validate=lambda value: self._validate_registry(
                value,
                ids,
                state="approved",
                registry_id=registry_id,
                version=version,
                approval_decision_id=decision_id,
                artifact_type=artifact_type,
                expected_market=explicit_market,
            ),
        )
        return self._checkpoint_receipt(
            record,
            step=f"{checkpoint_key}_readback",
            key=checkpoint_key,
            receipt=receipt,
        )

    def _validate_binding_identity(
        self,
        receipt: Mapping[str, Any],
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> None:
        self._check_binding_identity(
            receipt,
            persona_id=record.persona_id,
            pool_id=ids.capital_pool_id,
            binding_id=ids.persona_capital_binding_id,
        )

    @staticmethod
    def _check_binding_identity(
        receipt: Mapping[str, Any],
        *,
        persona_id: str,
        pool_id: str,
        binding_id: str,
    ) -> None:
        if (
            receipt.get("binding_id") != binding_id
            or receipt.get("persona_id") != persona_id
            or receipt.get("capital_pool_id") != pool_id
            or receipt.get("role") != "paper_owner"
            or receipt.get("allowed_deployment_scope") != "paper"
        ):
            raise PersonaProvisioningCoordinationError(
                "PersonaCapitalBinding readback does not match paper admission"
            )

    def _coordinate_binding_create(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        symbols, _, _ = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)
        base_payload: dict[str, Any] = {
            "actor_id": self.actor_id,
            "actor_role": "admin",
            "binding_id": ids.persona_capital_binding_id,
            "persona_id": record.persona_id,
            "capital_pool_id": ids.capital_pool_id,
            "role": "paper_owner",
            "allowed_deployment_scope": "paper",
            "mandate": record.request_payload.get("mandate"),
            "budget": record.request_payload.get("budget"),
            "created_by": self.actor_id,
            "metadata": {
                "tenant_id": record.tenant_id,
                "registry_id": ids.strategy_artifact_id,
                "strategy_spec_registry_id": ids.registry_id,
                "requested_by": self._requested_by(record),
                "provisioning_request_hash": record.request_hash,
                **({"market": explicit_market} if explicit_market is not None else {}),
            },
        }
        payload = {
            **base_payload,
            "idempotency_key": f"{record.idempotency_key}:persona-capital-binding",
            "request_hash": _stable_hash(base_payload),
        }

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_binding_identity(receipt, record, ids)
            if receipt.get("status") not in {"pending", "active"}:
                raise PersonaProvisioningCoordinationError(
                    "PersonaCapitalBinding create readback is not pending/active"
                )

        receipt = self._create_then_get(
            owner="capital",
            get_path=f"/api/bindings/{_path_id(ids.persona_capital_binding_id)}",
            post_path="/api/bindings",
            payload=payload,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step="persona_capital_binding_created_readback",
            key="persona_capital_binding_created",
            receipt=receipt,
        )

    def _coordinate_binding_activate(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        get_path = f"/api/bindings/{_path_id(ids.persona_capital_binding_id)}"

        def ready(receipt: Mapping[str, Any]) -> bool:
            return receipt.get("status") == "active"

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_binding_identity(receipt, record, ids)
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "PersonaCapitalBinding activation readback is not active"
                )

        receipt = self._transition_then_get(
            owner="capital",
            get_path=get_path,
            post_path=f"{get_path}/activate",
            payload={"actor_id": self.actor_id, "actor_role": "persona.admin"},
            ready=ready,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step="persona_capital_binding_active_readback",
            key="persona_capital_binding_active",
            receipt=receipt,
        )

    def _coordinate_deployment_plan(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        symbols, _, _ = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)
        # Deployment reads the authoritative RegistryEntry and ApprovalDecision
        # itself under its own scoped verified reader principal, and its request
        # model forbids extra fields.  These checkpointed receipts therefore stay
        # local coordination preconditions from the earlier validated steps.
        # Deployment must revalidate current owner authority using only the exact
        # identifiers; these checkpoints are never sent as client snapshots.
        registry_receipt = _mapping(
            record.references.get("strategy_artifact_approved"),
            label="checkpointed approved StrategyArtifact RegistryEntry",
        )
        _mapping(
            record.references.get("strategy_artifact_approval_decided"),
            label="checkpointed ApprovalDecision",
        )
        baseline_receipt = _mapping(
            record.references.get("baseline_strategy_artifact_approved"),
            label="checkpointed approved zero-capital baseline StrategyArtifact RegistryEntry",
        )
        self._validate_registry(
            registry_receipt,
            ids,
            state="approved",
            registry_id=ids.strategy_artifact_id,
            version=ids.version,
            approval_decision_id=ids.strategy_artifact_approval_decision_id,
            artifact_type="execution_bundle",
            expected_market=explicit_market,
        )
        baseline_entry = _registry_entry(baseline_receipt)
        self._validate_registry(
            baseline_receipt,
            ids,
            state="approved",
            registry_id=ids.baseline_strategy_artifact_id,
            version=ids.baseline_version,
            approval_decision_id=ids.baseline_strategy_artifact_approval_decision_id,
            artifact_type="execution_bundle",
            expected_market=explicit_market,
        )
        payload = {
            "plan_id": ids.deployment_plan_id,
            "approval_decision_id": ids.strategy_artifact_approval_decision_id,
            "capital_pool_id": ids.capital_pool_id,
            "target_stage": "paper",
            "current_stage": "none",
            "registry_id": ids.strategy_artifact_id,
            "created_by": self.actor_id,
            "sponsor_persona_id": record.persona_id,
            "scale": {"capital_scale_pct": 0.0, "gross_scale_pct": 100.0},
            "rollback": {
                "target_artifact_id": baseline_entry["registry_id"],
                "target_version": baseline_entry["version"],
                "action_type": "pause_then_replace",
                "reason": "Fail closed to the approved zero-capital paper baseline",
            },
            "pre_checks": [
                "registry_entry_approved",
                "approval_decision_approved",
                "persona_capital_binding_active",
            ],
            "post_checks": [
                "deployment_saga_admitted",
                "first_evaluation_schedule_registered",
            ],
            "status": "approved",
            "metadata": {
                "tenant_id": record.tenant_id,
                "persona_id": record.persona_id,
                "persona_capital_binding_id": ids.persona_capital_binding_id,
                "execution_context": "paper",
                "rollback_semantics": "zero_capital_safe_stop",
                "strategy_spec_registry_id": ids.registry_id,
                "strategy_artifact_id": ids.strategy_artifact_id,
                "rollback_registry_id": ids.baseline_strategy_artifact_id,
                "rollback_strategy_spec_registry_id": ids.baseline_registry_id,
                "rollback_approval_decision_id": (
                    ids.baseline_strategy_artifact_approval_decision_id
                ),
                "requested_by": self._requested_by(record),
                "provisioning_request_hash": record.request_hash,
                **({"market": explicit_market} if explicit_market is not None else {}),
            },
        }

        def validate(receipt: Mapping[str, Any]) -> None:
            rollback = receipt.get("rollback")
            rollback = rollback if isinstance(rollback, Mapping) else {}
            metadata = receipt.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if (
                receipt.get("plan_id") != ids.deployment_plan_id
                or receipt.get("approval_decision_id")
                != ids.strategy_artifact_approval_decision_id
                or receipt.get("artifact_id") != ids.strategy_artifact_id
                or receipt.get("artifact_version") != ids.version
                or receipt.get("strategy_id") != ids.strategy_id
                or receipt.get("capital_pool_id") != ids.capital_pool_id
                or receipt.get("target_stage") != "paper"
                or receipt.get("status") not in {"approved", "executing", "executed"}
                # DeploymentPlan.binding_id is reserved for the canonical
                # RuntimeBinding and must never alias PersonaCapitalBinding.
                or receipt.get("binding_id") == ids.persona_capital_binding_id
                or metadata.get("persona_capital_binding_id")
                != ids.persona_capital_binding_id
                or metadata.get("strategy_artifact_id") != ids.strategy_artifact_id
                or rollback.get("target_artifact_id") != ids.baseline_strategy_artifact_id
                or rollback.get("target_version") != ids.baseline_version
                or (
                    explicit_market is not None
                    and metadata.get("market") != explicit_market
                )
            ):
                raise PersonaProvisioningCoordinationError(
                    "DeploymentPlan readback does not match approved paper admission"
                )

        receipt = self._create_then_get(
            owner="deployment",
            get_path=f"/api/deployment/plans/{_path_id(ids.deployment_plan_id)}",
            post_path="/api/deployment/plans",
            payload=payload,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step="deployment_plan_readback",
            key="deployment_plan",
            receipt=receipt,
        )

    def build_paper_execution_projection_metadata(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        deployment_plan_receipt: Mapping[str, Any] | None = None,
        registry_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the paper RuntimeBinding execution projection via StagePlanner.build_execution_projection.

        Reuses the approved-artifact projection contract used by
        ``services.deployment.service.DeploymentService.create_plan`` for inline
        paper artifacts (``object_store``, ``strategy_id``, ``artifact_checksum``,
        ``symbol``/``symbols``, ``market_data_policy``). Requires real validated
        Registry and Deployment owner receipts and fails closed when either receipt
        is absent, incomplete, or not approved.
        """
        from services.control_plane.governance.deployment_plan import (
            DeploymentPlan,
            DeploymentPlanError,
            StagePlanner,
        )
        from services.registry.strategy_artifact import (
            strategy_artifact_minimum_closes,
            validate_strategy_artifact,
        )

        if not isinstance(registry_receipt, Mapping) or not registry_receipt:
            raise PersonaProvisioningCoordinationError(
                "Approved StrategyArtifact RegistryEntry receipt is required to build paper execution projection",
                reason_code="registry_receipt_missing",
            )
        if not isinstance(deployment_plan_receipt, Mapping) or not deployment_plan_receipt:
            raise PersonaProvisioningCoordinationError(
                "Approved DeploymentPlan receipt is required to build paper execution projection",
                reason_code="deployment_plan_receipt_missing",
            )

        symbols, _, _ = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)
        self._validate_registry(
            registry_receipt,
            ids,
            state="approved",
            registry_id=ids.strategy_artifact_id,
            version=ids.version,
            approval_decision_id=ids.strategy_artifact_approval_decision_id,
            artifact_type="execution_bundle",
            expected_market=explicit_market,
        )
        entry = deepcopy(dict(_registry_entry(registry_receipt)))
        if (
            entry.get("approval_decision_id") != ids.strategy_artifact_approval_decision_id
            or not isinstance(entry.get("approved_at"), str)
            or not str(entry.get("approved_at") or "").strip()
        ):
            raise PersonaProvisioningCoordinationError(
                "Approved StrategyArtifact RegistryEntry is missing approval_decision_id or approved_at",
                reason_code="registry_entry_unapproved",
            )
        entry_meta = (
            dict(entry.get("metadata") or {})
            if isinstance(entry.get("metadata"), Mapping)
            else {}
        )
        raw_artifact = entry_meta.get("strategy_artifact")
        if not isinstance(raw_artifact, Mapping):
            raise PersonaProvisioningCoordinationError(
                "StrategyArtifact RegistryEntry is missing metadata.strategy_artifact"
            )
        artifact = deepcopy(dict(raw_artifact))

        try:
            validate_strategy_artifact(artifact)
        except StrategyArtifactValidationError as exc:
            raise PersonaProvisioningCoordinationError(str(exc)) from exc

        checksum = _artifact_checksum(artifact)
        for artifact_field, registry_field in (
            ("artifact_id", "registry_id"),
            ("strategy_id", "strategy_id"),
            ("version", "version"),
        ):
            if artifact.get(artifact_field) != entry.get(registry_field):
                raise PersonaProvisioningCoordinationError(
                    f"StrategyArtifact {artifact_field} does not match Registry"
                )
        if entry.get("checksum") != checksum:
            raise PersonaProvisioningCoordinationError(
                "StrategyArtifact checksum does not match approved Registry entry"
            )

        raw_plan_meta = deployment_plan_receipt.get("metadata")
        plan_meta: dict[str, Any] = (
            deepcopy(dict(raw_plan_meta))
            if isinstance(raw_plan_meta, Mapping)
            else {}
        )
        raw_rollback = deployment_plan_receipt.get("rollback")
        rollback_map = raw_rollback if isinstance(raw_rollback, Mapping) else {}
        if (
            deployment_plan_receipt.get("plan_id") != ids.deployment_plan_id
            or deployment_plan_receipt.get("approval_decision_id")
            != ids.strategy_artifact_approval_decision_id
            or deployment_plan_receipt.get("artifact_id") != ids.strategy_artifact_id
            or deployment_plan_receipt.get("artifact_version") != ids.version
            or deployment_plan_receipt.get("artifact_type") != "execution_bundle"
            or deployment_plan_receipt.get("strategy_id") != ids.strategy_id
            or deployment_plan_receipt.get("capital_pool_id") != ids.capital_pool_id
            or deployment_plan_receipt.get("target_stage") != "paper"
            or deployment_plan_receipt.get("status")
            not in {"approved", "executing", "executed"}
            or not isinstance(deployment_plan_receipt.get("created_at"), str)
            or not str(deployment_plan_receipt.get("created_at") or "").strip()
            or deployment_plan_receipt.get("binding_id") == ids.persona_capital_binding_id
            or plan_meta.get("persona_capital_binding_id")
            != ids.persona_capital_binding_id
            or plan_meta.get("strategy_artifact_id") != ids.strategy_artifact_id
            or rollback_map.get("target_artifact_id")
            != ids.baseline_strategy_artifact_id
            or rollback_map.get("target_version") != ids.baseline_version
            or (
                explicit_market is not None
                and plan_meta.get("market") != explicit_market
            )
        ):
            raise PersonaProvisioningCoordinationError(
                "DeploymentPlan readback does not match approved paper admission",
                reason_code="deployment_plan_invalid",
            )
        try:
            plan_obj = DeploymentPlan.from_dict(deployment_plan_receipt)
        except (KeyError, TypeError, ValueError, DeploymentPlanError) as exc:
            raise PersonaProvisioningCoordinationError(
                f"DeploymentPlan readback is incomplete or invalid: {exc}",
                reason_code="deployment_plan_invalid",
            ) from exc
        plan_errors = plan_obj.validate()
        if plan_errors:
            raise PersonaProvisioningCoordinationError(
                "DeploymentPlan readback validation failed: " + "; ".join(plan_errors),
                reason_code="deployment_plan_invalid",
            )

        try:
            projection = StagePlanner().build_execution_projection(plan_obj, entry)
        except DeploymentPlanError as exc:
            raise PersonaProvisioningCoordinationError(str(exc)) from exc

        artifact_text = json.dumps(
            artifact,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        object_store = (
            deepcopy(dict(plan_meta.get("object_store")))
            if isinstance(plan_meta.get("object_store"), Mapping)
            else {}
        )
        object_store[projection.metadata_key] = projection.metadata
        object_store[projection.artifact_key] = artifact_text

        result: dict[str, Any] = {
            "object_store": object_store,
            "strategy_id": str(artifact["strategy_id"]),
            "artifact_checksum": checksum,
        }
        parameters = (
            artifact.get("parameters")
            if isinstance(artifact.get("parameters"), Mapping)
            else {}
        )
        symbol = str(plan_meta.get("symbol") or "").strip()
        if symbol:
            result["symbol"] = symbol
        else:
            symbols = parameters.get("symbols")
            cleaned = (
                [x.strip() for x in symbols if isinstance(x, str) and x.strip()]
                if isinstance(symbols, list)
                else []
            )
            if (
                not isinstance(symbols, list)
                or not cleaned
                or len(cleaned) != len(symbols)
                or len(set(cleaned)) != len(cleaned)
            ):
                raise PersonaProvisioningCoordinationError(
                    "paper_plan_symbols_invalid: inline paper artifact requires an explicit symbol "
                    "or a non-empty list of distinct approved symbols"
                )
            if len(cleaned) == 1:
                result["symbol"] = cleaned[0]
            else:
                result["symbols"] = list(cleaned)

        existing_policy = plan_meta.get("market_data_policy")
        if isinstance(existing_policy, Mapping):
            result["market_data_policy"] = deepcopy(dict(existing_policy))
        else:
            try:
                minimum_closes = strategy_artifact_minimum_closes(artifact)
            except (StrategyArtifactValidationError, KeyError, TypeError, ValueError) as exc:
                raise PersonaProvisioningCoordinationError(
                    f"minimum_closes cannot be derived: {exc}"
                ) from exc
            result["market_data_policy"] = {
                "owner": "source-ingest",
                "contract": "latest_stored_normalized",
                "minimum_closes": minimum_closes,
                "max_age_seconds": 86400,
            }
        return result

    def _coordinate_deployment_dispatch(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        symbols, _, _ = self._paper_universe(record)
        explicit_market = self._explicit_market(record, symbols)
        saga_path = f"/api/deployment/sagas/{_path_id(ids.deployment_saga_id)}"

        def ready(receipt: Mapping[str, Any]) -> bool:
            return (
                receipt.get("saga_id") == ids.deployment_saga_id
                and receipt.get("plan_id") == ids.deployment_plan_id
                and str(receipt.get("status") or "")
                not in {"failed", "aborted", "compensating"}
            )

        def validate(receipt: Mapping[str, Any]) -> None:
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "Deployment saga readback does not prove admitted provisioning"
                )

        # Same owner-authority rule as the plan POST: prove the approved
        # RegistryEntry checkpoint exists locally, then dispatch identifiers
        # only.  DispatchDeploymentPlanRequest forbids extra fields and
        # Deployment re-reads the authoritative entry itself.
        self._validate_registry(
            _mapping(
                record.references.get("strategy_artifact_approved"),
                label="checkpointed approved StrategyArtifact RegistryEntry",
            ),
            ids,
            state="approved",
            registry_id=ids.strategy_artifact_id,
            version=ids.version,
            approval_decision_id=ids.strategy_artifact_approval_decision_id,
            artifact_type="execution_bundle",
            expected_market=explicit_market,
        )
        receipt = self._transition_then_get(
            owner="deployment",
            get_path=saga_path,
            post_path=(
                f"/api/deployment/plans/{_path_id(ids.deployment_plan_id)}/dispatch"
            ),
            payload={
                "trace_id": f"persona-provisioning:{ids.token}",
                "correlation_id": record.idempotency_key,
                "idempotency_key": f"{record.idempotency_key}:deployment-dispatch",
                "actor_id": self.actor_id,
                "saga_id": ids.deployment_saga_id,
                "source_task_id": f"persona-provisioning-{ids.token}",
                "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
                "metadata": {
                    "tenant_id": record.tenant_id,
                    "persona_id": record.persona_id,
                    "capital_pool_id": ids.capital_pool_id,
                    "persona_capital_binding_id": ids.persona_capital_binding_id,
                    "execution_context": "paper",
                    "requested_by": self._requested_by(record),
                    **({"market": explicit_market} if explicit_market is not None else {}),
                },
            },
            ready=ready,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step="deployment_dispatch_admitted_readback",
            key="deployment_dispatch",
            receipt=receipt,
        )

    @staticmethod
    def _workflow_ids(items: Any) -> set[str]:
        if not isinstance(items, list):
            return set()
        values: set[str] = set()
        for item in items:
            if isinstance(item, Mapping):
                workflow_id = str(item.get("workflow_id") or "").strip()
                if workflow_id:
                    values.add(workflow_id)
            elif isinstance(item, str) and item.strip():
                values.add(item.strip())
        return values

    def _validate_schedule_receipt(
        self,
        receipt: Mapping[str, Any],
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> None:
        mode = str(receipt.get("mode") or "").strip().lower()
        if mode in {"", "dry_run", "unavailable", "disabled"}:
            raise PersonaProvisioningCoordinationError(
                f"first-evaluation schedule registrar is not live (mode={mode or 'missing'})"
            )
        workflows = self._workflow_ids(receipt.get("registered")) | self._workflow_ids(
            receipt.get("skipped")
        )
        authoritative = receipt.get("authoritative_readback")
        authoritative = authoritative if isinstance(authoritative, Mapping) else {}
        authoritative_first_evaluation = (
            authoritative.get("persona_id") == record.persona_id
            and authoritative.get("workflow_id") == FIRST_EVALUATION_WORKFLOW_ID
            and authoritative.get("registered") is True
        )
        if (
            FIRST_EVALUATION_WORKFLOW_ID not in workflows
            and not authoritative_first_evaluation
        ):
            raise PersonaProvisioningCoordinationError(
                "schedule receipt does not contain the exact first-evaluation workflow"
            )
        if (
            receipt.get("persona_id") != record.persona_id
            or receipt.get("capital_pool_id") != ids.capital_pool_id
            or receipt.get("binding_id") != ids.persona_capital_binding_id
        ):
            raise PersonaProvisioningCoordinationError(
                "schedule receipt identity does not match the admitted Persona binding"
            )

    def _coordinate_schedule(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        prior = record.references.get("first_evaluation_schedule")
        if isinstance(prior, Mapping):
            receipt = deepcopy(dict(prior))
            self._validate_schedule_receipt(receipt, record, ids)
        else:
            value = self.schedule_registrar(
                record.persona_id,
                ids.capital_pool_id,
                ids.persona_capital_binding_id,
            )
            receipt = _mapping(value, label="first-evaluation schedule registrar")
            self._validate_schedule_receipt(receipt, record, ids)
        if "provisioning_readback_started_at" not in record.references:
            record.references["provisioning_readback_started_at"] = utc_now()
        return self._checkpoint_receipt(
            record,
            step="schedule_registered_readback",
            key="first_evaluation_schedule",
            receipt=receipt,
        )

    # ------------------------------------------------------------------
    # Pool envelope publication (R6): same owner paths, paper scope only.
    # ------------------------------------------------------------------
    def publish_pool_envelope(
        self, publication: Mapping[str, Any], *, binding_snapshot: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Admit one immutable pool-envelope artifact and move the binding.

        ``binding_snapshot`` is the publisher's just-read owner state, supplied
        internally to share one pre-dispatch read in a daily cycle. It is never
        accepted in a public request; all binding identity/stage checks below
        and the Deployment owner's replacement guards still run.

        Every owner write is GET-first with authoritative readback, so a
        retry after response loss, or a duplicate publish, replays instead of
        duplicating.  Approval is the same automated paper-scope Governance
        decision used for admission; nothing beyond paper is accepted.  The
        runtime moves through the Deployment saga (Deployment calls the
        runtime-manager ``replace`` owner route); this method never calls
        RuntimeManager.  Any mismatch raises
        ``PersonaProvisioningCoordinationError`` before the binding moves, so
        the previous envelope keeps running.
        """

        pub = dict(publication)
        tenant_id = str(pub["tenant_id"])
        persona_id = str(pub["persona_id"])
        pool_version = int(pub["pool_version"])
        envelope = _mapping(pub["envelope"], label="envelope artifact")
        if str(pub.get("stage") or "paper") != "paper":
            raise PersonaProvisioningCoordinationError(
                "pool envelope publication is limited to paper scope"
            )
        record = ProvisioningRecord(
            tenant_id=tenant_id,
            idempotency_key=f"pool-publish:{persona_id}:{pool_version}",
            request_hash=str(pub["request_hash"]),
            normalized_name="",
            persona_id=persona_id,
            request_payload={},
        )
        ids = deterministic_provisioning_ids(record)
        label = f"pool-{ids.token}-v{pool_version}"
        artifact_id = str(envelope["artifact_id"])
        version = str(envelope["version"])
        decision_id = f"apv-persona-{label}"
        plan_id = f"plan-persona-{label}"
        saga_id = f"deployment-saga-{plan_id}"
        previous = _mapping(pub["previous"], label="previous envelope")
        previous_id = str(previous["artifact_id"])
        previous_version = str(previous["version"])
        market = str(pub.get("market") or "") or None

        # 1. The binding being replaced must be the verified current one.
        if self.runtime_binding_reader is None:
            raise PersonaProvisioningCoordinationError(
                "runtime binding readback is required to publish a pool envelope"
            )
        bindings = [
            b
            for b in (binding_snapshot if binding_snapshot is not None else self.runtime_binding_reader(
                str(previous.get("plan_id") or ids.deployment_plan_id),
                ids.capital_pool_id,
            ))
            if str(b.get("status") or "") not in TERMINAL_RUNTIME_BINDING_STATUSES
        ]
        current = [
            b
            for b in bindings
            if b.get("capital_pool_id") == ids.capital_pool_id
            and b.get("persona_capital_binding_id") == ids.persona_capital_binding_id
        ]
        if len(current) != 1:
            raise PersonaProvisioningCoordinationError(
                f"expected exactly one live RuntimeBinding for the persona pool, got {len(current)}"
            )
        binding = current[0]
        already_moved = (binding.get("artifact_id"), binding.get("artifact_version")) == (
            artifact_id,
            version,
        )
        if not already_moved and (
            binding.get("artifact_id") != previous_id
            or binding.get("artifact_version") != previous_version
            or binding.get("deployment_mode") != "paper"
            or binding.get("status") != "active"
        ):
            raise PersonaProvisioningCoordinationError(
                "current RuntimeBinding does not run the previous envelope in active paper"
            )

        # 1b. If the bound CapitalPool exists at the Capital owner, verify its bound RiskPolicy
        # is resolved, active, paper-stage, and tenant-isolated before rebinding/renewing.
        pool_rec = self._owner_get("capital", f"/api/capital-pools/{_path_id(ids.capital_pool_id)}")
        if pool_rec is not None:
            pool_meta = pool_rec.get("metadata") if isinstance(pool_rec.get("metadata"), Mapping) else {}
            ledger_record = self.store.get_by_persona(tenant_id, persona_id) if self.store is not None else None
            ledger_profile, _, _ = (
                self._record_risk_profile_context(ledger_record)
                if ledger_record is not None
                else (None, False, None)
            )
            pool_policy_res = resolve_pool_risk_policy_binding(
                capital_pool_id=ids.capital_pool_id,
                pool=self._pool_with_authoritative_approval_digest(
                    pool_rec,
                    tenant_id=tenant_id,
                ),
                expected_tenant_id=tenant_id,
                expected_stage="paper",
                requested_profile=ledger_profile or (
                    str(pool_meta.get("requested_risk_profile")).strip()
                    if pool_meta.get("requested_risk_profile")
                    else None
                ),
                require_approved_profile_mapping=None,
                approval_reader=self._governance_approval_reader(),
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
            if not pool_policy_res.resolved:
                raise PersonaProvisioningCoordinationError(
                    f"Cannot publish pool envelope: bound capital pool risk policy is unresolved "
                    f"({pool_policy_res.reason_code}: {pool_policy_res.reason})"
                )
        elif pub.get("require_risk_policy") is True:
            raise PersonaProvisioningCoordinationError(
                f"Cannot publish pool envelope: bound capital pool {ids.capital_pool_id!r} not found (risk_policy_missing)"
            )

        # 2. Candidate envelope in Registry.
        get_path = f"/api/registry/strategy-artifacts/{_path_id(artifact_id)}"
        metadata = {
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "capital_pool_id": ids.capital_pool_id,
            "persona_capital_binding_id": ids.persona_capital_binding_id,
            "execution_context": "paper",
            "capital_scale_pct": 0.0,
            "requested_by": pub.get("requested_by"),
            "pool_version": pool_version,
            "pool_digest": pub["pool_digest"],
            "previous_envelope_id": previous_id,
            "previous_envelope_version": previous_version,
            "rollback_target_registry_id": previous_id,
            **({"market": market} if market is not None else {}),
        }

        def validate_registry(receipt: Mapping[str, Any], *, state: str | None = None) -> None:
            entry = _registry_entry(receipt)
            if (
                entry.get("registry_id") != artifact_id
                or entry.get("version") != version
                or entry.get("strategy_id") != ids.strategy_id
                or entry.get("artifact_type") != "execution_bundle"
                or (state is not None and entry.get("artifact_state") != state)
                or entry.get("artifact_state") not in {"candidate", "approved"}
            ):
                raise PersonaProvisioningCoordinationError(
                    "envelope RegistryEntry readback does not match the published pool version"
                )
            body = (entry.get("metadata") or {}).get("strategy_artifact")
            if not isinstance(body, Mapping) or _artifact_checksum(
                body
            ) != _artifact_checksum(envelope):
                raise PersonaProvisioningCoordinationError(
                    "envelope RegistryEntry artifact differs from the pinned pool snapshot"
                )
            if (entry.get("metadata") or {}).get("pool_version") != pool_version:
                raise PersonaProvisioningCoordinationError(
                    "envelope RegistryEntry is bound to a different pool version"
                )

        self._create_then_get(
            owner="registry",
            get_path=get_path,
            post_path="/api/registry/strategy-artifacts",
            payload={
                "registry_id": artifact_id,
                "artifact_state": "candidate",
                "strategy_artifact": dict(envelope),
                "producer_run_id": f"persona-pool-publish-{label}",
                "evaluation_summary": {
                    "admission": "persona_pool_envelope",
                    "risk_level": "low",
                    "execution_context": "paper",
                    "capital_scale_pct": 0.0,
                    "pool_version": pool_version,
                },
                "rollback_target": previous_id,
                "metadata": metadata,
            },
            validate=validate_registry,
        )

        # 3. Automated paper-scope approval bound to the exact checksum.
        candidate = _registry_entry(self._owner_get("registry", get_path) or {})
        candidate_digest = _owner_digest(
            candidate.get("checksum"), label=f"RegistryEntry {artifact_id} readback"
        )
        expires_at = _approval_expiry(
            candidate.get("created_at"), label=f"RegistryEntry {artifact_id} created_at"
        )
        approval_path = f"/api/governance/approvals/{_path_id(decision_id)}"

        def validate_approval(receipt: Mapping[str, Any]) -> None:
            self._validate_approval_identity(
                receipt,
                record,
                ids,
                registry_id=artifact_id,
                decision_id=decision_id,
                version=version,
                candidate_digest=candidate_digest,
                expires_at=expires_at,
            )

        self._create_then_get(
            owner="governance",
            get_path=approval_path,
            post_path="/api/governance/approvals",
            payload={
                "decision_id": decision_id,
                "expected_version": 0,
                "target_type": "registry_entry",
                "target_id": artifact_id,
                "target_version": version,
                "risk_level": "low",
                "capital_pool_id": ids.capital_pool_id,
                "persona_id": persona_id,
                "tenant_id": tenant_id,
                "owner_user_id": self.governance_actor_id,
                "proposal_id": f"persona-pool-publish-{label}",
                "proposal_revision": 1,
                "proposal_content_digest": str(pub["request_hash"]),
                "validation_result_digest": _stable_hash(
                    {
                        "registry_id": artifact_id,
                        "risk_level": "low",
                        "candidate_digest": candidate_digest,
                    }
                ),
                "candidate_digest": candidate_digest,
                "expires_at": expires_at,
            },
            validate=validate_approval,
        )

        def decision_ready(receipt: Mapping[str, Any]) -> bool:
            return receipt.get("decision_state") == "decided" and receipt.get("decision") == "approved"

        def review_ready(receipt: Mapping[str, Any]) -> bool:
            return receipt.get("decision_state") in {"under_review", "decided"}

        def cas(current: Mapping[str, Any] | None, extra: Mapping[str, Any]) -> Mapping[str, Any]:
            if current is None:
                raise PersonaProvisioningCoordinationError("ApprovalDecision has no readback")
            validate_approval(current)
            return {
                "actor_role": "automated_gate",
                "actor_id": self.governance_actor_id,
                "expected_version": int(current.get("version") or 0),
                **extra,
            }

        self._transition_then_get(
            owner="governance",
            get_path=approval_path,
            post_path=f"{approval_path}/review",
            payload=lambda current: cas(current, {}),
            ready=review_ready,
            validate=lambda r: (validate_approval(r), review_ready(r) or (_ for _ in ()).throw(
                PersonaProvisioningCoordinationError("ApprovalDecision was not accepted for review")
            )),
        )

        def decide_valid(receipt: Mapping[str, Any]) -> None:
            validate_approval(receipt)
            if not decision_ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision readback is not decided/approved"
                )
            expiry = _parse_owner_time(receipt.get("expires_at"), label="approval expires_at")
            if expiry <= datetime.now(timezone.utc):
                raise PersonaProvisioningCoordinationError(
                    f"ApprovalDecision {decision_id} expired; it is not renewed"
                )

        self._transition_then_get(
            owner="governance",
            get_path=approval_path,
            post_path=f"{approval_path}/decide",
            payload=lambda current: cas(
                current,
                {
                    "outcome": "approved",
                    "rationale": "Low-risk internal paper pool envelope",
                    "evidence_refs": [{"ref_type": "registry_entry", "ref_id": artifact_id}],
                    "candidate_digest": candidate_digest,
                    "expires_at": expires_at,
                },
            ),
            ready=decision_ready,
            validate=decide_valid,
        )

        def approved_ready(receipt: Mapping[str, Any]) -> bool:
            entry = _registry_entry(receipt)
            return (
                entry.get("artifact_state") == "approved"
                and entry.get("approval_decision_id") == decision_id
            )

        def advance_payload(current: Mapping[str, Any] | None) -> Mapping[str, Any]:
            if current is None:
                raise PersonaProvisioningCoordinationError("envelope candidate has no readback")
            validate_registry(current, state="candidate")
            entry = _registry_entry(current)
            base = {
                "approval_decision_id": decision_id,
                "expected_artifact_state": "candidate",
                "expected_version": str(entry["version"]),
                "expected_updated_at": entry.get("updated_at"),
            }
            return {
                "target_state": "approved",
                "command_key": (
                    f"persona-pool-publish:{ids.token}:advance:{artifact_id}:approved:"
                    f"{_stable_hash(base)[:16]}"
                ),
                **base,
            }

        self._transition_then_get(
            owner="registry",
            get_path=get_path,
            post_path=f"{get_path}/advance",
            payload=advance_payload,
            ready=approved_ready,
            validate=lambda r: (
                validate_registry(r, state="approved"),
                approved_ready(r) or (_ for _ in ()).throw(
                    PersonaProvisioningCoordinationError(
                        "envelope RegistryEntry does not cite the canonical ApprovalDecision"
                    )
                ),
            ),
        )

        # 4. DeploymentPlan identifies the verified current binding.
        plan_path = f"/api/deployment/plans/{_path_id(plan_id)}"

        def validate_plan(receipt: Mapping[str, Any]) -> None:
            plan_meta = receipt.get("metadata")
            plan_meta = plan_meta if isinstance(plan_meta, Mapping) else {}
            if (
                receipt.get("plan_id") != plan_id
                or receipt.get("approval_decision_id") != decision_id
                or receipt.get("artifact_id") != artifact_id
                or receipt.get("artifact_version") != version
                or receipt.get("capital_pool_id") != ids.capital_pool_id
                or receipt.get("target_stage") != "paper"
                or receipt.get("current_stage") != "paper"
                or receipt.get("runtime_action") != "replace_binding"
                or receipt.get("status") not in {"approved", "executing", "executed"}
                or plan_meta.get("persona_capital_binding_id") != ids.persona_capital_binding_id
                or plan_meta.get("pool_version") != pool_version
            ):
                raise PersonaProvisioningCoordinationError(
                    "DeploymentPlan readback does not match the published pool envelope"
                )

        self._create_then_get(
            owner="deployment",
            get_path=plan_path,
            post_path="/api/deployment/plans",
            payload={
                "plan_id": plan_id,
                "approval_decision_id": decision_id,
                "capital_pool_id": ids.capital_pool_id,
                "target_stage": "paper",
                "current_stage": "paper",
                "registry_id": artifact_id,
                "binding_id": binding["binding_id"],
                "created_by": self.actor_id,
                "sponsor_persona_id": persona_id,
                "scale": {"capital_scale_pct": 0.0, "gross_scale_pct": 100.0},
                "rollback": {
                    "target_artifact_id": previous_id,
                    "target_version": previous_version,
                    "action_type": "pause_then_replace",
                    "reason": "Fail closed to the previous approved envelope",
                },
                "pre_checks": [
                    "registry_entry_approved",
                    "approval_decision_approved",
                    "persona_capital_binding_active",
                ],
                "post_checks": ["deployment_saga_admitted"],
                "status": "approved",
                "metadata": {
                    "tenant_id": tenant_id,
                    "persona_id": persona_id,
                    "persona_capital_binding_id": ids.persona_capital_binding_id,
                    "execution_context": "paper",
                    "pool_version": pool_version,
                    "strategy_artifact_id": artifact_id,
                    "requested_by": pub.get("requested_by"),
                    **({"market": market} if market is not None else {}),
                },
            },
            validate=validate_plan,
        )

        # 5. Dispatch through Deployment; it replaces the binding.
        saga_path = f"/api/deployment/sagas/{_path_id(saga_id)}"

        def saga_ready(receipt: Mapping[str, Any]) -> bool:
            return (
                receipt.get("saga_id") == saga_id
                and receipt.get("plan_id") == plan_id
                and str(receipt.get("status") or "") not in {"failed", "aborted", "compensating"}
            )

        def validate_saga(receipt: Mapping[str, Any]) -> None:
            if saga_ready(receipt):
                return
            if (
                receipt.get("saga_id") == saga_id
                and receipt.get("plan_id") == plan_id
                and str(receipt.get("status") or "") in {"failed", "aborted"}
            ):
                raise _TerminalSagaRejection(
                    "Deployment saga reached a terminal failure for the envelope dispatch"
                )
            raise PersonaProvisioningCoordinationError(
                "Deployment saga readback does not prove the envelope dispatch"
            )

        def _receipt(saga_status: Any) -> dict[str, Any]:
            return {
                "persona_id": persona_id,
                "pool_version": pool_version,
                "envelope_artifact_id": artifact_id,
                "envelope_version": version,
                "approval_decision_id": decision_id,
                "deployment_plan_id": plan_id,
                "deployment_saga_id": saga_id,
                "previous_envelope_id": previous_id,
                "source_binding_id": binding["binding_id"],
                "runtime_id": binding.get("runtime_id"),
                "capital_pool_id": ids.capital_pool_id,
                "persona_capital_binding_id": ids.persona_capital_binding_id,
                "saga_status": saga_status,
                "binding_already_moved": already_moved,
            }

        dispatch_attempted = False

        def dispatch_payload(_receipt_in: Mapping[str, Any] | None) -> Mapping[str, Any]:
            # Called only immediately before the dispatch POST is sent.
            nonlocal dispatch_attempted
            dispatch_attempted = True
            return dispatch_body

        dispatch_body = {
                    "trace_id": f"persona-pool-publish:{ids.token}:{pool_version}",
                    "correlation_id": record.idempotency_key,
                    "idempotency_key": f"{record.idempotency_key}:deployment-dispatch",
                    "actor_id": self.actor_id,
                    "saga_id": saga_id,
                    "source_task_id": f"persona-pool-publish-{label}",
                    "metadata": {
                        "tenant_id": tenant_id,
                        "persona_id": persona_id,
                        "capital_pool_id": ids.capital_pool_id,
                        "persona_capital_binding_id": ids.persona_capital_binding_id,
                        "execution_context": "paper",
                        "pool_version": pool_version,
                    },
                }
        try:
            saga = self._transition_then_get(
                owner="deployment",
                get_path=saga_path,
                post_path=f"/api/deployment/plans/{_path_id(plan_id)}/dispatch",
                payload=dispatch_payload,
                ready=saga_ready,
                validate=validate_saga,
            )
        except Exception as exc:
            if not dispatch_attempted:
                # Failed before any dispatch POST (e.g. initial saga GET):
                # nothing was admitted, so surface the raw error and let the
                # caller fail closed with the previous binding unchanged.
                raise
            terminal = False
            cause: BaseException | None = exc
            while cause is not None:
                if isinstance(cause, _TerminalSagaRejection):
                    terminal = True
                    break
                cause = cause.__cause__
            if terminal:
                raise PoolDispatchUncertain(
                    str(exc), _receipt(None), terminal_rejection=True
                ) from exc
            # Dispatch may have been admitted: the owners, not this exception,
            # say whether the binding moved.  Never claim the previous kept.
            raise PoolDispatchUncertain(str(exc), _receipt(None)) from exc
        return _receipt(saga.get("status"))

    def read_paper_risk_change(self, *, tenant_id: str, persona_id: str, pool_id: str) -> dict[str, Any]:
        from services.capital.profile_change import validate_scope
        from services.capital.pg_store import CapitalPool

        path = f"/api/capital-pools/{_path_id(pool_id)}"
        pool = self._owner_get("capital", path)
        if pool is None or pool.get("pool_id") != pool_id or not self._formal_tenant_matches(pool, tenant_id):
            raise PersonaProvisioningCoordinationError("paper_risk_change_pool_readback_failed")
        validate_scope(CapitalPool.from_dict(pool), tenant_id, persona_id)
        change = self._owner_get("capital", f"{path}/risk-profile-change?persona_id={_path_id(persona_id)}")
        if not change or change.get("requestedRisk") not in {None, "low", "medium", "high"}:
            raise PersonaProvisioningCoordinationError("paper_risk_change_readback_failed")
        return change

    def change_paper_risk_profile(
        self, *, tenant_id: str, persona_id: str, pool_id: str,
        profile: str, request_id: str, request_hash: str, requested_by: str, issued_at_ms: int | None,
    ) -> dict[str, Any]:
        before = self.read_paper_risk_change(tenant_id=tenant_id, persona_id=persona_id, pool_id=pool_id)
        path = f"/api/capital-pools/{_path_id(pool_id)}"
        failure = None
        try:
            self.transport.post("capital", f"{path}/risk-profile-change", {
                "actor_id": self.actor_id, "actor_role": "capital.admin",
                "requested_by": requested_by,
                "expected_persona_id": persona_id, "expected_revision": before["revision"],
                "issued_at_ms": issued_at_ms,
                "request_id": request_id, "request_hash": request_hash, "requested_profile": profile,
            })
        except Exception as exc:
            failure = exc
        # Lost responses are recovered only from a durable, scoped receipt.
        pool = self._owner_get("capital", path)
        meta = pool.get("metadata", {}) if pool else {}
        receipt = (meta.get("risk_profile_change_receipts") or {}).get(request_id)
        if receipt is None and issued_at_ms is None:
            # A superseded opaque key may have moved from the bounded row
            # window to Capital's exact durable audit archive. Scope validation
            # runs on the same owner read before exposing any receipt.
            archived = self._owner_get("capital", f"{path}/risk-profile-change?persona_id={_path_id(persona_id)}&request_id={_path_id(request_id)}")
            receipt = (archived or {}).get("requestReceipt")
        if (not receipt or receipt.get("request_hash") != request_hash or receipt.get("profile") != profile
                or receipt.get("issued_at_ms") != issued_at_ms):
            reason = ("paper_risk_change_idempotency_conflict" if receipt else
                      "paper_risk_change_key_stale" if
                      (issued_at_ms is not None and (
                       issued_at_ms <= int(meta.get("risk_profile_change_watermark_ms", 0)) or
                       issued_at_ms < max((r.get("issued_at_ms") or 0 for r in
                                          (meta.get("risk_profile_change_receipts") or {}).values()), default=0))) else
                      "paper_risk_change_revision_conflict" if
                      int((meta.get("risk_profile_change") or {}).get("revision", 0)) != before["revision"]
                      else "paper_risk_change_not_committed")
            raise PersonaProvisioningCoordinationError(reason, reason_code=reason) from failure
        change = self.read_paper_risk_change(tenant_id=tenant_id, persona_id=persona_id, pool_id=pool_id)
        resolution = change.get("resolution") or {}
        owner_request = resolution.get("owner_decision_request")
        if change.get("requestId") == request_id and change.get("status") == "pending_approval" and owner_request:
            from types import SimpleNamespace

            change["ownerDecisionRequest"] = self._route_or_reuse_risk_policy_owner_decision_request(
                tenant_id=tenant_id, persona_id=persona_id, pool_id=pool_id,
                requested_profile=change["requestedRisk"], resolution=SimpleNamespace(**resolution),
                proposal_key=str(owner_request.get("candidate_risk_policy_digest") or ""),
            )
        return change

    def repair_paper_pool_risk_policy(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        capital_pool_id: str | None = None,
        binding_id: str | None = None,
        requested_by: str = "system-governance",
        requested_profile: str | None = None,
        risk_policy_ref: str | None = None,
        risk_policy: Mapping[str, Any] | None = None,
        risk_policy_version: str | None = None,
        approval_decision_id: str | None = None,
        require_approved_profile_mapping: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Idempotent governed repair and authoritative readback for a dev paper pool's RiskPolicy.

        Covers new pools, existing active trading pools, retries, rebinding, and policy version
        changes. Preserves tenant isolation, existing paper/live boundaries, and legitimate inline
        or explicitly selected policy contracts where complete. When no approved mapping exists
        and ``require_approved_profile_mapping=True``, surfaces the decision to the policy owner
        with evidence instead of inventing arbitrary numeric limits.
        """
        for label_name, value in (("tenant_id", tenant_id), ("persona_id", persona_id)):
            if not str(value or "").strip():
                raise PersonaProvisioningCoordinationError(f"{label_name} is required")

        ledger_record = self.store.get_by_persona(tenant_id, persona_id) if self.store is not None else None
        if ledger_record is not None:
            ids = deterministic_provisioning_ids(ledger_record)
        else:
            synthetic = ProvisioningRecord(
                tenant_id=tenant_id,
                idempotency_key=f"repair-risk-policy:{persona_id}",
                request_hash="",
                normalized_name="",
                persona_id=persona_id,
                request_payload={},
            )
            ids = deterministic_provisioning_ids(synthetic)

        resolved_pool_id = str(capital_pool_id or ids.capital_pool_id).strip()
        resolved_binding_id = str(binding_id or ids.persona_capital_binding_id).strip()
        pool_path = f"/api/capital-pools/{_path_id(resolved_pool_id)}"
        pool = self._owner_get("capital", pool_path)
        if pool is None:
            raise PersonaProvisioningCoordinationError(
                f"CapitalPool {resolved_pool_id!r} was not found at the Capital owner"
            )
        pool_meta = dict(pool.get("metadata")) if isinstance(pool.get("metadata"), Mapping) else {}
        if not self._formal_tenant_matches(pool, tenant_id):
            raise PersonaProvisioningCoordinationError(
                "CapitalPool belongs to another tenant; refusing risk policy repair"
            )
        if (
            pool_meta.get("execution_context") != "paper"
            or pool.get("single_runtime_enforced") is not True
        ):
            raise PersonaProvisioningCoordinationError(
                "CapitalPool is not a single-runtime paper pool; refusing risk policy repair outside dev paper"
            )
        if pool_meta.get("persona_id") not in (None, "", persona_id):
            raise PersonaProvisioningCoordinationError(
                f"CapitalPool {resolved_pool_id!r} belongs to persona {pool_meta.get('persona_id')!r}, not {persona_id!r}"
            )

        latest_change = pool_meta.get("risk_profile_change")
        if isinstance(latest_change, Mapping):
            if (requested_profile not in (None, latest_change.get("requested_profile"))
                    or any(v is not None for v in (risk_policy_ref, risk_policy, risk_policy_version, approval_decision_id))):
                raise PersonaProvisioningCoordinationError("paper_risk_change_superseded; use normal Persona PATCH")
            if dry_run:
                change = self.read_paper_risk_change(tenant_id=tenant_id, persona_id=persona_id, pool_id=resolved_pool_id)
            else:
                rid = latest_change["request_id"]
                receipt = pool_meta["risk_profile_change_receipts"][rid]
                change = self.change_paper_risk_profile(
                    tenant_id=tenant_id, persona_id=persona_id, pool_id=resolved_pool_id,
                    profile=latest_change["requested_profile"], request_id=rid, request_hash=receipt["request_hash"],
                    requested_by=latest_change["requested_by"],
                    issued_at_ms=receipt["issued_at_ms"],
                )
            return {"tenant_id": tenant_id, "persona_id": persona_id, "capital_pool_id": resolved_pool_id,
                    "dry_run": dry_run, "riskPolicyChange": change, "after_resolution": change.get("resolution")}

        approval_renewal: dict[str, Any] | None = None
        force_redraft_reason: str | None = None
        if not dry_run and risk_policy is None and not risk_policy_ref and approval_decision_id is None:
            # An approved risk-officer draft that expired (or is about to) is renewed through the same gate
            # before anything else is proposed: the same policy, never a draft-less proposal.
            approval_renewal = self.renew_paper_risk_policy_approval(
                tenant_id=tenant_id, persona_id=persona_id, pool_id=resolved_pool_id)
            if approval_renewal["status"] == "renewed":
                pool = self._owner_get("capital", pool_path) or pool
                pool_meta = dict(pool.get("metadata")) if isinstance(pool.get("metadata"), Mapping) else {}
            elif approval_renewal["status"] == "redraft_requested":
                force_redraft_reason = str(approval_renewal.get("reason") or "approved policy no longer validates")

        pcb = self._owner_get("capital", f"/api/bindings/{_path_id(resolved_binding_id)}")
        if pcb is not None:
            self._check_binding_identity(
                pcb,
                persona_id=persona_id,
                pool_id=resolved_pool_id,
                binding_id=resolved_binding_id,
            )

        pool_profile = self._extract_request_risk_profile_context({"metadata": pool_meta})[0]
        ledger_profile = (
            self._record_risk_profile_context(ledger_record)[0]
            if ledger_record is not None else None
        )
        profile_sources = (
            ("repair_request", requested_profile),
            ("provisioning_request", ledger_profile),
            ("capital_pool", pool_profile),
        )
        effective_profile = (
            self._verified_requested_profile(*profile_sources)
            if any(value is not None and str(value).strip() for _, value in profile_sources)
            else None
        )
        caller_approval_id = str(approval_decision_id or "").strip() or None
        effective_approval_id = (
            str(
                caller_approval_id
                or pool_meta.get("profile_mapping_approval_id")
                or pool_meta.get("approval_decision_id")
                or (
                    ledger_record.request_payload.get("profile_mapping_approval_id")
                    or ledger_record.request_payload.get("approval_decision_id")
                    if ledger_record
                    else ""
                )
                or ""
            ).strip()
            or None
        )
        approval_reader = self._governance_approval_reader()
        pool_with_current_digest = self._pool_with_authoritative_approval_digest(
            pool,
            tenant_id=tenant_id,
        )
        before_resolution = resolve_pool_risk_policy_binding(
            capital_pool_id=resolved_pool_id,
            pool=pool_with_current_digest,
            expected_tenant_id=tenant_id,
            expected_stage="paper",
            requested_profile=requested_profile,
            require_approved_profile_mapping=True if requested_profile else None,
            approval_reader=approval_reader,
            require_active=True,
            require_limits=("gross_limit",),
            raise_on_error=False,
        )

        caller_clean_ref = (
            str(risk_policy_ref).strip()
            if risk_policy_ref is not None and str(risk_policy_ref).strip()
            else None
        )
        pool_existing_ref = (
            str(pool.get("risk_policy_ref")).strip()
            if pool.get("risk_policy_ref") is not None and str(pool.get("risk_policy_ref")).strip()
            else None
        )
        clean_ref = caller_clean_ref or pool_existing_ref
        explicit_repair_version = (
            str(risk_policy_version).strip()
            if risk_policy_version is not None and str(risk_policy_version).strip()
            else None
        )
        auto_discovered_policy: dict[str, Any] | None = None
        should_discover_profile = bool(
            require_approved_profile_mapping
            and risk_policy is None
            and not caller_clean_ref
            and (
                not pool_existing_ref
                or pool_meta.get("require_approved_profile_mapping") is True
                or requested_profile is not None
                or not before_resolution.resolved
            )
        )
        if should_discover_profile:
            effective_profile = self._verified_requested_profile(("verified_request", effective_profile))
            auto_res = discover_approved_profile_risk_policy(
                resolved_pool_id=resolved_pool_id,
                effective_tenant_id=tenant_id,
                clean_stage="paper",
                clean_profile=effective_profile,
                expected_version=explicit_repair_version,
                base_pool=pool,
                approval_decision_id=caller_approval_id,
                approval_reader=approval_reader,
                require_limits=("gross_limit",),
            )
            if auto_res.resolved and auto_res.policy is not None and auto_res.approval_decision_id is not None:
                auto_discovered_policy = (
                    deepcopy(dict(auto_res.raw_policy))
                    if isinstance(auto_res.raw_policy, Mapping)
                    else auto_res.policy.to_dict()
                )
                clean_ref = auto_res.risk_policy_ref
                effective_approval_id = auto_res.approval_decision_id
                caller_approval_id = auto_res.approval_decision_id
            elif auto_res.status == "ambiguous_profile_mapping":
                owner_decision_res = self._ambiguous_profile_resolution(
                    pool_id=resolved_pool_id,
                    tenant_id=tenant_id,
                    requested_profile=effective_profile,
                    auto_res=auto_res,
                )
                pool = self._persist_requested_profile(
                    pool=pool, tenant_id=tenant_id, persona_id=persona_id,
                    pool_id=resolved_pool_id, profile=effective_profile, dry_run=dry_run,
                )
                routed_req = self._route_or_reuse_risk_policy_owner_decision_request(
                    tenant_id=tenant_id,
                    persona_id=persona_id,
                    pool_id=resolved_pool_id,
                    requested_profile=effective_profile,
                    resolution=owner_decision_res,
                    dry_run=dry_run,
                )
                after_dict = owner_decision_res.to_dict()
                after_dict["owner_decision_request"] = routed_req
                return {
                    "tenant_id": tenant_id,
                    "persona_id": persona_id,
                    "capital_pool_id": resolved_pool_id,
                    "persona_capital_binding_id": resolved_binding_id,
                    "repaired": False,
                    "idempotent_noop": False,
                    "dry_run": dry_run,
                    "before_resolution": before_resolution.to_dict(),
                    "after_resolution": after_dict,
                    "owner_decision_request": routed_req,
                }
            elif not pool_existing_ref or not before_resolution.resolved or before_resolution.profile_mapping_status != "explicit_policy":
                repair_draft: DraftResult | None = None
                inline_policy = pool_meta.get("risk_policy") if isinstance(pool_meta.get("risk_policy"), Mapping) else None
                if force_redraft_reason is None and auto_res.candidate_policy is not None and not self._is_legacy_default_policy(
                    auto_res.candidate_policy.to_dict(), resolved_pool_id, str(effective_profile or "")
                ):
                    candidate_baseline = auto_res.candidate_policy.to_dict()
                elif force_redraft_reason is None and auto_res.candidate_policy is None and inline_policy is not None and validate_drafted_pool_risk_policy(
                    inline_policy, profile=str(effective_profile or "")
                )[0] is not None:
                    candidate_baseline = deepcopy(dict(inline_policy))
                else:
                    # Absent, or a legacy inline policy without single-name cap / cash reserve (the former
                    # hardcoded default): the next version is drafted by the risk officer, never invented here.
                    repair_draft = self._risk_officer_policy_candidate(
                        tenant_id=tenant_id,
                        persona_id=persona_id,
                        pool_id=resolved_pool_id,
                        profile=effective_profile,
                        budget=pool.get("budget"),
                        currency=str(pool.get("currency") or ""),
                        request=ledger_record.request_payload if ledger_record is not None else None,
                        base_pool=pool,
                        version=self._next_policy_version(
                            auto_res.candidate_policy.version if auto_res.candidate_policy is not None
                            else inline_policy.get("version") if inline_policy is not None else None,
                            explicit_repair_version,
                        ),
                    )
                    candidate_baseline = self._draft_candidate_policy_dict(repair_draft)
                if (
                    explicit_repair_version is not None
                    and str(candidate_baseline.get("risk_policy_id") or "").startswith(
                        ("rp-pool-", "paper-inline-policy:")
                    )
                ):
                    candidate_baseline["version"] = explicit_repair_version
                candidate_owner_meta = {
                    **pool_meta,
                    "profile_auto_resolved": True,
                    "risk_policy": candidate_baseline,
                }
                if effective_approval_id is not None:
                    candidate_owner_meta["profile_mapping_approval_id"] = effective_approval_id
                candidate_owner_pool = self._candidate_pool_with_approval_digest(
                    base_pool=pool,
                    pool_id=resolved_pool_id,
                    tenant_id=tenant_id,
                    risk_policy_ref=None,
                    metadata=candidate_owner_meta,
                )
                owner_decision_res = resolve_pool_risk_policy_binding(
                    capital_pool_id=resolved_pool_id,
                    pool=candidate_owner_pool,
                    expected_tenant_id=tenant_id,
                    expected_stage="paper",
                    expected_version=explicit_repair_version,
                    requested_profile=effective_profile,
                    require_approved_profile_mapping=True,
                    approval_decision_id=caller_approval_id,
                    approval_reader=approval_reader,
                    require_active=True,
                    require_limits=("gross_limit",),
                    raise_on_error=False,
                )
                if owner_decision_res.status in {"unresolved_profile_mapping", "ambiguous_profile_mapping"}:
                    pool = self._persist_requested_profile(
                        pool=pool, tenant_id=tenant_id, persona_id=persona_id,
                        pool_id=resolved_pool_id, profile=effective_profile, dry_run=dry_run,
                    )
                    routed_req = self._route_or_reuse_risk_policy_owner_decision_request(
                        tenant_id=tenant_id,
                        persona_id=persona_id,
                        pool_id=resolved_pool_id,
                        requested_profile=effective_profile,
                        resolution=owner_decision_res,
                        dry_run=dry_run,
                        draft=repair_draft,
                        baseline_policy=(
                            before_resolution.policy.to_dict() if before_resolution.policy is not None
                            else dict(inline_policy) if inline_policy is not None
                            else auto_res.candidate_policy.to_dict() if auto_res.candidate_policy is not None
                            else None
                        ),
                    )
                    after_dict = owner_decision_res.to_dict()
                    after_dict["owner_decision_request"] = routed_req
                    if force_redraft_reason is not None:
                        routed_req["risk_officer_redraft_requested"] = force_redraft_reason
                    return {
                        "tenant_id": tenant_id,
                        "persona_id": persona_id,
                        "capital_pool_id": resolved_pool_id,
                        "persona_capital_binding_id": resolved_binding_id,
                        "approval_renewal": approval_renewal,
                        "repaired": False,
                        "idempotent_noop": False,
                        "dry_run": dry_run,
                        "before_resolution": before_resolution.to_dict(),
                        "after_resolution": after_dict,
                        "owner_decision_request": routed_req,
                    }

        has_existing_inline_or_markers = bool(
            ("risk_policy" in pool_meta and pool_meta.get("risk_policy") is not None)
            or pool.get("risk_policy_ref")
            or any(
                pool_meta.get(k) not in (None, False, "")
                for k in (
                    "risk_policy_stale",
                    "risk_policy_superseded_by",
                    "risk_policy_expires_at",
                    "risk_policy_effective_from",
                    "expected_risk_policy_version",
                    "expected_risk_policy_digest",
                )
            )
        )

        target_policy: dict[str, Any]
        has_authoritative_replacement = False
        if risk_policy is not None:
            if not isinstance(risk_policy, Mapping):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool risk policy repair rejected: risk_policy must be an object (risk_policy_invalid)"
                )
            target_policy = deepcopy(dict(risk_policy))
            has_authoritative_replacement = True
        elif auto_discovered_policy is not None:
            target_policy = deepcopy(auto_discovered_policy)
            has_authoritative_replacement = True
        elif clean_ref is not None:
            ref_resolution = resolve_pool_risk_policy_binding(
                capital_pool_id=resolved_pool_id,
                pool={
                    "pool_id": resolved_pool_id,
                    "tenant_id": tenant_id,
                    "risk_policy_ref": clean_ref,
                    "metadata": {"tenant_id": tenant_id},
                },
                expected_tenant_id=tenant_id,
                expected_stage="paper",
                expected_version=risk_policy_version,
                requested_profile=effective_profile,
                require_approved_profile_mapping=False,
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
            if not ref_resolution.resolved or ref_resolution.policy is None:
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool risk policy repair rejected: {ref_resolution.reason} ({ref_resolution.reason_code})"
                )
            target_policy = ref_resolution.policy.to_dict()
            has_authoritative_replacement = bool(
                (risk_policy_ref is not None and str(risk_policy_ref).strip())
                or isinstance(pool_meta.get("risk_policy"), Mapping)
            )
        elif (
            (
                (before_resolution.resolved and before_resolution.policy is not None)
                or before_resolution.status == "unresolved_profile_mapping"
            )
            and isinstance(pool_meta.get("risk_policy"), Mapping)
        ):
            target_policy = deepcopy(dict(pool_meta["risk_policy"]))
            if (
                risk_policy_version is not None
                and str(risk_policy_version).strip()
                and str(risk_policy_version).strip() != str(target_policy.get("version") or "")
            ):
                is_default_baseline = str(
                    before_resolution.risk_policy_id or target_policy.get("risk_policy_id") or ""
                ).startswith(
                    ("rp-pool-", "paper-inline-policy:")
                )
                if not is_default_baseline or "expected_risk_policy_digest" in pool_meta:
                    raise PersonaProvisioningCoordinationError(
                        "CapitalPool risk policy repair rejected: version change on explicit or digest-pinned "
                        "policy requires an authoritative replacement policy (risk_policy_stale)"
                    )
                target_policy["version"] = str(risk_policy_version).strip()
        elif before_resolution.status == "missing" and not has_existing_inline_or_markers:
            raise PersonaProvisioningCoordinationError(
                "CapitalPool risk policy repair rejected: no policy exists and none may be invented; a risk "
                "profile mapping drafted by the risk officer and approved by Governance is required "
                "(risk_policy_profile_required)",
                reason_code="risk_policy_profile_required",
                resolution_status="missing",
            )
        else:
            raise PersonaProvisioningCoordinationError(
                f"CapitalPool risk policy repair rejected: cannot replace or clear {before_resolution.status} "
                f"policy without an authoritative approved replacement ({before_resolution.reason_code}): "
                f"{before_resolution.reason}"
            )

        is_explicit_caller_replacement = bool(
            (risk_policy is not None or caller_clean_ref is not None)
            and auto_discovered_policy is None
            and caller_approval_id is None
        )
        stripped_replacement_keys = {
            "risk_policy_stale",
            "risk_policy_superseded_by",
            "risk_policy_expires_at",
            "risk_policy_effective_from",
            "expected_risk_policy_digest",
            *(
                {
                    "require_approved_profile_mapping",
                    "profile_mapping_approval_id",
                    "approval_decision_id",
                    "decision_id",
                    "profile_mapping_approval",
                    "profile_auto_resolved",
                }
                if is_explicit_caller_replacement
                else set()
            ),
        }
        validation_meta = (
            {
                **{
                    k: v
                    for k, v in pool_meta.items()
                    if k not in stripped_replacement_keys
                },
                "risk_policy": target_policy,
            }
            if has_authoritative_replacement
            else {
                **pool_meta,
                "risk_policy": target_policy,
            }
        )
        if effective_approval_id is not None and not is_explicit_caller_replacement:
            validation_meta["profile_mapping_approval_id"] = effective_approval_id
        if has_authoritative_replacement and risk_policy_version is not None and str(risk_policy_version).strip():
            validation_meta["expected_risk_policy_version"] = str(risk_policy_version).strip()
        elif "expected_risk_policy_version" in validation_meta:
            validation_meta["expected_risk_policy_version"] = str(target_policy.get("version") or "1.0")

        candidate_target_pool = self._candidate_pool_with_approval_digest(
            base_pool=pool,
            pool_id=resolved_pool_id,
            tenant_id=tenant_id,
            risk_policy_ref=clean_ref,
            metadata=validation_meta,
        )
        target_validation = resolve_pool_risk_policy_binding(
            capital_pool_id=resolved_pool_id,
            pool=candidate_target_pool,
            expected_tenant_id=tenant_id,
            expected_stage="paper",
            requested_profile=effective_profile,
            require_approved_profile_mapping=require_approved_profile_mapping,
            approval_decision_id=None if is_explicit_caller_replacement else effective_approval_id,
            approval_reader=approval_reader,
            require_active=True,
            require_limits=("gross_limit",),
            raise_on_error=False,
        )
        if not target_validation.resolved:
            raise PersonaProvisioningCoordinationError(
                f"CapitalPool risk policy repair rejected: {target_validation.reason} ({target_validation.reason_code})"
            )

        persist_require_mapping = bool(
            require_approved_profile_mapping
            and (
                target_validation.profile_mapping_approval_id is not None
                or target_validation.profile_mapping_status != "explicit_policy"
            )
        )
        already_converged = (
            before_resolution.resolved
            and before_resolution.risk_policy_id == target_validation.risk_policy_id
            and before_resolution.risk_policy_version == target_validation.risk_policy_version
            and before_resolution.risk_policy_digest == target_validation.risk_policy_digest
            and (clean_ref is None or pool.get("risk_policy_ref") == clean_ref)
            and pool_meta.get("requested_risk_profile") == effective_profile
            and bool(pool_meta.get("require_approved_profile_mapping")) == persist_require_mapping
            and (
                target_validation.profile_mapping_approval_id is None
                or pool_meta.get("profile_mapping_approval_id") == target_validation.profile_mapping_approval_id
            )
        )
        repaired = False
        updated_pool = pool_with_current_digest
        if not already_converged and not dry_run:
            cleaned_base_meta = (
                {
                    k: v
                    for k, v in pool_meta.items()
                    if k
                    not in {
                        "profile_mapping_approval_id",
                        "approval_decision_id",
                        "decision_id",
                        "profile_mapping_approval",
                        "profile_auto_resolved",
                    }
                }
                if target_validation.profile_mapping_approval_id is None
                else dict(pool_meta)
            )
            updated_meta = {
                **cleaned_base_meta,
                "tenant_id": tenant_id,
                "persona_id": persona_id,
                "execution_context": "paper",
                "requested_risk_profile": effective_profile,
                "require_approved_profile_mapping": persist_require_mapping,
                "risk_policy": deepcopy(target_policy),
                "risk_policy_repaired_by": self.actor_id,
                "risk_policy_requested_by": requested_by,
                **(
                    {"profile_mapping_approval_id": target_validation.profile_mapping_approval_id}
                    if target_validation.profile_mapping_approval_id is not None
                    else {}
                ),
            }
            if has_authoritative_replacement:
                updated_meta["risk_policy_stale"] = False
                updated_meta["risk_policy_superseded_by"] = None
                updated_meta["risk_policy_expires_at"] = None
                updated_meta["risk_policy_effective_from"] = None
            if "expected_risk_policy_version" in updated_meta:
                updated_meta["expected_risk_policy_version"] = str(target_policy.get("version") or "1.0")
            if has_authoritative_replacement and "expected_risk_policy_digest" in updated_meta:
                updated_meta["expected_risk_policy_digest"] = target_validation.risk_policy_digest
            self.transport.patch(
                "capital",
                f"{pool_path}/status",
                {
                    "actor_id": self.actor_id,
                    "actor_role": "capital.admin",
                    "status": str(pool.get("status") or "active"),
                    "risk_policy_ref": clean_ref,
                    "metadata": updated_meta,
                },
            )
            re_read = self._owner_get("capital", pool_path)
            if re_read is None:
                raise PersonaProvisioningCoordinationError(
                    f"CapitalPool {resolved_pool_id!r} risk policy repair has no authoritative readback"
                )
            updated_pool = self._pool_with_authoritative_approval_digest(
                re_read,
                tenant_id=tenant_id,
            )
            repaired = True

        after_resolution = (
            target_validation
            if (dry_run and not already_converged)
            else resolve_pool_risk_policy_binding(
                capital_pool_id=resolved_pool_id,
                pool=updated_pool,
                expected_tenant_id=tenant_id,
                expected_stage="paper",
                requested_profile=effective_profile,
                require_approved_profile_mapping=require_approved_profile_mapping,
                approval_decision_id=effective_approval_id,
                approval_reader=approval_reader,
                require_active=True,
                require_limits=("gross_limit",),
                raise_on_error=False,
            )
        )
        if not after_resolution.resolved:
            raise PersonaProvisioningCoordinationError(
                f"CapitalPool {resolved_pool_id!r} post-repair readback failed policy resolution: "
                f"{after_resolution.reason} ({after_resolution.reason_code})"
            )

        active_runtime_binding: dict[str, Any] | None = None
        if self.runtime_binding_reader is not None:
            live_rbs = [
                dict(b)
                for b in (self.runtime_binding_reader("", resolved_pool_id) or [])
                if isinstance(b, Mapping)
                and str(b.get("status") or "") not in TERMINAL_RUNTIME_BINDING_STATUSES
                and str(b.get("capital_pool_id") or "") == resolved_pool_id
            ]
            if live_rbs:
                active_runtime_binding = live_rbs[-1]

        return {
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "capital_pool_id": resolved_pool_id,
            "persona_capital_binding_id": resolved_binding_id,
            "approval_renewal": approval_renewal,
            "repaired": repaired,
            "idempotent_noop": already_converged,
            "dry_run": dry_run,
            "capital_pool": updated_pool,
            "persona_capital_binding": pcb,
            "active_runtime_binding": active_runtime_binding,
            "before_resolution": before_resolution.to_dict(),
            "after_resolution": after_resolution.to_dict(),
            "provenance": {
                "policy_owner": "services/capital/risk_policy.py",
                "governance_surface": "services/control-plane/governance/risk_policy.py",
                "requested_profile": effective_profile,
                "requested_profile_translation": (
                    "Persona create request_payload['risk'] translates to StrategyArtifact "
                    "expression_policy.risk_profile (execution_style), while CapitalPool effective "
                    "limits (gross_limit, net_limit, max_leverage, max_single_name_weight) and "
                    "policy version are resolved from CapitalPool.metadata.risk_policy or "
                    "CapitalPool.risk_policy_ref via resolve_bound_pool_risk_policy."
                ),
                "approval_and_renewal_posture": (
                    "ApprovalDecision target_type='registry_entry' expires_at is the 24h candidate "
                    "admission window consumed when advance_artifact_state transitions the candidate "
                    "to approved; ongoing paper pool trading, rebinding, and pool-envelope renewal "
                    "are governed by active CapitalPool status, active non-stale RiskPolicy, and "
                    "PersonaCapitalBinding effective_from/effective_to."
                ),
            },
        }

    def _record_failure(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        failed_step: str,
        error: Exception,
    ) -> ProvisioningRecord:
        err_dict: dict[str, Any] = {
            "failed_step": failed_step,
            "terminal_reason": str(error) or error.__class__.__name__,
            "error_type": error.__class__.__name__,
            "failed_at": utc_now(),
        }
        reason_code = getattr(error, "reason_code", None)
        if reason_code:
            err_dict["reason_code"] = str(reason_code)
        absence_source = getattr(error, "absence_source", None)
        if absence_source:
            err_dict["absence_source"] = str(absence_source)
        resolution_status = getattr(error, "resolution_status", None)
        if resolution_status:
            err_dict["resolution_status"] = str(resolution_status)
        owner_decision_req = getattr(error, "owner_decision_request", None)
        if isinstance(owner_decision_req, Mapping):
            err_dict["owner_decision_request"] = deepcopy(dict(owner_decision_req))
            record.references["risk_policy_owner_decision_request"] = deepcopy(dict(owner_decision_req))
        record.error = err_dict
        record.result = None
        record.state = "failed"
        record.current_step = f"{failed_step}_failed"
        record = self._checkpoint(record)
        return self._compensate_failure(record, ids, failed_step=failed_step)

    def _request_deployment_compensation(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        failed_step: str,
        require_saga: bool,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Hand failure to Deployment and prove the committed saga state."""

        saga_path = f"/api/deployment/sagas/{_path_id(ids.deployment_saga_id)}"
        saga = self._owner_get("deployment", saga_path)
        if saga is None:
            if require_saga:
                raise PersonaProvisioningCoordinationError(
                    "checkpointed deployment dispatch has no authoritative saga readback"
                )
            return None, True

        def validate_identity(value: Mapping[str, Any]) -> None:
            if (
                value.get("saga_id") != ids.deployment_saga_id
                or value.get("plan_id") != ids.deployment_plan_id
            ):
                raise PersonaProvisioningCoordinationError(
                    "deployment compensation readback has the wrong saga identity"
                )

        validate_identity(saga)
        status = str(saga.get("status") or "")
        # Deployment only retires completed or failed+compensated sagas, so a
        # retired saga proves the Deployment side is already torn down; a
        # retiring one is still in flight. Neither accepts /failure.
        if status == "retired":
            return saga, True
        if status == "retiring":
            return saga, False
        mutation_error: Exception | None = None
        if status not in {"compensating", "failed", "aborted"}:
            try:
                self.transport.post(
                    "deployment",
                    f"{saga_path}/failure",
                    {
                        "reason": (
                            f"Persona provisioning {failed_step} failed: "
                            f"{(record.error or {}).get('terminal_reason') or 'unknown error'}"
                        )
                    },
                )
            except Exception as exc:  # response loss is reconciled by GET
                mutation_error = exc
            saga = self._owner_get("deployment", saga_path)
            if saga is None:
                detail = f": {mutation_error}" if mutation_error else ""
                raise PersonaProvisioningCoordinationError(
                    f"deployment compensation request has no saga readback{detail}"
                )
            validate_identity(saga)
            status = str(saga.get("status") or "")

        decision = saga.get("compensation")
        if not isinstance(decision, Mapping):
            detail = f" after mutation error: {mutation_error}" if mutation_error else ""
            raise PersonaProvisioningCoordinationError(
                f"deployment compensation readback has no durable decision{detail}"
            )
        current_step = str(saga.get("current_step") or "")
        if status == "compensating" and current_step == "compensation_requested":
            return saga, False
        if status in {"failed", "aborted"} and current_step == "compensated":
            return saga, True
        raise PersonaProvisioningCoordinationError(
            "deployment compensation did not reach a recognized authoritative state "
            f"(status={status!r}, current_step={current_step!r})"
        )

    def _compensate_failure(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        failed_step: str,
    ) -> ProvisioningRecord:
        """Reconcile Deployment handoff and fail-close the capital binding."""

        deployment_receipt: dict[str, Any] | None = None
        deployment_complete = True
        deployment_error: Exception | None = None
        may_have_dispatched = (
            "deployment_dispatch" in record.references
            or failed_step in {"deployment_dispatch", "schedule_registration"}
        )
        if may_have_dispatched:
            try:
                deployment_receipt, deployment_complete = (
                    self._request_deployment_compensation(
                        record,
                        ids,
                        failed_step=failed_step,
                        require_saga=(
                            "deployment_dispatch" in record.references
                            or failed_step == "schedule_registration"
                        ),
                    )
                )
                if deployment_receipt is not None:
                    record.references["deployment_compensation_readback"] = deepcopy(
                        deployment_receipt
                    )
                    record.compensation = {
                        "status": "in_progress" if deployment_complete else "pending",
                        "deployment": {
                            "saga_id": ids.deployment_saga_id,
                            "status": (
                                "completed" if deployment_complete else "requested"
                            ),
                            "receipt": deepcopy(deployment_receipt),
                        },
                    }
                    record.current_step = (
                        "deployment_compensation_terminal_readback"
                        if deployment_complete
                        else "deployment_compensation_requested_readback"
                    )
                    record = self._checkpoint(record)
            except Exception as exc:  # noqa: BLE001 - fail closed below
                deployment_complete = False
                deployment_error = exc

        binding_path = f"/api/bindings/{_path_id(ids.persona_capital_binding_id)}"
        attempted_action = "inspect_persona_capital_binding_for_fail_closed_compensation"
        attempted_target_status: str | None = None
        binding: dict[str, Any] | None = None
        binding_error: Exception | None = None
        binding_complete = False
        action: str | None = None
        target_status: str | None = None
        try:
            binding = self._owner_get("capital", binding_path)
            if binding is not None:
                # Never compensate an ID collision or receipt belonging to a
                # different tenant/persona.  Identity is proven before mutation.
                self._validate_binding_identity(binding, record, ids)
                current_status = str(binding.get("status") or "")
                if current_status == "active":
                    target_status = "suspended"
                    action = "suspend_persona_capital_binding"
                elif current_status == "pending":
                    target_status = "revoked"
                    action = "revoke_pending_persona_capital_binding"
                elif current_status in {"suspended", "revoked", "expired"}:
                    target_status = current_status
                    action = "persona_capital_binding_already_fail_closed"
                else:
                    raise PersonaProvisioningCoordinationError(
                        f"binding compensation cannot classify status {current_status!r}"
                    )
                attempted_action = action
                attempted_target_status = target_status
                if current_status != target_status:
                    mutation_error: Exception | None = None
                    try:
                        self.transport.patch(
                            "capital",
                            f"{binding_path}/status",
                            {
                                "actor_id": self.actor_id,
                                "actor_role": "persona.admin",
                                "status": target_status,
                            },
                        )
                    except Exception as exc:  # response loss is reconciled by GET
                        mutation_error = exc
                    binding = self._owner_get("capital", binding_path)
                    if binding is None or binding.get("status") != target_status:
                        detail = f": {mutation_error}" if mutation_error else ""
                        raise PersonaProvisioningCoordinationError(
                            "binding fail-closed transition has no authoritative receipt"
                            f"{detail}"
                        )
                self._validate_binding_identity(binding, record, ids)
                record.references[f"persona_capital_binding_{target_status}"] = deepcopy(
                    binding
                )
                binding_complete = True
        except Exception as exc:  # noqa: BLE001 - aggregate both owner failures
            binding_error = exc

        # A genuinely early failure has no binding or deployment side effect.
        # Leave it as a retryable failed record rather than claiming compensation.
        if binding is None and not may_have_dispatched and binding_error is None:
            record.state = "failed"
            record.current_step = f"{failed_step}_failed"
            record.compensation = None
            return self._checkpoint(record)
        if binding is None and binding_error is None:
            binding_error = PersonaProvisioningCoordinationError(
                "unsafe provisioning failure has no authoritative PersonaCapitalBinding "
                "readback to prove fail-closed state"
            )

        deployment_summary: dict[str, Any] | None = None
        if deployment_receipt is not None:
            deployment_summary = {
                "saga_id": ids.deployment_saga_id,
                "status": "completed" if deployment_complete else "requested",
                "receipt": deepcopy(deployment_receipt),
            }
        elif deployment_error is not None:
            deployment_summary = {
                "saga_id": ids.deployment_saga_id,
                "status": "failed",
                "terminal_reason": str(deployment_error)
                or deployment_error.__class__.__name__,
            }

        record.error = record.error or {"failed_step": failed_step}
        record.error.pop("compensation_error", None)
        if binding_complete and deployment_complete and deployment_error is None:
            record.state = "compensated"
            record.current_step = (
                "deployment_and_binding_compensation_readback"
                if deployment_receipt is not None
                else f"binding_{target_status}_compensation_readback"
            )
            record.compensation = {
                "status": "completed",
                "action": action,
                "binding_id": ids.persona_capital_binding_id,
                "resulting_status": target_status,
                "receipt": deepcopy(binding),
                "deployment": deployment_summary,
                "compensated_at": utc_now(),
            }
        else:
            errors = [
                str(value) or value.__class__.__name__
                for value in (deployment_error, binding_error)
                if value is not None
            ]
            pending = (
                binding_complete
                and deployment_receipt is not None
                and not deployment_complete
                and not errors
            )
            record.state = "failed"
            record.current_step = (
                "deployment_compensation_requested_readback"
                if pending
                else "compensation_failed"
            )
            record.compensation = {
                "status": "pending" if pending else "failed",
                "action": action or attempted_action,
                "binding_id": ids.persona_capital_binding_id,
                "target_status": target_status or attempted_target_status,
                "resulting_status": target_status if binding_complete else None,
                "receipt": deepcopy(binding) if binding_complete else None,
                "deployment": deployment_summary,
                (
                    "requested_at" if pending else "failed_at"
                ): utc_now(),
            }
            if errors:
                record.compensation["terminal_reason"] = "; ".join(errors)
                record.error["compensation_error"] = record.compensation[
                    "terminal_reason"
                ]
        return self._checkpoint(record)
