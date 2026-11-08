"""Standalone contract tests for the prepared Evolution Programs router.

These tests build `create_evolution_programs_router()` into a bare FastAPI
app with fakes for every injected dependency -- they do not import
`main.py` and do not touch the live `/bff/evolution-programs*` routes
main.py currently serves. They characterize router.py's own behavior
against CHARACTERIZATION.md so a reviewer (and the future cutover task)
can see it holds the documented contract before it is ever wired in.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


from services.control_plane.bff.evolution.router import create_evolution_programs_router  # noqa: E402
from services.control_plane.bff.ports.evolution_program_commands import (  # noqa: E402
    EvolutionProgramCommandError,
    EvolutionProgramConflictError,
    EvolutionProgramNotFoundError,
)


class _FakeIdentity:
    def __init__(self, operator_id: str = "operator-1") -> None:
        self.operator_id = operator_id
        self.claims: Dict[str, Any] = {}


class _FakeReadStore:
    """Minimal durable-store double covering exactly the functions the
    router calls, mirroring read_store.py's real signatures/behavior.

    Read-only: there is no ``create_evolution_program``/
    ``patch_evolution_program`` here at all -- the U8A owner contract routes
    every write through the injected ``program_commands`` port instead.
    ``seed`` is a test-setup helper standing in for what the command port's
    fake would durably persist.
    """

    def __init__(self) -> None:
        self._programs: Dict[str, Dict[str, Any]] = {}
        self._decisions: List[Dict[str, Any]] = []

    def seed(self, program: Dict[str, Any]) -> None:
        self._programs[program["program_id"]] = program

    def list_evolution_programs(self) -> List[Dict[str, Any]]:
        items = list(self._programs.values())
        return sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not program_id:
            return None
        return self._programs.get(program_id)

    def list_evolution_program_runs(self, program_id: str) -> List[Dict[str, Any]]:
        if program_id not in self._programs:
            return []
        return [d for d in self._decisions if d.get("program_id") == program_id]

    def list_evolution_program_candidates(self, program_id: str) -> List[Dict[str, Any]]:
        if program_id not in self._programs:
            return []
        return [d for d in self._decisions if d.get("program_id") == program_id and d.get("status") == "pending"]


class _FakeProgramCommandPort:
    """Stub standing in for ``EvolutionServiceProgramCommandPort`` -- models
    the Evolution service owner API's create/PATCH(name) semantics without a
    real HTTP call, matching the fake used in
    ``test_evolution_program_owner_contract.py``."""

    def __init__(self, read_store: _FakeReadStore) -> None:
        self._read_store = read_store
        self._seq = 0

    async def create_program(self, *, caller_token, tenant_id, actor_id, name, idempotency_key):
        self._seq += 1
        program_id = f"p{self._seq}"
        program = {
            "id": program_id,
            "program_id": program_id,
            "tenant_id": tenant_id,
            "name": name,
            "status": "draft",
            "revision": 1,
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z",
            "created_by": actor_id,
        }
        self._read_store.seed(program)
        return program

    async def patch_program_name(self, *, caller_token, tenant_id, actor_id, program_id, name, expected_revision, idempotency_key):
        current = self._read_store.get_evolution_program(program_id)
        if current is None:
            raise EvolutionProgramNotFoundError(f"Evolution program not found: {program_id}")
        if int(current.get("revision") or 0) != expected_revision:
            raise EvolutionProgramConflictError(f"Evolution program {program_id} was modified concurrently")
        updated = dict(current)
        updated["name"] = name
        updated["revision"] = int(current["revision"]) + 1
        updated["updated_at"] = "2026-08-28T01:00:00Z"
        updated["updated_by"] = actor_id
        self._read_store.seed(updated)
        return updated


def _bff_error(status_code, code, message, reason, **extra):
    return HTTPException(status_code=status_code, detail={"code": code.value, "message": message, "reason": reason, **extra})


def _build_app(
    read_store: _FakeReadStore,
    *,
    submit_program_action=None,
    program_commands: Optional[_FakeProgramCommandPort] = None,
) -> TestClient:
    calls: Dict[str, Any] = {}

    def require_operator_role(identity):
        calls["operator_role_checked"] = True

    resolved_commands = program_commands if program_commands is not None else _FakeProgramCommandPort(read_store)

    router = create_evolution_programs_router(
        get_read_store=lambda: read_store,
        extract_identity=lambda authorization: _FakeIdentity(),
        require_read_role=lambda identity: None,
        require_operator_role=require_operator_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-08-28T00:00:00Z",
        submit_program_action=submit_program_action,
        program_commands=resolved_commands,
    )
    app = FastAPI()

    @app.exception_handler(EvolutionProgramCommandError)
    async def _handle_program_command_error(request, exc: EvolutionProgramCommandError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"message": str(exc)})

    app.include_router(router)
    client = TestClient(app)
    client._pantheon_calls = calls  # type: ignore[attr-defined]
    return client


def test_list_empty_uses_items_envelope():
    client = _build_app(_FakeReadStore())
    resp = client.get("/bff/evolution-programs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["page_info"] == {"next_page_token": None}
    assert "surfaces" in body["meta"]
    assert body["meta"]["snapshot_at"] == "2026-08-28T00:00:00Z"


def test_create_requires_name_and_persists_durably():
    store = _FakeReadStore()
    client = _build_app(store)

    bad = client.post("/bff/evolution-programs", json={})
    assert bad.status_code == 422

    created = client.post("/bff/evolution-programs", json={"name": "Alpha"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Alpha"
    assert body["program_id"] in store._programs
    assert client._pantheon_calls.get("operator_role_checked") is True

    # Durable: a fresh router instance over the same store sees the write.
    other_client = _build_app(store)
    listed = other_client.get("/bff/evolution-programs").json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["program_id"] == body["program_id"]


def _seed_program(store: _FakeReadStore, program_id: str, name: str, *, created_at: str, revision: int = 1) -> None:
    store.seed({
        "id": program_id,
        "program_id": program_id,
        "name": name,
        "status": "draft",
        "revision": revision,
        "created_at": created_at,
        "updated_at": created_at,
        "created_by": "op",
    })


def test_get_detail_404_and_data_envelope():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store)

    missing = client.get("/bff/evolution-programs/does-not-exist")
    assert missing.status_code == 404

    found = client.get("/bff/evolution-programs/p1")
    assert found.status_code == 200
    assert found.json()["data"]["program_id"] == "p1"


def test_patch_updates_whitelisted_fields_only():
    """PATCH accepts only ``name`` (plus the ``revision`` CAS precondition);
    every other field -- including the old ``status`` write path -- is
    rejected with 422 before the command port is ever called, and a
    whitelisted ``name`` update is durably persisted through the
    ``program_commands`` port."""
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store)

    resp = client.patch("/bff/evolution-programs/p1", json={"status": "paused", "unexpected_field": "ignored"})
    assert resp.status_code == 422

    resp = client.patch("/bff/evolution-programs/p1", json={"name": "P1 Renamed", "revision": 1})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "P1 Renamed"
    assert "unexpected_field" not in data
    assert store.get_evolution_program("p1")["name"] == "P1 Renamed"


def test_status_filter_is_csv_case_insensitive():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    store._programs["p1"]["status"] = "Active"
    _seed_program(store, "p2", "P2", created_at="2026-01-02T00:00:00Z")
    store._programs["p2"]["status"] = "paused"
    client = _build_app(store)

    resp = client.get("/bff/evolution-programs", params={"status": "active,PAUSED"})
    ids = {item["program_id"] for item in resp.json()["items"]}
    assert ids == {"p1", "p2"}

    resp = client.get("/bff/evolution-programs", params={"status": "archived"})
    assert resp.json()["items"] == []


def test_runs_and_candidates_404_on_missing_program():
    client = _build_app(_FakeReadStore())
    assert client.get("/bff/evolution-programs/missing/runs").status_code == 404
    assert client.get("/bff/evolution-programs/missing/candidates").status_code == 404


def test_action_without_injected_dispatch_is_501():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store, submit_program_action=None)

    resp = client.post("/bff/evolution-programs/p1/actions/approve", json={})
    assert resp.status_code == 501


def test_action_dispatches_through_injected_callable():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")

    captured = {}

    def submit_program_action(entity_type, entity_id, action_id, resolved_key, identity, payload, authorization=None):
        captured.update(
            entity_type=entity_type,
            entity_id=entity_id,
            action_id=action_id,
            resolved_key=resolved_key,
            payload=payload,
            authorization=authorization,
        )
        return {"status": "accepted"}

    client = _build_app(store, submit_program_action=submit_program_action)
    resp = client.post(
        "/bff/evolution-programs/p1/actions/approve",
        json={"reason": "looks good"},
        headers={"Authorization": "Bearer test-operator-token"},
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}
    assert captured == {
        "entity_type": "EvolutionProgram",
        "entity_id": "p1",
        "action_id": "approve",
        "resolved_key": "",
        "payload": {"reason": "looks good"},
        "authorization": "Bearer test-operator-token",
    }


def test_evolution_child_checks_accept_expression_controls_and_reject_forbidden_controls():
    import copy
    import pytest
    from services.control_plane.bff.evolution.service import (
        EvolutionService,
        validate_evolution_child_artifact,
    )
    from services.control_plane.bff.persona_provisioning_coordinator import (
        deterministic_provisioning_ids,
    )
    from services.control_plane.bff.test_persona_provisioning_coordinator import (
        FakeOwnerTransport,
        _coordinator,
        _record_and_store,
        _schedule_receipt,
    )
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        mutate_strategy_artifact,
    )

    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    _coordinator(store, transport, _schedule_receipt).coordinate(record)
    ids = deterministic_provisioning_ids(record)
    parent = transport.objects[
        ("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")
    ]["entry"]["metadata"]["strategy_artifact"]

    child = mutate_strategy_artifact(
        parent,
        parameter_updates={
            "strength_scale_k": 1.25,
            "strength_floor_momentum": 0.015,
            "lever_min_strength": 0.65,
            "notional_fraction_at_full_strength": 0.85,
        },
        new_artifact_id=f"{ids.strategy_artifact_id}-evo-child-1",
        new_version="1.0.1",
        source_run_ids=["vbt-evo-child-run-1"],
        parent_registry_id=ids.strategy_artifact_id,
    )

    svc = EvolutionService(_FakeReadStore())
    check_res = svc.validate_child_artifact(parent, child)
    assert check_res["valid"] is True
    assert check_res["mutated_controls"] == [
        "lever_min_strength",
        "notional_fraction_at_full_strength",
        "strength_floor_momentum",
        "strength_scale_k",
    ]

    for forbidden in (
        "max_leverage",
        "gross_limit",
        "net_limit",
        "allowed_asset_classes",
        "allow_pyramiding",
        "close_before_reverse",
        "prefer_future_when_short",
    ):
        with pytest.raises(StrategyArtifactValidationError):
            validate_evolution_child_artifact(
                parent,
                child,
                parameter_changes={forbidden: 0.5},
            )

    tampered_child = copy.deepcopy(child)
    tampered_child["expression_policy"]["parameters"]["allow_pyramiding"] = True
    with pytest.raises(StrategyArtifactValidationError):
        validate_evolution_child_artifact(parent, tampered_child)



def _expression_parent_and_ids():
    from services.control_plane.bff.persona_provisioning_coordinator import (
        deterministic_provisioning_ids,
    )
    from services.control_plane.bff.test_persona_provisioning_coordinator import (
        FakeOwnerTransport,
        _coordinator,
        _record_and_store,
        _schedule_receipt,
    )

    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    _coordinator(store, transport, _schedule_receipt).coordinate(record)
    ids = deterministic_provisioning_ids(record)
    parent = transport.objects[
        ("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")
    ]["entry"]["metadata"]["strategy_artifact"]
    return parent, ids


def _replay_records():
    """0050 SHORT/LONG decisions with recorded D-1 prices and marks (design E sequences)."""
    from services.execution.lean_runtime.test_expression_replay import _cash, _fut, _record

    cands = lambda: [_cash("0050", 104.95, 952), _fut("ssf:SRF:202611", "0050", 1000, 104.95, 8700.0, 2)]  # noqa: E731
    marks = {"eq:0050": [92.5], "ssf:SRF:202611": [92.5]}
    return [
        _record("d-short", "0050", "SHORT", 1.0, cands(), 300000.0, marks),
        _record("d-weak-long", "0050", "LONG", 0.6, cands(), 300000.0, {k: [110.0] for k in marks}),
    ]


def test_evolution_child_expression_change_is_replayed_before_qualification():
    import pytest
    from services.control_plane.bff.evolution.service import EvolutionService
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        mutate_strategy_artifact,
    )

    parent, ids = _expression_parent_and_ids()
    svc = EvolutionService(_FakeReadStore())

    def child_with(updates, n):
        return mutate_strategy_artifact(
            parent, parameter_updates=updates,
            new_artifact_id=f"{ids.strategy_artifact_id}-evo-replay-{n}",
            new_version=f"1.0.{n}", source_run_ids=[f"vbt-evo-replay-{n}"],
            parent_registry_id=ids.strategy_artifact_id,
        )

    # Parent lever_min_strength 0.5: the weak LONG (0.6) uses the future. Raising the
    # threshold above 0.6 makes it a cash share, which earns less on the recorded rise.
    worse = child_with({"lever_min_strength": 0.9}, 1)
    with pytest.raises(StrategyArtifactValidationError, match="child_replay_worse_than_parent"):
        svc.propose_child_artifact(parent, worse, replay_records=_replay_records())

    # Lowering it leaves the recorded outcomes at least as good: accepted with evidence.
    better = child_with({"lever_min_strength": 0.3}, 2)
    result = svc.propose_child_artifact(parent, better, replay_records=_replay_records())
    assert result["valid"] is True
    assert result["expression_replay"]["passed"] is True
    assert result["expression_replay"]["evaluated_controls"] == ["lever_min_strength"]
    assert result["expression_replay"]["child_total_pnl"] >= result["expression_replay"]["parent_total_pnl"]

    # The pre-qualification path refuses an instrument-affecting change with no records.
    with pytest.raises(StrategyArtifactValidationError, match="no_recorded_decisions"):
        svc.propose_child_artifact(parent, better, replay_records=[])
    # Controls that only reshape the view are not replayed (replay never recomputes direction).
    view_only = child_with({"strength_scale_k": 1.5}, 3)
    assert "expression_replay" not in svc.propose_child_artifact(parent, view_only, replay_records=[])
    # Plain validation without records stays backward compatible and says it did not replay.
    assert svc.validate_child_artifact(parent, better)["expression_replay"]["status"] == "not_evaluated"


def test_evolution_child_that_raises_a_risk_limit_is_rejected():
    import copy
    import pytest
    from services.control_plane.bff.evolution.service import validate_evolution_child_artifact
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        mutate_strategy_artifact,
    )

    parent, ids = _expression_parent_and_ids()
    child = mutate_strategy_artifact(
        parent, parameter_updates={"lever_min_strength": 0.4},
        new_artifact_id=f"{ids.strategy_artifact_id}-evo-risk", new_version="1.0.9",
        source_run_ids=["vbt-evo-risk"], parent_registry_id=ids.strategy_artifact_id,
    )
    for limit in ("max_leverage", "gross_limit", "net_limit"):
        for section in ("parameters", "expression_policy"):
            raised = copy.deepcopy(child)
            target = raised["parameters"] if section == "parameters" else raised["expression_policy"]["parameters"]
            target[limit] = 3.0  # the operator's limit is 2.0 in the pool policy
            with pytest.raises(StrategyArtifactValidationError, match="risk limit|forbidden|schema|classified"):
                validate_evolution_child_artifact(parent, raised)
        with pytest.raises(StrategyArtifactValidationError):
            validate_evolution_child_artifact(parent, child, parameter_changes={limit: 3.0})
