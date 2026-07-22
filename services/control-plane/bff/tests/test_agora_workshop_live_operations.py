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
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agora.strategy_workshop import MemoryWorkshopStore  # noqa: E402
from agora.strategy_workshop.operations import CanonicalOperationError  # noqa: E402
from agora.strategy_workshop.router import create_strategy_workshop_router  # noqa: E402
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
    ) -> Dict[str, Any]:
        self._record(
            "dispatch_research_run",
            {"task_payload": task_payload, "run_payload": run_payload},
        )
        if self.raise_research_error:
            raise CanonicalOperationError(
                "research_orchestrator",
                "research service unavailable",
                retryable=True,
            )
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
    ) -> Dict[str, Any]:
        self._record(
            "open_consultation",
            {"request_id": request_id, "payload": payload},
        )
        return {
            "request_id": request_id,
            "status": "submitted",
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
