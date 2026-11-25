"""REGISTRY-LINEAGE-OWNER-READ-20261104: every BFF lineage entry reads the registry owner through one port.

A real local HTTP server plays the registry owner, serving the registry's own
``build_lineage_graph`` over the real hosted persona-family row shape; the BFF
port, router and consumers run unmodified against it.
"""
from __future__ import annotations

import base64
import inspect
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.lineage import create_lineage_router
from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.core.display_names import configure_display_name_source
from services.control_plane.bff.evolution.service import ew04_inspiration_projection_from_lineage_edges
from services.control_plane.bff.ports import ReadSurfacePorts
from services.control_plane.bff.ports.lifecycle_telemetry_governance import DomainLineagePort
from services.registry.storage import build_lineage_graph, lineage_row, persona_binding_row
from services.registry.test_lineage_read import (
    FD,
    LIVE_ARTIFACT_ID,
    LIVE_PERSONA_ID,
    LIVE_POOL_ID,
    LIVE_RECORDED_ROOTS,
    LIVE_STRATEGY_ID,
    LIVE_STRATEGY_TITLE,
    LIVE_TOKEN,
    live_persona_family_entries,
    persona_family_entries,
)

ART = f"artifact-persona-paper-{FD}"


def _jwt(tenant: str) -> str:
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"Bearer {seg({'alg': 'none'})}.{seg({'tenant_id': tenant})}.sig"


class _Owner:
    def __init__(self, rows: list[dict], binding_rows: Optional[list[dict]] = None):
        self.rows, self.requests = rows, []
        self.binding_rows = list(binding_rows or [])
        self.fail_strategy_specs = False
        owner = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                owner.requests.append((u.path, q, dict(self.headers)))
                if u.path == "/api/registry/strategy-specs":
                    if owner.fail_strategy_specs:
                        self.send_response(503)
                        self.end_headers()
                        return
                    body: Any = owner.binding_rows
                else:
                    body = build_lineage_graph(
                        owner.rows, root_id=q.get("root_id"), depth=int(q.get("depth", 3)), limit=int(q.get("limit", 200)),
                    )
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def owner(monkeypatch):
    entries = persona_family_entries()
    o = _Owner(
        [lineage_row(e) for e in entries],
        [persona_binding_row(e) for e in entries if e.artifact_type.value == "strategy_spec"],
    )
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", o.url)
    tok = owner_reads.authorization.set(_jwt("tenant-a"))
    tt = owner_reads.selected_tenant.set("tenant-a")
    yield o
    owner_reads.authorization.reset(tok)
    owner_reads.selected_tenant.reset(tt)
    o.close()


def _client(store: Any) -> TestClient:
    app = FastAPI()
    app.include_router(create_lineage_router(
        read_surface=store, extract_identity=lambda a=None: {"roles": ["operator"]},
        require_read_role=lambda i: None, snapshot_meta=lambda t: {"snapshot_at": t},
        utc_now=lambda: "2026-11-04T00:00:00Z",
    ))
    return TestClient(app)


def test_bff_lineage_returns_family_chain_from_owner(owner):
    store = ReadSurfacePorts()
    body = _client(store).get(f"/bff/lineage?root_id=strategy-persona-{FD}&depth=3").json()
    assert body["meta"]["status"] == "ok" and body["meta"]["source"] == "service_client"
    edges = {(e["from"], e["to"], e["label"]) for e in body["data"]["edges"]}
    assert (f"reg-strategy-spec-persona-baseline-{FD}", f"reg-strategy-spec-persona-{FD}", "parent") in edges
    assert (f"reg-strategy-spec-persona-{FD}", ART, "source_strategy_spec") in edges
    assert (f"artifact-persona-paper-baseline-{FD}", ART, "parent") in edges
    assert (ART, f"persona-{FD}", "persona") in edges and (ART, f"pool-{FD}", "capital_pool") in edges
    types = {n["id"]: n["type"] for n in body["data"]["nodes"]}
    assert types[f"persona-{FD}"] == "Persona" and types[f"pool-{FD}"] == "CapitalPool" and types[ART] == "Artifact"
    assert all("from_node" not in e for e in body["data"]["edges"])
    path, query, headers = owner.requests[0]
    assert path == "/api/registry/lineage" and query["root_id"] == f"strategy-persona-{FD}"
    assert headers.get("X-Tenant-Id") == "tenant-a" and headers.get("Authorization", "").startswith("Bearer ")


def test_real_empty_graph_is_ok_with_zero_nodes(owner):
    owner.rows = []
    body = _client(ReadSurfacePorts()).get("/bff/lineage").json()
    assert body["meta"]["status"] == "ok" and body["data"]["nodes"] == [] and body["data"]["edges"] == []


def test_owner_connection_failure_reports_unavailable(monkeypatch):
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()  # closed port: real ECONNREFUSED
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", f"http://127.0.0.1:{port}")
    tok = owner_reads.authorization.set(_jwt("tenant-a"))
    try:
        store = ReadSurfacePorts()
        body = _client(store).get("/bff/lineage").json()
        assert body["meta"]["status"] == "unavailable"
        assert body["data"]["status"] == "unavailable" and body["data"]["nodes"] == []
        # same-context read (the request outcome lives in the request's context)
        assert store.get_lineage_graph(root_id=ART) == []
        assert store.dataset_source("lineage_edges") == "unavailable"
    finally:
        owner_reads.authorization.reset(tok)


def test_unconfigured_owner_is_unavailable(monkeypatch):
    monkeypatch.delenv("PANTHEON_REGISTRY_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_REGISTRY_URL", raising=False)
    assert ReadSurfacePorts().dataset_source("lineage_edges") == "unavailable"


