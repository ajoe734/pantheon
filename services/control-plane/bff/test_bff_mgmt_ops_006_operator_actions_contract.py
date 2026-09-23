"""BFF contract tests for MGMT-OPS-006: governed operator actions and Human Review.

The operator-action precondition logic this suite exercises
(``_enforce_ops_console_preconditions``, the default validators dispatch
table, and the source-confidence gate) is the real extracted
``command_adapters.preconditions`` seam: ``build_default_validators`` builds
the same dispatch table ``CommandAdapterService`` uses in production, and
each validator takes its ``read_surface``/``ops_read_model_fn`` collaborators
as constructor-time parameters rather than reading process-global state. This
suite therefore builds a standalone ``CommandAdapterService`` (real
preconditions/idempotency/audit logic, no reimplementation) against a fresh,
test-owned ``ReadSurfacePorts`` and ``CommandStore`` per test, and mounts it
on a fresh ``FastAPI()`` app via the real ``create_command_adapters_router``
factory -- the same "no symbol imported from main.py, only the real
production modules" pattern ``tests/conftest.py``'s
``build_command_security_app`` uses. No handler or validator logic is
reimplemented here.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.control_plane.bff import command_executor
from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.preconditions import (
    build_default_validators,
)
from services.control_plane.bff.command_adapters.router import (
    create_command_adapters_router,
)
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import CommandType, RiskLevel, utc_now
from services.control_plane.bff.ports import ReadSurfacePorts

OPERATOR_TOKEN = "Bearer op-mgmt-ops-006:operator"
ADMIN_TOKEN = "Bearer op-mgmt-ops-006:admin"
REVIEWER_TOKEN = "Bearer op-mgmt-ops-006:reviewer"
APPROVER_TOKEN = "Bearer op-mgmt-ops-006:approver"
READONLY_TOKEN = "Bearer op-mgmt-ops-006:reader"


def _load_fallback_data() -> dict[str, Any]:
    fallback_path = os.path.join(os.path.dirname(__file__), "data", "read_surfaces.json")
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class MgmtOps006TestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        if seed_data is not None:
            self._data: dict[str, Any] = seed_data
        elif allow_fallback:
            self._data = _load_fallback_data()
        else:
            self._data = {}
        self.allow_fallback = allow_fallback

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "source": "local_snapshot", "snapshot_at": snapshot_at}

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, [])

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        persona_id = kwargs.get("persona_id") or kwargs.get("id") or "p-new"
        persona = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": kwargs.get("name") or persona_id,
            "lifecycle_state": kwargs.get("lifecycle_state") or "active",
            "metadata": kwargs.get("metadata") or {},
        }
        ds = self._data.setdefault("personas", {})
        if isinstance(ds, dict):
            ds[persona_id] = persona
        elif isinstance(ds, list):
            ds.append(persona)
        return persona

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("personas", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("personas", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_runtime_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        if isinstance(ds, dict):
            if str(binding_id or "") in ds:
                return ds[str(binding_id or "")]
            for r in ds.values():
                if isinstance(r, dict) and (r.get("id") == binding_id or r.get("binding_id") == binding_id or r.get("bindingId") == binding_id):
                    return r
            return None
        return next((r for r in ds if isinstance(r, dict) and (r.get("id") == binding_id or r.get("binding_id") == binding_id or r.get("bindingId") == binding_id)), None)

    def get_runtime_binding_by_runtime_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        if isinstance(ds, dict):
            for r in ds.values():
                if isinstance(r, dict) and (r.get("runtime_id") == runtime_id or r.get("runtimeId") == runtime_id):
                    return r
            val = ds.get(str(runtime_id or ""))
            if isinstance(val, dict) and (val.get("runtime_id") == runtime_id or val.get("runtimeId") == runtime_id):
                return val
            return None
        return next((r for r in ds if isinstance(r, dict) and (r.get("runtime_id") == runtime_id or r.get("runtimeId") == runtime_id)), None)


def _extract_identity_from_bearer(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
    **_kwargs: Any,
) -> Any:
    """Adapt the real stub extractor to the ``(auth, mfa_token=...)`` shape
    ``create_command_adapters_router`` expects (same adapter shape as
    ``tests/conftest.py``'s ``extract_identity_from_bearer_stub``)."""
    return extract_identity_stub(authorization)


def _make_process_command_task(commands: CommandStore) -> Any:
    """Real synchronous command executor glue for the injected store.

    Fetches the submitted record and dispatches it through the real
    ``command_executor.execute_command_with_status`` (the same executor
    ``main.py``'s background worker calls), then persists the resulting
    status/result/error. No execution/dispatch business logic is
    reimplemented here -- only the thin fetch/dispatch/persist wiring that
    ``tests/test_bff_emergency_containment.py`` uses for the same purpose.
    """

    def _process_command_task(command_id: str) -> None:
        record = commands.get_command(command_id)
        if not record:
            return
        command_type = CommandType(record["type"])
        params = dict(record.get("params") or {})
        status, result, error = command_executor.execute_command_with_status(
            command_id, command_type, params
        )
        commands.update_status(command_id, status, result=result, error=error)

    return _process_command_task


def _mounted_app(
    store: MgmtOps006TestReadPorts,
    commands: CommandStore,
    *,
    ops_read_model_fn: Optional[Any] = None,
) -> FastAPI:
    """Mount a standalone ``CommandAdapterService`` built from the real
    extracted ``build_default_validators`` dispatch table (the same table
    production wires in) against a fresh, test-owned read surface and
    command store, rather than the ``bff_main.app`` monolith.
    """
    service = CommandAdapterService(
        command_store=commands,
        read_surface=store,
        extract_identity=_extract_identity_from_bearer,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        validators=build_default_validators(
            read_surface=lambda: store,
            ops_read_model_fn=ops_read_model_fn,
            bff_error_fn=bff_error,
            utc_now_fn=utc_now,
        ),
        process_command_task=_make_process_command_task(commands),
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_command_adapters_router(service=service))
    app.state.command_store = commands
    return app


@contextmanager
def _client_with_store(
    store: MgmtOps006TestReadPorts,
    *,
    ops_read_model_fn: Optional[Any] = None,
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory(prefix="paper-action-contract-") as command_dir, patch.dict(
        os.environ, {"PANTHEON_BFF_TENANT_ID": "tenant-default"}
    ):
        # A fresh, isolated CommandStore backed by a private tmp file is used
        # per test (CommandStore is a durable file-backed store with no
        # in-memory `_cache`/mutable `file_path` to swap out).
        commands = CommandStore(os.path.join(command_dir, "commands.jsonl"))
        app = _mounted_app(store, commands, ops_read_model_fn=ops_read_model_fn)
        yield TestClient(app, raise_server_exceptions=False)


def _fresh_store() -> MgmtOps006TestReadPorts:
    return MgmtOps006TestReadPorts(allow_fallback=True)


def test_operator_roles_check() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-role",
        name="Test Role Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    with _client_with_store(store) as client:
        # PausePaperRuntime without operator/admin role should be forbidden
        response = client.post(
            "/bff/v1/commands",
            headers={"Authorization": READONLY_TOKEN, "Idempotency-Key": "test-role-1"},
            json={
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "runtime-test-role"},
                "params": {"runtime_id": "runtime-test-role"},
                "audit_context": {"reason": "Test role block"},
            }
        )
        assert response.status_code == 403
        assert "role_check" in response.text or "Forbidden" in response.text


def test_rejected_preconditions_unverifiable_source_confidence() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-unverifiable",
        name="Unverifiable Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    from operations_read_model import OperationsReadModelEntry, OperationsIdentity, DataConfidence as OpsDataConfidence, OperationsPerformance

    def mock_ops_model(persona_id, period="latest"):
        return OperationsReadModelEntry(
            identity=OperationsIdentity(persona_id=persona_id, period=period, as_of="2026-07-09T00:00:00Z"),
            data_confidence=OpsDataConfidence.UNAVAILABLE,
            performance=OperationsPerformance(),
            sources=[],
            diagnostics=[]
        )

    with _client_with_store(store, ops_read_model_fn=mock_ops_model) as client:
        response = client.post(
            "/bff/v1/commands",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "test-unverifiable-1"},
            json={
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "runtime-test"},
                "params": {"persona_id": "persona-test-unverifiable", "runtime_id": "runtime-test"},
                "audit_context": {"reason": "Test confidence block"},
            }
        )
        assert response.status_code == 422
        assert "source_confidence" in response.text or "unavailable" in response.text or "unverifiable" in response.text


