"""Pool envelope publish through the real route, publisher and coordinator.

Only owner HTTP is faked: Registry/Governance/Deployment/Capital via the
provisioning ``FakeOwnerTransport`` (real owner validation helpers), and the
RuntimeManager owner via a client with the real single-runtime guard.  The
Deployment dispatch executes the REAL ``dispatch_to_runtime_manager`` so the
second pool version proves ``replace`` (not ``deploy``) moves the binding.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.persona_pool_publisher import (
    InMemoryPublishRecords,
    OwnerPoolReader,
    PersonaPoolPublisher,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.routes import lifecycle as personas_lifecycle
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _coordinator,
    _record_and_store,
    _schedule_receipt,
)
from services.control_plane.governance.deployment_plan import StagePlanner
from services.control_plane.persona.persona_strategy_pool import (
    AppendPoolVersionRequest,
    PersonaStrategyPoolStore,
    PoolMember,
)
from services.registry.models import RegistryEntry
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    build_strategy_artifact_registry_payload,
    load_strategy_artifact_registration,
    strategy_artifact_checksum,
)

from services.deployment.runtime_manager_dispatch_adapter import (  # noqa: E402
    RuntimeManagerClientError,
    dispatch_to_runtime_manager,
)

# pool_policy is a top-level sibling module of persona_strategy_pool.
_PERSONA_DIR = str(Path(__file__).resolve().parents[2] / "persona")

TENANT = "tenant-a"
PERSONA = "persona-a"
DIGEST = "sha256:" + "7" * 64


# ---------------------------------------------------------------------------
# RuntimeManager owner fake: real single-runtime guard and replace continuity.
# ---------------------------------------------------------------------------
class FakeRuntimeManager:
    def __init__(self) -> None:
        self.bindings: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.positions: dict[str, dict[str, Any]] = {}
        self.ledger: dict[str, list[str]] = {}

    def _new(self, request: dict[str, Any], runtime_id: str, extra: dict[str, Any]) -> dict[str, Any]:
        binding = {
            "binding_id": f"rb-{len(self.bindings) + 1}",
            "plan_id": request["plan_id"],
            "runtime_id": runtime_id,
            "capital_pool_id": request["capital_pool_id"],
            "artifact_id": request["artifact_id"],
            "artifact_version": request["artifact_version"],
            "deployment_mode": request["target_stage"],
            "execution_mode": request["target_stage"],
            "persona_capital_binding_id": request["persona_capital_binding_id"],
            "status": "active",
            "metadata": {
                **copy.deepcopy(request.get("metadata") or {}),
                "strategy_id": request["strategy_id"],
                **extra,
            },
        }
        self.bindings[binding["binding_id"]] = binding
        return binding

    def deploy(self, request):
        self.calls.append("deploy")
        if any(
            b["capital_pool_id"] == request["capital_pool_id"] and b["status"] == "active"
            for b in self.bindings.values()
        ):
            raise RuntimeManagerClientError(
                "single-runtime rule violation", status_code=409, error_code="BINDING_ERROR"
            )
        runtime_id = request.get("runtime_id") or f"runtime-{request['capital_pool_id']}"
        return copy.deepcopy(self._new(request, runtime_id, {}))

    def replace(self, runtime_id, request):
        self.calls.append("replace")
        old = self.bindings.get(request["current_binding_id"])
        if old is None or old["runtime_id"] != runtime_id or old["status"] != "active":
            raise RuntimeManagerClientError("current binding not replaceable", status_code=422)
        for field in ("capital_pool_id", "persona_capital_binding_id"):
            if request[field] != old[field]:
                raise RuntimeManagerClientError(f"{field} continuity violated", status_code=422)
        if request["target_stage"] != old["deployment_mode"]:
            raise RuntimeManagerClientError("replace must stay in stage", status_code=422)
        new = self._new(
            request, runtime_id,
            {"replacement_kind": "forward", "replacement_parent_binding_id": old["binding_id"]},
        )
        old["status"] = "retired"
        return {"operation": "forward_replace", "new_binding": copy.deepcopy(new),
                "old_binding": copy.deepcopy(old)}

    def get(self, binding_id):
        value = self.bindings.get(binding_id)
        return copy.deepcopy(value) if value else None

    def list_by_plan(self, plan_id):
        return [copy.deepcopy(b) for b in self.bindings.values() if b["plan_id"] == plan_id]

    def list_by_pool(self, pool_id):
        return [copy.deepcopy(b) for b in self.bindings.values() if b["capital_pool_id"] == pool_id]

    def reader(self, plan_id, pool_id):
        found = {b["binding_id"]: b for b in [*self.list_by_plan(plan_id), *self.list_by_pool(pool_id)]}
        return list(found.values())


def _authority_report(plan: dict[str, Any], persona_id: str, pcb: str) -> dict[str, Any]:
    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": plan["plan_id"],
        "plan_status": "approved",
        "target_stage": plan["target_stage"],
        "artifact_id": plan["artifact_id"],
        "artifact_version": plan["artifact_version"],
        "strategy_id": plan["strategy_id"],
        "approval_decision_id": plan["approval_decision_id"],
        "capital_pool_id": plan["capital_pool_id"],
        "sponsor_persona_id": persona_id,
        "persona_capital_binding_id": pcb,
        "deployment_plan_current_stage": plan["current_stage"],
        "deployment_plan_binding_id": plan.get("binding_id"),
        "deployment_plan_runtime_lifecycle": {},
        "deployment_plan_sha256": DIGEST,
        "deployment_plan_authority_sha256": DIGEST,
        "registry_entry_sha256": DIGEST,
        "approval_decision_sha256": DIGEST,
        "capital_pool_sha256": DIGEST,
        "capital_admissibility_sha256": DIGEST,
        "persona_capital_binding_sha256": DIGEST,
    }


class PublishOwnerTransport(FakeOwnerTransport):
    """Owner fakes + real StagePlanner + real Deployment dispatch adapter."""

    def __init__(self, runtime: FakeRuntimeManager, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.runtime = runtime
        self.refuse_dispatch = False
        self.queue_dispatch = False
        self.queued: list[Any] = []

    def _apply_post(self, owner, path, body):
        if owner == "deployment" and path == "/api/deployment/plans":
            response = super()._apply_post(owner, path, body)
            plan = self.objects[(owner, f"/api/deployment/plans/{body['plan_id']}")]
            from services.control_plane.governance.deployment_plan import DeploymentStage

            transition = StagePlanner().derive_transition_type(
                DeploymentStage(body["current_stage"]),
                DeploymentStage(body["target_stage"]),
                replacement=bool(body.get("binding_id")),
            )
            plan["transition_type"] = transition.value
            plan["runtime_action"] = StagePlanner().default_runtime_action(transition).value
            return response
        if owner == "deployment" and path.endswith("/dispatch"):
            if self.queue_dispatch:
                # Accepted asynchronously (outbox): executes only when drained.
                self.queue_dispatch = False
                self.queued.append(lambda: self._apply_post(owner, path, body))
                return {"accepted": True, "replayed": False}
            plan_id = path.split("/")[-2]
            plan = self.objects[("deployment", f"/api/deployment/plans/{plan_id}")]
            saga = {
                "saga_id": body["saga_id"], "plan_id": plan_id, "status": "awaiting_binding",
                "current_step": "binding_requested", "binding_id": None, "runtime_id": None,
                "approval_decision_id": plan["approval_decision_id"],
                "strategy_id": plan["strategy_id"], "artifact_id": plan["artifact_id"],
                "artifact_version": plan["artifact_version"],
                "capital_pool_id": plan["capital_pool_id"],
                "current_stage": plan["current_stage"], "target_stage": plan["target_stage"],
                "runtime_action": plan["runtime_action"],
            }
            meta = plan["metadata"]
            pcb = meta["persona_capital_binding_id"]
            report = _authority_report(plan, meta["persona_id"], pcb)
            result = dispatch_to_runtime_manager(
                saga=saga,
                deploy_context={
                    "sponsor_persona_id": meta["persona_id"],
                    "persona_capital_binding_id": pcb,
                    "persona_capital_binding_status": "active",
                    "allowed_deployment_scope": "paper",
                    "loader_checks_passed": True,
                    "plan_status": "approved",
                    "current_binding_id": plan.get("binding_id"),
                    "metadata": {**meta, "authoritative_loader_attestation": report},
                },
                client=self.runtime,
            )
            if result.succeeded():
                saga.update(status="binding_created", binding_id=result.binding_id)
            else:
                saga.update(status="failed", failure_reason=result.error_message)
            self._put("deployment", f"/api/deployment/sagas/{body['saga_id']}", saga)
            return {"deployment_saga": {"saga": saga}, "replayed": False}
        return super()._apply_post(owner, path, body)


# ---------------------------------------------------------------------------
# World: bootstrapped persona, approved member artifacts, two pool versions.
# ---------------------------------------------------------------------------
def _member_entry(name: str, symbol: str) -> dict[str, Any]:
    artifact = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])[
        "strategy_artifact"
    ]
    artifact["artifact_id"] = f"artifact-{name}"
    artifact["strategy_id"] = f"strategy-{name}"
    artifact["parameters"]["symbols"] = [symbol]
    artifact.pop("binding_intent", None)
    registry_id, admitted = build_strategy_artifact_registry_payload(
        {
            "registry_id": f"artifact-{name}",
            "artifact_state": "candidate",
            "strategy_artifact": artifact,
            "metadata": {"tenant_id": TENANT, "persona_id": PERSONA},
        }
    )
    entry = RegistryEntry(
        registry_id=registry_id, owner_tenant=TENANT,
        created_at="2026-07-15T00:00:00Z", updated_at="2026-07-15T00:00:00Z",
        **vars(admitted),
    ).to_dict()
    entry["artifact_state"] = "approved"
    assert entry["checksum"] == strategy_artifact_checksum(artifact)
    return entry


class World:
    def __init__(self) -> None:
        self.runtime = FakeRuntimeManager()
        self.transport = PublishOwnerTransport(self.runtime)
        store, record = _record_and_store()
        self.coordinator = PersonaProvisioningCoordinator(
            store=store, transport=self.transport,
            schedule_registrar=lambda p, pool, b: _schedule_receipt(p, pool, b),
            lease_owner="test-worker", actor_id="pantheon-persona-provisioner",
            runtime_binding_reader=self.runtime.reader,
        )
        self.coordinator.coordinate(record)
        self.ids = deterministic_provisioning_ids(record)
        self.entries = {
            "artifact-a": _member_entry("a", "2330.TW"),
            "artifact-b": _member_entry("b", "2317.TW"),
        }
        for rid, entry in self.entries.items():
            self.transport.objects[("registry", f"/api/registry/strategy-artifacts/{rid}")] = {
                "entry": copy.deepcopy(entry), "deployment_stage": "none",
            }
        # The persona's bootstrap runtime (Deployment dispatch executed it
        # through the real adapter: first plan => deploy, not replace).
        self.runtime.positions["runtime"] = {"2330.TW": 1000}
        self.runtime.ledger["runtime"] = ["fill-1"]
        self.pool_records = InMemoryPublishRecords()
        self.pool = PersonaStrategyPoolStore(
            self.pool_records,
            registry_reader=lambda ref, tenant: copy.deepcopy(self.entries.get(ref, {})),
        )
        self.publisher = PersonaPoolPublisher(
            coordinator=self.coordinator,
            pool_store=self.pool,
            records=InMemoryPublishRecords(),
        )

    def member(self, ref: str, weight: float) -> PoolMember:
        return PoolMember(
            artifact_ref=ref, checksum=self.entries[ref]["checksum"], stage="active",
            risk_weight=weight, baseline_ref=ref, joined_at="2026-07-15T00:00:00Z",
        )

    def append(self, members, expected: int, *, policy: bool = False):
        return self.pool.append(
            PERSONA, TENANT,
            AppendPoolVersionRequest(expected_version=expected, members=members, change_reason="test"),
            actor_id="operator-a",
            decision={"status": "applied"} if policy else None,
        )


@pytest.fixture
def world(monkeypatch) -> World:
    # pool_policy is a top-level sibling module of persona_strategy_pool; the
    # suite's import isolation resets sys.path per test, so add it here.
    monkeypatch.syspath_prepend(_PERSONA_DIR)
    return World()


def _client(world: World, monkeypatch, tmp_path, tenant: str = TENANT) -> TestClient:
    def identity(authorization, mfa_token=None, session_cookie=None):
        return OperatorIdentity(
            operator_id="operator-a", roles=["operator"], mfa_verified=False,
            claims={"sub": "operator-a", "tenant_id": tenant, "roles": ["operator"]},
            token_kind="structured",
        )

    read_store = create_in_memory_read_surface_ports()
    command_store = CommandStore(str(tmp_path / "commands.jsonl"))
    service = PersonaService(
        write_owner=read_store, read_store=read_store,
        ranking_write_owner=read_store, command_store=command_store,
    )
    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def _h(request, exc):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"message": str(detail)}})

    app.include_router(
        create_personas_router(
            service=service, get_read_store=lambda: read_store,
            get_command_store=lambda: command_store, extract_identity_fn=identity,
        )
    )
    monkeypatch.setattr(personas_lifecycle, "_pool_publisher", lambda authorization: world.publisher)
    monkeypatch.setattr(personas_lifecycle, "_ensure_persona_exists", lambda *a, **k: None)
    return TestClient(app)


PATH = f"/bff/personas/{PERSONA}/strategy-pool/publish"
H = {"Authorization": "Bearer operator-a"}


def _live(world: World) -> list[dict[str, Any]]:
    return [b for b in world.runtime.bindings.values() if b["status"] == "active"]


def test_two_pool_versions_move_binding_with_replace_not_deploy(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    boot_calls = list(world.runtime.calls)
    assert boot_calls == ["deploy"]
    boot = _live(world)[0]

    world.append([world.member("artifact-a", 0.5)], 0)
    first = client.post(PATH, json={}, headers=H)
    assert first.status_code == 200, first.text
    data1 = first.json()["data"]
    assert data1["status"] == "published" and data1["pool_version"] == 1
    assert world.runtime.calls == ["deploy", "replace"]

    world.append([world.member("artifact-a", 0.5), world.member("artifact-b", 0.5)], 1, policy=True)
    second = client.post(PATH, json={}, headers=H)
    assert second.status_code == 200, second.text
    data2 = second.json()["data"]
    assert data2["status"] == "published" and data2["pool_version"] == 2
    # Second version used replace; the single-runtime guard (deploy) never ran again.
    assert world.runtime.calls == ["deploy", "replace", "replace"]

    live = _live(world)
    assert len(live) == 1
    moved = live[0]
    # One runtime, one capital pool, one persona capital binding throughout.
    assert moved["runtime_id"] == boot["runtime_id"]
    assert moved["capital_pool_id"] == boot["capital_pool_id"] == world.ids.capital_pool_id
    assert moved["persona_capital_binding_id"] == world.ids.persona_capital_binding_id
    assert moved["artifact_id"] == data2["envelope_artifact_id"]
    assert moved["metadata"]["replacement_kind"] == "forward"
    assert world.runtime.bindings[boot["binding_id"]]["status"] == "retired"
    # Positions and ledger stay keyed to the unchanged runtime.
    assert world.runtime.positions["runtime"] == {"2330.TW": 1000}
    assert world.runtime.ledger["runtime"] == ["fill-1"]

    # Envelope pins every member version/checksum/weight and the pool version.
    entry = world.transport.objects[
        ("registry", f"/api/registry/strategy-artifacts/{data2['envelope_artifact_id']}")
    ]["entry"]
    assert entry["artifact_state"] == "approved"
    body = entry["metadata"]["strategy_artifact"]
    pins = {m["member_id"]: m for m in body["strategy_logic"]["members"]}
    assert set(pins) == {"artifact-a", "artifact-b"}
    for ref, pin in pins.items():
        assert pin["checksum"] == world.entries[ref]["checksum"]
        assert pin["version"] == world.entries[ref]["version"]
        assert pin["risk_weight"] == 0.5
    assert body["parameters"]["pool_version"] == 2
    assert data1["envelope_artifact_id"] in body["lineage"]["parent_registry_ids"]
    # Paper-scope automated governance approval, no human step.
    decision = world.transport.objects[
        ("governance", f"/api/governance/approvals/{data2['approval_decision_id']}")
    ]
    assert decision["decision"] == "approved" and decision["actor_role"] == "automated_gate"


def test_duplicate_publish_is_idempotent(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    assert client.post(PATH, json={}, headers=H).status_code == 200
    before_calls = list(world.runtime.calls)
    before_posts = sum(world.transport.mutations.values())
    again = client.post(PATH, json={"pool_version": 1}, headers=H)
    assert again.status_code == 200, again.text
    assert again.json()["data"]["idempotent_replay"] is True
    assert world.runtime.calls == before_calls
    assert sum(world.transport.mutations.values()) == before_posts
    assert len(_live(world)) == 1


def _refused(response, world, reason_fragment):
    assert response.status_code == 409, response.text
    assert reason_fragment in response.text
    # Previous envelope keeps running: still exactly the bootstrap binding.
    live = _live(world)
    assert len(live) == 1 and live[0]["artifact_id"] == world.ids.strategy_artifact_id
    assert world.runtime.calls == ["deploy"]


def test_member_checksum_mismatch_refused_previous_keeps_running(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    key = ("registry", "/api/registry/strategy-artifacts/artifact-a")
    world.transport.objects[key]["entry"]["checksum"] = "sha256:" + "0" * 64
    _refused(client.post(PATH, json={}, headers=H), world, "checksum")
    refusals = world.publisher.history(TENANT, PERSONA)["refusals"]
    assert refusals and "checksum" in refusals[-1]["reason"]


def test_embedded_body_tampered_with_matching_entry_checksum_refused(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    key = ("registry", "/api/registry/strategy-artifacts/artifact-a")
    body = world.transport.objects[key]["entry"]["metadata"]["strategy_artifact"]
    body["parameters"]["symbols"] = ["9999.TW"]  # entry + pool checksum unchanged
    _refused(client.post(PATH, json={}, headers=H), world, "embedded strategy artifact")
    refusals = world.publisher.history(TENANT, PERSONA)["refusals"]
    assert refusals and refusals[-1]["step"] == "member"


def test_embedded_body_identity_or_version_mismatch_refused(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    key = ("registry", "/api/registry/strategy-artifacts/artifact-a")
    world.transport.objects[key]["entry"]["version"] = "9.9.9"
    _refused(client.post(PATH, json={}, headers=H), world, "embedded strategy artifact")


def test_saga_readback_loss_after_dispatch_is_not_reported_as_previous_kept(
    world, monkeypatch, tmp_path
):
    import urllib.error

    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    real_get = world.transport.get

    def lossy_get(owner, path):
        if owner == "deployment" and "/sagas/" in path and "replace" in world.runtime.calls:
            raise urllib.error.URLError("readback lost")
        return real_get(owner, path)

    world.transport.get = lossy_get
    response = client.post(PATH, json={}, headers=H)
    world.transport.get = real_get
    assert "replace" in world.runtime.calls
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert "previous-envelope-keeps-running" not in response.text
    assert data["status"] in {"published", "dispatched"}
    history = world.publisher.history(TENANT, PERSONA)
    assert not history["refusals"]
    assert history["published"]["1"]["envelope_artifact_id"] == data["envelope_artifact_id"]
    assert _live(world)[0]["artifact_id"] == data["envelope_artifact_id"]


def test_queued_dispatch_with_lost_readback_is_not_refused_when_binding_later_moves(
    world, monkeypatch, tmp_path
):
    import urllib.error

    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    world.transport.queue_dispatch = True
    real_get = world.transport.get

    def lossy_get(owner, path):
        if owner == "deployment" and "/sagas/" in path and world.transport.queued:
            raise urllib.error.URLError("readback lost")
        return real_get(owner, path)

    world.transport.get = lossy_get
    response = client.post(PATH, json={}, headers=H)
    world.transport.get = real_get
    # Old binding is still active, but accepted work is pending: not a refusal.
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "dispatched"
    assert _live(world)[0]["artifact_id"] == world.ids.strategy_artifact_id
    history = world.publisher.history(TENANT, PERSONA)
    assert not history["refusals"]
    assert history["published"]["1"]["status"] == "dispatched"
    assert history["published"]["1"]["deployment_plan_id"] == data["deployment_plan_id"]

    assert len(world.transport.queued) == 1
    # The already accepted queued work executes later and moves the binding.
    for run in world.transport.queued:
        run()
    assert world.runtime.calls == ["deploy", "replace"]
    assert _live(world)[0]["artifact_id"] == data["envelope_artifact_id"]


def test_member_not_approved_refused(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    key = ("registry", "/api/registry/strategy-artifacts/artifact-a")
    world.transport.objects[key]["entry"]["artifact_state"] = "candidate"
    _refused(client.post(PATH, json={}, headers=H), world, "not approved")


def test_wrong_tenant_member_refused(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    key = ("registry", "/api/registry/strategy-artifacts/artifact-a")
    world.transport.objects[key]["entry"]["owner_tenant"] = "tenant-other"
    _refused(client.post(PATH, json={}, headers=H), world, "not a registry entry")


def test_wrong_current_binding_refused(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    # The running binding is retired out-of-band: nothing live to replace.
    for binding in world.runtime.bindings.values():
        binding["status"] = "retired"
    response = client.post(PATH, json={}, headers=H)
    assert response.status_code == 409 and "exactly one live RuntimeBinding" in response.text
    assert world.runtime.calls == ["deploy"]


def test_beyond_paper_refused(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    for stage in ("canary", "live"):
        response = client.post(PATH, json={"stage": stage}, headers=H)
        assert response.status_code == 403 and "paper" in response.text
    assert world.runtime.calls == ["deploy"]


def test_unapproved_envelope_never_reaches_deployment(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    original = world.transport._advance_registry_entry

    def deny(target, body):
        raise ValueError("HTTP 400: approval rejected")

    world.transport._advance_registry_entry = deny
    response = client.post(PATH, json={}, headers=H)
    world.transport._advance_registry_entry = original
    assert response.status_code == 409
    assert not [p for (o, p) in world.transport.objects if o == "deployment" and "pool" in p]
    assert world.runtime.calls == ["deploy"]


def test_dispatch_failure_records_reason_and_old_envelope_keeps_running(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    # Stale source: runtime-manager rejects replace (e.g. binding moved away).
    world.runtime.replace = lambda runtime_id, request: (_ for _ in ()).throw(
        RuntimeManagerClientError("current binding not replaceable", status_code=422)
    )
    response = client.post(PATH, json={}, headers=H)
    assert response.status_code == 409, response.text
    assert len(_live(world)) == 1
    assert _live(world)[0]["artifact_id"] == world.ids.strategy_artifact_id
    assert world.publisher.history(TENANT, PERSONA)["refusals"]
    assert "deploy" == world.runtime.calls[-1] or "replace" not in world.runtime.calls


def test_older_pool_version_refused_after_newer_published(world, monkeypatch, tmp_path):
    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    world.append([world.member("artifact-a", 0.5), world.member("artifact-b", 0.5)], 1, policy=True)
    assert client.post(PATH, json={"pool_version": 2}, headers=H).status_code == 200
    stale = client.post(PATH, json={"pool_version": 1}, headers=H)
    assert stale.status_code == 409 and "not newer" in stale.text


@pytest.mark.parametrize("owner_status,expected", [(404, 404), (503, 503), (500, 503), (None, 503)])
def test_real_pool_factory_translates_owner_errors(
    world, monkeypatch, tmp_path, owner_status, expected
):
    """Real _pool_publisher factory + real route; only the owner GET is faked."""
    import urllib.error
    from services.control_plane.bff.personas import service as persona_service

    real_factory = personas_lifecycle._pool_publisher
    client = _client(world, monkeypatch, tmp_path)
    monkeypatch.setattr(personas_lifecycle, "_pool_publisher", real_factory)
    monkeypatch.setattr(personas_lifecycle, "_POOL_PUBLISH_RECORDS", None)
    monkeypatch.setattr(persona_service, "_persona_provisioning_store", lambda: world.coordinator.store)
    monkeypatch.setenv("PANTHEON_PERSONA_API_URL", "http://persona-owner.invalid")
    error = (
        urllib.error.HTTPError("http://persona-owner.invalid/pool", owner_status, "x", None, None)
        if owner_status
        else urllib.error.URLError("connection refused")
    )

    def owner_get(*args, **kwargs):
        raise error

    monkeypatch.setattr(persona_service, "_get_json", owner_get)
    calls_before = list(world.runtime.calls)
    response = client.post(PATH, json={}, headers=H)
    assert response.status_code == expected, response.text
    assert world.runtime.calls == calls_before
    assert len(_live(world)) == 1
    record = personas_lifecycle._POOL_PUBLISH_RECORDS.get("tenant-a:persona-a")
    assert record is not None and record["refusals"]
    assert record["refusals"][-1]["step"] in {"pool", "owner_unavailable"}


@pytest.mark.parametrize("owner_read", ["runtime_bindings", "envelope_registry", "member_registry"])
def test_pre_dispatch_owner_read_failure_is_503_and_recorded(world, monkeypatch, tmp_path, owner_read):
    """Every authoritative pre-dispatch owner read fails closed with 503."""
    import urllib.error

    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    error = urllib.error.HTTPError("http://owner.invalid/read", 503, "unavailable", None, None)
    if owner_read == "runtime_bindings":
        def fail_binding_read(*args):
            raise error

        world.coordinator.runtime_binding_reader = fail_binding_read
    else:
        original = world.transport.get

        def get(owner, path):
            if owner == "registry" and (
                owner_read == "envelope_registry" or path.endswith("/artifact-a")
            ):
                raise error
            return original(owner, path)

        world.transport.get = get
    calls_before = list(world.runtime.calls)
    response = TestClient(client.app, raise_server_exceptions=False).post(PATH, json={}, headers=H)
    assert response.status_code == 503, response.text
    refusals = world.publisher.history(TENANT, PERSONA)["refusals"]
    assert refusals and refusals[-1]["step"] == "owner_unavailable"
    assert world.runtime.calls == calls_before


def test_initial_saga_get_failure_before_dispatch_fails_closed_503(
    world, monkeypatch, tmp_path
):
    import urllib.error

    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    real_get = world.transport.get
    real_post = world.transport.post
    dispatch_posts: list[str] = []

    def lossy_get(owner, path):
        if owner == "deployment" and "/sagas/" in path:
            raise urllib.error.URLError("owner read failed")
        return real_get(owner, path)

    def counting_post(owner, path, payload):
        if owner == "deployment" and path.endswith("/dispatch"):
            dispatch_posts.append(path)
        return real_post(owner, path, payload)

    world.transport.get = lossy_get
    world.transport.post = counting_post
    response = client.post(PATH, json={}, headers=H)
    world.transport.get = real_get
    world.transport.post = real_post
    assert response.status_code == 503, response.text
    assert dispatch_posts == []
    assert world.runtime.calls == ["deploy"]
    assert _live(world)[0]["artifact_id"] == world.ids.strategy_artifact_id
    history = world.publisher.history(TENANT, PERSONA)
    assert history["refusals"] and "owner path unavailable" in history["refusals"][-1]["reason"]
    assert "1" not in history["published"]


def test_retry_while_queued_and_saga_get_unavailable_preserves_uncertainty(
    world, monkeypatch, tmp_path
):
    import urllib.error

    client = _client(world, monkeypatch, tmp_path)
    world.append([world.member("artifact-a", 0.5)], 0)
    world.transport.queue_dispatch = True
    real_get = world.transport.get

    def lossy_get(owner, path):
        if owner == "deployment" and "/sagas/" in path and world.transport.queued:
            raise urllib.error.URLError("readback lost")
        return real_get(owner, path)

    world.transport.get = lossy_get
    first = client.post(PATH, json={}, headers=H)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "dispatched"

    def always_lossy(owner, path):
        if owner == "deployment" and "/sagas/" in path:
            raise urllib.error.URLError("readback still lost")
        return real_get(owner, path)

    world.transport.get = always_lossy
    retry = client.post(PATH, json={}, headers=H)
    world.transport.get = real_get
    assert retry.status_code == 200, retry.text
    data = retry.json()["data"]
    assert data["status"] == "dispatched"
    history = world.publisher.history(TENANT, PERSONA)
    assert not history["refusals"]
    assert history["published"]["1"]["status"] == "dispatched"
    assert len(world.transport.queued) == 1
    for run in world.transport.queued:
        run()
    assert world.runtime.calls == ["deploy", "replace"]
    assert _live(world)[0]["artifact_id"] == data["envelope_artifact_id"]