def test_dataset_source_reports_real_outcome_not_typed_store(owner):
    store = ReadSurfacePorts()
    store.get_lineage_graph(root_id=ART)
    assert store.dataset_source("lineage_edges") == "service_client"


def test_all_entries_share_the_one_port(owner):
    store = ReadSurfacePorts()
    # strategy lineage, evolution list/edge/graph and the incident edge read all use this port
    assert store.lifecycle_telemetry_governance.lineage.__class__ is DomainLineagePort
    edges = store.list_lineage_edges(artifact_id=ART)
    assert edges and all(ART in (e["from_artifact_id"], e["to_artifact_id"]) for e in edges)
    one = store.get_lineage_edge(edges[0]["id"])
    assert one and one["id"] == edges[0]["id"]
    assert store.lifecycle_telemetry_governance.artifact_exists(ART) is True
    assert store.lifecycle_telemetry_governance.artifact_exists("missing-artifact") is False
    projection = ew04_inspiration_projection_from_lineage_edges(ART, store, utc_now=lambda: "t")
    assert projection and {e["source_artifact_id"] for e in projection["inspiration_edges"]} >= {
        f"reg-strategy-spec-persona-{FD}", f"artifact-persona-paper-baseline-{FD}"}


def test_no_unpopulated_in_process_edge_list_on_production_path():
    assert "lineage_edges" not in inspect.signature(DomainLineagePort.__init__).parameters
    assert not hasattr(DomainLineagePort(), "_lineage_edges")
    assert not hasattr(ReadSurfacePorts().lifecycle_telemetry_governance.lineage, "_lineage_edges")


def test_live_persona_four_roots_resolve_display_names_with_one_bounded_read(owner):
    all_entries = persona_family_entries() + live_persona_family_entries()
    owner.rows = [lineage_row(e) for e in all_entries]
    owner.binding_rows = [
        persona_binding_row(e) for e in all_entries if e.artifact_type.value == "strategy_spec"
    ]
    store = ReadSurfacePorts()
    persona_calls = []

    def _list_personas(**_kw):
        persona_calls.append(1)
        return [{"persona_id": LIVE_PERSONA_ID, "display_name": "衡岳"}]

    store.list_personas = _list_personas
    client = _client(store)

    graphs_by_root = {}
    for root in LIVE_RECORDED_ROOTS:
        owner.requests.clear()
        persona_calls.clear()
        resp = client.get(f"/bff/lineage?root_id={root}&depth=3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["status"] == "ok"
        assert body["meta"]["surfaces"]["lineage"]["status"] == "ok"
        assert body["meta"]["surfaces"]["display_names"]["status"] == "ok"
        # Bounded reads per request: 1 lineage read + 1 strategy label read + 1 persona read
        assert [r[0] for r in owner.requests] == [
            "/api/registry/lineage",
            "/api/registry/strategy-specs",
        ]
        assert len(persona_calls) == 1
        graphs_by_root[root] = body["data"]

    first = graphs_by_root[LIVE_STRATEGY_ID]
    expected_node_ids = {n["id"] for n in first["nodes"]}
    expected_edges = {(e["from"], e["to"], e["label"]) for e in first["edges"]}
    for root, data in graphs_by_root.items():
        assert {n["id"] for n in data["nodes"]} == expected_node_ids, root
        assert {(e["from"], e["to"], e["label"]) for e in data["edges"]} == expected_edges, root

    by_id = {n["id"]: n for n in first["nodes"]}
    for nid, node in by_id.items():
        assert node["label"] == nid
        assert node["name_missing"] is False, node
        assert node["display_name"], node

    assert by_id[LIVE_PERSONA_ID]["display_name"] == "衡岳"
    assert by_id[LIVE_ARTIFACT_ID]["display_name"] == LIVE_STRATEGY_TITLE
    assert by_id[LIVE_ARTIFACT_ID]["strategy_id"] == LIVE_STRATEGY_ID
    assert by_id[LIVE_ARTIFACT_ID]["version"] == "1.2.0"
    assert by_id[f"reg-strategy-spec-persona-{LIVE_TOKEN}"]["display_name"] == LIVE_STRATEGY_TITLE
    assert by_id[f"reg-strategy-spec-persona-{LIVE_TOKEN}"]["version"] == "1.0.1"
    assert by_id[LIVE_POOL_ID]["display_name"] == f"{LIVE_STRATEGY_TITLE} (CapitalPool)"
    assert by_id[f"persona-provisioning-forward-{LIVE_TOKEN}"]["display_name"] == f"{LIVE_STRATEGY_TITLE} (Experiment)"


def test_failed_display_name_resolver_leaves_graph_readable_with_degraded_surface(owner):
    entries = live_persona_family_entries()
    owner.rows = [lineage_row(e) for e in entries]
    owner.binding_rows = [persona_binding_row(e) for e in entries if e.artifact_type.value == "strategy_spec"]
    owner.fail_strategy_specs = True
    store = ReadSurfacePorts()
    store.list_personas = lambda **_kw: (_ for _ in ()).throw(RuntimeError("persona owner down"))

    body = _client(store).get(f"/bff/lineage?root_id={LIVE_STRATEGY_ID}&depth=3").json()
    assert body["data"]["status"] == "degraded"
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["surfaces"]["lineage"]["status"] == "degraded"
    assert body["meta"]["surfaces"]["display_names"]["status"] == "degraded"
    assert body["data"]["edges"]
    assert body["data"]["nodes"]
    for node in body["data"]["nodes"]:
        assert node["label"] == node["id"]
        assert node["display_name"] is None
        assert node["name_missing"] is True
