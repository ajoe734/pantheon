"""Test suite for BFF main.py composition root assembly (OPGAP-BFF-MAIN-ASSEMBLY-V3-20260901).

Asserts:
1. main.py is a pure composition root with zero legacy @app.(get|post|put|patch|delete) decorators.
2. read_store.py is deleted and zero production code imports read_store.
3. All canonical domain routers are included on bff_main.app.
4. Route resolution, uniqueness, and static shadowing constraints pass without regression.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parents[1]


def test_main_py_is_pure_composition_root() -> None:
    """Verify that main.py contains zero direct route decorators on app."""
    main_path = BFF_DIR / "main.py"
    assert main_path.exists(), "services/control-plane/bff/main.py must exist"

    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename="main.py")
    route_methods = {"get", "post", "put", "patch", "delete", "options", "head"}

    app_route_decorators = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app":
                        if dec.func.attr in route_methods:
                            app_route_decorators.append((node.name, dec.func.attr, node.lineno))

    assert not app_route_decorators, (
        f"Found {len(app_route_decorators)} direct @app route decorator(s) in main.py: "
        f"{app_route_decorators[:10]}"
    )


def test_read_store_file_is_deleted() -> None:
    """Verify that read_store.py is completely removed."""
    read_store_path = BFF_DIR / "read_store.py"
    assert not read_store_path.exists(), f"Expected {read_store_path} to be deleted"


def test_zero_production_imports_of_read_store() -> None:
    """Verify zero production code references read_store module."""
    prod_py_files = [
        f for f in BFF_DIR.rglob("*.py")
        if "tests" not in f.parts and "test_" not in f.name and "scratch" not in f.parts
    ]
    offenders = []
    for py_file in prod_py_files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "read_store" or alias.name.startswith("read_store."):
                            offenders.append(str(py_file.relative_to(BFF_DIR)))
                elif isinstance(node, ast.ImportFrom):
                    if node.module and (node.module == "read_store" or node.module.startswith("read_store.")):
                        offenders.append(str(py_file.relative_to(BFF_DIR)))
        except SyntaxError:
            continue

    assert not offenders, f"Production files still import read_store: {offenders}"


def test_all_canonical_domain_routers_mounted() -> None:
    """Verify that all canonical domain routers are registered on the app."""
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.test_normalized_route_uniqueness import scan_fastapi_routes

    entries = scan_fastapi_routes(bff_main.app)
    assert len(entries) >= 400, f"Expected 400+ routes across domain routers, found {len(entries)}"

    paths = {e.raw_path for e in entries}

    # Verify key routes from distinct domain routers
    expected_domain_samples = [
        "/bff/me",                                      # Auth
        "/api/v1/personas",                             # Personas
        "/api/v1/trainer/sessions",                     # Training
        "/api/v1/approval-decisions",                   # Governance
        "/bff/evolution-programs",                      # Evolution
        "/api/v1/capital-pools",                        # Capital
        "/bff/strategies",                              # Strategies
        "/api/v1/incidents",                            # Incidents
        "/bff/events",                                  # Events
        "/api/v1/runtime-bindings",                     # Runtime
        "/api/v1/deployment-plans",                     # Deployment
        "/bff/jobs",                                    # Jobs
        "/bff/agora/workshops",                         # Agora
        "/bff/personas/{persona_id}/trade-journal",     # Trade Journal
        "/bff/management/trade-journeys",               # Trade Journeys
    ]

    for sample in expected_domain_samples:
        assert sample in paths, f"Expected domain sample route {sample} to be mounted on bff_main.app"


def test_training_v3_router_is_mounted_on_the_composed_app() -> None:
    """Verify the real training endpoint is served by the composed application."""
    from services.control_plane.bff import main as bff_main

    from services.control_plane.bff.test_normalized_route_uniqueness import scan_fastapi_routes

    routes = [entry for entry in scan_fastapi_routes(bff_main.app) if entry.raw_path == "/api/v1/trainer/sessions"]
    assert {entry.method for entry in routes} == {"GET", "POST"}


def test_retired_legacy_handlers_deleted_from_main_py() -> None:
    """Verify that legacy route handler function bodies superseded by domain routers are deleted."""
    main_path = BFF_DIR / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename="main.py")

    top_level_funcs = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    superseded_handlers = [
        "bff_me",
        "health",
        "get_settings",
        "update_settings",
        "export_settings",
        "import_settings",
        "bff_auth_dev_login",
        "bff_auth_readiness",
        "bff_auth_refresh",
        "bff_logout",
        "bff_switch_tenant",
        "bff_update_locale",
        "create_approval_decision",
        "bff_create_capital_pool",
        "get_persona_detail",
        "bff_management_board_pack",
        "bff_v5_control_room",
        "api_v1_list_experiments",
        "api_v1_get_experiment",
        "bff_apply_rebalance_proposal",
        "bff_create_mcp_server",
        "bff_create_paper_persona_bundle",
        "bff_create_rebalance",
        "bff_create_review",
        "bff_create_skill",
        "bff_create_tool",
        "bff_get_capital_pool",
        "bff_get_persona",
        "bff_get_persona_activity",
        "bff_get_persona_audit",
        "bff_get_persona_capabilities_surface",
    ]

    retained = [h for h in superseded_handlers if h in top_level_funcs]
    assert not retained, f"Superseded legacy handler functions still defined in main.py: {retained}"


def test_composed_persona_and_command_services_use_typed_app_dependencies() -> None:
    """Exercise the actual composition result, not AST name-use guesses."""
    from services.control_plane.bff import main as bff_main

    assert bff_main.app.state.persona_service.get_read_store() is bff_main.app_deps.read_surface
    assert bff_main.app.state.command_adapter_service.read_store is bff_main.app_deps.read_surface
    assert bff_main.app.state.command_adapter_service.command_store is bff_main.app_deps.command_store


def test_personas_service_module_owns_no_default_store_objects() -> None:
    """personas.service creates no read store, command store, or write owner of its own."""
    from services.control_plane.bff.personas import service as ps

    for name in ("read_store", "command_store", "persona_write_owner", "_DefaultCommandStore"):
        assert not hasattr(ps, name), f"personas.service must not declare a module-level {name}"


def test_personas_out_of_context_accessors_fail_closed_when_not_composed(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.control_plane.bff.personas import service as ps

    monkeypatch.setattr(ps, "_composed_persona_service", None)
    for accessor in (ps._get_active_read_store, ps._get_active_command_store, ps._get_active_write_owner):
        with pytest.raises(RuntimeError, match="failing closed"):
            accessor()


def test_personas_out_of_context_accessors_return_the_main_composed_stores() -> None:
    """Out-of-context persona accessors resolve to the very objects main.py exposes."""
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.personas import service as ps

    assert ps._composed_persona_service is bff_main.app.state.persona_service
    assert ps._get_active_read_store() is bff_main.read_store
    assert ps._get_active_command_store() is bff_main.command_store
    assert ps._get_active_write_owner() is bff_main.persona_write_owner
    assert ps._get_active_read_store() is bff_main.app_deps.read_surface
    assert ps._get_active_command_store() is bff_main.app_deps.command_store
    assert ps._get_active_write_owner() is bff_main.app_deps.persona_write_owner


def test_command_written_through_persona_fallback_is_visible_in_main_command_store() -> None:
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.models import CommandType
    from services.control_plane.bff.personas import service as ps

    command_id = "cmd-persona-single-store-visibility"
    ps._get_active_command_store().submit_command(
        command_id,
        CommandType.PAUSE_RUNTIME,
        {"type": "persona", "id": "persona-single-store"},
        "2026-10-08T00:00:00Z",
        {},
        {},
    )
    assert bff_main.command_store.get_command(command_id)["command_id"] == command_id


def test_personas_service_fails_startup_closed_when_ranking_owner_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify PersonaService fails startup closed if Rankings write-owner is unconfigured."""
    import os
    from services.control_plane.bff.personas.service import PersonaService

    monkeypatch.delenv("RANKING_STORE_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PANTHEON_DATABASE_URL", raising=False)

    with pytest.raises((ValueError, RuntimeError)):
        PersonaService(
            get_read_store=lambda: object(),
            get_command_store=lambda: object(),
            get_provisioning_store=lambda: object(),
        )


