"""Owner-shaped pool observations through the production persona router.

The pool/history/health and demand payloads come from their actual stores;
only HTTP transport and external identity verification are replaced.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.personas import cycle_views
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.ports import persona_pool as pool_port
from services.control_plane.bff.ports.ooda_management import OodaPacketsPort
from services.control_plane.ooda.ooda_loop_packet import OodaLoopPacket
from services.control_plane.persona.persona_strategy_pool import (
    AppendPoolVersionRequest, PersonaStrategyPoolStore, PoolMember,
)
from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.research.research_demand import ResearchDemandStore
from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
from services.runtime_auth_inbound import encode_jwt_hs256

TENANT, PERSONA, REF = "tenant-pool", "persona-pool", "artifact-momentum"
CHECKSUM = "sha256:" + "a" * 64
AT = "2026-11-13T08:00:00Z"
PATHS = [f"/bff/personas/{PERSONA}", f"/api/v1/operator/persona-management/{PERSONA}"]


@pytest.fixture
def world(monkeypatch, tmp_path):
    # D13 enrichment is tested with real owners in test_persona_cycle_views;
    # pool tests keep their HTTP contract focused on the three pool reads.
    monkeypatch.setattr(cycle_views, "http_request_json", lambda *a, **kw: {"status": "unavailable"})
    for env in ("PANTHEON_BFF_OODA_PACKET_STORE", "PANTHEON_OODA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "persona"))
    pools = PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda ref, tenant: {"artifact_state": "approved", "checksum": CHECKSUM},
        now=lambda: AT,
    )
    member = PoolMember(artifact_ref=REF, checksum=CHECKSUM, stage="active", risk_weight=.5,
                        baseline_ref="approved-gate-baseline", joined_at="2026-10-01T00:00:00Z")
    pools.append(PERSONA, TENANT, AppendPoolVersionRequest(
        expected_version=0, members=[member], change_reason="bootstrap"), actor_id="persona-owner")
    pools.append(PERSONA, TENANT, AppendPoolVersionRequest(
        expected_version=1, members=[member.model_copy(update={"risk_weight": .4})],
        change_reason="reduce_risk"), actor_id="persona-owner")
    pool = pools.current(PERSONA, TENANT)
    virtual_books = MemberVirtualBooks()
    for day in range(21):
        virtual_books.observe(binding_id="binding-paper", envelope_checksum="sha256:envelope",
            symbol="2330.TW", event_time=(datetime(2026, 10, 20, tzinfo=timezone.utc) + timedelta(days=day)).isoformat(),
            price=100 + day + day % 2, source_ref=f"test:recorded-mark:{day}", bar_frequency="1d",
            members={REF: {"artifact_id": REF, "version": "1.0.0", "checksum": CHECKSUM,
                           "virtual_only": False, "view": {"direction": "LONG", "strength": 1}}})
    raw_book = virtual_books.snapshot()[0]
    # CycleOwners.observe retains the full owner book and projects net-cost metrics at top level.
    book = {**raw_book, **raw_book["net_of_costs"]}
    observations = [{"date": day, "gross_notional": 40, "gross_limit": 100,
                     "drawdown": 2, "drawdown_limit": 10, "source_ref": "test:paper-observation"}
                    for day in book["dates"]]
    health = pools.health(PERSONA, TENANT, {REF: book}, observations)
    packet = OodaLoopPacket(packet_id="cycle-current", loop_type="paper_strategy", status="observing",
                            environment="paper", persona_ids=[PERSONA], created_at=AT, updated_at=AT).to_dict()
    packet.update(tenant_id=TENANT, persona_cycle={
        "phase": "terminal", "failure_reason": None,
        "observe_bundle": {"persona_id": PERSONA, "tenant_id": TENANT, "pool": deepcopy(pool),
                           "books": {REF: book}, "pool_health": health, "unavailable": []},
        "decisions": [{"kind": "no_action", "status": "staged"}],
        "case_files": {"private": "must not leak"}, "turn": {"prompt": "private prompt"},
    }, pool_autonomy={"status": "completed", "act": {"paper_only": True}})
    older = deepcopy(packet)
    older.update(packet_id="cycle-older", updated_at="2026-11-12T08:00:00Z")
    packets = [older, packet]
    demands = ResearchDemandStore(tmp_path / "demands.json", now=lambda: datetime(2026, 11, 13, tzinfo=timezone.utc))
    demand = demands.open(TENANT, {"persona_id": PERSONA, "mandate": "Taiwan equities",
                                  "short_measure": "health_inputs_unknown", "pool_version": 2})
    payloads = {
        "http://persona-owner/api/personas/" + PERSONA + "/strategy-pool": pool,
        "http://persona-owner/api/personas/" + PERSONA + "/strategy-pool/versions": {
            "versions": pools.versions(PERSONA, TENANT), "refusals": []},
        "http://research-owner/api/research/persona-demands?persona_id=" + PERSONA + "&status=open": {
            "demands": [demand], "count": 1},
    }
    calls = []
    for owner in ("PERSONA", "RESEARCH"):
        monkeypatch.setenv(f"PANTHEON_{owner}_API_URL", f"http://{owner.lower()}-owner")

    def http(url, method="GET", **kwargs):
        calls.append((url, method, kwargs))
        result = payloads[url]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    monkeypatch.setattr(pool_port, "http_request_json", http)
    ooda = OodaPacketsPort(records_provider=lambda: deepcopy(packets))
    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": [{"persona_id": PERSONA, "tenant_id": TENANT,
            "name": "Pool persona", "lifecycle_state": "paper_running", "metadata": {"tenant_id": TENANT}}]},
        ooda_management_kwargs={"ooda_port": ooda},
    )
    service = PersonaService(write_owner=store, read_store=store, ranking_write_owner=store,
        command_store=CommandStore(str(tmp_path / "commands.jsonl")),
        provisioning_store=MemoryPersonaProvisioningStore())

    def client(tenant=TENANT):
        app = FastAPI()
        app.include_router(create_personas_router(service=service,
            extract_identity_fn=lambda *args, **kwargs: OperatorIdentity(
                operator_id="reader", roles=["viewer"], mfa_verified=False,
                claims={"sub": "reader", "tenant_id": tenant}, token_kind="structured")))
        return TestClient(app)

    return SimpleNamespace(client=client, payloads=payloads, calls=calls, packets=packets,
                           packet=packet, pool=pool, book=book, health=health, ooda=ooda, store=store)


@pytest.mark.parametrize("path", PATHS)
def test_real_route_returns_owner_values(world, path):
    response = world.client().get(path, headers={"Authorization": "Bearer reader-token"})
    assert response.status_code == 200, response.text
    result = response.json()
    view = result["data"]["strategyPool"]
    assert view["current"] == world.pool
    assert [v["pool_version"] for v in view["versions"]] == [1, 2]
    assert view["memberVirtualBooks"][0] == {
        "artifact_ref": REF, "checksum": CHECKSUM, "stage": "active", "risk_weight": .4,
        "baseline_ref": "approved-gate-baseline", "status": "ok", "reason": None, "book": world.book}
    assert view["health"] == world.health
    assert view["health"]["gross_utilisation"] == {"status": "known", "value": .4, "reason": None}
    assert [c["packet_id"] for c in view["cycles"]] == ["cycle-current", "cycle-older"]
    assert view["cycles"][0]["pool_autonomy"] == world.packet["pool_autonomy"]
    assert view["openDemands"][0]["status"] == "open"
    assert "private prompt" not in response.text and "must not leak" not in response.text
    for name in ("strategy_pool", "strategy_pool_versions", "member_virtual_books", "pool_health",
                 "autonomy_cycles", "open_demands"):
        assert result["meta"]["surfaces"][name]["status"] == "ok"
    assert result["meta"]["surfaces"]["pool_health"]["observed_at"] == AT
    assert len(world.calls) == 3
    assert all(method == "GET" and kwargs["tenant_id"] == TENANT
               and kwargs["auth_token"] == "Bearer reader-token" and 0 < kwargs["timeout"] <= 2
               for _, method, kwargs in world.calls)


@pytest.mark.parametrize("path", PATHS)
def test_other_tenant_refused_before_pool_owner_reads(world, path):
    response = world.client("tenant-other").get(path)
    assert response.status_code == 404, response.text
    assert world.calls == []
    assert REF not in response.text


@pytest.mark.parametrize("code", [403, 404, 503])
def test_unavailable_pool_owner_has_no_synthetic_data(world, code):
    for url in list(world.payloads):
        if "persona-owner" in url:
            world.payloads[url] = HTTPError(url, code, "private owner error", None, io.BytesIO(b"secret"))
    response = world.client().get(PATHS[0])
    assert response.status_code == 200
    result = response.json()
    view = result["data"]["strategyPool"]
    assert view["current"] is view["versions"] is view["memberVirtualBooks"] is view["health"] is None
    assert view["openDemands"]
    assert result["meta"]["surfaces"]["strategy_pool"]["reason"] == f"owner_http_{code}"
    assert "secret" not in response.text and "private owner error" not in response.text


@pytest.mark.parametrize("target", ["current", "versions", "demands"])
def test_owner_malformed_payload_is_unavailable(world, target):
    url = list(world.payloads)[["current", "versions", "demands"].index(target)]
    world.payloads[url] = {"unexpected": []}
    result = world.client().get(PATHS[0]).json()
    key, section = {"current": ("current", "strategy_pool"), "versions": ("versions", "strategy_pool_versions"),
                    "demands": ("openDemands", "open_demands")}[target]
    assert result["data"]["strategyPool"][key] is None
    assert result["meta"]["surfaces"][section]["status"] == "unavailable"


@pytest.mark.parametrize("target", ["current", "history", "demand"])
def test_misscoped_owner_payload_is_not_exposed(world, target):
    if target == "current":
        world.payloads[list(world.payloads)[0]] = {**deepcopy(world.pool), "tenant_id": "secret-tenant"}
    elif target == "history":
        world.payloads[list(world.payloads)[1]]["versions"][0]["persona_id"] = "secret-persona"
    else:
        world.payloads[list(world.payloads)[2]]["demands"][0]["tenant_id"] = "secret-tenant"
    response = world.client().get(PATHS[0])
    assert response.status_code == 200
    assert "secret-tenant" not in response.text and "secret-persona" not in response.text
    section = {"current": "strategy_pool", "history": "strategy_pool_versions", "demand": "open_demands"}[target]
    assert response.json()["meta"]["surfaces"][section]["status"] == "unavailable"


@pytest.mark.parametrize("mismatch", ["version", "digest", "scope", "missing_observation"])
def test_latest_cycle_mismatch_does_not_fall_back_to_older_metrics(world, mismatch):
    bundle = world.packet["persona_cycle"]["observe_bundle"]
    if mismatch == "version":
        bundle["pool"]["pool_version"] = 1
    elif mismatch == "digest":
        bundle["pool"]["digest"] = "another-digest"
    elif mismatch == "scope":
        bundle["tenant_id"] = "secret-tenant"
    else:
        del world.packet["persona_cycle"]["observe_bundle"]
    result = world.client().get(PATHS[0]).json()
    assert result["data"]["strategyPool"]["health"] is None
    assert result["data"]["strategyPool"]["memberVirtualBooks"] is None
    assert result["meta"]["surfaces"]["pool_health"]["status"] == "unavailable"


def test_book_checksum_mismatch_keeps_member_but_hides_wrong_metrics(world):
    world.packet["persona_cycle"]["observe_bundle"]["books"][REF]["checksum"] = "wrong"
    result = world.client().get(PATHS[0]).json()
    row = result["data"]["strategyPool"]["memberVirtualBooks"][0]
    assert row["artifact_ref"] == REF and row["baseline_ref"] == "approved-gate-baseline"
    assert row["status"] == "unavailable" and row["book"] is None
    assert result["meta"]["surfaces"]["member_virtual_books"]["status"] == "unavailable"


def test_cycle_scope_and_paper_boundary(world):
    for key, value in [("tenant_id", "secret-tenant"), ("persona_ids", ["secret-persona"]), ("environment", "live")]:
        other = deepcopy(world.packet)
        other.update(packet_id="secret-packet", updated_at="2026-11-14T08:00:00Z", **{key: value})
        world.packets.append(other)
    response = world.client().get(PATHS[0])
    assert "secret-" not in response.text
    assert len(response.json()["data"]["strategyPool"]["cycles"]) == 2


def test_empty_cycle_owner_differs_from_unavailable_owner(world):
    world.packets.clear()
    result = world.client().get(PATHS[0]).json()
    assert result["data"]["strategyPool"]["cycles"] == []
    assert result["data"]["strategyPool"]["health"] is None
    world.store.ooda_management.ooda = OodaPacketsPort()
    result = world.client().get(PATHS[0]).json()
    assert result["data"]["strategyPool"]["cycles"] is None
    assert result["meta"]["surfaces"]["autonomy_cycles"]["reason"] == "cycle_owner_unavailable"


def test_empty_demands_are_valid_and_owner_network_failure_is_explicit(world):
    url = list(world.payloads)[2]
    world.payloads[url] = {"demands": [], "count": 0}
    assert world.client().get(PATHS[0]).json()["data"]["strategyPool"]["openDemands"] == []
    world.payloads[url] = URLError("private connection string")
    response = world.client().get(PATHS[0])
    assert response.json()["data"]["strategyPool"]["openDemands"] is None
    assert "private connection string" not in response.text


def test_unknown_health_is_preserved_and_incomplete_book_is_unavailable(world):
    health = world.packet["persona_cycle"]["observe_bundle"]["pool_health"]
    health["effective_bets"] = {"status": "unknown", "value": None, "reason": "virtual_book_window_missing"}
    world.packet["persona_cycle"]["observe_bundle"]["books"][REF]["marks_complete"] = False
    result = world.client().get(PATHS[0]).json()
    view = result["data"]["strategyPool"]
    assert view["health"]["effective_bets"] == health["effective_bets"]
    assert view["memberVirtualBooks"][0]["book"] is None
    assert result["meta"]["surfaces"]["member_virtual_books"]["status"] == "unavailable"


def test_unconfigured_http_owners_are_explicit(world, monkeypatch):
    for owner in ("PERSONA", "RESEARCH"):
        monkeypatch.delenv(f"PANTHEON_{owner}_API_URL")
        monkeypatch.delenv(f"PANTHEON_{owner}_SERVICE_URL", raising=False)
    # Restore real production HTTP entry; configuration must fail before any network I/O.
    from services.control_plane.bff.command_adapters.base import http_request_json
    monkeypatch.setattr(pool_port, "http_request_json", http_request_json)
    result = world.client().get(PATHS[0]).json()
    view = result["data"]["strategyPool"]
    assert view["current"] is view["versions"] is view["openDemands"] is None
    assert result["meta"]["surfaces"]["strategy_pool"]["status"] == "unavailable"


def test_real_http_transport_forwards_authenticated_tenant(world, monkeypatch):
    from services.control_plane.bff.command_adapters.base import http_request_json
    monkeypatch.setattr(pool_port, "http_request_json", http_request_json)
    requests = []

    class Response(io.BytesIO):
        status = 200

    def urlopen(request, timeout):
        requests.append(request)
        return Response(json.dumps(world.payloads[request.full_url]).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    token = encode_jwt_hs256({"sub": "reader", "roles": ["viewer"], "tenant_id": TENANT,
                              "exp": 4102444800}, secret="pool-view-test-secret")
    response = world.client().get(PATHS[0], headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["strategyPool"]["current"] == world.pool
    assert len(requests) == 3
    assert all(r.get_method() == "GET" and r.get_header("X-tenant-id") == TENANT
               and r.get_header("Authorization") == "Bearer " + token for r in requests)


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_production_default_reads_real_persona_cycle_owner_records(world, monkeypatch, tmp_path, suffix):
    from services.persona.cron_ooda_closure import append_ooda_packet
    path = tmp_path / ("daily-cycles" + suffix)
    append_ooda_packet(path, world.packet)
    before = path.read_bytes()
    world.store.ooda_management.ooda = OodaPacketsPort()  # production default
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(path))
    result = world.client().get(PATHS[0]).json()
    view = result["data"]["strategyPool"]
    assert view["cycles"][0]["packet_id"] == "cycle-current"
    assert view["memberVirtualBooks"][0]["book"] == world.book
    assert view["health"] == world.health
    assert path.read_bytes() == before  # read-only, owner record never rewritten


@pytest.mark.parametrize("contents", [None, "broken json"])
def test_production_missing_or_corrupt_cycle_store_fails_closed(world, monkeypatch, tmp_path, contents):
    path = tmp_path / "daily-cycles.json"
    if contents is not None:
        path.write_text(contents)
    world.store.ooda_management.ooda = OodaPacketsPort()
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(path))
    result = world.client().get(PATHS[0]).json()
    view = result["data"]["strategyPool"]
    assert view["cycles"] is view["health"] is view["memberVirtualBooks"] is None
    assert result["meta"]["surfaces"]["autonomy_cycles"]["status"] == "unavailable"
    assert path.exists() == (contents is not None)
