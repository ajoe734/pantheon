"""Tests for BFF-PERSONA-SUBROUTES-USER-PRIVATE-VISIBILITY-20261128.

The 15 GET persona subroutes inventoried by BFF-PERSONA-LIST-USER-PRIVATE-VISIBILITY-20261128
apply the same user_private owner rule as GET /bff/personas/{persona_id}:

1. A non-owner reading a user_private record gets the exact missing-persona 404 body.
2. The owner, a persona without owner_scope / visibility_scope, and a trading persona keep
   their current answer.
3. The rule is the shared read-side check (_ensure_persona_exists with viewer_user_id) built on
   _persona_fleet_record_visible_to and _fleet_viewer_user_id; the write routes keep
   _ensure_persona_exists.
4. A rejected request makes no memory, audit, telemetry, persona-owner or pool owner call,
   and every request costs at most one Agora scope resolution (zero for non-private records).
"""
from __future__ import annotations

import copy
import inspect
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff import trade_journal
from services.control_plane.bff.agora.identity import scope as agora_scope
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.routes import detail as detail_routes
from services.control_plane.bff.personas.routes import lifecycle as lifecycle_routes
from services.control_plane.bff.personas.routes.common import PersonaRouteContext
from services.control_plane.bff.personas.routes.detail import build_detail_router
from services.control_plane.bff.personas.routes.lifecycle import build_lifecycle_router
from services.control_plane.bff.personas.service import PersonaDirectorySnapshot

TENANT_ID = "pantheon-dev"
ADMIN_TEST_USER = "pantheon-dev-admin-test"
OPERATOR_A_USER = "pantheon-dev-operator-a"

SERVANT_ADMIN_TEST = "agora-servant-799a3ce8c021b91cbb54"
SERVANT_OPERATOR_A = "agora-servant-fbeaee01e8f34c786555"
SERVANT_UNSCOPED = "agora-servant-unscoped-public"
TRADER = "persona-2448c7053e33ea48da47"
MISSING = "persona-does-not-exist"

# (route path template, owner bucket called after the gate)
SUBROUTES: List[Tuple[str, str]] = [
    ("/bff/personas/{pid}/route-policy", "route_policy"),
    ("/bff/personas/{pid}/runtime-profile", "runtime_profile"),
    ("/bff/personas/{pid}/activity", "activity"),
    ("/bff/personas/{pid}/evaluations", "evaluations"),
    ("/bff/personas/{pid}/memory", "memory"),
    ("/bff/personas/{pid}/audit", "audit"),
    ("/bff/personas/{pid}/skills", "capability_snapshot"),
    ("/bff/personas/{pid}/tools", "capability_snapshot"),
    ("/bff/personas/{pid}/capabilities", "capability_snapshot"),
    ("/bff/personas/{pid}/strategy-pool/publish-history", "pool_history"),
    ("/bff/personas/{pid}/strategy-matches", "strategy_matches"),
    ("/bff/personas/{pid}/trade-journal", "telemetry"),
    ("/bff/personas/{pid}/trade-journal/ep-1", "telemetry"),
    ("/bff/personas/{pid}/trade-reflections", "persona_owner"),
    ("/bff/personas/{pid}/trade-patterns", "persona_owner"),
]
OWNER_BUCKETS = sorted({bucket for _, bucket in SUBROUTES})


# The routes and the shared service check raise the same production error envelope.
_bff_error = personas_service._bff_error


def _trader(persona_id: str) -> Dict[str, Any]:
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "tenant_id": TENANT_ID,
        "name": f"Trader {persona_id}",
        "lifecycle_state": "paper_owner",
        "archetype": "momentum",
        "strategy_family": "daily_learning_paper_momentum",
        "owner": ADMIN_TEST_USER,
        "metadata": {"owner": ADMIN_TEST_USER, "tenant_id": TENANT_ID, "capital_mode": "paper"},
    }


def _servant(persona_id: str, agora_user_id: str, *, scoped: bool = True) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"persona_class": "agora_servant", "agora_user_id": agora_user_id}
    if scoped:
        meta["owner_scope"] = "user_private"
        meta["visibility_scope"] = "private"
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "tenant_id": TENANT_ID,
        "name": "Agora Servant",
        "lifecycle_state": "paper_owner",
        "archetype": "agora_servant",
        "owner": agora_user_id,
        "metadata": meta,
    }


