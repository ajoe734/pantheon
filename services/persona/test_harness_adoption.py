"""Owner API -> real shadow evaluator -> adoption -> durable Memory writeback.

Provider answers, consultation owner responses and paired book facts are test
fixtures. The gate, CAS persistence, packet reader, routes and Memory service
are production code. These fixtures are not hosted-performance evidence.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.persona.cron_ooda_closure import append_ooda_packet
from services.persona.harness import PersistentHarnessOwner
from services.persona.harness_shadow_eval import run_shadow_comparison
from services.persona.consult_triggers import required_consultation
from services.persona.test_harness import ROOT, headers, initialize, submit, system  # noqa: F401
from services.persona.test_harness_shadow_eval import provider_by_version, remove, seed
from services.persona.write_owner import PersistentPersonaOwner, create_app


@pytest.fixture
def adoption(system, tmp_path, monkeypatch):
    client, owner, _, path, _, evidence = system
    harness = PersistentHarnessOwner(owner)  # Default production verifier, no allow-all receipt.
    clock = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    harness.adoption.now = lambda: clock[0]
    client = TestClient(create_app(owner=owner, harness_owner=harness))
    scoped = (client, owner, harness, path, {}, evidence)
    initialize(scoped)
    submit(scoped, patch_type="threshold",
           proposed_change={"cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8}})
    assert client.post(ROOT + "/patches/patch-1/precheck", headers=headers()).status_code == 200
    packets = tmp_path / "packets.jsonl"
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(packets))
    seed(packets, 3)
    comparison = run_shadow_comparison(harness, "p-alpha", "patch-1", packet_store_path=packets,
        provider=provider_by_version([remove(1, 0.2)], [remove(3, 0.9)]), actor="owner-actor")
    requirement = required_consultation("harness_patch", "patch-1", initiator_persona_id="p-alpha",
        touches=["cycle_thresholds"], target_version="1", candidate_digest=comparison["candidate_digest"])
    request = {"request_id": "consult-1", "tenant_id": "tenant-a", "from_persona_id": "p-alpha", "status": "published",
               "target_type": "harness_patch", "target_id": "patch-1",
               "metadata": {"panel": requirement.to_panel_metadata()}}
    memo = {"memo_id": "memo-1", "request_id": "consult-1", "status": "published",
            "target_type": "harness_patch", "target_id": "patch-1", "recommendation": "approve"}
    transcripts = [{"request_id": "consult-1", "events": [
        {"actor": {"actor_type": "persona", "actor_id": p.persona_id},
         "event_type": "persona_contribution", "content": {"recommendation": "approve"},
         "evidence_refs": ["review-evidence-" + p.persona_id]} for p in requirement.participants]}]
    harness.adoption.consultation_factory = lambda tenant: SimpleNamespace(
        get_memo=lambda ref: copy.deepcopy(memo) if ref == "memo-1" else None,
        get_request=lambda ref: copy.deepcopy(request), list_transcripts=lambda: copy.deepcopy(transcripts))
    deliveries = []
    harness.adoption.sender = lambda kind, payload: deliveries.append((kind, payload)) or delivery_receipt(kind, payload)
    return SimpleNamespace(client=client, owner=owner, harness=harness, clock=clock, packets=packets,
        request=request, memo=memo, transcripts=transcripts, deliveries=deliveries, system=scoped)


def activate(a):
    return a.client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json={"adoption_ref": "memo-1"})


def delivery_receipt(kind, payload):
    if kind == "governance":
        return {"decision_id": payload["decision_id"], "tenant_id": payload["tenant_id"],
                "target_id": payload["target_id"], "metadata": payload["metadata"]}
    return {"source_event_id": payload["source_event_id"], "source_event_type": "harness_experience",
            "sponsor_persona_id": payload["sponsor_persona_id"],
            "persona_memory_ids": ["test-memory-id"], "institutional_entry_ids": ["test-institutional-id"]}


def observations(a, *, delta=0.01, confounders=None, count=10):
    ids = []
    trading = datetime(2026, 10, 2, tzinfo=timezone.utc)
    while len(ids) < count:
        if trading.weekday() < 5:  # Test calendar only; production consumes owner trading dates.
            pid = "observation-" + trading.date().isoformat()
            facts = {"patch_id": "patch-1", "base_version": 1, "candidate_version": 2,
                "trading_date": trading.date().isoformat(), "criterion": "net_of_costs_total_return",
                "base_utility": 0.0, "candidate_utility": delta, "confidence": 0.8,
                "confounders": confounders or [], "evidence_refs": ["virtual-book-window:" + pid]}
            append_ooda_packet(a.packets, {"packet_id": pid, "tenant_id": "tenant-a",
                "persona_ids": ["p-alpha"], "environment": "paper", "updated_at": trading.isoformat(),
                "persona_cycle": {"phase": "terminal", "observe_bundle": {"persona_id": "p-alpha",
                    "harness_version": 2, "unavailable": [], "harness_observation": facts}}})
            ids.append(pid)
        trading += timedelta(days=1)
    a.clock[0] = trading + timedelta(days=1)
    return ids


def observe(a, ids):
    return a.client.post(ROOT + "/patches/patch-1/observe", headers=headers(), json={"packet_ids": ids})


@pytest.mark.parametrize(("delta", "confounders", "outcome"), [
    (0.01, [], "helped"), (-0.01, [], "hurt"), (0.0, [], "no_effect"),
    (0.01, ["regime_changed"], "confounded"), (None, [], "confounded")])
def test_production_entrypoints_record_four_outcomes_and_memory(adoption, tmp_path, monkeypatch, delta, confounders, outcome):
    from services.memory import main as memory
    from services.memory.persona_memory_store import PersonaMemoryStore
    from services.memory.institutional_memory_store import InstitutionalMemoryStore
    from services.memory.test_main import JWT_SECRET, _make_jwt
    a = adoption
    persona_path, institutional_path = tmp_path / "pm.json", tmp_path / "im.json"
    pm, im = PersonaMemoryStore(persona_path), InstitutionalMemoryStore(institutional_path)
    monkeypatch.setattr(memory, "_persona_store", lambda: pm)
    monkeypatch.setattr(memory, "_store", lambda: im)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    memory_client = TestClient(memory.app)
    token = _make_jwt(roles=["memory-writer"], tenant_id="tenant-a")

    def send(kind, payload):
        a.deliveries.append((kind, payload))
        if kind == "governance":
            return delivery_receipt(kind, payload)
        result = memory_client.post("/api/memory/writebacks/learn-feedback", json=payload,
                                    headers={"Authorization": "Bearer " + token})
        assert result.status_code in {200, 201}, result.text
        return result.json()
    a.harness.adoption.sender = send
    assert activate(a).status_code == 200
    ids = observations(a, delta=delta, confounders=confounders)
    response = observe(a, ids)
    assert response.status_code == 200, response.text
    experience = response.json()["experience"]
    assert experience["outcome"] == outcome
    assert experience["cue"] and experience["lesson"] and experience["confidence"] == 0.8
    stored = PersonaMemoryStore(persona_path).find_by_source_event(
        source_event_type="harness_experience", source_event_id=experience["source_event_id"])
    assert len(stored) == 1 and stored[0].content["structured_payload"]["outcome"] == outcome
    assert stored[0].content["structured_payload"]["evidence_refs"] == experience["evidence_refs"]
    assert (any(k == "governance" for k, _ in a.deliveries)) == (outcome == "hurt")
    assert observe(a, ids).json()["experience"] == experience
    fresh = PersistentHarnessOwner(PersistentPersonaOwner.from_json_path(a.system[3]))
    assert fresh.read("p-alpha")["active_experiment"] is None
    submit(a.system, patch_id="patch-2", base_version=2, rollback_target=2, patch_type="threshold",
           proposed_change={"cycle_thresholds": {"minimum_evidence_count": 4, "minimum_confidence": 0.8}})
    assert a.harness.read("p-alpha")["patches"]["patch-2"]["experience_refs"] == [experience]
    fresh.adoption.now = lambda: a.clock[0]
    assert fresh.adoption.cooldown_reasons(fresh.read("p-alpha"),
        a.harness.read("p-alpha")["patches"]["patch-2"]) == ["component_cooldown:cycle_thresholds"]
    assert a.client.post(ROOT + "/patches/patch-2/precheck", headers=headers()).status_code == 200
    response = a.client.post(ROOT + "/patches/patch-2/activate", headers=headers(), json={"adoption_ref": "memo-1"})
    assert "component_cooldown:cycle_thresholds" in response.json()["detail"]["reasons"]
    if outcome == "hurt":
        assert a.client.post(ROOT + "/rollback", headers=headers(),
            json={"patch_id": "patch-1", "reason": "Apply hurt rollback proposal"}).status_code == 200


@pytest.mark.parametrize("change", ["veto", "missing_answer", "digest", "tenant", "keep_base", "missing_memo", "conditional"])
def test_missing_or_unbound_approval_never_adopts(adoption, change):
    a = adoption
    if change == "veto":
        a.transcripts[0]["events"][-1]["content"]["recommendation"] = "reject"
    elif change == "missing_answer":
        a.transcripts[0]["events"].pop()
    elif change == "digest":
        a.request["metadata"]["panel"]["gated_decision"]["candidate_digest"] = "other"
    elif change == "tenant":
        a.request["tenant_id"] = "other"
    elif change == "missing_memo":
        a.memo["status"] = "draft"
    elif change == "conditional":
        a.transcripts[0]["events"][-1]["content"]["recommendation"] = "approve_with_conditions"
    else:
        row = a.harness.read("p-alpha")["patches"]["patch-1"]["comparisons"][-1]
        a.harness.record_comparison("p-alpha", "patch-1", {**row, "decision": "KEEP_BASE"}, "owner-actor")
    response = activate(a)
    assert response.status_code == 422, response.text
    assert a.harness.read("p-alpha")["active_version"] == 1
    assert bool(a.deliveries) == (change in {"veto", "missing_answer", "conditional"})


def test_unavailable_observation_is_persisted_and_tenant_checked(adoption):
    a = adoption
    assert activate(a).status_code == 200
    assert observe(a, ["nonexistent"]).status_code == 422
    row = a.harness.read("p-alpha")["patches"]["patch-1"]
    assert "observation_window_incomplete" in row["observation_checks"][-1]["reasons"]
    assert a.harness.read("p-alpha")["active_experiment"] == "patch-1"
    ids = observations(a)
    assert a.client.post(ROOT + "/patches/patch-1/observe", headers=headers("other"),
                         json={"packet_ids": ids}).status_code == 403
    assert observe(a, [ids[0]] * 10).status_code == 422
    assert observe(a, ids[:9]).status_code == 422


def test_delivery_failure_survives_restart_and_replays(adoption):
    a = adoption
    assert activate(a).status_code == 200
    ids = observations(a, delta=-0.01)
    def fail(*args):
        raise ConnectionError("test owner outage")
    a.harness.adoption.sender = fail
    assert observe(a, ids).status_code == 200
    state = a.harness.read("p-alpha")
    assert len(state["outbox"]) == 2 and all(r["error"] == "ConnectionError" for r in state["outbox"].values())
    fresh = PersistentHarnessOwner(PersistentPersonaOwner.from_json_path(a.system[3]))
    deliveries = []
    fresh.adoption.sender = lambda kind, payload: deliveries.append((kind, payload)) or delivery_receipt(kind, payload)
    fresh.adoption.deliver("p-alpha", "owner-actor")
    assert len(deliveries) == 2
    fresh.adoption.deliver("p-alpha", "owner-actor")
    assert len(deliveries) == 2


def test_outside_paper_is_governance_proposal_without_activation(adoption):
    a = adoption
    raw = a.owner._records.get("p-alpha")
    assert a.owner._records.compare_and_set("p-alpha", raw, {**raw, "lifecycle_state": "live_owner"})[0]
    submit(a.system, patch_id="outside-paper")
    state = a.harness.read("p-alpha")
    assert state["active_version"] == 1
    assert state["patches"]["outside-paper"]["status"] == "governance_required"
    assert a.deliveries[0][0] == "governance"
    assert a.deliveries[0][1]["metadata"]["operator_required"] is True


# The multi-service fixture runs actual Consultation, Memory, Governance and
# Evolution applications. Only provider HTTP supplies scripted persona answers.
from services.consultation.tests.test_persona_panel_integration import (  # noqa: E402,F401
    stack, _config, _provider, _provider_fake,
)


def test_real_panel_and_authenticated_memory_and_governance_transport(adoption, stack, monkeypatch):
    from dataclasses import replace
    from services.consultation.client import ConsultationServiceClient
    from services.consultation.workflow_executor import run_tick
    from services.consultation.workflow_state import WorkflowStateStore
    from services.memory.test_main import _make_jwt
    from services.persona.consult_triggers import RISK_PERSONA_ID
    from services.evolution import main as evolution
    a = adoption
    monkeypatch.setattr(evolution, "store", evolution.EvolutionDecisionStore(
        storage_path=str(stack["root"] / "adoption-evolution.json")))
    monkeypatch.setenv("CONSULTATION_HANDOFF_ALLOWED_TENANTS", "tenant-a")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", _make_jwt(roles=["memory-writer"], tenant_id="tenant-a"))
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", stack["memory"])
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", stack["api"])
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", stack["evolution"])
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "test-evolution-owner-token")
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "tenant-a")
    a.harness.adoption.consultation_factory = a.harness.adoption._consultation
    a.harness.adoption.sender = a.harness.adoption._send
    panel = a.request["metadata"]["panel"]
    requirement = required_consultation("harness_patch", "patch-1", initiator_persona_id="p-alpha",
        touches=["cycle_thresholds"], target_version="1", candidate_digest=panel["gated_decision"]["candidate_digest"])
    consult = ConsultationServiceClient(base_url=stack["api"], tenant_id="tenant-a")
    with _provider_fake({"p-alpha": "approve", RISK_PERSONA_ID: "approve"}) as provider:
        cfg = replace(_config(stack, f"http://127.0.0.1:{provider.server_address[1]}/contribute"), tenant_id="tenant-a")
        created = consult.open_persona_panel(requirement, persona_id="p-alpha", trace_id="adoption-integration")
        result = run_tick(config=cfg, state=WorkflowStateStore(cfg.state_path), provider=_provider(cfg))
        assert result["completed"] == 1, result
    memo = consult.list_memos(request_id=created["request_id"], status="published")[0]
    response = a.client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json={"adoption_ref": memo["memo_id"]})
    assert response.status_code == 200, response.text
    ids = observations(a, delta=-0.01)
    response = observe(a, ids)
    assert response.status_code == 200, response.text
    assert response.json()["experience"]["outcome"] == "hurt"
    outbox = a.harness.read("p-alpha")["outbox"]
    assert all(row["status"] == "delivered" for row in outbox.values()), [(r["kind"], r.get("error")) for r in outbox.values()]
    governance = next(row for row in outbox.values() if row["kind"] == "governance")
    assert governance["receipt"]["decision_state"] == "proposed"
    assert governance["receipt"]["risk_level"] == "medium"
    assert governance["receipt"]["metadata"]["reason"] == "hurt_rollback_proposal"
