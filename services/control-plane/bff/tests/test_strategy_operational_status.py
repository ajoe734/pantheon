"""Strategy operational truth: personaStates, operationalStatus, hide filter, rules.

Covers BFF-STRATEGY-OPERATIONAL-TRUTH-20261107 with the real hosted shapes read on
2026-11-07: 141 default-view strategies (134 whose only persona is retired, 5 with
a paper_running persona, 2 unbound), summary rows with market_scope.symbols
[2330.TW], one persona id per bound row and the provisioning coordinator's
StrategySpec / execution_bundle shapes. The registry owner is a real
``RegistryService`` behind a stubbed HTTP boundary, so paging, ordering and
``state_counts`` are the owner's own. Nothing here writes a strategy, artifact
or persona.
"""
from __future__ import annotations

import urllib.parse
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.strategies import service as strategies_service_mod
from services.control_plane.bff.strategies.router import create_strategies_router
from services.control_plane.bff.strategies.routes.common import (
    PersonaStateLookup,
    _project_strategy_spec_detail,
    project_strategy_rules,
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
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore

TENANT = "tenant-dev"
HEADERS = {
    "Authorization": f"Bearer op-strategy-view:operator,viewer:{TENANT}",
    "X-Tenant-Id": TENANT,
}
RUNNING_PERSONA_COUNT = 5
UNBOUND_COUNT = 2
RETIRED_COUNT = 134
TOTAL_COUNT = RUNNING_PERSONA_COUNT + UNBOUND_COUNT + RETIRED_COUNT  # 141


@pytest.fixture(autouse=True)
def _permissive_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "1")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


# ---------------------------------------------------------------------------
# Real-shape fixtures
# ---------------------------------------------------------------------------