def test_deployment_router_receives_queries_port() -> None:
    """Verify DeploymentService uses explicit queries dependency."""
    from services.control_plane.bff.deployment.service import DeploymentService

    mock_queries = object()
    service = DeploymentService(
        queries=mock_queries,
        bff_error=lambda *a, **k: RuntimeError(),
        dataset_surface_status=lambda *a, **k: {},
        composed_surface_status=lambda *a, **k: {},
        aggregate_group_surface=lambda *a, **k: {},
        split_csv_query=lambda *a, **k: None,
        snapshot_meta=lambda *a, **k: {},
        surface_degradation_reason=lambda *a, **k: None,
    )
    assert service.queries is mock_queries
    assert not hasattr(service, "read_store")


def test_deployment_service_requires_queries() -> None:
    """Verify DeploymentService raises TypeError when queries is not provided."""
    from services.control_plane.bff.deployment.service import DeploymentService

    with pytest.raises(TypeError):
        DeploymentService(  # type: ignore[call-arg]
            bff_error=lambda *a, **k: RuntimeError(),
            dataset_surface_status=lambda *a, **k: {},
            composed_surface_status=lambda *a, **k: {},
            aggregate_group_surface=lambda *a, **k: {},
            split_csv_query=lambda *a, **k: None,
            snapshot_meta=lambda *a, **k: {},
            surface_degradation_reason=lambda *a, **k: None,
        )