def _identity_for_header(authorization: Optional[str], **_: Any) -> Any:
    raw = str(authorization or "").strip()
    if raw.startswith("Bearer "):
        raw = raw[len("Bearer "):].strip()
    return SimpleNamespace(
        operator_id=raw or ADMIN_TEST_USER,
        tenant_id=TENANT_ID,
        roles=["operator"],
        claims={},
    )


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> Tuple[TestClient, Dict[str, int]]:
    counts: Dict[str, int] = {"scope_resolutions": 0, "directory_reads": 0, **{b: 0 for b in OWNER_BUCKETS}}
    records = {
        r["id"]: r
        for r in (
            _trader(TRADER),
            _servant(SERVANT_ADMIN_TEST, ADMIN_TEST_USER),
            _servant(SERVANT_OPERATOR_A, OPERATOR_A_USER),
            _servant(SERVANT_UNSCOPED, OPERATOR_A_USER, scoped=False),
        )
    }
    snapshot = PersonaDirectorySnapshot(
        tenant_id=TENANT_ID,
        snapshot_at="2026-11-28T00:00:00Z",
        records_by_id=records,
        catalog_defaults_by_id={},
    )

    def _directory(*_a: Any, **_kw: Any) -> PersonaDirectorySnapshot:
        counts["directory_reads"] += 1
        return snapshot

    def _hit(bucket: str, value: Any) -> Any:
        counts[bucket] += 1
        return copy.deepcopy(value)

    real_resolve_scope = agora_scope.resolve_agora_user_scope

    def _counted_resolve_scope(*args: Any, **kwargs: Any) -> Any:
        counts["scope_resolutions"] += 1
        return real_resolve_scope(*args, **kwargs)

    monkeypatch.setattr(agora_scope, "resolve_agora_user_scope", _counted_resolve_scope)
    monkeypatch.setattr(personas_service, "_get_persona_directory_snapshot", _directory)
    tenant_payload = lambda identity, requested_tenant=None: {"id": TENANT_ID}  # noqa: E731
    monkeypatch.setattr(detail_routes, "_bff_me_tenant_payload", tenant_payload)

    # Detail owners, each counted after the gate.
    monkeypatch.setattr(
        detail_routes,
        "_retrieve_canonical_persona_memory",
        lambda pid, *a, **kw: _hit("memory", ([], {"available": True})),
    )
    monkeypatch.setattr(detail_routes, "_list_governance_audit_events", lambda **kw: _hit("audit", []))
    monkeypatch.setattr(
        detail_routes,
        "build_persona_runtime_profile",
        lambda persona, **kw: _hit("runtime_profile", SimpleNamespace(to_dict=lambda: {"persona_id": persona["persona_id"]})),
    )
    monkeypatch.setattr(detail_routes, "_merged_skill_records", lambda: [])
    monkeypatch.setattr(detail_routes, "_merged_tool_records", lambda: [])
    service = SimpleNamespace(
        get_route_policy_for_persona=lambda pid: _hit("route_policy", {"personaId": pid, "rules": []}),
        get_persona_consult_policy=lambda pid: {},
        get_persona=lambda pid: copy.deepcopy(records.get(pid)),
        get_sessions_for_persona=lambda pid: _hit("activity", []),
        list_consultations_for_persona=lambda pid: [],
        get_teaching_sessions_for_persona=lambda pid: _hit("evaluations", []),
        get_capability_snapshot_for_persona=lambda pid: _hit("capability_snapshot", {}),
    )

    # Lifecycle owners.
    monkeypatch.setattr(
        lifecycle_routes,
        "_pool_publisher",
        lambda authorization: SimpleNamespace(history=lambda tenant, pid: _hit("pool_history", [])),
    )

    def _discovery_after_gate(*_a: Any, **_kw: Any) -> Any:
        counts["strategy_matches"] += 1
        return SimpleNamespace(
            get_persona=lambda pid: copy.deepcopy(records.get(pid)),
            dataset_source=lambda dataset: "store",
            get_route_policy_for_persona=lambda pid: {},
            get_capability_snapshot_for_persona=lambda pid: {},
        )

    monkeypatch.setattr(personas_service, "_get_active_read_store", _discovery_after_gate)
    monkeypatch.setattr(personas_service, "_load_strategy_seed_match_candidates", lambda **kw: ([], {"status": "ok"}))
    monkeypatch.setattr(personas_service, "_list_strategy_spec_match_candidates", lambda **kw: ([], {"status": "ok"}))
    monkeypatch.setattr(personas_service, "_dataset_surface_status", lambda *a, **kw: {"status": "ok"})

    # Trade journal owners.
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry.test")
    monkeypatch.setenv("PERSONA_URL", "http://persona.test")
    monkeypatch.setattr(trade_journal, "bound_tenant", lambda payload, tenant, auth: TENANT_ID)

    def _owner_http(url: str, data: Any, headers: Dict[str, str], method: str = "POST") -> Tuple[int, Dict[str, Any]]:
        pid = url.split("persona_id=")[-1].split("&")[0] if "persona_id=" in url else url.split("/api/personas/")[-1].split("/")[0]
        if url.startswith("http://telemetry.test"):
            counts["telemetry"] += 1
            if "/trade-episodes/ep-1" in url:
                return 404, {}
            return 200, {"projections": [], "next_cursor": None, "count": 0}
        counts["persona_owner"] += 1
        return 200, {"data": [], "meta": {"tenant_id": TENANT_ID, "persona_id": pid}}

    monkeypatch.setattr(trade_journal, "_http_call", _owner_http)

    utc_now = lambda: "2026-11-28T00:00:00Z"  # noqa: E731
    ctx = PersonaRouteContext(
        service=service,
        read_store=None,
        command_store=None,
        ranking_write_owner=None,
        write_owner=None,
        extract_identity=_identity_for_header,
        require_read_role=lambda _identity: None,
        require_operator_role=lambda _identity: None,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=lambda items, token, size: (items[int(token or 0): int(token or 0) + size], None),
        snapshot_meta=lambda ts: {"snapshot_at": ts},
        dataset_surface_status=lambda *a, **kw: {"status": "ok"},
        read_surface_meta=lambda _ds, _surface, *, snapshot_at, total=None, **kw: {"status": "ok", "snapshot_at": snapshot_at},
        raise_if_read_surface_unavailable=lambda *a, **kw: None,
        reject_body_idempotency_key=lambda _p: None,
        resolve_final_idempotency_key=lambda k: str(k or "idem-1"),
        submit_persona_action=None,
    )
    app = FastAPI()
    app.include_router(build_detail_router(ctx))
    app.include_router(build_lifecycle_router(ctx))
    # No hook passed: trade_journal uses its default owner-rule check, as core/app_factory.py does.
    app.include_router(
        trade_journal.create_trade_journal_router(
            extract_identity=_identity_for_header,
            require_read_role=lambda _identity: None,
            require_operator_role=lambda _identity: None,
        )
    )
    return TestClient(app), counts