def _spec_entry(
    *,
    strategy_id: str,
    title: str,
    persona_ids: List[str],
    updated_at: str,
    hypothesis: str = "Paper-only lifecycle verification in dev",
    objective: str = "Admit this Persona to paper-only evaluation with zero live capital.",
    symbols: Optional[List[str]] = None,
) -> RegistryEntry:
    """StrategySpec registry entry in the persona_provisioning_coordinator shape."""
    inner: Dict[str, Any] = {
        "spec_version": "1.0.0",
        "strategy_id": strategy_id,
        "title": title,
        "hypothesis": hypothesis,
        "objective": objective,
        "lifecycle_state": "approved",
        "market_scope": {
            "symbols": ["2330.TW"] if symbols is None else symbols,
            "asset_classes": ["equity"],
            "frequency": "1d",
        },
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "SHARES",
            "rebalance_cadence": "1d bar close, paper only",
            "execution_mode_hint": "paper",
        },
        "evaluation_plan": {"metrics": ["sharpe_ratio"], "paper_gate": "sharpe_ratio >= 1.1"},
        "persona_ids": list(persona_ids),
        "metadata": {"market": "TW", "execution_context": "paper"},
    }
    return RegistryEntry(
        registry_id=f"reg-{strategy_id}",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=strategy_id,
        version="1.0.0",
        artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=[f"run-{strategy_id}"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum=f"sha256:reg-{strategy_id}",
        evaluation_summary={"risk_level": "low"},
        metadata={
            "tenant_id": TENANT,
            "title": title,
            "persona_ids": list(persona_ids),
            "strategy_spec": inner,
        },
        created_at=updated_at,
        updated_at=updated_at,
        owner_tenant=TENANT,
    )


def _bundle_entry(
    *,
    registry_id: str,
    strategy_id: str,
    artifact_state: str = "approved",
    parent_registry_ids: Optional[List[str]] = None,
    baseline: bool = False,
) -> Dict[str, Any]:
    """execution_bundle entry as the coordinator writes it."""
    positive = "HOLD" if baseline else "BUY"
    non_positive = "HOLD" if baseline else "SELL"
    return {
        "registry_id": registry_id,
        "artifact_type": "execution_bundle",
        "strategy_id": strategy_id,
        "version": "1.0.0",
        "artifact_state": artifact_state,
        "lineage": {
            "parent_registry_ids": list(parent_registry_ids or []),
            "source_strategy_spec_id": f"reg-{strategy_id}",
        },
        "metadata": {
            "strategy_artifact": {
                "strategy_logic": {
                    "kind": "close_to_close_momentum",
                    "lookback_parameter": "lookback_bars",
                    "threshold_parameter": "momentum_threshold",
                    "positive_action": positive,
                    "non_positive_action": non_positive,
                },
                "parameters": {
                    "market": "TW",
                    "symbols": ["2330.TW"],
                    "data_source": "source-ingest:paper/persona-bootstrap",
                    "bar_frequency": "1d",
                    "lookback_bars": 2,
                    "quantity_type": "SHARES",
                    "order_quantity": 0 if baseline else 1,
                    "momentum_threshold": 0,
                    "zero_momentum_action": non_positive,
                },
                "mutation_surface": {
                    "controls": [
                        {
                            "parameter_key": "lookback_bars",
                            "value_type": "integer",
                            "current_value": 2,
                            "allowed_range": {"min": 2, "max": 60},
                            "step": 1,
                        },
                        {
                            "parameter_key": "momentum_threshold",
                            "value_type": "number",
                            "current_value": 0.0,
                            "allowed_range": {"min": 0.0, "max": 0.05},
                            "step": 0.001,
                        },
                    ],
                    "immutable_parameters": [
                        "symbols",
                        "bar_frequency",
                        "data_source",
                        "order_quantity",
                        "quantity_type",
                        "zero_momentum_action",
                        "market",
                    ],
                },
            }
        },
    }


def _persona_record(persona_id: str, *, lifecycle_state: str, status: str = "active") -> Dict[str, Any]:
    return {
        "persona_id": persona_id,
        "tenant_id": TENANT,
        "lifecycle_state": lifecycle_state,
        "status": status,
    }


class _World:
    """141 strategies + their persona records, with call counters."""

    def __init__(self) -> None:
        self.entries: List[RegistryEntry] = []
        self.persona_records: Dict[str, Dict[str, Any]] = {}
        self.running_strategy_ids: List[str] = []
        self.unbound_strategy_ids: List[str] = []
        self.retired_strategy_ids: List[str] = []
        self.owner_summary_calls: List[Tuple[str, int]] = []
        self.directory_reads: List[str] = []
        self.bundle_reads: List[str] = []
        self.bundles_by_strategy: Dict[str, Any] = {}
        self.directory_failure: Optional[Exception] = None
        self.bundle_failure: bool = False

        running_slots = {10, 40, 70, 100, 130}
        unbound_slots = {20, 80}
        for idx in range(TOTAL_COUNT):
            token = f"{idx:020x}"
            sid = f"strategy-persona-{token}"
            # newest first in the owner order, so running / unbound rows are
            # interleaved with retired ones across owner and BFF pages
            updated_at = f"2026-11-0{1 + (idx // 60)}T{(idx % 60) // 6:02d}:{idx % 6:02d}:00Z"
            if idx in unbound_slots:
                persona_ids: List[str] = []
                self.unbound_strategy_ids.append(sid)
            else:
                pid = f"persona-{token}"
                persona_ids = [pid]
                if idx in running_slots:
                    self.persona_records[pid] = _persona_record(pid, lifecycle_state="paper_running")
                    self.running_strategy_ids.append(sid)
                else:
                    # half retired through the lifecycle, half archived through status
                    if idx % 2:
                        self.persona_records[pid] = _persona_record(pid, lifecycle_state="retired")
                    else:
                        self.persona_records[pid] = _persona_record(
                            pid, lifecycle_state="paper_running", status="archived"
                        )
                    self.retired_strategy_ids.append(sid)
            self.entries.append(
                _spec_entry(
                    strategy_id=sid,
                    title=f"Pantheon Dev Paper Release {idx}",
                    persona_ids=persona_ids,
                    updated_at=updated_at,
                )
            )

    def persona_directory(self, tenant_id: Optional[str] = None, *, snapshot_at: Optional[str] = None, **_: Any):
        self.directory_reads.append(str(tenant_id))
        if self.directory_failure is not None:
            raise self.directory_failure
        return SimpleNamespace(records_by_id=dict(self.persona_records))


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    world: _World,
    *,
    wire_personas: bool = True,
) -> Tuple[FastAPI, RegistryService]:
    reg_store = RegistryStore()
    for entry in world.entries:
        reg_store._entries[entry.registry_id] = entry
    reg_service = RegistryService(store=reg_store)

    def _owner_http_stub(url: str, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        tenant_id = str(kwargs.get("tenant_id") or TENANT)
        if parsed.path == "/api/registry/strategy-specs" and qs.get("view") == ["strategy_summary_page"]:
            payload = reg_service.strategy_spec_summary_page(
                visible_tenants=frozenset({tenant_id, "__builtin__"}),
                artifact_type="strategy_spec",
                state=qs["state"][0] if "state" in qs else None,
                exclude_state=qs["exclude_state"][0] if "exclude_state" in qs else None,
                persona_id=qs["persona_id"][0] if "persona_id" in qs else None,
                page_token=qs["page_token"][0] if "page_token" in qs else None,
                page_size=int(qs["page_size"][0]) if "page_size" in qs else 20,
            )
            world.owner_summary_calls.append((f"{parsed.path}?{parsed.query}", len(payload.get("items") or [])))
            return payload
        prefix = "/api/registry/strategies/"
        suffix = "/strategy-specs"
        if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
            sid = urllib.parse.unquote(parsed.path[len(prefix) : -len(suffix)])
            return [
                {"entry": entry.to_dict(), "deployment_stage": "none"}
                for entry in world.entries
                if entry.strategy_id == sid
            ]
        raise AssertionError(f"Unexpected owner HTTP call: {url}")

    def _registry_get(base_url: str, path: str) -> Tuple[bool, Any]:
        parsed = urllib.parse.urlparse(path)
        prefix = "/api/registry/strategies/"
        suffix = "/strategy-artifacts"
        assert parsed.path.startswith(prefix) and parsed.path.endswith(suffix), path
        sid = urllib.parse.unquote(parsed.path[len(prefix) : -len(suffix)])
        world.bundle_reads.append(sid)
        if world.bundle_failure:
            return False, None
        return True, world.bundles_by_strategy.get(sid, [])

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.local")
    monkeypatch.setattr(adapter_base, "http_request_json", _owner_http_stub)

    rks_port = DefaultResearchKnowledgeSourcePort(http_get_fn=_registry_get)
    read_ports = create_read_surface_ports(research_knowledge_source=rks_port)
    persona_kwargs: Dict[str, Any] = {}
    if wire_personas:
        persona_kwargs = dict(
            persona_directory_snapshot=world.persona_directory,
            persona_record_projected_state=personas_service._persona_record_projected_state,
            persona_operational_states=personas_service._PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        )
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
            utc_now=lambda: "2026-11-07T07:40:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=personas_service._normalize_lifecycle_state,
            normalize_risk_level=personas_service._normalize_risk_level,
            bff_me_tenant_payload=lambda identity, requested_tenant=None: {"id": TENANT},
            **persona_kwargs,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    return app, reg_service


@pytest.fixture()
def world() -> _World:
    return _World()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, world: _World) -> TestClient:
    app, _ = _build_app(monkeypatch, world)
    return TestClient(app)


