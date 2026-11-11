"""Regression and contract test for decomposed Strategies router."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.strategies.router import create_strategies_router

AUTH_HEADERS = {"Authorization": "Bearer test-op:operator,admin"}


def test_strategies_router_decomposition_routes():
    router = create_strategies_router()
    actual_routes = {(r.path, frozenset(r.methods)) for r in router.routes}

    expected_routes = [
        ("/bff/strategies", frozenset({"GET"})),
        ("/bff/strategies", frozenset({"POST"})),
        ("/bff/strategies/{strategy_id}", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}", frozenset({"PATCH"})),
        ("/bff/strategies/{strategy_id}/specs", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/specs", frozenset({"POST"})),
        ("/bff/strategies/{strategy_id}/experiments", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/artifacts", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/lineage", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/audit", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/ooda", frozenset({"GET"})),
        ("/bff/strategies/{strategy_id}/dry-run", frozenset({"POST"})),
        ("/bff/management/strategy-seeds", frozenset({"GET"})),
        ("/bff/management/strategy-seeds/{seed_id}", frozenset({"GET"})),
        ("/bff/management/strategy-seeds/{seed_id}/review", frozenset({"POST"})),
        ("/bff/management/strategy-seeds/{seed_id}/merge", frozenset({"POST"})),
        ("/bff/management/strategy-seeds/{seed_id}/submit-replication", frozenset({"POST"})),
    ]

    assert len(router.routes) == len(expected_routes)
    for path, methods in expected_routes:
        assert (path, methods) in actual_routes, f"Missing route {path} {methods}"


def test_strategies_router_client_execution():
    app = FastAPI()
    router = create_strategies_router(
        list_strategy_summaries=lambda: [
            {"strategy_id": "strat-1", "title": "Momentum Alpha", "lifecycle_state": "candidate"}
        ],
        read_surface=lambda: type(
            "FakeReadStore",
            (),
            {
                "get_strategy_spec": lambda self, sid: {"strategy_id": sid, "title": "Momentum Alpha"},
                "get_strategy_spec_detail": lambda self, sid, version_selector="current": {
                    "lifecycle_state": "candidate",
                    "governance": {"risk_level": "medium"},
                },
                "list_strategy_spec_versions": lambda self, sid: [{"version": "1.0.0"}],
            },
        )(),
    )
    app.include_router(router)
    client = TestClient(app)

    # List strategies
    resp = client.get("/bff/strategies", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "strat-1"

    # Get strategy
    resp = client.get("/bff/strategies/strat-1", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "strat-1"

    # List specs
    resp = client.get("/bff/strategies/strat-1/specs", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "service",
        "routes",
        "routes.common",
        "routes.collection",
        "routes.detail",
        "routes.seeds",
        "router",
    ],
)
def test_strategies_modules_cold_import_in_any_entry_order(module):
    """Each module must import first in a fresh interpreter (no service<->routes cycle)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import services.control_plane.bff.strategies.{module}"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 0, result.stderr