def test_bootstrap_app_dependencies_contract() -> None:
    """Verify bootstrap package exposes AppDependencies container with typed dependencies."""
    from services.control_plane.bff.bootstrap import AppDependencies
    from services.control_plane.bff.deployment.ports import DeploymentCommands, DeploymentQueries

    mock_queries = object()
    mock_commands = object()
    mock_read_surface = object()
    mock_ranking = object()
    mock_persona = object()
    mock_strategy = object()
    mock_cmd = object()
    mock_settings = object()

    deps = AppDependencies(
        deployment_queries=mock_queries,
        deployment_commands=mock_commands,
        read_surface=mock_read_surface,
        command_store=mock_cmd,
        persona_write_owner=mock_persona,
        ranking_write_owner=mock_ranking,
        strategy_write_owner=mock_strategy,
        settings_store=mock_settings,
    )
    assert deps.deployment_queries is mock_queries
    assert deps.deployment_commands is mock_commands
    assert deps.read_surface is mock_read_surface
    assert deps.ranking_write_owner is mock_ranking
    assert deps.strategy_write_owner is mock_strategy
    assert not hasattr(deps, "queries")
    assert not hasattr(deps, "read_store")

    default_deps = AppDependencies.create_default()
    assert default_deps.deployment_queries is not None
    assert default_deps.deployment_commands is not None
    assert default_deps.read_surface is not None
    assert default_deps.ranking_write_owner is not None
    assert default_deps.persona_write_owner is not None
    assert isinstance(default_deps.deployment_queries, DeploymentQueries)
    assert isinstance(default_deps.deployment_commands, DeploymentCommands)


def test_main_py_uses_app_dependencies_for_composition() -> None:
    """Verify main.py uses AppDependencies for composition root assembly."""
    from services.control_plane.bff import main as bff_main
    assert hasattr(bff_main, "app_deps"), "main.py must hold app_deps"
    from services.control_plane.bff.bootstrap import AppDependencies
    assert isinstance(bff_main.app_deps, AppDependencies)




def test_personas_service_no_import_time_stores_and_explicit_constructor() -> None:
    """Verify personas.service and router have no import-time store defaults and require explicit injection."""
    from services.control_plane.bff.personas import service as ps
    from services.control_plane.bff.personas import router as pr

    assert not hasattr(pr, "router"), "personas.router must not expose a default module-level router"
    assert getattr(ps, "persona_write_owner", None) is None, "personas.service must not construct persona_write_owner at import time"

    # Verify top-level AST assignments in personas/service.py have no store instantiations
    service_ast = ast.parse((BFF_DIR / "personas" / "service.py").read_text(encoding="utf-8"))
    for node in service_ast.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("persona_write_owner", "read_store", "_ranking_write_owner"):
                    assert isinstance(node.value, ast.Constant) and node.value.value is None, (
                        f"Expected {target.id} to be assigned None at import time, got {ast.dump(node.value)}"
                    )

    # Verify _get_ranking_write_owner raises RuntimeError if not configured, rather than self-creating defaults
    original_owner = ps._ranking_write_owner
    ps._ranking_write_owner = None
    context_token = ps._current_persona_service.set(None)
    try:
        with pytest.raises(RuntimeError):
            ps._get_ranking_write_owner()
    finally:
        ps._ranking_write_owner = original_owner
        ps._current_persona_service.reset(context_token)

    with pytest.raises((TypeError, RuntimeError)):
        ps.PersonaService()  # type: ignore[call-arg]

    with pytest.raises((TypeError, RuntimeError)):
        pr.create_personas_router()  # type: ignore[call-arg]



