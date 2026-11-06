"""BFF-EVIDENCE-BOUNDED-READ-20261106 regression tests for bounded Management Evidence reads.

Covers:
1. Real HTTP owner-backed 518-item tenant population (510 official_reference,
   8 source_record, 518 verified) returning 20 items per page with whole-population
   summary under the genuine ``run_management_read`` wrapper and
   ``OwnerReadContextMiddleware``, including the 500-620 ms owner latency window
   that previously tripped the generic 0.6 s fallback timeout on 3/10 reads.
2. Repeated sequential reads, concurrent reload waves, and page-2 pagination
   preserving whole-population summary counts.
3. Tenant isolation (including ``selected_tenant`` context enforcement on both
   paged ``service_client`` and single-item reads) and capability redaction.
4. Honest unavailable / timeout / saturation / error semantics: genuine owner
   unavailability or route timeout never fabricates a zero dataset
   (``summary.total_evidence`` and ``summary.verified_count`` are ``None``,
   never ``0``), and ``_default_http_get`` / ``_read_owner_evidence_page``
   respect ``remaining_management_read_budget``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import (
    OwnerReadContextMiddleware,
    management_read_deadline,
    selected_tenant,
)
from services.control_plane.bff.management_read_models.router import (
    _default_bff_error,
    _default_extract_identity,
    _default_require_read_role,
    _get_management_evidence_read_timeout,
    create_management_router,
)
from services.control_plane.bff.personas.routes.common import (
    ManagementReadSaturated,
    ManagementReadTimeout,
    run_management_read,
)
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    _EvidenceOwnerReadError,
)
from services.knowledge.evidence.models import EvidenceItem
from services.knowledge.evidence.repository import InMemoryEvidenceRepository
from services.runtime_auth_inbound import encode_jwt_hs256


TENANT_DEV = "tenant-dev"
TENANT_OTHER = "tenant-other"
JWT_SECRET = "bff-evidence-bounded-read-secret"
ADMIN_HEADERS = {
    "Authorization": "Bearer op-evid-admin:admin",
    "X-Tenant-Id": TENANT_DEV,
}
OPERATOR_HEADERS = {
    "Authorization": "Bearer op-evid-operator:operator",
    "X-Tenant-Id": TENANT_DEV,
}


def _hosted_row(
    i: int,
    *,
    tenant_id: Optional[str] = TENANT_DEV,
    source_class: str = "official_reference",
    confidence: float = 1.0,
) -> Dict[str, Any]:
    day = f"2026-11-{(i % 28) + 1:02d}"
    metadata: Dict[str, Any] = {
        "source_class": source_class,
        "dataset": "tw_price_daily",
        "symbol": "0050",
        "venue": "TWSE",
        "provider": "TWSE/TPEx",
        "raw_storage_refs": [
            {"uri": f"gs://pantheon-raw/tw_price_daily/{i}/{n}", "sha256": "ab" * 32, "note": "x" * 90}
            for n in range(40)
        ],
        "calendar_evidence": {
            "sessions": [{"date": day, "open": "09:00", "close": "13:30", "note": "y" * 60} for _ in range(20)]
        },
        "normalized_row": {"open": 190.1, "high": 191.0, "low": 189.5, "close": 190.8, "volume": 1234567},
    }
    if tenant_id is not None:
        metadata["tenant_id"] = tenant_id
    return {
        "evidence_item_id": f"evi-{i:016x}",
        "source_id": f"src-{i:016x}",
        "item_type": "source_record",
        "content_ref": f"tw-official://tw_price_daily/TWSE/0050/{day}/{i:016x}",
        "citation_label": f"TW official tw_price_daily TWSE 0050 {day} #{i}",
        "body": f"TW official tw_price_daily TWSE 0050 {day} #{i}",
        "event_time": day,
        "available_time": f"{day}T14:30:00+08:00",
        "confidence": confidence,
        "access_scope": ["public", "research"],
        "trace_refs": [f"trace://tw_price_daily/{i}"],
        "metadata": metadata,
    }


def _build_hosted_518_repo() -> InMemoryEvidenceRepository:
    """Build the hosted Dev dataset shape:
    - 518 tenant-dev items (510 official_reference + 8 source_record, all verified)
    - 96 unscoped legacy rows (tenant_id=None)
    - 16 foreign tenant rows (tenant-other)
    """
    repo = InMemoryEvidenceRepository()
    for i in range(518):
        source_class = "official_reference" if i < 510 else "source_record"
        raw = _hosted_row(i, tenant_id=TENANT_DEV, source_class=source_class, confidence=1.0)
        item = EvidenceItem.from_dict(raw)
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[(TENANT_DEV, item.evidence_item_id)] = item

    for i in range(518, 614):
        raw = _hosted_row(i, tenant_id=None, source_class="legacy_unscoped", confidence=1.0)
        item = EvidenceItem.from_dict(raw)
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[(None, item.evidence_item_id)] = item

    for i in range(614, 630):
        raw = _hosted_row(i, tenant_id=TENANT_OTHER, source_class="foreign_source", confidence=1.0)
        item = EvidenceItem.from_dict(raw)
        repo._evidence_items[item.evidence_item_id] = item
        repo._evidence_items_by_tenant[(TENANT_OTHER, item.evidence_item_id)] = item

    return repo


class _RealHttpOwnerServer:
    """Real localhost HTTP server wrapping the source-ingest evidence owner router
    with configurable per-request latency so tests exercise DefaultResearchKnowledgeSourcePort._default_http_get
    over real sockets under run_management_read."""

    def __init__(self, repo: InMemoryEvidenceRepository, *, latency_seconds: float = 0.0) -> None:
        from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

        self.repo = repo
        self.latency_seconds = latency_seconds
        self.fail_status: Optional[int] = None
        self.calls: List[Tuple[str, Optional[str], Optional[str]]] = []
        self._lock = threading.Lock()

        owner_app = FastAPI()
        owner_app.include_router(create_ingest_operations_router(SimpleNamespace(evidence_repository=repo)))
        self._owner_client = TestClient(owner_app)
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                auth_hdr = self.headers.get("Authorization")
                tenant_hdr = self.headers.get("X-Tenant-Id")
                with parent._lock:
                    parent.calls.append((self.path, auth_hdr, tenant_hdr))
                    delay = parent.latency_seconds
                    fail_code = parent.fail_status
                if delay > 0:
                    time.sleep(delay)
                if fail_code is not None:
                    self.send_response(fail_code)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"owner unavailable"}')
                    return
                fwd_headers: Dict[str, str] = {}
                if auth_hdr:
                    fwd_headers["Authorization"] = auth_hdr
                if tenant_hdr:
                    fwd_headers["X-Tenant-Id"] = tenant_hdr
                resp = parent._owner_client.get(self.path, headers=fwd_headers)
                body = resp.content
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def _build_bounded_bff_app(store: Any) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=_default_bff_error,
            run_management_read=run_management_read,
        )
    )
    return app


def test_real_owner_518_bounded_reads_and_page2_succeed_across_610ms_latency_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the Dev 10-read diagnostic where bounded 518-item owner reads take
    ~500-615 ms: under the genuine run_management_read wrapper and real HTTP owner
    transport, all 10 sequential reads and page 2 must return 20 items with the full
    518 whole-population summary and zero read_timeout degradations."""
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", JWT_SECRET)
    repo = _build_hosted_518_repo()
    # 0.62s exceeds the old 0.60s generic fallback budget that caused 3/10 Dev reads
    # to fail at 610-612 ms, while staying well within the 2.0s owner / 2.5s route budget.
    server = _RealHttpOwnerServer(repo, latency_seconds=0.62)
    try:
        token = encode_jwt_hs256(
            {"sub": "op-dev", "roles": ["operator", "admin"], "tenant_id": TENANT_DEV, "exp": int(time.time()) + 600},
            secret=JWT_SECRET,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": TENANT_DEV,
        }
        ks = DefaultResearchKnowledgeSourcePort(source_ingest_service_url=server.base_url)
        store = create_read_surface_ports(research_knowledge_source=ks)
        app = _build_bounded_bff_app(store)

        expected_summary = {
            "total_evidence": 518,
            "total_items": 518,
            "returned_evidence": 20,
            "visible_evidence": 20,
            "redacted_evidence": 0,
            "verified_evidence": 518,
            "verified_count": 518,
            "by_source_type": {"official_reference": 510, "source_record": 8},
            "by_link_type": {"supporting": 518},
            "by_credibility_tier": {"verified": 518},
        }

        with TestClient(app) as client:
            for read_idx in range(10):
                resp = client.get("/bff/management/evidence?page_size=20", headers=headers)
                assert resp.status_code == 200, (read_idx, resp.text)
                body = resp.json()
                assert body["page_info"] == {
                    "next_page_token": "20",
                    "total": 518,
                    "page_size": 20,
                }, (read_idx, body["page_info"])
                assert len(body["data"]["items"]) == 20
                assert body["data"]["summary"] == expected_summary, (read_idx, body["data"]["summary"])
                assert body["meta"]["surfaces"]["management_evidence"]["status"] == "ok"
                assert body["meta"]["surfaces"]["evidence_refs"]["status"] == "ok"
                assert body["meta"]["surfaces"]["evidence_refs"]["source"] == "service_client"

            # Page 2 must return the next 20 disjoint items and retain the exact
            # 518 whole-population summary counts.
            page1_ids = {item["id"] for item in body["data"]["items"]}
            resp_p2 = client.get("/bff/management/evidence?page_size=20&page_token=20", headers=headers)
            assert resp_p2.status_code == 200, resp_p2.text
            page2 = resp_p2.json()
            page2_ids = {item["id"] for item in page2["data"]["items"]}
            assert len(page2["data"]["items"]) == 20
            assert page1_ids.isdisjoint(page2_ids)
            assert page2["page_info"] == {
                "next_page_token": "40",
                "total": 518,
                "page_size": 20,
            }
            assert page2["data"]["summary"] == expected_summary
            assert "legacy_unscoped" not in page2["data"]["summary"]["by_source_type"]
            assert "foreign_source" not in page2["data"]["summary"]["by_source_type"]

            # Detail route (/bff/management/evidence/{ref_id}) also succeeds under the
            # 0.62s owner latency and enforces tenant isolation.
            target_id = body["data"]["items"][0]["id"]
            detail_resp = client.get(f"/bff/management/evidence/{target_id}", headers=headers)
            assert detail_resp.status_code == 200, detail_resp.text
            detail_body = detail_resp.json()
            assert detail_body["data"]["items"][0]["id"] == target_id

            # Foreign-tenant item must return 404 for tenant-dev caller.
            foreign_resp = client.get("/bff/management/evidence/evi-0000000000000266", headers=headers)
            assert foreign_resp.status_code == 404

        page_calls = [c[0] for c in server.calls if "page_size=20" in c[0]]
        assert len(page_calls) == 11
        assert len(server.calls) == 14
    finally:
        server.close()


