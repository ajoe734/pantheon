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


EQUIVALENT_MOVED_NAMES = (
    "_list_persona_records",
    "_meta_staleness",
    "_surface_status",
    "_dataset_source_after_read",
    "_raise_if_read_surface_unavailable",
    "_composed_surface_status",
    "_decode_page_token",
    "_page_slice",
    "_dataset_surface_status",
    "_list_governance_audit_events",
    "_sem_command_response",
    "_composed_dataset_surface_status",
    "_read_surface_meta",
)


def _module_level_definitions(module):
    import ast
    import inspect

    defined = set()
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return defined


def test_converged_helpers_have_single_persona_service_owner(monkeypatch):
    main = importlib.import_module("services.control_plane.bff.main")
    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    app_factory = importlib.import_module("services.control_plane.bff.core.app_factory")

    defined_in_main = _module_level_definitions(main)
    defined_in_service = _module_level_definitions(persona_service)
    for name in EQUIVALENT_MOVED_NAMES:
        assert name not in defined_in_main, name
        assert name in defined_in_service, name
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

    for name in EQUIVALENT_MOVED_NAMES:
        assert original_resolver(name, main.app_deps) is getattr(persona_service, name), name
    for name, value in resolved.items():
        if name in EQUIVALENT_MOVED_NAMES:
            assert value is getattr(persona_service, name), name
    assert persona_service._composed_command_adapter_service is main.app.state.command_adapter_service


def test_former_main_callers_pass_the_main_read_store_explicitly(monkeypatch):
    main = importlib.import_module("services.control_plane.bff.main")
    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    app_factory = importlib.import_module("services.control_plane.bff.core.app_factory")
    management_service = importlib.import_module("services.control_plane.bff.assistant.management_service")
    human_inbox = importlib.import_module("services.control_plane.bff.governance.human_inbox")

    app_factory.compose_bff_app(app_deps=main.app_deps)

    captured = {}

    def record_list(tenant_id=None, *, read_store=None):
        captured["list_persona_records"] = read_store
        return []

    # Re-run main.py's own production wiring statement so the callable under test
    # is the one main wires, then restore the management_service globals.
    import ast
    import inspect

    wiring = next(
        node
        for node in ast.parse(inspect.getsource(main)).body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", None) == "wire_management_runtime_projections"
    )
    saved_globals = dict(vars(management_service))
    monkeypatch.setattr(main, "_list_persona_records", record_list)
    try:
        exec(compile(ast.Module([wiring], []), "main.py:wire_management_runtime_projections", "exec"), vars(main))
        management_service.get_list_persona_records()("tenant-1")
    finally:
        vars(management_service).clear()
        vars(management_service).update(saved_globals)
    assert captured["list_persona_records"] is main.read_store

    def record_source(dataset, *, read_store=None):
        captured.setdefault("dataset_source_after_read", []).append(read_store)
        return "missing"

    monkeypatch.setattr(main, "_dataset_source_after_read", record_source)
    monkeypatch.setattr(persona_service, "_dataset_source_after_read", record_source)
    main._composed_dataset_surface_status(
        "personas", [], read_store=main.read_store, snapshot_at="2026-10-08T00:00:00Z", source="test"
    )

    class InjectedStore:
        def list_governance_review_queue_items(self):
            return []

        def list_approval_queue_items(self):
            return []

    human_inbox._human_inbox_governance_contributor("2026-10-08T00:00:00Z", read_store=InjectedStore())
    human_inbox._human_inbox_approval_contributor("2026-10-08T00:00:00Z", read_store=InjectedStore())
    assert len(captured["dataset_source_after_read"]) == 3
    assert all(store is main.read_store for store in captured["dataset_source_after_read"])


class _SurfaceStore:
    """Read store whose generic source and incident port disagree."""

    def __init__(self, source, incident_port_source="missing"):
        self._source = source
        self.incident_port = type(
            "IncidentPort", (), {"dataset_source": staticmethod(lambda: incident_port_source)}
        )()

    def dataset_source(self, dataset):
        return self._source


