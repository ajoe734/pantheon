"""Acceptance tests for the Deployment outbox dispatcher lease and happy path."""
from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from services.governance.test_approval_authority_postgres import owner_env, approved_registry_owners

from services.deployment.outbox_lease import (
    DeploymentOutboxLeaseStore,
    OutboxLeaseError,
)
from services.deployment.runtime_manager_dispatch_adapter import (
    DispatchOutcome,
    dispatch_to_runtime_manager,
)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _outbox_record() -> dict:
    return {
        "owner_service": "deployment",
        "event": {
            "event_id": "event-binding-requested",
            "event_type": "runtime.binding.requested",
            "aggregate_type": "deployment_saga",
            "aggregate_id": "saga-plan-001",
            "sequence_no": 1,
            "trace_id": "trace-deployment-001",
            "idempotency_key": "deployment:saga-plan-001:1",
            "payload": {},
        },
        "status": "pending",
    }


def _authority_report() -> dict:
    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": "plan-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "strategy_id": "strategy-001",
        "approval_decision_id": "approval-001",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-001",
        "persona_capital_binding_id": "pcb-001",
        "deployment_plan_current_stage": "none",
        "deployment_plan_binding_id": None,
        "deployment_plan_runtime_lifecycle": {},
        "deployment_plan_sha256": "sha256:" + "0" * 64,
        "deployment_plan_authority_sha256": "sha256:" + "a" * 64,
        "registry_entry_sha256": "sha256:" + "1" * 64,
        "approval_decision_sha256": "sha256:" + "2" * 64,
        "capital_pool_sha256": "sha256:" + "3" * 64,
        "capital_admissibility_sha256": "sha256:" + "4" * 64,
        "persona_capital_binding_sha256": "sha256:" + "5" * 64,
    }


def _saga(*, binding_id: str | None = None) -> dict:
    return {
        "saga_id": "saga-plan-001",
        "plan_id": "plan-001",
        "approval_decision_id": "approval-001",
        "strategy_id": "strategy-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-001",
        "current_stage": "none",
        "target_stage": "paper",
        "runtime_action": "deploy_new_binding",
        "status": "awaiting_binding" if binding_id is None else "awaiting_runtime_load",
        "binding_id": binding_id,
    }


def _deploy_context() -> dict:
    return {
        "sponsor_persona_id": "persona-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "plan_status": "approved",
        "metadata": {
            "tenant_id": "tenant-a",
            "deployment_correlation_id": "correlation-deployment-001",
            "authoritative_loader_attestation": _authority_report(),
        },
    }


def _binding() -> dict:
    return {
        "binding_id": "binding-001",
        "plan_id": "plan-001",
        "runtime_id": "runtime-001",
        "capital_pool_id": "pool-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "deployment_mode": "paper",
        "execution_mode": "paper",
        "persona_capital_binding_id": "pcb-001",
        "status": "active",
        "metadata": {
            "strategy_id": "strategy-001",
            "tenant_id": "tenant-a",
            "deployment_correlation_id": "correlation-deployment-001",
            "authoritative_loader_attestation": _authority_report(),
        },
    }


def test_concurrent_consumers_cannot_claim_the_same_event(tmp_path) -> None:
    clock = MutableClock()
    storage_path = tmp_path / "deployment_outbox_leases.json"
    ready = Barrier(2)

    def claim(consumer_name: str) -> list[dict]:
        store = DeploymentOutboxLeaseStore(storage_path, clock=clock)
        ready.wait()
        return store.claim(
            [_outbox_record()],
            tenant_id="tenant-a",
            consumer_name=consumer_name,
            lease_seconds=30,
            limit=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("consumer-a", "consumer-b")))

    assert sorted(len(records) for records in claims) == [0, 1]
    winner = next(records[0] for records in claims if records)
    assert winner["status"] == "pending"
    assert winner["lease_status"] == "active"
    health = DeploymentOutboxLeaseStore(storage_path, clock=clock).health()
    assert health["active_claim_count"] == 1
    assert health["recovered_claim_count"] == 0