def test_emergency_containment_limit() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-containment",
        name="Containment Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    with _client_with_store(store) as client:
        # EmergencyContainment trying to increase allocation or promote should fail
        response = client.post(
            "/bff/v1/commands",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "test-containment-1"},
            json={
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "persona-test-containment"},
                "params": {
                    "persona_id": "persona-test-containment",
                    "allocation_increase": True
                },
                "audit_context": {"reason": "Containment promotion test"},
            }
        )
        assert response.status_code == 422
        assert "Emergency containment cannot promote or increase allocation" in response.text


def test_command_idempotency() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-idempotency",
        name="Idempotency Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    runtimes = [
        {
            "binding_id": "rb-test",
            "runtime_id": "runtime-test",
            "status": "active",
            "deployment_mode": "paper",
            "metadata": {"tenant_id": "tenant-default"},
        }
    ]
    store.list_runtime_bindings = lambda **_: runtimes
    store.get_runtime_binding_by_runtime_id = lambda runtime_id: runtimes[0] if runtime_id == "runtime-test" else None

    from operations_read_model import OperationsReadModelEntry, OperationsIdentity, DataConfidence as OpsDataConfidence, OperationsPerformance

    def mock_ops_model(persona_id, period="latest"):
        return OperationsReadModelEntry(
            identity=OperationsIdentity(persona_id=persona_id, period=period, as_of="2026-07-09T00:00:00Z"),
            data_confidence=OpsDataConfidence.FORMAL,
            performance=OperationsPerformance(),
            sources=[],
            diagnostics=[]
        )

    with _client_with_store(store, ops_read_model_fn=mock_ops_model) as client:
        body = {
            "command": "Observe",
            "target": {"type": "Persona", "id": "persona-test-idempotency"},
            "params": {"persona_id": "persona-test-idempotency"},
            "audit_context": {"reason": "Idempotency testing"},
        }
        headers = {
            "Authorization": OPERATOR_TOKEN,
            "Idempotency-Key": "key-idempotency-ops-006",
            "X-Correlation-Id": "corr-idempotency-ops-006",
        }

        first = client.post("/bff/v1/commands", headers=headers, json=body)
        assert first.status_code == 202, first.text
        assert first.json()["data"]["command_id"]

        second = client.post("/bff/v1/commands", headers=headers, json=body)
        assert second.status_code == 202
        assert second.json()["data"]["command_id"] == first.json()["data"]["command_id"]


