"""Transport-level contract tests for paper Persona provisioning coordination."""
from __future__ import annotations

import os
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest

try:
    from services.control_plane.bff.persona_provisioning import (
        MemoryPersonaProvisioningStore,
        ProvisioningRecord,
    )
    from services.control_plane.bff.persona_provisioning_coordinator import (
        FIRST_EVALUATION_WORKFLOW_ID,
        PersonaProvisioningCoordinator,
        deterministic_provisioning_ids,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(__file__))
    from persona_provisioning import MemoryPersonaProvisioningStore, ProvisioningRecord  # type: ignore[no-redef]
    from persona_provisioning_coordinator import (  # type: ignore[no-redef]
        FIRST_EVALUATION_WORKFLOW_ID,
        PersonaProvisioningCoordinator,
        deterministic_provisioning_ids,
    )
from services.registry.paper_strategy_spec import validate_strategy_spec
from services.registry.strategy_artifact import (
    strategy_artifact_checksum,
    validate_strategy_artifact,
)


class TrackingStore(MemoryPersonaProvisioningStore):
    def __init__(self) -> None:
        super().__init__()
        self.checkpoints: list[str] = []
        self.checkpoint_lease_seconds: list[int] = []

    def checkpoint(self, record, *, lease_owner, lease_seconds=60):
        self.checkpoints.append(record.current_step)
        self.checkpoint_lease_seconds.append(lease_seconds)
        return super().checkpoint(
            record,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
        )