def test_idle_timeout_reclaims_lease_and_rejects_stale_ack(tmp_path) -> None:
    clock = MutableClock()
    store = DeploymentOutboxLeaseStore(
        tmp_path / "deployment_outbox_leases.json",
        clock=clock,
    )
    first = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-a",
        lease_seconds=30,
        limit=1,
    )[0]

    clock.advance(31)
    second = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-b",
        lease_seconds=30,
        limit=1,
    )[0]

    assert second["claim_token"] != first["claim_token"]
    assert second["recovery_count"] == 1
    with pytest.raises(OutboxLeaseError, match="not owned by this caller"):
        store.acknowledge(
            event_id="event-binding-requested",
            claim_token=first["claim_token"],
            tenant_id="tenant-a",
            consumer_name="consumer-a",
        )
    store.acknowledge(
        event_id="event-binding-requested",
        claim_token=second["claim_token"],
        tenant_id="tenant-a",
        consumer_name="consumer-b",
    )
    health = store.health()
    assert health["active_claim_count"] == 0
    assert health["acknowledged_claim_count"] == 1
    assert health["recovered_claim_count"] == 1


def test_crash_after_runtime_binding_side_effect_recovers_without_duplicate(
    tmp_path,
) -> None:
    clock = MutableClock()
    store = DeploymentOutboxLeaseStore(
        tmp_path / "deployment_outbox_leases.json",
        clock=clock,
    )
    first_claim = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-a",
        lease_seconds=30,
        limit=1,
    )[0]

    binding = _binding()
    client = MagicMock()
    client.deploy.return_value = binding
    client.get.return_value = binding

    first_dispatch = dispatch_to_runtime_manager(
        saga=_saga(),
        deploy_context=_deploy_context(),
        client=client,
    )
    assert first_dispatch.outcome == DispatchOutcome.SUCCESS
    assert first_dispatch.binding_id == "binding-001"
    assert client.deploy.call_count == 1

    # Simulate a process crash after RuntimeBinding and saga state committed,
    # but before the original outbox claim could be acknowledged.
    clock.advance(31)
    recovered_claim = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-b",
        lease_seconds=30,
        limit=1,
    )[0]
    assert recovered_claim["claim_token"] != first_claim["claim_token"]
    assert recovered_claim["recovery_count"] == 1

    replay = dispatch_to_runtime_manager(
        saga=_saga(binding_id="binding-001"),
        deploy_context=_deploy_context(),
        client=client,
    )

    assert replay.outcome == DispatchOutcome.SUCCESS
    assert replay.idempotent_replay is True
    assert client.deploy.call_count == 1
    assert client.get.call_count == 2