def test_pause_paper_runtime_distinct_binding_success() -> None:
    store = _fresh_store()
    binding = {
        "binding_id": "bind-paper-001",
        "runtime_id": "rt-paper-001",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    store._data["runtime_bindings"] = {"bind-paper-001": binding}

    token_id = "token-pause-distinct-001"
    with _client_with_store(store) as client:
        # 1. Create confirm token bound to canonical action and Runtime target
        ct_resp = client.post(
            "/bff/confirm-tokens",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": f"key-ct-{token_id}"},
            json={
                "tokenId": token_id,
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "rt-paper-001"},
                "issuedForOperatorId": "op-mgmt-ops-006",
            },
        )
        assert ct_resp.status_code == 201, ct_resp.text

        # 2. Submit and verify a separate authoritative owner GET, not the POST echo.
        # runtime_adapter's own binding lookup falls back to bff_main.read_store
        # when no store is injected; patch that resolver directly to the same
        # test-owned store instead of importing/monkeypatching main.py.
        with patch("services.control_plane.bff.command_adapters.runtime_adapter.http_request_json") as mock_http, patch(
            "services.control_plane.bff.command_adapters.runtime_adapter._get_runtime_manager_client"
        ) as mock_rm, patch(
            "services.control_plane.bff.command_adapters.runtime_adapter._get_read_store",
            return_value=store,
        ), patch.dict(os.environ, {"PANTHEON_INTERNAL_API_URL": "http://internal.unit.invalid"}):
            mock_rm.return_value.get.return_value = {**binding, "status": "paused"}
            mock_http.return_value = {
                "status": "executed",
                "status_after": "paused",
                "binding_id": "bind-paper-001",
                "runtime_id": "rt-paper-001",
                "degraded_mode": False,
            }
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "Idempotency-Key": "key-cmd-pause-distinct",
                    "X-Confirm-Token": token_id,
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-paper-001"},
                    "params": {
                        "runtime_id": "rt-paper-001",
                        "bounded_duration_minutes": 15,
                    },
                    "audit_context": {"reason": "Pause paper runtime test"},
                },
            )
            assert resp.status_code == 202, resp.text
            data = resp.json()["data"]
            assert data["command"] == "PausePaperRuntime"
            receipt = client.get(f"/api/v1/operator/commands/{data['command_id']}", headers={"Authorization": OPERATOR_TOKEN}).json()
            assert receipt["target"]["id"] == "rt-paper-001"
            assert receipt["target"]["type"] == "Runtime"
            assert receipt["status"] == "executed", receipt
            assert receipt["result"]["authoritative_readback"]["runtime_binding_id"] == "bind-paper-001"
            assert receipt["result"]["authoritative_readback"]["status"] == "paused"
            mock_rm.return_value.get.assert_called_once_with("bind-paper-001")

            stored = client.app.state.command_store.get_command(data["command_id"])
            assert stored is not None
            assert stored["params"]["runtime_id"] == "rt-paper-001"
            assert stored["params"]["runtime_binding_id"] == "bind-paper-001"
            assert stored["params"]["tenant_id"] == "tenant-default"
            assert "verified_binding" not in stored["params"]
            assert stored["params"]["duration_seconds"] == 900
            assert stored["params"]["bounded_duration_minutes"] == 15


