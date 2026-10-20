"""Saved Research run-output artifact reads: owner endpoints and the BFF default port/client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.research.client import (
    ResearchCommandError,
    ResearchCommandUnavailableError,
    ResearchServiceClient,
)
from services.research import main as research_main
from services.research.store import ResearchOrchestratorStore


_SECRET = "s3cret-owner-test-key-0123456789abcdef"


def _jwt(tenant: str, role: str = "operator") -> str:
    return _hs256(tenant, _SECRET, role)


@pytest.fixture(autouse=True)
def _owner_verifier(monkeypatch):
    """The owner verifies the BFF-forwarded caller bearer with the shared service verifier (no anonymous path)."""
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", _SECRET)


def _hs256(tenant: str, secret: str, role: str = "operator") -> str:
    seg = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    head = seg(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = seg(json.dumps({"sub": "u1", "roles": [role], "tenant_id": tenant}).encode())
    sig = seg(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"Bearer {head}.{body}.{sig}"


@pytest.fixture()
def owner(tmp_path, monkeypatch):
    store = ResearchOrchestratorStore(tmp_path)
    monkeypatch.setattr(research_main, "store", store)
    store.put_task({"task_id": "rtask-1", "tenant_id": "t-a", "status": "completed"})
    store.put_run({
        "run_id": "rrun-1", "task_id": "rtask-1", "status": "completed", "provenance": "real",
        "completed_at": "2026-10-16T01:00:00Z",
        "receipt": {"receipt_id": "rcpt-1", "artifact_digest": "sha256:abc"},
    })
    store.put_artifact({
        "artifact_id": "rart-1", "run_id": "rrun-1", "task_id": "rtask-1", "artifact_type": "backtest_result",
        "title": "Execution artifact", "checksum": "sha256:abc", "created_at": "2026-10-16T01:00:00Z",
        "payload": {"big": "x" * 50}, "provenance": "real",
    })
    store.put_artifact({  # other tenant's output, and one with unknown ownership
        "artifact_id": "rart-b", "run_id": "rrun-b", "tenant_id": "t-b", "artifact_type": "backtest_result",
    })
    store.put_artifact({"artifact_id": "rart-orphan", "run_id": "rrun-x", "artifact_type": "backtest_result"})
    return TestClient(research_main.app)


def _h(tenant):
    return {"Authorization": _jwt(tenant), "X-Tenant-Id": tenant}


def test_owner_lists_only_caller_tenant_and_omits_payload(owner):
    rows = owner.get("/api/research/artifacts", headers=_h("t-a")).json()
    assert [r["artifact_id"] for r in rows] == ["rart-1"]
    assert rows[0]["run"]["receipt_id"] == "rcpt-1" and rows[0]["tenant_id"] == "t-a"
    assert "payload" not in rows[0]
    assert owner.get("/api/research/artifacts", headers=_h("t-zzz")).json() == []


def test_owner_detail_denies_cross_tenant_and_missing(owner):
    assert owner.get("/api/research/artifacts/rart-1", headers=_h("t-a")).json()["run"]["run_id"] == "rrun-1"
    assert owner.get("/api/research/artifacts/rart-1", headers=_h("t-b")).status_code == 404
    assert owner.get("/api/research/artifacts/rart-b", headers=_h("t-a")).status_code == 404
    assert owner.get("/api/research/artifacts/nope", headers=_h("t-a")).status_code == 404


def test_owner_denies_anonymous_and_tenantless_reads(owner):
    for path in ("/api/research/artifacts", "/api/research/artifacts/rart-1", "/api/research/artifacts/rart-orphan"):
        assert owner.get(path).status_code == 401
        assert owner.get(path, headers={"X-Tenant-Id": "t-a"}).status_code == 401  # header alone is not identity
        assert owner.get(path, headers={"Authorization": _jwt("t-a")}).status_code == 400  # no tenant: never orphan view
    # authenticated caller still cannot see unresolved-tenant output
    assert owner.get("/api/research/artifacts/rart-orphan", headers=_h("t-a")).status_code == 404
    assert all(r["artifact_id"] != "rart-orphan" for r in owner.get("/api/research/artifacts", headers=_h("t-a")).json())


def test_owner_denies_signed_token_tenant_claim_mismatch(owner):
    tok = _jwt("t-a")
    ok = owner.get("/api/research/artifacts", headers={"Authorization": tok, "X-Tenant-Id": "t-a"})
    assert ok.status_code == 200 and [r["artifact_id"] for r in ok.json()] == ["rart-1"]
    bad = owner.get("/api/research/artifacts", headers={"Authorization": tok, "X-Tenant-Id": "t-b"})
    assert bad.status_code == 403
    forged = _hs256("t-a", "wrong-key-wrong-key-wrong-key-0000")
    assert owner.get("/api/research/artifacts", headers={"Authorization": forged, "X-Tenant-Id": "t-a"}).status_code == 401


class _CountingOwner:
    """Authenticated owner stub backed by the real owner app; counts calls."""

    def __init__(self, http):
        self.calls = []
        self.client = ResearchServiceClient(base_url="http://owner", http_request=self._request)
        self._http = http

    def _request(self, method, url, payload, headers):
        self.calls.append((method, url, dict(headers or {})))
        resp = self._http.request(method, url.replace("http://owner", ""), headers=headers or {})
        return resp.status_code, resp.json()


@pytest.fixture()
def wired(owner):
    stub = _CountingOwner(owner)
    port = DefaultResearchKnowledgeSourcePort(research_write_owner=stub.client)
    return stub, port


@pytest.fixture()
def as_tenant():
    def _set(tenant):
        owner_reads.authorization.set(_jwt(tenant))
        owner_reads.selected_tenant.set(tenant)

    yield _set
    owner_reads.authorization.set(None)
    owner_reads.selected_tenant.set(None)


def test_default_port_reads_owner_with_one_call_and_keeps_identity(wired, as_tenant):
    stub, port = wired
    as_tenant("t-a")
    assert port.dataset_source("research_artifacts") == "service_client"
    rows = port.list_research_artifacts()
    assert len(stub.calls) == 1 and stub.calls[0][2]["X-Tenant-Id"] == "t-a"
    assert stub.calls[0][2]["Authorization"].startswith("Bearer ")
    (row,) = rows
    assert row["artifact_id"] == "rart-1"
    assert row["saved_output"]["receipt_id"] == "rcpt-1" and row["saved_output"]["run_id"] == "rrun-1"
    assert row["saved_output"]["checksum"] == "sha256:abc"
    # nothing invented
    assert row["status"] == "" and row["version"] is None and row["lineage_id"] is None
    assert row["is_current_version"] is None and row["allowedActions"] == {"canCompare": False}
    detail = port.get_research_artifact("rart-1")
    assert detail["lineage"]["versions"] == [] and detail["metrics"] == {} and detail["source_kind"] == "research_run_output"
    assert detail["allowedActions"] == {"canCompare": False}


def test_default_port_not_found_empty_and_tenant_boundary(wired, as_tenant):
    stub, port = wired
    as_tenant("t-b")
    assert [r["artifact_id"] for r in port.list_research_artifacts()] == ["rart-b"]
    assert port.get_research_artifact("rart-1") is None
    as_tenant("t-empty")
    assert port.list_research_artifacts() == []  # genuine empty, owner reachable
    assert port.get_research_artifact("nope") is None


def test_owner_failure_is_not_empty_or_missing(as_tenant):
    as_tenant("t-a")
    for code in (503, 500, 404):
        client = ResearchServiceClient(base_url="http://owner", http_request=lambda *a, _c=code: (_c, {"detail": "boom"}))
        port = DefaultResearchKnowledgeSourcePort(research_write_owner=client)
        with pytest.raises(ResearchCommandError):
            port.list_research_artifacts()
    client = ResearchServiceClient(base_url="http://owner", http_request=lambda *a: (503, {"detail": "down"}))
    with pytest.raises(ResearchCommandUnavailableError):
        DefaultResearchKnowledgeSourcePort(research_write_owner=client).get_research_artifact("rart-1")


def test_unauthenticated_read_never_reaches_owner():
    owner_reads.authorization.set(None)
    calls = []
    client = ResearchServiceClient(base_url="http://owner", http_request=lambda *a: calls.append(a) or (200, []))
    with pytest.raises(ResearchCommandError) as exc:
        DefaultResearchKnowledgeSourcePort(research_write_owner=client).list_research_artifacts()
    assert exc.value.status_code == 401 and calls == []


def test_injected_fixture_dict_still_wins_and_owner_untouched():
    class _Boom:
        def list_research_artifacts(self):
            raise AssertionError("owner must not be called when a fixture is injected")

    port = DefaultResearchKnowledgeSourcePort(
        research_write_owner=_Boom(),
        research_artifacts_store={"a1": {"artifact_id": "a1", "status": "sealed", "created_at": "2026-01-01T00:00:00Z"}},
    )
    assert [r["artifact_id"] for r in port.list_research_artifacts()] == ["a1"]


class _StubOwner:
    def __init__(self, rows=(), fail=None):
        self.rows, self.fail, self.list_calls = list(rows), fail, 0

    def list_research_artifacts(self):
        self.list_calls += 1
        if self.fail:
            raise self.fail
        return self.rows

    def get_research_artifact(self, artifact_id):
        if self.fail:
            raise self.fail
        return next((r for r in self.rows if r["artifact_id"] == artifact_id), None)


def _bff(owner):
    from services.control_plane.bff.tests.knowledge_read_port_fixtures import create_research_test_app

    port = DefaultResearchKnowledgeSourcePort(research_write_owner=owner)
    app = create_research_test_app(
        port, utc_now=lambda: "2026-10-20T00:00:00Z",
        dataset_surface_status=port.dataset_surface_status, include_prepared_subrouters=False,
    )
    return TestClient(app, raise_server_exceptions=False)


_AUTH = {"Authorization": "Bearer test-operator:operator"}
_ROW = {"artifact_id": "rart-1", "run_id": "rrun-1", "title": "T", "artifact_type": "backtest_result",
        "checksum": "sha256:abc", "run": {"receipt_id": "rcpt-1"}}


def test_bff_routes_distinguish_data_empty_unavailable_and_missing():
    ok = _bff(_StubOwner([_ROW]))
    body = ok.get("/bff/artifacts?page_size=100", headers=_AUTH).json()
    assert body["page_info"]["total"] == 1 and body["meta"]["surfaces"]["artifact_list"] == {"status": "ok", "source": "service_client"}
    detail = ok.get("/bff/artifacts/rart-1", headers=_AUTH).json()["data"]
    assert detail["saved_output"]["receipt_id"] == "rcpt-1" and detail["allowedActions"] == {"canCompare": False}
    assert ok.get("/bff/artifacts/nope", headers=_AUTH).status_code == 404

    empty = _bff(_StubOwner([])).get("/bff/artifacts", headers=_AUTH)
    assert empty.status_code == 200 and empty.json()["meta"]["surfaces"]["artifact_list"]["status"] == "ok"

    down = _bff(_StubOwner(fail=ResearchCommandUnavailableError("owner down")))
    assert down.get("/bff/artifacts", headers=_AUTH).status_code == 503
    assert down.get("/bff/artifacts/rart-1", headers=_AUTH).status_code == 503


def test_saved_outputs_are_not_comparable():
    resp = _bff(_StubOwner([_ROW, {**_ROW, "artifact_id": "rart-2"}])).get(
        "/api/v1/research/artifacts/compare?artifact_ids=rart-1,rart-2", headers=_AUTH)
    assert resp.status_code in (400, 409, 422) and "rart-1" in resp.text


def test_owner_admits_authenticated_viewer_reads_and_rejects_unknown_role(owner):
    hdr = {"Authorization": _jwt("t-a", "viewer"), "X-Tenant-Id": "t-a"}
    lst = owner.get("/api/research/artifacts", headers=hdr)
    assert lst.status_code == 200 and [r["artifact_id"] for r in lst.json()] == ["rart-1"]
    assert owner.get("/api/research/artifacts/rart-1", headers=hdr).status_code == 200
    assert owner.get("/api/research/artifacts/rart-b", headers=hdr).status_code == 404
    bad = {"Authorization": _jwt("t-a", "nobody"), "X-Tenant-Id": "t-a"}
    assert owner.get("/api/research/artifacts", headers=bad).status_code == 403
