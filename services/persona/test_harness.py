"""Safety and integration coverage of the durable production owner routes."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from services.persona.harness import (
    ALWAYS_ON_TEXT_BUDGET, HarnessSurface, HarnessVersion, PersonaPatch,
    PersistentHarnessOwner, digest, precheck,
)
from services.persona.runtime_profile import build_persona_runtime_profile
from services.persona.trade_reflection_pipeline import ReflectionRequest, TradeReflectionPipeline
from services.persona.write_owner import (
    CreatePersonaRequest, PatchPersonaRequest, PersistentPersonaOwner, create_app,
)
from services.runtime_auth_inbound import encode_jwt_hs256

BASE = {
    "identity_inputs": ["Paper research persona; propose evidence-cited changes."],
    "guardrails": {"allowed_tools": ["research.read"], "forbidden_solo_actions": ["trial_admission", "harness_patch"]},
    "consult_policy": [{"action": "trial_admission", "required_roles": ["risk_persona"]},
                       {"action": "harness_patch", "required_roles": ["risk_persona", "red_team"]}],
    "tool_guidance": {"research.read": "Read governed research."},
    "skills": [{"name": "harness", "trigger": "When reviewing harness changes",
                "scripts": ["services/persona/harness.py"], "guidance": "Precheck first."}],
    "workflow": {"cadence_seconds": 3600, "steps": ["observe", "consult", "review"]},
    "cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8},
    "memory_retrieval_policy": {"scopes": ["own_private"], "max_items": 10, "minimum_confidence": 0.8},
}
ROOT = "/api/personas/p-alpha/harness"
SECRET = "harness-test-jwt-only"


def headers(tenant="tenant-a", roles=None):
    token = encode_jwt_hs256({"sub": "owner-actor", "roles": roles or ["persona.admin"],
                             "tenant_id": tenant, "exp": 4102444800}, secret=SECRET)
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}


class Provider:
    name = "test-only"
    model = "fixture"

    def reflect(self, *, facts, trigger):
        return {"expected_vs_actual": {"thesis": "Review supplied episode"},
                "attribution": "unknown", "counterfactuals": [], "lesson_candidates": []}


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", SECRET)
    path = tmp_path / "personas.json"
    owner = PersistentPersonaOwner.from_json_path(path)
    artifact = TradeReflectionPipeline(Provider()).process(ReflectionRequest(
        request_id="fixture", persona_id="p-alpha", trade_episode_ids=("episode-fixture",),
        trigger="episode_closed", facts={"environment": "paper", "status": "closed"}))
    owner.create(CreatePersonaRequest(actor_id="owner-actor", persona_id="p-alpha", name="Alpha",
                                     mandate="Paper analysis", tenant_id="tenant-a",
                                     metadata={"trade_reflections": [artifact]}))
    # Test setup only: lifecycle authorization has its own existing test suite.
    raw = owner._records.get("p-alpha")
    updated = {**raw, "lifecycle_state": "paper_owner"}
    assert owner._records.compare_and_set("p-alpha", raw, updated)[0]
    receipts = {}

    def verify_adoption(persona, candidate, ref):
        # Stand-in for the downstream shadow/adoption owner: exact receipt binding.
        return receipts.get(ref) == {"tenant_id": persona.tenant_id,
                                     "persona_id": persona.persona_id, **candidate}

    harness = PersistentHarnessOwner(owner, adoption_verifier=verify_adoption)
    client = TestClient(create_app(owner=owner, harness_owner=harness))
    evidence = {"kind": "trade_reflection", "ref": artifact["reflection_id"],
                "moment": artifact["facts_snapshot_ref"]}
    return client, owner, harness, path, receipts, evidence


def proposal(evidence, **changes):
    data = {"patch_id": "patch-1", "persona_id": "p-alpha", "session_id": "session-1",
            "base_version": 1, "patch_type": "behavior",
            "proposed_change": {"identity_inputs": ["Paper analysis: cite evidence before proposing."]},
            "evidence_refs": [evidence], "expected_effect": "Improve evidence citation",
            "rollback_target": 1}
    return {**data, **changes}


def initialize(system):
    client = system[0]
    response = client.post(ROOT, headers=headers(), json=BASE)
    assert response.status_code == 201, response.text
    return response.json()


def submit(system, **changes):
    response = system[0].post(ROOT + "/patches", headers=headers(), json=proposal(system[-1], **changes))
    assert response.status_code == 201, response.text
    return response.json()


def receipt(system, patch_id="patch-1"):
    state = system[2].read("p-alpha")
    proposed = state["patches"][patch_id]["proposal"]
    base = state["versions"][proposed["base_version"] - 1]["surface"]
    candidate, reasons = precheck(HarnessSurface.model_validate(base), PersonaPatch.model_validate(proposed))
    assert not reasons
    system[4]["adopt-1"] = {"tenant_id": "tenant-a", "persona_id": "p-alpha", "proposal": proposed,
                            "candidate_digest": digest(candidate.model_dump(mode="json"))}
    return {"adoption_ref": "adopt-1"}


def test_real_owner_route_roundtrip_restart_and_rollback(system):
    client, owner, harness, path, _, _ = system
    assert client.get(ROOT, headers=headers()).json()["reason"] == "harness_not_initialized"
    initial = initialize(system)
    submit(system)
    checked = client.post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "previewed"
    activated = client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json=receipt(system))
    assert activated.status_code == 200, activated.text
    assert activated.json()["version"] == 2
    fresh = PersistentPersonaOwner.from_json_path(path)
    fresh_client = TestClient(create_app(owner=fresh))
    state = fresh_client.get(ROOT, headers=headers(roles=["viewer"])).json()
    assert state["active_version"] == 2
    assert state["active_experiment"] == "patch-1"
    assert state["versions"][0] == initial["versions"][0]
    schema_root = Path(__file__).resolve().parents[2] / "docs/contracts"
    for version in state["versions"]:
        jsonschema.validate(version, json.loads((schema_root / "persona_harness.v1.schema.json").read_text()))
    jsonschema.validate(state["patches"]["patch-1"]["proposal"], json.loads((schema_root / "persona_harness_patch.v1.schema.json").read_text()))
    runtime = build_persona_runtime_profile(fresh.get("p-alpha").model_dump()).to_dict()
    assert runtime["harness"]["active_version"] == 2
    jsonschema.validate(runtime, json.loads(Path(__file__).with_name("runtime_profile.schema.json").read_text()))
    response = fresh_client.post(ROOT + "/rollback", headers=headers(), json={"patch_id": "patch-1", "reason": "Observed regression"})
    assert response.status_code == 200, response.text
    final = PersistentHarnessOwner(PersistentPersonaOwner.from_json_path(path)).read("p-alpha")
    assert final["active_version"] == 1
    assert final["versions"] == state["versions"]
    assert final["active_experiment"] is None
    assert final["events"][-1]["reason"] == "Observed regression"
    assert final["patches"]["patch-1"]["status"] == "rolled_back"


@pytest.mark.parametrize(("patch_type", "change", "reason"), [
    ("tool_profile", {"guardrails": {"allowed_tools": ["research.read", "trade.execute"], "forbidden_solo_actions": BASE["guardrails"]["forbidden_solo_actions"]}}, "new_tool_permission"),
    ("behavior", {"guardrails": {"allowed_tools": ["research.read"], "forbidden_solo_actions": []}}, "removed_forbidden_solo_action"),
    ("consult_policy", {"consult_policy": []}, "removed_consult_trigger_or_reviewer"),
    ("consult_policy", {"consult_policy": [{"action": "harness_patch", "required_roles": ["risk_persona"]}]}, "removed_consult_trigger_or_reviewer"),
    ("behavior", {"identity_inputs": ["x" * ALWAYS_ON_TEXT_BUDGET]}, "always_on_text_budget_exceeded"),
    ("skill", {"skills": [{**BASE["skills"][0], "trigger": ""}]}, "invalid_candidate_schema"),
    ("skill", {"skills": [{**BASE["skills"][0], "trigger": "   "}]}, "blank_surface_text"),
    ("skill", {"skills": [{**BASE["skills"][0], "scripts": ["missing-script.py"]}]}, "skill_script_missing_or_outside_repository"),
    ("skill", {"skills": [{**BASE["skills"][0], "scripts": ["/etc/passwd"]}]}, "skill_script_missing_or_outside_repository"),
    ("skill", {"skills": [{**BASE["skills"][0], "scripts": ["../../etc/passwd"]}]}, "skill_script_missing_or_outside_repository"),
    ("threshold", {"cycle_thresholds": {"minimum_evidence_count": 1, "minimum_confidence": 0.8}}, "lowered_cycle_threshold"),
    ("workflow", {"workflow": {"cadence_seconds": 1, "steps": BASE["workflow"]["steps"]}}, "increased_workflow_frequency"),
    ("memory_policy", {"memory_retrieval_policy": {"scopes": ["own_private", "institutional"], "max_items": 10, "minimum_confidence": 0.8}}, "expanded_memory_retrieval"),
    ("behavior", {"guardrails": {**BASE["guardrails"], "environment": "live"}}, "invalid_candidate_schema"),
])
def test_static_refusals_are_durable(system, patch_type, change, reason):
    initialize(system)
    submit(system, patch_type=patch_type, proposed_change=change)
    response = system[0].post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert response.status_code == 422, response.text
    assert reason in response.json()["detail"]["reasons"]
    state = PersistentHarnessOwner(PersistentPersonaOwner.from_json_path(system[3])).read("p-alpha")
    assert state["active_version"] == 1 and len(state["versions"]) == 1
    assert reason in state["events"][-1]["reasons"]


@pytest.mark.parametrize("field", ["risk_limits", "capital_limits", "environment", "environment_ceiling", "execution_authority", "live_permissions", "allowed_tools"])
def test_immutable_surface_rejected(system, field):
    initialize(system)
    submit(system, proposed_change={field: {"enabled": True}})
    result = system[0].post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert result.status_code == 422
    assert "immutable_or_wrong_patch_surface" in result.json()["detail"]["reasons"]


@pytest.mark.parametrize("field", ["evidence_refs", "expected_effect", "rollback_target", "patch_type"])
def test_required_proposal_provenance(system, field):
    initialize(system)
    body = proposal(system[-1])
    body.pop(field)
    assert system[0].post(ROOT + "/patches", headers=headers(), json=body).status_code == 422
    assert not system[2].read("p-alpha")["patches"]


def test_empty_or_unresolved_evidence_fails_closed(system):
    initialize(system)
    body = proposal(system[-1], evidence_refs=[])
    assert system[0].post(ROOT + "/patches", headers=headers(), json=body).status_code == 422
    submit(system, evidence_refs=[{**system[-1], "ref": "nonexistent-trajectory"}])
    response = system[0].post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert "trajectory_evidence_unresolved" in response.json()["detail"]["reasons"]


def test_production_default_activation_keeps_base_with_recorded_reason(system):
    initialize(system)
    submit(system)
    client = TestClient(create_app(owner=system[1]))
    assert client.post(ROOT + "/patches/patch-1/precheck", headers=headers()).status_code == 200
    response = client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json={"adoption_ref": "caller-claimed-approval"})
    assert response.status_code == 422
    state = system[2].read("p-alpha")
    assert state["active_version"] == 1
    assert state["events"][-1]["reasons"] == ["adoption_evidence_unavailable_or_denied"]


def test_only_one_experiment_and_no_stale_precheck_replay(system):
    initialize(system)
    submit(system)
    submit(system, patch_id="patch-2")
    client = system[0]
    for patch in ("patch-1", "patch-2"):
        assert client.post(ROOT + f"/patches/{patch}/precheck", headers=headers()).status_code == 200
    assert client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json=receipt(system)).status_code == 200
    response = client.post(ROOT + "/patches/patch-2/activate", headers=headers(), json={"adoption_ref": "adopt-1"})
    assert response.status_code == 422
    assert {"active_experiment_exists", "stale_base_version"}.issubset(response.json()["detail"]["reasons"])
    assert system[2].read("p-alpha")["active_experiment"] == "patch-1"
    assert client.post(ROOT + "/patches/patch-1/precheck", headers=headers()).status_code == 409


def test_evidence_is_revalidated_at_activation(system):
    initialize(system)
    submit(system)
    client, owner = system[:2]
    client.post(ROOT + "/patches/patch-1/precheck", headers=headers())
    proof = receipt(system)
    owner.patch("p-alpha", PatchPersonaRequest(actor_id="owner-actor", metadata={"trade_reflections": []}))
    response = client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json=proof)
    assert response.status_code == 422
    assert "trajectory_evidence_unresolved" in response.json()["detail"]["reasons"]


def test_authentication_tenant_and_roles(system):
    client = system[0]
    assert client.get(ROOT, headers={"X-Tenant-Id": "tenant-a"}).status_code == 401
    assert client.get(ROOT, headers=headers("tenant-b")).status_code == 403
    assert client.post(ROOT, headers=headers(roles=["viewer"]), json=BASE).status_code == 403
    assert client.post(ROOT, headers=headers(roles=["operator"]), json=BASE).status_code == 403
    assert client.get(ROOT, headers={**headers(), "X-Tenant-Id": "tenant-b"}).status_code == 403


@pytest.mark.parametrize("state", ["draft", "research_only", "live_owner", "frozen", "retired"])
def test_nonpaper_lifecycle_cannot_initialize(system, state):
    raw = system[1]._records.get("p-alpha")
    assert system[1]._records.compare_and_set("p-alpha", raw, {**raw, "lifecycle_state": state})[0]
    response = system[0].post(ROOT, headers=headers(), json=BASE)
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_owner_required"


def test_generic_metadata_cannot_bypass_harness_owner(system):
    client = system[0]
    bad = client.patch("/api/personas/p-alpha", headers=headers(), json={"actor_id": "owner-actor", "metadata": {"persona_harness": {"active_version": 99}}})
    assert bad.status_code == 422, bad.text
    create = client.post("/api/personas", headers=headers(), json={"actor_id": "owner-actor", "name": "evil", "mandate": "test", "metadata": {"persona_harness": {}}})
    assert create.status_code == 422, create.text


def test_parallel_initialization_has_one_winner(system):
    client = system[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: client.post(ROOT, headers=headers(), json=BASE).status_code, range(2)))
    assert sorted(results) == [201, 409]
    assert len(system[2].read("p-alpha")["versions"]) == 1


def test_runtime_profile_invalid_harness_never_claims_ready():
    profile = build_persona_runtime_profile({"persona_id": "p-alpha", "metadata": {
        "persona_harness": {"active_version": 1, "versions": [{}]}}}).to_dict()
    assert profile["harness"]["status"] == "unavailable"
    assert profile["harness"]["reason"] == "invalid_harness_record"


def test_machine_schemas_match_models():
    root = Path(__file__).resolve().parents[2] / "docs/contracts"
    for model, name in [(HarnessVersion, "persona_harness.v1"), (PersonaPatch, "persona_harness_patch.v1")]:
        schema = json.loads((root / f"{name}.schema.json").read_text())
        schema.pop("$id")
        schema.pop("$schema")
        assert schema == model.model_json_schema()


def test_deployed_control_plane_mount_exposes_harness_routes(system, monkeypatch):
    import importlib.util
    import sys
    import services.persona.write_owner as write_owner

    monkeypatch.setattr(write_owner, "build_persona_owner", lambda: system[1])
    path = Path(__file__).resolve().parents[1] / "control-plane/persona/main.py"
    monkeypatch.syspath_prepend(str(path.parent))
    name = "persona_harness_deployed_mount_test"
    spec = importlib.util.spec_from_file_location(name, path)
    service = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, service)
    spec.loader.exec_module(service)
    client = TestClient(service.app)
    assert client.post(ROOT, headers=headers(), json=BASE).status_code == 201
    assert client.post(ROOT + "/patches", headers=headers(), json=proposal(system[-1])).status_code == 201
    response = client.post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert response.status_code == 200, response.text
    response = client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json={"adoption_ref": "untrusted"})
    assert response.status_code == 422, response.text
    assert client.get(ROOT, headers=headers()).json()["active_version"] == 1


def test_parallel_activation_has_exactly_one_experiment(system):
    initialize(system)
    client, _, harness = system[:3]
    for patch_id in ("patch-1", "patch-2"):
        submit(system, patch_id=patch_id)
        assert client.post(ROOT + f"/patches/{patch_id}/precheck", headers=headers()).status_code == 200
        receipt(system, patch_id)
        system[4][patch_id] = copy.deepcopy(system[4]["adopt-1"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda patch_id: client.post(
            ROOT + f"/patches/{patch_id}/activate", headers=headers(), json={"adoption_ref": patch_id}
        ), ("patch-1", "patch-2")))
    assert sorted(r.status_code for r in responses) == [200, 422]
    state = harness.read("p-alpha")
    assert len(state["versions"]) == 2
    assert sum(row["status"] == "committed" for row in state["patches"].values()) == 1


def test_versions_remain_append_only_after_rollback(system):
    from datetime import timedelta
    initialize(system)
    client = system[0]
    submit(system)
    client.post(ROOT + "/patches/patch-1/precheck", headers=headers())
    assert client.post(ROOT + "/patches/patch-1/activate", headers=headers(), json=receipt(system)).status_code == 200
    assert client.post(ROOT + "/rollback", headers=headers(), json={"patch_id": "patch-1", "reason": "Reject candidate"}).status_code == 200
    submit(system, patch_id="patch-2")
    client.post(ROOT + "/patches/patch-2/precheck", headers=headers())
    assert client.post(ROOT + "/patches/patch-2/activate", headers=headers(), json=receipt(system, "patch-2")).status_code == 422
    after_cooldown = system[2].adoption.now() + timedelta(days=8)
    system[2].adoption.now = lambda: after_cooldown
    assert client.post(ROOT + "/patches/patch-2/activate", headers=headers(), json=receipt(system, "patch-2")).status_code == 200
    state = system[2].read("p-alpha")
    assert [row["version"] for row in state["versions"]] == [1, 2, 3]
    assert state["versions"][-1]["parent_version"] == 1
    assert state["active_version"] == 3


def test_internal_unknown_action_cannot_bypass_adoption(system):
    from services.persona.harness import HarnessError
    initialize(system)
    submit(system)
    with pytest.raises(HarnessError, match="unknown_harness_action"):
        system[2].operate("p-alpha", "patch-1", "typo", "owner-actor")
    assert system[2].read("p-alpha")["active_version"] == 1
