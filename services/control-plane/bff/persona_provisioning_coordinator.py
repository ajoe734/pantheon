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
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from .persona_provisioning import (
    TERMINAL_STATES,
    PersonaProvisioningStore,
    ProvisioningRecord,
    utc_now,
)


FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_UNKNOWN_SOURCE_COMMIT = "0" * 40


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
        return self.store.release(
            active,
            lease_owner=self.lease_owner,
            lease_seconds=self.lease_seconds,
        )

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
        symbols = record.request_payload.get("symbols")
        if not isinstance(symbols, list) or not all(
            isinstance(item, str) and item.strip() for item in symbols
        ):
            symbols = ["SPY"]
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
                "bar_frequency": str(record.request_payload.get("bar_frequency") or "1d"),
                "data_source": str(
                    record.request_payload.get("data_source")
                    or "source-ingest:paper/persona-bootstrap"
                ),
                "lookback_bars": 2,
                "momentum_threshold": 0.0,
                "order_quantity": 0 if baseline else 1,
                "quantity_type": "SHARES",
                "zero_momentum_action": non_positive_action,
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
                ],
                "immutable_parameters": [
                    "symbols",
                    "bar_frequency",
                    "data_source",
                    "order_quantity",
                    "quantity_type",
                    "zero_momentum_action",
                ],
            },
            "lineage": {
                "source_run_ids": [source_run_id],
                "source_strategy_spec_id": spec_registry_id,
            },
            "provenance_refs": [
                "task:LOOP-PROD-PER-001",
                f"persona-provisioning:{ids.token}",
                f"strategy-spec:{spec_registry_id}",
            ],
        }
        if not baseline:
            artifact["lineage"]["parent_registry_ids"] = [ids.baseline_strategy_artifact_id]
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
                "requested_by": self._requested_by(record),
                "fail_closed_baseline": baseline,
                "rollback_target_registry_id": (
                    None if baseline else ids.baseline_strategy_artifact_id
                ),
                "provisioning_request_hash": record.request_hash,
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
        get_path = f"/api/registry/{registry_kind}/{_path_id(registry_id)}"

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
                artifact_type=artifact_type,
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
                "registry_id": ids.strategy_artifact_id,
                "strategy_spec_registry_id": ids.registry_id,
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
            record.references.get("strategy_artifact_approved"),
            label="checkpointed approved StrategyArtifact RegistryEntry",
        )
        approval_receipt = _mapping(
            record.references.get("strategy_artifact_approval_decided"),
            label="checkpointed ApprovalDecision",
        )
        baseline_receipt = _mapping(
            record.references.get("baseline_strategy_artifact_approved"),
            label="checkpointed approved zero-capital baseline StrategyArtifact RegistryEntry",
        )
        registry_entry = _registry_entry(registry_receipt)
        baseline_entry = _registry_entry(baseline_receipt)
        self._validate_registry(
            baseline_receipt,
            ids,
            state="approved",
            registry_id=ids.baseline_strategy_artifact_id,
            version=ids.baseline_version,
            approval_decision_id=ids.baseline_strategy_artifact_approval_decision_id,
            artifact_type="execution_bundle",
        )
        payload = {
            "plan_id": ids.deployment_plan_id,
            "approval_decision_id": ids.strategy_artifact_approval_decision_id,
            "capital_pool_id": ids.capital_pool_id,
            "target_stage": "paper",
            "current_stage": "none",
            "registry_id": ids.strategy_artifact_id,
            "registry_entry": registry_entry,
            "approval_decision": approval_receipt,
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
                record.references.get("strategy_artifact_approved"),
                label="checkpointed approved StrategyArtifact RegistryEntry",
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
        if "provisioning_readback_started_at" not in record.references:
            record.references["provisioning_readback_started_at"] = utc_now()
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