def _ids(body: Dict[str, Any]) -> List[str]:
    return [row["id"] for row in body["items"]]


# ---------------------------------------------------------------------------
# AC1 / AC3 / AC7: rows carry persona states, operationalStatus and symbols
# ---------------------------------------------------------------------------


def test_list_rows_carry_persona_states_operational_status_and_symbols(
    client: TestClient, world: _World
) -> None:
    resp = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_info"]["total"] == TOTAL_COUNT
    rows = {row["id"]: row for row in body["items"]}
    assert len(rows) == TOTAL_COUNT

    for sid in world.running_strategy_ids:
        row = rows[sid]
        assert row["operationalStatus"] == "running"
        assert [s["state"] for s in row["personaStates"]] == ["paper_running"]
        assert row["personaStates"][0]["personaId"] == row["personaIds"][0]
    for sid in world.unbound_strategy_ids:
        assert rows[sid]["operationalStatus"] == "unbound"
        assert rows[sid]["personaStates"] == []
    for sid in world.retired_strategy_ids:
        row = rows[sid]
        assert row["operationalStatus"] == "retired"
        assert [s["state"] for s in row["personaStates"]] == ["retired"]
    # every row carries the summary's market_scope.symbols and nothing else was read
    assert all(row["symbols"] == ["2330.TW"] for row in rows.values())
    assert all(row["alpha"] == "" for row in rows.values())
    assert world.directory_reads == [TENANT]


def test_symbols_are_empty_list_when_summary_has_none(
    monkeypatch: pytest.MonkeyPatch, world: _World
) -> None:
    world.entries.append(
        _spec_entry(
            strategy_id="strat-src-dev-product-spy-anchor-1",
            title="SPY anchor",
            persona_ids=[],
            updated_at="2026-11-07T00:00:00Z",
            symbols=[],
        )
    )
    app, _ = _build_app(monkeypatch, world)
    body = TestClient(app).get("/bff/strategies?page_size=200", headers=HEADERS).json()
    row = next(r for r in body["items"] if r["id"] == "strat-src-dev-product-spy-anchor-1")
    assert row["symbols"] == []


def test_persona_directory_is_read_once_per_list_request(client: TestClient, world: _World) -> None:
    for query in (
        "page_size=20",
        "exclude_state=draft&page_size=200",
        "exclude_state=draft&hide_retired_personas=true&page_size=3",
    ):
        world.directory_reads.clear()
        assert client.get(f"/bff/strategies?{query}", headers=HEADERS).status_code == 200
        assert world.directory_reads == [TENANT], query