def _get(client: TestClient, path: str, persona_id: str, viewer: str):
    return client.get(
        path.format(pid=persona_id),
        headers={"Authorization": f"Bearer {viewer}", "X-Tenant-Id": TENANT_ID},
    )


def _missing_persona_body(persona_id: str) -> Dict[str, Any]:
    error = _bff_error(
        404, ErrorCode.RESOURCE_NOT_FOUND, "Persona not found", f"Persona {persona_id} does not exist"
    )
    return {"detail": jsonable_encoder(error.detail)}


@pytest.mark.parametrize("path,bucket", SUBROUTES)
def test_non_owner_gets_missing_persona_404_and_no_owner_call(harness, path: str, bucket: str) -> None:
    client, counts = harness
    response = _get(client, path, SERVANT_OPERATOR_A, ADMIN_TEST_USER)
    assert response.status_code == 404, response.text
    assert response.json() == _missing_persona_body(SERVANT_OPERATOR_A)
    assert {b: counts[b] for b in OWNER_BUCKETS} == {b: 0 for b in OWNER_BUCKETS}
    assert counts["scope_resolutions"] == 1


@pytest.mark.parametrize("path,bucket", SUBROUTES[:11])
def test_non_owner_404_is_identical_to_missing_persona_on_persona_routes(harness, path: str, bucket: str) -> None:
    client, _counts = harness
    hidden = _get(client, path, SERVANT_OPERATOR_A, ADMIN_TEST_USER)
    missing = _get(client, path, MISSING, ADMIN_TEST_USER)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == _missing_persona_body(SERVANT_OPERATOR_A)
    assert missing.json() == _missing_persona_body(MISSING)