def test_dataset_surface_status_defaults_keep_main_rules_and_service_rules_keep_service_rules(monkeypatch):
    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", "fresh")
    at = "2026-10-09T00:00:00Z"

    # Unavailable source: main rules mark it unavailable without staleness,
    # service rules attach the staleness record.
    store = _SurfaceStore("unavailable")
    main_rules = persona_service._dataset_surface_status("personas", read_store=store, snapshot_at=at)
    service_rules = persona_service._dataset_surface_status(
        "personas", read_store=store, snapshot_at=at, service_surface_rules=True
    )
    assert main_rules == {"status": "unavailable", "source": "unavailable"}
    assert service_rules["status"] == "unavailable"
    assert service_rules["staleness"] == {"served_from": "unavailable", "last_known_at": at}

    # Incidents: main rules derive the source from the incident port, service
    # rules take the store's own dataset source.
    store = _SurfaceStore("typed_store", incident_port_source="unavailable")
    main_rules = persona_service._dataset_surface_status("incidents", read_store=store, snapshot_at=at)
    service_rules = persona_service._dataset_surface_status(
        "incidents", read_store=store, snapshot_at=at, service_surface_rules=True
    )
    assert main_rules == {"status": "unavailable", "source": "unavailable"}
    assert service_rules == {"status": "ok", "source": "typed_store"}

    # The dependent helpers forward the rule selection.
    meta_main = persona_service._read_surface_meta("personas", "personas", read_store=_SurfaceStore("unavailable"), snapshot_at=at)
    meta_service = persona_service._read_surface_meta(
        "personas", "personas", read_store=_SurfaceStore("unavailable"), snapshot_at=at, service_surface_rules=True
    )
    assert "staleness" not in meta_main["surfaces"]["personas"]
    assert "staleness" in meta_service["surfaces"]["personas"]
    composed_main = persona_service._composed_dataset_surface_status(
        "personas", [], read_store=_SurfaceStore("unavailable"), snapshot_at=at, source="x"
    )
    composed_service = persona_service._composed_dataset_surface_status(
        "personas", [], read_store=_SurfaceStore("unavailable"), snapshot_at=at, source="x", service_surface_rules=True
    )
    assert "staleness" not in composed_main
    assert "staleness" in composed_service


def test_service_internal_callers_pass_service_surface_rules():
    import ast
    import inspect

    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    names = {"_dataset_surface_status", "_composed_dataset_surface_status", "_read_surface_meta"}
    calls = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(persona_service)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names
    ]
    assert len(calls) >= 30
    for call in calls:
        keywords = {k.arg: k.value for k in call.keywords}
        assert isinstance(keywords.get("service_surface_rules"), (ast.Constant, ast.Name)), call.lineno
        assert getattr(keywords["service_surface_rules"], "value", True) is True, call.lineno


