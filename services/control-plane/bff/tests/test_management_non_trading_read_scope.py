"""Management cohort regression using the real Registry and mounted routes."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.performance import service as perf
from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.management_read_models.ranking_router import create_performance_attribution_router
from services.control_plane.bff.tests.test_bff_pm12_attribution_retired_runtime_scope import _runtime, _sources
from services.control_plane.bff.tests.test_strategy_operational_status import (
    HEADERS, TOTAL_COUNT, _World, _build_app, _permissive_auth,  # noqa: F401
)


IDENTITIES = [
    {"metadata": {"persona_class": "agora_servant", "capital_mode": "paper_owner"}},
    {"personaClass": "consult_panel"},
    {"metadata": {"persona_class": "agora_advisor"}},
    {"origin": "private_servant"},
    {"metadata": {"ownerScope": "user_private"}},
    {"consultPolicyId": "consult-panel.non-trading"},
    {"agoraUserId": "trader", "execution_authority": "none"},
]


@pytest.mark.parametrize("identity", IDENTITIES)
@pytest.mark.parametrize("query", [
    "exclude_state=draft&hide_retired_personas=true",
    "hide_retired_personas=true",
    "exclude_state=draft",
])
def test_strategy_default_cohort_uses_canonical_metadata_and_one_directory_read(monkeypatch, identity, query):
    world = _World()
    hidden_sid = world.running_strategy_ids[0]
    hidden_entry = next(e for e in world.entries if e.strategy_id == hidden_sid)
    hidden_entry.metadata["title"] = "agora-servant-799a3ce8c021b91cbb54 trader paper book"
    hidden_entry.metadata["strategy_spec"]["title"] = hidden_entry.metadata["title"]
    pid = hidden_entry.metadata["persona_ids"][0]
    world.persona_records[pid].update(deepcopy(identity))
    # Opaque ordinary persona/strategy ids deliberately carry no servant pattern.
    app, _ = _build_app(monkeypatch, world)
    body = TestClient(app).get(f"/bff/strategies?{query}&page_size=200", headers=HEADERS).json()
    ids = {r["id"] for r in body["items"]}
    assert hidden_sid not in ids
    assert set(world.running_strategy_ids[1:]) <= ids  # legacy records without class metadata
    assert world.directory_reads == ["tenant-dev"]
    expected = 6 if "hide_retired_personas=true" in query else TOTAL_COUNT - 1
    assert body["page_info"]["total"] == expected
    assert body["meta"]["hidden_persona_strategy_count"] == 1
    assert body["meta"]["hidden_test_persona_strategy_count"] == 1
    assert body["meta"]["persona_exclusion_scope"] == ["test", "non_trading"]
    assert len(world.owner_summary_calls) <= 2
    assert all(pid in call[0] for call in world.owner_summary_calls)


@pytest.mark.parametrize("mixed", ["trading", "unknown", "test"])
def test_mixed_strategy_hidden_only_when_every_persona_is_excluded(monkeypatch, mixed):
    world = _World()
    sid = world.running_strategy_ids[0]
    entry = next(e for e in world.entries if e.strategy_id == sid)
    pid = entry.metadata["persona_ids"][0]
    world.persona_records[pid]["metadata"] = {"persona_class": "agora_servant"}
    second_pid = "p-extra"
    world.persona_records[second_pid] = {
        "persona_id": second_pid, "tenant_id": "tenant-dev", "lifecycle_state": "paper_running",
        "metadata": {"data_scope": "test"} if mixed == "test" else (
            {"persona_class": "trading"} if mixed == "trading" else {}
        ),
    }
    entry.metadata["persona_ids"].append(second_pid)
    entry.metadata["strategy_spec"]["persona_ids"].append(second_pid)
    app, _ = _build_app(monkeypatch, world)
    body = TestClient(app).get("/bff/strategies?hide_retired_personas=true", headers=HEADERS).json()
    assert (sid in {r["id"] for r in body["items"]}) == (mixed != "test")


def test_strategy_paging_has_no_gaps_and_failed_directory_does_not_hide(monkeypatch):
    world = _World()
    entry = next(e for e in world.entries if e.strategy_id == world.running_strategy_ids[0])
    world.persona_records[entry.metadata["persona_ids"][0]]["persona_class"] = "agora_servant"
    app, _ = _build_app(monkeypatch, world)
    client = TestClient(app)
    seen = []
    token = None
    for _ in range(3):
        params = {"hide_retired_personas": "true", "page_size": 2}
        if token:
            params["page_token"] = token
        body = client.get("/bff/strategies", params=params, headers=HEADERS).json()
        assert body["page_info"]["total"] == 6
        seen.extend(r["id"] for r in body["items"])
        token = body["page_info"]["next_page_token"]
    assert token is None
    assert len(set(seen)) == len(seen) == 6
    world.directory_failure = RuntimeError("persona owner down")
    body = client.get("/bff/strategies?hide_retired_personas=true&page_size=200", headers=HEADERS).json()
    assert body["page_info"]["total"] == TOTAL_COUNT
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "unavailable"
    assert body["meta"]["hidden_test_persona_strategy_count"] == 0
    assert body["meta"]["hidden_persona_strategy_count"] == 0
    assert body["meta"]["persona_exclusion_scope"] == ["test"]


def test_older_registry_without_owner_hide_uses_the_same_non_trading_cohort(monkeypatch):
    world = _World()
    entry = next(e for e in world.entries if e.strategy_id == world.running_strategy_ids[0])
    world.persona_records[entry.metadata["persona_ids"][0]]["persona_class"] = "agora_servant"
    app, _ = _build_app(monkeypatch, world)
    owner_http = adapter_base.http_request_json

    def older_owner(url, **kwargs):
        parsed = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parsed.query) if k not in {"hide_stubs", "hide_persona_ids"}]
        return owner_http(urlunsplit(parsed._replace(query=urlencode(query))), **kwargs)

    monkeypatch.setattr(adapter_base, "http_request_json", older_owner)
    body = TestClient(app).get("/bff/strategies?exclude_state=draft&hide_retired_personas=true", headers=HEADERS).json()
    assert body["page_info"]["total"] == 6
    assert world.running_strategy_ids[0] not in {r["id"] for r in body["items"]}
    assert world.directory_reads == ["tenant-dev"]


def _attribution_sources():
    runtimes = [_runtime(f"rt-{pid}", pid, "active") for pid in ("servant", "trading", "legacy", "test")]
    sources = _sources(runtimes, {
        runtime["runtime_id"]: {
            "pnl": 1000 if runtime["persona_id"] == "servant" else 10,
            "total_trades": 1,
            "instrument_counterfactuals": [{"decision_id": "d", "symbol": "SPY"}],
        } for runtime in runtimes
    })
    sources["personas_by_id"]["servant"]["metadata"] = {
        "persona_class": "agora_servant", "capital_mode": "paper_owner",
    }
    sources["personas_by_id"]["trading"]["metadata"] = {"persona_class": "trading"}
    sources["personas_by_id"]["test"]["metadata"] = {"data_scope": "test"}
    return sources


@pytest.mark.parametrize("suffix", ["", "/by-persona", "/by-strategy", "/by-pool"])
@pytest.mark.parametrize("include_test", [False, True])
def test_all_management_attribution_routes_filter_before_totals_and_paging(monkeypatch, suffix, include_test):
    sources = _attribution_sources()
    before = deepcopy(sources)
    source_reads = []
    graded_runtime_ids = []
    original_learning = perf.pm12_attribution_instrument_learning

    def learning(scoped_sources):
        graded_runtime_ids.append(set(scoped_sources["telemetry_by_runtime_id"]))
        return original_learning(scoped_sources)

    monkeypatch.setattr(perf, "pm12_attribution_instrument_learning", learning)

    def source_read(tenant):
        source_reads.append(tenant)
        return sources

    def response(**kw):
        return perf.pm12_performance_attribution_response(
            **kw, sources_fn=source_read, utc_now=lambda: "2026-11-27T00:00:00Z",
        )

    app = FastAPI()
    app.include_router(create_performance_attribution_router(
        extract_identity=lambda _: SimpleNamespace(roles={"viewer"}),
        require_read_role=lambda _: None,
        bff_me_tenant_payload=lambda *a, **kw: {"id": "tenant-dev"},
        pm12_performance_attribution_response=response,
    ))
    client = TestClient(app)
    params = {"page_size": 1, "include_test_personas": str(include_test).lower()}
    if not suffix:
        params["dimension"] = "runtime"
    result = client.get(f"/bff/management/performance-attribution{suffix}", params=params)
    assert result.status_code == 200, result.text
    body = result.json()
    expected_count = 3 if include_test else 2
    assert body["page_info"]["total"] == expected_count
    assert body["data"]["summary"]["total_pnl"] == expected_count * 10
    assert body["data"]["summary"]["runtime_count"] == expected_count
    assert body["meta"]["hidden_non_trading_persona_count"] == 1
    assert body["data"]["summary"]["hidden_test_persona_count"] == (0 if include_test else 1)
    assert source_reads == ["tenant-dev"]
    assert graded_runtime_ids == [{"rt-trading", "rt-legacy"} | ({"rt-test"} if include_test else set())]
    assert "servant" not in str(body["data"]["items"])
    # Other readers can still inspect the original paper book facts. No owner data is mutated.
    assert "servant" in {f["persona_id"] for f in perf.pm12_performance_attribution_facts(sources)}
    assert sources == before


def test_explicit_management_attribution_servant_drilldown_is_empty():
    sources = _attribution_sources()
    body = perf.pm12_performance_attribution_response(
        dimensions=["persona"], period="latest", page_token=None, page_size=20,
        persona_id="servant", sources_fn=lambda _: sources, include_test_personas=True,
    )
    assert body["page_info"]["total"] == 0
    assert body["data"]["items"] == []


@pytest.mark.parametrize("include_test", [False, True])
def test_combined_hidden_count_is_compatible_without_extra_reads(monkeypatch, include_test):
    world = _World()
    for sid, metadata in zip(world.running_strategy_ids[:2], [
        {"persona_class": "agora_servant"}, {"data_scope": "test"},
    ]):
        entry = next(e for e in world.entries if e.strategy_id == sid)
        world.persona_records[entry.metadata["persona_ids"][0]]["metadata"] = metadata
    app, _ = _build_app(monkeypatch, world)
    body = TestClient(app).get(
        "/bff/strategies?exclude_state=draft&page_size=200&include_test_personas="
        + str(include_test).lower(), headers=HEADERS,
    ).json()
    meta = body["meta"]
    assert meta["hidden_test_persona_strategy_count"] == meta["hidden_persona_strategy_count"] == (0 if include_test else 2)
    assert meta["persona_exclusion_scope"] == ([] if include_test else ["test", "non_trading"])
    assert meta["include_test_personas"] is include_test
    assert body["page_info"]["total"] == TOTAL_COUNT - (0 if include_test else 2)
    assert world.directory_reads == ["tenant-dev"]
    assert len(world.owner_summary_calls) <= 2


def test_strategy_registry_unavailable_keeps_dependency_error(monkeypatch):
    app, registry = _build_app(monkeypatch, _World())

    def unavailable(**kwargs):
        raise RuntimeError("Registry owner unavailable")

    monkeypatch.setattr(registry, "strategy_spec_summary_page", unavailable)
    response = TestClient(app).get("/bff/strategies?page_size=200", headers=HEADERS)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert set(body["meta"]) == {"correlationId"}
