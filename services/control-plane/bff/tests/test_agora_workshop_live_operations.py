"""Focused integration tests for Strategy Workshop live-operation routes.

The router is assembled with an in-memory workshop store and a narrow fake for
the canonical Registry, Research, Consultation, and Approval authorities.  The
fake intentionally has no execution authority: deploy/order calls fail the
test immediately if a workshop route ever attempts one.
"""
from __future__ import annotations

import copy
import os
import sys
import uuid
from typing import Any, Dict, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


from services.control_plane.bff.agora.strategy_workshop import MemoryWorkshopStore  # noqa: E402
from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError  # noqa: E402
from services.control_plane.bff.agora.strategy_workshop.router import create_strategy_workshop_router  # noqa: E402
from services.research.strategy_spec.models import (  # noqa: E402
    validate_strategy_spec_payload,
)
from services.research.strategy_spec.patching import (  # noqa: E402
    compute_document_sha256,
)


WORKSHOP_ID = "ws-live-operations"
TENANT_ID = "tenant-alpha"
USER_ID = "user-alpha"
STRATEGY_ID = "strat-workshop-live"
BASE_REGISTRY_ID = "reg-workshop-base"
NOW = "2026-07-15T12:00:00Z"


def _base_strategy_spec() -> Dict[str, Any]:
    """Return a minimal document accepted by the canonical StrategySpec schema."""

    return {
        "spec_version": "1.0",
        "strategy_id": STRATEGY_ID,
        "title": "Workshop Base Strategy",
        "hypothesis": "Markets exhibit persistent medium-term momentum.",
        "objective": "Evaluate the hypothesis in a research-only environment.",
        "lifecycle_state": "draft",
        "market_scope": {
            "symbols": ["RESEARCH_UNIVERSE"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {"ref": "dataset:research-equities-v1", "kind": "dataset"},
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {"metrics": ["sharpe_ratio"]},
        "governance": {
            "approval_required": True,
            "policy_id": "research-only-v1",
        },
        "provenance": {
            "source_kind": "manual",
            "created_at": NOW,
        },
        "metadata": {"fixture": "live-operation-router"},
    }


class FakeCanonicalOperations:
    """Canonical adapter fake with authoritative readbacks and call recording."""

    def __init__(self) -> None:
        base_spec = _base_strategy_spec()
        validate_strategy_spec_payload(base_spec)
        self.registry: Dict[str, Dict[str, Any]] = {
            BASE_REGISTRY_ID: {
                "entry": {
                    "registry_id": BASE_REGISTRY_ID,
                    "strategy_id": STRATEGY_ID,
                    "version": "1.0.0",
                    "artifact_state": "draft",
                    "lineage": {"parent_registry_ids": []},
                    "metadata": {"strategy_spec": base_spec},
                }
            }
        }
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.calls: list[tuple[str, Dict[str, Any]]] = []
        self.research_status = "queued"
        self.raise_research_error = False
        self.research_error: Optional[CanonicalOperationError] = None
        self.consultation_error: Optional[CanonicalOperationError] = None
        self.consultation_status = "submitted"
        self.forbidden_execution_calls: list[str] = []

    def _record(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.calls.append((name, copy.deepcopy(payload or {})))

    def call_count(self, name: str) -> int:
        return sum(call_name == name for call_name, _ in self.calls)

    def get_strategy_spec(self, registry_id: str) -> Dict[str, Any]:
        self._record("get_strategy_spec", {"registry_id": registry_id})
        readback = self.registry.get(registry_id)
        if readback is None:
            raise CanonicalOperationError(
                "strategy_registry",
                "StrategySpec was not found",
                status_code=404,
            )
        return copy.deepcopy(readback)

    def create_strategy_spec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._record("create_strategy_spec", payload)
        document = copy.deepcopy(payload["strategy_spec"])
        validate_strategy_spec_payload(document)
        registry_id = str(payload["registry_id"])
        self.registry[registry_id] = {
            "entry": {
                "registry_id": registry_id,
                "strategy_id": payload["strategy_id"],
                "version": payload["version"],
                "artifact_state": payload["artifact_state"],
                "lineage": copy.deepcopy(payload["lineage"]),
                "metadata": {
                    **copy.deepcopy(payload["metadata"]),
                    "strategy_spec": document,
                    "authoritative_readback": True,
                },
            }
        }
        return self.get_strategy_spec(registry_id)

    def add_approval(
        self,
        decision_id: str,
        *,
        version_id: str,
        approver: str = "reviewer-beta",
    ) -> None:
        self.approvals[decision_id] = {
            "decision_id": decision_id,
            "outcome": "approved",
            "state": "decided",
            "tenant_id": TENANT_ID,
            "owner_user_id": USER_ID,
            "target_type": "strategy_workshop",
            "target_id": WORKSHOP_ID,
            "target_version": version_id,
            "reviewer": approver,
        }

    def get_approval_decision(self, decision_id: str) -> Dict[str, Any]:
        self._record("get_approval_decision", {"decision_id": decision_id})
        decision = self.approvals.get(decision_id)
        if decision is None:
            raise CanonicalOperationError(
                "approval_decision_store",
                "approval decision was not found",
                status_code=404,
            )
        return copy.deepcopy(decision)

    def dispatch_research_run(
        self,
        *,
        task_payload: Dict[str, Any],
        run_payload: Dict[str, Any],
        resume: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._record(
            "dispatch_research_run",
            {
                "task_payload": task_payload,
                "run_payload": run_payload,
                "resume": copy.deepcopy(resume),
            },
        )
        if self.raise_research_error:
            raise CanonicalOperationError(
                "research_orchestrator",
                "research service unavailable",
                retryable=True,
            )
        if self.research_error is not None:
            error, self.research_error = self.research_error, None
            raise error
        return {
            "task": {
                "task_id": "research-task-001",
                "status": "accepted",
                "authoritative_readback": True,
            },
            "run": {
                "run_id": "research-run-001",
                "task_id": "research-task-001",
                "status": self.research_status,
                "authoritative_readback": True,
            },
        }

    def open_consultation(
        self,
        *,
        request_id: str,
        payload: Dict[str, Any],
        resume: bool = False,
    ) -> Dict[str, Any]:
        self._record(
            "open_consultation",
            {"request_id": request_id, "payload": payload, "resume": resume},
        )
        if self.consultation_error is not None:
            error, self.consultation_error = self.consultation_error, None
            raise error
        return {
            "request_id": request_id,
            "status": self.consultation_status,
            "target_id": WORKSHOP_ID,
            "authoritative_readback": True,
        }

    def cancel_consultation(
        self,
        request_id: str,
        *,
        actor_id: str,
        trace_id: str,
    ) -> None:
        self._record(
            "cancel_consultation",
            {
                "request_id": request_id,
                "actor_id": actor_id,
                "trace_id": trace_id,
            },
        )

    # These methods are deliberately outside the canonical workshop adapter.
    # If a future router reaches for them, the test must fail immediately.
    def deploy(self, *_args: Any, **_kwargs: Any) -> None:
        self.forbidden_execution_calls.append("deploy")
        raise AssertionError("Strategy Workshop must not deploy")

    def submit_order(self, *_args: Any, **_kwargs: Any) -> None:
        self.forbidden_execution_calls.append("submit_order")
        raise AssertionError("Strategy Workshop must not submit orders")


class ConsultationCommitFailingStore(MemoryWorkshopStore):
    """Simulate loss of the local projection after canonical consultation open."""

    def complete_command(self, **kwargs: Any) -> Dict[str, Any]:
        if kwargs.get("operation") == "open_consultation":
            return {
                "outcome": "projection_unavailable",
                "receipt": self.get_command_receipt(
                    workshop_id=kwargs["workshop_id"],
                    tenant_id=kwargs["tenant_id"],
                    user_id=kwargs["user_id"],
                    operation=kwargs["operation"],
                    idempotency_key=kwargs["idempotency_key"],
                ),
            }
        return super().complete_command(**kwargs)


def _bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: str,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
    **_kwargs: Any,
) -> HTTPException:
    details: Dict[str, Any] = {"reason": reason}
    if precondition_failed is not None:
        details["precondition_failed"] = precondition_failed
    if suggestion is not None:
        details["suggestion"] = suggestion
    details.update(details_extra or {})
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": getattr(code, "value", str(code)),
                "message": message,
                "details": details,
            }
        },
    )


