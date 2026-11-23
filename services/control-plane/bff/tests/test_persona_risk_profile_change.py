"""Normal BFF routes -> real Capital API/store -> one running paper evaluator.

Policy limits and approval receipts are explicitly synthetic local fixtures.
"""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.capital.test_profile_change import change_fixture  # noqa: F401
from services.capital.pg_store import PersistentCapitalPoolStore, JsonlCapitalAuditStore, PersonaCapitalBindingStore
from services.capital.capital_guard import CapitalGuard
from services.capital.allocation_store import AllocationAuthorityStore
from services.control_plane.bff.test_persona_provisioning_route_security import (  # noqa: F401
    route_harness, _headers, _patch_tenant_payload,
)
from services.control_plane.bff.personas import service as persona_module
from services.control_plane.bff.persona_provisioning_coordinator import PersonaProvisioningCoordinator, deterministic_provisioning_ids


@pytest.fixture
def normal_edit(route_harness, change_fixture, tmp_path, monkeypatch):
    from services.capital import main as capital

    pool, reader, policies, approvals = change_fixture
    record, _ = route_harness.store.reserve(
        tenant_id=pool.owner_id, idempotency_key="original-create", request_hash="immutable-create-hash",
        normalized_name="isolated fixture", persona_id="persona-test", request_payload={"name": "Isolated fixture", "risk": "low"},
    )
    pool = replace(pool, pool_id=deterministic_provisioning_ids(record).capital_pool_id)
    _patch_tenant_payload(monkeypatch, lambda *a, **k: {"id": pool.owner_id})
    store = PersistentCapitalPoolStore(tmp_path / "pool-state.json")
    store.create(pool)
    capital_service = capital.CapitalBoundaryService(
        pool_store=store, binding_store=PersonaCapitalBindingStore(),
        allocation_store=AllocationAuthorityStore(path=tmp_path / "allocation.json"), audit_log_path=tmp_path / "audit.jsonl",
        audit_store=JsonlCapitalAuditStore(tmp_path / "audit.jsonl"), guard=CapitalGuard(approval_reader=reader),
    )
    monkeypatch.setenv("CAPITAL_AUTH_DISABLED", "true")
    monkeypatch.setattr(capital, "get_capital_service", lambda: capital_service)
    api = TestClient(capital.app)
    raw = route_harness.read_store.create_persona(
        persona_id="persona-test", name="Isolated fixture", actor_id="operator-a", risk_level="low",
        lifecycle_state="paper_running", metadata={
            "tenant_id": pool.owner_id, "capital_mode": "paper", "persona_class": "trading",
            "internal_paper_capital_pool_id": pool.pool_id, "deployment_stage": "paper",
        },
    )

    class Transport:
        lost_response = False
        unavailable = False

        def get(self, owner, path):
            if self.unavailable:
                raise OSError("synthetic outage")
            assert owner == "capital"
            response = api.get(path, headers={"X-Tenant-Id": pool.owner_id})
            response.raise_for_status()
            return response.json()

        def post(self, owner, path, payload):
            assert owner == "capital"
            response = api.post(path, json=payload, headers={"X-Tenant-Id": pool.owner_id})
            response.raise_for_status()
            if self.lost_response:
                raise OSError("synthetic lost commit response")
            return response.json()

    transport = Transport()
    coordinator = PersonaProvisioningCoordinator(
        store=route_harness.store, transport=transport, schedule_registrar=lambda *a: {},
        actor_id="fixture-bff", lease_owner="fixture-worker",
    )
    # Governance proposal transport has independent tests; a missing mapping in
    # this fixture remains pending without manufacturing financial approval.
    monkeypatch.setattr(coordinator, "_route_or_reuse_risk_policy_owner_decision_request", lambda **kw: kw)
    monkeypatch.setattr(persona_module, "_chain_paper_capital_coordinator", lambda tenant: coordinator)
    return SimpleNamespace(bff=route_harness.client, store=store, api=api, record=record,
                           provisioning=route_harness.store, personas=route_harness.read_store,
                           reader=reader, policies=policies, approvals=approvals,
                           pool_id=pool.pool_id,
                           coordinator=coordinator, transport=transport)


def patch(world, profile, key="edit"):
    return world.bff.patch("/bff/personas/persona-test", json={"risk": profile}, headers=_headers("operator-a", key))


