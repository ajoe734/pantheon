"""EVIDENCE-OWNER-LIST-BOUNDED-20261104.

Every fixture row below has the real hosted shape (tw_price_daily / TWSE / 0050,
~12 KB ``raw_storage_refs`` and ~4.5 KB ``calendar_evidence`` per row). No
synthetic row shape is used to prove behaviour.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models.router import (
    _default_bff_error,
    _default_extract_identity,
    _default_require_read_role,
    create_management_router,
)
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    normalize_source_ingest_evidence_item,
)
from services.control_plane.bff.research.router import create_research_router
from services.knowledge.evidence.models import EvidenceItem, summarize_evidence_items_collection
from services.knowledge.evidence.repository import InMemoryEvidenceRepository

ADMIN_HEADERS = {"Authorization": "Bearer op-evid:admin"}
OWNER_URL = "http://source-ingest.test"
TENANT = "tenant-dev"
OWNER_BUDGET_SECONDS = 4.0  # the BFF client timeout in _default_http_get


def real_row(i: int) -> Dict[str, Any]:
    """The real hosted row shape, parameterised only by the day index."""
    day = f"2026-11-{(i % 28) + 1:02d}"
    return {
        "evidence_item_id": f"evi-{i:016x}",
        "source_id": f"src-{i:016x}",
        "item_type": "source_record",
        "content_ref": f"tw-official://tw_price_daily/TWSE/0050/{day}/{i:016x}",
        "citation_label": f"TW official tw_price_daily TWSE 0050 {day}",
        "body": f"TW official tw_price_daily TWSE 0050 {day}",
        "event_time": day,
        "available_time": f"{day}T14:30:00+08:00",
        "confidence": 1.0,
        "access_scope": ["public", "research"],
        "trace_refs": [f"trace://tw_price_daily/{i}"],
        "metadata": {
            "tenant_id": TENANT,
            "source_class": "official_reference",
            "dataset": "tw_price_daily",
            "symbol": "0050",
            "venue": "TWSE",
            "provider": "TWSE/TPEx",
            "raw_storage_refs": [
                {"uri": f"gs://pantheon-raw/tw_price_daily/{i}/{n}", "sha256": "ab" * 32, "note": "x" * 90}
                for n in range(90)
            ],
            "calendar_evidence": {
                "sessions": [{"date": day, "open": "09:00", "close": "13:30", "note": "y" * 60} for _ in range(40)]
            },
            "normalized_row": {"open": 190.1, "high": 191.0, "low": 189.5, "close": 190.8, "volume": 1234567},
        },
    }


def owner_repo(n: int) -> InMemoryEvidenceRepository:
    repo = InMemoryEvidenceRepository()
    for i in range(n):
        item = EvidenceItem.from_dict(real_row(i))
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[(TENANT, item.evidence_item_id)] = item
    return repo


# --------------------------------------------------------------------------
# Owner route
# --------------------------------------------------------------------------
@pytest.fixture()
def owner_client(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "evid-secret")

    def build(n: int) -> Tuple[TestClient, InMemoryEvidenceRepository]:
        repo = owner_repo(n)
        app = FastAPI()
        app.include_router(create_ingest_operations_router(SimpleNamespace(evidence_repository=repo)))
        token = encode_jwt_hs256(
            {"sub": "r", "roles": ["operator"], "tenant_id": TENANT, "exp": int(time.time()) + 600},
            secret="evid-secret",
        )
        return TestClient(app, headers={"Authorization": f"Bearer {token}"}), repo

    return build


@pytest.mark.parametrize("rows", [614, 5000])
def test_owner_first_page_is_bounded_and_fast(owner_client, rows: int) -> None:
    client, repo = owner_client(rows)
    before_bytes = len(json.dumps({"items": [i.to_dict() for i in repo.list_evidence_items(TENANT)]}))

    started = time.perf_counter()
    resp = client.get("/api/source-ingest/evidence/items")
    elapsed = time.perf_counter() - started

    assert resp.status_code == 200
    body = resp.json()
    after_bytes = len(resp.content)
    assert body["total"] == rows
    assert len(body["items"]) == 200
    assert after_bytes < 1_000_000, (before_bytes, after_bytes)
    assert before_bytes > 5_000_000  # the unbounded list really was MBs of this row shape
    assert elapsed < OWNER_BUDGET_SECONDS
    for item in body["items"]:
        assert "raw_storage_refs" not in item["metadata"]
        assert "calendar_evidence" not in item["metadata"]
        assert item["metadata"]["normalized_row"]["close"] == 190.8


def test_owner_pages_cover_every_row_once_and_detail_stays_full(owner_client) -> None:
    client, _repo = owner_client(614)
    seen: List[str] = []
    token: Optional[str] = None
    while True:
        params = {"page_size": 200, **({"page_token": token} if token else {})}
        body = client.get("/api/source-ingest/evidence/items", params=params).json()
        seen.extend(i["evidence_item_id"] for i in body["items"])
        token = body["next_page_token"]
        if not token:
            break
    assert len(seen) == 614 and len(set(seen)) == 614

    detail = client.get(f"/api/source-ingest/evidence/items/{seen[0]}").json()["item"]
    assert len(detail["metadata"]["raw_storage_refs"]) == 90
    assert detail["metadata"]["calendar_evidence"]["sessions"]


# --------------------------------------------------------------------------
# Shared normalizer (real row)
# --------------------------------------------------------------------------
def test_normalizer_maps_real_row_without_inventing_time() -> None:
    row = real_row(3)
    out = normalize_source_ingest_evidence_item(row)
    assert out["ref_id"] == row["evidence_item_id"]
    assert out["display_label"] == row["citation_label"]
    assert out["source_document"]["title"] == row["citation_label"]
    assert out["summary"] == row["body"]
    assert out["source_document"]["captured_at"] == row["available_time"]
    assert out["source_document"]["source_ref"] == row["content_ref"]
    assert out["source_document"]["source_type"] == "official_reference"
    assert out["credibility"] == {"tier": "verified", "verified": True}

    no_available = dict(row, available_time=None)
    assert normalize_source_ingest_evidence_item(no_available)["source_document"]["captured_at"] == row["event_time"]
    neither = dict(row, available_time=None, event_time=None)
    assert normalize_source_ingest_evidence_item(neither)["source_document"]["captured_at"] is None
    weak = dict(row, confidence=0.5)
    assert normalize_source_ingest_evidence_item(weak)["credibility"] == {"tier": "unverified", "verified": False}


def test_repository_branch_uses_the_same_normalizer() -> None:
    repo = owner_repo(2)
    port = DefaultResearchKnowledgeSourcePort(evidence_repository=repo)
    refs = port.list_evidence_refs()
    detail = port.get_evidence_ref_detail("evi-0000000000000001")
    expected = normalize_source_ingest_evidence_item(real_row(1))
    assert detail["display_label"] == expected["display_label"]
    assert detail["source_document"]["captured_at"] == expected["source_document"]["captured_at"]
    assert all(r["source_document"]["captured_at"] for r in refs)
    assert {r["source_document"]["source_type"] for r in refs} == {"official_reference"}


# --------------------------------------------------------------------------
# Fake owner over HTTP-shaped calls (real rows, owner paging semantics)
# --------------------------------------------------------------------------
class FakeOwner:
    def __init__(self, rows: int) -> None:
        self.rows = rows
        self.calls: List[str] = []
        self.fail = False
        self.fail_summary = False
        self._summary = summarize_evidence_items_collection(real_row(i) for i in range(rows))

    def __call__(self, base_url: str, path: str) -> Tuple[bool, Any]:
        self.calls.append(path)
        if self.fail:
            return False, None
        parsed = urlparse(path)
        if parsed.path.endswith("/evidence/items"):
            q = parse_qs(parsed.query)
            size = int(q.get("page_size", ["200"])[0])
            start = int(q.get("page_token", ["0"])[0])
            items = []
            for i in range(start, min(start + size, self.rows)):
                row = real_row(i)
                row["metadata"].pop("raw_storage_refs")
                row["metadata"].pop("calendar_evidence")
                items.append(row)
            nxt = start + size
            return True, {
                "items": items,
                "total": self.rows,
                "page_size": size,
                "next_page_token": str(nxt) if nxt < self.rows else None,
                "summary": None if self.fail_summary else self._summary,
            }
        item_id = parsed.path.rsplit("/", 1)[-1]
        i = int(item_id.removeprefix("evi-"), 16)
        return (True, {"item": real_row(i)}) if i < self.rows else (False, None)


def mounted(store: Any) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_management_router(read_surface=store))
    app.include_router(
        create_research_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=_default_bff_error,
            utc_now=lambda: "2026-11-04T00:00:00Z",
        )
    )
    return TestClient(app)


def store_with(owner_get) -> Tuple[Any, DefaultResearchKnowledgeSourcePort]:
    ks = DefaultResearchKnowledgeSourcePort(source_ingest_service_url=OWNER_URL, http_get_fn=owner_get)
    return create_read_surface_ports(research_knowledge_source=ks), ks


def test_unfiltered_management_page_does_not_download_every_owner_row() -> None:
    owner = FakeOwner(614)
    store, _ks = store_with(owner)
    body = mounted(store).get("/bff/management/evidence?page_size=20", headers=ADMIN_HEADERS).json()
    list_calls = [c for c in owner.calls if "/evidence/items?" in c]
    assert len(list_calls) == 1 and "page_size=20" in list_calls[0]
    assert body["page_info"]["total"] == 614
    assert len(body["data"]["items"]) == 20
    assert body["page_info"]["next_page_token"] == "20"
    assert body["meta"]["surfaces"]["evidence_refs"]["status"] == "ok"
    second = mounted(store).get("/bff/management/evidence?page_size=20&page_token=20", headers=ADMIN_HEADERS).json()
    assert {i["id"] for i in second["data"]["items"]}.isdisjoint({i["id"] for i in body["data"]["items"]})


def test_filtered_management_read_has_correct_totals_across_owner_pages() -> None:
    owner = FakeOwner(614)
    store, _ks = store_with(owner)
    body = mounted(store).get(
        "/bff/management/evidence?verified=true&page_size=10", headers=ADMIN_HEADERS
    ).json()
    assert body["page_info"]["total"] == 614
    assert len([c for c in owner.calls if "/evidence/items?" in c]) == 4  # 200 + 200 + 200 + 14
    none = mounted(store).get(
        "/bff/management/evidence?verified=false&page_size=10", headers=ADMIN_HEADERS
    ).json()
    assert none["page_info"]["total"] == 0
    assert none["meta"]["surfaces"]["evidence_refs"]["status"] == "ok"


def test_four_surfaces_agree_for_the_same_real_item() -> None:
    owner = FakeOwner(5)
    store, _ks = store_with(owner)
    client = mounted(store)
    target = "evi-0000000000000002"
    row = real_row(2)
    mgmt = client.get(f"/bff/management/evidence?ref_id={target}", headers=ADMIN_HEADERS).json()["data"]["items"][0]
    kn_list = next(
        i for i in client.get("/api/v1/knowledge/evidence?page_size=50", headers=ADMIN_HEADERS).json()["evidence_refs"]
        if i["ref_id"] == target
    )
    kn_detail = client.get(f"/api/v1/knowledge/evidence/{target}", headers=ADMIN_HEADERS).json()
    assert mgmt["title"] == kn_list["source_document"]["title"] == kn_detail["source_document"]["title"] == row["citation_label"]
    assert mgmt["summary"] == kn_list["summary"] == kn_detail["summary"] == row["body"]
    assert mgmt["captured_at"] == kn_list["source_document"]["captured_at"] == kn_detail["source_document"]["captured_at"] == row["available_time"]
    assert mgmt["source_ref"] == kn_list["source_document"]["source_ref"] == kn_detail["source_document"]["source_ref"] == row["content_ref"]
    assert mgmt["source_type"] == kn_list["source_document"]["source_type"] == "official_reference"
    detail_calls = [c for c in owner.calls if f"/evidence/items/{target}" in c]
    assert detail_calls, "detail must read one item through the owner item route"
    # The fourth surface: /bff/management/evidence/{id}.
    mgmt_detail = client.get(f"/bff/management/evidence/{target}", headers=ADMIN_HEADERS)
    assert mgmt_detail.status_code == 200
    md = mgmt_detail.json()["data"]["items"][0]
    for key in ("title", "summary", "captured_at", "source_ref", "source_type"):
        assert md[key] == mgmt[key], key
    # display_label is kept by both knowledge projections.
    assert kn_list["display_label"] == kn_detail["display_label"] == row["citation_label"]
    assert client.get("/bff/management/evidence/evi-ffffffffffffffff", headers=ADMIN_HEADERS).status_code == 404


def test_ref_id_filter_reads_one_owner_item_not_every_page() -> None:
    owner = FakeOwner(614)
    store, _ks = store_with(owner)
    client = mounted(store)
    target = "evi-0000000000000002"
    body = client.get(f"/bff/management/evidence?ref_id={target}", headers=ADMIN_HEADERS).json()
    assert [i["ref_id"] for i in body["data"]["items"]] == [target]
    assert [c for c in owner.calls if "/evidence/items?" in c] == []
    assert len([c for c in owner.calls if f"/evidence/items/{target}" in c]) == 1
    owner.calls.clear()
    owner.fail = True
    down = client.get(f"/bff/management/evidence/{target}", headers=ADMIN_HEADERS).json()
    assert down["meta"]["surfaces"]["evidence_refs"]["status"] == "unavailable"
    assert down["data"]["summary"]["total_evidence"] is None


def test_knowledge_list_and_detail_issue_no_extra_availability_probe() -> None:
    owner = FakeOwner(614)
    store, _ks = store_with(owner)
    client = mounted(store)
    r = client.get("/api/v1/knowledge/evidence?page_size=20", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    # Streaming the rows is the read; no page_size=1 dataset probe on top.
    assert [c for c in owner.calls if "page_size=1&" in c] == []
    owner.calls.clear()
    target = "evi-0000000000000002"
    assert client.get(f"/api/v1/knowledge/evidence/{target}", headers=ADMIN_HEADERS).status_code == 200
    assert owner.calls == [f"/api/source-ingest/evidence/items/{target}"]
    owner.fail = True
    down = client.get("/api/v1/knowledge/evidence?page_size=20", headers=ADMIN_HEADERS).json()
    assert down["meta"]["surfaces"]["evidence_refs_list"] == "unavailable"
    assert down["evidence_refs"] == []


def test_pm12_availability_comes_from_the_read_that_produced_the_rows(monkeypatch) -> None:
    from services.control_plane.bff.personas import service as persona_service

    owner = FakeOwner(30)
    store, _ks = store_with(owner)
    monkeypatch.setattr(persona_service, "_get_active_read_store", lambda: store)
    window = {"start_at": "2026-10-01T00:00:00Z", "end_exclusive_at": "2027-01-01T00:00:00Z"}
    identity = type("I", (), {"capabilities": []})()
    _items, _raw, _n, available = persona_service._pm12_public_quarter_evidence_refs(identity, window)
    assert available is True
    assert [c for c in owner.calls if "page_size=1&" in c] == []
    owner.fail = True
    *_rest, available = persona_service._pm12_public_quarter_evidence_refs(identity, window)
    assert available is False


# --------------------------------------------------------------------------
# Failure is reported, per read, never as ok/0
# --------------------------------------------------------------------------
def test_owner_failure_reports_unavailable_with_null_totals() -> None:
    owner = FakeOwner(614)
    owner.fail = True
    store, _ks = store_with(owner)
    body = mounted(store).get("/bff/management/evidence", headers=ADMIN_HEADERS).json()
    surfaces = body["meta"]["surfaces"]
    for name in ("management_evidence", "evidence_refs", "knowledge_evidence"):
        assert surfaces[name]["status"] == "unavailable", name
    summary = body["data"]["summary"]
    assert summary["total_evidence"] is None and summary["total_items"] is None
    assert summary["returned_evidence"] is None and summary["verified_count"] is None
    assert body["data"]["items"] == []


def test_one_failed_read_does_not_mislabel_the_next() -> None:
    owner = FakeOwner(614)
    store, ks = store_with(owner)
    client = mounted(store)
    owner.fail = True
    assert client.get("/bff/management/evidence", headers=ADMIN_HEADERS).json()["meta"]["surfaces"]["evidence_refs"]["status"] == "unavailable"
    owner.fail = False
    ok = client.get("/bff/management/evidence", headers=ADMIN_HEADERS).json()
    assert ok["meta"]["surfaces"]["evidence_refs"]["status"] == "ok"
    assert ok["page_info"]["total"] == 614
    owner.fail = True
    assert ks.read_evidence_refs(page_size=20).status == "unavailable"
    assert not hasattr(ks, "_evidence_status")


def _hung_server() -> Tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held: List[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            held.append(conn)  # accept, then never answer

    threading.Thread(target=accept, daemon=True).start()
    return srv, srv.getsockname()[1]


def test_real_socket_timeout_through_default_http_helper_is_unavailable() -> None:
    srv, port = _hung_server()
    try:
        helper_ok, helper_payload = DefaultResearchKnowledgeSourcePort._default_http_get(
            f"http://127.0.0.1:{port}", "/api/source-ingest/evidence/items?page_size=20&page_token=0"
        )
        assert (helper_ok, helper_payload) == (False, None)

        ks = DefaultResearchKnowledgeSourcePort(source_ingest_service_url=f"http://127.0.0.1:{port}")
        started = time.perf_counter()
        body = mounted(create_read_surface_ports(research_knowledge_source=ks)).get(
            "/bff/management/evidence", headers=ADMIN_HEADERS
        ).json()
        assert time.perf_counter() - started < 10
        for name in ("management_evidence", "evidence_refs", "knowledge_evidence"):
            assert body["meta"]["surfaces"][name]["status"] == "unavailable", name
        assert body["data"]["summary"]["total_evidence"] is None
    finally:
        srv.close()


# --------------------------------------------------------------------------
# PM12 quarterly evidence must not absorb market rows
# --------------------------------------------------------------------------
def test_pm12_quarter_excludes_unlinked_source_ingest_rows() -> None:
    from services.control_plane.bff.personas.service import _pm12_quarter_evidence_refs

    port = DefaultResearchKnowledgeSourcePort(evidence_repository=owner_repo(30))
    market = port.list_evidence_refs()
    assert market and all(r["source_document"]["captured_at"] for r in market)
    window = {"start_at": "2026-10-01T00:00:00Z", "end_exclusive_at": "2027-01-01T00:00:00Z"}
    assert _pm12_quarter_evidence_refs(market, window) == []

    linked = dict(market[0], linked_object_summary={"entity_type": "persona", "entity_ref": "persona-1"})
    assert _pm12_quarter_evidence_refs([*market, linked], window) == [linked]


# --------------------------------------------------------------------------
# Postgres owner path: SQL strips the heavy keys and bounds rows in the DB
# --------------------------------------------------------------------------
def test_postgres_owner_page_query_bounds_rows_and_drops_heavy_keys_in_sql() -> None:
    from contextlib import contextmanager

    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    repo = PostgresSourceEvidenceRepository.__new__(PostgresSourceEvidenceRepository)
    repo.table = "source_ingest.source_evidence"
    repo._replaying = False
    seen: List[Tuple[str, tuple]] = []
    stripped_rows = []
    for i in range(3):
        row = real_row(i)
        row["metadata"].pop("raw_storage_refs")
        row["metadata"].pop("calendar_evidence")
        stripped_rows.append(row)

    class Result:
        def __init__(self, rows: list) -> None:
            self.rows = rows

        def fetchall(self) -> list:
            return self.rows

        def fetchone(self) -> Any:
            return self.rows[0]

    class Conn:
        def execute(self, sql: str, params: tuple) -> Result:
            seen.append((sql, params))
            if "COUNT(*)" in sql:
                return Result([(614,)])
            return Result([(r,) for r in stripped_rows])

    @contextmanager
    def fake_connection():
        yield Conn()

    repo._get_connection = fake_connection  # type: ignore[method-assign]
    items, total = repo.list_evidence_items_page(tenant_id=TENANT, limit=3, offset=6)
    assert total == 614 and len(items) == 3
    page_sql, page_params = seen[1]
    assert "LIMIT %s OFFSET %s" in page_sql
    assert "#- '{metadata,raw_storage_refs}'" in page_sql and "#- '{metadata,calendar_evidence}'" in page_sql
    assert page_params[-2:] == (3, 6)
    assert all("raw_storage_refs" not in i.metadata for i in items)


# --------------------------------------------------------------------------
# BFF-EVIDENCE-SUMMARY-POPULATION-20261105: tenant population summary counts
# --------------------------------------------------------------------------
def test_management_evidence_summary_covers_whole_tenant_population_across_pages_with_one_bounded_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "evid-secret")
    repo = InMemoryEvidenceRepository()

    # 518 tenant-dev rows (hosted shape: 418 verified >=0.8, 100 unverified <0.8;
    # 400 official_reference, 118 exchange_filing).
    for i in range(518):
        raw = real_row(i)
        raw["confidence"] = 0.95 if i < 418 else 0.55
        raw["metadata"]["source_class"] = "official_reference" if i < 400 else "exchange_filing"
        item = EvidenceItem.from_dict(raw)
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[(TENANT, item.evidence_item_id)] = item

    # 96 legacy rows without a tenant (614 total rows on hosted source_evidence).
    for i in range(518, 614):
        raw = real_row(i)
        raw["metadata"].pop("tenant_id", None)
        raw["confidence"] = 1.0
        raw["metadata"]["source_class"] = "legacy_unscoped"
        item = EvidenceItem.from_dict(raw)
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[(None, item.evidence_item_id)] = item

    # Foreign tenant rows that must also stay excluded from tenant-dev.
    for i in range(614, 630):
        raw = real_row(i)
        raw["metadata"]["tenant_id"] = "tenant-other"
        raw["confidence"] = 1.0
        raw["metadata"]["source_class"] = "foreign_source"
        item = EvidenceItem.from_dict(raw)
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[("tenant-other", item.evidence_item_id)] = item

    owner_app = FastAPI()
    owner_app.include_router(create_ingest_operations_router(SimpleNamespace(evidence_repository=repo)))
    token = encode_jwt_hs256(
        {"sub": "r", "roles": ["operator"], "tenant_id": TENANT, "exp": int(time.time()) + 600},
        secret="evid-secret",
    )
    owner_http = TestClient(owner_app, headers={"Authorization": f"Bearer {token}"})
    owner_calls: List[str] = []

    def forward_to_owner(_base_url: str, path: str) -> Tuple[bool, Any]:
        owner_calls.append(path)
        resp = owner_http.get(path)
        return (resp.status_code == 200), (resp.json() if resp.status_code == 200 else None)

    store, _ks = store_with(forward_to_owner)
    bff_client = mounted(store)

    page1 = bff_client.get("/bff/management/evidence?page_size=20", headers=ADMIN_HEADERS).json()
    assert len(owner_calls) == 1
    assert "page_size=20" in owner_calls[0] and "page_token=0" in owner_calls[0]

    page2 = bff_client.get("/bff/management/evidence?page_size=20&page_token=20", headers=ADMIN_HEADERS).json()
    assert len(owner_calls) == 2
    assert "page_size=20" in owner_calls[1] and "page_token=20" in owner_calls[1]

    assert page1["page_info"]["total"] == 518
    assert page2["page_info"]["total"] == 518
    assert len(page1["data"]["items"]) == 20
    assert len(page2["data"]["items"]) == 20
    assert {i["id"] for i in page1["data"]["items"]}.isdisjoint({i["id"] for i in page2["data"]["items"]})

    expected_population_fields = {
        "total_evidence": 518,
        "total_items": 518,
        "verified_count": 418,
        "verified_evidence": 418,
        "by_source_type": {"official_reference": 400, "exchange_filing": 118},
        "by_link_type": {"supporting": 518},
        "by_credibility_tier": {"verified": 418, "unverified": 100},
    }
    for page_payload in (page1, page2):
        summary = page_payload["data"]["summary"]
        for field_name, expected_val in expected_population_fields.items():
            assert summary[field_name] == expected_val, (field_name, summary[field_name], expected_val)
        assert summary["returned_evidence"] == 20
        assert summary["visible_evidence"] == 20
        assert "legacy_unscoped" not in summary["by_source_type"]
        assert "foreign_source" not in summary["by_source_type"]


def test_owner_count_failure_returns_null_summary_fields_and_preserves_bounded_list_total(
    owner_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Owner HTTP response where bounded page succeeds (518 total, 20 items)
    #    but the owner summary count read failed (summary=None).
    owner = FakeOwner(518)
    owner.fail_summary = True
    store, _ks = store_with(owner)
    body = mounted(store).get("/bff/management/evidence?page_size=20", headers=ADMIN_HEADERS).json()
    assert len(owner.calls) == 1
    assert body["page_info"]["total"] == 518
    assert len(body["data"]["items"]) == 20
    summary = body["data"]["summary"]
    assert summary["total_evidence"] == 518
    assert summary["total_items"] == 518
    assert summary["returned_evidence"] == 20
    assert summary["visible_evidence"] == 20
    for field_name in (
        "verified_count",
        "verified_evidence",
        "by_source_type",
        "by_link_type",
        "by_credibility_tier",
    ):
        assert summary[field_name] is None, (field_name, summary[field_name])

    # 2. Real owner route when repo.summarize_evidence_items raises while
    #    repo.list_evidence_items_page succeeds.
    client, repo = owner_client(518)

    def _boom(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("PostgreSQL evidence query failed: OperationalError")

    monkeypatch.setattr(repo, "summarize_evidence_items", _boom)
    owner_resp = client.get("/api/source-ingest/evidence/items?page_size=20&page_token=0")
    assert owner_resp.status_code == 200
    owner_payload = owner_resp.json()
    assert owner_payload["total"] == 518
    assert len(owner_payload["items"]) == 20
    assert owner_payload["summary"] is None


def test_postgres_owner_summarize_evidence_items_aggregates_in_sql_and_excludes_tenantless_rows() -> None:
    from contextlib import contextmanager

    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    repo = PostgresSourceEvidenceRepository.__new__(PostgresSourceEvidenceRepository)
    repo.table = "source_ingest.source_evidence"
    repo._replaying = False
    seen: List[Tuple[str, tuple]] = []

    class Result:
        def __init__(self, rows: list) -> None:
            self.rows = rows

        def fetchall(self) -> list:
            return self.rows

    class Conn:
        def execute(self, sql: str, params: tuple) -> Result:
            seen.append((sql, params))
            return Result(
                [
                    ("official_reference", "supporting", "verified", True, 400),
                    ("official_reference", "supporting", "unverified", False, 18),
                    ("exchange_filing", "supporting", "verified", True, 100),
                ]
            )

    @contextmanager
    def fake_connection():
        yield Conn()

    repo._get_connection = fake_connection  # type: ignore[method-assign]
    summary = repo.summarize_evidence_items(tenant_id=TENANT)
    assert len(seen) == 1
    sql, params = seen[0]
    assert "GROUP BY 1, 2, 3, 4" in sql
    assert "payload->'metadata'->>'tenant_id' = %s" in sql
    assert params == ("evidence_item", TENANT)
    assert summary == {
        "total": 518,
        "verified_count": 500,
        "verified_evidence": 500,
        "by_source_type": {"official_reference": 418, "exchange_filing": 100},
        "by_link_type": {"supporting": 518},
        "by_credibility_tier": {"verified": 500, "unverified": 18},
    }