def _harness(
    *,
    store: Optional[MemoryWorkshopStore] = None,
) -> tuple[TestClient, MemoryWorkshopStore, FakeCanonicalOperations]:
    workshop_store = store or MemoryWorkshopStore()
    if workshop_store.get_session(WORKSHOP_ID) is None:
        # Reusing a populated store simulates a BFF restart: receipts,
        # version links, and the session survive; only process state resets.
        workshop_store.create_session(
            {
                "workshop_id": WORKSHOP_ID,
                "tenant_id": TENANT_ID,
                "user_id": USER_ID,
                "strategy_id": STRATEGY_ID,
                "active_strategy_spec_registry_id": BASE_REGISTRY_ID,
                "status": "open",
            }
        )
    canonical = FakeCanonicalOperations()
    identity = {
        "operator_id": USER_ID,
        "roles": ["operator"],
        "token_kind": "test",
        "mfa_verified": False,
        "claims": {
            "tenant_id": TENANT_ID,
            "allowed_tenants": [TENANT_ID, "tenant-beta"],
            "user_id": USER_ID,
        },
    }
    app = FastAPI()
    app.include_router(
        create_strategy_workshop_router(
            extract_identity=lambda _authorization, **_kwargs: identity,
            require_read_role=lambda _identity: None,
            require_write_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: NOW,
            workshop_store=workshop_store,
            canonical_operations=canonical,
        )
    )
    return (
        TestClient(app, raise_server_exceptions=False),
        workshop_store,
        canonical,
    )


def _command_headers(
    idempotency_key: str,
    etag: str,
    *,
    tenant_id: str = TENANT_ID,
    mfa: bool = True,
) -> Dict[str, str]:
    headers = {
        "Authorization": "Bearer workshop-test",
        "X-Tenant-Id": tenant_id,
        "If-Match": etag,
        "Idempotency-Key": idempotency_key,
        "X-Request-Id": f"request-{idempotency_key}",
    }
    if mfa:
        headers["X-MFA-Token"] = "mfa-test-proof"
    return headers


def _etag(store: MemoryWorkshopStore) -> str:
    session = store.get_session(WORKSHOP_ID)
    assert session is not None
    return f'W/"workshop:{WORKSHOP_ID}:v{session["lock_version"]}"'


def _version_body(title: str = "Workshop Candidate Version") -> Dict[str, Any]:
    base = _base_strategy_spec()
    return {
        "patch": [{"op": "replace", "path": "/title", "value": title}],
        "base_document_sha256": compute_document_sha256(base),
        "reason": "Refine the workshop research candidate",
    }


def _create_version(
    client: TestClient,
    store: MemoryWorkshopStore,
    *,
    key: str = "create-version-001",
    body: Optional[Dict[str, Any]] = None,
) -> tuple[Any, str]:
    response = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=_command_headers(key, _etag(store)),
        json=body or _version_body(),
    )
    assert response.status_code == 201, response.text
    version_id = response.json()["data"]["resource"]["version"][
        "workshop_version_id"
    ]
    return response, version_id


def _research_body(version_id: str, approval_id: str) -> Dict[str, Any]:
    return {
        "research_context": "Validate momentum without any live execution.",
        "strategy_version_ref": version_id,
        "parameters": {"environment": "research"},
        "approval_decision_id": approval_id,
        "adapter": "handoff_only",
        "requested_mode": "handoff_only",
        "dispatch_mode": "handoff_only",
    }


def _reason(response: Any) -> str:
    return response.json()["detail"]["error"]["details"]["reason"]