def test_strategies_service_has_no_routes_import_dependency():
    """service.py must import and execute get_strategy_rules without importing routes.*."""
    script = (
        "import sys; "
        "from types import SimpleNamespace; "
        "from services.control_plane.bff.strategies.service import StrategiesService; "
        "assert 'services.control_plane.bff.strategies.routes' not in sys.modules; "
        "svc = StrategiesService(read_surface=SimpleNamespace("
        "read_registry_strategy_artifacts_for_strategy=lambda sid: SimpleNamespace("
        "status='ok', items=[{'registry_id': 'art-1', 'artifact_type': 'execution_bundle', "
        "'artifact_state': 'approved', 'strategy_id': sid, 'version': '1.0.0'}]))); "
        "rules, status = svc.get_strategy_rules('strat-1'); "
        "assert status == 'ok' and len(rules) == 1 and rules[0]['registryId'] == 'art-1'; "
        "assert 'services.control_plane.bff.strategies.routes' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 0, result.stderr


def _build_bounded_hide_retired_test_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    draft_count: int = 3217,
    retired_persona_count: int = 134,
    running_persona_count: int = 5,
    idle_persona_count: int = 21,
    unbound_approved_count: int = 2,
    retired_strategy_count: int = 200,
):
    import urllib.error
    import urllib.parse
    from types import SimpleNamespace

    from services.control_plane.bff.auth import policy as auth_policy
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.personas import service as personas_service
    from services.control_plane.bff.ports import create_read_surface_ports
    from services.control_plane.bff.strategies import service as strategies_service_mod
    from services.control_plane.bff.strategies.routes.common import (
        default_page_slice,
        default_read_surface_meta,
    )
    from services.registry.models import (
        ArtifactState,
        ArtifactType,
        Lineage,
        RegistryEntry,
        StorageBackend,
        StorageRef,
    )
    from services.registry.service import RegistryService
    from services.registry.storage import RegistryStore

    tenant = "tenant-dev"
    reg_store = RegistryStore()
    persona_records: dict[str, dict] = {}
    owner_summary_calls: list[tuple[str, int]] = []
    total_full_rows_extracted = 0

    def _add_entry(
        idx: int,
        sid: str,
        art_state: ArtifactState,
        lifecycle_state: str,
        persona_ids: list[str],
        updated_at: str,
    ) -> None:
        rid = f"reg-{sid}-v1"
        reg_store._entries[rid] = RegistryEntry(
            registry_id=rid,
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=art_state,
            lineage=Lineage(source_run_ids=[f"run-{idx}"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid}",
            metadata={
                "strategy_spec": {
                    "strategy_id": sid,
                    "version": "1.0.0",
                    "title": f"Strategy {sid}",
                    "lifecycle_state": lifecycle_state,
                    "persona_ids": persona_ids,
                    "governance": {"risk_level": "medium", "owner": "quant-desk"},
                    "market_scope": {"market": "TW", "alpha": "momentum"},
                }
            },
            created_at="2026-10-01T00:00:00Z",
            updated_at=updated_at,
            owner_tenant=tenant,
        )

    idx_counter = 0
    # 1. Non-draft strategies (interleaved timestamps)
    for i in range(retired_persona_count):
        pid = f"persona-retired-{i:04d}"
        persona_records[pid] = {
            "persona_id": pid,
            "tenant_id": tenant,
            "lifecycle_state": "retired" if i % 2 == 0 else "paper_running",
            "status": "active" if i % 2 == 0 else "archived",
        }
        _add_entry(
            idx_counter,
            f"strat-ret-persona-{i:04d}",
            ArtifactState.APPROVED,
            "approved",
            [pid],
            f"2026-10-20T{(i // 60) % 24:02d}:{i % 60:02d}:00Z",
        )
        idx_counter += 1

    for i in range(running_persona_count):
        pid = f"persona-running-{i:04d}"
        persona_records[pid] = {
            "persona_id": pid,
            "tenant_id": tenant,
            "lifecycle_state": "paper_running",
            "status": "active",
        }
        _add_entry(
            idx_counter,
            f"strat-running-persona-{i:04d}",
            ArtifactState.APPROVED,
            "paper_running",
            [pid],
            f"2026-10-25T00:{i:02d}:00Z",
        )
        idx_counter += 1

    for i in range(idle_persona_count):
        pid = f"persona-idle-{i:04d}"
        persona_records[pid] = {
            "persona_id": pid,
            "tenant_id": tenant,
            "lifecycle_state": "approved",
            "status": "active",
        }
        _add_entry(
            idx_counter,
            f"strat-idle-persona-{i:04d}",
            ArtifactState.CANDIDATE,
            "candidate",
            [pid],
            f"2026-10-22T00:{i:02d}:00Z",
        )
        idx_counter += 1

    for i in range(unbound_approved_count):
        _add_entry(
            idx_counter,
            f"strat-unbound-approved-{i:04d}",
            ArtifactState.APPROVED,
            "approved",
            [],
            f"2026-10-24T00:{i:02d}:00Z",
        )
        idx_counter += 1

    # 2. 3,217 unbound draft strategies
    for i in range(draft_count):
        day = 1 + (i % 28)
        hour = (i // 28) % 24
        minute = i % 60
        _add_entry(
            idx_counter,
            f"strat-unbound-draft-{i:04d}",
            ArtifactState.DRAFT,
            "draft",
            [],
            f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00Z",
        )
        idx_counter += 1

    # 3. 200 retired strategy families
    for i in range(retired_strategy_count):
        _add_entry(
            idx_counter,
            f"strat-retired-family-{i:04d}",
            ArtifactState.RETIRED,
            "retired",
            [],
            "2026-09-15T00:00:00Z",
        )
        idx_counter += 1

    reg_service = RegistryService(store=reg_store)
    state_holder = {"force_owner_error": None, "total_full_rows_extracted": 0}

    def _owner_http_stub(url: str, **kwargs):
        if state_holder["force_owner_error"] is not None:
            raise state_holder["force_owner_error"]
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        tenant_id = str(kwargs.get("tenant_id") or tenant)
        if parsed.path == "/api/registry/strategy-specs" and qs.get("view") == ["strategy_summary_page"]:
            try:
                payload = reg_service.strategy_spec_summary_page(
                    visible_tenants=frozenset({tenant_id, "__builtin__"}),
                    artifact_type="strategy_spec",
                    state=qs["state"][0] if "state" in qs else None,
                    exclude_state=qs["exclude_state"][0] if "exclude_state" in qs else None,
                    persona_id=qs["persona_id"][0] if "persona_id" in qs else None,
                    page_token=qs["page_token"][0] if "page_token" in qs else None,
                    page_size=int(qs["page_size"][0]) if "page_size" in qs else 20,
                )
            except ValueError as exc:
                msg = str(exc)
                status = 400 if (msg.startswith("Invalid page_token") or "exclude_state" in msg) else 503
                raise urllib.error.HTTPError(
                    url, status, msg, hdrs=None, fp=None
                ) from exc
            state_holder["total_full_rows_extracted"] += reg_store.full_summary_rows_extracted
            owner_summary_calls.append((f"{parsed.path}?{parsed.query}", len(payload.get("items") or [])))
            return payload
        raise AssertionError(f"Unexpected owner HTTP call: {url}")

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.local")
    monkeypatch.setattr(adapter_base, "http_request_json", _owner_http_stub)

    read_ports = create_read_surface_ports()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=lambda: strategies_service_mod.list_strategy_summaries(read_ports),
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-10T12:00:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=personas_service._normalize_lifecycle_state,
            normalize_risk_level=personas_service._normalize_risk_level,
            bff_me_tenant_payload=lambda identity, requested_tenant=None: {"id": tenant},
            persona_directory_snapshot=lambda tenant_id=None, **_: SimpleNamespace(
                records_by_id=dict(persona_records)
            ),
            persona_record_projected_state=personas_service._persona_record_projected_state,
            persona_operational_states=personas_service._PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    return app, owner_summary_calls, state_holder


def test_hide_retired_personas_without_state_filter_avoids_17_page_scan_over_3400_strategies(
    monkeypatch: pytest.MonkeyPatch,
):
    """AC3: ``GET /bff/strategies`` with ``hide_retired_personas=true`` and no
    state filter over ~3,400 strategy families (3,217 unbound drafts + 162
    non-drafts) no longer needs one full registry scan per 200 rows (17 scans);
    it completes in <= 2 registry reads while producing the exact same response.
    """
    app, owner_summary_calls, state_holder = _build_bounded_hide_retired_test_app(monkeypatch)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-op:operator,viewer:tenant-dev", "X-Tenant-Id": "tenant-dev"}

    owner_summary_calls.clear()
    state_holder["total_full_rows_extracted"] = 0
    resp = client.get(
        "/bff/strategies?hide_retired_personas=true&page_size=20",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Must use at most 2 registry reads (1 first page of 200 + 1 exclude_state=draft read of 162 rows),
    # never 17 pages of 200 rows across all 3,379 strategies!
    assert len(owner_summary_calls) == 2, f"Expected 2 registry reads, got {len(owner_summary_calls)}: {owner_summary_calls}"
    assert owner_summary_calls[0] == (
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=200",
        200,
    )
    assert owner_summary_calls[1] == (
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=200&exclude_state=draft",
        162,
    )
    assert len(body["items"]) == 20
    # Total full summary rows extracted across both calls is 200 + 162 = 362 (out of 3,579 stored rows),
    # vs 17 * 3,579 = 60,843 rows previously.
    assert state_holder["total_full_rows_extracted"] == 362

    # Verify exact totals, hidden count, operational_counts, and state_counts
    # 3,379 total current strategies - 134 retired-persona strategies = 3,245 visible
    assert body["page_info"] == {"next_page_token": "20", "total": 3245}
    assert body["meta"]["total"] == 3245
    assert body["meta"]["hidden_retired_persona_count"] == 134
    assert body["meta"]["operational_counts"] == {
        "running": 5,
        "idle": 21,
        "retired": 134,
        "unbound": 3219,  # 3,217 unbound drafts + 2 unbound approved
        "unknown": 0,
    }
    assert body["meta"]["state_counts"] == {
        "draft": 3217,
        "review": 21,
        "approved": 136,
        "paper_running": 5,
        "canary_running": 0,
        "live_running": 0,
        "deployed": 0,
        "paused": 0,
        "retired": 200,
    }
    # No item on the returned page may belong to a retired persona
    for item in body["items"]:
        assert item["operationalStatus"] != "retired"

    # Page 2 (offset 20 -> next 20 items) also takes only 2 registry reads
    owner_summary_calls.clear()
    resp_page2 = client.get(
        "/bff/strategies?hide_retired_personas=true&page_size=20&page_token=20",
        headers=headers,
    )
    assert resp_page2.status_code == 200, resp_page2.text
    page2_body = resp_page2.json()
    assert len(owner_summary_calls) == 2
    assert len(page2_body["items"]) == 20
    assert page2_body["page_info"] == {"next_page_token": "40", "total": 3245}


def test_bff_strategies_preserves_error_contract_for_owner_failures_invalid_tokens_and_conflicting_filters(
    monkeypatch: pytest.MonkeyPatch,
):
    """AC5: Owner failures (503), invalid page tokens (400), and conflicting
    state filters (400) keep today's status and error on ``GET /bff/strategies``.
    """
    from services.control_plane.bff.command_adapters import base as adapter_base

    app, _calls, state_holder = _build_bounded_hide_retired_test_app(
        monkeypatch,
        draft_count=10,
        retired_persona_count=2,
        running_persona_count=1,
        idle_persona_count=1,
        unbound_approved_count=1,
        retired_strategy_count=1,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-op:operator,viewer:tenant-dev", "X-Tenant-Id": "tenant-dev"}

    # 1. Invalid page_token -> 400 VALIDATION_FAILED
    for query in (
        "page_token=not-an-int",
        "hide_retired_personas=true&page_token=not-an-int",
        "hide_retired_personas=true&page_token=-1",
    ):
        resp_bad = client.get(f"/bff/strategies?{query}", headers=headers)
        assert resp_bad.status_code == 400, (query, resp_bad.text)
        err = resp_bad.json()["error"]
        assert err["code"] == "VALIDATION_FAILED"
        assert "page_token" in err["message"]

    # 2. Conflicting state and exclude_state -> 400 VALIDATION_FAILED
    resp_conflict = client.get(
        "/bff/strategies?state=approved&exclude_state=draft",
        headers=headers,
    )
    assert resp_conflict.status_code == 400, resp_conflict.text
    assert resp_conflict.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "Conflicting state filters" in resp_conflict.json()["error"]["message"]

    # 3. Owner failure -> 503 DEPENDENCY_UNAVAILABLE
    import urllib.error

    state_holder["force_owner_error"] = urllib.error.HTTPError(
        "http://registry.local/api/registry/strategy-specs",
        503,
        "registry owner db unavailable",
        hdrs=None,
        fp=None,
    )
    for query in ("page_size=20", "hide_retired_personas=true&page_size=20"):
        resp_503 = client.get(f"/bff/strategies?{query}", headers=headers)
        assert resp_503.status_code == 503, (query, resp_503.text)
        assert resp_503.json()["error"]["code"] in ("DEPENDENCY_UNAVAILABLE", "OWNER_UNAVAILABLE")

