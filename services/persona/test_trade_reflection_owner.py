from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.persona.write_owner import (
    CreatePersonaRequest,
    PersistentPersonaOwner,
    create_app,
)
from services.runtime_auth_inbound import encode_jwt_hs256

JWT_SECRET = "persona-reflection-test-secret"


def _auth_header(tenant_id: str = "tenant-1", roles: list[str] | None = None) -> dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": "test-operator",
            "roles": roles or ["operator", "admin", "persona.admin"],
            "tenant_id": tenant_id,
            "exp": 4102444800,
        },
        secret=JWT_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


class MockReflectionProvider:
    name = "mock-openclaw"
    model = "mock-model-v1"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def reflect(self, *, facts: dict[str, Any], trigger: str) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return {
            "expected_vs_actual": {"thesis": "supported", "entry_quality": "good"},
            "attribution": "process",
            "counterfactuals": [{"alternative_action": "wait", "estimated_impact": "higher", "assumptions": "same"}],
            "lesson_candidates": [{"scope": "strategy", "proposed_change": "fine tune", "confidence": 0.8}],
            "mistakes": [],
            "what_worked": ["timing"],
            "unknowns": [],
            "followups": [],
        }


def _setup_app(
    td: str,
    monkeypatch: pytest.MonkeyPatch,
    provider: Any = None,
    telemetry_data: dict[str, Any] | None = None,
) -> tuple[TestClient, PersistentPersonaOwner, Path]:
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "persona-svc-token")

    store_path = Path(td) / "personas.json"
    owner = PersistentPersonaOwner.from_json_path(store_path)
    owner.create(
        CreatePersonaRequest(
            actor_id="test-operator",
            persona_id="p-alpha",
            name="Alpha Persona",
            mandate="Alpha Mandate",
            tenant_id="tenant-1",
        )
    )

    def fetcher(ep_id: str, tenant_id: str | None, auth: str | None) -> dict[str, Any] | None:
        if telemetry_data and ep_id in telemetry_data:
            return telemetry_data[ep_id]
        if ep_id == "ep-1":
            return {
                "trade_episode_id": "ep-1",
                "persona_id": "p-alpha",
                "tenant_id": "tenant-1",
                "status": "closed",
                "realized_pnl": 100.0,
            }
        return None

    app = create_app(
        owner=owner,
        reflection_provider=provider or MockReflectionProvider(),
        telemetry_fetcher=fetcher,
    )
    return TestClient(app), owner, store_path


def test_reflection_retry_happy_path_and_durable_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client, owner, store_path = _setup_app(td, monkeypatch)
        headers = {**_auth_header("tenant-1"), "Idempotency-Key": "idemp-1"}
        body = {"reason": "manual review of trade"}
        resp = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json=body)
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["action"] == "reflection.retry"
        assert data["persona_id"] == "p-alpha"
        assert data["resource_id"] == "ep-1"
        assert data["status"] == "accepted"
        assert "facts_snapshot_ref" in data
        assert "reflection_id" in data

        readback = client.get("/api/personas/p-alpha/trade-reflections", headers=_auth_header("tenant-1"))
        assert readback.status_code == 200
        reflections = readback.json()["data"]
        assert len(reflections) == 1
        assert reflections[0]["trade_episode_id"] == "ep-1"
        assert reflections[0]["attribution"] == "process"

        # Fresh process reads back from same store path
        fresh_owner = PersistentPersonaOwner.from_json_path(store_path)
        fresh_app = create_app(owner=fresh_owner)
        fresh_client = TestClient(fresh_app)
        fresh_readback = fresh_client.get("/api/personas/p-alpha/trade-reflections", headers=_auth_header("tenant-1"))
        assert fresh_readback.status_code == 200
        assert len(fresh_readback.json()["data"]) == 1


def test_reflection_retry_idempotency_and_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _, _ = _setup_app(td, monkeypatch)
        headers = {**_auth_header("tenant-1"), "Idempotency-Key": "idemp-same"}
        body = {"reason": "initial review"}
        first = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json=body)
        assert first.status_code == 202

        # Replay with same key and same payload returns same receipt
        replay = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json=body)
        assert replay.status_code == 202
        assert replay.json()["data"]["receipt_id"] == first.json()["data"]["receipt_id"]
        assert replay.json()["meta"]["idempotent_replay"] is True

        # Replay with same key but different reason causes 409 conflict
        conflict = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json={"reason": "changed reason"})
        assert conflict.status_code == 409