# ---------------------------------------------------------------------------
# AC4 / AC5 / AC6 / AC11: hide filter, default view, counts, bounded reads
# ---------------------------------------------------------------------------


def test_hide_filter_leaves_the_seven_operational_rows(client: TestClient, world: _World) -> None:
    body = client.get(
        "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=200",
        headers=HEADERS,
    ).json()
    assert body["page_info"]["total"] == 7
    assert body["page_info"]["next_page_token"] is None
    assert sorted(_ids(body)) == sorted(world.running_strategy_ids + world.unbound_strategy_ids)
    assert body["meta"]["hidden_retired_persona_count"] == RETIRED_COUNT
    assert body["meta"]["operational_counts"] == {
        "running": RUNNING_PERSONA_COUNT,
        "idle": 0,
        "retired": RETIRED_COUNT,
        "unbound": UNBOUND_COUNT,
        "unknown": 0,
    }


def test_hide_filter_pages_across_the_filter_boundary(client: TestClient, world: _World) -> None:
    expected = set(world.running_strategy_ids + world.unbound_strategy_ids)
    seen: List[str] = []
    page_sizes: List[int] = []
    token: Optional[str] = None
    for _ in range(10):
        suffix = f"&page_token={token}" if token else ""
        body = client.get(
            "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=3" + suffix,
            headers=HEADERS,
        ).json()
        assert body["page_info"]["total"] == 7
        assert body["meta"]["total"] == 7
        assert body["meta"]["hidden_retired_persona_count"] == RETIRED_COUNT
        page_sizes.append(len(body["items"]))
        seen.extend(_ids(body))
        token = body["page_info"]["next_page_token"]
        if token is None:
            break
    assert page_sizes == [3, 3, 1]
    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == expected


def test_hide_filter_walks_owner_pages_of_200_with_one_directory_read(
    client: TestClient, world: _World
) -> None:
    resp = client.get(
        "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=3",
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert [call for call, _ in world.owner_summary_calls] == [
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=200&exclude_state=draft"
    ]
    assert world.directory_reads == [TENANT]


def test_hide_filter_walks_every_owner_page_of_a_larger_candidate_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    for idx in range(TOTAL_COUNT, 450):
        pid = f"persona-extra-{idx}"
        world.persona_records[pid] = _persona_record(pid, lifecycle_state="retired")
        world.entries.append(
            _spec_entry(
                strategy_id=f"strategy-extra-{idx}",
                title=f"extra {idx}",
                persona_ids=[pid],
                updated_at="2026-10-01T00:00:00Z",
            )
        )
    app, _ = _build_app(monkeypatch, world)
    body = TestClient(app).get(
        "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=50",
        headers=HEADERS,
    ).json()
    assert body["page_info"]["total"] == 7
    assert body["meta"]["hidden_retired_persona_count"] == 450 - 7
    assert [size for _, size in world.owner_summary_calls] == [200, 200, 50]
    assert all("page_size=200" in call for call, _ in world.owner_summary_calls)


@pytest.mark.parametrize(
    "query",
    [
        "page_size=20",
        "exclude_state=draft&page_size=20",
        "exclude_state=draft&page_size=200",
        "exclude_state=draft&page_size=20&page_token=40",
        "state=approved&page_size=50",
        "state=retired&page_size=20",
        "persona_id=persona-{persona}&page_size=20",
    ],
)
def test_without_hide_filter_rows_paging_and_state_counts_are_the_owners_own(
    monkeypatch: pytest.MonkeyPatch, world: _World, query: str
) -> None:
    app, reg_service = _build_app(monkeypatch, world)
    client = TestClient(app)
    persona = world.persona_records and next(iter(world.persona_records)).removeprefix("persona-")
    query = query.replace("{persona}", persona or "")
    params = urllib.parse.parse_qs(query)
    expected = reg_service.strategy_spec_summary_page(
        visible_tenants=frozenset({TENANT, "__builtin__"}),
        artifact_type="strategy_spec",
        state=params["state"][0] if "state" in params else None,
        exclude_state=params["exclude_state"][0] if "exclude_state" in params else None,
        persona_id=params["persona_id"][0] if "persona_id" in params else None,
        page_token=params["page_token"][0] if "page_token" in params else None,
        page_size=int(params["page_size"][0]),
    )
    body = client.get(f"/bff/strategies?{query}", headers=HEADERS).json()
    assert _ids(body) == [item["strategy_id"] for item in expected["items"]]
    assert body["page_info"] == {
        "next_page_token": expected["next_page_token"],
        "total": expected["total"],
    }
    assert body["meta"]["total"] == expected["total"]
    assert body["meta"]["state_counts"] == {
        **{k: 0 for k in strategies_service_mod.STRATEGY_DEFAULT_STATE_COUNT_KEYS},
        **expected["state_counts"],
    }
    assert body["meta"]["hidden_retired_persona_count"] == 0


def test_operational_counts_cover_the_candidate_set_before_the_hide_filter(
    client: TestClient,
) -> None:
    plain = client.get("/bff/strategies?exclude_state=draft&page_size=20", headers=HEADERS).json()
    hidden = client.get(
        "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=20",
        headers=HEADERS,
    ).json()
    expected = {
        "running": RUNNING_PERSONA_COUNT,
        "idle": 0,
        "retired": RETIRED_COUNT,
        "unbound": UNBOUND_COUNT,
        "unknown": 0,
    }
    assert plain["meta"]["operational_counts"] == expected
    assert hidden["meta"]["operational_counts"] == expected
    assert plain["meta"]["hidden_retired_persona_count"] == 0
    assert hidden["meta"]["hidden_retired_persona_count"] == RETIRED_COUNT
    assert plain["page_info"]["total"] == TOTAL_COUNT
    assert hidden["page_info"]["total"] == 7


def test_operational_counts_make_no_extra_owner_read_for_a_candidate_set_in_one_page(
    client: TestClient, world: _World
) -> None:
    client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=HEADERS)
    assert len(world.owner_summary_calls) == 1
    world.owner_summary_calls.clear()
    client.get("/bff/strategies?exclude_state=draft&page_size=20", headers=HEADERS)
    # the page read, plus one walk of the 141 candidates for the counts
    assert [call.split("?")[1].split("&")[1] for call, _ in world.owner_summary_calls] == [
        "page_size=20",
        "page_size=200",
    ]