def test_pause_paper_runtime_missing_runtime_404() -> None:
    store = _fresh_store()
    store._data["runtime_bindings"] = {}

    with _client_with_store(store) as client:
        with patch("services.control_plane.bff.command_adapters.runtime_adapter.http_request_json") as mock_http:
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "Idempotency-Key": "key-cmd-pause-404",
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-missing"},
                    "params": {"runtime_id": "rt-missing"},
                    "audit_context": {"reason": "Missing runtime test"},
                },
            )
            assert resp.status_code == 404
            assert "Runtime rt-missing does not exist" in resp.text
            assert not mock_http.called


def test_pause_paper_runtime_non_paper_stage_422() -> None:
    store = _fresh_store()
    binding = {
        "id": "bind-live-001",
        "binding_id": "bind-live-001",
        "runtime_id": "rt-nonpaper-001",
        "status": "active",
        "deployment_mode": "live",
        "metadata": {"tenant_id": "tenant-default"},
    }
    store._data["runtime_bindings"] = {"bind-live-001": binding}

    with _client_with_store(store) as client:
        with patch("services.control_plane.bff.command_adapters.runtime_adapter.http_request_json") as mock_http:
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "Idempotency-Key": "key-cmd-pause-nonpaper",
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-nonpaper-001"},
                    "params": {"runtime_id": "rt-nonpaper-001"},
                    "audit_context": {"reason": "Non-paper runtime test"},
                },
            )
            assert resp.status_code == 422
            assert "stage_mismatch" in resp.text
            assert not mock_http.called


