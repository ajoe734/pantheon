from __future__ import annotations

import importlib

import pytest


PERSONA_MAIN_RESIDUE_NAMES = (
    "_STRATEGY_BFF_LIFECYCLE_MAP",
    "_PERSONA_OPERATIONAL_LIFECYCLE_STATES",
    "_STRATEGY_BFF_RISK_MAP",
    "_STRATEGY_PERSONA_BFF_IDEMPOTENCY",
    "_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY",
    "_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY",
    "_PERSONA_PROVISIONING_STORE",
    "_PERSONA_PROVISIONING_STORE_LOCK",
    "_PERSONA_FIRST_EVALUATION_WORKFLOW_ID",
    "_PersonaOwnerHttpTransport",
    "_strategy_persona_idempotency_check",
    "_persist_persona_provisioning_terminal_transition",
    "_materialize_terminal_persona_provisioning_ledger",
    "_project_persona_dto",
    "_list_strategy_summaries",
)


def test_persona_dependencies_resolve_to_their_single_owner_after_composition(monkeypatch):
    main = importlib.import_module("services.control_plane.bff.main")
    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    strategy_service = importlib.import_module("services.control_plane.bff.strategies.service")
    app_factory = importlib.import_module("services.control_plane.bff.core.app_factory")

    owners = {name: (persona_service, name) for name in PERSONA_MAIN_RESIDUE_NAMES}
    owners["_list_strategy_summaries"] = (strategy_service, "list_strategy_summaries")

    for name, (owner, owner_name) in owners.items():
        assert not hasattr(main, name), name
        assert hasattr(owner, owner_name), name

    # These names are obtained by app_factory's production default resolver
    # after _dep has found no main.py override. Check object identity rather
    # than merely checking that composition returned some callable.
    resolver_owned_names = (
        "_strategy_persona_idempotency_check",
        "_STRATEGY_PERSONA_BFF_IDEMPOTENCY",
        "_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY",
        "_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY",
    )
    original_resolver = app_factory._resolve_default_dependency
    resolved_by_composition = {}

    def recording_resolver(name, app_deps):
        value = original_resolver(name, app_deps)
        if name in resolver_owned_names or name == "_list_strategy_summaries":
            resolved_by_composition[name] = value
        return value

    monkeypatch.setattr(app_factory, "_resolve_default_dependency", recording_resolver)
    composed_app = app_factory.compose_bff_app(app_deps=main.app_deps)

    for name in resolver_owned_names:
        assert not hasattr(main, name), name
        assert resolved_by_composition[name] is getattr(persona_service, name)
    assert callable(resolved_by_composition["_list_strategy_summaries"])

    assert main.app is not None
    assert composed_app.state.persona_service is not None
    store = persona_service._persona_provisioning_store()
    assert composed_app.state.persona_service.get_provisioning_store() is store

    mounted_routes = []
    pending_routes = list(composed_app.routes)
    while pending_routes:
        route = pending_routes.pop()
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            pending_routes.extend(included_router.routes)
        else:
            mounted_routes.append(route)
    detail_routes = [
        route
        for route in mounted_routes
        if getattr(route, "endpoint", None) is not None
        and route.endpoint.__globals__.get("_persona_provisioning_store")
        is persona_service._persona_provisioning_store
    ]
    assert detail_routes, [
        getattr(route, "path", None)
        for route in mounted_routes
        if "persona" in getattr(route, "path", "")
    ]
    route_store_functions = [
        route.endpoint.__globals__.get("_persona_provisioning_store")
        for route in detail_routes
    ]
    assert persona_service._persona_provisioning_store in route_store_functions
    assert all(function() is store for function in route_store_functions if function is not None)


def test_persona_composition_still_fails_closed_for_unresolved_dependencies():
    app_factory = importlib.import_module("services.control_plane.bff.core.app_factory")
    with pytest.raises(app_factory.UnresolvedBffDependency):
        app_factory._resolve_default_dependency("_unregistered_persona_dependency", object())


SAFE_DUPLICATE_NAMES = (
    "_read_surface_state",
    "_loop_run_controller_is_formal",
    "_INCIDENT_SEVERITY_MAP",
    "_incident_home_severity",
    "_management_number",
    "_management_avg",
    "_management_count_by",
    "_deprecated_bff_path_response",
)


def test_safe_duplicate_names_have_single_persona_service_owner(monkeypatch):
    import ast
    import inspect

    main = importlib.import_module("services.control_plane.bff.main")
    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    app_factory = importlib.import_module("services.control_plane.bff.core.app_factory")

    tree = ast.parse(inspect.getsource(main))
    defined_in_main = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined_in_main.add(node.name)
        elif isinstance(node, ast.Assign):
            defined_in_main.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for name in SAFE_DUPLICATE_NAMES:
        assert name not in defined_in_main, name
        assert hasattr(persona_service, name), name
        if name in vars(main):
            assert vars(main)[name] is getattr(persona_service, name), name

    resolved = {}
    original_resolver = app_factory._resolve_default_dependency

    def recording_resolver(name, app_deps):
        value = original_resolver(name, app_deps)
        resolved[name] = value
        return value

    monkeypatch.setattr(app_factory, "_resolve_default_dependency", recording_resolver)
    app_factory.compose_bff_app(app_deps=main.app_deps)

    for name in ("_read_surface_state", "_deprecated_bff_path_response"):
        assert original_resolver(name, main.app_deps) is getattr(persona_service, name), name
    for name, value in resolved.items():
        if name in SAFE_DUPLICATE_NAMES:
            assert value is getattr(persona_service, name), name
