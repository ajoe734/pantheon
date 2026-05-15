"""Gateway routing tests for policy-learning service.

Tests that:
- Closed gate (default): offline-capable adapters are still rejected.
- Open gate (PANTHEON_OFFLINE_GATE_ENABLED=true): offline adapters are routed to the
  research-worker-gateway, with gateway_ref stored in job state.
- Open gate: paper/canary/live modes remain fail-closed.
- Capabilities surface reflects gateway_routing state when gate is open.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_pl_module(
    data_dir: str | None = None,
    offline_gate: str = "false",
    gateway_url: str = "http://research-worker-gateway-svc:8103",
):
    with mock.patch.dict(
        "os.environ",
        {
            "POLICY_LEARNING_DATA_DIR": data_dir or tempfile.mkdtemp(),
            "POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS": "false",
            "PANTHEON_OFFLINE_GATE_ENABLED": offline_gate,
            "RESEARCH_WORKER_GATEWAY_URL": gateway_url,
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("pl_gateway_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["pl_gateway_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def _make_gateway_response(job_id: str = "wjob-20260430-001") -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "completed",
        "worker": "trl",
        "exit_code": 0,
        "stdout": "training done",
        "stderr": "",
    }


def test_closed_gate_offline_adapters_rejected() -> None:
    """Gate closed: trl/finrl/qlib/rllib/ray_tune are still rejected as production adapters."""
    module = _load_pl_module()
    client = TestClient(module.app)

    for adapter in ("trl", "finrl", "qlib", "rllib", "ray_tune"):
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": f"policy-{adapter}-closed",
                "objective": "Attempt offline adapter in closed gate.",
                "adapter": adapter,
                "requested_mode": "offline",
                "proposed_at": "2026-04-30T06:00:00Z",
            },
        )
        assert result.status_code == 201
        payload = result.json()
        assert payload["status"] == "rejected", f"Expected rejected for {adapter}, got {payload['status']}"
        assert payload["rejection"]["reason"] == "production_adapter_disabled"
        assert payload["production_activation"] == "disabled"


def test_open_gate_routes_offline_adapter_to_gateway() -> None:
    """Gate open: trl adapter proposal is routed to research-worker-gateway."""
    module = _load_pl_module(offline_gate="true")
    client = TestClient(module.app)

    fake_gateway_response = _make_gateway_response("wjob-20260430-001")
    with mock.patch.object(module, "_route_to_gateway", return_value=fake_gateway_response):
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": "policy-trl-offline",
                "objective": "Activate TRL DPO training offline.",
                "adapter": "trl",
                "requested_mode": "offline",
                "source_refs": [{"type": "feedback_batch", "id": "fb-001"}],
                "proposed_at": "2026-04-30T06:10:00Z",
            },
        )
    assert result.status_code == 201
    job = result.json()
    assert job["status"] == "dispatched"
    assert job["production_activation"] == "disabled"
    assert job["gateway_ref"]["gateway_job_id"] == "wjob-20260430-001"
    assert job["gateway_ref"]["gateway"] == "research-worker-gateway"
    assert any(e["event_type"] == "proposal_dispatched" for e in job["events"])

    status = client.get(f"/api/policy-learning/jobs/{job['job_id']}/status")
    assert status.status_code == 200
    assert status.json()["gateway_ref"]["gateway_job_id"] == "wjob-20260430-001"


def test_open_gate_gateway_unavailable_recorded() -> None:
    """Gate open but gateway unreachable: job is dispatched with gateway_unavailable ref."""
    module = _load_pl_module(offline_gate="true")
    client = TestClient(module.app)

    with mock.patch.object(module, "_route_to_gateway", return_value=None):
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": "policy-finrl-unavailable",
                "objective": "FinRL offline with gateway down.",
                "adapter": "finrl",
                "requested_mode": "offline",
                "proposed_at": "2026-04-30T06:11:00Z",
            },
        )
    assert result.status_code == 201
    job = result.json()
    assert job["status"] == "dispatched"
    assert job["gateway_ref"]["gateway_job_id"] is None
    assert job["gateway_ref"]["error"] == "gateway_unavailable"


def test_open_gate_requires_explicit_offline_requested_mode() -> None:
    """Gate open: offline adapters are routed only when requested_mode is exactly offline."""
    module = _load_pl_module(offline_gate="true")
    client = TestClient(module.app)

    with mock.patch.object(module, "_route_to_gateway") as route:
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": "policy-trl-stub",
                "objective": "Invalid mixed-mode policy learning dispatch.",
                "adapter": "trl",
                "requested_mode": "stub",
                "proposed_at": "2026-04-30T06:11:30Z",
            },
        )
    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    assert payload["rejection"]["reason"] == "offline_mode_required"
    assert "gateway_ref" not in payload
    route.assert_not_called()


def test_open_gate_production_modes_remain_fail_closed() -> None:
    """Gate open: paper/canary/live modes are always rejected for policy-learning adapters."""
    module = _load_pl_module(offline_gate="true")
    client = TestClient(module.app)

    for adapter, mode in (("trl", "production"), ("qlib", "paper"), ("finrl", "canary"), ("rllib", "live")):
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": f"policy-{adapter}-blocked",
                "objective": f"Blocked {mode} mode.",
                "adapter": adapter,
                "requested_mode": mode,
                "proposed_at": "2026-04-30T06:12:00Z",
            },
        )
        assert result.status_code == 201
        payload = result.json()
        assert payload["status"] == "rejected", f"Expected rejected for {adapter}/{mode}"
        assert payload["rejection"]["reason"] == "production_adapter_disabled"


def test_open_gate_non_offline_adapters_remain_fail_closed() -> None:
    """Gate open: openclaw and wandb (no gateway entrypoints) remain fail-closed."""
    module = _load_pl_module(offline_gate="true")
    client = TestClient(module.app)

    for adapter in ("openclaw", "wandb"):
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": f"policy-{adapter}-still-closed",
                "objective": f"Attempt {adapter} with open gate.",
                "adapter": adapter,
                "requested_mode": "offline",
                "proposed_at": "2026-04-30T06:13:00Z",
            },
        )
        assert result.status_code == 201
        payload = result.json()
        assert payload["status"] == "rejected"
        assert payload["rejection"]["reason"] == "production_adapter_disabled"


def test_capabilities_reflect_gateway_routing_when_gate_open() -> None:
    """Capabilities show gateway_routing=enabled for offline adapters when gate is open."""
    closed_module = _load_pl_module(offline_gate="false")
    open_module = _load_pl_module(offline_gate="true")

    closed_caps = TestClient(closed_module.app).get("/api/policy-learning/capabilities").json()
    open_caps = TestClient(open_module.app).get("/api/policy-learning/capabilities").json()

    assert closed_caps["offline_gate"] == "disabled"
    assert open_caps["offline_gate"] == "enabled"

    closed_map = {e["adapter"]: e for e in closed_caps["capabilities"]}
    open_map = {e["adapter"]: e for e in open_caps["capabilities"]}

    for adapter in ("trl", "finrl", "rllib", "ray_tune", "qlib"):
        assert closed_map[adapter]["gate_state"] == "fail_closed"
        assert open_map[adapter]["gate_state"] == "activation_ready"
        assert open_map[adapter]["gateway_routing"] == "enabled"

    # Non-offline adapters stay fail_closed.
    for adapter in ("openclaw", "wandb"):
        assert open_map[adapter]["gate_state"] == "fail_closed"


def test_open_gate_stub_adapter_unaffected() -> None:
    """Stub adapter continues to work normally when gate is open."""
    module = _load_pl_module(offline_gate="true")
    client = TestClient(module.app)

    result = client.post(
        "/api/policy-learning/jobs",
        json={
            "policy_id": "policy-stub-gate-open",
            "objective": "Stub proposal with open gate.",
            "adapter": "stub",
            "requested_mode": "stub",
            "proposed_at": "2026-04-30T06:14:00Z",
        },
    )
    assert result.status_code == 201
    assert result.json()["status"] == "proposed"