def test_all_six_operations_read_back_canonical_state_and_never_execute() -> None:
    client, store, canonical = _harness()

    created, version_id = _create_version(client, store)
    created_resource = created.json()["data"]["resource"]
    registry_id = created_resource["version"]["strategy_spec_registry_id"]
    assert created_resource["strategy_spec"]["entry"]["metadata"][
        "authoritative_readback"
    ] is True
    assert created_resource["strategy_spec"]["entry"]["version"] == "1.0.1"
    assert created_resource["strategy_spec"]["entry"]["metadata"][
        "strategy_spec"
    ]["spec_version"] == "1.0"

    listed = client.get(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers={
            "Authorization": "Bearer workshop-test",
            "X-Tenant-Id": TENANT_ID,
            "X-Request-Id": "request-list-versions",
        },
    )
    assert listed.status_code == 200, listed.text
    versions = listed.json()["data"]["versions"]
    assert len(versions) == 2
    legacy_base, created_readback = versions
    assert created_readback == created_resource
    assert legacy_base["version"]["strategy_spec_registry_id"] == BASE_REGISTRY_ID
    assert legacy_base["version"]["document_sha256"] == compute_document_sha256(
        _base_strategy_spec()
    )
    assert created_resource["version"]["parent_workshop_version_id"] == (
        legacy_base["version"]["workshop_version_id"]
    )
    assert created_resource["version"]["document_sha256"] == (
        compute_document_sha256(
            created_resource["strategy_spec"]["entry"]["metadata"]["strategy_spec"]
        )
    )

    selected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
        headers=_command_headers("select-version-001", created.headers["etag"]),
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["resource"]["workshop"][
        "selected_version_id"
    ] == version_id

    canonical.add_approval("approval-research", version_id=version_id)
    research = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-001", selected.headers["etag"]),
        json=_research_body(version_id, "approval-research"),
    )
    assert research.status_code == 202, research.text
    research_resource = research.json()["data"]["resource"]
    assert research_resource["run"]["run_id"] == "research-run-001"
    assert research_resource["run"]["authoritative_readback"] is True
    dispatch_call = next(
        payload for name, payload in canonical.calls if name == "dispatch_research_run"
    )
    assert dispatch_call["task_payload"]["constraints"]["no_live_capital"] is True
    assert dispatch_call["run_payload"]["dispatch_mode"] == "handoff_only"

    consultation = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consultation-001", research.headers["etag"]),
        json={
            "consultation_type": "committee",
            "subject": "Review the research-only workshop candidate",
            "context_refs": ["evidence:bundle-001"],
        },
    )
    assert consultation.status_code == 201, consultation.text
    assert consultation.json()["data"]["resource"]["consultation"][
        "authoritative_readback"
    ] is True

    canonical.add_approval("approval-conclude", version_id=version_id)
    concluded = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
        headers=_command_headers("conclude-001", consultation.headers["etag"]),
        json={
            "final_version_id": version_id,
            "conclusion_notes": "Approved as a research candidate only.",
            "approval_decision_id": "approval-conclude",
        },
    )
    assert concluded.status_code == 200, concluded.text
    concluded_body = concluded.json()
    assert concluded_body["data"]["resource"]["workshop"]["status"] == "concluded"
    assert concluded_body["data"]["resource"]["two_person_proof"] == {
        "approval_decision_id": "approval-conclude",
        "requested_by": USER_ID,
        "approved_by": "reviewer-beta",
        "distinct_actors": True,
        "decision": "approved",
    }
    assert concluded_body["meta"]["no_direct_action"] == {
        "deployment_triggered": False,
        "order_submitted": False,
        "live_capital_changed": False,
    }
    assert canonical.registry[registry_id]["entry"]["registry_id"] == registry_id
    assert canonical.forbidden_execution_calls == []
    assert canonical.call_count("create_strategy_spec") == 1
    assert canonical.call_count("dispatch_research_run") == 1
    assert canonical.call_count("open_consultation") == 1

    # The terminal aggregate rejects every mutation before a new downstream
    # StrategySpec, research run, or consultation can be created.
    terminal_etag = concluded.headers["etag"]
    canonical.add_approval("approval-after-conclude", version_id=version_id)
    attempts = [
        client.post(
            f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
            headers=_command_headers("terminal-create", terminal_etag),
            json=_version_body("Must Not Be Created"),
        ),
        client.post(
            f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
            headers=_command_headers("terminal-select", terminal_etag),
        ),
        client.post(
            f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
            headers=_command_headers("terminal-research", terminal_etag),
            json=_research_body(version_id, "approval-after-conclude"),
        ),
        client.post(
            f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
            headers=_command_headers("terminal-consult", terminal_etag),
            json={
                "consultation_type": "advisory",
                "subject": "Must not open",
            },
        ),
        client.post(
            f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
            headers=_command_headers("terminal-conclude", terminal_etag),
            json={
                "final_version_id": version_id,
                "approval_decision_id": "approval-after-conclude",
            },
        ),
    ]
    assert [response.status_code for response in attempts] == [409, 409, 409, 409, 409]
    assert canonical.call_count("create_strategy_spec") == 1
    assert canonical.call_count("dispatch_research_run") == 1
    assert canonical.call_count("open_consultation") == 1
    assert canonical.forbidden_execution_calls == []


def test_exact_replay_is_stable_and_changed_body_conflicts() -> None:
    client, store, canonical = _harness()
    body = _version_body()
    headers = _command_headers("replay-version-001", _etag(store))

    first = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=headers,
        json=body,
    )
    replay = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=headers,
        json=body,
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["etag"] == first.headers["etag"]
    assert canonical.call_count("create_strategy_spec") == 1

    changed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=headers,
        json=_version_body("Same Key, Different Document"),
    )
    assert changed.status_code == 409, changed.text
    assert _reason(changed) == "IDEMPOTENCY_REQUEST_HASH_MISMATCH"
    assert canonical.call_count("create_strategy_spec") == 1


def test_command_guards_reject_stale_etag_wrong_tenant_and_missing_mfa() -> None:
    stale_client, stale_store, stale_canonical = _harness()
    stale = stale_client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=_command_headers(
            "guard-stale",
            f'W/"workshop:{WORKSHOP_ID}:v0"',
        ),
        json=_version_body(),
    )
    assert stale.status_code == 409, stale.text
    assert _reason(stale) == "CONCURRENT_MODIFICATION"
    details = stale.json()["detail"]["error"]["details"]
    assert details["current_etag"] == _etag(stale_store)
    assert stale_canonical.calls == []

    tenant_client, tenant_store, tenant_canonical = _harness()
    wrong_tenant = tenant_client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=_command_headers(
            "guard-tenant",
            _etag(tenant_store),
            tenant_id="tenant-beta",
        ),
        json=_version_body(),
    )
    assert wrong_tenant.status_code == 403, wrong_tenant.text
    assert _reason(wrong_tenant) == "CROSS_USER_ACCESS_FORBIDDEN"
    assert tenant_canonical.calls == []

    mfa_client, mfa_store, mfa_canonical = _harness()
    missing_mfa = mfa_client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers=_command_headers("guard-mfa", _etag(mfa_store), mfa=False),
        json=_version_body(),
    )
    assert missing_mfa.status_code == 401, missing_mfa.text
    assert _reason(missing_mfa) == "MFA_REQUIRED"
    assert mfa_canonical.calls == []


def test_version_read_scope_and_select_cas_leave_projection_unchanged() -> None:
    tenant_client, _tenant_store, tenant_canonical = _harness()
    denied = tenant_client.get(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers={
            "Authorization": "Bearer workshop-test",
            "X-Tenant-Id": "tenant-beta",
        },
    )
    assert denied.status_code == 403, denied.text
    assert _reason(denied) == "CROSS_USER_ACCESS_FORBIDDEN"
    assert tenant_canonical.calls == []

    client, store, _canonical = _harness()
    created, version_id = _create_version(client, store, key="select-cas-create")
    selected_before = store.get_session(WORKSHOP_ID)["selected_version_id"]
    stale = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
        headers=_command_headers(
            "select-cas-stale",
            f'W/"workshop:{WORKSHOP_ID}:v1"',
        ),
    )
    assert stale.status_code == 409, stale.text
    assert _reason(stale) == "CONCURRENT_MODIFICATION"
    assert store.get_session(WORKSHOP_ID)["selected_version_id"] == selected_before
    assert store.get_session(WORKSHOP_ID)["lock_version"] == 2
    assert created.headers["etag"] == f'W/"workshop:{WORKSHOP_ID}:v2"'


def test_immutable_version_digest_rejects_changed_registry_bytes() -> None:
    client, store, canonical = _harness()
    created, _version_id = _create_version(client, store, key="digest-create")
    registry_id = created.json()["data"]["resource"]["version"][
        "strategy_spec_registry_id"
    ]
    canonical.registry[registry_id]["entry"]["metadata"]["strategy_spec"][
        "title"
    ] = "Mutated behind immutable Registry identity"

    listed = client.get(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions",
        headers={
            "Authorization": "Bearer workshop-test",
            "X-Tenant-Id": TENANT_ID,
        },
    )
    assert listed.status_code == 409, listed.text
    assert _reason(listed) == "WORKSHOP_VERSION_PROJECTION_CONFLICT"