# ---------------------------------------------------------------------------
# AC2: missing persona, failed directory read
# ---------------------------------------------------------------------------


def test_persona_missing_from_directory_gets_state_missing_and_is_idle(
    client: TestClient, world: _World
) -> None:
    gone = world.running_strategy_ids[0]
    persona = next(r for r in client.get("/bff/strategies?page_size=200", headers=HEADERS).json()["items"] if r["id"] == gone)["personaIds"][0]
    del world.persona_records[persona]
    body = client.get("/bff/strategies?page_size=200", headers=HEADERS).json()
    row = next(r for r in body["items"] if r["id"] == gone)
    assert row["personaStates"] == [{"personaId": persona, "state": "missing"}]
    assert row["operationalStatus"] == "idle"
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "ok"


def test_failed_directory_read_marks_every_row_unknown_and_hides_nothing(
    client: TestClient, world: _World
) -> None:
    world.directory_failure = RuntimeError("persona owner down")
    for query in ("page_size=200", "hide_retired_personas=true&page_size=200"):
        resp = client.get(f"/bff/strategies?{query}", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == TOTAL_COUNT
        assert len(body["items"]) == TOTAL_COUNT
        assert {row["operationalStatus"] for row in body["items"]} == {"unknown"}
        bound = [row for row in body["items"] if row["personaIds"]]
        assert all(s["state"] == "unknown" for row in bound for s in row["personaStates"])
        assert body["meta"]["surfaces"]["persona_states"]["status"] == "unavailable"
        assert body["meta"]["hidden_retired_persona_count"] == 0
        assert body["meta"]["operational_counts"]["unknown"] == TOTAL_COUNT


def test_unwired_persona_directory_is_unavailable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, world: _World
) -> None:
    app, _ = _build_app(monkeypatch, world, wire_personas=False)
    body = TestClient(app).get("/bff/strategies?page_size=20", headers=HEADERS).json()
    assert {row["operationalStatus"] for row in body["items"]} == {"unknown"}
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# AC1 / AC3: state equality with the Persona projection; one status helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lifecycle_state,status,expected",
    [
        ("paper_running", "active", "paper_running"),
        ("retired", "active", "retired"),
        ("paper_running", "archived", "retired"),
        ("draft", "active", "draft"),
        ("research_only", "active", "research_only"),
        ("paper_running", "suspended", "suspended"),
    ],
)
def test_persona_state_equals_the_state_the_persona_projection_returns(
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_state: str,
    status: str,
    expected: str,
) -> None:
    world = _World()
    sid = world.running_strategy_ids[0]
    persona = f"persona-{sid.removeprefix('strategy-persona-')}"
    world.persona_records[persona] = _persona_record(persona, lifecycle_state=lifecycle_state, status=status)
    app, _ = _build_app(monkeypatch, world)
    client = TestClient(app)

    row = next(
        r for r in client.get("/bff/strategies?page_size=200", headers=HEADERS).json()["items"] if r["id"] == sid
    )
    detail = client.get(f"/bff/strategies/{sid}", headers=HEADERS).json()["data"]
    # the Persona list route filters with exactly this projection of the same record
    persona_route_state = personas_service._persona_record_projected_state(world.persona_records[persona])
    assert persona_route_state == expected
    assert row["personaStates"] == [{"personaId": persona, "state": expected}]
    assert detail["personaStates"] == row["personaStates"]
    assert detail["operationalStatus"] == row["operationalStatus"]


