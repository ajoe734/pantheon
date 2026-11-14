"""Single-owner gate for persona source-health / execution-health projection.

Part of BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001: retains
``services/control-plane/bff/personas/service.py`` (``PersonaService``) as the
sole owner of the source-health overlay and the execution persona-health
builder, with one TTL cache per service instance. ``main.py`` constructs one
app-scoped ``PersonaService`` and injects its bound methods into the runtime
router; it must define no second implementation, cache, or wrapper.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

BFF_DIR = Path(__file__).resolve().parents[1]

# The 11 AST-duplicate function bodies plus the 395-line builder and its two
# no-counterpart helpers (BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001).
# None of these may be (re)defined at module level in main.py: the sole
# implementation lives on PersonaService / personas/service.py.
RETIRED_MAIN_PROJECTION_FUNCTIONS = {
    "_trading_performance_delta",
    "_source_ingest_truth_by_connector",
    "_connector_candidates_for_provider",
    "_source_failure_reason",
    "_provider_status_from_truth",
    "_source_truth_projection",
    "_select_source_truth",
    "_source_health_bindings_from_requirements",
    "_data_source_ok_tone",
    "_upgrade_all_green_data_source_state",
    "_overlay_source_health_truth",
    "_build_persona_health_items",
    "_first_binding_for_persona",
    "_runtime_for_pool",
}


def _main_py_source() -> str:
    return (BFF_DIR / "main.py").read_text(encoding="utf-8")


def test_main_py_defines_no_retired_projection_functions() -> None:
    """main.py must not (re)define any of the 11+2 retired duplicate bodies."""
    tree = ast.parse(_main_py_source(), filename="main.py")
    top_level_funcs = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reintroduced = sorted(top_level_funcs & RETIRED_MAIN_PROJECTION_FUNCTIONS)
    assert not reintroduced, (
        f"main.py must not redefine retired persona-health projection "
        f"functions; found: {reintroduced}"
    )


def test_main_py_has_no_module_global_source_health_cache() -> None:
    """main.py must not hold a module-global source-health cache/TTL/constant."""
    tree = ast.parse(_main_py_source(), filename="main.py")
    top_level_names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    top_level_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top_level_names.add(node.target.id)

    forbidden = {
        "_SOURCE_HEALTH_OVERLAY_CACHE",
        "_SOURCE_HEALTH_OVERLAY_TTL",
        "_SOURCE_PROVIDER_CONNECTOR_CANDIDATES",
    }
    leaked = sorted(top_level_names & forbidden)
    assert not leaked, f"main.py must not own a second cache/constant set: {leaked}"


def test_main_py_constructs_persona_service_once_before_runtime_router() -> None:
    """app_factory.py builds exactly one app-scoped PersonaService, before the runtime
    router is assembled, and injects its bound method (not a bare function)."""
    text = (BFF_DIR / "core" / "app_factory.py").read_text(encoding="utf-8")

    construct_idx = text.index("PersonaService(")
    assert text.count("PersonaService(") == 1, (
        "app_factory.py must construct exactly one persona_service instance"
    )

    runtime_router_idx = text.index("create_runtime_router(")
    assert construct_idx < runtime_router_idx, (
        "persona_service must be constructed before the runtime router is "
        "assembled so runtime consumers bind to the same instance"
    )

    assert (
        'getattr(persona_service, "build_persona_health_items")' in text
    ), "runtime router must be injected the bound instance method, not a bare function"


def test_main_py_reuses_shared_instance_for_overlay_and_builder_call_sites() -> None:
    """The remaining main.py call sites must go through persona_service, not a
    bare module function."""
    text = _main_py_source()
    service_text = (BFF_DIR / "personas" / "service.py").read_text(encoding="utf-8")
    projection_start = service_text.index("def _project_persona_dto(")
    projection_end = service_text.index("\ndef ", projection_start + 1)
    projection = service_text[projection_start:projection_end]
    assert "_overlay_source_health_truth(" in projection
    assert "_active_persona_service().overlay_source_health_truth(" in service_text
    assert "overlay_source_health_truth(" not in text
    assert "persona_service.build_persona_health_items(" in text
    # No bare-function call sites of the retired names remain.
    for name in ("_overlay_source_health_truth(", "_build_persona_health_items("):
        for line in text.splitlines():
            if name in line:
                assert "persona_service." in line or "service.dependency" in line, (
                    f"Unexpected bare-function call site of {name!r}: {line!r}"
                )


def _fake_read_store() -> Any:
    class FakeReadStore:
        def __init__(self) -> None:
            self.registry_calls = 0
            self.snapshot_calls = 0

        def get_source_connector_registry(self) -> Dict[str, Any]:
            self.registry_calls += 1
            return {
                "connectors": [
                    {
                        "connector_id": "tw-twse-tpex-official-market",
                        "status": "configured",
                        "schedule": {},
                        "freshness": {"status": "fresh"},
                        "health_metrics": {},
                    }
                ]
            }

        def get_source_health_usage_snapshot(self) -> Dict[str, Any]:
            self.snapshot_calls += 1
            return {
                "sources": [
                    {
                        "health": {
                            "source_id": "tw-twse-tpex-official-market",
                            "status": "ok",
                            "last_success_at": "2026-09-14T00:00:00Z",
                            "row_count_last_run": 8,
                        },
                        "usage_aggregate_30d": {},
                    }
                ]
            }

        def list_persona_league(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def list_incidents(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def list_evolution_decisions(self) -> List[Dict[str, Any]]:
            return []

        def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
            return []

        def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return [
                {
                    "persona_id": "persona-tw-equity",
                    "id": "persona-tw-equity",
                    "name": "TW Equity",
                    "lifecycle_state": "active",
                    "metadata": {},
                }
            ]

        def get_bindings_for_persona(self, persona_id: str) -> List[Dict[str, Any]]:
            return []

        def list_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def list_runtime_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def list_strategy_specs(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def count_strategy_persona_bindings(self) -> Dict[str, int]:
            return {}

    return FakeReadStore()


def _make_persona_service(*, read_store: Optional[Any] = None, clock: Optional[Any] = None):
    from services.control_plane.bff.personas.service import PersonaService
    from services.control_plane.bff.personas.service import create_persona_registry_write_owner

    kwargs: Dict[str, Any] = dict(
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=object(),
        read_store=read_store if read_store is not None else _fake_read_store(),
        command_store=object(),
    )
    if clock is not None:
        kwargs["source_health_clock"] = clock
    return PersonaService(**kwargs)


def test_persona_service_owns_overlay_and_builder_methods() -> None:
    service = _make_persona_service()
    assert hasattr(service, "overlay_source_health_truth")
    assert hasattr(service, "build_persona_health_items")
    assert callable(service.overlay_source_health_truth)
    assert callable(service.build_persona_health_items)


def test_build_persona_health_items_uses_real_instance_builder() -> None:
    service = _make_persona_service()
    items = service.build_persona_health_items("2026-09-14T00:00:00Z")
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]
    assert item["persona_id"] == "persona-tw-equity"
    # None until a canonical trading-return schema is defined; no substitute value.
    assert item["perf_delta"] is None
    assert item["perfDelta"] is None


def test_overlay_source_health_truth_live_readback() -> None:
    service = _make_persona_service()
    dss, srcs, bindings = service.overlay_source_health_truth(
        {},
        [{"provider_key": "twse"}],
        required_data_sources=[],
    )
    assert srcs[0]["status"] == "read_ok"
    assert srcs[0]["source_health"]["row_count_last_run"] == 8
    assert dss["live_ingestion_enabled"] is True


def test_source_health_cache_is_instance_scoped_not_shared() -> None:
    """Two PersonaService instances must never share or clobber each other's
    source-health cache (the pre-existing module-global-cache defect)."""
    store_a = _fake_read_store()
    store_b = _fake_read_store()
    service_a = _make_persona_service(read_store=store_a)
    service_b = _make_persona_service(read_store=store_b)

    service_a.overlay_source_health_truth({}, [{"provider_key": "twse"}])
    assert store_a.registry_calls == 1
    assert store_b.registry_calls == 0

    service_b.overlay_source_health_truth({}, [{"provider_key": "twse"}])
    assert store_b.registry_calls == 1
    # Second instance's fetch must not have touched the first instance's store.
    assert store_a.registry_calls == 1

    assert service_a._source_health_cache is not service_b._source_health_cache


def test_source_health_cache_ttl_hit_then_refresh() -> None:
    store = _fake_read_store()
    now = {"t": 0.0}
    service = _make_persona_service(read_store=store, clock=lambda: now["t"])

    service.overlay_source_health_truth({}, [{"provider_key": "twse"}])
    assert store.registry_calls == 1

    # Within TTL (60s): cache hit, no re-fetch.
    now["t"] = 30.0
    service.overlay_source_health_truth({}, [{"provider_key": "twse"}])
    assert store.registry_calls == 1

    # Past TTL: refresh.
    now["t"] = 61.0
    service.overlay_source_health_truth({}, [{"provider_key": "twse"}])
    assert store.registry_calls == 2


def test_runtime_persona_and_main_share_the_same_bound_instance() -> None:
    """The same PersonaService instance's bound method must be what the
    runtime router receives, matching main.py's composition wiring."""
    service = _make_persona_service()
    bound = service.build_persona_health_items
    # A bound method's __self__ identifies the exact owning instance.
    assert bound.__self__ is service
    items_direct = service.build_persona_health_items("2026-09-14T00:00:00Z")
    items_via_bound = bound("2026-09-14T00:00:00Z")
    assert items_direct == items_via_bound


