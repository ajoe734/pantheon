"""Linked population compatibility and production BFF routes against PostgreSQL."""
from __future__ import annotations

import concurrent.futures
import copy
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.tests.test_bff_pm12_persona_league import HEADERS, _fresh_client
from services.control_plane.bff.tests.test_quarterly_ranking_read_latency import (
    _Owner, _OwnerBackedStore, _get, _linked_refs, _strip_volatile, ROUTES,
)
from services.source_ingestion.tests.test_evidence_read_cost import (
    TYPES, VOLUME_TENANT, record_measurement, volume_repo,
)

WINDOW = {"start_at": "2026-01-01T00:00:00Z", "end_exclusive_at": "2026-04-01T00:00:00Z"}


class PopulationOwner:
    def __init__(self, mode="new", fail_offset=None):
        self.mode, self.fail_offset, self.calls = mode, fail_offset, []
        self.rows = [dict(evidence_item_id=f"raw-{i}", item_type="filing", metadata={},
                          available_time="2026-02-15T00:00:00Z") for i in range(900)]
        self.rows += [dict(evidence_item_id=f"linked-{i}", item_type="filing", metadata={
            "linked_object_summary": {"entity_type": "persona", "entity_ref": "p-001"}},
            available_time="2026-02-15T00:00:00Z") for i in range(600)]

    def http_get(self, base, path):
        self.calls.append(path)
        q = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        offset, size = int(q["page_token"]), int(q["page_size"])
        if offset == self.fail_offset:
            return False, None
        hints = "linked_entity_types" in q
        new = self.mode == "new" or (self.mode == "mixed" and offset == 0)
        rows = self.rows[900:] if hints and new else self.rows
        filters = {}
        if new and hints:
            filters = {"linked_entity_types": q["linked_entity_types"].split(","), "include_summary": False}
        elif self.mode == "partial" and hints:
            # Implements the entity filter, but ignores summary suppression.
            rows = self.rows[900:]
            filters = {"linked_entity_types": q["linked_entity_types"].split(",")}
        return True, {"items": rows[offset:offset + size], "total": len(rows), "filters": filters}


def read_owner(owner):
    port = DefaultResearchKnowledgeSourcePort(source_ingest_service_url="http://owner.test", http_get_fn=owner.http_get)
    return personas_service._pm12_read_all_evidence_pages(port.read_evidence_refs, WINDOW)


@pytest.mark.parametrize("mode", ["old", "partial", "mixed"])
def test_unsupported_or_mixed_version_owner_restarts_one_complete_population(mode):
    owner = PopulationOwner(mode)
    rows = read_owner(owner)
    assert rows is not None
    assert len(rows) == len({r["ref_id"] for r in rows}) == 1500
    assert {r["ref_id"] for r in rows} == {r["evidence_item_id"] for r in owner.rows}
    # Legacy fallback is request-local; a future request can use the new owner.
    owner.mode, owner.calls = "new", []
    rows = read_owner(owner)
    assert len(rows) == 600 and len(owner.calls) == 2


def test_any_filtered_owner_page_failure_stays_unavailable():
    assert read_owner(PopulationOwner(fail_offset=500)) is None


@pytest.mark.parametrize("route", ROUTES)
def test_linked_unlinked_fixture_keeps_ranked_items_attachments_and_redaction(route, monkeypatch):
    # Native BFF evidence includes linked, unlinked and restricted rows. These
    # are already projected refs; source-ingest metadata is deliberately not
    # promoted into new BFF links by this performance change.
    refs = _linked_refs("linked", 501, "2026-02-15T00:00:00Z")
    refs += [{**r, "linked_object_summary": {}} for r in _linked_refs("unlinked", 50, "2026-02-15T00:00:00Z")]
    refs += [{**r, "evidence_type": "policy", "linked_object_summary": {"entity_type": "persona", "entity_ref": "p-001"}}
             for r in _linked_refs("restricted", 3, "2026-02-15T00:00:00Z")]
    original = _OwnerBackedStore.__init__

    def seeded(self, owner, persona_count=166):
        original(self, owner, persona_count)
        self._port._evidence_refs = {r["ref_id"]: copy.deepcopy(r) for r in refs}

    monkeypatch.setattr(_OwnerBackedStore, "__init__", seeded)
    optimized = _get(_Owner(0), route)
    monkeypatch.setattr(personas_service, "_PM12_QUARTER_EVIDENCE_PAGE_SIZE", None)
    previous = _get(_Owner(0), route)
    assert _strip_volatile(optimized) == _strip_volatile(previous)
    assert "linked-0000" in repr(optimized), "attached evidence must be nonempty"
    assert "redacted" in repr(optimized)
    assert "insufficient_capability" in repr(optimized)


def test_real_postgres_owner_and_four_concurrent_bff_routes(volume_repo, monkeypatch, tmp_path):
    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

    repo = volume_repo
    owner_app = FastAPI()
    owner_app.include_router(create_ingest_operations_router(SimpleNamespace(evidence_repository=repo)))
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "local-cost-test")
    token = encode_jwt_hs256({"sub": "cost-test", "roles": ["operator"], "tenant_id": VOLUME_TENANT,
                             "exp": int(time.time()) + 600}, secret="local-cost-test")
    owner_client = TestClient(owner_app, headers={"Authorization": f"Bearer {token}"})
    requests, summary_calls = [], []
    summarize = repo.summarize_evidence_items

    def counted(*a, **kw):
        summary_calls.append(True)
        return summarize(*a, **kw)

    monkeypatch.setattr(repo, "summarize_evidence_items", counted)

    class ActualOwner:
        def http_get(self, base, path):
            start = time.monotonic()
            response = owner_client.get(path)
            body = response.json()
            requests.append({"path": path, "seconds": time.monotonic() - start,
                             "status": response.status_code, "items": len(body.get("items", [])),
                             "ids": [r["evidence_item_id"] for r in body.get("items", [])],
                             "error": body if response.status_code != 200 else None})
            return response.status_code == 200, body

    owner = ActualOwner()
    started = time.monotonic()
    rows = read_owner(owner)
    elapsed = time.monotonic() - started
    assert rows is not None and len(rows) == 1800, requests
    assert len(requests) == 4 and not summary_calls
    assert elapsed < 1.0, elapsed
    assert all(int(r.split("-")[1]) >= 7200 for request in requests for r in request["ids"])
    record_measurement("quarter_owner", {"seconds": elapsed, "pages": len(requests), "rows": len(rows), "summary_calls": len(summary_calls)})

    store = _OwnerBackedStore(owner)
    client = _fresh_client(str(tmp_path), store=store)
    routes = ("/bff/management/persona-league", "/bff/management/persona-league/rankings", *ROUTES[:2])
    requests.clear()

    def get(route):
        start = time.monotonic()
        response = client.get(route, headers=HEADERS, params={"quarter": "2026-Q1", "page_size": 200, "limit": 200})
        return {"route": route, "seconds": time.monotonic() - start, "status": response.status_code,
                "item_count": len(response.json().get("data", {}).get("items", []))}

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(get, routes))
    wall = time.monotonic() - started
    assert all(r["status"] == 200 and r["seconds"] < 6.0 and r["item_count"] >= 166 for r in results), results
    assert wall < 6.0 and not summary_calls
    assert all("include_summary=false" in r["path"] and r["status"] == 200 for r in requests)
    record_measurement("concurrent_bff", {"wall_seconds": wall, "routes": results,
        "owner_requests": len(requests), "owner_request_seconds": [r["seconds"] for r in requests],
        "summary_calls": len(summary_calls), "transport": "in-process HTTP TestClient; real PostgreSQL 16"})