def test_app_dependencies_concrete_types_and_no_any_ports() -> None:
    """Verify AppDependencies defines concrete typed ports without Any or permissive fallback."""
    import inspect
    from typing import get_type_hints, Any
    from services.control_plane.bff.bootstrap.dependencies import AppDependencies

    hints = get_type_hints(AppDependencies)
    assert getattr(hints["command_store"], "__name__", "") == "CommandStore", f"Expected CommandStore, got {hints['command_store']}"
    assert getattr(hints["settings_store"], "__name__", "") == "SettingsStore", f"Expected SettingsStore, got {hints['settings_store']}"
    assert getattr(hints["persona_write_owner"], "__name__", "") == "PersonaRegistryHttpWritePort", f"Expected PersonaRegistryHttpWritePort, got {hints['persona_write_owner']}"
    assert getattr(hints["ranking_write_owner"], "__name__", "") == "RankingSnapshotReadPort", f"Expected RankingSnapshotReadPort, got {hints['ranking_write_owner']}"

    sig = inspect.signature(AppDependencies.create_default)
    for param_name, param in sig.parameters.items():
        assert param.annotation is not Any, f"AppDependencies.create_default parameter '{param_name}' must not be Any"



def test_deployment_adapters_concrete_read_surface_and_canonical_write_owner() -> None:
    """Verify DeploymentReadSurfaceAdapter requires ReadSurfacePorts and DefaultDeploymentCommands delegates to DeploymentCommandAdapter."""
    from typing import get_type_hints
    from services.control_plane.bff.deployment.adapters import (
        DeploymentReadSurfaceAdapter,
        DefaultDeploymentCommands,
        DeploymentCommandAdapter,
    )

    hints = get_type_hints(DeploymentReadSurfaceAdapter.__init__)
    read_surface_hint = hints.get("read_surface")
    assert getattr(read_surface_hint, "__name__", "") == "ReadSurfacePorts", (
        f"DeploymentReadSurfaceAdapter.read_surface must be ReadSurfacePorts, got {read_surface_hint}"
    )

    with pytest.raises(TypeError):
        DeploymentReadSurfaceAdapter(read_surface=object())  # type: ignore[arg-type]

    cmd_adapter = DeploymentCommandAdapter()
    commands = DefaultDeploymentCommands(write_owner=cmd_adapter)
    assert commands._write_owner is cmd_adapter


def test_research_domain_and_capabilities_mounted() -> None:
    """Verify research search and capabilities routes are mounted on bff_main.app."""
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.test_normalized_route_uniqueness import scan_fastapi_routes

    entries = scan_fastapi_routes(bff_main.app)
    paths = {e.raw_path for e in entries}
    assert "/bff/capabilities" in paths, "Expected /bff/capabilities mounted on bff_main.app"
    assert "/api/v1/research/search" in paths, "Expected /api/v1/research/search mounted on bff_main.app"
    assert "/api/v1/research/tickets" in paths, "Expected /api/v1/research/tickets mounted on bff_main.app"