def test_tenant_isolation_and_capability_redaction_preserved_across_bounded_reads() -> None:
    """Verify selected_tenant context filters out foreign-tenant rows even if an
    owner transport returns mixed-tenant items, and capability redaction still
    strips restricted fields per caller role."""
    mixed_items = [
        _hosted_row(0, tenant_id=TENANT_DEV, source_class="official_reference"),
        _hosted_row(1, tenant_id=TENANT_OTHER, source_class="foreign_source"),
    ]
    # Add a metric item requiring metric.read capability for redaction verification.
    metric_row = _hosted_row(2, tenant_id=TENANT_DEV, source_class="official_reference")
    metric_row["item_type"] = "metric"
    mixed_items.append(metric_row)

    seen_headers: List[Optional[str]] = []

    def _http_get(_base_url: str, path: str) -> Tuple[bool, Any]:
        seen_headers.append(selected_tenant.get())
        parsed = urlparse(path)
        if parsed.path.endswith("/evidence/items"):
            return True, {
                "items": mixed_items,
                "total": 2,
                "page_size": 20,
                "next_page_token": None,
                "summary": {
                    "total": 2,
                    "verified_count": 2,
                    "verified_evidence": 2,
                    "by_source_type": {"official_reference": 2},
                    "by_link_type": {"supporting": 2},
                    "by_credibility_tier": {"verified": 2},
                },
            }
        return False, None

    ks = DefaultResearchKnowledgeSourcePort(
        source_ingest_service_url="http://source-ingest.test",
        http_get_fn=_http_get,
    )
    store = create_read_surface_ports(research_knowledge_source=ks)
    app = _build_bounded_bff_app(store)

    with TestClient(app) as client:
        resp_admin = client.get("/bff/management/evidence?page_size=20", headers=ADMIN_HEADERS)
        assert resp_admin.status_code == 200
        body_admin = resp_admin.json()
        returned_ids = [it["id"] for it in body_admin["data"]["items"]]
        # Foreign tenant row evi-0000000000000001 must be filtered out by selected_tenant.
        assert "evi-0000000000000001" not in returned_ids
        assert returned_ids == ["evi-0000000000000000", "evi-0000000000000002"]
        assert body_admin["data"]["summary"]["redacted_evidence"] == 0

        # Operator lacks metric.read capability -> metric item is redacted.
        resp_op = client.get("/bff/management/evidence?page_size=20", headers=OPERATOR_HEADERS)
        assert resp_op.status_code == 200
        body_op = resp_op.json()
        by_id = {it["id"]: it for it in body_op["data"]["items"]}
        assert by_id["evi-0000000000000000"]["redacted"] is False
        assert by_id["evi-0000000000000002"]["redacted"] is True
        assert by_id["evi-0000000000000002"]["required_capability"] == "metric.read"
        assert body_op["data"]["summary"]["redacted_evidence"] == 1
        assert body_op["meta"]["redacted_evidence_count"] == 1

    assert seen_headers == [TENANT_DEV, TENANT_DEV]