def test_pause_paper_runtime_cross_tenant_403() -> None:
    store = _fresh_store()
    binding = {
        "id": "bind-paper-tenant",
        "binding_id": "bind-paper-tenant",
        "runtime_id": "rt-paper-tenant",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-alpha"},
    }
    store._data["runtime_bindings"] = {"bind-paper-tenant": binding}

    tenant_beta_token = OPERATOR_TOKEN  # caller fixture is tenant-default, not tenant-alpha

    with _client_with_store(store) as client:
        with patch("services.control_plane.bff.command_adapters.runtime_adapter.http_request_json") as mock_http:
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": tenant_beta_token,
                    "Idempotency-Key": "key-cmd-pause-tenant",
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-paper-tenant"},
                    "params": {"runtime_id": "rt-paper-tenant"},
                    "audit_context": {"reason": "Cross-tenant test"},
                },
            )
            assert resp.status_code == 403
            assert "cross_tenant" in resp.text
            assert not mock_http.called


def test_pause_paper_runtime_wrong_payload_binding_id_422() -> None:
    store = _fresh_store()
    binding = {
        "id": "bind-paper-real",
        "binding_id": "bind-paper-real",
        "runtime_id": "rt-paper-001",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    store._data["runtime_bindings"] = {"bind-paper-real": binding}

    with _client_with_store(store) as client:
        with patch("services.control_plane.bff.command_adapters.runtime_adapter.http_request_json") as mock_http:
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "Idempotency-Key": "key-cmd-pause-bad-binding",
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-paper-001"},
                    "params": {
                        "runtime_id": "rt-paper-001",
                        "binding_id": "bind-paper-MALICIOUS",
                    },
                    "audit_context": {"reason": "Wrong binding ID test"},
                },
            )
            assert resp.status_code == 422
            assert "binding_mismatch" in resp.text
            assert not mock_http.called


def test_pause_paper_runtime_invalid_duration_422() -> None:
    store = _fresh_store()
    binding = {
        "id": "bind-paper-001",
        "binding_id": "bind-paper-001",
        "runtime_id": "rt-paper-001",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    store._data["runtime_bindings"] = {"bind-paper-001": binding}

    with _client_with_store(store) as client:
        for bad_duration in [-10, 0, "not_a_number"]:
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "Idempotency-Key": f"key-cmd-pause-dur-{bad_duration}",
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-paper-001"},
                    "params": {
                        "runtime_id": "rt-paper-001",
                        "bounded_duration_minutes": bad_duration,
                    },
                    "audit_context": {"reason": "Invalid duration test"},
                },
            )
            assert resp.status_code == 422
            assert "bounded_duration_minutes" in resp.text


def test_pause_paper_runtime_confirm_token_binding_immutability() -> None:
    store = _fresh_store()
    binding1 = {
        "id": "bind-paper-001",
        "binding_id": "bind-paper-001",
        "runtime_id": "rt-paper-001",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    binding2 = {
        "id": "bind-paper-002",
        "binding_id": "bind-paper-002",
        "runtime_id": "rt-paper-002",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    store._data["runtime_bindings"] = {
        "bind-paper-001": binding1,
        "bind-paper-002": binding2,
    }

    token_id = "token-pause-immutability"
    with _client_with_store(store) as client:
        # Create token bound to rt-paper-001
        ct_resp = client.post(
            "/bff/confirm-tokens",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": f"key-ct-{token_id}"},
            json={
                "tokenId": token_id,
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "rt-paper-001"},
                "issuedForOperatorId": "op-mgmt-ops-006",
            },
        )
        assert ct_resp.status_code == 201

        # Attempt to use the token for rt-paper-002 -> must reject
        resp = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": "key-cmd-pause-hijack",
                "X-Confirm-Token": token_id,
            },
            json={
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "rt-paper-002"},
                "params": {"runtime_id": "rt-paper-002"},
                "audit_context": {"reason": "Confirm token hijack attempt"},
            },
        )
        assert resp.status_code == 428
        assert "CONFIRM_TOKEN_BINDING_MISMATCH" in resp.text