def test_approved_paper_command_reaches_terminal_plan_and_binding(
    tmp_path, monkeypatch, owner_env
) -> None:
    """Run the deployment worker's two-event paper path against real stores.

    The worker's HTTP helpers are adapted to a FastAPI TestClient so this
    deterministic test stays process-local. The Deployment service, saga/outbox
    store, RuntimeManagerService, adapter, and DEP-003 projection all execute
    their production paths.
    """
    governance_dir = tmp_path / "governance"
    governance_dir.mkdir()
    runtime_binding_store = tmp_path / "runtime_bindings.json"
    (governance_dir / "capital_pools.json").write_text(
        json.dumps(
            [
                {
                    "pool_id": "pool-l12-dep",
                    "status": "active",
                    "single_runtime_enforced": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    (governance_dir / "persona_capital_bindings.json").write_text(
        json.dumps(
            [
                {
                    "binding_id": "pcb-l12-dep",
                    "persona_id": "persona-l12-dep",
                    "capital_pool_id": "pool-l12-dep",
                    "role": "paper_owner",
                    "status": "active",
                    "allowed_deployment_scope": "paper",
                    "created_at": "2026-08-09T00:00:00Z",
                    "approval_decision_id": "approval-l12-dep",
                }
            ]
        ),
        encoding="utf-8",
    )

    with approved_registry_owners(owner_env, execution_bundle=True) as owners, monkeypatch.context() as environment:
        registry_id = owners["entry"]["registry_id"]
        approval_id = owners["decision"]["decision_id"]
        environment.setenv("DEPLOYMENT_REGISTRY_BASE_URL", owners["registry_url"])
        environment.setenv("DEPLOYMENT_REGISTRY_SERVICE_TOKEN", owners["registry_token"])
        environment.setenv("DEPLOYMENT_GOVERNANCE_BASE_URL", owners["governance_url"])
        environment.setenv("DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN", owners["governance_token"])
        environment.setenv("CAPITAL_DATA_DIR", str(governance_dir))
        environment.setenv("DEPLOYMENT_DATA_DIR", str(governance_dir))
        environment.setenv("PANTHEON_GOVERNANCE_DATA_DIR", str(governance_dir))
        environment.setenv(
            "PANTHEON_RUNTIME_BINDING_STORE_PATH", str(runtime_binding_store)
        )
        environment.setenv("PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED", "false")
        environment.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "l12:service")
        environment.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "synthetic-tenant")
        environment.delenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", raising=False)
        environment.delenv("PANTHEON_RUNTIME_MANAGER_URL", raising=False)

        import services.deployment.service as deployment_service

        deployment_service = importlib.reload(deployment_service)
        client = TestClient(
            deployment_service.app,
            headers={
                "Authorization": "Bearer l12:operator,service",
                "X-Tenant-Id": "synthetic-tenant",
            },
        )
        try:
            created = client.post(
                "/api/deployment/plans",
                json={
                    "plan_id": "plan-l12-dep",
                    "approval_decision_id": approval_id,
                    "registry_id": registry_id,
                    "capital_pool_id": "pool-l12-dep",
                    "sponsor_persona_id": "persona-l12-dep",
                    "target_stage": "paper",
                    "rollback": {
                        "target_artifact_id": "artifact-rollback",
                        "target_version": "0.9.0",
                        "action_type": "replace",
                    },
                },
            )
            assert created.status_code == 201, created.text
            dispatched = client.post(
                "/api/deployment/plans/plan-l12-dep/dispatch",
                json={
                    "trace_id": "trace-l12-dep",
                    "workflow_id": "l12.minimum.deployment",
                    "source_task_id": "L12-MIN-DEP-20260808",
                    "idempotency_key": "l12-min-dep-command-001",
                },
            )
            assert dispatched.status_code == 200, dispatched.text
            saga_id = dispatched.json()["deployment_saga"]["saga"]["saga_id"]

            worker = importlib.import_module(
                "services.deployment.outbox_consumer_worker"
            )
            runtime_service = worker.RuntimeManagerClient(allow_local=True)._local()

            class LocalRuntimeAuthority:
                def deploy(self, request):
                    return runtime_service.deploy(request).to_dict()

                def get(self, binding_id):
                    binding = runtime_service.get(binding_id)
                    return binding.to_dict() if binding is not None else None

                def list_by_plan(self, plan_id):
                    return [
                        binding.to_dict()
                        for binding in runtime_service.list_by_plan(plan_id)
                    ]

            runtime_client = LocalRuntimeAuthority()

            def _response(response):
                assert response.status_code < 300, response.text
                return response.json()

            def _claim(*, consumer_name, aggregate_id=None, **_kwargs):
                return _response(
                    client.post(
                        "/api/deployment/outbox/claim",
                        json={
                            "consumer_name": consumer_name,
                            "lease_seconds": 60,
                            "limit": 25,
                            "aggregate_id": aggregate_id,
                        },
                    )
                )

            def _authority_report(*, saga, plan, **_kwargs):
                # Actual shared Runtime verifier; only capital/lifecycle reads are
                # isolated doubles. Registry and Governance use scoped HTTP reads.
                from services.governance.approval_authority import ApprovalReader
                from importlib import import_module
                from urllib.parse import urlparse
                import httpx
                authority = import_module('services.runtime-manager.deploy_authority')
                pool = json.loads((governance_dir / 'capital_pools.json').read_text())[0]
                binding = json.loads((governance_dir / 'persona_capital_bindings.json').read_text())[0]

                def fetch(url, timeout):
                    if url.startswith(owners['registry_url']):
                        response = httpx.get(url, headers={'Authorization': 'Bearer '+owners['registry_token']}, timeout=timeout)
                        assert response.status_code == 200, response.text
                        return response.json()
                    path = urlparse(url).path
                    if path.startswith('/api/deployment/plans/'):
                        return _response(client.get(path))
                    if path.startswith('/api/capital-pools/'):
                        return pool
                    if path == '/api/bindings/admissibility':
                        return dict(persona_id=binding['persona_id'], capital_pool_id=binding['capital_pool_id'],
                                    target_stage='paper', permitted=True, pool_status=pool['status'],
                                    single_runtime_enforced=True, binding_id=binding['binding_id'],
                                    binding_status=binding['status'], allowed_deployment_scope='paper')
                    if path.startswith('/api/bindings/'):
                        return binding
                    raise AssertionError(url)

                return authority.verify_deploy_authorities(
                    dict(plan_id=plan['plan_id'], plan_status=plan['status'], target_stage=plan['target_stage'],
                         artifact_id=plan['artifact_id'], artifact_version=plan['artifact_version'],
                         strategy_id=plan['strategy_id'], approval_decision_id=plan['approval_decision_id'],
                         capital_pool_id=plan['capital_pool_id'], sponsor_persona_id=plan['sponsor_persona_id'],
                         persona_capital_binding_id=binding['binding_id'], persona_capital_binding_status='active',
                         allowed_deployment_scope='paper'),
                    deployment_base_url='http://deployment.test', registry_base_url=owners['registry_url'],
                    governance_base_url=owners['governance_url'], capital_base_url='http://capital.test',
                    fetch_json=fetch, approval_reader=ApprovalReader(base_url=owners['governance_url'],
                                                                  service_token=owners['governance_token']))

            def _unexpected_urlopen(request, *_args, **_kwargs):
                raise RuntimeError(f"unexpected outbound request: {request.full_url}")

            with (
                patch.object(worker, "fetch_pending_outbox", side_effect=_claim),
                patch.object(
                    worker,
                    "fetch_saga",
                    side_effect=lambda *, saga_id, **_kwargs: _response(
                        client.get(f"/api/deployment/sagas/{saga_id}")
                    ),
                ),
                patch.object(
                    worker,
                    "fetch_plan",
                    side_effect=lambda *, plan_id, **_kwargs: _response(
                        client.get(f"/api/deployment/plans/{plan_id}")
                    ),
                ),
                patch.object(
                    worker,
                    "fetch_applied_inbox",
                    side_effect=lambda **_kwargs: _response(
                        client.get("/api/deployment/inbox")
                    ),
                ),
                patch.object(
                    worker,
                    "run_compatibility_check",
                    side_effect=lambda *, capital_pool_id, sponsor_persona_id, target_stage, **_kwargs: _response(
                        client.post(
                            "/api/deployment/plans/compatibility-check",
                            json={
                                "capital_pool_id": capital_pool_id,
                                "sponsor_persona_id": sponsor_persona_id,
                                "target_stage": target_stage,
                            },
                        )
                    ),
                ),
                patch.object(
                    worker,
                    "verify_binding_deploy_authorities",
                    side_effect=_authority_report,
                ),
                patch.object(
                    worker,
                    "record_binding_created",
                    side_effect=lambda *, saga_id, binding_id, runtime_id, note, **_kwargs: _response(
                        client.post(
                            f"/api/deployment/sagas/{saga_id}/binding-created",
                            json={
                                "binding_id": binding_id,
                                "runtime_id": runtime_id,
                                "note": note,
                            },
                        )
                    ),
                ),
                patch.object(
                    worker,
                    "record_runtime_active",
                    side_effect=lambda *, saga_id, binding_id, runtime_id, note, **_kwargs: _response(
                        client.post(
                            f"/api/deployment/sagas/{saga_id}/runtime-active",
                            json={
                                "binding_id": binding_id,
                                "runtime_id": runtime_id,
                                "note": note,
                            },
                        )
                    ),
                ),
                patch.object(
                    worker,
                    "fetch_projection",
                    side_effect=lambda *, plan_id, **_kwargs: _response(
                        client.get(f"/api/deployment/projections/{plan_id}")
                    ),
                ),
                patch.object(
                    worker,
                    "consume_event",
                    side_effect=lambda *, event_id, consumer_name, **_kwargs: _response(
                        client.post(
                            f"/api/deployment/outbox/{event_id}/consume",
                            json={"consumer_name": consumer_name},
                        )
                    ),
                ),
                patch.object(worker, "RuntimeManagerClient", return_value=runtime_client),
                patch.object(worker.urllib.request, "urlopen", side_effect=_unexpected_urlopen),
            ):
                first = worker.run_poll(
                    api_url="http://deployment.test",
                    consumer_name="l12-dep-consumer",
                )
                second = worker.run_poll(
                    api_url="http://deployment.test",
                    consumer_name="l12-dep-consumer",
                )

            assert first == {
                "events_found": 1,
                "consumed": 1,
                "duplicates": 0,
                "skipped_not_due": 0,
                "retry_scheduled": 0,
                "dead_lettered": 0,
                "errors": [],
            }
            assert second == first
            projection = _response(
                client.get("/api/deployment/projections/plan-l12-dep")
            )
            assert projection["plan_status"] == "executed"
            assert projection["deployment_saga_id"] == saga_id
            assert projection["deployment_saga_status"] == "completed"
            assert projection["runtime_status"] == "active"
            assert projection["runtime_binding_id"] == projection["plan"]["binding_id"]
            assert projection["source_status"] == {
                "deployment_plan": "canonical",
                "approval_decision": "canonical",
                "runtime_binding": "canonical",
                "deployment_saga": "canonical",
                "registry_entry": "canonical",
                "execution_projection": "derived",
            }
            capital_readback = _response(
                client.post(
                    "/api/deployment/plans/compatibility-check",
                    json={
                        "capital_pool_id": "pool-l12-dep",
                        "sponsor_persona_id": "persona-l12-dep",
                        "target_stage": "paper",
                    },
                )
            )
            assert capital_readback["active_runtime_binding_count"] == 1
            assert capital_readback["active_runtime_binding_ids"] == [
                projection["runtime_binding_id"]
            ]
            # A historical Registry APPROVED state must not authorize another
            # plan or dispatch after Governance revokes the cited decision.
            next_request = json.loads(created.request.content)
            next_request['plan_id'] = 'plan-l12-revocation'
            queued = client.post('/api/deployment/plans', json=next_request)
            assert queued.status_code == 201, queued.text
            assert _authority_report(saga={}, plan=queued.json())['status'] == 'passed'
            before_outbox = client.get('/api/deployment/outbox').json()
            from services.governance.test_approval_authority_postgres import post
            revoked = post(owners['governance_url'], '/'+approval_id+'/revoke', owner_env,
                           dict(expected_version=3, actor_id='synthetic-reviewer', actor_role='risk_owner'), roles=['risk_owner'])
            assert revoked.status_code == 200, revoked.text
            authority_module = importlib.import_module('services.runtime-manager.deploy_authority')
            with pytest.raises(authority_module.DeployAuthorityError, match='governance authority mismatch'):
                _authority_report(saga={}, plan=queued.json())
            rejected = client.post('/api/deployment/plans/plan-l12-revocation/dispatch', json={})
            assert rejected.status_code == 400, rejected.text
            assert client.get('/api/deployment/outbox').json() == before_outbox
            next_request['plan_id'] = 'plan-l12-after-revocation'
            assert client.post('/api/deployment/plans', json=next_request).status_code == 422
        finally:
            client.close()