def test_audit_events_keep_agora_merge_for_console_reads_and_skip_it_for_persona_detail():
    import ast
    import inspect
    from types import SimpleNamespace

    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.models import CommandType

    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    detail = importlib.import_module("services.control_plane.bff.personas.routes.detail")
    import tempfile
    import os

    command_store = CommandStore(os.path.join(tempfile.mkdtemp(prefix="audit-merge-"), "commands.jsonl"))
    command_store.submit_command(
        command_id="cmd-audit-1",
        command_type=CommandType.CAPITAL_POOL_ACTION,
        target={"type": "CapitalPool", "id": "pool-1"},
        submitted_at="2026-10-03T00:00:00Z",
        params={},
        audit_context={"operator_id": "op-1", "reason": "COMMAND_AUDIT"},
    )
    read_store = SimpleNamespace(list_governance_audit_events=lambda **kw: [])
    agora_event = {
        "entry_id": "agora-1",
        "timestamp": "2026-10-04T00:00:00Z",
        "actor": "agora-op",
        "action_type": "agora.mutation",
        "target_type": "AgoraEntity",
    }
    agora_store = SimpleNamespace(list_agora_audit_events=lambda **kw: [agora_event])
    service = persona_service.PersonaService(
        read_store=read_store,
        write_owner=SimpleNamespace(),
        command_store=command_store,
        ranking_write_owner=SimpleNamespace(),
    )
    token = persona_service._current_persona_service.set(service)
    try:
        console = persona_service._list_governance_audit_events(agora_audit_store=agora_store)
        persona_tab = persona_service._list_governance_audit_events(
            include_agora_events=False, agora_audit_store=agora_store
        )
    finally:
        persona_service._current_persona_service.reset(token)
    console_ids = {event["entry_id"] for event in console}
    persona_ids = {event["entry_id"] for event in persona_tab}
    assert "agora-1" in console_ids
    assert "agora-1" not in persona_ids
    assert console_ids - {"agora-1"} == persona_ids and persona_ids

    # The persona detail audit tab is the only service-side caller and must opt out.
    detail_calls = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(detail)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_list_governance_audit_events"
    ]
    assert len(detail_calls) == 1
    (keyword,) = detail_calls[0].keywords
    assert keyword.arg == "include_agora_events" and keyword.value.value is False


def test_sem_command_response_defaults_use_the_composed_adapter_and_forward_authorization_and_dry_run(monkeypatch):
    from types import SimpleNamespace

    from services.control_plane.bff.models import CommandType, ObjectType

    persona_service = importlib.import_module("services.control_plane.bff.personas.service")
    main = importlib.import_module("services.control_plane.bff.main")
    seen = {}

    class RecordingAdapter:
        def sem_command_response(self, **kwargs):
            seen["composed"] = kwargs
            return "composed"

    args = dict(
        command_type=CommandType.PAUSE_EXECUTION,
        target_type=ObjectType.RUNTIME_BINDING,
        target_id="rt-1",
        payload={},
        identity=SimpleNamespace(operator_id="op"),
        idempotency_key="k",
    )
    monkeypatch.setattr(persona_service, "_composed_command_adapter_service", RecordingAdapter())
    assert persona_service._sem_command_response(**args, authorization="Bearer t", dry_run=True) == "composed"
    assert seen["composed"]["authorization"] == "Bearer t" and seen["composed"]["dry_run"] is True

    # Former service behaviour: a request-local adapter that is not the composed one.
    class IsolatedAdapter(RecordingAdapter):
        def __init__(self, **kwargs):
            seen["isolated_deps"] = kwargs

        def sem_command_response(self, **kwargs):
            seen["isolated"] = kwargs
            return "isolated"

    monkeypatch.setattr(persona_service, "CommandAdapterService", IsolatedAdapter)
    assert persona_service._sem_command_response(**args, isolated_adapter=True) == "isolated"
    assert "validators" not in seen["isolated_deps"]
    assert seen["isolated"]["authorization"] is None and seen["isolated"]["dry_run"] is False

    # Unwired composition fails closed instead of building a hidden adapter.
    monkeypatch.setattr(persona_service, "_composed_command_adapter_service", None)
    with pytest.raises(RuntimeError, match="failing closed"):
        persona_service._sem_command_response(**args)
    assert main._sem_command_response is persona_service._sem_command_response


def test_composed_app_paths_reach_the_same_read_and_command_stores():
    main = importlib.import_module("services.control_plane.bff.main")
    persona_service = importlib.import_module("services.control_plane.bff.personas.service")

    # Former main paths used main.read_store / main.command_store; former service
    # paths use the composed persona service stores. All four are the app_deps objects.
    composed = main.app.state.persona_service
    assert persona_service._composed_persona_service is composed
    assert persona_service._get_active_read_store() is main.read_store is composed.get_read_store()
    assert persona_service._get_active_command_store() is main.command_store is composed.get_command_store()
    assert main.app.state.command_adapter_service is main._command_adapter_service
    assert persona_service._composed_command_adapter_service is main._command_adapter_service