def test_pause_paper_runtime_target_redirection_token_a_params_b_rejects_and_no_downstream_post() -> None:
    store = _fresh_store()
    binding_a = {
        "id": "bind-paper-001",
        "binding_id": "bind-paper-001",
        "runtime_id": "rt-paper-001",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    binding_b = {
        "id": "bind-paper-002",
        "binding_id": "bind-paper-002",
        "runtime_id": "rt-paper-002",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": "tenant-default"},
    }
    store._data["runtime_bindings"] = {
        "bind-paper-001": binding_a,
        "bind-paper-002": binding_b,
    }

    token_id = "token-pause-redirect-001"
    with _client_with_store(store) as client:
        # Issue confirm token for target rt-paper-001
        ct_resp = client.post(
            "/bff/confirm-tokens",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "key-ct-pause-redirect"},
            json={
                "tokenId": token_id,
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "rt-paper-001"},
                "issuedForOperatorId": "op-mgmt-ops-006",
            },
        )
        assert ct_resp.status_code == 201

        with patch("services.control_plane.bff.command_adapters.runtime_adapter.http_request_json") as mock_http:
            # Target is rt-paper-001 with Token A, but params.runtime_id redirects to rt-paper-002
            resp = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "Idempotency-Key": "key-cmd-pause-redirect-test",
                    "X-Confirm-Token": token_id,
                },
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "rt-paper-001"},
                    "params": {"runtime_id": "rt-paper-002"},
                    "audit_context": {"reason": "Target redirection attempt with token A and params B"},
                },
            )
            assert resp.status_code == 422
            assert "target_redirection_detected" in resp.text
            assert not mock_http.called


def test_generic_enforce_ops_console_preconditions_no_runtime_from_persona_entity_id_regression() -> None:
    store = _fresh_store()
    store.create_persona(persona_id="persona-no-runtime", lifecycle_state="active", metadata={"tenant_id": "tenant-default"})
    # Read store has no runtime bindings for this persona
    from operations_read_model import (
        OperationsReadModelEntry,
        OperationsIdentity,
        DataConfidence as OpsDataConfidence,
        OperationsPerformance,
    )

    def mock_ops_model(persona_id, period="latest"):
        return OperationsReadModelEntry(
            identity=OperationsIdentity(persona_id=persona_id, period=period, as_of="2026-07-09T00:00:00Z"),
            data_confidence=OpsDataConfidence.FORMAL,
            performance=OperationsPerformance(),
            sources=[],
            diagnostics=[],
        )

    with _client_with_store(store, ops_read_model_fn=mock_ops_model) as client:
        # Test Observe with entity_id in params matching persona id
        resp_obs = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": "key-cmd-observe-regression",
            },
            json={
                "command": "Observe",
                "target": {"type": "Persona", "id": "persona-no-runtime"},
                "params": {
                    "persona_id": "persona-no-runtime",
                    "entity_id": "persona-no-runtime",
                },
                "audit_context": {"reason": "Regression test for Observe entity_id"},
            },
        )
        assert resp_obs.status_code == 202, resp_obs.text
        assert resp_obs.json()["data"]["command_id"]

        # Test RequestReview with entity_id in params matching persona id
        resp_rev = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": "key-cmd-request-review-regression",
            },
            json={
                "command": "RequestReview",
                "target": {"type": "Persona", "id": "persona-no-runtime"},
                "params": {
                    "persona_id": "persona-no-runtime",
                    "entity_id": "persona-no-runtime",
                },
                "audit_context": {"reason": "Regression test for RequestReview entity_id"},
            },
        )
        assert resp_rev.status_code == 202, resp_rev.text
        assert resp_rev.json()["data"]["command_id"]
