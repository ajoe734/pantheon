"""Restart-safe coordinator for admitting a newly-created Persona to paper.

The BFF owns only the coordination ledger.  Capital, Registry, Governance,
Deployment, and Cron remain the authoritative writers for their objects.  In
particular, this module never creates or guesses a RuntimeBinding: dispatching
a Deployment saga means only that provisioning was admitted.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from persona_provisioning import (
    TERMINAL_STATES,
    PersonaProvisioningStore,
    ProvisioningRecord,
    utc_now,
)


FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"


class OwnerTransport(Protocol):
    """Synchronous transport over authoritative owner HTTP APIs.

    ``get`` returns ``None`` for HTTP 404.  Other transport or owner failures
    are raised.  Mutation responses are deliberately not trusted: every
    mutation below is followed by an authoritative GET readback.
    """

    def get(self, owner: str, path: str) -> Mapping[str, Any] | None: ...

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


class PersonaProvisioningCoordinationError(RuntimeError):
    """An owner receipt could not prove the requested provisioning step."""


@dataclass(frozen=True)
class ProvisioningIds:
    """Deterministic owner identities for one tenant-scoped Persona."""

    token: str
    capital_pool_id: str
    strategy_id: str
    baseline_registry_id: str
    baseline_approval_decision_id: str
    registry_id: str
    approval_decision_id: str
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
        registry_id=f"reg-strategy-spec-persona-{token}",
        approval_decision_id=f"apv-persona-paper-{token}",
        persona_capital_binding_id=f"pcb-persona-paper-{token}",
        deployment_plan_id=plan_id,
        deployment_saga_id=f"deployment-saga-{plan_id}",
    )


def _path_id(value: str) -> str:
    return quote(value, safe="")


def _stable_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PersonaProvisioningCoordinationError(f"{label} did not return an object")
    return deepcopy(dict(value))


def _registry_entry(receipt: Mapping[str, Any]) -> dict[str, Any]:
    wrapped = receipt.get("entry")
    value = wrapped if isinstance(wrapped, Mapping) else receipt
    return _mapping(value, label="Registry readback")


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
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("lease_owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.transport = transport
        self.schedule_registrar = schedule_registrar
        self.lease_owner = lease_owner
        self.lease_seconds = lease_seconds
        self.actor_id = actor_id

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
        if existing.state in TERMINAL_STATES:
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

        failed_step = "capital_pool"
        ids = deterministic_provisioning_ids(active)
        try:
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
                "rollback_registry_id": ids.baseline_registry_id,
                "rollback_approval_decision_id": ids.baseline_approval_decision_id,
                "registry_id": ids.registry_id,
                "approval_decision_id": ids.approval_decision_id,
                "deployment_plan_id": ids.deployment_plan_id,
                "deployment_saga_id": ids.deployment_saga_id,
                "first_evaluation_workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
            }
            active = self.store.checkpoint(active, lease_owner=self.lease_owner)
        except Exception as exc:  # noqa: BLE001 - downstream boundaries are injected
            active = self._record_failure(active, ids, failed_step=failed_step, error=exc)
        return self.store.release(active, lease_owner=self.lease_owner)

    def _preview(self, record: ProvisioningRecord) -> ProvisioningRecord:
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
                "strategy_spec_candidate",
                "approval_proposed",
                "approval_reviewed",
                "approval_decided",
                "strategy_spec_approved",
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
        return self.store.checkpoint(record, lease_owner=self.lease_owner)

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
        payload: Mapping[str, Any],
        ready: Callable[[Mapping[str, Any]], bool],
        validate: Callable[[Mapping[str, Any]], None],
    ) -> dict[str, Any]:
        receipt = self._owner_get(owner, get_path)
        mutation_error: Exception | None = None
        if receipt is None or not ready(receipt):
            try:
                self.transport.post(owner, post_path, deepcopy(dict(payload)))
            except Exception as exc:  # response loss is reconciled by authoritative GET
                mutation_error = exc
            receipt = self._owner_get(owner, get_path)
        if receipt is None:
            detail = f" after mutation error: {mutation_error}" if mutation_error else ""
            raise PersonaProvisioningCoordinationError(
                f"{owner} transition has no readback at {get_path}{detail}"
            )
        try:
            validate(receipt)
        except Exception as exc:
            if mutation_error is not None:
                raise PersonaProvisioningCoordinationError(
                    f"{owner} transition failed ({mutation_error}) and readback "
                    f"was not ready: {exc}"
                ) from exc
            raise
        return receipt

    def _coordinate_capital_pool(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
        base_payload: dict[str, Any] = {
            "actor_id": self.actor_id,
            "actor_role": "admin",
            "pool_id": ids.capital_pool_id,
            "name": f"{record.request_payload.get('name') or record.normalized_name} paper pool",
            "owner_id": record.tenant_id,
            "owner_type": "org",
            "status": "active",
            "currency": str(record.request_payload.get("currency") or "USD"),
            "budget": record.request_payload.get("budget"),
            "risk_policy_ref": record.request_payload.get("risk_policy_ref"),
            "single_runtime_enforced": True,
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "tenant_id": record.tenant_id,
                "persona_id": record.persona_id,
                "requested_by": self._requested_by(record),
                "provisioning_request_hash": record.request_hash,
            },
        }
        payload = {
            **base_payload,
            "idempotency_key": f"{record.idempotency_key}:capital-pool",
            "request_hash": _stable_hash(base_payload),
        }

        def validate(receipt: Mapping[str, Any]) -> None:
            if (
                receipt.get("pool_id") != ids.capital_pool_id
                or receipt.get("owner_id") != record.tenant_id
                or receipt.get("owner_type") != "org"
                or receipt.get("status") != "active"
                or receipt.get("single_runtime_enforced") is not True
            ):
                raise PersonaProvisioningCoordinationError(
                    "CapitalPool readback does not match the internal paper pool contract"
                )

        receipt = self._create_then_get(
            owner="capital",
            get_path=f"/api/capital-pools/{_path_id(ids.capital_pool_id)}",
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

    def _strategy_spec_payload(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> dict[str, Any]:
        registry_id, _, version, label = self._artifact_values(ids, baseline=baseline)
        strategy_spec = {
            "strategy_id": ids.strategy_id,
            "version": version,
            "name": (
                f"{record.request_payload.get('name') or record.normalized_name} "
                "zero-capital baseline"
                if baseline
                else str(record.request_payload.get("name") or record.normalized_name)
            ),
            "persona_id": record.persona_id,
            "tenant_id": record.tenant_id,
            "mandate": (
                "Fail-closed paper baseline: allocate zero capital and emit no orders"
                if baseline
                else record.request_payload.get("mandate")
            ),
            "traits": (
                {"hard_rules": ["zero_capital", "no_orders", "no_positions"]}
                if baseline
                else deepcopy(record.request_payload.get("traits") or {})
            ),
            "execution_context": "paper",
            "capital_pool_id": ids.capital_pool_id,
            "capital_scale_pct": 0.0 if baseline else None,
            "fail_closed_baseline": baseline,
        }
        source_run_id = f"persona-provisioning-{label}-{ids.token}"
        payload = {
            "registry_id": registry_id,
            "strategy_id": ids.strategy_id,
            "version": version,
            "artifact_state": "candidate",
            "source_seed_id": source_run_id,
            "lineage": {"source_run_ids": [source_run_id]},
            "producer_run_id": source_run_id,
            "evaluation_summary": {
                "admission": (
                    "persona_paper_zero_capital_baseline"
                    if baseline
                    else "persona_paper_bootstrap"
                ),
                "risk_level": "low",
                "capital_scale_pct": 0.0 if baseline else None,
            },
            "metadata": {
                "tenant_id": record.tenant_id,
                "persona_id": record.persona_id,
                "capital_pool_id": ids.capital_pool_id,
                "requested_by": self._requested_by(record),
                "fail_closed_baseline": baseline,
                "rollback_target_registry_id": (
                    None if baseline else ids.baseline_registry_id
                ),
                "provisioning_request_hash": record.request_hash,
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

    def _validate_registry(
        self,
        receipt: Mapping[str, Any],
        ids: ProvisioningIds,
        *,
        state: str,
        registry_id: str | None = None,
        version: str | None = None,
        approval_decision_id: str | None = None,
    ) -> None:
        entry = _registry_entry(receipt)
        expected_registry_id = registry_id or ids.registry_id
        expected_version = version or ids.version
        if (
            entry.get("registry_id") != expected_registry_id
            or entry.get("artifact_type") != "strategy_spec"
            or entry.get("strategy_id") != ids.strategy_id
            or entry.get("version") != expected_version
            or entry.get("artifact_state") != state
        ):
            raise PersonaProvisioningCoordinationError(
                f"Registry readback does not match the stable StrategySpec in state {state}"
            )
        if approval_decision_id and entry.get("approval_decision_id") != approval_decision_id:
            raise PersonaProvisioningCoordinationError(
                "Approved RegistryEntry does not cite the canonical ApprovalDecision"
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

    def _validate_approval_identity(
        self,
        receipt: Mapping[str, Any],
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        registry_id: str,
        decision_id: str,
        version: str,
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

    def _coordinate_approval_proposal(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> ProvisioningRecord:
        registry_id, decision_id, version, label = self._artifact_values(
            ids,
            baseline=baseline,
        )
        checkpoint_prefix = "baseline_" if baseline else ""
        payload = {
            "decision_id": decision_id,
            "target_type": "registry_entry",
            "target_id": registry_id,
            "target_version": version,
            "risk_level": "low",
            "capital_pool_id": ids.capital_pool_id,
            "persona_id": record.persona_id,
            "tenant_id": record.tenant_id,
            "owner_user_id": self._requested_by(record) or self.actor_id,
            "proposal_id": f"persona-provisioning-{label}-{ids.token}",
            "proposal_revision": 1,
            "proposal_content_digest": record.request_hash,
            "validation_result_digest": _stable_hash(
                {"registry_id": registry_id, "risk_level": "low"}
            ),
        }

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_approval_identity(
                receipt,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
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
            step=f"{checkpoint_prefix}approval_proposed_readback",
            key=f"{checkpoint_prefix}approval_proposed",
            receipt=receipt,
        )

    def _coordinate_approval_review(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> ProvisioningRecord:
        registry_id, decision_id, version, _ = self._artifact_values(
            ids,
            baseline=baseline,
        )
        checkpoint_prefix = "baseline_" if baseline else ""
        get_path = f"/api/governance/approvals/{_path_id(decision_id)}"

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
            )
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision was not accepted for review"
                )

        receipt = self._transition_then_get(
            owner="governance",
            get_path=get_path,
            post_path=f"{get_path}/review",
            payload={"actor_role": "automated_gate", "actor_id": self.actor_id},
            ready=ready,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=f"{checkpoint_prefix}approval_reviewed_readback",
            key=f"{checkpoint_prefix}approval_reviewed",
            receipt=receipt,
        )

    def _coordinate_approval_decision(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> ProvisioningRecord:
        registry_id, decision_id, version, _ = self._artifact_values(
            ids,
            baseline=baseline,
        )
        checkpoint_prefix = "baseline_" if baseline else ""
        get_path = f"/api/governance/approvals/{_path_id(decision_id)}"

        def ready(receipt: Mapping[str, Any]) -> bool:
            return (
                receipt.get("decision_state") == "decided"
                and receipt.get("decision") == "approved"
            )

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_approval_identity(
                receipt,
                record,
                ids,
                registry_id=registry_id,
                decision_id=decision_id,
                version=version,
            )
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "ApprovalDecision readback is not decided/approved"
                )

        receipt = self._transition_then_get(
            owner="governance",
            get_path=get_path,
            post_path=f"{get_path}/decide",
            payload={
                "actor_role": "automated_gate",
                "actor_id": self.actor_id,
                "outcome": "approved",
                "rationale": "Low-risk internal paper Persona admission",
                "evidence_refs": [
                    {"ref_type": "registry_entry", "ref_id": registry_id}
                ],
            },
            ready=ready,
            validate=validate,
        )
        return self._checkpoint_receipt(
            record,
            step=f"{checkpoint_prefix}approval_decided_readback",
            key=f"{checkpoint_prefix}approval_decided",
            receipt=receipt,
        )

    def _coordinate_registry_approval(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        baseline: bool = False,
    ) -> ProvisioningRecord:
        registry_id, decision_id, version, _ = self._artifact_values(
            ids,
            baseline=baseline,
        )
        checkpoint_prefix = "baseline_" if baseline else ""
        get_path = f"/api/registry/strategy-specs/{_path_id(registry_id)}"

        def ready(receipt: Mapping[str, Any]) -> bool:
            entry = _registry_entry(receipt)
            return (
                entry.get("artifact_state") == "approved"
                and entry.get("approval_decision_id") == decision_id
            )

        receipt = self._transition_then_get(
            owner="registry",
            get_path=get_path,
            post_path=f"{get_path}/advance",
            payload={
                "target_state": "approved",
                "approver": self.actor_id,
                "approval_decision_id": decision_id,
            },
            ready=ready,
            validate=lambda value: self._validate_registry(
                value,
                ids,
                state="approved",
                registry_id=registry_id,
                version=version,
                approval_decision_id=decision_id,
            ),
        )
        return self._checkpoint_receipt(
            record,
            step=f"{checkpoint_prefix}strategy_spec_approved_readback",
            key=f"{checkpoint_prefix}strategy_spec_approved",
            receipt=receipt,
        )

    def _validate_binding_identity(
        self,
        receipt: Mapping[str, Any],
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> None:
        if (
            receipt.get("binding_id") != ids.persona_capital_binding_id
            or receipt.get("persona_id") != record.persona_id
            or receipt.get("capital_pool_id") != ids.capital_pool_id
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
                "registry_id": ids.registry_id,
                "requested_by": self._requested_by(record),
                "provisioning_request_hash": record.request_hash,
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
            return (
                receipt.get("status") == "active"
                and receipt.get("approval_decision_id") == ids.approval_decision_id
            )

        def validate(receipt: Mapping[str, Any]) -> None:
            self._validate_binding_identity(receipt, record, ids)
            if not ready(receipt):
                raise PersonaProvisioningCoordinationError(
                    "PersonaCapitalBinding activation readback is not active with approval"
                )

        receipt = self._transition_then_get(
            owner="capital",
            get_path=get_path,
            post_path=f"{get_path}/activate",
            payload={
                "actor_id": self.actor_id,
                "actor_role": "persona.admin",
                "approval_decision_id": ids.approval_decision_id,
            },
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
        registry_receipt = _mapping(
            record.references.get("strategy_spec_approved"),
            label="checkpointed approved RegistryEntry",
        )
        approval_receipt = _mapping(
            record.references.get("approval_decided"),
            label="checkpointed ApprovalDecision",
        )
        baseline_receipt = _mapping(
            record.references.get("baseline_strategy_spec_approved"),
            label="checkpointed approved zero-capital baseline RegistryEntry",
        )
        registry_entry = _registry_entry(registry_receipt)
        baseline_entry = _registry_entry(baseline_receipt)
        self._validate_registry(
            baseline_receipt,
            ids,
            state="approved",
            registry_id=ids.baseline_registry_id,
            version=ids.baseline_version,
            approval_decision_id=ids.baseline_approval_decision_id,
        )
        payload = {
            "plan_id": ids.deployment_plan_id,
            "approval_decision_id": ids.approval_decision_id,
            "capital_pool_id": ids.capital_pool_id,
            "target_stage": "paper",
            "current_stage": "none",
            "registry_id": ids.registry_id,
            "registry_entry": registry_entry,
            "approval_decision": approval_receipt,
            "created_by": self.actor_id,
            "sponsor_persona_id": record.persona_id,
            "binding_id": ids.persona_capital_binding_id,
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
                "rollback_registry_id": ids.baseline_registry_id,
                "rollback_approval_decision_id": ids.baseline_approval_decision_id,
                "requested_by": self._requested_by(record),
                "provisioning_request_hash": record.request_hash,
            },
        }

        def validate(receipt: Mapping[str, Any]) -> None:
            rollback = receipt.get("rollback")
            rollback = rollback if isinstance(rollback, Mapping) else {}
            if (
                receipt.get("plan_id") != ids.deployment_plan_id
                or receipt.get("approval_decision_id") != ids.approval_decision_id
                or receipt.get("artifact_id") != ids.registry_id
                or receipt.get("artifact_version") != ids.version
                or receipt.get("strategy_id") != ids.strategy_id
                or receipt.get("capital_pool_id") != ids.capital_pool_id
                or receipt.get("target_stage") != "paper"
                or receipt.get("status") not in {"approved", "executing", "executed"}
                or receipt.get("binding_id") != ids.persona_capital_binding_id
                or rollback.get("target_artifact_id") != ids.baseline_registry_id
                or rollback.get("target_version") != ids.baseline_version
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

    def _coordinate_deployment_dispatch(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
    ) -> ProvisioningRecord:
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

        registry_entry = _registry_entry(
            _mapping(
                record.references.get("strategy_spec_approved"),
                label="checkpointed approved RegistryEntry",
            )
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
                "registry_entry": registry_entry,
                "metadata": {
                    "tenant_id": record.tenant_id,
                    "persona_id": record.persona_id,
                    "capital_pool_id": ids.capital_pool_id,
                    "persona_capital_binding_id": ids.persona_capital_binding_id,
                    "execution_context": "paper",
                    "requested_by": self._requested_by(record),
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
        return self._checkpoint_receipt(
            record,
            step="schedule_registered_readback",
            key="first_evaluation_schedule",
            receipt=receipt,
        )

    def _record_failure(
        self,
        record: ProvisioningRecord,
        ids: ProvisioningIds,
        *,
        failed_step: str,
        error: Exception,
    ) -> ProvisioningRecord:
        record.error = {
            "failed_step": failed_step,
            "terminal_reason": str(error) or error.__class__.__name__,
            "error_type": error.__class__.__name__,
            "failed_at": utc_now(),
        }
        record.result = None
        record.state = "failed"
        record.current_step = f"{failed_step}_failed"
        record = self.store.checkpoint(record, lease_owner=self.lease_owner)

        binding_path = f"/api/bindings/{_path_id(ids.persona_capital_binding_id)}"
        attempted_action = "inspect_persona_capital_binding_for_fail_closed_compensation"
        attempted_target_status: str | None = None
        try:
            binding = self._owner_get("capital", binding_path)
            if binding is None:
                return record
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
                        f"binding fail-closed transition has no authoritative receipt{detail}"
                    )
            self._validate_binding_identity(binding, record, ids)
            receipt_key = f"persona_capital_binding_{target_status}"
            record.references[receipt_key] = deepcopy(binding)
            record.compensation = {
                "status": "completed",
                "action": action,
                "binding_id": ids.persona_capital_binding_id,
                "resulting_status": target_status,
                "receipt": deepcopy(binding),
                "compensated_at": utc_now(),
            }
            record.state = "compensated"
            record.current_step = f"binding_{target_status}_compensation_readback"
            return self.store.checkpoint(record, lease_owner=self.lease_owner)
        except Exception as compensation_error:  # noqa: BLE001
            record.state = "failed"
            record.current_step = "compensation_failed"
            record.compensation = {
                "status": "failed",
                "action": attempted_action,
                "binding_id": ids.persona_capital_binding_id,
                "target_status": attempted_target_status,
                "terminal_reason": str(compensation_error)
                or compensation_error.__class__.__name__,
                "failed_at": utc_now(),
            }
            record.error["compensation_error"] = record.compensation["terminal_reason"]
            return self.store.checkpoint(record, lease_owner=self.lease_owner)