def test_v5_execution_persona_health_returns_top_level_owner_and_preserves_precedence_and_default() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.runtime.router import create_runtime_router

    store = _fake_read_store()
    personas_payload = [
        {
            "persona_id": "persona-chain-4923",
            "id": "persona-chain-4923",
            "name": "Chain 4923 Research Persona",
            "owner": "operator-chain-4923-owner",
            "lifecycle_state": "active",
            "metadata": {
                "data_scope": "regular",
                "persona_class": "research",
                "requested_by": "operator-chain-4923-owner",
            },
        },
        {
            "persona_id": "persona-meta-owner",
            "id": "persona-meta-owner",
            "name": "Meta Owner Precedence",
            "owner": "top-level-owner-ignored",
            "lifecycle_state": "active",
            "metadata": {
                "owner": "metadata-owner-wins",
                "owner_id": "metadata-owner-id-second",
            },
        },
        {
            "persona_id": "persona-meta-owner-id",
            "id": "persona-meta-owner-id",
            "name": "Meta Owner ID Precedence",
            "owner": "top-level-owner-ignored",
            "lifecycle_state": "active",
            "metadata": {
                "owner_id": "metadata-owner-id-wins",
            },
        },
        {
            "persona_id": "persona-default-owner",
            "id": "persona-default-owner",
            "name": "Default Owner Fallback",
            "lifecycle_state": "active",
            "metadata": {},
        },
    ]
    store.list_personas = lambda **kwargs: list(personas_payload)
    service = _make_persona_service(read_store=store)

    app = FastAPI()
    app.include_router(
        create_runtime_router(
            read_surface=store,
            dependencies={
                "_extract_identity": lambda auth: {"roles": ["viewer", "operator", "admin"]},
                "_require_read_role": lambda identity: None,
                "_dataset_surface_status": lambda name, snapshot_at=None: {"status": "ok", "source": "in_memory"},
                "_composed_dataset_surface_status": lambda name, items, snapshot_at=None, source="": {
                    "status": "ok",
                    "source": source,
                },
                "_build_persona_health_items": service.build_persona_health_items,
                "utc_now": lambda: "2026-11-07T00:00:00Z",
            },
        )
    )
    client = TestClient(app)
    resp = client.get(
        "/bff/v5/execution/persona-health",
        headers={"Authorization": "Bearer op:operator"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    by_id = {item["persona_id"]: item for item in items}
    assert by_id["persona-chain-4923"]["owner"] == "operator-chain-4923-owner"
    assert by_id["persona-meta-owner"]["owner"] == "metadata-owner-wins"
    assert by_id["persona-meta-owner-id"]["owner"] == "metadata-owner-id-wins"
    assert by_id["persona-default-owner"]["owner"] == "pathreon-management"



def test_persona_health_rows_use_thin_source_health_overlay_and_stay_under_1mb() -> None:
    """BFF-PERSONA-HEALTH-AND-LEAGUE-READS-20261114: four personas that each
    carry ~1 MB of required-source health must not bloat persona-health."""
    import json as _json

    read_store = _fake_read_store()
    pad = "x" * 250_000
    base_registry = read_store.get_source_connector_registry

    def _heavy_registry() -> Dict[str, Any]:
        registry = base_registry()
        for connector in registry["connectors"]:
            connector["schedule"] = {"blob": pad}
        return registry

    read_store.get_source_connector_registry = _heavy_registry  # type: ignore[method-assign]

    def _list_personas(**kwargs: Any) -> List[Dict[str, Any]]:
        rows = []
        for index in range(4):
            rows.append(
                {
                    "persona_id": f"persona-heavy-{index}",
                    "id": f"persona-heavy-{index}",
                    "name": f"Heavy {index}",
                    "lifecycle_state": "active",
                    "metadata": {},
                    "required_data_sources": [
                        {
                            "source_key": f"src-{index}-{part}",
                            "connector_candidates": ["tw-twse-tpex-official-market"],
                        }
                        for part in range(4)
                    ],
                }
            )
        return rows

    read_store.list_personas = _list_personas  # type: ignore[method-assign]
    service = _make_persona_service(read_store=read_store)
    items = service.build_persona_health_items("2026-09-14T00:00:00Z")
    assert len(items) == 4
    assert len(_json.dumps(items, default=str)) < 1_000_000
    for item in items:
        status = item.get("data_source_status") or {}
        assert "required_source_health" not in status
        assert "requiredSourceHealth" not in status
        assert item["source_health_bindings"] == []