def test_strategy_pages_report_the_state_projection_the_persona_list_route_filters_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /bff/personas?state= filters ``_get_persona_directory_snapshot`` records with
    ``_persona_record_projected_state``; the Strategy pages must report that same
    state for every persona of the same directory read."""
    from services.control_plane.bff.personas.routes import collection as persona_collection

    world = _World()
    states = {
        "paper_running": ("paper_running", "active"),
        "retired": ("retired", "active"),
        "archived": ("paper_running", "archived"),
        "draft": ("draft", "active"),
        "research_only": ("research_only", "active"),
        "suspended": ("paper_running", "suspended"),
    }
    owners = list(world.persona_records)[: len(states)]
    for persona, (lifecycle, status) in zip(owners, states.values()):
        world.persona_records[persona] = _persona_record(persona, lifecycle_state=lifecycle, status=status)

    # the production wiring passes the Persona route's own two functions
    assert persona_collection._persona_record_projected_state is personas_service._persona_record_projected_state
    directory = world.persona_directory(TENANT)
    persona_route_states = {
        pid: persona_collection._persona_record_projected_state(raw)
        for pid, raw in directory.records_by_id.items()
    }

    app, _ = _build_app(monkeypatch, world)
    rows = TestClient(app).get("/bff/strategies?page_size=200", headers=HEADERS).json()["items"]
    seen = {s["personaId"]: s["state"] for row in rows for s in row["personaStates"]}
    assert seen == persona_route_states
    assert {persona_route_states[p] for p in owners} == {
        "paper_running", "retired", "draft", "research_only", "suspended",
    }


def test_operational_status_helper_rules() -> None:
    operational = frozenset(personas_service._PERSONA_OPERATIONAL_LIFECYCLE_STATES)

    def lookup(states: Dict[str, str], available: bool = True) -> PersonaStateLookup:
        return PersonaStateLookup(available=available, states_by_id=states, operational_states=operational)

    assert lookup({}).operational_status([]) == "unbound"
    assert lookup({"a": "retired", "b": "retired"}).operational_status(["a", "b"]) == "retired"
    assert lookup({"a": "retired", "b": "paper_running"}).operational_status(["a", "b"]) == "running"
    assert lookup({"a": "retired", "b": "draft"}).operational_status(["a", "b"]) == "idle"
    assert lookup({"a": "research_only"}).operational_status(["a"]) == "idle"
    assert lookup({}).operational_status(["ghost"]) == "idle"
    assert lookup({}, available=False).operational_status([]) == "unknown"
    assert lookup({}, available=False).operational_status(["a"]) == "unknown"
    # frozen / suspended personas are not running even if lifecycle was operational
    assert lookup({"a": "suspended"}).operational_status(["a"]) == "idle"


# ---------------------------------------------------------------------------
# AC8: spec text on the detail DTO
# ---------------------------------------------------------------------------


def test_detail_spec_carries_title_hypothesis_objective_from_the_provisioning_shape(
    client: TestClient, world: _World
) -> None:
    sid = world.running_strategy_ids[0]
    resp = client.get(f"/bff/strategies/{sid}", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    spec = resp.json()["data"]["spec"]
    assert spec["title"] == next(e for e in world.entries if e.strategy_id == sid).metadata["title"]
    assert spec["hypothesis"] == "Paper-only lifecycle verification in dev"
    assert spec["objective"] == "Admit this Persona to paper-only evaluation with zero live capital."
    assert spec["market_scope"] == {"symbols": ["2330.TW"], "asset_classes": ["equity"], "frequency": "1d"}
    assert spec["execution_profile"]["execution_mode_hint"] == "paper"


def test_spec_projection_is_an_allowlist_and_text_fields_are_string_or_null() -> None:
    projected = _project_strategy_spec_detail(
        {
            "spec_version_id": "reg-1",
            "spec_version": "1.0.0",
            "title": "  T  ",
            "hypothesis": 12,
            "objective": "",
            "secret_internal_field": "must not leak",
            "market_scope": {"symbols": ["2330.TW"], "internal": "x"},
        }
    )
    assert projected is not None
    assert projected["title"] == "T"
    assert projected["hypothesis"] is None
    assert projected["objective"] is None
    assert "secret_internal_field" not in projected
    assert projected["market_scope"] == {"symbols": ["2330.TW"]}
    nested = _project_strategy_spec_detail(
        {"metadata": {"strategy_spec": {"hypothesis": "nested H", "objective": "nested O"}}}
    )
    assert nested is not None and nested["hypothesis"] == "nested H" and nested["objective"] == "nested O"


# ---------------------------------------------------------------------------
# AC9 / AC10: rules from approved execution bundles
# ---------------------------------------------------------------------------


def test_detail_rules_trading_and_rollback_baseline_from_lineage(
    client: TestClient, world: _World
) -> None:
    sid = world.running_strategy_ids[0]
    token = sid.removeprefix("strategy-persona-")
    trading_id = f"artifact-persona-paper-{token}"
    baseline_id = f"artifact-persona-paper-baseline-{token}"
    world.bundles_by_strategy[sid] = [
        {"entry": _bundle_entry(registry_id=trading_id, strategy_id=sid, parent_registry_ids=[baseline_id]), "deployment_stage": "none"},
        {"entry": _bundle_entry(registry_id=baseline_id, strategy_id=sid, baseline=True), "deployment_stage": "none"},
        {"entry": _bundle_entry(registry_id="artifact-candidate", strategy_id=sid, artifact_state="candidate"), "deployment_stage": "none"},
    ]
    resp = client.get(f"/bff/strategies/{sid}", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rules = {rule["registryId"]: rule for rule in body["data"]["rules"]}
    assert set(rules) == {trading_id, baseline_id}  # candidate bundle is not a rule
    assert rules[trading_id]["role"] == "trading"
    assert rules[baseline_id]["role"] == "rollback_baseline"
    trading = rules[trading_id]
    assert trading["version"] == "1.0.0"
    assert trading["strategyLogic"] == {
        "kind": "close_to_close_momentum",
        "lookback_parameter": "lookback_bars",
        "threshold_parameter": "momentum_threshold",
        "positive_action": "BUY",
        "non_positive_action": "SELL",
    }
    assert trading["parameters"]["lookback_bars"] == 2
    assert trading["parameters"]["order_quantity"] == 1
    assert [c["parameter_key"] for c in trading["adjustableControls"]] == ["lookback_bars", "momentum_threshold"]
    assert "zero_momentum_action" in trading["immutableParameters"]
    assert rules[baseline_id]["strategyLogic"]["positive_action"] == "HOLD"
    assert rules[baseline_id]["parameters"]["order_quantity"] == 0
    assert body["meta"]["surfaces"]["strategy_rules"]["status"] == "ok"
    # rules are read once per request through the per-strategy owner route
    assert world.bundle_reads == [sid]


def test_detail_rules_empty_list_without_approved_bundle(client: TestClient, world: _World) -> None:
    sid = world.running_strategy_ids[1]
    world.bundles_by_strategy[sid] = [
        {"entry": _bundle_entry(registry_id="artifact-draft", strategy_id=sid, artifact_state="draft"), "deployment_stage": "none"}
    ]
    body = client.get(f"/bff/strategies/{sid}", headers=HEADERS).json()
    assert body["data"]["rules"] == []
    assert body["meta"]["surfaces"]["strategy_rules"]["status"] == "ok"


def test_failed_bundle_read_keeps_detail_200_with_rules_null(client: TestClient, world: _World) -> None:
    sid = world.running_strategy_ids[2]
    world.bundle_failure = True
    resp = client.get(f"/bff/strategies/{sid}", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["rules"] is None
    assert body["data"]["id"] == sid
    assert body["data"]["spec"]["hypothesis"]
    assert body["meta"]["surfaces"]["strategy_rules"]["status"] == "unavailable"


def test_rule_roles_never_derive_a_registry_id_by_rewriting_another() -> None:
    entries = [
        {"entry": _bundle_entry(registry_id="artifact-x-paper", strategy_id="s", parent_registry_ids=["artifact-x-paper-baseline"])},
        # a bundle whose id merely looks like a baseline is still trading unless named as a parent
        {"entry": _bundle_entry(registry_id="artifact-y-paper-baseline", strategy_id="s")},
    ]
    rules = {r["registryId"]: r["role"] for r in project_strategy_rules(entries, strategy_id="s")}
    assert rules == {"artifact-x-paper": "trading", "artifact-y-paper-baseline": "trading"}
    # a bundle of another strategy is ignored
    assert project_strategy_rules(entries, strategy_id="other") == []


def test_detail_failed_directory_read_is_unknown_but_detail_is_200(
    client: TestClient, world: _World
) -> None:
    sid = world.running_strategy_ids[0]
    world.directory_failure = RuntimeError("down")
    resp = client.get(f"/bff/strategies/{sid}", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["operationalStatus"] == "unknown"
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "unavailable"
    assert world.directory_reads == [TENANT]


def test_default_list_overlaps_persona_directory_and_registry_page_reads(
    monkeypatch: pytest.MonkeyPatch, world: _World
) -> None:
    """The Persona directory and the registry summary page are independent
    owner reads. They must overlap, each be read exactly once, and the page
    must be unchanged."""
    import threading
    import time

    delay = 0.4
    persona_directory = world.persona_directory
    spans: Dict[str, Tuple[float, float]] = {}
    lock = threading.Lock()

    def _slow_owner(url: str, **kwargs: Any) -> Any:
        start = time.monotonic()
        time.sleep(delay)
        out = owner_http(url, **kwargs)
        with lock:
            spans.setdefault("registry", (start, time.monotonic()))
        return out

    def _slow_directory(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        time.sleep(delay)
        out = persona_directory(*args, **kwargs)
        with lock:
            spans["directory"] = (start, time.monotonic())
        return out

    monkeypatch.setattr(world, "persona_directory", _slow_directory)
    app, _ = _build_app(monkeypatch, world)
    owner_http = adapter_base.http_request_json
    monkeypatch.setattr(adapter_base, "http_request_json", _slow_owner)
    with TestClient(app) as c:
        resp = c.get("/bff/strategies?page_size=20", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    first_page_reads = [c for c in world.owner_summary_calls if "page_size=20" in c[0] and "page_size=200" not in c[0]]
    assert len(first_page_reads) == 1
    assert len(world.directory_reads) == 1
    (r0, r1), (d0, d1) = spans["registry"], spans["directory"]
    assert max(r0, d0) < min(r1, d1), "registry page and persona directory reads did not overlap"


def test_default_list_owner_page_failure_is_read_once_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, world: _World
) -> None:
    """An uncached page port must be read once: the prefetch and the handler
    share that single result or failure, never a hidden second read."""
    from fastapi import HTTPException

    app, _ = _build_app(monkeypatch, world)
    service_cls = strategies_service_mod.StrategiesService
    real_page = service_cls.list_strategy_summaries_page
    calls: List[int] = []

    def _uncached_flaky_page(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise HTTPException(status_code=503, detail="strategy_store_unavailable")
        return real_page(self, *args, **kwargs)

    monkeypatch.setattr(service_cls, "list_strategy_summaries_page", _uncached_flaky_page)
    with TestClient(app) as c:
        resp = c.get("/bff/strategies?page_size=20", headers=HEADERS)
    assert resp.status_code == 503, resp.text
    assert len(calls) == 1


def test_default_list_pool_contention_fails_truthfully_within_request_deadline(
    monkeypatch: pytest.MonkeyPatch, world: _World
) -> None:
    """With the shared read pool saturated past the request deadline, the page
    join must stop at the deadline and answer the dependency envelope, not wait
    for the pool or surface a raw 500."""
    import threading
    import time

    from services.control_plane.bff.core.owner_reads import management_read_deadline
    from services.control_plane.bff.personas.service import (
        _PERSONA_FLEET_READ_EXECUTOR as pool,
    )

    class _Deadline:
        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            tok = management_read_deadline.set(time.monotonic() + 0.1)
            try:
                await self.app(scope, receive, send)
            finally:
                management_read_deadline.reset(tok)

    app, _ = _build_app(monkeypatch, world)
    app.add_middleware(_Deadline)
    release = threading.Event()
    ready = [threading.Event() for _ in range(pool._max_workers)]

    def _occupy(event: threading.Event) -> None:
        event.set()
        release.wait(3)

    blockers = [pool.submit(_occupy, e) for e in ready]
    try:
        assert all(e.wait(1) for e in ready)
        timer = threading.Timer(0.8, release.set)
        timer.start()
        with TestClient(app, raise_server_exceptions=False) as c:
            started = time.monotonic()
            resp = c.get("/bff/strategies?page_size=20", headers=HEADERS)
            elapsed = time.monotonic() - started
    finally:
        release.set()
        timer.cancel()
        for f in blockers:
            f.result(timeout=2)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert elapsed < 0.6