def test_research_requires_an_authoritative_distinct_approver() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store)
    headers = _command_headers("approval-guard", created.headers["etag"])

    omitted = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=headers,
        json={
            key: value
            for key, value in _research_body(version_id, "unused").items()
            if key != "approval_decision_id"
        },
    )
    assert omitted.status_code == 422, omitted.text

    missing = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=headers,
        json=_research_body(version_id, "approval-missing"),
    )
    assert missing.status_code == 409, missing.text
    assert _reason(missing) == "approval decision was not found"

    canonical.add_approval(
        "approval-same-actor",
        version_id=version_id,
        approver=USER_ID,
    )
    same_actor = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=headers,
        json=_research_body(version_id, "approval-same-actor"),
    )
    assert same_actor.status_code == 403, same_actor.text
    assert _reason(same_actor) == "APPROVAL_DISTINCT_ACTOR_REQUIRED"
    assert canonical.call_count("dispatch_research_run") == 0


def test_downstream_rejection_has_durable_failure_receipt_and_safe_compensation() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store)
    canonical.add_approval("approval-rejected-run", version_id=version_id)
    canonical.research_status = "rejected"
    headers = _command_headers("research-rejected", created.headers["etag"])
    body = _research_body(version_id, "approval-rejected-run")

    rejected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=headers,
        json=body,
    )
    assert rejected.status_code == 409, rejected.text
    assert _reason(rejected) == "RESEARCH_RUN_REJECTED"

    receipt = store.get_command_receipt(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="dispatch_research",
        idempotency_key="research-rejected",
    )
    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["failure"]["reason"] == "RESEARCH_RUN_REJECTED"
    assert receipt["compensation"] == {"workshop_effect": "none"}

    replay = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=headers,
        json=body,
    )
    assert replay.status_code == 409, replay.text
    assert _reason(replay) == "COMMAND_PREVIOUSLY_FAILED"
    assert canonical.call_count("dispatch_research_run") == 1
    assert canonical.forbidden_execution_calls == []


def test_conclude_refuses_missing_and_unselected_final_versions() -> None:
    client, store, canonical = _harness()
    _created_a, version_a = _create_version(client, store, key="conclude-guard-a")
    created_b, version_b = _create_version(
        client,
        store,
        key="conclude-guard-b",
        body=_version_body("Second Candidate"),
    )
    selected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_b}/select",
        headers=_command_headers("conclude-guard-select", created_b.headers["etag"]),
    )
    assert selected.status_code == 200, selected.text
    canonical.add_approval("approval-guard-conclude", version_id=version_a)
    baseline_readbacks = canonical.call_count("get_strategy_spec")

    missing = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
        headers=_command_headers("conclude-guard-missing", selected.headers["etag"]),
        json={
            "final_version_id": "wsv-does-not-exist",
            "approval_decision_id": "approval-guard-conclude",
        },
    )
    assert missing.status_code == 409, missing.text
    assert _reason(missing) == "WORKSHOP_VERSION_REQUIRED"

    unselected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
        headers=_command_headers("conclude-guard-unselected", selected.headers["etag"]),
        json={
            "final_version_id": version_a,
            "approval_decision_id": "approval-guard-conclude",
        },
    )
    assert unselected.status_code == 409, unselected.text
    assert _reason(unselected) == "WORKSHOP_FINAL_VERSION_NOT_SELECTED"
    details = unselected.json()["detail"]["error"]["details"]
    assert details["selected_version_id"] == version_b

    cross_tenant = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
        headers=_command_headers(
            "conclude-guard-tenant",
            selected.headers["etag"],
            tenant_id="tenant-beta",
        ),
        json={
            "final_version_id": version_b,
            "approval_decision_id": "approval-guard-conclude",
        },
    )
    assert cross_tenant.status_code == 403, cross_tenant.text
    assert _reason(cross_tenant) == "CROSS_USER_ACCESS_FORBIDDEN"

    # None of the refused conclusions admitted a command or touched the
    # Registry; the workshop is still reviewable.
    session = store.get_session(WORKSHOP_ID)
    assert session["status"] == "in_review"
    assert session["selected_version_id"] == version_b
    assert canonical.call_count("get_strategy_spec") == baseline_readbacks
    assert canonical.forbidden_execution_calls == []


def test_conclude_refuses_mutated_or_incomplete_final_version_with_durable_receipt() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="conclude-digest-create")
    registry_id = created.json()["data"]["resource"]["version"][
        "strategy_spec_registry_id"
    ]
    selected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
        headers=_command_headers("conclude-digest-select", created.headers["etag"]),
    )
    assert selected.status_code == 200, selected.text
    canonical.add_approval("approval-digest-conclude", version_id=version_id)
    canonical.registry[registry_id]["entry"]["metadata"]["strategy_spec"][
        "title"
    ] = "Mutated behind immutable Registry identity"

    mutated = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
        headers=_command_headers("conclude-digest-key", selected.headers["etag"]),
        json={
            "final_version_id": version_id,
            "approval_decision_id": "approval-digest-conclude",
        },
    )
    assert mutated.status_code == 409, mutated.text
    assert _reason(mutated) == "WORKSHOP_VERSION_PROJECTION_CONFLICT"

    receipt = store.get_command_receipt(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="conclude",
        idempotency_key="conclude-digest-key",
    )
    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["failure"]["reason"] == "WORKSHOP_VERSION_PROJECTION_CONFLICT"
    session = store.get_session(WORKSHOP_ID)
    assert session["status"] == "in_review"
    assert session.get("final_workshop_version_id") is None

    # An incomplete Registry readback (document removed) is also refused with
    # its own durable failed receipt.
    canonical.registry[registry_id]["entry"]["metadata"].pop("strategy_spec")
    incomplete = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/conclude",
        headers=_command_headers("conclude-incomplete-key", _etag(store)),
        json={
            "final_version_id": version_id,
            "approval_decision_id": "approval-digest-conclude",
        },
    )
    assert incomplete.status_code == 502, incomplete.text
    assert _reason(incomplete) == "STRATEGY_SPEC_DOCUMENT_REQUIRED"
    incomplete_receipt = store.get_command_receipt(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="conclude",
        idempotency_key="conclude-incomplete-key",
    )
    assert incomplete_receipt is not None
    assert incomplete_receipt["status"] == "failed"
    assert store.get_session(WORKSHOP_ID)["status"] == "in_review"
    assert canonical.forbidden_execution_calls == []


def test_research_timeout_is_durable_and_new_key_retry_succeeds() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="research-timeout-create")
    canonical.add_approval("approval-timeout-run", version_id=version_id)
    canonical.raise_research_error = True
    body = _research_body(version_id, "approval-timeout-run")

    timed_out = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-timeout", created.headers["etag"]),
        json=body,
    )
    assert timed_out.status_code == 503, timed_out.text
    assert _reason(timed_out) == "research service unavailable"

    receipt = store.get_command_receipt(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="dispatch_research",
        idempotency_key="research-timeout",
    )
    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["failure"]["retryable"] is True

    same_key = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-timeout", _etag(store)),
        json=body,
    )
    assert same_key.status_code == 409, same_key.text
    assert _reason(same_key) == "COMMAND_PREVIOUSLY_FAILED"

    canonical.raise_research_error = False
    retried = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-timeout-retry", _etag(store)),
        json=body,
    )
    assert retried.status_code == 202, retried.text
    retried_resource = retried.json()["data"]["resource"]
    assert retried_resource["run"]["run_id"] == "research-run-001"
    assert retried_resource["downstream_terminal"] is False
    assert canonical.call_count("dispatch_research_run") == 2
    assert canonical.forbidden_execution_calls == []