@pytest.mark.parametrize("path,bucket", SUBROUTES)
def test_owner_still_answered(harness, path: str, bucket: str) -> None:
    client, counts = harness
    response = _get(client, path, SERVANT_ADMIN_TEST, ADMIN_TEST_USER)
    expected = 404 if path.endswith("/trade-journal/ep-1") else 200  # telemetry owner says no such episode
    assert response.status_code == expected, response.text
    if expected == 404:
        assert response.json()["error"]["message"] == "telemetry owner rejected the read"
    assert counts[bucket] >= 1
    assert counts["scope_resolutions"] == 1


@pytest.mark.parametrize("persona_id", [TRADER, SERVANT_UNSCOPED])
@pytest.mark.parametrize("path,bucket", SUBROUTES)
def test_unscoped_and_trading_personas_unchanged_without_scope_resolution(
    harness, path: str, bucket: str, persona_id: str
) -> None:
    client, counts = harness
    response = _get(client, path, persona_id, ADMIN_TEST_USER)
    expected = 404 if path.endswith("/trade-journal/ep-1") else 200
    assert response.status_code == expected, response.text
    assert counts[bucket] >= 1
    assert counts["scope_resolutions"] == 0


@pytest.mark.parametrize(
    "path", ["/bff/personas/{pid}/trade-journal", "/bff/personas/{pid}/trade-reflections"]
)
def test_trade_journal_missing_persona_keeps_owner_answer(harness, path: str) -> None:
    client, counts = harness
    response = _get(client, path, MISSING, ADMIN_TEST_USER)
    assert response.status_code == 200, response.text
    assert counts["telemetry"] + counts["persona_owner"] == 1
    assert counts["scope_resolutions"] == 0


def test_read_check_reuses_shared_visibility_rule_and_write_routes_keep_existence_check() -> None:
    check = inspect.getsource(personas_service._ensure_persona_exists)
    assert "_persona_fleet_record_visible_to" in check
    assert "viewer_user_id is None" in check  # write routes pass no viewer: existence only
    assert "_fleet_viewer_user_id" in inspect.getsource(detail_routes._lazy_viewer_user_id)

    detail_src = inspect.getsource(detail_routes)
    assert detail_src.count("_ensure_persona_exists(persona_id, viewer_user_id=_lazy_viewer_user_id(identity, utc_now))") == 9

    lifecycle_src = inspect.getsource(lifecycle_routes)
    # Publish and test-prompt writes keep the bare existence check; only publish-history passes a viewer.
    assert lifecycle_src.count("_ensure_persona_exists, persona_id, tenant_id)") == 2  # publish + service reader
    assert lifecycle_src.count("        _ensure_persona_exists(persona_id)\n") == 1
    assert lifecycle_src.count("viewer_user_id=_lazy_viewer_user_id(identity, utc_now, tenant_id)") == 1
    discovery = inspect.getsource(personas_service._persona_strategy_discovery_payload)
    assert "_ensure_persona_exists(persona_id, viewer_user_id=viewer_user_id)" in discovery
    # POST strategy-discovery passes no viewer.
    start_src = inspect.getsource(lifecycle_routes.build_lifecycle_router)
    assert start_src.count("viewer_user_id=_lazy_viewer_user_id(identity, utc_now)") == 1


# --- Registry outage: the journal check fails closed (real PersonaFleetPort, counted transport) ---

JOURNAL_ROUTES = [path for path, _ in SUBROUTES[11:]]