def test_facts_snapshot_ref_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _, _ = _setup_app(td, monkeypatch)
        headers = {**_auth_header("tenant-1"), "Idempotency-Key": "idemp-mismatch"}
        body = {"reason": "review", "facts_snapshot_ref": "facts://sha256/fake-hash"}
        resp = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json=body)
        assert resp.status_code == 409


def test_missing_episode_and_cross_persona_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        telemetry = {
            "ep-other": {
                "trade_episode_id": "ep-other",
                "persona_id": "p-other",
                "tenant_id": "tenant-1",
            }
        }
        client, _, _ = _setup_app(td, monkeypatch, telemetry_data=telemetry)
        headers = {**_auth_header("tenant-1"), "Idempotency-Key": "idemp-missing"}
        missing = client.post("/api/personas/p-alpha/trade-journal/ep-nonexistent/reflection:retry", headers=headers, json={"reason": "r"})
        assert missing.status_code == 404

        other = client.post("/api/personas/p-alpha/trade-journal/ep-other/reflection:retry", headers=headers, json={"reason": "r"})
        assert other.status_code == 403


def test_cross_tenant_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _, _ = _setup_app(td, monkeypatch)
        # Foreign tenant caller token
        headers = {**_auth_header("tenant-foreign"), "Idempotency-Key": "idemp-foreign"}
        resp = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json={"reason": "r"})
        assert resp.status_code == 403


def test_provider_failure_zero_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        failing_provider = MockReflectionProvider(fail=True)
        client, owner, _ = _setup_app(td, monkeypatch, provider=failing_provider)
        headers = {**_auth_header("tenant-1"), "Idempotency-Key": "idemp-fail"}
        resp = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers=headers, json={"reason": "r"})
        assert resp.status_code == 503

        # Assert no reflection or idempotency entry persisted on failure
        persona = owner.get("p-alpha")
        assert not (persona.metadata or {}).get("trade_reflections")
        assert not (persona.metadata or {}).get("trade_reflection_idempotency")


def test_exact_nonempty_tenant_and_unauthenticated_denials(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        mock_prov = MockReflectionProvider()
        client, owner, _ = _setup_app(td, monkeypatch, provider=mock_prov)

        # 1. Valid operator JWT without tenant returns 403 on POST retry before provider effect
        no_tenant_tok = encode_jwt_hs256({"sub": "op", "roles": ["operator", "admin"], "exp": 4102444800}, secret=JWT_SECRET)
        resp_post = client.post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers={"Authorization": f"Bearer {no_tenant_tok}", "Idempotency-Key": "k-no-tenant"}, json={"reason": "test"})
        assert resp_post.status_code == 403
        assert mock_prov.calls == 0
        assert not (owner.get("p-alpha").metadata or {}).get("trade_reflections")

        # 2. Valid operator JWT without tenant returns 403 on GET readback
        resp_get = client.get("/api/personas/p-alpha/trade-reflections", headers={"Authorization": f"Bearer {no_tenant_tok}"})
        assert resp_get.status_code == 403

        # 3. Unauthenticated GET returns 401
        resp_unauth = client.get("/api/personas/p-alpha/trade-reflections")
        assert resp_unauth.status_code == 401


def test_concurrent_cas_no_lost_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    with tempfile.TemporaryDirectory() as td:
        telemetry = {
            "ep-1": {"trade_episode_id": "ep-1", "persona_id": "p-alpha", "tenant_id": "tenant-1", "status": "closed"},
            "ep-2": {"trade_episode_id": "ep-2", "persona_id": "p-alpha", "tenant_id": "tenant-1", "status": "closed"},
        }
        client, owner, _ = _setup_app(td, monkeypatch, telemetry_data=telemetry)

        # Interleaved concurrent requests for distinct episodes
        def _call(ep_and_key):
            ep, key = ep_and_key
            headers = {**_auth_header("tenant-1"), "Idempotency-Key": key}
            return client.post(f"/api/personas/p-alpha/trade-journal/{ep}/reflection:retry", headers=headers, json={"reason": f"retry {ep}"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_call, [("ep-1", "k-1"), ("ep-2", "k-2")]))

        assert all(r.status_code == 202 for r in results)

        # Both reflections must survive in metadata
        persona = owner.get("p-alpha")
        stored_refs = (persona.metadata or {}).get("trade_reflections", [])
        stored_episodes = {r["trade_episode_id"] for r in stored_refs}
        assert stored_episodes == {"ep-1", "ep-2"}

        # Both idempotency entries must survive
        stored_idemp = (persona.metadata or {}).get("trade_reflection_idempotency", {})
        assert "k-1" in stored_idemp
        assert "k-2" in stored_idemp