def test_consultation_projection_failure_cancels_downstream_and_keeps_receipt() -> None:
    client, store, canonical = _harness(store=ConsultationCommitFailingStore())
    created, version_id = _create_version(client, store)
    selected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
        headers=_command_headers("select-before-failure", created.headers["etag"]),
    )
    assert selected.status_code == 200, selected.text
    headers = _command_headers("consult-projection-failure", selected.headers["etag"])
    body = {
        "consultation_type": "advisory",
        "subject": "Open then compensate after projection failure",
    }

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=headers,
        json=body,
    )
    assert failed.status_code == 503, failed.text
    assert _reason(failed) == "WORKSHOP_COMMIT_FAILED"
    assert canonical.call_count("open_consultation") == 1
    assert canonical.call_count("cancel_consultation") == 1

    receipt = store.get_command_receipt(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        idempotency_key="consult-projection-failure",
    )
    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["failure"] == {"reason": "WORKSHOP_COMMIT_FAILED"}
    assert receipt["compensation"]["required"] is True
    assert receipt["compensation"]["canonical_refs"][
        "consultation_request_id"
    ].startswith("cr-ws-")
    # Successful downstream cancellation seals the compensation: the failed
    # attempt is resolved and is no longer resumable partial-effect lineage.
    assert receipt["compensation"]["resolution"] == "cancelled"
    assert receipt["compensation"]["resolved_at"]
    assert store.find_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        request_hash=receipt["request_hash"],
    ) is None

    replay = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=headers,
        json=body,
    )
    assert replay.status_code == 409, replay.text
    assert _reason(replay) == "COMMAND_PREVIOUSLY_FAILED"
    assert canonical.call_count("open_consultation") == 1
    assert canonical.call_count("cancel_consultation") == 1
    assert canonical.forbidden_execution_calls == []


def _last_call(canonical: FakeCanonicalOperations, name: str) -> Dict[str, Any]:
    return next(
        payload for call_name, payload in reversed(canonical.calls) if call_name == name
    )


def _receipt(
    store: MemoryWorkshopStore, operation: str, idempotency_key: str
) -> Dict[str, Any]:
    receipt = store.get_command_receipt(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    assert receipt is not None
    return receipt


def test_research_partial_failure_records_lineage_and_new_key_retry_resumes() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="research-partial-create")
    canonical.add_approval("approval-partial-run", version_id=version_id)
    body = _research_body(version_id, "approval-partial-run")
    canonical.research_error = CanonicalOperationError(
        "research_orchestrator",
        "canonical research dispatch response is missing run_id",
        retryable=True,
        partial_effects={"research_task_id": "research-task-001"},
    )

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-partial", created.headers["etag"]),
        json=body,
    )
    assert failed.status_code == 503, failed.text
    first_task_key = _last_call(canonical, "dispatch_research_run")[
        "task_payload"
    ]["idempotency_key"]

    # The failed receipt carries durable partial-effect lineage: the created
    # downstream task id and a resumable compensation with the downstream
    # idempotency digest.
    receipt = _receipt(store, "dispatch_research", "research-partial")
    assert receipt["status"] == "failed"
    assert receipt["canonical_refs"] == {"research_task_id": "research-task-001"}
    assert receipt["compensation"]["resumable"] is True
    assert receipt["compensation"]["partial_effects"] == {
        "research_task_id": "research-task-001"
    }
    assert receipt["compensation"]["downstream_idempotency_digest"]

    retried = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-partial-retry", _etag(store)),
        json=body,
    )
    assert retried.status_code == 202, retried.text
    retry_call = _last_call(canonical, "dispatch_research_run")
    # The retry adopts the recorded task instead of creating a duplicate and
    # reuses the same downstream idempotency key.
    assert retry_call["resume"] == {
        "research_task_id": "research-task-001",
        "research_run_id": None,
    }
    assert retry_call["task_payload"]["idempotency_key"] == first_task_key

    retry_receipt = _receipt(store, "dispatch_research", "research-partial-retry")
    assert retry_receipt["status"] == "completed"
    assert retry_receipt["canonical_refs"]["research_task_id"] == "research-task-001"
    assert (
        retry_receipt["canonical_refs"]["resumed_from_idempotency_key"]
        == "research-partial"
    )

    resolved = _receipt(store, "dispatch_research", "research-partial")
    assert resolved["compensation"]["resolution"] == "resumed"
    assert (
        resolved["compensation"]["resolved_by_idempotency_key"]
        == "research-partial-retry"
    )
    assert store.find_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="dispatch_research",
        request_hash=receipt["request_hash"],
    ) is None
    assert canonical.forbidden_execution_calls == []


def test_research_partial_failure_after_run_acceptance_resumes_both_ids() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="research-run-partial-create")
    canonical.add_approval("approval-run-partial", version_id=version_id)
    body = _research_body(version_id, "approval-run-partial")
    canonical.research_error = CanonicalOperationError(
        "research_orchestrator",
        "authoritative research run readback id mismatch",
        retryable=True,
        partial_effects={
            "research_task_id": "research-task-001",
            "research_run_id": "research-run-001",
        },
    )

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-run-partial", created.headers["etag"]),
        json=body,
    )
    assert failed.status_code == 503, failed.text
    receipt = _receipt(store, "dispatch_research", "research-run-partial")
    assert receipt["canonical_refs"] == {
        "research_task_id": "research-task-001",
        "research_run_id": "research-run-001",
    }

    retried = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-run-partial-retry", _etag(store)),
        json=body,
    )
    assert retried.status_code == 202, retried.text
    retry_call = _last_call(canonical, "dispatch_research_run")
    assert retry_call["resume"] == {
        "research_task_id": "research-task-001",
        "research_run_id": "research-run-001",
    }
    assert canonical.call_count("dispatch_research_run") == 2


def test_resume_requires_the_same_request_body() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="research-hash-create")
    canonical.add_approval("approval-hash-guard", version_id=version_id)
    body = _research_body(version_id, "approval-hash-guard")
    canonical.research_error = CanonicalOperationError(
        "research_orchestrator",
        "run dispatch lost",
        retryable=True,
        partial_effects={"research_task_id": "research-task-001"},
    )

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-hash-a", created.headers["etag"]),
        json=body,
    )
    assert failed.status_code == 503, failed.text

    different_body = {**body, "research_context": "A different research request."}
    fresh = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("research-hash-b", _etag(store)),
        json=different_body,
    )
    assert fresh.status_code == 202, fresh.text
    dispatch_calls = [
        payload
        for name, payload in canonical.calls
        if name == "dispatch_research_run"
    ]
    assert len(dispatch_calls) == 2
    # A different logical request never adopts another command's downstream
    # resources or its downstream idempotency keys.
    assert dispatch_calls[-1]["resume"] is None
    assert (
        dispatch_calls[-1]["task_payload"]["idempotency_key"]
        != dispatch_calls[0]["task_payload"]["idempotency_key"]
    )


