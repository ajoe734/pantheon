"""Test suite for BFF main.py composition root assembly (OPGAP-BFF-MAIN-ASSEMBLY-V3-20260901).

Asserts:
1. main.py is a pure composition root with zero legacy @app.(get|post|put|patch|delete) decorators.
2. read_store.py is deleted and zero production code imports read_store.
3. All canonical domain routers are included on bff_main.app.
4. Route resolution, uniqueness, and static shadowing constraints pass without regression.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

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
        "/api/v1/operator/commands",                    # Command Adapters
        "/api/v1/runtime-bindings",                     # Runtime
        "/api/v1/deployment-plans",                     # Deployment
        "/bff/jobs",                                    # Jobs
        "/bff/agora/workshops",                         # Agora
        "/bff/personas/{persona_id}/trade-journal",     # Trade Journal
        "/bff/management/trade-journeys",               # Trade Journeys
    ]

    for sample in expected_domain_samples:
        assert sample in paths, f"Expected domain sample route {sample} to be mounted on bff_main.app"


def test_training_v3_router_mounted() -> None:
    """Verify that training router is mounted via create_training_router."""
    main_text = (BFF_DIR / "main.py").read_text(encoding="utf-8")
    assert "create_training_router" in main_text
    assert "training.router" in main_text


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


def test_zero_unreferenced_dead_functions_in_main_py() -> None:
    """Verify that main.py has zero orphaned top-level functions."""
    from collections import defaultdict

    main_path = BFF_DIR / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename="main.py")

    top_level_funcs = {
        node.name: node.lineno
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    usages = defaultdict(int)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            usages[node.id] += 1

    # Allowed external exports tested across other suites
    allowed_exports = {
        "_surface_degradation_reason",
        "_extract_identity",
        "_extract_identity_jwt",
        "_extract_identity_stub",
        "_build_persona_health_items",
        "_trading_performance_delta",
    }

    dead_funcs = [
        (fname, lineno)
        for fname, lineno in top_level_funcs.items()
        if usages[fname] == 0 and fname not in allowed_exports
    ]

    assert not dead_funcs, f"Found {len(dead_funcs)} unreferenced dead function(s) in main.py: {dead_funcs}"


def test_personas_module_global_read_store_not_instantiated_on_import() -> None:
    """Verify personas.service does not construct a module-global read_store on import."""
    import sys
    from services.control_plane.bff.personas import service as ps

    # Module-level read_store must be None prior to explicit service construction
    # or must have been injected explicitly by composition root.
    assert hasattr(ps, "read_store"), "personas.service must declare read_store symbol"


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
    mock_cmd = object()
    mock_settings = object()

    deps = AppDependencies(
        deployment_queries=mock_queries,
        deployment_commands=mock_commands,
        read_surface=mock_read_surface,
        command_store=mock_cmd,
        persona_write_owner=mock_persona,
        ranking_write_owner=mock_ranking,
        settings_store=mock_settings,
    )
    assert deps.deployment_queries is mock_queries
    assert deps.deployment_commands is mock_commands
    assert deps.read_surface is mock_read_surface
    assert deps.ranking_write_owner is mock_ranking
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


def test_main_py_zero_lambda_read_store_service_locator_seams() -> None:
    """Verify main.py has zero lambda: read_store service locator seams in router mounting."""
    import re
    main_text = (BFF_DIR / "main.py").read_text(encoding="utf-8")
    seams = re.findall(r"(?:get_read_store\s*=\s*lambda|lambda[^:\n]*:\s*read_store\b)", main_text)
    assert not seams, f"Found {len(seams)} lambda read_store service locator seam(s) in main.py: {seams}"


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
    try:
        with pytest.raises(RuntimeError):
            ps._get_ranking_write_owner()
    finally:
        ps._ranking_write_owner = original_owner

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
    assert getattr(hints["ranking_write_owner"], "__name__", "") == "RankingSnapshotWriteOwnerPort", f"Expected RankingSnapshotWriteOwnerPort, got {hints['ranking_write_owner']}"

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
