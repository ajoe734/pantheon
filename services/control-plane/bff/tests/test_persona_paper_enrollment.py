"""Paper enrolment through the real BFF entry points and the real neighbouring owners.

Real here: the BFF provisioning coordinator (``coordinate`` and the reconcile
step), the Persona owner app (lifecycle, pool seed and harness routes via its
own HTTP surface and the BFF's HTTP port), the Governance approval app, the
shared ``ApprovalEvidence`` verification the Persona owner runs, the pool
store, the harness validation, the cron registrar and the cycle observe
bundle.  Stand-ins only for owners this change does not touch: the Capital,
Registry and Deployment saga owners (the existing coordinator test transport),
the Registry artifact lister and the cron gateway transport.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import threading
import urllib.error
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.persona_paper_enrollment import (
    PersonaPaperEnrollment,
    lifecycle_decision_id,
    render_harness_surface_v1,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from services.control_plane.bff.ports.persona_write_owner import PersonaRegistryHttpWritePort
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _record_and_store,
    _schedule_receipt,
)
from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.governance.approval_authority import ApprovalEvidence, ApprovalReader
from services.persona.persona_cycle_controller import CycleOwners
from services.persona.write_owner import CreatePersonaRequest
from services.runtime_auth_inbound import encode_jwt_hs256

REPO = Path(__file__).resolve().parents[4]
PERSONA_DIR = REPO / "services" / "control-plane" / "persona"
SECRET = "enrolment-persona-secret-0123456789"
GOV_SECRET = "enrolment-governance-secret-0123456789"
TENANT = "tenant-a"
PERSONA = "persona-a"
GATE = "pantheon-persona-provisioner"
PROPOSER = "persona-enrollment-proposer"
CHECKSUM = "sha256:" + "a" * 64


def _jwt(secret: str, sub: str, roles: list[str], extra: Mapping[str, Any] | None = None) -> str:
    claims = {
        "sub": sub, "roles": roles, "tenant_id": TENANT, "allowed_tenants": [TENANT],
        "exp": 4102444800, "iss": "enrol", "aud": "enrol", **dict(extra or {}),
    }
    return "Bearer " + encode_jwt_hs256(claims, secret=secret)


class GovernanceBridge:
    """Owner-transport view of the real Governance app under one verified principal."""

    def __init__(self, client: TestClient, token: str, fallback: Any = None) -> None:
        self.client, self.token, self.fallback = client, token, fallback
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def _real(self, path: str) -> bool:
        return "apv-persona-enroll-" in path

    def get(self, owner: str, path: str):
        if owner == "governance" and self._real(path):
            response = self.client.get(path, headers={"Authorization": self.token})
            return None if response.status_code == 404 else self._ok(response)
        return self.fallback.get(owner, path)

    def list(self, owner: str, path: str):
        return self.fallback.list(owner, path)

    def patch(self, owner: str, path: str, payload):
        return self.fallback.patch(owner, path, payload)

    def post(self, owner: str, path: str, payload):
        if owner == "governance" and (
            self._real(path) or self._real(str(payload.get("decision_id", "")))
        ):
            self.posts.append((path, dict(payload)))
            key = hashlib.sha256(json.dumps([path, payload], sort_keys=True).encode()).hexdigest()
            response = self.client.post(
                path, json=dict(payload),
                headers={"Authorization": self.token, "Idempotency-Key": key},
            )
            return self._ok(response)
        return self.fallback.post(owner, path, payload)

    @staticmethod
    def _ok(response):
        if response.status_code >= 400:
            raise ConnectionError(f"governance {response.status_code}: {response.text[:200]}")
        return response.json()


class RuntimeCron:
    """Minimal cron gateway: the real registrar writes and reads job rows through it."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def gateway_call(self, method: str, params: Mapping[str, Any], **_: Any):
        if method == "cron.list":
            return {"jobs": deepcopy(self.jobs), "hasMore": False}
        if method == "cron.add":
            job = {**dict(params), "id": f"job-{len(self.jobs) + 1}"}
            self.jobs.append(job)
            return job
        if method == "cron.update":
            job = next(j for j in self.jobs if j["id"] == params["id"])
            job.update(params["patch"])
            return job
        if method == "cron.remove":
            self.jobs = [j for j in self.jobs if j["id"] != params["id"]]
            return {"removed": True}
        raise AssertionError(method)