def refetch(world):
    response = world.bff.get("/bff/personas/persona-test", headers={"Authorization": "Bearer viewer:viewer"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_normal_operator_patch_and_reload_preserve_original_request(normal_edit):
    world = normal_edit
    before = deepcopy(world.record.request_payload), world.record.request_hash
    for index, tier in enumerate(("medium", "high", "low")):
        response = patch(world, tier, str(index))
        assert response.status_code == 200, response.text
        change = response.json()["data"]["riskPolicyChange"]
        assert change == {"requestedRisk": tier, "effectiveRisk": tier, "status": "effective",
                          "policyVersion": "fixture-v2", "approvalId": "fixture-approved-" + tier}
        assert refetch(world)["riskPolicyChange"] == change
        assert refetch(world)["risk"] == tier
    original = world.provisioning.get("tenant-profile-test", "original-create")
    assert (original.request_payload, original.request_hash) == before


def test_pending_then_independent_approval_same_key_converges(normal_edit, monkeypatch):
    world = normal_edit
    approved_medium = world.approvals.pop(1)
    pending = patch(world, "medium").json()["data"]["riskPolicyChange"]
    assert pending["status"] == "pending_approval" and pending["effectiveRisk"] == "low"
    assert refetch(world)["riskPolicyChange"] == pending
    world.approvals.append(approved_medium)
    # Clear process cache to exercise restart-safe owner receipt idempotency.
    monkeypatch.setattr(persona_module, "_STRATEGY_PERSONA_BFF_IDEMPOTENCY", {})
    response = patch(world, "medium")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["riskPolicyChange"]["status"] == "effective"
    assert world.store.require(world.pool_id).metadata["risk_profile_change"]["revision"] == 1


def test_superseded_retry_returns_latest_risk_and_lost_response_recovers(normal_edit, monkeypatch):
    world = normal_edit
    world.transport.lost_response = True
    assert patch(world, "medium", "old").status_code == 200
    assert patch(world, "high", "new").status_code == 200
    monkeypatch.setattr(persona_module, "_STRATEGY_PERSONA_BFF_IDEMPOTENCY", {})
    response = patch(world, "medium", "old")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["riskPolicyChange"]["requestedRisk"] == "high"
    assert refetch(world)["risk"] == "high"


def test_capital_outage_does_not_succeed_from_persona_only(normal_edit):
    world = normal_edit
    original = deepcopy(world.personas.get_persona("persona-test"))
    world.transport.unavailable = True
    assert patch(world, "high").status_code == 503
    assert world.personas.get_persona("persona-test") == original
    assert refetch(world)["riskPolicyChange"]["status"] == "unavailable"


def test_persona_failure_after_capital_commit_retries_without_duplicate_intent(normal_edit, monkeypatch):
    world = normal_edit
    updater = world.personas.update_persona
    monkeypatch.setattr(world.personas, "update_persona", lambda *a, **k: None)
    def combined_patch():
        return world.bff.patch("/bff/personas/persona-test", json={"risk": "medium", "name": "Renamed"},
                               headers=_headers("operator-a", "combined-edit"))
    assert combined_patch().status_code == 503
    assert refetch(world)["riskPolicyChange"]["effectiveRisk"] == "medium"
    monkeypatch.setattr(world.personas, "update_persona", updater)
    assert combined_patch().status_code == 200
    assert world.store.require(world.pool_id).metadata["risk_profile_change"]["revision"] == 1


@pytest.mark.parametrize("auth", [None, "Bearer viewer:viewer"])
def test_normal_edit_requires_operator(normal_edit, auth):
    headers = {"Idempotency-Key": "no-permission"}
    if auth:
        headers["Authorization"] = auth
    response = normal_edit.bff.patch("/bff/personas/persona-test", json={"risk": "high"}, headers=headers)
    assert response.status_code in {401, 403}
    assert "risk_profile_change" not in normal_edit.store.require(normal_edit.pool_id).metadata


def test_foreign_tenant_and_missing_idempotency_cannot_write(normal_edit, monkeypatch):
    world = normal_edit
    response = world.bff.patch("/bff/personas/persona-test", json={"risk": "high"},
                               headers={"Authorization": "Bearer operator-a:operator"})
    assert response.status_code == 400
    _patch_tenant_payload(monkeypatch, lambda *a, **k: {"id": "foreign-tenant"})
    assert patch(world, "high").status_code == 404
    assert "risk_profile_change" not in world.store.require(world.pool_id).metadata


def test_durable_key_conflict_after_restart_and_unrelated_edits(normal_edit, monkeypatch):
    world = normal_edit
    assert patch(world, "medium").status_code == 200
    before = deepcopy(world.store.require(world.pool_id))
    monkeypatch.setattr(persona_module, "_STRATEGY_PERSONA_BFF_IDEMPOTENCY", {})
    assert patch(world, "high").status_code == 409
    response = world.bff.patch("/bff/personas/persona-test", json={"name": "Renamed", "successRate": 0.8},
                               headers=_headers("operator-a", "non-risk-edit"))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["name"] == "Renamed"
    assert response.json()["data"]["riskPolicyChange"]["effectiveRisk"] == "medium"
    assert world.store.require(world.pool_id) == before


def test_pending_gate_preserves_genuine_reductions(normal_edit, monkeypatch):
    from services.capital import risk_policy
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

    world = normal_edit
    world.approvals.pop(1)
    assert patch(world, "medium").status_code == 200
    monkeypatch.setattr(risk_policy, "configured_capital_owner_approval_reader", lambda *a, **kw: world.reader)
    algo = PaperExecutionAlgorithm(initial_cash=100000.0, capital_pool_id=world.pool_id,
                                  persona_binding_id="pcb-test", in_process_broker=True,
                                  capital_pool_reader=lambda _: world.store.require(world.pool_id).to_dict())
    algo._holding("TEST").Quantity = 100.0
    algo._instrument_positions[("pcb-test", "TEST", "eq:TEST")] = {
        "persona_binding_id": "pcb-test", "normalised_underlying": "TEST", "instrument_id": "eq:TEST",
        "symbol": "TEST", "kind": "cash_share", "quantity": 100.0, "multiplier": 1.0,
        "average_entry_price": 100.0, "mark_price": 100.0, "status": "open",
    }
    for side, qty, accepted in (("buy", 10, False), ("sell", 50, True), ("sell", 100, True), ("sell", 150, False)):
        gate = algo.EvaluateOrderRiskGate(symbol="TEST", side=side, quantity=qty, price=100.0,
                                          instrument_id="eq:TEST", persona_binding_id="pcb-test")
        assert gate["accepted"] is accepted, gate


def test_approved_edit_changes_sizing_and_order_gate_in_one_running_runtime(normal_edit, monkeypatch):
    from services.capital import risk_policy
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

    world = normal_edit
    monkeypatch.setattr(risk_policy, "configured_capital_owner_approval_reader", lambda *a, **kw: world.reader)
    algo = PaperExecutionAlgorithm(initial_cash=100000.0, capital_pool_id=world.pool_id, persona_binding_id="pcb-test",
                                  capital_pool_reader=lambda _: world.store.require(world.pool_id).to_dict(),
                                  in_process_broker=True)

    def size_and_gate():
        policy = algo._resolve_active_risk_policy()
        sizing = algo._compute_submission_sizing_evidence(
            symbol="TEST", underlying="TEST", instrument_id="eq:TEST", kind="cash_share", side="buy",
            requested_quantity=500.0, ref_price=100.0, multiplier=1.0, contract=None, policy=policy,
            q_before=0.0, is_opening_or_increasing=True, raw_cash_before=100000.0,
            effective_cash_before=100000.0, spendable_cash_before=100000.0, cash_reserve_required=0.0,
            reserved_cash=0.0, reserved_margin=0.0, equity_before=100000.0, gross_before=0.0,
            signed_net_before=0.0, underlying_before=0.0, underlying_same_dir_before=0.0,
            margin_reserved_before=0.0, active_reservations_count=0,
        )
        gate = algo.EvaluateOrderRiskGate(symbol="TEST", side="buy", quantity=500.0, price=100.0,
                                          instrument_id="eq:TEST", persona_binding_id="pcb-test")
        return sizing, gate

    low_size, low_gate = size_and_gate()
    assert not low_gate["accepted"]
    response = patch(world, "medium")
    assert response.status_code == 200, response.text
    medium_size, medium_gate = size_and_gate()
    assert medium_gate["accepted"], medium_gate
    assert low_size["permitted_units"] < medium_size["permitted_units"]
