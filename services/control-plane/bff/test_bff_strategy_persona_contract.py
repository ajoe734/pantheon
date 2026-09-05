"""
Contract tests for BFF-LUV-GAP-002: strategy and persona BFF
compatibility surfaces consumed by execute-plans.

Covers:
  * list / detail / patch happy paths and overlay round-trip,
  * sub-resource routes (specs, experiments, artifacts, lineage, audit,
    route-policy, activity, evaluations, memory),
  * action endpoints — happy path and precondition errors,
  * dry-run and test-prompt stubs,
  * /bff/search and /bff/types compatibility surface,
  * 404 behavior for missing strategy / persona ids.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.models import CommandType
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.ports import ReadSurfacePorts
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


def _local_strategy_persona_read_data() -> dict[str, Any]:
    return {
        "strategies": {
            "strat-1": {
                "id": "strat-1",
                "strategy_id": "strat-1",
                "name": "Strategy 1",
                "state": "active",
                "risk": "medium",
                "personaIds": ["persona-1"],
                "capitalPoolId": "pool-1",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-01T00:00:00Z",
            }
        },
        "personas": {
            "persona-1": {
                "id": "persona-1",
                "persona_id": "persona-1",
                "name": "Persona 1",
                "state": "active",
                "status": "active",
                "archetype": "macro",
                "routedStrategies": ["strat-1"],
                "successRate": 0.8,
                "metadata": {
                    "owner": "op-1",
                    "archetype": "macro",
                    "risk_level": "medium",
                },
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-01T00:00:00Z",
            }
        },
        "runtime_bindings": {},
        "persona_league": [],
        "bindings": {},
        "capital_pools": {},
    }


class StrategyPersonaTestReadPorts(ReadSurfacePorts):
    def __init__(
        self,
        seed_data: dict[str, Any] | None = None,
        *,
        allow_local_snapshot_fallback: bool = True,
    ) -> None:
        super().__init__()
        self._data = seed_data if seed_data is not None else _local_strategy_persona_read_data()
        self.allow_local_snapshot_fallback = allow_local_snapshot_fallback
        self._ranking_snapshots: dict[str, Any] = {}

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "bff_local_dev_store"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "source": "bff_local_dev_store",
            "snapshot_at": snapshot_at,
            "freshness": "fresh",
            "observed_time": snapshot_at,
            "coverage": 1.0,
            "missing_bindings": False,
        }

    def _ensure_local_overlay_records(self, dataset: str) -> dict[str, Any]:
        return self._data.setdefault(dataset, {})

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("personas", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("personas", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def upsert_persona(self, persona: dict[str, Any]) -> dict[str, Any]:
        pid = persona.get("id") or persona.get("persona_id")
        self._data.setdefault("personas", {})[pid] = persona
        return persona

    def get_strategy(self, strategy_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("strategies", {})
        if isinstance(ds, dict):
            return ds.get(str(strategy_id or ""))
        return next((s for s in ds if s.get("id") == strategy_id or s.get("strategy_id") == strategy_id), None)

    def list_strategies(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("strategies", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def upsert_strategy(self, strategy: dict[str, Any]) -> dict[str, Any]:
        sid = strategy.get("id") or strategy.get("strategy_id")
        self._data.setdefault("strategies", {})[sid] = strategy
        return strategy

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_bindings") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_runtime_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or {}
        if isinstance(ds, dict):
            return ds.get(str(binding_id or ""))
        return next((r for r in ds if r.get("id") == binding_id or r.get("binding_id") == binding_id), None)

    def create_runtime_binding(self, **kwargs: Any) -> dict[str, Any]:
        binding_id = kwargs.get("binding_id") or kwargs.get("runtime_binding_id") or kwargs.get("id") or "rb-1"
        record = dict(kwargs)
        record["id"] = binding_id
        record["binding_id"] = binding_id
        self._data.setdefault("runtime_bindings", {})[binding_id] = record
        return record

    def create_persona(
        self,
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        created_at: str | None = None,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: str | None = None,
        strategy_family: str | None = None,
        traits: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        required_data_sources: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        clean_metadata = dict(metadata or {})
        clean_metadata.update({
            "owner": actor_id,
            "archetype": archetype,
            "risk_level": risk_level,
        })
        if traits:
            clean_metadata["traits"] = dict(traits)
        record = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": name,
            "created_by": actor_id,
            "created_at": created_at or bff_main.utc_now(),
            "updated_at": created_at or bff_main.utc_now(),
            "archetype": archetype,
            "lifecycle_state": lifecycle_state,
            "mandate": mandate or archetype,
            "strategy_family": strategy_family or archetype,
            "metadata": clean_metadata,
            "required_data_sources": required_data_sources or [],
        }
        self._data.setdefault("personas", {})[persona_id] = record
        return record

    def update_persona(
        self,
        persona_id: str,
        *,
        name: str | None = None,
        actor_id: str | None = None,
        updated_at: str | None = None,
        archetype: str | None = None,
        lifecycle_state: str | None = None,
        risk_level: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        if not persona_id:
            return None
        existing = self.get_persona(persona_id)
        if existing is None:
            return None
        record = dict(existing)
        record["id"] = persona_id
        record["persona_id"] = persona_id
        if name is not None:
            record["name"] = name
        if lifecycle_state is not None:
            record["lifecycle_state"] = lifecycle_state
            record["status"] = lifecycle_state
        if archetype is not None:
            record["mandate"] = archetype
            record["strategy_family"] = archetype
        record["updated_at"] = updated_at or bff_main.utc_now()

        clean_metadata = dict(record.get("metadata") if isinstance(record.get("metadata"), dict) else {})
        if metadata:
            clean_metadata.update(metadata)
        if actor_id is not None:
            clean_metadata["owner"] = actor_id
        if archetype is not None:
            clean_metadata["archetype"] = archetype
        if risk_level is not None:
            clean_metadata["risk_level"] = risk_level
        record["metadata"] = clean_metadata
        self._data.setdefault("personas", {})[persona_id] = record
        return record

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        return None

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return None

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("persona_league", [])
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("capital_pools", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_route_policy_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        return None

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        return []


_CURRENT_STORE_DATA: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
    from services.persona.runtime_profile import build_persona_runtime_profile
    monkeypatch.setattr(bff_main, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    transport = FakeOwnerTransport()
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(bff_main, "_PersonaOwnerHttpTransport", lambda: transport)
    monkeypatch.setattr(bff_main, "_register_persona_cron_required", _schedule_receipt)
    # Mock create_capital_binding
    monkeypatch.setattr(bff_main, "create_capital_binding", lambda payload: {"status": "created"})
    
    # Mock _post_json to do nothing and return empty dict
    monkeypatch.setattr(bff_main, "_post_json", lambda *args, **kwargs: {})
    
    # Mock _get_json to raise urllib.error.HTTPError for 404 (not found) by default
    import urllib.error
    from io import BytesIO
    fp = BytesIO(b"")
    mock_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, fp)
    monkeypatch.setattr(bff_main, "_get_json", lambda *args, **kwargs: (_ for _ in ()).throw(mock_404))
    
    # Mock _runtime_manager_client
    class MockRuntimeManagerClient:
        def deploy(self, request):
            binding_id = (
                request.get("persona_capital_binding_id")
                or request.get("binding_id")
                or request.get("runtime_binding_id")
                or "test-binding"
            )
            bff_main.read_store.create_runtime_binding(
                runtime_id=request.get("runtime_id", "test-runtime"),
                name=request.get("metadata", {}).get("name", "test"),
                persona_id=request.get("metadata", {}).get("persona_id", "test"),
                binding_id=binding_id,
                deployment_plan_id=request.get("plan_id", "test-plan"),
                runtime_kind="paper",
                actor_id="test",
                created_at=bff_main.utc_now(),
                params=request.get("metadata", {}),
                state=request.get("state") or "running",
            )
            return bff_main.read_store.get_runtime_binding(binding_id)
            
        def get(self, binding_id):
            return bff_main.read_store.get_runtime_binding(binding_id)
            
        def list_all(self):
            return list((bff_main.read_store._ensure_local_overlay_records("runtime_bindings") or {}).values())

        def list_by_plan(self, plan_id):
            return [
                binding
                for binding in self.list_all()
                if binding.get("plan_id") == plan_id
            ]
            
    mock_client = MockRuntimeManagerClient()
    monkeypatch.setattr(bff_main, "_runtime_manager_client", lambda: mock_client)


def _error(resp):
    body = resp.json()
    if isinstance(body.get("error"), dict):
        return body["error"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    raise AssertionError(f"response did not contain BFF error envelope: {body}")


def _fresh_client(td: str) -> TestClient:
    global _CURRENT_STORE_DATA
    _CURRENT_STORE_DATA = _local_strategy_persona_read_data()
    bff_main.read_store = StrategyPersonaTestReadPorts(
        seed_data=_CURRENT_STORE_DATA,
        allow_local_snapshot_fallback=True,
    )
    bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    bff_main._COMMAND_AUTH_CONTEXT.clear()
    return TestClient(bff_main.app)


# ---------------------------------------------------------------------------
# /bff/strategies
# ---------------------------------------------------------------------------


def test_bff_strategies_list_returns_200_and_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body and "page_info" in body
            for item in body["data"]:
                assert "id" in item
                assert "name" in item
                assert "state" in item
                assert "risk" in item
                assert "personaIds" in item
                assert "capitalPoolId" in item
        finally:
            bff_main.read_store = original


def test_bff_strategies_create_requires_idempotency_and_name() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            missing_key = client.post(
                "/bff/strategies", json={"name": "Alpha"}, headers=HEADERS,
            )
            assert missing_key.status_code == 400, missing_key.text
            assert _error(missing_key)["code"] == "VALIDATION_FAILED"

            missing_name = client.post(
                "/bff/strategies",
                json={},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-002"},
            )
            assert missing_name.status_code == 422, missing_name.text
            assert _error(missing_name)["code"] == "VALIDATION_FAILED"
        finally:
            bff_main.read_store = original


def test_bff_strategies_create_then_get_then_patch_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Mean Reversion", "alpha": "alpha-meanrev"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-001"},
            )
            assert create.status_code == 201, create.text
            strategy_id = create.json()["data"]["id"]

            get_resp = client.get(f"/bff/strategies/{strategy_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["name"] == "Mean Reversion"

            patch_resp = client.patch(
                f"/bff/strategies/{strategy_id}",
                json={"name": "Mean Reversion v2", "state": "review"},
                headers={**HEADERS, "Idempotency-Key": "patch-strategy-001"},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            assert patch_resp.json()["data"]["name"] == "Mean Reversion v2"
            assert patch_resp.json()["data"]["state"] == "review"
        finally:
            bff_main.read_store = original


def test_bff_strategies_subresources_return_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Carry"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-003"},
            )
            strategy_id = create.json()["data"]["id"]

            for subpath in ("specs", "experiments", "artifacts", "lineage", "audit"):
                resp = client.get(f"/bff/strategies/{strategy_id}/{subpath}", headers=HEADERS)
                assert resp.status_code == 200, f"{subpath}: {resp.text}"
                body = resp.json()
                assert "data" in body or "items" in body
                assert "meta" in body

            specs_post = client.post(
                f"/bff/strategies/{strategy_id}/specs",
                json={"version": "1.0"},
                headers={**HEADERS, "Idempotency-Key": "spec-001"},
            )
            assert specs_post.status_code == 201, specs_post.text
            assert specs_post.json()["data"]["strategy_id"] == strategy_id
        finally:
            bff_main.read_store = original


def test_bff_strategies_actions_use_final_envelope_and_precondition() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Momentum"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-004"},
            )
            strategy_id = create.json()["data"]["id"]

            missing_key = client.post(
                f"/bff/actions/strategy/{strategy_id}/edit",
                json={},
                headers=HEADERS,
            )
            assert missing_key.status_code == 400, missing_key.text
            err = _error(missing_key)
            assert err["code"] == "VALIDATION_FAILED"
            assert err["details"]["precondition_failed"] == "idempotency_key"

            ok = client.post(
                f"/bff/actions/strategy/{strategy_id}/edit",
                json={"reason": "operator review"},
                headers={**HEADERS, "Idempotency-Key": f"strategy-action-{strategy_id}"},
            )
            assert ok.status_code == 202, ok.text
            assert get_catalog_entry(CommandType.STRATEGY_ACTION.value) is not None
            assert ok.json()["data"]["command"] == CommandType.STRATEGY_ACTION.value
            assert ok.json()["data"]["receipt"]["command"] == CommandType.STRATEGY_ACTION.value
        finally:
            bff_main.read_store = original


def test_bff_strategies_dry_run_returns_run_handle() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/strategies",
                json={"name": "Carry"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-005"},
            )
            strategy_id = create.json()["data"]["id"]

            dry = client.post(
                f"/bff/strategies/{strategy_id}/dry-run",
                json={"params": {"window": "30d"}},
                headers={**HEADERS, "Idempotency-Key": "dry-run-001"},
            )
            assert dry.status_code == 202, dry.text
            assert dry.json()["data"]["strategy_id"] == strategy_id
            assert "run_id" in dry.json()["data"]
        finally:
            bff_main.read_store = original


def test_bff_strategies_404_for_unknown_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/strategy-does-not-exist", headers=HEADERS)
            assert resp.status_code == 404, resp.text
            assert _error(resp)["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# /bff/personas
# ---------------------------------------------------------------------------


def test_bff_personas_list_returns_200_and_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body and "page_info" in body
            for item in body["data"]:
                assert "id" in item
                assert "name" in item
                assert "state" in item
                assert "archetype" in item
                assert "routedStrategies" in item
                assert "successRate" in item
        finally:
            bff_main.read_store = original


def test_bff_personas_create_requires_idempotency_and_name() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            missing_key = client.post(
                "/bff/personas", json={"name": "Quant"}, headers=HEADERS,
            )
            assert missing_key.status_code == 400, missing_key.text

            missing_name = client.post(
                "/bff/personas",
                json={},
                headers={**HEADERS, "Idempotency-Key": "create-persona-002"},
            )
            assert missing_name.status_code == 422, missing_name.text
        finally:
            bff_main.read_store = original


def test_bff_personas_create_then_subresources_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Macro Macro", "archetype": "macro"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-001"},
            )
            assert create.status_code == 201, create.text
            create_body = create.json()
            created = create_body["data"]
            persona_id = created["id"]
            assert created["state"] == "provisioning"
            assert created["capitalMode"] == "paper"
            assert created["deploymentStage"] == "paper"
            assert created["paperLedgerId"].startswith("paper-ledger-")
            assert "capitalPoolId" not in created
            assert "runtimeId" not in created
            assert "runtimeBindingId" not in created
            assert create_body["meta"]["create_flow"] == "durable_owner_coordinated_provisioning"
            assert create_body["meta"]["paper_ledger_id"] == created["paperLedgerId"]
            assert create_body["meta"]["live_capital_side_effects"] is False
            assert create_body["meta"]["human_review_required_for_live"] is True

            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main.read_store = StrategyPersonaTestReadPorts(
                seed_data=_CURRENT_STORE_DATA,
                allow_local_snapshot_fallback=False,
            )
            binding_id = "rb-macro-macro-authoritative"
            runtime_id = "runtime-macro-macro-authoritative"
            persona_capital_binding_id = create_body["meta"]["persona_capital_binding_id"]
            persisted = bff_main.read_store.get_persona(persona_id)
            assert persisted is not None
            capital_pool_id = persisted["metadata"]["internal_paper_capital_pool_id"]
            tenant_id = persisted["metadata"]["tenant_id"]
            bff_main.read_store.create_runtime_binding(
                runtime_id=runtime_id,
                name="Macro Macro paper runtime",
                persona_id=persona_id,
                binding_id=binding_id,
                deployment_plan_id=created["deploymentPlanId"],
                runtime_kind="paper",
                actor_id="test",
                created_at=bff_main.utc_now(),
                params={
                    "persona_capital_binding_id": persona_capital_binding_id,
                    "capital_pool_id": capital_pool_id,
                },
                state="running",
            )
            authoritative_binding = {
                "binding_id": binding_id,
                "runtime_id": runtime_id,
                "plan_id": created["deploymentPlanId"],
                "persona_capital_binding_id": persona_capital_binding_id,
                "capital_pool_id": capital_pool_id,
                "deployment_mode": "paper",
                "state": "running",
                "status": "running",
                "metadata": {
                    "persona_id": persona_id,
                    "tenant_id": tenant_id,
                },
            }

            class ExactRuntimeManagerClient:
                def get(self, requested_binding_id):
                    return (
                        authoritative_binding
                        if requested_binding_id == binding_id
                        else None
                    )

                def list_all(self):
                    return [authoritative_binding]

                def list_by_plan(self, requested_plan_id):
                    return (
                        [authoritative_binding]
                        if requested_plan_id == created["deploymentPlanId"]
                        else []
                    )

            bff_main._runtime_manager_client = lambda: ExactRuntimeManagerClient()
            worker_session = {
                "session_id": "session-1",
                "runtime_id": runtime_id,
                "binding_id": binding_id,
                "capital_pool_id": capital_pool_id,
                "status": "running",
                "active": True,
                "last_heartbeat_at": bff_main.utc_now(),
            }
            bff_main.read_store.list_authoritative_paper_runtime_monitoring_sessions = (
                lambda: [worker_session]
            )
            projection = {
                "plan_id": created["deploymentPlanId"],
                "deployment_saga_id": create_body["meta"]["deployment_saga_id"],
                "deployment_saga_status": "completed",
                "deployment_saga_progress": {"progress_status": "completed"},
                "runtime_binding_id": binding_id,
                "runtime_id": runtime_id,
                "runtime_binding": authoritative_binding,
            }
            bff_main._get_json = lambda *_args, **_kwargs: projection
            bff_main._register_persona_cron_required = lambda *_args, **_kwargs: {
                "authoritative_readback": {
                    "persona_id": persona_id,
                    "workflow_id": "pantheon.persona.first-evaluation",
                    "registered": True,
                    "runtime_id": runtime_id,
                    "runtime_binding_id": binding_id,
                    "capital_pool_id": capital_pool_id,
                    "persona_capital_binding_id": persona_capital_binding_id,
                    "job_id": f"job-{persona_id}",
                    "job_name": f"pantheon-first-evaluation-{persona_id}",
                    "request_id": (
                        f"persona-provisioning:{persona_id}:"
                        "pantheon.persona.first-evaluation"
                    ),
                    "schedule": {"kind": "cron", "expr": "*/15 * * * *"},
                    "session_target": persona_id,
                    "observed_at": bff_main.utc_now(),
                }
            }

            detail = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["id"] == persona_id
            assert detail.json()["data"]["state"] == "provisioning"
            reconciled = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile",
                headers=HEADERS,
            )
            assert reconciled.status_code == 200, reconciled.text
            detail = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert detail.json()["data"]["state"] == "paper_running"
            assert detail.json()["data"]["capitalMode"] == "paper"
            assert detail.json()["data"]["runtimeId"] == runtime_id

            fleet = client.get(f"/bff/management/persona-fleet?persona={persona_id}", headers=HEADERS)
            assert fleet.status_code == 200, fleet.text
            rows = {item["persona_id"]: item for item in fleet.json()["data"]["items"]}
            row = rows[persona_id]
            assert row["state"] == "paper_running"
            assert row["capital_mode"] == "paper"
            assert row["paper_ledger_id"] == created["paperLedgerId"]
            assert row["paper_ledger"]["is_isolated"] is True
            assert row["capital_pool_id"] is None
            assert row["runtime_id"] == runtime_id
            assert row["runtime_binding_id"] == binding_id
            assert row["runtime_binding"]["state"] == "running"
            assert row["deployment_stage"] == "paper"

            for subpath in ("route-policy", "runtime-profile", "activity", "evaluations", "memory", "audit"):
                resp = client.get(f"/bff/personas/{persona_id}/{subpath}", headers=HEADERS)
                assert resp.status_code == 200, f"{subpath}: {resp.text}"
                body = resp.json()
                assert "data" in body or "items" in body
                assert "meta" in body

            stored = bff_main.read_store.get_persona(persona_id)
            reconcile = stored["metadata"]["openclaw_agent_reconcile"]
            assert reconcile["status"] == "pending"
            assert reconcile["reason"] == "persona_created"
            assert reconcile["agent_id"] == persona_id
            assert reconcile["model_id"] == f"openclaw/{persona_id}"
            assert reconcile["workspace_ref"].endswith(f"/{persona_id}")
            assert reconcile["model_routing"]["status"] == "ready"
            assert reconcile["consumer"] == "scripts/openclaw-sync-persona-agents.py"
        finally:
            bff_main.read_store = original


def test_bff_persona_runtime_profile_exposes_route_policy_model_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Runtime Persona", "archetype": "macro"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-runtime-profile"},
            )
            assert create.status_code == 201, create.text
            persona_id = create.json()["data"]["id"]

            def route_policy(pid: str):
                assert pid == persona_id
                return {
                    "personaId": pid,
                    "model_routing": {
                        "mode": "hard_pin",
                        "model": "openai/gpt-5.5",
                    },
                }

            bff_main.read_store.get_route_policy_for_persona = route_policy
            resp = client.get(f"/bff/personas/{persona_id}/runtime-profile", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["persona_id"] == persona_id
            assert data["workspace_ref"].endswith(f"/{persona_id}")
            assert data["model_routing"]["mode"] == "hard_pin"
            assert data["model_routing"]["status"] == "ready"
            assert data["model_routing"]["primary_model"] == "openai/gpt-5.5"
            assert data["model_routing"]["fallback_models"] == []
            assert data["memory_policy"]["source"] == "canonical_persona_memory_plane"
            assert data["memory_policy"]["cache_mutation_policy"] == "memory_bridge_only"
            assert data["memory_policy"]["direct_session_writes"] is False

            payload = json.dumps(data)
            for forbidden in ("api_key", "secret", "token", "credential", "oauth"):
                assert forbidden not in payload.lower()
        finally:
            bff_main.read_store = original


def test_bff_persona_runtime_profile_fails_closed_for_unknown_model_ref() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Invalid Runtime Persona", "archetype": "macro"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-invalid-runtime-profile"},
            )
            assert create.status_code == 201, create.text
            persona_id = create.json()["data"]["id"]

            bff_main.read_store.get_route_policy_for_persona = lambda pid: {
                "personaId": pid,
                "model_routing": {"mode": "preferred_pool_model", "model": "vendor/unknown"},
            }

            resp = client.get(f"/bff/personas/{persona_id}/runtime-profile", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            routing = resp.json()["data"]["model_routing"]
            assert routing["status"] == "degraded"
            assert routing["primary_model"] is None
            assert routing["fallback_models"] == []
            assert routing["blocked_reason"] == "unknown_model_ref"
            assert routing["invalid_refs"] == ["vendor/unknown"]
        finally:
            bff_main.read_store = original


def test_bff_personas_create_rejects_initial_live_or_canary_mode() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            for mode in ("live", "canary"):
                response = client.post(
                    "/bff/personas",
                    json={"name": f"{mode.title()} Persona", "initialMode": mode},
                    headers={**HEADERS, "Idempotency-Key": f"create-persona-{mode}"},
                )
                assert response.status_code == 422, response.text
                err = _error(response)
                assert err["code"] == "VALIDATION_FAILED"
                assert err["details"]["precondition_failed"] == "capital_mode"
        finally:
            bff_main.read_store = original


def test_bff_personas_patch_persists_without_snapshot_fallback() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Tactical Persona", "archetype": "macro"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-patch"},
            )
            assert create.status_code == 201, create.text
            persona_id = create.json()["data"]["id"]

            patch = client.patch(
                f"/bff/personas/{persona_id}",
                json={"name": "Persistent Persona", "risk": "high", "successRate": 0.42},
                headers={**HEADERS, "Idempotency-Key": "patch-persona-001"},
            )
            assert patch.status_code == 200, patch.text

            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main.read_store = StrategyPersonaTestReadPorts(
                seed_data=_CURRENT_STORE_DATA,
                allow_local_snapshot_fallback=False,
            )
            detail = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["name"] == "Persistent Persona"
            assert detail.json()["data"]["risk"] == "high"
            assert detail.json()["meta"]["surfaces"]["persona_detail"]["source"] == "bff_local_dev_store"

            stored = bff_main.read_store.get_persona(persona_id)
            reconcile = stored["metadata"]["openclaw_agent_reconcile"]
            assert reconcile["status"] == "pending"
            assert reconcile["reason"] == "persona_updated"
            assert reconcile["agent_id"] == persona_id
            assert reconcile["model_id"] == f"openclaw/{persona_id}"
            assert reconcile["workspace_ref"].endswith(f"/{persona_id}")
            assert reconcile["model_routing"]["status"] == "ready"
        finally:
            bff_main.read_store = original


def test_bff_personas_actions_route_through_command_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Risk Officer"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-003"},
            )
            persona_id = create.json()["data"]["id"]

            precondition = client.post(
                f"/bff/actions/persona/{persona_id}/retire",
                json={},
                headers=HEADERS,
            )
            assert precondition.status_code == 400, precondition.text
            assert _error(precondition)["code"] == "VALIDATION_FAILED"

            ok = client.post(
                f"/bff/actions/persona/{persona_id}/retire",
                json={"reason": "decommission"},
                headers={**HEADERS, "Idempotency-Key": f"persona-action-{persona_id}"},
            )
            assert ok.status_code == 202, ok.text
            assert get_catalog_entry(CommandType.PERSONA_ACTION.value) is not None
            assert ok.json()["data"]["command"] == CommandType.PERSONA_ACTION.value
            assert ok.json()["data"]["receipt"]["command"] == CommandType.PERSONA_ACTION.value
        finally:
            bff_main.read_store = original


def test_bff_personas_test_prompt_requires_prompt() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create = client.post(
                "/bff/personas",
                json={"name": "Trader"},
                headers={**HEADERS, "Idempotency-Key": "create-persona-004"},
            )
            persona_id = create.json()["data"]["id"]

            empty = client.post(
                f"/bff/personas/{persona_id}/test-prompt",
                json={},
                headers={**HEADERS, "Idempotency-Key": "test-prompt-001"},
            )
            assert empty.status_code == 422, empty.text
            assert _error(empty)["details"]["precondition_failed"] == "prompt"

            ok = client.post(
                f"/bff/personas/{persona_id}/test-prompt",
                json={"prompt": "What's the macro view?"},
                headers={**HEADERS, "Idempotency-Key": "test-prompt-002"},
            )
            assert ok.status_code == 202, ok.text
            assert ok.json()["data"]["persona_id"] == persona_id
            assert ok.json()["data"]["prompt"] == "What's the macro view?"
        finally:
            bff_main.read_store = original


def test_bff_personas_404_for_unknown_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/persona-does-not-exist", headers=HEADERS)
            assert resp.status_code == 404, resp.text
            assert _error(resp)["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Platform helpers — /bff/search and /bff/types
# ---------------------------------------------------------------------------


def test_bff_search_returns_results_for_overlay_strategy() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            client.post(
                "/bff/strategies",
                json={"name": "Liquidity Premium"},
                headers={**HEADERS, "Idempotency-Key": "create-strategy-search"},
            )
            resp = client.get("/bff/search?q=liquidity", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert any(r.get("type") == "strategy" for r in body["data"])
        finally:
            bff_main.read_store = original


def test_bff_types_compat_lists_canonical_entities() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/types", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"]["types_source"].endswith("types.ts")
            assert "Strategy" in body["data"]["exported_entities"]
            assert "Persona" in body["data"]["exported_entities"]
            assert "compatibility_decision" in body["data"]
        finally:
            bff_main.read_store = original
