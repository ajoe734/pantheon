"""Tenant authority audit: only the verified caller tenant may scope a read or write.

Tenant values from payloads, kwargs or stored records may be compared for equality but
never fill a missing trusted tenant.  Signed-JWT cases mount the real Agora router.
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff import command_executor
from services.control_plane.bff.agora.performance.store import PerformanceSuggestionStore
from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.command_adapters import strategy_adapter
from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.governance.decision_journal_write_owner import (
    build_decision_journal_write_owner,
)
from services.control_plane.bff.personas.service import (
    _bff_error,
    _extract_identity,
    _require_operator_role,
    _require_read_role,
)
from services.runtime_auth_inbound import encode_jwt_hs256

_SECRET, _ISSUER, _AUDIENCE = "tenant-audit-secret", "tenant-audit", "pantheon-bff"


def _jwt_headers(monkeypatch, tenant: str) -> dict[str, str]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    for name in ("PANTHEON_BFF_TENANT_ID", "PANTHEON_BFF_DEFAULT_TENANT_ID", "PANTHEON_TENANT_ID"):
        monkeypatch.delenv(name, raising=False)
    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "low-priv-operator", "roles": ["operator"], "tenant_id": tenant,
            "allowed_tenants": [tenant], "iss": _ISSUER, "aud": _AUDIENCE,
            "iat": now, "exp": now + 3600,
        },
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{time.time_ns()}"}


def _journal_client(owner) -> TestClient:
    router = create_agora_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_write_role=_require_operator_role,
        require_operator_role=_require_operator_role,
        require_journal_write_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-10-03T00:00:00Z",
        read_surface=object(),
        journal_write_owner=owner,
        sync_servant_agent=lambda p: {},
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _handler(request, exc):
        content = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=content)

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _stored_journal_tenants(owner) -> list:
    return [getattr(row, "tenant_id", None) or row.get("tenant_id") for row in owner.stores.entries.list_all()]


def test_journal_create_uses_jwt_tenant_and_denies_foreign_body_tenant(monkeypatch, tmp_path):
    owner = build_decision_journal_write_owner(data_dir=str(tmp_path))
    client = _journal_client(owner)
    headers = _jwt_headers(monkeypatch, "tenant-a")

    foreign = client.post("/bff/agora/journal", headers=headers,
                          json={"title": "x", "body": "y", "tenant_id": "tenant-b"})
    assert foreign.status_code == 403, foreign.text
    assert _stored_journal_tenants(owner) == []

    same = client.post("/bff/agora/journal", headers=headers,
                       json={"title": "x", "body": "y", "tenant_id": "tenant-a"})
    absent = client.post("/bff/agora/journal", headers={**headers, "Idempotency-Key": "idem-absent"},
                         json={"title": "z", "body": "y"})
    assert same.status_code == 201 and absent.status_code == 201, (same.text, absent.text)
    assert _stored_journal_tenants(owner) == ["tenant-a", "tenant-a"]


def test_journal_owner_rejects_missing_trusted_tenant_even_if_payload_names_one(tmp_path):
    owner = build_decision_journal_write_owner(data_dir=str(tmp_path))
    for tenant in (None, "", "  "):
        with pytest.raises(ValueError):
            owner.create_decision_journal_entry(
                title="t", body="b", actor_id="a", created_at="2026-10-03T00:00:00Z",
                payload={"tenant_id": "tenant-b", "tenantId": "tenant-b"}, tenant_id=tenant, user_id="u",
            )
    assert list(owner.stores.entries.list_all()) == []


class _Resp:
    status = 200

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _post_calls(monkeypatch, payload, **kw):
    calls: list[dict[str, str]] = []

    def _urlopen(req, timeout=None):
        calls.append({k.lower(): v for k, v in req.header_items()})
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    command_executor._post_json("http://owner.invalid/x", payload, auth_token="Bearer t", **kw)
    return calls


@pytest.mark.parametrize("trusted", [None, "", "   ", "tenant-a"])
def test_post_json_rejects_payload_tenant_that_is_not_the_trusted_tenant(monkeypatch, trusted):
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or _Resp())
    with pytest.raises(ActionUnavailableError) as exc:
        command_executor._post_json("http://owner.invalid/x", {"tenant_id": "tenant-b"}, tenant_id=trusted)
    assert exc.value.error_code == "TENANT_MISMATCH"
    assert calls == []


def test_post_json_positive_and_tenantless_controls(monkeypatch):
    (same,) = _post_calls(monkeypatch, {"tenant_id": "tenant-a"}, tenant_id="tenant-a")
    assert same["x-tenant-id"] == "tenant-a"
    (bound,) = _post_calls(monkeypatch, {}, tenant_id=" tenant-a ")
    assert bound["x-tenant-id"] == "tenant-a"
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or _Resp())
    for payload in ({}, {"k": 1}):
        with pytest.raises(ActionUnavailableError) as exc:
            command_executor._post_json("http://owner.invalid/x", payload, auth_token=None, tenant_id=None)
        assert exc.value.error_code == "TENANT_MISMATCH"
        with pytest.raises(ActionUnavailableError):
            adapter_base._headers(payload, None, None, None)
    assert calls == []


@pytest.mark.parametrize("trusted", [None, "", "tenant-a"])
def test_adapter_headers_reject_payload_tenant_that_is_not_the_trusted_tenant(trusted):
    with pytest.raises(ActionUnavailableError):
        adapter_base._headers({"tenant_id": "tenant-b"}, None, None, trusted)
    assert adapter_base._headers({"tenant_id": "tenant-a"}, None, None, "tenant-a")["X-Tenant-Id"] == "tenant-a"


def _suggestion_store(tmp_path) -> PerformanceSuggestionStore:
    store = PerformanceSuggestionStore(str(tmp_path / "s.sqlite"), incidents_api_url="")
    with sqlite3.connect(store.path) as conn:
        for tenant in ("tenant-a", "tenant-b"):
            conn.execute(
                "INSERT INTO performance_suggestions VALUES (?,?,?,?,?,?,?,?,?)",
                (tenant, "u", f"sg-{tenant}", "s1", "30d", "open", 1, json.dumps({"tenant": tenant}), "t"),
            )
    return store


def test_suggestion_reads_require_trusted_tenant(tmp_path):
    store = _suggestion_store(tmp_path)
    assert store.list_suggestions("tenant-a") == [{"tenant": "tenant-a"}]
    assert store.get_suggestion("tenant-a", suggestion_id="sg-tenant-a") == {"tenant": "tenant-a"}
    assert store.get_suggestion("tenant-a", suggestion_id="sg-tenant-b") is None
    for tenant in (None, ""):
        with pytest.raises(ValueError):
            store.list_suggestions(tenant)
        with pytest.raises(ValueError):
            store.get_suggestion(tenant, suggestion_id="sg-tenant-b")


def _validate_receipt(caller_tenant, entry_tenant):
    entry = {
        "registry_id": "reg-1", "strategy_id": "s-1", "checksum": "c", "version": 1,
        "owner_tenant": entry_tenant, "metadata": {"k": "v"}, "updated_at": "t",
        "last_actor": {"actor_id": "actor-1"},
    }
    receipt = {
        "command_key": "cmd-1", "registry_id": "reg-1", "receipt_key": "rk", "request_digest": "rd",
        "committed_at": "t", "committed_entry": entry,
    }
    return strategy_adapter._validate_scoped_receipt(
        receipt, command_id="cmd-1", registry_id="reg-1", strategy_id="s-1",
        expected_metadata=None, new_metadata={"k": "v"}, caller_actor_id="actor-1",
        caller_tenant=caller_tenant, original_entry=dict(entry), expected_receipt_key="rk",
        expected_request_digest="rd", action_id="update_params",
    )


@pytest.mark.parametrize("caller_tenant, entry_tenant, allowed", [
    ("tenant-a", "tenant-a", True),
    ("tenant-a", "tenant-b", False),
    ("unscoped", "tenant-b", False),
    ("", "tenant-b", False),
])
def test_registry_receipt_tenant_is_always_compared(caller_tenant, entry_tenant, allowed):
    if allowed:
        assert _validate_receipt(caller_tenant, entry_tenant)
    else:
        with pytest.raises(ActionUnavailableError) as exc:
            _validate_receipt(caller_tenant, entry_tenant)
        assert exc.value.error_code == "READBACK_MISMATCH"


# --- Mounted signed-identity regressions through the real command and readiness routes ---

from test_receipt_owner_routes import _tok, mounted, owner  # noqa: E402,F401  (fixtures)


def _owner_requests(monkeypatch) -> list:
    seen: list = []
    real = urllib.request.urlopen

    def _spy(req, *args, **kwargs):
        seen.append(getattr(req, "full_url", req))
        return real(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", _spy)
    return seen


def _owner_writes(owner) -> int:
    return json.loads(owner.read_text())["writes"]


@pytest.mark.parametrize("command, target, params", [
    ("CreateDeployment", {"type": "Deployment", "id": "plan-a"}, {}),
    ("EvolutionProgramAction", {"type": "EvolutionProgram", "id": "program-a"},
     {"action_id": "pause_program", "program_id": "program-a"}),
])
def test_command_route_binds_owner_write_to_jwt_tenant(mounted, owner, monkeypatch, command, target, params):
    client, store, _ = mounted
    requests = _owner_requests(monkeypatch)

    def submit(key, body_tenant, token=_tok("tenant-a")):
        body_params = dict(params)
        if body_tenant:
            body_params["tenant_id"] = body_tenant
        return client.post(
            "/bff/v1/commands",
            json={"command": command, "target": target, "params": body_params,
                  "audit_context": {"reason": "tenant authority audit"}},
            headers={"Authorization": token, "Idempotency-Key": key},
        )

    foreign = submit("foreign", "tenant-b")
    record = store.get_command_by_idempotency_key("foreign", operator_id="tenant-a")
    assert foreign.status_code >= 400 or (record or {}).get("status") != "executed", (foreign.text, record)
    assert (record or {}).get("status") != "executed"
    assert requests == [] and _owner_writes(owner) == 0

    for key, body_tenant in (("missing", "tenant-b"), ("missing-bare", None)):
        missing = submit(key, body_tenant, token=_tok(None))  # signed JWT without a tenant claim
        record = store.get_command_by_idempotency_key(key, operator_id="no-tenant")
        assert missing.status_code >= 400 or (record or {}).get("status") != "executed", missing.text
        assert (record or {}).get("status") != "executed"
        assert requests == [] and _owner_writes(owner) == 0

    absent = submit("absent", None)
    same = submit("same", "tenant-a")
    assert absent.status_code == 202 and same.status_code == 202, (absent.text, same.text)
    assert store.get_command_by_idempotency_key("absent", operator_id="tenant-a")["status"] == "executed"
    assert store.get_command_by_idempotency_key("same", operator_id="tenant-a")["status"] == "executed"
    assert requests and _owner_writes(owner) >= 1


def test_evolution_adapter_requires_verified_tenant_and_rejects_foreign_params(monkeypatch):
    from services.control_plane.bff.command_adapters.evolution_adapter import EvolutionCommandAdapter

    calls: list = []
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution.invalid")
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", "tenant-env-default")
    monkeypatch.setattr(
        "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
        lambda *a, **k: calls.append(k) or {"receipt_id": "r", "program_id": "p", "status": "paused"},
    )
    adapter = EvolutionCommandAdapter()

    def run(params, token):
        return adapter.execute(
            command_id="c", command_type="EvolutionProgramAction",
            params={"action_id": "pause_program", "program_id": "p", **params}, auth_token=token,
        )

    for params, token, code in [
        ({"tenant_id": "tenant-b"}, None, "TENANT_REQUIRED"),
        ({}, "Bearer not-a-jwt", "TENANT_REQUIRED"),
        ({"tenant_id": "tenant-b"}, _tok("tenant-a"), "TENANT_MISMATCH"),
        ({"payload": {"tenant_id": "tenant-b"}}, _tok("tenant-a"), "TENANT_MISMATCH"),
    ]:
        with pytest.raises(ActionUnavailableError) as exc:
            run(params, token)
        assert exc.value.error_code == code
    assert calls == []

    run({"tenant_id": "tenant-a"}, _tok("tenant-a"))
    run({}, _tok("tenant-a"))
    assert [c["headers"]["X-Tenant-Id"] for c in calls] == ["tenant-a", "tenant-a"]


def _readiness_rows():
    return [
        {"id": "a", "tenant_id": "tenant-a"},
        {"id": "b", "tenant_id": "tenant-b"},
        {"id": "tenantless-private"},
        {"id": "tenantless-public", "visibility": "public"},
    ]


@pytest.mark.parametrize("caller, expected", [("tenant-a", {"a", "tenantless-public"}),
                                              ("tenant-b", {"b", "tenantless-public"})])
def test_readiness_route_hides_tenantless_private_rows_from_signed_callers(monkeypatch, caller, expected):
    from agora.operational_readiness import create_operational_readiness_router
    from test_agora_operational_readiness_live_binding import SURFACES, AuthoritativeReadStore

    store = AuthoritativeReadStore(surfaces={name: _readiness_rows() for name in SURFACES})
    app = FastAPI()
    app.include_router(create_operational_readiness_router(
        utc_now=lambda: "2026-09-01T12:00:00Z", extract_identity=_extract_identity,
        require_read_role=_require_read_role, get_read_store=lambda: store,
    ))
    response = TestClient(app).get("/bff/agora/operational-readiness", headers=_jwt_headers(monkeypatch, caller))
    assert response.status_code == 200, response.text
    assert all(s["count"] == len(expected) for s in response.json()["data"]["surfaces"].values())
    assert store.scopes[-1].tenant_id == caller and store.mutation_calls == 0


# --- Research dispatcher: stored/plan/stage tenants are compared, never used as authority ---

class _DatasetSpy:
    def __init__(self):
        self.tenants: list = []

    def get_by_ref(self, ref, tenant_id=None, user_id=None):
        self.tenants.append(tenant_id)
        return None


@pytest.mark.parametrize("trusted", [None, "", "  "])
def test_governed_dataset_resolution_rejects_missing_trusted_tenant(trusted):
    from services.control_plane.bff.agora.research.dispatcher import resolve_governed_dataset

    spy = _DatasetSpy()
    stage = {"input_refs": ["dataset:x"], "tenant_id": "tenant-b"}
    with pytest.raises(ValueError):
        resolve_governed_dataset(stage, {"tenant_id": "tenant-b"}, dataset_store=spy, tenant_id=trusted)
    assert spy.tenants == []

    resolve_governed_dataset(stage, {"tenant_id": "tenant-b"}, dataset_store=spy, tenant_id="tenant-a")
    assert spy.tenants == ["tenant-a"]


class _OutboxStore:
    def __init__(self, records):
        self.records = records
        self.plan_reads: list = []

    def list_outbox_records(self, **kwargs):
        return self.records

    def get_plan(self, plan_id, tenant_id=None, user_id=None):
        self.plan_reads.append(tenant_id)
        return None


def test_drain_outbox_never_invents_or_widens_tenant():
    from services.control_plane.bff.agora.research.dispatcher import ResearchDispatcher

    base = {"plan_id": "p", "stage_id": "s", "run_id": "r", "user_id": "u"}
    records = [
        {**base},  # tenantless record: previously defaulted to "pantheon-dev"
        {**base, "tenant_id": "tenant-b"},
        {**base, "tenant_id": "tenant-a"},
    ]
    store = _OutboxStore(records)
    ResearchDispatcher(store=store).drain_outbox(tenant_id="tenant-a")
    assert store.plan_reads == ["tenant-a"]

    store.plan_reads.clear()
    ResearchDispatcher(store=store).drain_outbox()
    assert store.plan_reads == ["tenant-b", "tenant-a"]  # tenantless record skipped, never defaulted


def test_journal_create_denies_jwt_without_tenant_authority_under_all_defaults(monkeypatch, tmp_path):
    owner = build_decision_journal_write_owner(data_dir=str(tmp_path))
    client = _journal_client(owner)
    _jwt_headers(monkeypatch, "tenant-a")
    no_tenant_headers = _jwt_without_tenant()

    # 1. Built-in default ("pantheon-dev") and absent body tenant: both fail closed
    res_absent = client.post(
        "/bff/agora/journal",
        headers={**no_tenant_headers, "Idempotency-Key": "idem-absent-1"},
        json={"title": "x", "body": "y"},
    )
    assert res_absent.status_code == 403, res_absent.text
    assert _stored_journal_tenants(owner) == []

    res_builtin = client.post(
        "/bff/agora/journal",
        headers={**no_tenant_headers, "Idempotency-Key": "idem-builtin-1"},
        json={"title": "x", "body": "y", "tenant_id": "pantheon-dev"},
    )
    assert res_builtin.status_code == 403, res_builtin.text
    assert _stored_journal_tenants(owner) == []

    res_foreign = client.post(
        "/bff/agora/journal",
        headers={**no_tenant_headers, "Idempotency-Key": "idem-foreign-1"},
        json={"title": "x", "body": "y", "tenant_id": "tenant-b"},
    )
    assert res_foreign.status_code == 403, res_foreign.text
    assert _stored_journal_tenants(owner) == []

    # 2. Active matching environment default ("tenant-b") kept active after token config
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-b")
    res_env_absent = client.post(
        "/bff/agora/journal",
        headers={**no_tenant_headers, "Idempotency-Key": "idem-env-absent-1"},
        json={"title": "x", "body": "y"},
    )
    assert res_env_absent.status_code == 403, res_env_absent.text
    assert _stored_journal_tenants(owner) == []

    res_env_match = client.post(
        "/bff/agora/journal",
        headers={**no_tenant_headers, "Idempotency-Key": "idem-env-match-1"},
        json={"title": "x", "body": "y", "tenant_id": "tenant-b"},
    )
    assert res_env_match.status_code == 403, res_env_match.text
    assert _stored_journal_tenants(owner) == []

    res_env_foreign = client.post(
        "/bff/agora/journal",
        headers={**no_tenant_headers, "Idempotency-Key": "idem-env-foreign-1"},
        json={"title": "x", "body": "y", "tenant_id": "tenant-c"},
    )
    assert res_env_foreign.status_code == 403, res_env_foreign.text
    assert _stored_journal_tenants(owner) == []

    # 3. Positive controls with valid tenant authority
    valid_headers = _jwt_headers(monkeypatch, "tenant-a")
    res_same = client.post(
        "/bff/agora/journal",
        headers={**valid_headers, "Idempotency-Key": "idem-pos-same"},
        json={"title": "x", "body": "y", "tenant_id": "tenant-a"},
    )
    assert res_same.status_code == 201, res_same.text
    assert _stored_journal_tenants(owner) == ["tenant-a"]

    res_pos_absent = client.post(
        "/bff/agora/journal",
        headers={**valid_headers, "Idempotency-Key": "idem-pos-absent"},
        json={"title": "x", "body": "y"},
    )
    assert res_pos_absent.status_code == 201, res_pos_absent.text
    assert _stored_journal_tenants(owner) == ["tenant-a", "tenant-a"]

    res_pos_foreign = client.post(
        "/bff/agora/journal",
        headers={**valid_headers, "Idempotency-Key": "idem-pos-foreign"},
        json={"title": "x", "body": "y", "tenant_id": "tenant-b"},
    )
    assert res_pos_foreign.status_code == 403, res_pos_foreign.text
    assert _stored_journal_tenants(owner) == ["tenant-a", "tenant-a"]



def _jwt_without_tenant() -> dict[str, str]:
    now = int(time.time())
    token = encode_jwt_hs256(
        {"sub": "low-priv-operator", "roles": ["operator"], "iss": _ISSUER, "aud": _AUDIENCE,
         "iat": now, "exp": now + 3600},
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


_RECEIPT_B = {
    "receipt_id": "receipt-b", "audit_event_id": "audit-b", "suggestion_id": "sg-tenant-b", "strategy_id": "s1",
    "action": "apply", "previous_status": "proposed", "status": "applied", "previous_version": 1, "version": 2,
    "actor_id": "low-priv-operator", "recorded_at": "t",
    "authoritative_readback": {
        "suggestion_id": "sg-tenant-b", "strategy_id": "s1", "period": "30d", "status": "applied", "version": 2,
        "provenance": {"source_id": "src", "source_type": "test", "produced_at": "t"}, "as_of": "t",
    },
}


def test_receipt_route_and_store_never_widen_to_an_empty_tenant(monkeypatch, tmp_path):
    from services.control_plane.bff.agora.performance.router import create_performance_router

    store = _suggestion_store(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO performance_action_receipts VALUES (?,?,?,?,?,?,?,?,?)",
            ("receipt-b", "tenant-b", "low-priv-operator", "sg-tenant-b", "s1", "k", "h", json.dumps(_RECEIPT_B), "t"),
        )
    assert store.get_receipt(tenant_id="tenant-b", owner_user_id="", receipt_id="receipt-b") == _RECEIPT_B
    for tenant in ("", "  ", None):
        assert store.get_receipt(tenant_id=tenant, owner_user_id="", receipt_id="receipt-b") is None

    app = FastAPI()
    app.include_router(create_performance_router(
        extract_identity=_extract_identity, require_read_role=_require_read_role,
        require_write_role=_require_operator_role, bff_error=_bff_error,
        utc_now=lambda: "2026-10-03T00:00:00Z", get_trade_journey_store=lambda: None, suggestion_store=store,
    ))
    client = TestClient(app, raise_server_exceptions=False)
    url = "/bff/agora/performance/action-receipts/receipt-b"
    foreign = client.get(url, headers=_jwt_headers(monkeypatch, "tenant-a"))
    absent = client.get(url, headers=_jwt_without_tenant())
    assert foreign.status_code == 404 and absent.status_code >= 400
    assert "receipt-b" not in absent.text
    same = client.get(url, headers=_jwt_headers(monkeypatch, "tenant-b"))
    assert same.status_code == 200 and "receipt-b" in same.text, same.text


def test_mounted_suggestion_read_route_scopes_to_the_jwt_tenant(monkeypatch, tmp_path):
    from services.control_plane.bff.agora.performance.router import create_performance_router

    store = _suggestion_store(tmp_path)
    app = FastAPI()
    app.include_router(create_performance_router(
        extract_identity=_extract_identity, require_read_role=_require_read_role,
        require_write_role=_require_operator_role, bff_error=_bff_error,
        utc_now=lambda: "2026-10-03T00:00:00Z", get_trade_journey_store=lambda: None, suggestion_store=store,
    ))
    client = TestClient(app, raise_server_exceptions=False)
    url = "/bff/agora/trading-room/strategies/s1/performance?period=30d"
    seen: list = []
    real = store.list_suggestions
    monkeypatch.setattr(store, "list_suggestions", lambda *a, **kw: seen.append(kw.get("tenant_id")) or real(*a, **kw))
    absent = client.get(url, headers=_jwt_without_tenant())
    assert absent.status_code >= 400 and seen == [], absent.text
    for tenant in ("tenant-a", "tenant-b"):
        response = client.get(url, headers=_jwt_headers(monkeypatch, tenant))
        assert response.status_code == 200, response.text
        foreign = "tenant-b" if tenant == "tenant-a" else "tenant-a"
        assert seen[-1] == tenant and f'"{foreign}"' not in response.text


def test_persona_operations_read_model_and_journal_recovery_require_the_caller_tenant(tmp_path):
    from services.control_plane.bff.management_read_models.service import ManagementService

    persona = {"persona_id": "p1", "tenant_id": "tenant-b"}
    service = ManagementService.__new__(ManagementService)
    service._ops_read_model_entry_fn = None
    service._utc_now = lambda: "2026-10-03T00:00:00Z"
    service._resolve_store = lambda: type("S", (), {"get_persona": lambda self, pid: persona})()
    for tenant in (None, "", "tenant-a"):
        assert service.get_operations_read_model("p1", tenant_id=tenant) is None
    assert service.get_operations_read_model("p1", tenant_id="tenant-b") is not None

    owner = build_decision_journal_write_owner(data_dir=str(tmp_path))
    entry = owner.create_decision_journal_entry(
        title="Recoverable Decision", body="Audit body", created_at="2026-10-03T00:00:00Z",
        tenant_id="tenant-b", user_id="u", actor_id="u",
    )
    record = {"idempotency_key": "idem-rec", "tenant_id": "tenant-b", "user_id": "u", "entry_id": entry["id"]}
    for tenant in (None, "", "  ", "tenant-a"):
        assert owner._recover_committed_entry_result(
            record, entry_id=entry["id"], tenant_id=tenant, user_id="u") is None
    positive = owner._recover_committed_entry_result(
        record, entry_id=entry["id"], tenant_id="tenant-b", user_id="u")
    assert positive is not None and positive.get("data", {}).get("id") == entry["id"]
    assert positive.get("data", {}).get("tenant_id") == "tenant-b"


def test_mounted_journal_crash_recovery_scopes_to_the_jwt_tenant(monkeypatch, tmp_path):
    owner = build_decision_journal_write_owner(data_dir=str(tmp_path))
    entry = owner.create_decision_journal_entry(
        title="Crash Recoverable", body="Audit body", created_at="2026-10-03T00:00:00Z",
        tenant_id="tenant-b", user_id="low-priv-operator", actor_id="low-priv-operator",
    )
    headers_b = _jwt_headers(monkeypatch, "tenant-b")
    client = _journal_client(owner)
    payload = {"id": entry["id"], "title": "Crash Recoverable", "body": "Audit body"}

    captured = []
    real_check = owner.check_create_idempotency

    def get_hash(*a, **kw):
        captured.append(kw.get("request_hash"))
        return real_check(*a, **kw)

    owner.check_create_idempotency = get_hash
    client.post("/bff/agora/journal", headers={**headers_b, "Idempotency-Key": "probe-key"}, json=payload)
    owner.check_create_idempotency = real_check

    scoped_key = "create:tenant-b:low-priv-operator:idem-rec"
    owner.stores.idempotency.put({
        "idempotency_key": scoped_key,
        "raw_idempotency_key": "idem-rec",
        "tenant_id": "tenant-b",
        "user_id": "low-priv-operator",
        "actor_id": "low-priv-operator",
        "entry_id": entry["id"],
        "status": "pending",
        "created_pid": 9999999,
        "request_hash": captured[0],
        "created_at": time.time(),
    })

    # 1. Absent tenant fails closed
    absent = client.post("/bff/agora/journal", headers={**_jwt_without_tenant(), "Idempotency-Key": "idem-rec"}, json=payload)
    assert absent.status_code >= 400

    # 2. Foreign tenant denied recovery
    headers_a = _jwt_headers(monkeypatch, "tenant-a")
    foreign = client.post("/bff/agora/journal", headers={**headers_a, "Idempotency-Key": "idem-rec"}, json={**payload, "tenant_id": "tenant-b"})
    assert foreign.status_code == 403

    # 3. Same tenant recovery succeeds
    same = client.post("/bff/agora/journal", headers={**headers_b, "Idempotency-Key": "idem-rec"}, json=payload)
    assert same.status_code in (200, 201), same.text
    assert same.json().get("data", {}).get("id") == entry["id"]
    assert same.json().get("data", {}).get("tenant_id") == "tenant-b"


def test_runtime_pause_binding_tenant_never_fills_the_caller_tenant(monkeypatch):
    from unittest.mock import MagicMock

    from services.control_plane.bff.command_adapters import runtime_adapter
    from services.control_plane.bff.command_adapters.runtime_adapter import RuntimeCommandAdapter

    binding = {"binding_id": "binding-b", "runtime_id": "rt-1", "status": "active",
               "deployment_mode": "paper", "metadata": {"tenant_id": "tenant-b"}}
    rm = MagicMock()
    rm.list_all.return_value = [binding]
    rm.get.return_value = {**binding, "status": "paused"}
    http = MagicMock(return_value={"status": "executed", "status_after": "paused", "binding_id": "binding-b"})
    monkeypatch.setattr(runtime_adapter, "_get_runtime_manager_client", lambda: rm)
    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: None)
    monkeypatch.setattr(runtime_adapter, "http_request_json", http)
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://internal.invalid")

    def pause(token, **params):
        return RuntimeCommandAdapter().execute(
            "c", "PausePaperRuntime", {"entity_type": "Runtime", "entity_id": "rt-1", **params}, auth_token=token)

    for token, params in ((None, {}), (_tok_for("tenant-a"), {}), (_jwt_without_tenant()["Authorization"], {}),
                          (_tok_for("tenant-b"), {"tenant_id": "tenant-a"})):
        with pytest.raises(ActionUnavailableError) as exc:
            pause(token, **params)
        assert exc.value.error_code == "TENANT_MISMATCH"
    http.assert_not_called()
    assert pause(_tok_for("tenant-b"))["status"] == "executed"
    assert http.call_count == 1


def _tok_for(tenant: str) -> str:
    return _jwt_headers_for(tenant)["Authorization"]


def _jwt_headers_for(tenant: str) -> dict[str, str]:
    now = int(time.time())
    token = encode_jwt_hs256(
        {"sub": "op", "roles": ["operator"], "tenant_id": tenant, "iss": _ISSUER, "aud": _AUDIENCE,
         "iat": now, "exp": now + 3600},
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _operator_of(token) -> str:
    return {_tok(None): "no-tenant", _tok("tenant-a"): "tenant-a", _tok("tenant-b"): "tenant-b"}[token]


@pytest.mark.parametrize("command, status_after", [("PausePaperRuntime", "paused"), ("ResumePaperRuntime", "active")])
def test_mounted_runtime_pause_resume_use_the_jwt_tenant(mounted, monkeypatch, command, status_after):
    from unittest.mock import MagicMock

    from services.control_plane.bff.command_adapters import runtime_adapter

    client, store, ports = mounted
    binding = {"binding_id": "binding-b", "runtime_id": "rt-1", "status": "active",
               "deployment_mode": "paper", "metadata": {"tenant_id": "tenant-a"}}
    monkeypatch.setattr(ports, "get_runtime_binding_by_runtime_id", lambda runtime_id: binding, raising=False)
    approval = {"decision_id": "approval-1", "decision": "approved", "command": command,
                "target": {"type": "Runtime", "id": "rt-1"}}
    monkeypatch.setattr(ports, "get_approval_decision", lambda decision_id: approval, raising=False)
    rm = MagicMock()
    rm.list_all.return_value = [binding]
    rm.get.return_value = {**binding, "status": status_after}
    http = MagicMock(return_value={"status": "executed", "status_after": status_after, "binding_id": "binding-b"})
    monkeypatch.setattr(runtime_adapter, "_get_runtime_manager_client", lambda: rm)
    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: None)
    monkeypatch.setattr(runtime_adapter, "http_request_json", http)
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://internal.invalid")

    def submit(key, token, body_tenant=None):
        operator = _operator_of(token)
        confirm = client.post(
            "/bff/confirm-tokens", headers={"Authorization": token, "Idempotency-Key": f"ct-{key}"},
            json={"tokenId": f"token-{key}", "command": command, "target": {"type": "Runtime", "id": "rt-1"},
                  "issuedForOperatorId": operator},
        )
        assert confirm.status_code == 201, confirm.text
        params = {"runtime_id": "rt-1", "approvalDecisionId": "approval-1",
                  **({"tenant_id": body_tenant} if body_tenant else {})}
        return client.post(
            "/bff/v1/commands",
            json={"command": command, "target": {"type": "Runtime", "id": "rt-1"}, "params": params,
                  "audit_context": {"reason": "tenant authority audit"}},
            headers={"Authorization": token, "Idempotency-Key": key, "X-Confirm-Token": f"token-{key}"},
        )

    for key, token, body_tenant in (("foreign", _tok("tenant-b"), None), ("foreign-body", _tok("tenant-b"), "tenant-a"),
                                    ("absent", _tok(None), None), ("absent-body", _tok(None), "tenant-a")):
        response = submit(key, token, body_tenant)
        record = store.get_command_by_idempotency_key(key, operator_id=_operator_of(token))
        assert response.status_code >= 400 or (record or {}).get("status") != "executed", response.text
        assert (record or {}).get("status") != "executed"
        http.assert_not_called()

    same = submit("same", _tok("tenant-a"), "tenant-a")
    assert same.status_code == 202, same.text
    assert store.get_command_by_idempotency_key("same", operator_id="tenant-a")["status"] == "executed"
    assert http.call_count == 1


def test_mounted_operations_read_model_route_scopes_to_the_jwt_tenant(monkeypatch):
    import os
    from services.control_plane.bff.auth.policy import bff_me_tenant_payload
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.management_read_models.router import create_management_router

    personas = {
        "p1": {"persona_id": "p1", "tenant_id": "tenant-b"},
        "p-dev": {"persona_id": "p-dev", "tenant_id": "pantheon-dev"},
        "p-custom": {"persona_id": "p-custom", "tenant_id": "custom-default"},
        "p-unscoped": {"persona_id": "p-unscoped"},
    }

    class _Store:
        def get_persona(self, persona_id):
            return personas.get(persona_id)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_management_router(
        read_surface=_Store(), extract_identity=_extract_identity, require_read_role=_require_read_role,
        tenant_payload_fn=bff_me_tenant_payload,
    ))
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Custom tenant: absent fails closed with 403; foreign returns 404; same tenant returns 200
    url_b = "/bff/management/operations-read-model/p1"
    assert client.get(url_b, headers=_jwt_without_tenant()).status_code == 403
    assert client.get(url_b, headers=_jwt_headers(monkeypatch, "tenant-a")).status_code == 404
    assert client.get(url_b, headers=_jwt_headers(monkeypatch, "tenant-b")).status_code == 200

    # 2. Built-in default fallback ("pantheon-dev"): absent fails closed with 403; foreign returns 404; same returns 200
    url_dev = "/bff/management/operations-read-model/p-dev"
    assert client.get(url_dev, headers=_jwt_without_tenant()).status_code == 403
    assert client.get(url_dev, headers=_jwt_headers(monkeypatch, "tenant-a")).status_code == 404
    assert client.get(url_dev, headers=_jwt_headers(monkeypatch, "pantheon-dev")).status_code == 200

    # 3. Environment default fallback ("custom-default"): absent fails closed with 403; foreign returns 404; same returns 200
    tok_absent = _jwt_without_tenant()
    tok_a = _jwt_headers_for("tenant-a")
    tok_custom = _jwt_headers_for("custom-default")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "custom-default")
    assert os.environ.get("PANTHEON_BFF_TENANT_ID") == "custom-default"

    url_custom = "/bff/management/operations-read-model/p-custom"
    assert client.get(url_custom, headers=tok_absent).status_code == 403
    assert os.environ.get("PANTHEON_BFF_TENANT_ID") == "custom-default"
    assert client.get(url_custom, headers=tok_a).status_code == 404
    assert os.environ.get("PANTHEON_BFF_TENANT_ID") == "custom-default"
    assert client.get(url_custom, headers=tok_custom).status_code == 200
    assert os.environ.get("PANTHEON_BFF_TENANT_ID") == "custom-default"

    # 4. Tenantless stored persona: absent fails closed with 403 at shared resolver before service is called
    url_unscoped = "/bff/management/operations-read-model/p-unscoped"
    assert client.get(url_unscoped, headers=_jwt_without_tenant()).status_code == 403
    assert client.get(url_unscoped, headers=_jwt_headers(monkeypatch, "tenant-a")).status_code == 200


def test_mounted_risk_radar_scopes_to_the_jwt_tenant(monkeypatch):
    from services.control_plane.bff.auth.policy import bff_me_tenant_payload
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.management_read_models.router import create_management_router

    class _RadarStore:
        def list_personas(self, tenant_id=None):
            personas = {
                "tenant-a": [{"persona_id": "p-a", "name": "Persona A", "tenant_id": "tenant-a"}],
                "tenant-b": [{"persona_id": "p-b", "name": "Persona B", "tenant_id": "tenant-b"}],
            }
            if tenant_id is None:
                return personas["tenant-a"] + personas["tenant-b"]
            return personas.get(tenant_id, [])

        def list_risk_radar_rows(self):
            return [
                {"persona_id": "p-a", "risk_state": "normal"},
                {"persona_id": "p-b", "risk_state": "normal"},
            ]

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_management_router(
        read_surface=_RadarStore(), extract_identity=_extract_identity, require_read_role=_require_read_role,
        tenant_payload_fn=bff_me_tenant_payload,
    ))
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Absent tenant claim fails closed at resolver with 403 before store is called
    assert client.get("/bff/management/risk-radar", headers=_jwt_without_tenant()).status_code == 403

    # 2. Foreign tenant (tenant-a) sees Persona A label, does not leak Persona B label
    res_a = client.get("/bff/management/risk-radar", headers=_jwt_headers(monkeypatch, "tenant-a"))
    assert res_a.status_code == 200
    rows_a = {r["persona_id"]: r["persona_label"] for r in (res_a.json().get("data") or {}).get("items", [])}
    assert rows_a.get("p-a") == "Persona A"
    assert rows_a.get("p-b") != "Persona B"

    # 3. Same tenant (tenant-b) sees Persona B label
    res_b = client.get("/bff/management/risk-radar", headers=_jwt_headers(monkeypatch, "tenant-b"))
    assert res_b.status_code == 200
    rows_b = {r["persona_id"]: r["persona_label"] for r in (res_b.json().get("data") or {}).get("items", [])}
    assert rows_b.get("p-b") == "Persona B"


def test_mounted_strategy_command_route_with_tenant_enforcing_owner(mounted, monkeypatch):
    import base64
    from test_receipt_owner_routes import _JWT_ENV
    from services.control_plane.bff.command_adapters import strategy_adapter
    from services.registry.pg_store import PostgresRegistryStore, _request_digest

    client, store, _ = mounted

    entry_b = {
        "registry_id": "reg-1",
        "strategy_id": "s-1",
        "checksum": "sha256:123",
        "version": 1,
        "owner_tenant": "tenant-b",
        "metadata": {"note": "old"},
        "updated_at": "2026-10-03T00:00:00Z",
        "last_actor": {"actor_id": "tenant-b", "tenant": "tenant-b"},
    }
    patched_b = {**entry_b, "metadata": {"note": "new"}, "updated_at": "2026-10-03T00:00:01Z"}
    rd = _request_digest({"registry_id": "reg-1", "expected_metadata": {"note": "old"}, "metadata": {"note": "new"}})

    owner_state = {"writes": 0, "downstream_calls": []}
    last_cmd = [None]

    def mock_registry_owner(url, method="GET", auth_token=None, payload=None, **kwargs):
        owner_state["downstream_calls"].append((method, url))
        raw = (auth_token or "").removeprefix("Bearer ").strip()
        claims = json.loads(base64.urlsafe_b64decode(raw.split(".")[1] + "==")) if "." in raw else {}
        tenant = claims.get("tenant_id")
        actor = claims.get("sub")

        if tenant != entry_b["owner_tenant"]:
            return 403, {}, {"detail": "Tenant access denied"}

        if method == "GET" and "/receipts/" not in url:
            return 200, {}, {"entry": entry_b}
        elif method == "PATCH":
            owner_state["writes"] += 1
            cmd_key = (payload or {}).get("command_key") or "cmd-1"
            last_cmd[0] = cmd_key
            rk = PostgresRegistryStore.receipt_key(
                cmd_key, "reg-1", actor={"actor_id": actor, "tenant": tenant}, command_type="metadata",
            )
            receipt = {
                "command_key": cmd_key, "registry_id": "reg-1", "receipt_key": rk,
                "request_digest": rd, "committed_at": "2026-10-03T00:00:01Z", "committed_entry": patched_b,
            }
            return 200, {"X-Idempotent-Replay": "false"}, {"entry": patched_b, "receipt": receipt}
        elif method == "GET" and "/receipts/" in url:
            cmd_key = last_cmd[0] or url.split("/receipts/")[1].split("?")[0]
            rk = PostgresRegistryStore.receipt_key(
                cmd_key, "reg-1", actor={"actor_id": actor, "tenant": tenant}, command_type="metadata",
            )
            receipt = {
                "command_key": cmd_key, "registry_id": "reg-1", "receipt_key": rk,
                "request_digest": rd, "committed_at": "2026-10-03T00:00:01Z", "committed_entry": patched_b,
            }
            return 200, {}, {"receipt": receipt}
        return 404, {}, {}

    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", _JWT_ENV["PANTHEON_BFF_JWT_SECRET"])
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_ISSUER", _JWT_ENV["PANTHEON_BFF_JWT_ISSUER"])
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_AUDIENCE", _JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"])
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://127.0.0.1:9999")
    monkeypatch.setattr(strategy_adapter, "http_request_json_with_headers", mock_registry_owner)

    def submit_command(key, token):
        return client.post(
            "/bff/v1/commands",
            json={
                "command": "StrategyAction",
                "target": {"type": "Strategy", "id": "s-1"},
                "params": {
                    "action_id": "update_params",
                    "strategy_id": "s-1",
                    "registry_id": "reg-1",
                    "expected_metadata": {"note": "old"},
                    "metadata": {"note": "new"},
                },
                "audit_context": {"reason": "tenant authority audit"},
            },
            headers={"Authorization": token, "Idempotency-Key": key},
        )

    # 1. Absent tenant JWT fails closed before any owner call
    resp_absent = submit_command("strat-absent", _tok(None))
    rec_absent = store.get_command_by_idempotency_key("strat-absent", operator_id="no-tenant")
    assert resp_absent.status_code >= 400 or (rec_absent or {}).get("status") != "executed"
    assert (rec_absent or {}).get("status") != "executed"
    assert owner_state["downstream_calls"] == []
    assert owner_state["writes"] == 0

    # 2. Foreign tenant denied by tenant-enforcing owner before mutating write
    resp_foreign = submit_command("strat-foreign", _tok("tenant-a"))
    rec_foreign = store.get_command_by_idempotency_key("strat-foreign", operator_id="tenant-a")
    assert resp_foreign.status_code >= 400 or (rec_foreign or {}).get("status") != "executed"
    assert (rec_foreign or {}).get("status") != "executed"
    assert owner_state["writes"] == 0

    # 3. Same tenant positive control succeeds and commits write
    resp_same = submit_command("strat-same", _tok("tenant-b"))
    rec_same = store.get_command_by_idempotency_key("strat-same", operator_id="tenant-b")
    assert resp_same.status_code == 202
    assert rec_same["status"] == "executed"
    assert owner_state["writes"] == 1


def test_strategy_registry_receipt_mismatch_post_write_semantics(monkeypatch):
    """Direct adapter test verifying post-write readback receipt verification.

    If a downstream registry owner permitted a PATCH from a foreign caller,
    StrategyCommandAdapter._validate_scoped_receipt catches the tenant
    divergence during post-write readback verification and raises
    ActionUnavailableError(READBACK_MISMATCH).
    """
    import base64
    from services.control_plane.bff.command_adapters import strategy_adapter
    from services.control_plane.bff.command_adapters.strategy_adapter import StrategyCommandAdapter
    from services.registry.pg_store import PostgresRegistryStore, _request_digest

    entry_b = {
        "registry_id": "reg-1", "strategy_id": "s-1", "checksum": "sha256:123", "version": 1,
        "owner_tenant": "tenant-b", "metadata": {"note": "old"}, "updated_at": "2026-10-03T00:00:00Z",
        "last_actor": {"actor_id": "op"},
    }
    patched_b = {**entry_b, "metadata": {"note": "new"}}
    rd = _request_digest({"registry_id": "reg-1", "expected_metadata": {"note": "old"}, "metadata": {"note": "new"}})

    def mock_http(url, method="GET", auth_token=None, **kwargs):
        raw = (auth_token or "").removeprefix("Bearer ").strip()
        claims = json.loads(base64.urlsafe_b64decode(raw.split(".")[1] + "==")) if "." in raw else {}
        tenant = claims.get("tenant_id")
        rk = PostgresRegistryStore.receipt_key("cmd-1", "reg-1", actor={"actor_id": "op", "tenant": tenant}, command_type="metadata")
        receipt = {"command_key": "cmd-1", "registry_id": "reg-1", "receipt_key": rk, "request_digest": rd, "committed_at": "2026-10-03T00:00:00Z", "committed_entry": patched_b}
        if method == "GET":
            return 200, {}, {"entry": entry_b, "receipt": receipt}
        elif method == "PATCH":
            return 200, {"X-Idempotent-Replay": "false"}, {"entry": patched_b, "receipt": receipt}
        return 404, {}, {}

    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_ISSUER", _ISSUER)
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://127.0.0.1:9999")
    monkeypatch.setattr(strategy_adapter, "http_request_json_with_headers", mock_http)

    adapter = StrategyCommandAdapter()
    params = {
        "action_id": "update_params", "strategy_id": "s-1", "registry_id": "reg-1",
        "expected_metadata": {"note": "old"}, "metadata": {"note": "new"},
    }

    # Absent tenant fails closed
    with pytest.raises(ActionUnavailableError) as exc_absent:
        adapter.execute("cmd-1", "StrategyAction", params, auth_token=_jwt_without_tenant()["Authorization"])
    assert exc_absent.value.error_code == "FORBIDDEN"

    # Foreign tenant fails closed on receipt readback mismatch if owner accepted write
    with pytest.raises(ActionUnavailableError) as exc_foreign:
        adapter.execute("cmd-1", "StrategyAction", params, auth_token=_tok_for("tenant-a"))
    assert exc_foreign.value.error_code == "READBACK_MISMATCH"

    # Same tenant positive control succeeds
    res = adapter.execute("cmd-1", "StrategyAction", params, auth_token=_tok_for("tenant-b"))
    assert res["status"] == "metadata_updated"
    assert res["entity_id"] == "s-1"