def test_consultation_partial_failure_retry_adopts_recorded_request() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="consult-partial-create")
    selected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
        headers=_command_headers("consult-partial-select", created.headers["etag"]),
    )
    assert selected.status_code == 200, selected.text
    body = {
        "consultation_type": "advisory",
        "subject": "Resume after partial submit failure",
    }
    canonical.consultation_error = CanonicalOperationError(
        "consultation_service",
        "canonical service is unavailable",
        retryable=True,
        partial_effects={"consultation_request_id": "__pending__"},
    )

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-partial", selected.headers["etag"]),
        json=body,
    )
    assert failed.status_code == 503, failed.text
    first_request_id = _last_call(canonical, "open_consultation")["request_id"]
    assert first_request_id.startswith("cr-ws-")

    receipt = _receipt(store, "open_consultation", "consult-partial")
    assert receipt["status"] == "failed"
    assert receipt["canonical_refs"] == {
        "consultation_request_id": "__pending__"
    }
    assert receipt["compensation"]["resumable"] is True

    retried = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-partial-retry", _etag(store)),
        json=body,
    )
    assert retried.status_code == 201, retried.text
    retry_call = _last_call(canonical, "open_consultation")
    assert retry_call["resume"] is True
    # The retry re-uses the recorded consultation request id, adopting the
    # possibly-created downstream request instead of opening a second one.
    assert retry_call["request_id"] == "__pending__"

    resolved = _receipt(store, "open_consultation", "consult-partial")
    assert resolved["compensation"]["resolution"] == "resumed"
    assert canonical.call_count("cancel_consultation") == 0
    assert canonical.forbidden_execution_calls == []


def test_restart_safe_retry_resumes_from_durable_state_without_duplicates() -> None:
    client, store, canonical = _harness()
    created, version_id = _create_version(client, store, key="restart-partial-create")
    canonical.add_approval("approval-restart-run", version_id=version_id)
    body = _research_body(version_id, "approval-restart-run")
    canonical.research_error = CanonicalOperationError(
        "research_orchestrator",
        "run acceptance lost before readback",
        retryable=True,
        partial_effects={"research_task_id": "research-task-001"},
    )

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("restart-partial", created.headers["etag"]),
        json=body,
    )
    assert failed.status_code == 503, failed.text
    first_task_key = _last_call(canonical, "dispatch_research_run")[
        "task_payload"
    ]["idempotency_key"]

    # Simulate a BFF restart: a brand-new router and canonical adapter over
    # the same durable store.  Resume state must come from the store alone.
    restarted_client, _, restarted_canonical = _harness(store=store)
    restarted_canonical.add_approval("approval-restart-run", version_id=version_id)

    retried = restarted_client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/research-runs",
        headers=_command_headers("restart-partial-retry", _etag(store)),
        json=body,
    )
    assert retried.status_code == 202, retried.text
    assert restarted_canonical.call_count("dispatch_research_run") == 1
    retry_call = _last_call(restarted_canonical, "dispatch_research_run")
    assert retry_call["resume"] == {
        "research_task_id": "research-task-001",
        "research_run_id": None,
    }
    assert retry_call["task_payload"]["idempotency_key"] == first_task_key
    assert retry_call["run_payload"]["idempotency_key"].endswith("-run")

    retry_receipt = _receipt(store, "dispatch_research", "restart-partial-retry")
    assert retry_receipt["status"] == "completed"
    assert (
        retry_receipt["canonical_refs"]["resumed_from_idempotency_key"]
        == "restart-partial"
    )
    resolved = _receipt(store, "dispatch_research", "restart-partial")
    assert resolved["compensation"]["resolution"] == "resumed"
    assert restarted_canonical.forbidden_execution_calls == []


# --------------------------------------------------------------------------- #
# Adopted-lineage safety: atomic claim, transactional source resolution,
# exclusive-ownership compensation (review follow-up after PR #3977).
# --------------------------------------------------------------------------- #


class FlakyConsultationCommitStore(MemoryWorkshopStore):
    """Fail the open_consultation projection commit a set number of times."""

    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures

    def complete_command(self, **kwargs: Any) -> Dict[str, Any]:
        if kwargs.get("operation") == "open_consultation" and self.failures > 0:
            self.failures -= 1
            return {
                "outcome": "projection_unavailable",
                "receipt": self.get_command_receipt(
                    workshop_id=kwargs["workshop_id"],
                    tenant_id=kwargs["tenant_id"],
                    user_id=kwargs["user_id"],
                    operation=kwargs["operation"],
                    idempotency_key=kwargs["idempotency_key"],
                ),
            }
        return super().complete_command(**kwargs)