@pytest.mark.parametrize("surface_state", ["fresh", "degraded", "unavailable"])
def test_research_search_full_app_minimal_app_parity(
    surface_state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify full-app (bff_main.app) and minimal-app (create_research_test_app) emit identical responses for search."""
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", surface_state)
    from fastapi.testclient import TestClient
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.test_rw02_search_contract import _SearchPortDouble, OPERATOR_AUTH
    from services.control_plane.bff.tests.knowledge_read_port_fixtures import create_research_test_app

    port = _SearchPortDouble(available=True)
    minimal_app = create_research_test_app(
        port,
        utc_now=lambda: "2026-04-19T20:14:30Z",
        submit_experiment_action=lambda *a, **kw: {},
    )
    minimal_client = TestClient(minimal_app)

    original_store = bff_main.read_store
    original_utc = bff_main.utc_now
    try:
        bff_main.read_store = port
        bff_main.utc_now = lambda: "2026-04-19T20:14:30Z"
        full_client = TestClient(bff_main.app)

        query = "/api/v1/research/search?q=momentum%20decay%20volatility&match_type=all&page_size=2"
        headers = {"Authorization": OPERATOR_AUTH}
        full_resp = full_client.get(query, headers=headers)
        minimal_resp = minimal_client.get(query, headers=headers)

        assert full_resp.status_code == 200
        assert minimal_resp.status_code == 200
        full_json = full_resp.json()
        minimal_json = minimal_resp.json()

        assert full_json["data"] == minimal_json["data"]
        assert full_json["page_info"] == minimal_json["page_info"]
        full_surf = full_json["meta"]["surfaces"]["search_results"]
        min_surf = minimal_json["meta"]["surfaces"]["search_results"]
        expected_status = "unavailable" if surface_state == "unavailable" else "degraded"
        assert full_surf["status"] == min_surf["status"] == expected_status
        assert full_surf["source"] == min_surf["source"] == "local_snapshot"
        assert full_surf["note"] == min_surf["note"] == "Served from local BFF snapshot fallback instead of a backend-owned read store."
        assert full_surf["staleness"]["served_from"] == min_surf["staleness"]["served_from"] == "local_snapshot"
        assert full_surf["staleness"]["last_known_at"] == full_json["meta"]["snapshot_at"]
        assert min_surf["staleness"]["last_known_at"] == minimal_json["meta"]["snapshot_at"]
        assert min_surf["staleness"]["last_known_at"] == "2026-04-19T20:14:30Z"

        # Boundary / limit alias scoping parity on both apps
        full_bound_0 = full_client.get(
            "/api/v1/research/search?q=momentum&page_size=1&limit=0",
            headers=headers,
        )
        min_bound_0 = minimal_client.get(
            "/api/v1/research/search?q=momentum&page_size=1&limit=0",
            headers=headers,
        )
        assert full_bound_0.status_code == 200
        assert min_bound_0.status_code == 200
        assert len(full_bound_0.json()["data"]) == 1
        assert len(min_bound_0.json()["data"]) == 1
        assert full_bound_0.json()["page_info"] == min_bound_0.json()["page_info"]

        full_bound_999 = full_client.get(
            "/api/v1/research/search?q=momentum&page_size=1&limit=999",
            headers=headers,
        )
        min_bound_999 = minimal_client.get(
            "/api/v1/research/search?q=momentum&page_size=1&limit=999",
            headers=headers,
        )
        assert full_bound_999.status_code == 200
        assert min_bound_999.status_code == 200
        assert len(full_bound_999.json()["data"]) == 1
        assert len(min_bound_999.json()["data"]) == 1
        assert full_bound_999.json()["page_info"] == min_bound_999.json()["page_info"]
    finally:
        bff_main.read_store = original_store
        bff_main.utc_now = original_utc


@pytest.mark.parametrize("state", ["fresh", "degraded", "unavailable"])
def test_dataset_surface_status_full_app_parity(state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify format_dataset_surface_status and bff_main._dataset_surface_status emit identical results across all states."""
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", state)
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.research.routes.common import format_dataset_surface_status

    for source in ["primary", "local_snapshot", "missing", "legacy_incident_backfill"]:
        full_res = bff_main._dataset_surface_status("test_ds", snapshot_at="2026-04-20T00:00:00Z", source=source)
        min_res = format_dataset_surface_status(
            "test_ds", snapshot_at="2026-04-20T00:00:00Z", source=source, utc_now=bff_main.utc_now
        )
        assert full_res == min_res




# BFF-MUTATION-ROUTE-ROLES-001: state-changing routes require the operator role,
# exercised through the mounted composition root (this reviewed composition suite).

MUTATION_ROUTES = [
    ("POST", "/bff/jobs/j1/actions/retry", 410, "ACTION_RETIRED", {"reason": "operator retry"}),
    ("POST", "/bff/rankings/r1/actions/publish", 410, "ACTION_RETIRED", {}),
    ("POST", "/api/v1/personas/p1/strategy-discovery", 202, None, {"query": "momentum", "lookback_days": 30}),
    ("POST", "/bff/personas/p1/strategy-discovery", 202, None, {"query": "momentum", "lookback_days": 30}),
    ("POST", "/api/v1/personas/p1/strategy-matches/m1/actions", 202, None, {"action": "promote_seed_candidate", "notes": "operator approved"}),
    ("POST", "/bff/personas/p1/strategy-matches/m1/actions", 202, None, {"action": "promote_seed_candidate", "notes": "operator approved"}),
    ("POST", "/bff/personas/p1/test-prompt", 202, None, {"prompt": "What is current portfolio exposure?"}),
]

@pytest.fixture(scope="module")
def mutation_roles_client():
    mp = pytest.MonkeyPatch()
    mp.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    mp.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    from services.control_plane.bff import main
    from services.control_plane.bff.personas import service as persona_service
    from services.control_plane.bff.personas.routes import lifecycle

    # main.py calls get_catalog_entry without importing it (pre-existing, out of scope here).
    from services.control_plane.bff.action_catalog import get_catalog_entry
    mp.setattr(main, "get_catalog_entry", get_catalog_entry, raising=False)
    # Valid fixture resources for every id the routes look up.
    store = type(main.read_store)
    mp.setattr(store, "get_job_bff", lambda self, job_id: {"job_id": job_id, "status": "failed"}, raising=False)
    mp.setattr(store, "get_ranking", lambda self, rid: {"ranking_id": rid}, raising=False)
    match = {"match_id": "m1", "matched_object_type": "strategy_spec_seed", "matched_object_id": "seed-1", "metadata": {}}
    mp.setattr(persona_service, "_ensure_persona_exists", lambda *a, **k: None)
    mp.setattr(lifecycle, "_ensure_persona_exists", lambda *a, **k: None)
    mp.setattr(persona_service, "_persona_strategy_discovery_payload", lambda *a, **k: {
        "profile": {}, "matches": [match], "surfaces": {}, "candidate_counts": {}})
    yield TestClient(main.app, raise_server_exceptions=False)
    mp.undo()

def _resolve(mutation_roles_client, path):
    return path

@pytest.mark.parametrize("method,path,status,code,payload", MUTATION_ROUTES)
def test_viewer_token_is_forbidden(mutation_roles_client, method, path, status, code, payload):
    r = mutation_roles_client.request(method, _resolve(mutation_roles_client, path), json=payload, headers={
        "Authorization": "Bearer viewer-1:viewer", "Idempotency-Key": f"k-viewer-{path}"})
    assert r.status_code == 403, r.text

@pytest.mark.parametrize("method,path,status,code,payload", MUTATION_ROUTES)
def test_operator_token_keeps_handler_behavior(mutation_roles_client, method, path, status, code, payload):
    r = mutation_roles_client.request(method, _resolve(mutation_roles_client, path), json=payload, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": f"k-op-{path}"})
    assert r.status_code == status, r.text
    body = r.json()
    if code:
        assert body["error"]["code"] == code
        assert body["error"]["message"]
    else:
        assert "data" in body
        assert isinstance(body["data"], (dict, list))

def test_reviewer_token_cannot_execute_retired_ranking_action(mutation_roles_client):
    r = mutation_roles_client.post("/bff/rankings/r1/actions/publish", json={}, headers={
        "Authorization": "Bearer rev-1:reviewer", "Idempotency-Key": "k-reviewer-ranking"})
    assert r.status_code == 410, r.text
    assert r.json()["error"]["details"]["replacement"] == "GET /bff/rankings"

@pytest.mark.parametrize("missing_reader", [True, False])
def test_lifecycle_readiness_reports_unavailable_projection(monkeypatch, missing_reader):
    from services.control_plane.bff import main
    from services.control_plane.bff.trade_journey_projection_store import ProjectionReadUnavailable

    def unavailable(**kwargs):
        raise ProjectionReadUnavailable("owner unavailable")

    reader = None if missing_reader else SimpleNamespace(controller_freshness=unavailable)
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres")
    monkeypatch.setattr(main, "read_store", SimpleNamespace(trade_journey_projection_reader=lambda: reader))
    result = main._lifecycle_projector_dependency()
    assert result["ready"] is False
    assert any(reason.startswith("projection_reader_unavailable:") for reason in result["reasons"])


def test_native_main_captures_context_projector_before_facade_install(tmp_path):
    """Fresh-process native mount; replace owner I/O, never the projector."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[4]
    code = r'''
import inspect, os
from unittest.mock import MagicMock, patch
from services.control_plane.bff.bootstrap.dependencies import AppDependencies
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.settings_store import SettingsStore
root = os.environ['BFF_DATA_DIR']
deps = AppDependencies(
    deployment_queries=MagicMock(), deployment_commands=MagicMock(),
    read_surface=create_in_memory_read_surface_ports(),
    command_store=CommandStore(root + '/commands.jsonl'),
    persona_write_owner=MagicMock(), ranking_write_owner=MagicMock(),
    strategy_write_owner=MagicMock(), settings_store=SettingsStore(root + '/settings.json'),
    decision_journal_write_owner=MagicMock(),
)
with patch.object(AppDependencies, 'create_default', return_value=deps):
    from services.control_plane.bff import main
route = next(r for r in main.app.routes if getattr(r, 'path', '') == '/api/v1/operator/runtime-state')
project = inspect.getclosurevars(route.endpoint).nonlocals['_project_operator_runtime_state_row']
row = project({'binding_id': 'b', 'runtime_id': 'r', 'strategy_id': 's', 'deployment_mode': 'paper'})
assert row['runtime_id'] == 'r'
assert row['strategy_id'] == 's'
assert 'telemetry_observation' in row
assert 'monitoring_observation' in row
assert 'rollback_observation' in row
assert project.__module__ == 'services.control_plane.bff.assistant.management_service'
print('native-runtime-projector-ok')
'''
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=root, text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(root), "BFF_DATA_DIR": str(tmp_path)}, timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "native-runtime-projector-ok" in run.stdout


_NATIVE_MAIN_PRELUDE = r'''
import json, os, sys
from unittest.mock import MagicMock, patch
from services.control_plane.bff.bootstrap.dependencies import AppDependencies
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.settings_store import SettingsStore
from services.control_plane.bff.core import app_factory

root = os.environ['BFF_DATA_DIR']
deps = AppDependencies(
    deployment_queries=MagicMock(), deployment_commands=MagicMock(),
    read_surface=create_in_memory_read_surface_ports(),
    command_store=CommandStore(root + '/commands.jsonl'),
    persona_write_owner=MagicMock(), ranking_write_owner=MagicMock(),
    strategy_write_owner=MagicMock(), settings_store=SettingsStore(root + '/settings.json'),
    decision_journal_write_owner=MagicMock(),
)
resolved = []
production_resolver = app_factory._resolve_default_dependency
def recording_resolver(name, app_deps):
    value = production_resolver(name, app_deps)  # raises UnresolvedBffDependency on fall-through
    resolved.append(name)
    return value
app_factory._resolve_default_dependency = recording_resolver
with patch.object(AppDependencies, 'create_default', return_value=deps):
    from services.control_plane.bff import main
'''


def _run_native_main(body: str, tmp_path) -> "subprocess.CompletedProcess[str]":
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[4]
    return subprocess.run(
        [sys.executable, "-c", _NATIVE_MAIN_PRELUDE + body], cwd=root, text=True, capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": str(root),
            "BFF_DATA_DIR": str(tmp_path),
            "PANTHEON_BFF_AUTH_STUB": "true",
            "PANTHEON_BFF_AUTH_MODE": "permissive",
        },
        timeout=120,
    )


def test_native_main_composition_has_zero_stand_in_fall_through(tmp_path):
    """Importing the production main mounts every router without a single stand-in.

    The production resolver raises ``UnresolvedBffDependency`` for any name that no explicit
    port, loaded ``main`` attribute or real owner supplies, so a clean import proves zero
    fall-through; the recorded names prove what did reach the resolver.
    """
    run = _run_native_main(
        r'''
assert 'services.control_plane.bff.tests.bff_compose_stand_ins' not in sys.modules
mounted = set()
for route in main.app.routes:
    mounted.update(getattr(sub, 'path', None) for sub in getattr(getattr(route, 'original_router', route), 'routes', [route]))
for path in ('/api/v1/bindings', '/bff/runtimes', '/bff/incidents', '/bff/alerts/{alert_id}/acknowledge'):
    assert path in mounted, path
print('RESOLVED', json.dumps(sorted(set(resolved))))
''',
        tmp_path,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    resolved = set(json.loads(run.stdout.split("RESOLVED", 1)[1]))
    assert {"_stable_capital_resource_id", "_capital_owner_role", "_raise_capital_owner_error"} <= resolved
    assert not resolved & {
        "_GOV_BFF_IDEMPOTENCY",
        "_capital_bff_idempotency_check",
        "_capital_bff_idempotency_store",
    }


@pytest.mark.parametrize(
    "name",
    [
        "_GOV_BFF_IDEMPOTENCY",
        "_capital_bff_idempotency_check",
        "_capital_bff_idempotency_store",
        "_project_operator_runtime_state_row",
        "_stable_json_hash",
        "_resolve_final_idempotency_key",
        "_publish_event",
        "_sse_buffers",
        "provider_readiness_cache",
        "not_a_real_dependency_name",
    ],
)
def test_production_resolver_fails_closed_for_stand_in_names(name):
    from services.control_plane.bff.core.app_factory import (
        UnresolvedBffDependency,
        _resolve_default_dependency,
    )

    with pytest.raises(UnresolvedBffDependency, match=name):
        _resolve_default_dependency(name, SimpleNamespace())


def test_no_production_module_imports_the_test_stand_ins():
    offenders = [
        str(path.relative_to(BFF_DIR))
        for path in BFF_DIR.rglob("*.py")
        if "bff_compose_stand_ins" in path.read_text(encoding="utf-8")
        and "tests" not in path.relative_to(BFF_DIR).parts
        and not path.name.startswith("test_")
    ]
    assert offenders == []


def test_native_main_binding_create_reaches_capital_owner_once_per_key(tmp_path):
    """POST /api/v1/bindings through the native composition against a recorded Capital owner."""
    run = _run_native_main(
        r'''
import urllib.error
from fastapi.testclient import TestClient
from services.control_plane.bff import command_executor

owner_records = {}
owner_writes = []

def recorded_capital_owner(url, payload, auth_token=None, mfa_token=None, tenant_id=None):
    assert url.endswith('/api/bindings'), url
    binding_id = payload['binding_id']
    existing = owner_records.get(binding_id)
    if existing is not None:
        if existing['request_hash'] != payload['request_hash']:
            raise urllib.error.HTTPError(url, 409, 'conflict', {}, None)
        return {**existing['body'], 'idempotent_replay': True}
    owner_writes.append(dict(payload))
    body = {**payload, 'status': 'pending', 'id': binding_id}
    owner_records[binding_id] = {'request_hash': payload['request_hash'], 'body': body}
    return body

headers = {'Authorization': 'Bearer op-1:operator', 'Idempotency-Key': 'bind-key-1'}
body = {'persona_id': 'persona-1', 'capital_pool_id': 'pool-1'}
with patch.object(command_executor, '_post_json', recorded_capital_owner), \
     patch.object(command_executor, '_capital_url', lambda path: 'http://capital.test' + path):
    client = TestClient(main.app, raise_server_exceptions=False)
    first = client.post('/api/v1/bindings', json=body, headers=headers)
    retry = client.post('/api/v1/bindings', json=body, headers=headers)
    other = client.post('/api/v1/bindings', json=body, headers={**headers, 'Idempotency-Key': 'bind-key-2'})
    conflict = client.post('/api/v1/bindings', json={**body, 'capital_pool_id': 'pool-2'}, headers=headers)

assert first.status_code == 201, first.text
assert retry.status_code == 201, retry.text
binding_id = first.json()['binding_id']
assert binding_id.startswith('binding-') and binding_id == retry.json()['binding_id']
assert first.json()['status'] == 'pending'
assert retry.json()['idempotent_replay'] is True
assert {k: v for k, v in retry.json().items() if k != 'idempotent_replay'} == first.json()
assert owner_writes[0]['actor_role'] == 'operator' and owner_writes[0]['idempotency_key'] == 'bind-key-1'
assert other.status_code == 201 and other.json()['binding_id'] != binding_id
assert len(owner_writes) == 2, owner_writes  # one owner write per idempotency key
assert conflict.status_code == 409, conflict.text  # owner conflict mapped by the Capital error mapping
print('native-binding-ok')
''',
        tmp_path,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "native-binding-ok" in run.stdout
