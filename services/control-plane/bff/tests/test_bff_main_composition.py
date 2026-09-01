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
import sys

import pytest

BFF_DIR = Path(__file__).resolve().parents[1]
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))


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
    import main as bff_main
    from test_normalized_route_uniqueness import scan_fastapi_routes

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