class _CountedRegistry:
    """Persona registry transport behind the real PersonaFleetPort; counts list calls."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self.records = records
        self.calls = 0
        self.fail = False
        self.unconfigured = False

    def list_personas(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        self.calls += 1
        if self.unconfigured:
            from services.control_plane.bff.ports import PersonaWriteOwnerUnavailable

            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner", "Persona service URL is not configured"
            )
        if self.fail:
            raise RuntimeError("persona registry down")
        return copy.deepcopy(self.records)


@pytest.fixture()
def journal_harness(monkeypatch: pytest.MonkeyPatch):
    from services.control_plane.bff.ports.persona_capital_runtime import (
        PersonaFleetPort,
        create_persona_capital_runtime_port,
    )
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

    registry = _CountedRegistry([_servant(SERVANT_OPERATOR_A, OPERATOR_A_USER), _servant(SERVANT_ADMIN_TEST, ADMIN_TEST_USER)])
    ports = ReadSurfacePorts(
        persona_capital_runtime=create_persona_capital_runtime_port(persona_port=PersonaFleetPort(store=registry))
    )
    monkeypatch.setattr(personas_service, "_get_active_read_store", lambda explicit=None: explicit or ports)
    # Empty provisioning ledger: the registry is the only directory source.
    monkeypatch.setattr(
        personas_service, "_persona_provisioning_store", lambda: SimpleNamespace(list_for_directory=lambda tenant: [])
    )
    monkeypatch.setattr(detail_routes, "_bff_me_tenant_payload", lambda identity, requested_tenant=None: {"id": TENANT_ID})
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry.test")
    monkeypatch.setenv("PERSONA_URL", "http://persona.test")
    monkeypatch.setattr(trade_journal, "bound_tenant", lambda payload, tenant, auth: TENANT_ID)
    owner_calls = {"n": 0}

    def _owner_http(url: str, data: Any, headers: Dict[str, str], method: str = "POST") -> Tuple[int, Dict[str, Any]]:
        owner_calls["n"] += 1
        if url.startswith("http://telemetry.test"):
            return 200, {"projections": [], "next_cursor": None, "count": 0, "projection": {}}
        return 200, {"data": [], "meta": {"tenant_id": TENANT_ID}}

    monkeypatch.setattr(trade_journal, "_http_call", _owner_http)
    app = FastAPI()
    app.include_router(
        trade_journal.create_trade_journal_router(
            extract_identity=_identity_for_header,
            require_read_role=lambda _identity: None,
            require_operator_role=lambda _identity: None,
        )
    )
    return TestClient(app), registry, owner_calls


@pytest.mark.parametrize("path", JOURNAL_ROUTES)
def test_journal_non_owner_404_with_one_registry_call(journal_harness, path: str) -> None:
    client, registry, owner_calls = journal_harness
    response = _get(client, path, SERVANT_OPERATOR_A, ADMIN_TEST_USER)
    assert response.status_code == 404, response.text
    assert owner_calls["n"] == 0
    assert registry.calls == 1  # snapshot's two list_personas reads share one owner call


@pytest.mark.parametrize("persona_id", [SERVANT_OPERATOR_A, MISSING])
@pytest.mark.parametrize("path", JOURNAL_ROUTES)
def test_journal_fails_closed_when_registry_unavailable(journal_harness, path: str, persona_id: str) -> None:
    client, registry, owner_calls = journal_harness
    registry.fail = True
    response = _get(client, path, persona_id, ADMIN_TEST_USER)
    assert response.status_code == 503, response.text
    assert owner_calls["n"] == 0
    assert registry.calls == 1


@pytest.mark.parametrize("persona_id", [SERVANT_OPERATOR_A, MISSING])
@pytest.mark.parametrize("path", JOURNAL_ROUTES)
def test_journal_fails_closed_when_registry_missing_or_unconfigured(journal_harness, path: str, persona_id: str) -> None:
    # Real PersonaFleetPort maps a "not configured" owner to source="missing" with no records.
    client, registry, owner_calls = journal_harness
    registry.unconfigured = True
    response = _get(client, path, persona_id, ADMIN_TEST_USER)
    assert response.status_code == 503, response.text
    assert owner_calls["n"] == 0
    assert registry.calls == 1


@pytest.mark.parametrize("path", JOURNAL_ROUTES)
def test_journal_fails_closed_without_registry_status_capability(journal_harness, monkeypatch, path: str) -> None:
    client, registry, owner_calls = journal_harness
    bare = SimpleNamespace(list_personas=lambda **_kw: [])
    monkeypatch.setattr(personas_service, "_get_active_read_store", lambda explicit=None: explicit or bare)
    response = _get(client, path, SERVANT_OPERATOR_A, ADMIN_TEST_USER)
    assert response.status_code == 503, response.text
    assert owner_calls["n"] == 0


@pytest.mark.parametrize("path", JOURNAL_ROUTES)
def test_journal_owner_and_absent_persona_keep_owner_answer_with_healthy_registry(journal_harness, path: str) -> None:
    client, registry, owner_calls = journal_harness
    expected = 404 if path.endswith("/trade-journal/ep-1") else 200  # owner's own episode answer
    assert _get(client, path, SERVANT_ADMIN_TEST, ADMIN_TEST_USER).status_code == expected
    assert _get(client, path, MISSING, ADMIN_TEST_USER).status_code == expected
    assert owner_calls["n"] == 2
    assert registry.calls == 2  # one per request