class ResolutionWriteFailingStore(MemoryWorkshopStore):
    """Simulate the transactional source-resolution write failing on commit."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_resolution_writes = True

    def complete_command(self, **kwargs: Any) -> Dict[str, Any]:
        if (
            self.fail_resolution_writes
            and kwargs.get("resolve_compensation") is not None
        ):
            raise RuntimeError("source resolution write failed")
        return super().complete_command(**kwargs)


def _select_version(
    client: TestClient,
    store: MemoryWorkshopStore,
    *,
    create_key: str,
    select_key: str,
) -> Any:
    created, version_id = _create_version(client, store, key=create_key)
    selected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/versions/{version_id}/select",
        headers=_command_headers(select_key, created.headers["etag"]),
    )
    assert selected.status_code == 200, selected.text
    return selected


def _consultation_partial_failure() -> CanonicalOperationError:
    return CanonicalOperationError(
        "consultation_service",
        "canonical service is unavailable",
        retryable=True,
        partial_effects={"consultation_request_id": "__pending__"},
    )


_CONSULT_BODY = {
    "consultation_type": "advisory",
    "subject": "Adopted lineage safety",
}


def test_adopted_consultation_commit_failure_never_cancels_shared_request() -> None:
    client, store, canonical = _harness(store=FlakyConsultationCommitStore(failures=1))
    selected = _select_version(
        client, store, create_key="consult-own-create", select_key="consult-own-select"
    )
    canonical.consultation_error = _consultation_partial_failure()

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-own-a", selected.headers["etag"]),
        json=_CONSULT_BODY,
    )
    assert failed.status_code == 503, failed.text

    # The retry adopts the recorded request; its projection commit fails.
    # An adopted request is shared lineage: it must never be cancelled.
    retry = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-own-b", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert retry.status_code == 503, retry.text
    assert _reason(retry) == "WORKSHOP_COMMIT_FAILED"
    assert canonical.call_count("cancel_consultation") == 0

    # The lineage moved to exactly one live receipt in the failure write.
    source = _receipt(store, "open_consultation", "consult-own-a")
    assert source["compensation"]["resolution"] == "superseded"
    assert source["compensation"]["resolved_by_idempotency_key"] == "consult-own-b"
    successor = _receipt(store, "open_consultation", "consult-own-b")
    assert successor["status"] == "failed"
    assert successor["compensation"]["resumable"] is True
    assert (
        successor["compensation"]["partial_effects"]["consultation_request_id"]
        == "__pending__"
    )
    resumable = store.find_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        request_hash=successor["request_hash"],
    )
    assert resumable is not None
    assert resumable["idempotency_key"] == "consult-own-b"

    # The next retry adopts the surviving lineage and commits exactly once.
    final = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-own-c", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert final.status_code == 201, final.text
    final_call = _last_call(canonical, "open_consultation")
    assert final_call["resume"] is True
    assert final_call["request_id"] == "__pending__"
    assert canonical.call_count("cancel_consultation") == 0
    resolved = _receipt(store, "open_consultation", "consult-own-b")
    assert resolved["compensation"]["resolution"] == "resumed"
    assert store.find_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        request_hash=successor["request_hash"],
    ) is None
    events = [
        event
        for event in store.list_events(WORKSHOP_ID)
        if event["event_type"] == "consultation_started"
    ]
    assert len(events) == 1
    assert canonical.forbidden_execution_calls == []


def test_concurrent_new_key_retries_never_share_adopted_lineage() -> None:
    client, store, canonical = _harness()
    selected = _select_version(
        client, store, create_key="consult-race-create", select_key="consult-race-select"
    )
    canonical.consultation_error = _consultation_partial_failure()

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-race-a", selected.headers["etag"]),
        json=_CONSULT_BODY,
    )
    assert failed.status_code == 503, failed.text
    source = _receipt(store, "open_consultation", "consult-race-a")

    # A concurrent in-flight retry holds the adoption claim.
    claimed = store.claim_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        request_hash=source["request_hash"],
        claimed_by_idempotency_key="consult-race-b",
    )
    assert claimed is not None
    assert claimed["idempotency_key"] == "consult-race-a"

    # The losing retry cannot adopt: it opens fresh under its own digest.
    other = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-race-c", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert other.status_code == 201, other.text
    other_call = _last_call(canonical, "open_consultation")
    assert other_call["resume"] is False
    assert other_call["request_id"] != "__pending__"
    assert other_call["request_id"].startswith("cr-ws-")
    source = _receipt(store, "open_consultation", "consult-race-a")
    assert not source["compensation"].get("resolution")
    assert source["compensation"]["claimed_by_idempotency_key"] == "consult-race-b"

    # The claim holder re-enters with its own key and adopts exactly once.
    winner = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-race-b", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert winner.status_code == 201, winner.text
    winner_call = _last_call(canonical, "open_consultation")
    assert winner_call["resume"] is True
    assert winner_call["request_id"] == "__pending__"
    resolved = _receipt(store, "open_consultation", "consult-race-a")
    assert resolved["compensation"]["resolution"] == "resumed"
    assert (
        resolved["compensation"]["resolved_by_idempotency_key"] == "consult-race-b"
    )
    assert canonical.call_count("cancel_consultation") == 0

    # Distinct digests mean distinct committed events: no id collision.
    events = [
        event
        for event in store.list_events(WORKSHOP_ID)
        if event["event_type"] == "consultation_started"
    ]
    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2


def test_resolution_write_failure_fails_closed_without_cancelling_adopted_request() -> None:
    client, store, canonical = _harness(store=ResolutionWriteFailingStore())
    selected = _select_version(
        client, store, create_key="consult-resw-create", select_key="consult-resw-select"
    )
    canonical.consultation_error = _consultation_partial_failure()

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-resw-a", selected.headers["etag"]),
        json=_CONSULT_BODY,
    )
    assert failed.status_code == 503, failed.text

    # Completion and source resolution are one transaction: when the
    # resolution write fails, the whole commit fails closed and the adopted
    # consultation — possibly committed by another retry — is not cancelled.
    retry = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-resw-b", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert retry.status_code == 503, retry.text
    assert _reason(retry) == "WORKSHOP_COMMIT_EXCEPTION"
    assert canonical.call_count("cancel_consultation") == 0
    source = _receipt(store, "open_consultation", "consult-resw-a")
    assert source["compensation"]["resolution"] == "superseded"
    successor = _receipt(store, "open_consultation", "consult-resw-b")
    assert successor["status"] == "failed"
    assert successor["compensation"]["resumable"] is True

    # Once the store heals, the next retry adopts the surviving lineage.
    store.fail_resolution_writes = False
    final = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-resw-c", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert final.status_code == 201, final.text
    assert _last_call(canonical, "open_consultation")["request_id"] == "__pending__"
    assert canonical.call_count("cancel_consultation") == 0
    assert store.find_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        request_hash=successor["request_hash"],
    ) is None
    events = [
        event
        for event in store.list_events(WORKSHOP_ID)
        if event["event_type"] == "consultation_started"
    ]
    assert len(events) == 1


def test_adopted_cancelled_consultation_is_rejected_and_lineage_sealed() -> None:
    client, store, canonical = _harness()
    selected = _select_version(
        client, store, create_key="consult-dead-create", select_key="consult-dead-select"
    )
    canonical.consultation_error = CanonicalOperationError(
        "consultation_service",
        "canonical service is unavailable",
        retryable=True,
        partial_effects={"consultation_request_id": "cr-ws-dead"},
    )

    failed = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-dead-a", selected.headers["etag"]),
        json=_CONSULT_BODY,
    )
    assert failed.status_code == 503, failed.text

    # The adopted request was cancelled downstream in the meantime: adoption
    # must not report it as a successful open, and the dead lineage is sealed.
    canonical.consultation_status = "cancelled"
    rejected = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-dead-b", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert rejected.status_code == 409, rejected.text
    assert _reason(rejected) == "CONSULTATION_REQUEST_CANCELLED"
    assert canonical.call_count("cancel_consultation") == 0
    source = _receipt(store, "open_consultation", "consult-dead-a")
    assert source["compensation"]["resolution"] == "cancelled"
    assert source["compensation"]["resolved_at"]
    assert store.find_resumable_command(
        workshop_id=WORKSHOP_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        operation="open_consultation",
        request_hash=source["request_hash"],
    ) is None

    # A later retry no longer re-adopts the dead request: it opens fresh.
    canonical.consultation_status = "submitted"
    final = client.post(
        f"/bff/agora/workshops/{WORKSHOP_ID}/consultations",
        headers=_command_headers("consult-dead-c", _etag(store)),
        json=_CONSULT_BODY,
    )
    assert final.status_code == 201, final.text
    final_call = _last_call(canonical, "open_consultation")
    assert final_call["resume"] is False
    assert final_call["request_id"] != "cr-ws-dead"


# --------------------------------------------------------------------------- #
# Store-level lineage semantics — Memory and Postgres backends
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["memory", "postgres"])
def lineage_store(request: pytest.FixtureRequest):
    if request.param == "memory":
        yield MemoryWorkshopStore()
        return
    dsn = os.environ.get("AGORA_WORKSHOP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set AGORA_WORKSHOP_TEST_POSTGRES_DSN for real Postgres coverage")
    from services.control_plane.bff.agora.strategy_workshop.store import PostgresWorkshopStore

    schema = f"test_agora_ws_lineage_{uuid.uuid4().hex[:12]}"
    store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    try:
        yield store
    finally:
        with store._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


_LINEAGE_SCOPE = {
    "workshop_id": WORKSHOP_ID,
    "tenant_id": TENANT_ID,
    "user_id": USER_ID,
    "operation": "open_consultation",
}
_LINEAGE_HASH = "sha256:lineage-request"


def _seed_resumable_source(store: Any, *, idempotency_key: str = "lineage-source") -> Dict[str, Any]:
    if store.get_session(WORKSHOP_ID) is None:
        store.create_session(
            {
                "workshop_id": WORKSHOP_ID,
                "tenant_id": TENANT_ID,
                "user_id": USER_ID,
                "strategy_id": STRATEGY_ID,
                "status": "open",
            }
        )
    session = store.get_session(WORKSHOP_ID)
    admitted = store.admit_command(
        **_LINEAGE_SCOPE,
        idempotency_key=idempotency_key,
        request_hash=_LINEAGE_HASH,
        expected_lock_version=int(session["lock_version"]),
        request_payload={},
        request_id="req-lineage",
        trace_id="trace-lineage",
    )
    assert admitted["outcome"] == "admitted"
    failed = store.fail_command(
        **_LINEAGE_SCOPE,
        idempotency_key=idempotency_key,
        request_hash=_LINEAGE_HASH,
        failure={"reason": "CANONICAL_UNAVAILABLE"},
        compensation={
            "required": True,
            "resumable": True,
            "downstream_idempotency_digest": "digest-lineage",
            "partial_effects": {"consultation_request_id": "cr-ws-lineage"},
        },
        canonical_refs={"consultation_request_id": "cr-ws-lineage"},
    )
    assert failed["outcome"] == "failed"
    return failed["receipt"]


def _admit_successor(store: Any, idempotency_key: str) -> None:
    session = store.get_session(WORKSHOP_ID)
    admitted = store.admit_command(
        **_LINEAGE_SCOPE,
        idempotency_key=idempotency_key,
        request_hash=_LINEAGE_HASH,
        expected_lock_version=int(session["lock_version"]),
        request_payload={},
        request_id=f"req-{idempotency_key}",
        trace_id=f"trace-{idempotency_key}",
    )
    assert admitted["outcome"] == "admitted"


def _claim(store: Any, claimed_by: str) -> Optional[Dict[str, Any]]:
    return store.claim_resumable_command(
        **_LINEAGE_SCOPE,
        request_hash=_LINEAGE_HASH,
        claimed_by_idempotency_key=claimed_by,
    )


def test_claim_resumable_command_is_exclusive_and_reclaimable(lineage_store: Any) -> None:
    store = lineage_store
    source = _seed_resumable_source(store)

    claimed = _claim(store, "retry-1")
    assert claimed is not None
    assert claimed["idempotency_key"] == source["idempotency_key"]
    assert claimed["compensation"]["claimed_by_idempotency_key"] == "retry-1"
    # A concurrent successor cannot claim the same lineage.
    assert _claim(store, "retry-2") is None
    # Exact-replay recovery: the holder may re-claim with its own key.
    reclaimed = _claim(store, "retry-1")
    assert reclaimed is not None
    assert reclaimed["idempotency_key"] == source["idempotency_key"]


def test_complete_command_resolves_the_claimed_source_atomically(lineage_store: Any) -> None:
    store = lineage_store
    source = _seed_resumable_source(store)
    assert _claim(store, "retry-1") is not None
    _admit_successor(store, "retry-1")

    completed = store.complete_command(
        **_LINEAGE_SCOPE,
        idempotency_key="retry-1",
        request_hash=_LINEAGE_HASH,
        result={"consultation": {"request_id": "cr-ws-lineage"}},
        canonical_refs={"consultation_request_id": "cr-ws-lineage"},
        resolve_compensation={
            "operation": "open_consultation",
            "idempotency_key": source["idempotency_key"],
            "resolution": {
                "resolved_at": NOW,
                "resolution": "resumed",
                "resolved_by_idempotency_key": "retry-1",
            },
        },
    )
    assert completed["outcome"] == "completed"
    resolved = store.get_command_receipt(
        **_LINEAGE_SCOPE, idempotency_key=source["idempotency_key"]
    )
    assert resolved["compensation"]["resolution"] == "resumed"
    assert resolved["compensation"]["resolved_at"]
    assert store.find_resumable_command(
        **_LINEAGE_SCOPE, request_hash=_LINEAGE_HASH
    ) is None
    assert _claim(store, "retry-9") is None


def test_fail_command_moves_lineage_to_exactly_one_live_receipt(lineage_store: Any) -> None:
    store = lineage_store
    source = _seed_resumable_source(store)
    assert _claim(store, "retry-1") is not None
    _admit_successor(store, "retry-1")

    failed = store.fail_command(
        **_LINEAGE_SCOPE,
        idempotency_key="retry-1",
        request_hash=_LINEAGE_HASH,
        failure={"reason": "WORKSHOP_COMMIT_FAILED"},
        compensation={
            "required": True,
            "resumable": True,
            "downstream_idempotency_digest": "digest-lineage",
            "partial_effects": {"consultation_request_id": "cr-ws-lineage"},
        },
        resolve_compensation={
            "operation": "open_consultation",
            "idempotency_key": source["idempotency_key"],
            "resolution": {
                "resolved_at": NOW,
                "resolution": "superseded",
                "resolved_by_idempotency_key": "retry-1",
            },
        },
    )
    assert failed["outcome"] == "failed"
    resolved = store.get_command_receipt(
        **_LINEAGE_SCOPE, idempotency_key=source["idempotency_key"]
    )
    assert resolved["compensation"]["resolution"] == "superseded"
    live = store.find_resumable_command(
        **_LINEAGE_SCOPE, request_hash=_LINEAGE_HASH
    )
    assert live is not None
    assert live["idempotency_key"] == "retry-1"


def test_resumed_projection_failure_rolls_back_source_resolution(lineage_store: Any) -> None:
    store = lineage_store
    source = _seed_resumable_source(store)
    # A prior successful retry already committed this digest-derived event.
    store.create_event(
        {
            "event_id": "wsevt-consult-digest-lineage",
            "workshop_id": WORKSHOP_ID,
            "actor_type": "operator",
            "event_type": "consultation_started",
            "redacted_summary": "prior committed consultation",
        }
    )
    assert _claim(store, "retry-1") is not None
    _admit_successor(store, "retry-1")

    with pytest.raises(Exception):
        store.complete_command(
            **_LINEAGE_SCOPE,
            idempotency_key="retry-1",
            request_hash=_LINEAGE_HASH,
            result={"consultation": {"request_id": "cr-ws-lineage"}},
            canonical_refs={"consultation_request_id": "cr-ws-lineage"},
            event={
                "event_id": "wsevt-consult-digest-lineage",
                "actor_type": "operator",
                "event_type": "consultation_started",
                "redacted_summary": "colliding commit",
            },
            resolve_compensation={
                "operation": "open_consultation",
                "idempotency_key": source["idempotency_key"],
                "resolution": {
                    "resolved_at": NOW,
                    "resolution": "resumed",
                    "resolved_by_idempotency_key": "retry-1",
                },
            },
        )

    # The whole commit rolled back: no duplicate event, the successor is not
    # completed, and the source resolution never became visible.
    successor = store.get_command_receipt(
        **_LINEAGE_SCOPE, idempotency_key="retry-1"
    )
    assert successor["status"] == "admitted"
    src = store.get_command_receipt(
        **_LINEAGE_SCOPE, idempotency_key=source["idempotency_key"]
    )
    assert not src["compensation"].get("resolved_at")
    assert src["compensation"]["claimed_by_idempotency_key"] == "retry-1"
    events = [
        event
        for event in store.list_events(WORKSHOP_ID)
        if event["event_type"] == "consultation_started"
    ]
    assert len(events) == 1