def _owner_now() -> str:
    """Owner services stamp second-precision UTC creation times."""

    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeOwnerTransport:
    """Small in-memory implementation of only the owner routes under test.

    Owner-side invariants that the coordinator's bodies must satisfy are
    modelled with the real owner code where it is pure: Registry's inline
    StrategySpec checksum and StrategySpec revision lineage rule, and strict
    Governance's proposal-owner / actor binding, CAS versioning and decided
    provenance.  ``governance_subject`` is the verified subject of the
    approval principal the transport would authenticate as; ``governance_roles``
    are the roles that principal has actually been granted.
    """

    def __init__(
        self,
        *,
        response_loss: set[str] | None = None,
        mutation_failure: set[str] | None = None,
        governance_subject: str = "pantheon-persona-provisioner",
        governance_roles: tuple[str, ...] = ("approval_proposer", "automated_gate"),
    ) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.response_loss = set(response_loss or set())
        self.mutation_failure = set(mutation_failure or set())
        self.mutations = Counter()
        self.governance_subject = governance_subject
        self.governance_roles = set(governance_roles)
        self.advance_receipts: dict[tuple[str, str], dict[str, Any]] = {}

    def _authorization_scope(self) -> dict[str, Any] | None:
        """Owner-stamped scope for the dedicated dev paper subject, else None.

        Strict Governance derives this from the verified principal's grant;
        it is never a client body field.
        """

        try:
            from services.governance.paper_approval_scope import (
                DEV_PAPER_AUTHORIZATION_SCOPE,
                DEV_PAPER_PROVISIONER_SUBJECT,
                dev_paper_feature_enabled,
            )
        except ImportError:  # paper scope owner module not integrated yet
            return None
        if self.governance_subject != DEV_PAPER_PROVISIONER_SUBJECT:
            return None
        if not dev_paper_feature_enabled():
            raise PermissionError(
                "HTTP 403: Dev paper approval principal is not enabled in this environment"
            )
        return deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE)

    def get(self, owner: str, path: str) -> Mapping[str, Any] | None:
        self.calls.append(("GET", owner, path, None))
        value = self.objects.get((owner, path))
        return deepcopy(value) if value is not None else None

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = deepcopy(dict(payload))
        self.calls.append(("POST", owner, path, body))
        self.mutations[(owner, path)] += 1
        if path in self.mutation_failure:
            raise ConnectionError(f"owner rejected {path}")
        response = self._apply_post(owner, path, body)
        if path in self.response_loss:
            self.response_loss.remove(path)
            raise ConnectionError(f"response lost for {path}")
        return deepcopy(response)

    def patch(self, owner: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = deepcopy(dict(payload))
        self.calls.append(("PATCH", owner, path, body))
        self.mutations[(owner, path)] += 1
        binding_path = path.removesuffix("/status")
        binding = self.objects[(owner, binding_path)]
        binding["status"] = body["status"]
        return deepcopy(binding)

    def _owner_registry_entry(self, registry_id: str) -> dict[str, Any]:
        """Resolve an approved RegistryEntry from owner state, by exact ID."""

        view = self.objects.get(
            ("registry", f"/api/registry/strategy-artifacts/{registry_id}")
        )
        if view is None:
            raise AssertionError(f"unknown RegistryEntry: {registry_id}")
        entry = deepcopy(view["entry"])
        if entry.get("artifact_state") != "approved":
            raise AssertionError(f"RegistryEntry {registry_id} is not approved")
        return entry

    def _put(self, owner: str, path: str, value: Mapping[str, Any]) -> dict[str, Any]:
        stored = deepcopy(dict(value))
        self.objects[(owner, path)] = stored
        return stored

    def _read_entry_dict(self, registry_id: str) -> dict[str, Any] | None:
        """Exact local owner read, as RegistryService._read_entry_dict."""

        for (view_owner, view_path), view in self.objects.items():
            if view_owner == "registry" and view_path.endswith(f"/{registry_id}"):
                return deepcopy(view["entry"])
        return None

    def _advance_registry_entry(self, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Model RegistryService.advance_artifact_state's strict approval contract.

        A caller-supplied ``approver`` is retired; approval needs the decision
        reference, a command key and the caller's exact CAS base
        (state/version/updated_at).  A same-key identical replay returns the
        committed row without re-running the transition, a same-key
        divergent replay is a conflict, and the approval itself is verified
        with the real shared ``ApprovalEvidence`` exactly as Registry does,
        including the paper usage context for a scoped decision.
        """

        from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid

        view = self.objects[("registry", target)]
        entry = view["entry"]
        approving = body["target_state"] == "approved"
        if approving:
            if body.get("approver") is not None:
                raise ValueError(
                    "HTTP 400: Caller-supplied approver is retired; use a Governance decision reference"
                )
            if not body.get("approval_decision_id") or not body.get("command_key"):
                raise ValueError(
                    "HTTP 400: Approval requires verified decision reference, actor and command key"
                )
            if not all(
                body.get(key)
                for key in ("expected_artifact_state", "expected_version", "expected_updated_at")
            ):
                raise ValueError(
                    "HTTP 400: Approval requires the caller artifact base state/version/time"
                )
        fields = {
            key: body.get(key)
            for key in (
                "target_state",
                "approver",
                "approval_decision_id",
                "expected_artifact_state",
                "expected_version",
                "expected_updated_at",
            )
        }
        receipt_key = (target, body["command_key"])
        receipt = self.advance_receipts.get(receipt_key)
        if receipt is not None:
            if receipt["fields"] != fields:
                raise ValueError("HTTP 409: command_key replay diverges from the committed command")
            return deepcopy(receipt["view"])
        base = (entry["artifact_state"], entry["version"], entry["updated_at"])
        claimed = (
            body["expected_artifact_state"],
            body.get("expected_version") or entry["version"],
            body.get("expected_updated_at") or entry["updated_at"],
        )
        if base != claimed:
            raise ValueError(f"HTTP 409: stale artifact base {claimed!r} != {base!r}")
        committed_at = _owner_now()
        if approving:
            decision = self.objects.get(
                ("governance", f"/api/governance/approvals/{body['approval_decision_id']}")
            )
            if decision is None:
                raise ValueError("HTTP 400: Governance denied exact decision read")
            try:
                evidence = ApprovalEvidence.model_validate(decision)
                usage_context = None
                if getattr(evidence, "authorization_scope", None) is not None:
                    from services.governance.paper_approval_scope import (
                        current_environment,
                        paper_candidate_usage_context,
                    )

                    usage_context = paper_candidate_usage_context(
                        deepcopy(entry),
                        evidence=evidence.model_dump(),
                        read_entry=self._read_entry_dict,
                        environment=current_environment(),
                    )
                expected = {
                    "tenant_id": entry["owner_tenant"],
                    "target_type": "registry_entry",
                    "target_id": entry["registry_id"],
                    "target_version": entry["version"],
                    "candidate_digest": entry["checksum"],
                }
                if usage_context is None:
                    evidence.require_valid(expected=expected)
                else:
                    evidence.require_valid(expected=expected, usage_context=usage_context)
            except (ApprovalInvalid, ValueError) as exc:
                raise ValueError(f"HTTP 400: {exc}") from exc
            entry.update(
                artifact_state="approved",
                approval_decision_id=body["approval_decision_id"],
                approver=evidence.actor_id,
                approved_at=committed_at,
                approval_evidence=evidence.model_dump(),
                updated_at=committed_at,
            )
        else:
            entry.update(artifact_state=body["target_state"], updated_at=committed_at)
        self.advance_receipts[receipt_key] = {"fields": fields, "view": deepcopy(view)}
        return view

    def _apply_post(self, owner: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if owner == "capital" and path == "/api/capital-pools":
            pool = {
                **body,
                "created_at": "2026-07-15T00:00:00Z",
                "idempotent_replay": False,
            }
            return self._put(owner, f"/api/capital-pools/{body['pool_id']}", pool)

        if owner == "registry" and path == "/api/registry/strategy-specs":
            # The real facade validates the inline StrategySpec against the
            # canonical schema, computes its checksum and enforces the
            # per-strategy revision lineage rule before it persists anything;
            # a rejected revision has no readback.
            from services.registry.models import RegistryEntry
            from services.registry.service import (
                StrategySpecRegisterRequest,
                _check_strategy_spec_version_lineage,
                _strategy_spec_register_payload,
            )

            admitted = _strategy_spec_register_payload(
                StrategySpecRegisterRequest.model_validate(body)
            )
            existing = [
                RegistryEntry.from_dict(view["entry"])
                for (view_owner, view_path), view in self.objects.items()
                if view_owner == "registry"
                and view_path.startswith("/api/registry/strategy-specs/")
                and view["entry"]["strategy_id"] == body["strategy_id"]
            ]
            _check_strategy_spec_version_lineage(
                existing,
                body["strategy_id"],
                body["version"],
                admitted.lineage,
                base_checksum=body.get("base_checksum"),
            )
            created_at = _owner_now()
            entry = RegistryEntry(
                registry_id=body["registry_id"],
                owner_tenant=body["metadata"]["tenant_id"],
                created_at=created_at,
                updated_at=created_at,
                **vars(admitted),
            ).to_dict()
            return self._put(
                owner,
                f"/api/registry/strategy-specs/{body['registry_id']}",
                {"entry": entry, "deployment_stage": "none"},
            )

        if owner == "registry" and path == "/api/registry/strategy-artifacts":
            # The real facade validates the StrategyArtifact schema and builds
            # the execution_bundle envelope, including its durable checksum.
            from services.registry.models import RegistryEntry
            from services.registry.strategy_artifact import (
                build_strategy_artifact_registry_payload,
            )

            registry_id, admitted = build_strategy_artifact_registry_payload(body)
            created_at = _owner_now()
            entry = RegistryEntry(
                registry_id=registry_id,
                owner_tenant=(body.get("metadata") or {}).get("tenant_id"),
                created_at=created_at,
                updated_at=created_at,
                **vars(admitted),
            ).to_dict()
            assert entry["checksum"] == strategy_artifact_checksum(body["strategy_artifact"])
            return self._put(
                owner,
                f"/api/registry/strategy-artifacts/{registry_id}",
                {"entry": entry, "deployment_stage": "none"},
            )

        if owner == "governance" and path == "/api/governance/approvals":
            # Strict Governance: the proposal owner must be the verified
            # subject, the body tenant must be the principal's tenant, the
            # base version must be zero, and a same-ID decision is a conflict.
            if body["owner_user_id"] != self.governance_subject:
                raise PermissionError(
                    "HTTP 403: Proposal owner and tenant must match verified principal"
                )
            if "approval_proposer" not in self.governance_roles and not (
                self.governance_roles & {"automated_gate", "governance_reviewer"}
            ):
                raise PermissionError("HTTP 403: Approval propose role required")
            if body["expected_version"] != 0:
                raise ValueError("HTTP 409: Proposal expected_version must be zero")
            decision_path = f"/api/governance/approvals/{body['decision_id']}"
            if (owner, decision_path) in self.objects:
                raise ValueError("HTTP 409: Approval decision already exists")
            decision = {
                **{key: value for key, value in body.items() if key != "expected_version"},
                "decision": None,
                "decision_state": "proposed",
                "actor_role": None,
                "actor_id": None,
                "rationale": None,
                "created_at": _owner_now(),
                "decided_at": None,
                "conditions": [],
                "evidence_refs": [],
                "superseded_by": None,
                "revoked_at": None,
                "metadata": None,
                "controller_record_ref": None,
                "recorded_at": None,
                "authority_status": None,
                "authorization_scope": self._authorization_scope(),
                "version": 1,
                "event_id": f"event-{body['decision_id']}-1",
            }
            return self._put(owner, decision_path, decision)

        if owner == "governance" and path.endswith(("/review", "/decide")):
            target = path.rsplit("/", 1)[0]
            decision = self.objects[(owner, target)]
            self._authorization_scope()  # the dedicated grant is re-admitted per command
            if body["actor_id"] != self.governance_subject or (
                body["actor_role"] not in self.governance_roles
            ):
                raise PermissionError(
                    "HTTP 403: Body actor and role must match verified principal"
                )
            if body["expected_version"] != decision["version"]:
                raise ValueError("HTTP 409: Approval base version is stale")
            decision["version"] += 1
            decision["event_id"] = f"event-{decision['decision_id']}-{decision['version']}"
            if path.endswith("/review"):
                if decision["decision_state"] != "proposed":
                    raise ValueError("HTTP 400: Can only accept review from 'proposed' state")
                decision.update(
                    decision_state="under_review",
                    actor_role=body["actor_role"],
                    actor_id=body["actor_id"],
                )
                return decision
            if decision["decision_state"] != "under_review":
                raise ValueError("HTTP 400: Can only decide from 'under_review'")
            decided_at = _owner_now()
            decision.update(
                decision_state="decided",
                decision=body["outcome"],
                rationale=body["rationale"],
                actor_role=body["actor_role"],
                actor_id=body["actor_id"],
                evidence_refs=deepcopy(body.get("evidence_refs") or []),
                conditions=list(body.get("conditions") or []),
                decided_at=decided_at,
                recorded_at=decided_at,
                authority_status="authoritative",
                controller_record_ref=(
                    f"governance-controller://approval-{decision['decision_id']}"
                ),
            )
            for field in ("candidate_digest", "proof_digest", "expires_at", "session_id"):
                if body.get(field) is not None:
                    decision[field] = body[field]
            return decision

        if owner == "registry" and path.endswith("/advance"):
            return self._advance_registry_entry(path.removesuffix("/advance"), body)

        if owner == "capital" and path == "/api/bindings":
            binding = {
                **body,
                "status": "pending",
                "approval_decision_id": None,
                "created_at": "2026-07-15T00:00:00Z",
            }
            return self._put(owner, f"/api/bindings/{body['binding_id']}", binding)

        if owner == "capital" and path.endswith("/activate"):
            target = path.removesuffix("/activate")
            binding = self.objects[(owner, target)]
            binding.update(
                status="active",
                approval_decision_id=body["approval_decision_id"],
            )
            return binding

        if owner == "deployment" and path == "/api/deployment/plans":
            # Deployment is the owner reader: it resolves the RegistryEntry by
            # the exact registry_id it was given, exactly like the real service.
            # It must never trust a caller-embedded snapshot.
            entry = self._owner_registry_entry(body["registry_id"])
            plan = {
                "plan_id": body["plan_id"],
                "approval_decision_id": body["approval_decision_id"],
                "artifact_id": entry["registry_id"],
                "artifact_version": entry["version"],
                "artifact_type": entry["artifact_type"],
                "strategy_id": entry["strategy_id"],
                "capital_pool_id": body["capital_pool_id"],
                "current_stage": body["current_stage"],
                "target_stage": body["target_stage"],
                "transition_type": "activate",
                "runtime_action": "deploy_new_binding",
                "status": body["status"],
                "created_at": "2026-07-15T00:00:00Z",
                "binding_id": body.get("binding_id"),
                "rollback": body["rollback"],
                "metadata": body["metadata"],
            }
            return self._put(owner, f"/api/deployment/plans/{body['plan_id']}", plan)

        if owner == "deployment" and path.endswith("/dispatch"):
            plan_id = path.split("/")[-2]
            saga = {
                "saga_id": body["saga_id"],
                "plan_id": plan_id,
                "status": "awaiting_binding",
                "current_step": "binding_requested",
                "binding_id": None,
                "runtime_id": None,
            }
            self._put(owner, f"/api/deployment/sagas/{body['saga_id']}", saga)
            return {"deployment_saga": {"saga": deepcopy(saga)}, "replayed": False}

        if owner == "deployment" and path.endswith("/failure"):
            target = path.removesuffix("/failure")
            saga = self.objects[(owner, target)]
            decision = {
                "command_type": "abort_plan",
                "owner_service": "deployment-orchestrator",
                "plan_status": "aborted",
                "binding_status": "none",
                "write_scope": "deployment_plan",
                "reason": body["reason"],
            }
            saga.update(
                status="compensating",
                current_step="compensation_requested",
                failure_reason=body["reason"],
                compensation=decision,
            )
            return decision

        raise AssertionError(f"unexpected mutation: {owner} {path}")


def _record_and_store() -> tuple[TrackingStore, ProvisioningRecord]:
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-a",
        idempotency_key="create-persona-a",
        request_hash="sha256:persona-request",
        normalized_name="trader a",
        persona_id="persona-a",
        request_payload={
            "name": "Trader A",
            "requested_by": "operator-a",
            "mandate": "Paper-only momentum research",
            "traits": {"risk_appetite": "low"},
            "budget": 25000,
        },
    )
    assert created
    return store, record


def _schedule_receipt(persona_id: str, pool_id: str, binding_id: str) -> dict[str, Any]:
    return {
        "persona_id": persona_id,
        "capital_pool_id": pool_id,
        "binding_id": binding_id,
        "mode": "gateway_rpc",
        "registered": [
            {
                "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
                "job_id": "cron-first-evaluation",
            }
        ],
        "skipped": [],
        "failed": [],
    }


def _coordinator(
    store,
    transport,
    registrar,
    *,
    lease_seconds: int = 60,
    actor_id: str = "pantheon-persona-provisioner",
    governance_actor_id: str | None = None,
) -> PersonaProvisioningCoordinator:
    return PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="test-worker",
        lease_seconds=lease_seconds,
        actor_id=actor_id,
        governance_actor_id=governance_actor_id,
    )


def _owner_entry(transport: FakeOwnerTransport, kind: str, registry_id: str) -> dict[str, Any]:
    """The durable owner-side RegistryEntry, by exact ID."""

    return deepcopy(transport.objects[("registry", f"/api/registry/{kind}/{registry_id}")]["entry"])


def _plus_24h(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_payload(
    transport: FakeOwnerTransport,
    path: str,
    *,
    identity: tuple[str, str] | None = None,
) -> dict[str, Any]:
    matches = [call[3] for call in transport.calls if call[0] == "POST" and call[2] == path]
    if identity is not None:
        field, expected = identity
        matches = [payload for payload in matches if payload and payload.get(field) == expected]
    assert len(matches) == 1
    assert matches[0] is not None
    return matches[0]


def test_owner_payload_contract_and_checkpointed_dispatch_admission() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    schedule_calls: list[tuple[str, str, str]] = []

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        schedule_calls.append((persona_id, pool_id, binding_id))
        return _schedule_receipt(persona_id, pool_id, binding_id)

    result = _coordinator(
        store,
        transport,
        registrar,
        lease_seconds=137,
    ).coordinate(record)
    ids = deterministic_provisioning_ids(record)

    assert result.state == "provisioning"
    assert result.current_step == "schedule_registered"
    assert result.result["status"] == "dispatch_admitted"
    assert result.result["paper_running"] is False
    assert "runtime_binding_id" not in result.result
    assert result.references["provisioning_readback_started_at"].endswith("Z")
    assert set(store.checkpoint_lease_seconds) == {137}
    assert schedule_calls == [
        (record.persona_id, ids.capital_pool_id, ids.persona_capital_binding_id)
    ]

    expected_steps = {
        "capital_pool_readback",
        "baseline_strategy_spec_candidate_readback",
        "baseline_approval_proposed_readback",
        "baseline_approval_reviewed_readback",
        "baseline_approval_decided_readback",
        "baseline_strategy_spec_approved_readback",
        "baseline_strategy_artifact_candidate_readback",
        "baseline_strategy_artifact_approval_proposed_readback",
        "baseline_strategy_artifact_approval_reviewed_readback",
        "baseline_strategy_artifact_approval_decided_readback",
        "baseline_strategy_artifact_approved_readback",
        "strategy_spec_candidate_readback",
        "approval_proposed_readback",
        "approval_reviewed_readback",
        "approval_decided_readback",
        "strategy_spec_approved_readback",
        "strategy_artifact_candidate_readback",
        "strategy_artifact_approval_proposed_readback",
        "strategy_artifact_approval_reviewed_readback",
        "strategy_artifact_approval_decided_readback",
        "strategy_artifact_approved_readback",
        "persona_capital_binding_created_readback",
        "persona_capital_binding_active_readback",
        "deployment_plan_readback",
        "deployment_dispatch_admitted_readback",
        "schedule_registered_readback",
    }
    assert expected_steps.issubset(store.checkpoints)

    pool = _post_payload(transport, "/api/capital-pools")
    assert pool["pool_id"] == ids.capital_pool_id
    assert pool["status"] == "active"
    assert pool["single_runtime_enforced"] is True
    assert pool["metadata"]["internal"] is True
    assert pool["metadata"]["requested_by"] == "operator-a"

    baseline = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.baseline_registry_id),
    )
    assert baseline["strategy_id"] == ids.strategy_id
    assert baseline["version"] == ids.baseline_version
    assert baseline["artifact_state"] == "candidate"
    assert baseline["strategy_spec"]["metadata"]["capital_scale_pct"] == 0.0
    assert baseline["strategy_spec"]["metadata"]["fail_closed_baseline"] is True

    baseline_approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.baseline_approval_decision_id),
    )
    assert baseline_approval["target_id"] == ids.baseline_registry_id
    assert baseline_approval["target_version"] == ids.baseline_version

    baseline_artifact = _post_payload(
        transport,
        "/api/registry/strategy-artifacts",
        identity=("registry_id", ids.baseline_strategy_artifact_id),
    )
    assert baseline_artifact["artifact_state"] == "candidate"
    assert baseline_artifact["strategy_artifact"]["artifact_id"] == (
        ids.baseline_strategy_artifact_id
    )
    assert baseline_artifact["strategy_artifact"]["strategy_id"] == ids.strategy_id
    assert baseline_artifact["strategy_artifact"]["lineage"][
        "source_strategy_spec_id"
    ] == ids.baseline_registry_id
    assert baseline_artifact["strategy_artifact"]["strategy_logic"][
        "positive_action"
    ] == "HOLD"
    validate_strategy_artifact(baseline_artifact["strategy_artifact"])

    baseline_artifact_approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.baseline_strategy_artifact_approval_decision_id),
    )
    assert baseline_artifact_approval["target_id"] == ids.baseline_strategy_artifact_id
    assert baseline_artifact_approval["target_version"] == ids.baseline_version

    candidate = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.registry_id),
    )
    assert candidate["registry_id"] == ids.registry_id
    assert candidate["artifact_state"] == "candidate"
    assert candidate["rollback_target"] == ids.baseline_version
    assert candidate["metadata"]["rollback_target_registry_id"] == ids.baseline_registry_id
    assert candidate["strategy_spec"]["metadata"]["persona_id"] == record.persona_id
    assert candidate["strategy_spec"]["metadata"]["capital_pool_id"] == ids.capital_pool_id
    # The forward revision names its exact approved parent and, like the
    # baseline, admits zero live capital in a paper-only execution context.
    # Both inline specs satisfy the canonical StrategySpec schema Registry
    # validates and hashes, from persisted request data only.
    assert candidate["lineage"]["parent_registry_ids"] == [ids.baseline_registry_id]
    assert "parent_registry_ids" not in baseline["lineage"]
    for spec_payload in (baseline, candidate):
        spec = spec_payload["strategy_spec"]
        assert validate_strategy_spec(spec) == []
        assert spec["strategy_id"] == ids.strategy_id
        assert spec["execution_profile"]["execution_mode_hint"] == "paper"
        assert spec["provenance"]["created_at"] == record.created_at
        assert spec["provenance"]["created_by"] == "pantheon-persona-provisioner"
        assert spec["metadata"]["capital_scale_pct"] == 0.0
        assert spec["metadata"]["execution_context"] == "paper"
        assert spec["metadata"]["requested_by"] == "operator-a"
        assert spec_payload["evaluation_summary"]["capital_scale_pct"] == 0.0
        assert spec_payload["evaluation_summary"]["execution_context"] == "paper"
        assert spec_payload["metadata"]["execution_context"] == "paper"
        assert spec_payload["metadata"]["capital_scale_pct"] == 0.0

    approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.approval_decision_id),
    )
    assert approval["target_type"] == "registry_entry"
    assert approval["target_id"] == ids.registry_id
    assert approval["risk_level"] == "low"
    # Every proposal binds the durable owner checksum of its exact target and
    # a finite expiry anchored to that entry's owner creation time; the
    # decision retains both.  The proposal owner is the Governance subject,
    # never the human requester.
    for decision_id, kind, registry_id in (
        (ids.baseline_approval_decision_id, "strategy-specs", ids.baseline_registry_id),
        (
            ids.baseline_strategy_artifact_approval_decision_id,
            "strategy-artifacts",
            ids.baseline_strategy_artifact_id,
        ),
        (ids.approval_decision_id, "strategy-specs", ids.registry_id),
        (
            ids.strategy_artifact_approval_decision_id,
            "strategy-artifacts",
            ids.strategy_artifact_id,
        ),
    ):
        owner_entry = _owner_entry(transport, kind, registry_id)
        proposal = _post_payload(
            transport,
            "/api/governance/approvals",
            identity=("decision_id", decision_id),
        )
        decision = _post_payload(
            transport,
            f"/api/governance/approvals/{decision_id}/decide",
        )
        assert owner_entry["checksum"].startswith("sha256:")
        assert proposal["candidate_digest"] == owner_entry["checksum"]
        assert proposal["expires_at"] == _plus_24h(owner_entry["created_at"])
        assert decision["candidate_digest"] == proposal["candidate_digest"]
        assert decision["expires_at"] == proposal["expires_at"]
        assert proposal["owner_user_id"] == "pantheon-persona-provisioner"
        assert proposal["owner_user_id"] != "operator-a"
        persisted = transport.objects[("governance", f"/api/governance/approvals/{decision_id}")]
        assert persisted["expires_at"] == _plus_24h(owner_entry["created_at"])
        assert persisted["expires_at"] <= _plus_24h(persisted["created_at"])
        assert persisted["candidate_digest"] == owner_entry["checksum"]
    artifact = _post_payload(
        transport,
        "/api/registry/strategy-artifacts",
        identity=("registry_id", ids.strategy_artifact_id),
    )
    assert artifact["registry_id"] == ids.strategy_artifact_id
    assert artifact["rollback_target"] == ids.baseline_strategy_artifact_id
    assert artifact["strategy_artifact"]["artifact_id"] == ids.strategy_artifact_id
    assert artifact["strategy_artifact"]["lineage"]["source_strategy_spec_id"] == ids.registry_id
    assert artifact["strategy_artifact"]["lineage"]["parent_registry_ids"] == [
        ids.baseline_strategy_artifact_id
    ]
    assert artifact["strategy_artifact"]["strategy_logic"]["positive_action"] == "BUY"
    validate_strategy_artifact(artifact["strategy_artifact"])
    artifact_approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.strategy_artifact_approval_decision_id),
    )
    assert artifact_approval["target_type"] == "registry_entry"
    assert artifact_approval["target_id"] == ids.strategy_artifact_id
    assert artifact_approval["risk_level"] == "low"
    review = _post_payload(
        transport,
        f"/api/governance/approvals/{ids.approval_decision_id}/review",
    )
    decide = _post_payload(
        transport,
        f"/api/governance/approvals/{ids.approval_decision_id}/decide",
    )
    assert review["actor_role"] == "automated_gate"
    assert decide["actor_role"] == "automated_gate"
    assert review["actor_id"] == decide["actor_id"] == "pantheon-persona-provisioner"
    for bundle in (baseline_artifact, artifact):
        assert bundle["evaluation_summary"]["execution_context"] == "paper"
        assert bundle["evaluation_summary"]["capital_scale_pct"] == 0.0
        assert bundle["metadata"]["execution_context"] == "paper"
        assert bundle["metadata"]["capital_scale_pct"] == 0.0
        assert bundle["metadata"]["persona_id"] == record.persona_id
        # No binding_intent: its schema-mandatory observed_* fields describe a
        # RuntimeBinding this coordinator has neither created nor observed.
        assert "binding_intent" not in bundle["strategy_artifact"]
        assert bundle["strategy_artifact"]["parameters"]["symbols"] == (
            candidate["strategy_spec"]["market_scope"]["symbols"]
        )

    # Registry approval: the approver is the Governance decision, the actor
    # is the transport subject (never a body field), and the CAS base is the
    # exact candidate row read back before the transition.
    for kind, registry_id, decision_id in (
        ("strategy-specs", ids.registry_id, ids.approval_decision_id),
        (
            "strategy-artifacts",
            ids.strategy_artifact_id,
            ids.strategy_artifact_approval_decision_id,
        ),
    ):
        advance = _post_payload(transport, f"/api/registry/{kind}/{registry_id}/advance")
        assert "approver" not in advance
        assert advance["target_state"] == "approved"
        assert advance["approval_decision_id"] == decision_id
        assert advance["expected_artifact_state"] == "candidate"
        approved_entry = _owner_entry(transport, kind, registry_id)
        assert advance["expected_version"] == approved_entry["version"]
        assert advance["expected_updated_at"] < approved_entry["updated_at"] or (
            advance["expected_updated_at"] == approved_entry["updated_at"]
        )
        assert advance["command_key"] == (
            f"persona-provisioning:{ids.token}:advance:{registry_id}:approved:"
            + advance["command_key"].rsplit(":", 1)[1]
        )
        assert approved_entry["approver"] == "pantheon-persona-provisioner"
        assert approved_entry["approval_evidence"]["decision_id"] == decision_id

    binding = _post_payload(transport, "/api/bindings")
    assert binding["binding_id"] == ids.persona_capital_binding_id
    assert binding["persona_id"] == record.persona_id
    assert binding["capital_pool_id"] == ids.capital_pool_id
    assert binding["role"] == "paper_owner"
    assert binding["allowed_deployment_scope"] == "paper"

    plan = _post_payload(transport, "/api/deployment/plans")
    assert "binding_id" not in plan
    assert plan["approval_decision_id"] == ids.strategy_artifact_approval_decision_id
    assert plan["registry_id"] == ids.strategy_artifact_id
    assert plan["metadata"]["persona_capital_binding_id"] == (
        ids.persona_capital_binding_id
    )
    assert plan["metadata"]["strategy_spec_registry_id"] == ids.registry_id
    assert plan["metadata"]["strategy_artifact_id"] == ids.strategy_artifact_id
    # Deployment owns the Registry/Governance reads; the coordinator sends the
    # exact IDs and never a client-side snapshot of owner state.
    assert "registry_entry" not in plan
    assert "approval_decision" not in plan
    assert plan["rollback"]["target_artifact_id"] == ids.baseline_strategy_artifact_id
    assert plan["rollback"]["target_version"] == ids.baseline_version
    assert plan["rollback"]["action_type"] == "pause_then_replace"

    dispatch_path = f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch"
    dispatch = _post_payload(transport, dispatch_path)
    assert dispatch["saga_id"] == ids.deployment_saga_id
    assert dispatch["metadata"]["persona_capital_binding_id"] == ids.persona_capital_binding_id
    assert "registry_entry" not in dispatch
    assert "runtime_id" not in dispatch
    assert "runtime_binding_id" not in dispatch
    assert {call[1] for call in transport.calls} == {
        "capital",
        "registry",
        "governance",
        "deployment",
    }

    create_read_paths = {
        "/api/capital-pools": f"/api/capital-pools/{ids.capital_pool_id}",
        "/api/bindings": f"/api/bindings/{ids.persona_capital_binding_id}",
        "/api/deployment/plans": f"/api/deployment/plans/{ids.deployment_plan_id}",
    }
    for index, (method, owner, path, payload) in enumerate(transport.calls):
        if method not in {"POST", "PATCH"}:
            continue
        preceding = transport.calls[:index]
        read_path = create_read_paths.get(path, path)
        if path == "/api/registry/strategy-specs":
            read_path = f"/api/registry/strategy-specs/{payload['registry_id']}"
        elif path == "/api/registry/strategy-artifacts":
            read_path = f"/api/registry/strategy-artifacts/{payload['registry_id']}"
        elif path == "/api/governance/approvals":
            read_path = f"/api/governance/approvals/{payload['decision_id']}"
        if path.endswith("/review") or path.endswith("/decide") or path.endswith("/advance"):
            read_path = path.rsplit("/", 1)[0]
        elif path.endswith("/activate") or path.endswith("/status"):
            read_path = path.rsplit("/", 1)[0]
        elif path.endswith("/dispatch"):
            read_path = f"/api/deployment/sagas/{ids.deployment_saga_id}"
        assert any(
            call[0] == "GET" and call[1] == owner and call[2] == read_path
            for call in preceding
        )


def test_mutation_payloads_parse_with_authoritative_owner_wire_models() -> None:
    from services.capital.models import (
        ActivateBindingRequest,
        CreateBindingRequest,
        CreateCapitalPoolRequest,
    )
    from services.deployment.models import (
        CreateDeploymentPlanRequest,
        DispatchDeploymentPlanRequest,
    )
    from services.governance.models import (
        AcceptReviewRequest,
        DecideRequest,
        ProposeApprovalRequest,
    )
    from services.registry.service import (
        AdvanceRequest,
        StrategyArtifactRegisterRequest,
        StrategySpecRegisterRequest,
    )

    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    transport = FakeOwnerTransport()
    _coordinator(store, transport, _schedule_receipt).coordinate(record)

    CreateCapitalPoolRequest(**_post_payload(transport, "/api/capital-pools"))
    CreateBindingRequest(**_post_payload(transport, "/api/bindings"))
    ActivateBindingRequest(
        **_post_payload(
            transport,
            f"/api/bindings/{ids.persona_capital_binding_id}/activate",
        )
    )

    for registry_id in (ids.baseline_registry_id, ids.registry_id):
        StrategySpecRegisterRequest(
            **_post_payload(
                transport,
                "/api/registry/strategy-specs",
                identity=("registry_id", registry_id),
            )
        )
        AdvanceRequest(
            **_post_payload(
                transport,
                f"/api/registry/strategy-specs/{registry_id}/advance",
            )
        )

    for registry_id in (
        ids.baseline_strategy_artifact_id,
        ids.strategy_artifact_id,
    ):
        artifact_registration = StrategyArtifactRegisterRequest(
            **_post_payload(
                transport,
                "/api/registry/strategy-artifacts",
                identity=("registry_id", registry_id),
            )
        )
        validate_strategy_artifact(artifact_registration.strategy_artifact)
        AdvanceRequest(
            **_post_payload(
                transport,
                f"/api/registry/strategy-artifacts/{registry_id}/advance",
            )
        )

    for decision_id in (
        ids.baseline_approval_decision_id,
        ids.baseline_strategy_artifact_approval_decision_id,
        ids.approval_decision_id,
        ids.strategy_artifact_approval_decision_id,
    ):
        ProposeApprovalRequest(
            **_post_payload(
                transport,
                "/api/governance/approvals",
                identity=("decision_id", decision_id),
            )
        )
        review = AcceptReviewRequest(
            **_post_payload(
                transport,
                f"/api/governance/approvals/{decision_id}/review",
            )
        )
        decision = DecideRequest(
            **_post_payload(
                transport,
                f"/api/governance/approvals/{decision_id}/decide",
            )
        )
        assert review.actor_role.value == "automated_gate"
        assert decision.actor_role.value == "automated_gate"

    plan = CreateDeploymentPlanRequest(
        **_post_payload(transport, "/api/deployment/plans")
    )
    dispatch = DispatchDeploymentPlanRequest(
        **_post_payload(
            transport,
            f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch",
        )
    )
    assert plan.rollback is not None
    assert plan.rollback.target_artifact_id == ids.baseline_strategy_artifact_id
    assert plan.rollback.target_version == ids.baseline_version
    assert plan.binding_id is None
    assert dispatch.saga_id == ids.deployment_saga_id

    # The forbidden client snapshots are gone from both wire requests; the owner
    # objects are resolved by exact ID from owner state, as Deployment does.
    assert not hasattr(plan, "registry_entry")
    assert not hasattr(plan, "approval_decision")
    assert not hasattr(dispatch, "registry_entry")
    owner_registry_entry = transport._owner_registry_entry(plan.registry_id)
    owner_approval_decision = transport.objects[
        ("governance", f"/api/governance/approvals/{plan.approval_decision_id}")
    ]
    assert owner_registry_entry["artifact_type"] == "execution_bundle"
    assert owner_registry_entry["approval_decision_id"] == plan.approval_decision_id
    assert owner_approval_decision["decision"] == "approved"
    assert owner_approval_decision["target_id"] == ids.strategy_artifact_id

    governance_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "governance"))
    sys.path.insert(0, governance_dir)
    from deployment_plan import DeploymentScale, RollbackRef, StagePlanner

    domain_plan = StagePlanner().create_plan(
        plan_id=plan.plan_id,
        approval_decision_id=plan.approval_decision_id,
        approval_decision=owner_approval_decision,
        registry_entry=owner_registry_entry,
        capital_pool_id=plan.capital_pool_id,
        target_stage=plan.target_stage.value,
        current_stage=plan.current_stage.value if plan.current_stage else None,
        created_by=plan.created_by,
        sponsor_persona_id=plan.sponsor_persona_id,
        binding_id=plan.binding_id,
        scale=(
            DeploymentScale(**plan.scale.model_dump()) if plan.scale is not None else None
        ),
        rollback=(
            RollbackRef(**plan.rollback.model_dump(mode="json"))
            if plan.rollback is not None
            else None
        ),
        pre_checks=list(plan.pre_checks),
        post_checks=list(plan.post_checks),
        metadata=plan.metadata,
        status=plan.status.value,
    )
    assert domain_plan.validate() == []


def test_response_loss_restart_and_duplicate_converge_without_duplicate_mutations() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    dispatch_path = f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch"
    transport = FakeOwnerTransport(
        response_loss={
            "/api/capital-pools",
            "/api/registry/strategy-specs",
            "/api/registry/strategy-artifacts",
            "/api/governance/approvals",
            f"/api/registry/strategy-specs/{ids.baseline_registry_id}/advance",
            (
                "/api/registry/strategy-artifacts/"
                f"{ids.baseline_strategy_artifact_id}/advance"
            ),
            "/api/bindings",
            dispatch_path,
        }
    )
    schedule_calls: list[tuple[str, str, str]] = []

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        schedule_calls.append((persona_id, pool_id, binding_id))
        return _schedule_receipt(persona_id, pool_id, binding_id)

    first = _coordinator(store, transport, registrar).coordinate(record)
    readback_started_at = first.references["provisioning_readback_started_at"]
    first_mutations = transport.mutations.copy()
    restarted = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="restarted-worker",
    )
    second = restarted.coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert first.state == second.state == "provisioning"
    assert first.result == second.result
    assert second.references["provisioning_readback_started_at"] == readback_started_at
    assert schedule_calls == [
        (record.persona_id, ids.capital_pool_id, ids.persona_capital_binding_id)
    ]
    assert transport.mutations == first_mutations
    assert transport.mutations[("deployment", dispatch_path)] == 1


def test_safe_early_failure_retries_without_replaying_committed_owner_writes() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    proposal_path = "/api/governance/approvals"
    transport = FakeOwnerTransport(mutation_failure={proposal_path})
    schedule_calls = 0

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        nonlocal schedule_calls
        schedule_calls += 1
        return _schedule_receipt(persona_id, pool_id, binding_id)

    first = _coordinator(store, transport, registrar).coordinate(record)

    assert first.state == "failed"
    assert first.error["failed_step"] == "baseline_approval_proposed"
    assert first.compensation is None
    assert "persona_capital_binding_created" not in first.references
    assert "deployment_plan" not in first.references

    transport.mutation_failure.remove(proposal_path)
    second = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="safe-retry-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert second.state == "provisioning"
    assert second.current_step == "schedule_registered"
    assert second.attempt_count == 2
    assert schedule_calls == 1
    assert transport.mutations[("capital", "/api/capital-pools")] == 1
    baseline_posts = [
        call
        for call in transport.calls
        if call[0] == "POST"
        and call[2] == "/api/registry/strategy-specs"
        and call[3]["registry_id"] == ids.baseline_registry_id
    ]
    assert len(baseline_posts) == 1


def test_compensation_reconciler_never_restarts_safe_early_failure() -> None:
    store, record = _record_and_store()
    proposal_path = "/api/governance/approvals"
    transport = FakeOwnerTransport(mutation_failure={proposal_path})

    first = _coordinator(store, transport, lambda *_args: {}).coordinate(record)
    mutations_after_failure = transport.mutations.copy()
    transport.mutation_failure.remove(proposal_path)

    reconciled = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=lambda *_args: {},
        lease_owner="compensation-only-worker",
    ).reconcile_failure_compensation(first)

    assert reconciled.state == "failed"
    assert reconciled.error["failed_step"] == "baseline_approval_proposed"
    assert reconciled.attempt_count == 1
    assert transport.mutations == mutations_after_failure


def test_unclassified_failed_record_stays_terminal_without_forward_replay() -> None:
    store, record = _record_and_store()
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="failed-record-writer",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "unclassified_failure"
    failed.error = {"failed_step": "unknown", "terminal_reason": "ambiguous state"}
    failed = store.checkpoint(
        failed,
        lease_owner="failed-record-writer",
        lease_seconds=60,
    )
    store.release(
        failed,
        lease_owner="failed-record-writer",
        lease_seconds=60,
    )
    transport = FakeOwnerTransport()
    schedule_called = False

    def registrar(*_args):
        nonlocal schedule_called
        schedule_called = True
        raise AssertionError("unclassified terminal failure must not replay")

    replay = _coordinator(store, transport, registrar).coordinate(record)

    assert replay.state == "failed"
    assert replay.attempt_count == 1
    assert transport.calls == []
    assert schedule_called is False


def test_compensation_response_loss_and_restart_converge_from_saga_readback() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    saga_path = f"/api/deployment/sagas/{ids.deployment_saga_id}"
    failure_path = f"{saga_path}/failure"
    transport = FakeOwnerTransport(response_loss={failure_path})
    schedule_calls = 0

    def registrar(*_args):
        nonlocal schedule_calls
        schedule_calls += 1
        raise ConnectionError("cron unavailable after dispatch")

    first = _coordinator(store, transport, registrar).coordinate(record)
    mutations_after_first = transport.mutations.copy()

    assert first.state == "failed"
    assert first.compensation["status"] == "pending"
    assert first.compensation["deployment"]["status"] == "requested"
    assert transport.mutations[("deployment", failure_path)] == 1

    restarted = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="compensation-restart-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert restarted.state == "failed"
    assert restarted.compensation["status"] == "pending"
    assert transport.mutations == mutations_after_first
    assert schedule_calls == 1

    saga = transport.objects[("deployment", saga_path)]
    saga.update(status="failed", current_step="compensated")
    terminal = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="compensation-terminal-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert terminal.state == "compensated"
    assert terminal.compensation["status"] == "completed"
    assert terminal.compensation["deployment"]["status"] == "completed"
    assert terminal.references["deployment_compensation_readback"]["current_step"] == (
        "compensated"
    )
    assert transport.mutations == mutations_after_first
    assert schedule_calls == 1


@pytest.mark.parametrize("failure_mode", ["dry_run", "unavailable"])
def test_schedule_failure_is_terminal_and_suspends_partial_admission(failure_mode: str) -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    schedule_calls = 0

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        nonlocal schedule_calls
        schedule_calls += 1
        if failure_mode == "unavailable":
            raise ConnectionError("cron gateway unavailable")
        return {
            "persona_id": persona_id,
            "capital_pool_id": pool_id,
            "binding_id": binding_id,
            "mode": "dry_run",
            "registered": [{"workflow_id": FIRST_EVALUATION_WORKFLOW_ID}],
            "skipped": [],
        }

    result = _coordinator(store, transport, registrar).coordinate(record)
    ids = deterministic_provisioning_ids(record)
    binding_path = f"/api/bindings/{ids.persona_capital_binding_id}"

    assert result.state == "failed"
    assert result.error["failed_step"] == "schedule_registration"
    assert result.error["terminal_reason"]
    assert result.compensation["status"] == "pending"
    assert result.compensation["action"] == "suspend_persona_capital_binding"
    assert result.compensation["deployment"]["status"] == "requested"
    assert result.references["deployment_compensation_readback"]["status"] == (
        "compensating"
    )
    assert result.references["persona_capital_binding_suspended"]["status"] == "suspended"
    assert transport.objects[("capital", binding_path)]["status"] == "suspended"
    assert transport.mutations[("capital", f"{binding_path}/status")] == 1

    mutations_after_failure = transport.mutations.copy()
    replay = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="terminal-replay-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert replay.state == "failed"
    assert replay.compensation["status"] == "pending"
    assert schedule_calls == 1
    assert transport.mutations == mutations_after_failure


def test_dry_run_is_pure_and_does_not_touch_store_transport_or_registrar() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    called = False

    def registrar(*_args):
        nonlocal called
        called = True
        raise AssertionError("dry-run must not call the schedule registrar")

    result = _coordinator(store, transport, registrar).coordinate(record, dry_run=True)

    assert result.current_step == "dry_run"
    assert result.result["mutations_performed"] is False
    assert result.result["ids"] == deterministic_provisioning_ids(record).to_dict()
    assert store.checkpoints == []
    assert store.get(record.tenant_id, record.idempotency_key).state == "reserved"
    assert transport.calls == []
    assert called is False


def test_activation_failure_revokes_pending_binding_fail_closed() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    activation_path = f"/api/bindings/{ids.persona_capital_binding_id}/activate"
    transport = FakeOwnerTransport(mutation_failure={activation_path})

    result = _coordinator(
        store,
        transport,
        lambda *_args: pytest.fail("schedule must not run after activation failure"),
    ).coordinate(record)

    binding_path = f"/api/bindings/{ids.persona_capital_binding_id}"
    assert result.state == "compensated"
    assert result.error["failed_step"] == "persona_capital_binding_active"
    assert result.compensation["action"] == "revoke_pending_persona_capital_binding"
    assert result.compensation["resulting_status"] == "revoked"
    assert result.references["persona_capital_binding_revoked"]["status"] == "revoked"
    assert transport.objects[("capital", binding_path)]["status"] == "revoked"


def test_schedule_exact_authoritative_readback_accepts_job_name_only_skips() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        return {
            "persona_id": persona_id,
            "capital_pool_id": pool_id,
            "binding_id": binding_id,
            "mode": "gateway_rpc",
            "registered": [],
            "skipped": ["pantheon-persona-first-evaluation-persona-a"],
            "authoritative_readback": {
                "persona_id": persona_id,
                "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
                "registered": True,
            },
        }

    result = _coordinator(store, transport, registrar).coordinate(record)

    assert result.state == "provisioning"
    assert result.current_step == "schedule_registered"


def test_safe_early_failure_name_error_capital_pool_replays_successfully() -> None:
    """Verify exact historical failure (NameError at capital_pool with failed_step present,
    empty references, null compensation) safely replays through forward coordination.
    """
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)

    # Seed the exact historical failure structure:
    # error dict has error_type=NameError, terminal_reason, and failed_step=capital_pool
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="prior-failed-run",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "capital_pool_failed"
    failed.error = {
        "failed_at": "2026-09-04T11:52:48Z",
        "error_type": "NameError",
        "failed_step": "capital_pool",
        "terminal_reason": "name 'urllib_error' is not defined",
        "compensation_error": "name 'urllib_error' is not defined",
    }
    failed.references = {}
    failed.compensation = None
    failed.attempt_count = 4
    failed = store.checkpoint(failed, lease_owner="prior-failed-run", lease_seconds=60)
    store.release(failed, lease_owner="prior-failed-run", lease_seconds=60)

    transport = FakeOwnerTransport()
    schedule_calls: list[tuple[str, str, str]] = []

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        schedule_calls.append((persona_id, pool_id, binding_id))
        return _schedule_receipt(persona_id, pool_id, binding_id)

    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="replay-coordinator-1",
    )

    replayed = coordinator.coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert replayed.state == "provisioning"
    assert replayed.current_step == "schedule_registered"
    assert replayed.error is None
    assert replayed.compensation is None
    assert replayed.attempt_count == 5
    assert len(schedule_calls) == 1
    assert "capital_pool" in replayed.references
    assert "persona_capital_binding_created" in replayed.references
    assert "deployment_dispatch" in replayed.references
    assert "first_evaluation_schedule" in replayed.references


def test_safe_early_failure_rejects_unsafe_binding_side_effects() -> None:
    """Fail-closed: records with committed binding references must NEVER forward replay."""
    store, record = _record_and_store()
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="unsafe-record-writer",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "persona_capital_binding_created_failed"
    failed.error = {
        "failed_step": "persona_capital_binding_created",
        "terminal_reason": "connection timeout during binding creation",
    }
    failed.references = {
        "capital_pool": {"pool_id": "pool-1", "status": "active"},
        "persona_capital_binding_created": {"binding_id": "pcb-1", "status": "pending"},
    }
    failed.compensation = None
    failed = store.checkpoint(failed, lease_owner="unsafe-record-writer", lease_seconds=60)
    store.release(failed, lease_owner="unsafe-record-writer", lease_seconds=60)

    transport = FakeOwnerTransport()
    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=lambda *_args: pytest.fail("Must not schedule"),
        lease_owner="replay-attempt",
    )

    result = coordinator.coordinate(store.get(record.tenant_id, record.idempotency_key))

    # Must remain terminal and NOT perform forward provisioning
    assert result.state in {"failed", "compensated"}
    assert "deployment_dispatch" not in result.references
    assert "schedule_registration" not in result.references


def test_safe_early_failure_rejects_non_null_compensation() -> None:
    """Fail-closed: records with existing compensation must NOT forward replay."""
    store, record = _record_and_store()
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="compensated-writer",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "capital_pool_failed"
    failed.error = {
        "failed_step": "capital_pool",
        "terminal_reason": "explicit rejection",
    }
    failed.references = {}
    failed.compensation = {"status": "completed", "action": "noop"}
    failed = store.checkpoint(failed, lease_owner="compensated-writer", lease_seconds=60)
    store.release(failed, lease_owner="compensated-writer", lease_seconds=60)

    transport = FakeOwnerTransport()
    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=lambda *_args: pytest.fail("Must not schedule"),
        lease_owner="replay-attempt",
    )

    result = coordinator.coordinate(store.get(record.tenant_id, record.idempotency_key))
    assert result.state == "failed"
    assert result.compensation == {"status": "completed", "action": "noop"}
    assert "capital_pool" not in result.references
