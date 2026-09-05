"""Permanent architecture gate: Backend composition boundaries and dependency direction.

ACG-00-BE / ACG-01-013:
- Enforces backend request flow: FastAPI composition root -> domain routers -> domain ports / services.
- Asserts domain router factory contract: routers receive injected dependencies via factory functions.
- Asserts storage layers (read_store, domain stores) do not import router modules for business logic.
- Asserts product BFF modules physically exclude development tooling (.orchestrator, development_bridge).
- Asserts no direct runtime route table mutation in domain modules.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Set, Tuple

import pytest
from fastapi import APIRouter


BFF_DIR = Path(__file__).resolve().parent
REPO_ROOT = BFF_DIR.parents[2]


def _find_bff_product_files() -> List[Path]:
    """Find all non-test Python files in the BFF service directory."""
    return sorted([
        p for p in BFF_DIR.glob("**/*.py")
        if "test" not in p.name and "tests" not in p.parts
    ])


def _find_bff_store_files() -> List[Path]:
    """Find all store-related Python files in the BFF service directory."""
    return sorted([
        p for p in _find_bff_product_files()
        if "store" in p.name.lower()
    ])


def test_domain_router_factory_contracts() -> None:
    """Verify that domain routers expose factory functions with dependency injection."""
    from services.control_plane.bff.agora.router import create_agora_router
    from services.control_plane.bff.console_gap.alpha_factory import create_alpha_factory_router
    from services.control_plane.bff.console_gap.consult_rules import create_consult_rules_router
    from services.control_plane.bff.console_gap.datasources import create_datasources_router
    from services.control_plane.bff.console_gap.knowledge import create_knowledge_router
    from services.control_plane.bff.console_gap.lineage import create_lineage_router
    from services.control_plane.bff.console_gap.memory_governance import create_memory_governance_router
    from services.control_plane.bff.console_gap.permissions import create_permissions_router
    from services.control_plane.bff.console_gap.route_policies import create_route_policies_router
    from services.control_plane.bff.console_gap.workflows_hooks import create_workflows_hooks_router
    from services.control_plane.bff.management_read_models import create_management_read_models_router
    from services.control_plane.bff.trade_journal import create_trade_journal_router
    from services.control_plane.bff.trade_journeys import create_trade_journeys_router

    router_factories: List[Tuple[str, Callable[..., APIRouter]]] = [
        ("create_permissions_router", create_permissions_router),
        ("create_memory_governance_router", create_memory_governance_router),
        ("create_consult_rules_router", create_consult_rules_router),
        ("create_route_policies_router", create_route_policies_router),
        ("create_workflows_hooks_router", create_workflows_hooks_router),
        ("create_datasources_router", create_datasources_router),
        ("create_management_read_models_router", create_management_read_models_router),
        ("create_knowledge_router", create_knowledge_router),
        ("create_trade_journal_router", create_trade_journal_router),
        ("create_trade_journeys_router", create_trade_journeys_router),
        ("create_lineage_router", create_lineage_router),
        ("create_alpha_factory_router", create_alpha_factory_router),
        ("create_agora_router", create_agora_router),
    ]

    for name, factory in router_factories:
        assert callable(factory), f"Router factory '{name}' must be callable"
        sig = inspect.signature(factory)
        # Factory functions must accept dependency injection arguments
        assert len(sig.parameters) > 0, f"Router factory '{name}' must accept injected dependencies"


def test_store_modules_do_not_import_routers_for_business_logic() -> None:
    """Storage layers must not import router modules for business logic."""
    store_files = _find_bff_store_files()
    assert len(store_files) > 0, "Expected to find store files in BFF"

    violations: List[Tuple[str, str]] = []
    for sf in store_files:
        try:
            tree = ast.parse(sf.read_text(encoding="utf-8"), filename=str(sf))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "router" in alias.name:
                        violations.append((str(sf.relative_to(BFF_DIR)), alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and "router" in node.module:
                    violations.append((str(sf.relative_to(BFF_DIR)), node.module))

    assert not violations, (
        f"Storage layer modules must not import router modules for business logic:\n"
        + "\n".join(f"  {f} imports {imp}" for f, imp in violations)
    )


def test_product_bff_physically_excludes_development_tooling() -> None:
    """Product BFF modules must not import development tooling."""
    product_files = _find_bff_product_files()
    assert len(product_files) > 0

    forbidden_prefixes = (
        ".orchestrator",
        "orchestrator",
        "development_bridge",
        "assistant.dev_bridge",
        "assistant.dev_docs",
        "assistant.repair_receipts",
        "assistant.orchestrator_status",
    )

    violations: List[Tuple[str, str]] = []
    for pf in product_files:
        try:
            tree = ast.parse(pf.read_text(encoding="utf-8"), filename=str(pf))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        violations.append((str(pf.relative_to(BFF_DIR)), alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith(forbidden_prefixes):
                    violations.append((str(pf.relative_to(BFF_DIR)), node.module))

    assert not violations, (
        f"Product BFF modules must not import development tooling:\n"
        + "\n".join(f"  {f} imports {imp}" for f, imp in violations)
    )


def test_no_direct_route_pruning_in_domain_routers() -> None:
    """Domain router modules must not directly mutate app.router.routes."""
    router_files = sorted([
        p for p in _find_bff_product_files()
        if "router" in p.name.lower()
    ])
    assert len(router_files) > 0

    violations: List[Tuple[str, int]] = []
    for rf in router_files:
        text = rf.read_text(encoding="utf-8")
        if "app.router.routes" in text or "app.routes" in text:
            for idx, line in enumerate(text.splitlines(), start=1):
                if "app.router.routes" in line or ("app.routes" in line and "=" in line):
                    violations.append((str(rf.relative_to(BFF_DIR)), idx))

    assert not violations, (
        f"Domain router modules must not mutate app.router.routes:\n"
        + "\n".join(f"  {f}:{line}" for f, line in violations)
    )


def test_composition_boundary_contract_invariants() -> None:
    """Verify that domain router packages are decoupled and independently constructible."""
    from services.control_plane.bff.console_gap.permissions import create_permissions_router
    from services.control_plane.bff.console_gap.memory_governance import create_memory_governance_router

    mock_store = lambda: None
    mock_identity = lambda auth: None
    mock_role = lambda ident: None

    perm_router = create_permissions_router(
        get_read_store=mock_store,
        extract_identity=mock_identity,
        require_read_role=mock_role,
    )
    assert isinstance(perm_router, APIRouter)
    assert len(perm_router.routes) > 0

    mem_router = create_memory_governance_router(
        get_read_store=mock_store,
        extract_identity=mock_identity,
        require_read_role=mock_role,
    )
    assert isinstance(mem_router, APIRouter)
    assert len(mem_router.routes) > 0


def test_product_aggregate_ownership_inventory_contract() -> None:
    """SD §3.1 / STRUCT-OWNERSHIP-001: Enforce product aggregate ownership invariants."""
    from scripts.check_product_ownership import (
        DEFAULT_MANIFEST,
        load_ownership_manifest,
        validate_aggregates,
    )

    assert DEFAULT_MANIFEST.is_file(), f"Product ownership manifest missing: {DEFAULT_MANIFEST}"
    manifest = load_ownership_manifest(DEFAULT_MANIFEST)
    errors = validate_aggregates(manifest)
    assert not errors, (
        f"Product aggregate ownership registry failed validation:\n"
        + "\n".join(f"  - {err}" for err in errors)
    )
    aggregates = manifest["aggregates"]
    assert len(aggregates) >= 20, f"Expected at least 20 aggregates, found {len(aggregates)}"


def test_mutation_route_ownership_mapping_contract() -> None:
    """SD §3.2 / STRUCT-OWNERSHIP-001: Every mounted non-GET route maps to exactly one owner."""
    from scripts.check_product_ownership import (
        DEFAULT_MANIFEST,
        load_ownership_manifest,
        validate_mutation_routes,
    )

    manifest = load_ownership_manifest(DEFAULT_MANIFEST)
    errors = validate_mutation_routes(manifest)
    assert not errors, (
        f"Mounted mutation routes failed ownership validation:\n"
        + "\n".join(f"  - {err}" for err in errors)
    )


def test_worker_ownership_and_partition_invariants() -> None:
    """SD §3.3 / STRUCT-OWNERSHIP-001: Compose workers have unique leases and partition policies."""
    from scripts.check_product_ownership import (
        DEFAULT_COMPOSE,
        DEFAULT_MANIFEST,
        load_ownership_manifest,
        validate_worker_ownership,
    )

    manifest = load_ownership_manifest(DEFAULT_MANIFEST)
    errors = validate_worker_ownership(manifest, compose_path=DEFAULT_COMPOSE)
    assert not errors, (
        f"Worker ownership inventory failed validation:\n"
        + "\n".join(f"  - {err}" for err in errors)
    )


def test_symbol_dispositions_inventory_contract() -> None:
    """SD §3.4 / STRUCT-OWNERSHIP-001: Classify all 208 duplicate groups and 17 unreachable tails."""
    from scripts.check_product_ownership import (
        DEFAULT_MANIFEST,
        load_ownership_manifest,
        validate_symbol_dispositions,
    )

    manifest = load_ownership_manifest(DEFAULT_MANIFEST)
    errors = validate_symbol_dispositions(manifest)
    assert not errors, (
        f"Symbol disposition inventory failed validation:\n"
        + "\n".join(f"  - {err}" for err in errors)
    )