def _load_persona_main(name: str):
    spec = importlib.util.spec_from_file_location(name, PERSONA_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # the caller put PERSONA_DIR on sys.path (monkeypatch)
    return module


def _approved_entry(persona_id: str = PERSONA, rid: str = "artifact-a", **over: Any) -> dict[str, Any]:
    return {
        "registry_id": rid, "artifact_state": "approved", "checksum": CHECKSUM,
        "approval_decision_id": "gate-baseline-1", "rollback_target": "rollback-no-trade",
        "approved_at": "2026-11-01T00:00:00+00:00", "owner_tenant": TENANT,
        "metadata": {"persona_id": persona_id, "execution_context": "paper",
                     "persona_capital_binding_id": "pcb-1"},
        **over,
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    from services.governance import main as governance_main
    from services.governance.pg_store import ApprovalDecisionStore, JsonlGovernanceAuditStore

    for key, value in {
        "PERSONA_AUTH_MODE": "strict", "PERSONA_JWT_SECRET": SECRET,
        "PERSONA_JWT_ISSUER": "enrol", "PERSONA_JWT_AUDIENCE": "enrol",
        "PANTHEON_BFF_JWT_SECRET": SECRET, "PANTHEON_BFF_JWT_ISSUER": "enrol",
        "PANTHEON_BFF_JWT_AUDIENCE": "enrol",
        "PANTHEON_PERSONA_SERVICE_TOKEN": "enrol-service-token",
        "PERSONA_STORE_PATH": str(tmp_path / "personas.json"),
        "PERSONA_CAPABILITY_STORE_PATH": str(tmp_path / "caps.json"),
        "PERSONA_POOL_STORE_PATH": str(tmp_path / "pools.json"),
        "PERSONA_TRAINING_TARGET_GOVERNANCE_BASE_URL": "http://governance.invalid",
        "PERSONA_GOVERNANCE_SERVICE_TOKEN": "persona-verifier-token",
        "PANTHEON_GOVERNANCE_JWT_SECRET": GOV_SECRET,
        "PANTHEON_GOVERNANCE_JWT_ISSUER": "enrol", "PANTHEON_GOVERNANCE_JWT_AUDIENCE": "enrol",
    }.items():
        monkeypatch.setenv(key, value)
    # Real Governance app over its real (JSON) decision store.
    monkeypatch.setattr(governance_main, "store", ApprovalDecisionStore(storage_path=str(tmp_path / "gov.json")))
    monkeypatch.setattr(governance_main, "audit_store", JsonlGovernanceAuditStore(str(tmp_path / "audit.jsonl")))
    governance = TestClient(governance_main.app)
    gate_token = _jwt(GOV_SECRET, GATE, ["automated_gate"])
    proposer_token = _jwt(GOV_SECRET, PROPOSER, ["approval_proposer"])

    # The Persona owner verifies a decision with the real shared ApprovalEvidence
    # logic; only the HTTP read is served by the real Governance app above.
    def read_decision(_reader, decision_id):
        response = governance.get(f"/api/governance/approvals/{decision_id}",
                                  headers={"Authorization": gate_token})
        from services.governance.approval_authority import ApprovalInvalid
        if response.status_code != 200:
            raise ApprovalInvalid("Governance denied exact decision read")
        return ApprovalEvidence.model_validate(response.json())

    monkeypatch.setattr(ApprovalReader, "get", read_decision)

    monkeypatch.syspath_prepend(str(PERSONA_DIR))  # the pool store lazily imports its policy sibling
    module = _load_persona_main("persona_main_enrolment_module")
    from persona_strategy_pool import PersonaStrategyPoolStore

    entries: dict[str, dict[str, Any]] = {"artifact-a": _approved_entry()}
    module.POOL_STORE = PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda rid, tenant: entries.get(rid, {}),
        owner_lister=lambda pid, tenant: [
            e for e in entries.values()
            if e["metadata"].get("persona_id") == pid and e["artifact_state"] == "approved"
        ],
    )
    owner = module.PERSONA_OWNER_API.state.persona_owner
    persona_client = TestClient(module.app)

    def opener(request, *, timeout):
        response = persona_client.request(
            request.get_method(), request.full_url, content=request.data,
            headers=dict(request.header_items()),
        )
        if response.is_error:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "owner rejected",
                                         response.headers, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    port = PersonaRegistryHttpWritePort(base_url="http://testserver", service_token="enrol-service-token",
                                        opener=opener)

    def make_persona(persona_id: str = PERSONA, *, lifecycle: str = "research_only", **over: Any) -> None:
        owner.create(CreatePersonaRequest(
            actor_id="op", persona_id=persona_id, name=persona_id, mandate="Paper momentum research",
            strategy_family="momentum", tenant_id=TENANT, **over,
        ))
        if lifecycle != "draft":
            response = persona_client.patch(
                f"/api/personas/{persona_id}/lifecycle",
                headers={"Authorization": "Bearer enrol-service-token", "X-Tenant-Id": TENANT},
                json={"actor_id": "operator-bff", "target_state": "research_only"},
            )
            assert response.status_code == 200, response.text

    make_persona()

    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    fake = FakeOwnerTransport(governance_subject=GATE)
    # Drive the real coordinator saga to completion against the saga-owner stand-ins.
    runtime_rows: list[dict[str, Any]] = []
    gate_bridge = GovernanceBridge(governance, gate_token, fake)
    proposer_bridge = GovernanceBridge(governance, proposer_token, fake)
    world = type("World", (), {})()
    world.__dict__.update(
        store=store, record=record, ids=ids, fake=fake, owner=owner, port=port, client=persona_client,
        governance=governance, gate=gate_bridge, proposer=proposer_bridge, entries=entries,
        module=module, runtime_rows=runtime_rows, make_persona=make_persona, tmp_path=tmp_path,
        gate_token=gate_token,
    )

    def runtime_reader(plan_id: str, pool_id: str):
        return deepcopy(runtime_rows)

    def coordinator(lease_owner: str = "worker-1", **over: Any) -> PersonaProvisioningCoordinator:
        options: dict[str, Any] = dict(
            store=store, transport=gate_bridge, schedule_registrar=_schedule_receipt,
            lease_owner=lease_owner, actor_id="pantheon-persona-provisioner", governance_actor_id=GATE,
            runtime_binding_reader=runtime_reader,
            persona_owner_provider=lambda: port,
            governance_proposer=proposer_bridge, governance_proposer_actor_id=PROPOSER,
            agent_provisioner=lambda pid: {"status": "ready"},
        )
        options.update(over)
        return PersonaProvisioningCoordinator(**options)

    world.coordinator = coordinator

    def activate_runtime() -> None:
        runtime_rows[:] = [{
            "binding_id": "rb-enrol-1", "runtime_id": "runtime-1", "status": "active",
            "plan_id": ids.deployment_plan_id, "capital_pool_id": ids.capital_pool_id,
            "persona_capital_binding_id": ids.persona_capital_binding_id,
            "deployment_stage": "paper",
            "metadata": {"persona_id": PERSONA, "tenant_id": TENANT},
        }]

    world.activate_runtime = activate_runtime
    return world


def _persona(world) -> dict[str, Any]:
    return world.owner.get(PERSONA).model_dump(mode="json")


def _eligible_by_registrar(world) -> set[str]:
    """The real cron registrar's daily-cycle reconcile: which personas it schedules."""
    from services.control_plane.cron.persona_cron_registrar import PersonaCronRegistrar

    cron = RuntimeCron()
    registrar = PersonaCronRegistrar(cron)
    registrar.reconcile_daily_cycles(world.owner.list_json())
    return {
        json.loads(job["payload"]["text"])["persona_id"]
        for job in cron.jobs if "payload" in job
    }


def _observe(world) -> dict[str, Any]:
    persona = next(p for p in world.owner.list_json() if p["persona_id"] == PERSONA)
    owners = CycleOwners(
        world.owner, world.module.POOL_STORE, env={},
        telemetry_reader=lambda tenant: [], feedback_owner=_NoFeedback(),
    )
    return owners.observe(persona)


class _NoFeedback:
    def cycle_references(self, **_: Any):
        return []


def _complete_paper_admission(world):
    """Real ``coordinate`` entry: capital -> approvals -> binding -> deployment -> schedule."""
    coordinator = world.coordinator()
    done = coordinator.coordinate(world.record)
    assert done.state == "provisioning" and done.current_step == "schedule_registered", done.error
    return done


# ---------------------------------------------------------------------------
# AC1 reproduce
# ---------------------------------------------------------------------------

def test_reproduce_unenrolled_paper_persona_is_invisible_to_the_daily_cycle(world):
    done = _complete_paper_admission(world)
    # Paper capital binding is active but the runtime is not yet up: nothing advances.
    assert done.references["paper_enrollment"]["status"] == "not_eligible"
    world.activate_runtime()
    binding = world.fake.objects[("capital", f"/api/bindings/{world.ids.persona_capital_binding_id}")]
    assert binding["status"] == "active"
    assert _persona(world)["lifecycle_state"] == "research_only"
    assert _eligible_by_registrar(world) == set()
    bundle = _observe(world)
    assert bundle["harness_version"] is None and "harness_not_initialized" in bundle["unavailable"]
    assert "strategy_pool" in bundle["unavailable"]


def test_production_callers_of_pool_seed_and_harness_initialise_exist_only_in_the_port():
    hits: dict[str, list[str]] = {"strategy-pool/seed": [], "/harness\"": []}
    for root in ("services", "integrations"):
        for path in (REPO / root).rglob("*.py"):
            name = path.name
            if name.startswith("test_") or "/tests/" in str(path) or name == "conftest.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "strategy-pool/seed" in text and "@app.post" not in text:
                hits["strategy-pool/seed"].append(str(path.relative_to(REPO)))
            if "/harness\"," in text and "dependency=\"persona_harness_owner\"" in text:
                hits["/harness\""].append(str(path.relative_to(REPO)))
    port = "services/control-plane/bff/ports/persona_write_owner.py"
    assert hits["strategy-pool/seed"] == [port]
    assert hits["/harness\""] == [port]


# ---------------------------------------------------------------------------
# AC2, AC3, AC6 enrolment end to end
# ---------------------------------------------------------------------------

def test_reconcile_enrols_a_paper_persona_through_governed_lifecycle_pool_and_harness(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)

    outcome = world.coordinator("reconciler").reconcile_paper_enrollment(record)

    readback = outcome.references["paper_enrollment"]
    assert readback["status"] == "enrolled", readback
    persona = _persona(world)
    assert persona["lifecycle_state"] == "paper_owner"
    # Each lifecycle step is a real Governance decision, proposed by the
    # independent proposer and approved by the paper automated gate identity.
    steps = readback["steps"]["lifecycle"]
    assert [(s["from"], s["to"]) for s in steps] == [
        ("research_only", "consultable"), ("consultable", "paper_owner"),
    ]
    for step in steps:
        decision = world.governance.get(
            f"/api/governance/approvals/{step['governance_decision_id']}",
            headers={"Authorization": world.gate_token},
        ).json()
        assert decision["decision_state"] == "decided" and decision["decision"] == "approved"
        assert decision["target_type"] == "persona_lifecycle_transition"
        assert decision["owner_user_id"] == PROPOSER and decision["actor_id"] == GATE
        assert decision["actor_role"] == "automated_gate"
    assert persona["metadata"]["last_lifecycle_governance_decision_id"] == steps[-1]["governance_decision_id"]
    assert steps[-1]["governance_decision_id"] == lifecycle_decision_id(
        TENANT, PERSONA, "consultable", "paper_owner")

    # Pool version 1 from the owner records; harness version 1 from the persona record.
    pool = world.module.POOL_STORE.current(PERSONA, TENANT)
    assert pool["pool_version"] == 1
    assert [m["artifact_ref"] for m in pool["members"]] == ["artifact-a"]
    assert pool["members"][0]["baseline_ref"] == "gate-baseline-1"
    harness = persona["metadata"]["persona_harness"]
    assert harness["active_version"] == 1
    surface = harness["versions"][0]["surface"]
    assert surface == render_harness_surface_v1(persona)
    assert surface["identity_inputs"][0] == "Mandate: Paper momentum research"

    # The cycle sees it: the registrar schedules it and observe carries both.
    assert _eligible_by_registrar(world) == {PERSONA}
    bundle = _observe(world)
    assert bundle["harness_version"] == 1
    assert bundle["pool"]["members"] and "strategy_pool" not in bundle["unavailable"]
    assert "harness_not_initialized" not in bundle["unavailable"]


def test_enrolment_runs_from_coordinate_when_paper_is_already_up(world):
    world.activate_runtime()
    done = _complete_paper_admission(world)
    assert done.references["paper_enrollment"]["status"] == "enrolled"
    assert _persona(world)["lifecycle_state"] == "paper_owner"
    assert done.state == "provisioning"  # paper admission outcome is unchanged


def test_replay_is_idempotent_and_survives_restart(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    first = world.coordinator("a").reconcile_paper_enrollment(record)
    proposals = [p for p in world.gate.posts if p[0] == "/api/governance/approvals"]
    again = world.coordinator("b").reconcile_paper_enrollment(first)
    assert again.references["paper_enrollment"]["status"] == "enrolled"
    assert [p for p in world.gate.posts if p[0] == "/api/governance/approvals"] == proposals
    assert len(world.module.POOL_STORE._records.get(PERSONA)["versions"]) == 1
    assert _persona(world)["metadata"]["persona_harness"]["active_version"] == 1
    assert len(_persona(world)["metadata"]["persona_harness"]["versions"]) == 1


def test_concurrent_replicas_converge_without_duplicates(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            world.coordinator(name).reconcile_paper_enrollment(record)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(f"replica-{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    # A replica that lost the lease skipped; a follow-up pass completes the rest.
    final = world.coordinator("last").reconcile_paper_enrollment(
        world.store.get(record.tenant_id, record.idempotency_key))
    assert final.references["paper_enrollment"]["status"] == "enrolled"
    assert _persona(world)["lifecycle_state"] == "paper_owner"
    assert len(world.module.POOL_STORE._records.get(PERSONA)["versions"]) == 1
    assert len(_persona(world)["metadata"]["persona_harness"]["versions"]) == 1


# ---------------------------------------------------------------------------
# AC2 refusal, AC3 fail closed, AC4/AC5 eligibility
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("roles", [["viewer"], ["approval_reader"]])
def test_refused_governance_decision_leaves_lifecycle_unchanged_with_reason(world, roles):
    _complete_paper_admission(world)
    world.activate_runtime()
    # A gate principal without the gate role is unavailable (cannot read) or
    # refused (can read, cannot review or decide); either way nothing advances.
    world.gate.token = _jwt(GOV_SECRET, GATE, roles)
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    outcome = world.coordinator("refused").reconcile_paper_enrollment(record)
    readback = outcome.references["paper_enrollment"]
    assert readback["status"] in {"refused", "unavailable"}, readback
    assert "governance" in readback["reason"], readback
    assert _persona(world)["lifecycle_state"] == "research_only"
    assert "last_lifecycle_governance_decision_id" not in _persona(world)["metadata"]


def test_missing_proposer_identity_fails_closed_with_reason(world, monkeypatch):
    monkeypatch.delenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID", raising=False)
    monkeypatch.delenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN", raising=False)
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    coordinator = world.coordinator("noproposer", governance_proposer=None, governance_proposer_actor_id=None)
    readback = coordinator.reconcile_paper_enrollment(record).references["paper_enrollment"]
    assert readback["status"] == "refused"
    assert readback["reason"] == "governance_proposer_identity_unconfigured"
    assert _persona(world)["lifecycle_state"] == "research_only"


@pytest.mark.parametrize("variant", ["no_artifact", "two_artifacts", "no_baseline"])
def test_pool_seed_fails_closed_with_the_owner_reason(world, variant):
    if variant == "no_artifact":
        world.entries.clear()
    elif variant == "two_artifacts":
        world.entries["artifact-b"] = _approved_entry(rid="artifact-b")
    else:
        world.entries["artifact-a"]["approval_decision_id"] = None
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    readback = world.coordinator("pool").reconcile_paper_enrollment(record).references["paper_enrollment"]
    assert readback["status"] == "refused"
    assert readback["reason"].startswith("pool_seed_refused:")
    assert _persona(world)["lifecycle_state"] == "research_only"
    assert "persona_harness" not in _persona(world)["metadata"]


def test_no_active_runtime_binding_means_nothing_advances(world):
    _complete_paper_admission(world)
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    readback = world.coordinator("idle").reconcile_paper_enrollment(record).references["paper_enrollment"]
    assert readback["status"] == "not_eligible" and readback["reason"] == "paper_runtime_binding_not_active"
    assert _persona(world)["lifecycle_state"] == "research_only"
    assert world.module.POOL_STORE._records.get(PERSONA) is None


def test_inactive_capital_binding_means_nothing_advances(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    world.fake.objects[("capital", f"/api/bindings/{world.ids.persona_capital_binding_id}")]["status"] = "revoked"
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    readback = world.coordinator("capital").reconcile_paper_enrollment(record).references["paper_enrollment"]
    assert (readback["status"], readback["reason"]) == ("not_eligible", "paper_capital_binding_not_active")
    assert _persona(world)["lifecycle_state"] == "research_only"


def test_non_paper_scope_is_untouched(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    world.fake.objects[("capital", f"/api/bindings/{world.ids.persona_capital_binding_id}")][
        "allowed_deployment_scope"] = "canary"
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    readback = world.coordinator("canary").reconcile_paper_enrollment(record).references["paper_enrollment"]
    assert readback["status"] == "not_eligible"
    assert _persona(world)["lifecycle_state"] == "research_only"


@pytest.mark.parametrize("state", ["failed", "compensated", "retired", "reserved"])
def test_failed_and_retired_ledger_records_are_not_enrolled(world, state):
    _complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)
    record.state = state
    skipped = world.coordinator("skip").reconcile_paper_enrollment(record)
    assert skipped.state == state
    assert _persona(world)["lifecycle_state"] == "research_only"


def test_archived_probe_and_non_trading_personas_are_not_enrolled(world):
    _complete_paper_admission(world)
    world.activate_runtime()
    enrollment = lambda: PersonaPaperEnrollment(  # noqa: E731
        tenant_id=TENANT, persona_id=PERSONA, capital_pool_id=world.ids.capital_pool_id,
        persona_capital_binding_id=world.ids.persona_capital_binding_id,
        deployment_plan_id=world.ids.deployment_plan_id, persona_owner=world.port,
        transport=world.gate, runtime_binding_reader=lambda *_: deepcopy(world.runtime_rows),
        governance_actor_id=GATE, proposer=world.proposer, proposer_actor_id=PROPOSER,
    )
    real_get = world.port.get_persona
    for patch, reason in (
        ({"status": "archived"}, "persona_not_active"),
        ({"metadata": {"probe": True}}, "persona_not_a_paper_trading_persona"),
        ({"metadata": {"persona_class": "consult_panel"}}, "persona_not_a_paper_trading_persona"),
        ({"metadata": {"trading": False}}, "persona_not_a_paper_trading_persona"),
    ):
        def get(pid, _patch=patch):
            base = real_get(pid)
            return {**base, **{k: v for k, v in _patch.items() if k != "metadata"},
                    "metadata": {**base["metadata"], **_patch.get("metadata", {})}}
        world.port.get_persona = get
        outcome = enrollment().enroll()
        assert (outcome.status, outcome.reason) == ("not_eligible", reason)
    world.port.get_persona = real_get
    assert _persona(world)["lifecycle_state"] == "research_only"


def test_harness_content_has_no_invented_guardrail_and_fails_closed_without_identity(world):
    surface = render_harness_surface_v1({"mandate": "m", "strategy_family": "f"})
    from integrations.openclaw.persona_agent_sync import PERSONA_AGENT_TOOL_PROFILE
    from services.persona.consult_triggers import GATED_DECISION_TYPES
    assert surface["guardrails"]["allowed_tools"] == PERSONA_AGENT_TOOL_PROFILE["allow"]
    assert surface["guardrails"]["forbidden_solo_actions"] == sorted(GATED_DECISION_TYPES)
    from services.control_plane.bff.persona_paper_enrollment import EnrollmentRefused
    with pytest.raises(EnrollmentRefused, match="harness_source_missing:mandate"):
        render_harness_surface_v1({"strategy_family": "f"})
    with pytest.raises(EnrollmentRefused, match="harness_source_missing:strategy_family"):
        render_harness_surface_v1({"mandate": "m"})
