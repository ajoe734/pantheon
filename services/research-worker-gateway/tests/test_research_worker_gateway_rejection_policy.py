from __future__ import annotations

from fastapi.testclient import TestClient

from test_research_worker_gateway_http_service import _load_service_module


def test_production_and_live_paths_are_rejected_and_persisted() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    rejected = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "qlib",
            "requested_mode": "production",
            "dispatch_mode": "stub",
            "idempotency_key": "reject-production",
            "requested_at": "2026-04-28T23:00:00Z",
        },
    )
    assert rejected.status_code == 201
    payload = rejected.json()
    assert payload["status"] == "rejected"
    assert payload["rejection"]["reason"] == "production_adapter_disabled"
    assert payload["production_activation"] == "disabled"

    status = client.get(f"/api/research-worker-gateway/jobs/{payload['job_id']}/status")
    assert status.status_code == 200
    assert status.json()["rejection"]["reason"] == "production_adapter_disabled"

    for index, (worker, mode) in enumerate(
        (
            ("trl", "paper"),
            ("rl", "canary"),
            ("wandb", "live"),
        ),
        start=1,
    ):
        rejected_mode = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": worker,
                "requested_mode": mode,
                "dispatch_mode": "stub",
                "idempotency_key": f"reject-{worker}-{mode}",
                "requested_at": f"2026-04-28T23:0{index}:00Z",
            },
        )
        assert rejected_mode.status_code == 201
        assert rejected_mode.json()["status"] == "rejected"
        assert rejected_mode.json()["rejection"]["reason"] == "production_adapter_disabled"


def test_gateway_rejects_execution_registry_governance_learning_and_unknown_workers() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    cases = [
        (
            {
                "worker": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "parameters": {"target": "LEAN live_trading path"},
            },
            "execution_path_disabled",
        ),
        (
            {
                "worker": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "parameters": {"direct_registry_write": True},
            },
            "registry_write_disabled",
        ),
        (
            {
                "worker": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "parameters": {"governance_stage": "approved"},
            },
            "governance_write_disabled",
        ),
        (
            {
                "worker": "rllib",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "parameters": {"ep5_activation": True},
            },
            "learning_activation_disabled",
        ),
        (
            {
                "worker": "unknown-worker",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
            },
            "unknown_worker",
        ),
        (
            {
                "worker": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "real_backend",
            },
            "dispatch_mode_disabled",
        ),
    ]
    for index, (body, reason) in enumerate(cases, start=1):
        body["idempotency_key"] = f"reject-case-{index}"
        body["requested_at"] = f"2026-04-28T23:{index + 1:02d}:00Z"
        result = client.post("/api/research-worker-gateway/jobs", json=body)
        assert result.status_code == 201
        assert result.json()["status"] == "rejected"
        assert result.json()["rejection"]["reason"] == reason


def test_capabilities_list_activation_gated_backend_inventory() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    resp = client.get("/api/research-worker-gateway/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["safety_boundary"]["registry_writes"] == "disabled"
    workers = {entry["worker"]: entry for entry in body["capabilities"]}

    for worker in ("openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"):
        assert worker in workers
        assert workers[worker]["gate_state"] == "fail_closed"
        assert workers[worker]["allowed_scope"] == "capability_metadata_read_only"

        denied = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": worker,
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "idempotency_key": f"deny-{worker}",
                "requested_at": "2026-04-29T01:00:00Z",
            },
        )
        assert denied.status_code == 201
        assert denied.json()["status"] == "rejected"
        assert denied.json()["rejection"]["reason"] == "production_adapter_disabled"


def test_dormant_worker_dispatch_remains_fail_closed_even_if_legacy_env_is_enabled() -> None:
    module = _load_service_module(production_adapters_enabled="true")
    client = TestClient(module.app)

    capabilities = client.get("/api/research-worker-gateway/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["production_activation"] == "disabled"

    result = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "qlib",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "idempotency_key": "legacy-env-still-closed",
            "requested_at": "2026-04-29T01:30:00Z",
        },
    )
    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    assert payload["production_activation"] == "disabled"
    assert payload["rejection"]["reason"] == "production_adapter_disabled"


def test_active_job_bound_rejects_safe_dispatch_before_second_job_starts() -> None:
    module = _load_service_module(max_active_jobs="1")
    client = TestClient(module.app)

    first = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "requested_at": "2026-04-28T23:20:00Z",
        },
    )
    assert first.status_code == 201
    assert first.json()["status"] == "queued"

    bounded = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "requested_at": "2026-04-28T23:21:00Z",
        },
    )
    assert bounded.status_code == 429

    rejected_does_not_count_against_bound = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "finrl",
            "requested_mode": "production",
            "dispatch_mode": "stub",
            "requested_at": "2026-04-28T23:22:00Z",
        },
    )
    assert rejected_does_not_count_against_bound.status_code == 201
    assert rejected_does_not_count_against_bound.json()["status"] == "rejected"


def test_openclaw_dispatch_is_fail_closed_production_adapter_denied() -> None:
    """OpenClaw is registered as a deferred production worker; dispatch must be rejected."""
    module = _load_service_module()
    client = TestClient(module.app)

    result = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "openclaw",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "objective": "attempt openclaw agent session dispatch",
            "idempotency_key": "openclaw-fail-closed-check",
            "requested_at": "2026-04-29T00:00:00Z",
        },
    )
    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    assert payload["rejection"]["reason"] == "production_adapter_disabled"
    assert payload["production_activation"] == "disabled"


def test_openclaw_capabilities_surface_gate_state() -> None:
    """The research-worker-gateway capabilities endpoint must expose openclaw as fail_closed."""
    module = _load_service_module()
    client = TestClient(module.app)

    resp = client.get("/api/research-worker-gateway/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    workers = {entry["worker"]: entry for entry in body["capabilities"]}
    assert "openclaw" in workers, "openclaw must appear in capabilities surface"
    openclaw = workers["openclaw"]
    assert openclaw["status"] == "deferred"
    assert openclaw.get("gate_state") == "fail_closed"
    assert openclaw.get("allowed_scope") == "capability_metadata_read_only"