@pytest.mark.parametrize(
    "exc_cls,expected_reason,expected_source",
    [
        (ManagementReadTimeout, "read_timeout", "timeout_fallback"),
        (ManagementReadSaturated, "read_capacity_saturated", "capacity_fallback"),
        (RuntimeError, "read_error", "error_fallback"),
    ],
)
def test_evidence_degraded_fallback_preserves_nofake0_and_distinguishes_failure_reasons(
    exc_cls: type[Exception],
    expected_reason: str,
    expected_source: str,
) -> None:
    """When run_management_read raises a timeout, saturation, or unexpected error,
    /bff/management/evidence and /bff/management/evidence/{ref_id} must report the
    honest reason and never claim a zero dataset (summary counts must be None)."""

    async def _failing_read(*_args: Any, **_kwargs: Any) -> Any:
        raise exc_cls("simulated read failure")

    store = create_read_surface_ports()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=_default_bff_error,
            run_management_read=_failing_read,
        )
    )

    with TestClient(app) as client:
        for path in ("/bff/management/evidence?page_size=20", "/bff/management/evidence/evi-0000000000000001"):
            resp = client.get(path, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, (path, resp.text)
            body = resp.json()
            assert body["data"]["items"] == []
            summary = body["data"]["summary"]
            for key in (
                "total_evidence",
                "total_items",
                "returned_evidence",
                "visible_evidence",
                "redacted_evidence",
                "verified_evidence",
                "verified_count",
                "by_source_type",
                "by_link_type",
                "by_credibility_tier",
            ):
                assert summary[key] is None, (path, key, summary[key])
            mgmt_surface = body["meta"]["surfaces"]["management_evidence"]
            assert mgmt_surface["status"] == "degraded"
            assert mgmt_surface["reason"] == expected_reason
            assert mgmt_surface["source"] == expected_source
            ev_surface = body["meta"]["surfaces"]["evidence_refs"]
            assert ev_surface["status"] == "unavailable"
            assert ev_surface["reason"] == expected_reason


def test_real_owner_unavailable_and_expired_deadline_remain_honest_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genuine owner 503 unavailability and expired management_read_deadline must
    return honest unavailable surfaces with null summary totals (never 0)."""
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", JWT_SECRET)
    repo = _build_hosted_518_repo()
    server = _RealHttpOwnerServer(repo)
    server.fail_status = 503
    try:
        ks = DefaultResearchKnowledgeSourcePort(source_ingest_service_url=server.base_url)
        store = create_read_surface_ports(research_knowledge_source=ks)
        app = _build_bounded_bff_app(store)

        with TestClient(app) as client:
            resp = client.get("/bff/management/evidence?page_size=20", headers=ADMIN_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["items"] == []
            assert body["data"]["summary"]["total_evidence"] is None
            assert body["data"]["summary"]["verified_count"] is None
            assert body["data"]["summary"]["by_source_type"] is None
            assert body["meta"]["surfaces"]["management_evidence"]["status"] == "unavailable"
            assert body["meta"]["surfaces"]["evidence_refs"]["status"] == "unavailable"

        # When management_read_deadline is already expired, _default_http_get and
        # _read_owner_evidence_page must abort immediately without making a network call.
        calls_before = len(server.calls)
        tok = management_read_deadline.set(time.monotonic() - 0.05)
        try:
            ok, payload = DefaultResearchKnowledgeSourcePort._default_http_get(
                server.base_url, "/api/source-ingest/evidence/items?page_size=20&page_token=0"
            )
            assert (ok, payload) == (False, None)
            with pytest.raises(_EvidenceOwnerReadError):
                ks._read_owner_evidence_page(start=0, page_size=20)
        finally:
            management_read_deadline.reset(tok)
        assert len(server.calls) == calls_before
    finally:
        server.close()


def test_evidence_route_timeout_budget_outlasts_owner_read_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_EVIDENCE_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_EVIDENCE_OWNER_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS", raising=False)
    assert _get_management_evidence_read_timeout() == pytest.approx(2.5)
