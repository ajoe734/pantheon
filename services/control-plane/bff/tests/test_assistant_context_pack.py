from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.assistant.context_composer import compose_context_pack
from services.control_plane.bff.assistant.routes import create_assistant_router
from services.control_plane.bff.assistant.source_collectors import (
    AssistantSourceCollectorDeps,
    collect_assistant_context_source,
)
from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_read_role,
)
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

REPO_ROOT = Path(__file__).resolve().parents[4]
OPERATOR_HEADERS = {"Authorization": "Bearer asst-kernel:operator"}


def _read_surface_state() -> str:
    return os.getenv("BFF_READ_SURFACE_STATE", "fresh")


def _surface_status() -> Dict[str, Any]:
    state = _read_surface_state()
    if state == "fresh":
        return {"status": "ok"}
    if state in {"degraded", "stale"}:
        return {
            "status": "degraded",
            "staleness": {"served_from": "cache", "last_known_at": utc_now()},
        }
    if state == "unavailable":
        return {
            "status": "unavailable",
            "staleness": {"served_from": "cache", "last_known_at": utc_now()},
        }
    return {"status": "ok"}


def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    source = source or "typed_store"
    surface["source"] = source
    if source == "missing":
        surface["status"] = "unavailable"
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )
    if has_data is False:
        if surface.get("status") == "ok":
            surface["status"] = "unavailable"
    return surface


def _filter_tenant_records(records: List[Dict[str, Any]], tenant_id: Optional[str]) -> List[Dict[str, Any]]:
    clean = str(tenant_id or "").strip()
    if not clean:
        return list(records)
    kept = []
    for record in records:
        record_tenant = str(record.get("tenant_id") or "").strip()
        if not record_tenant or record_tenant in {"*", clean}:
            kept.append(record)
    return kept


class _FakePersonaService:
    def build_persona_health_items(self, snapshot_at: str) -> List[Dict[str, Any]]:
        return []


def _seed_store(path: str = ""):
    jobs_map = {
        "job_123": {
            "id": "job_123",
            "job_id": "job_123",
            "job_type": "paper-loop",
            "status": "running",
            "tenant_id": "tenant-alpha",
            "created_at": "2026-05-31T15:00:00Z",
            "logs": [
                {
                    "ts": "2026-05-31T15:01:00Z",
                    "level": "info",
                    "message": "loop tick",
                }
            ],
        },
        "job_beta": {
            "id": "job_beta",
            "job_id": "job_beta",
            "job_type": "paper-loop",
            "status": "running",
            "tenant_id": "tenant-beta",
            "created_at": "2026-05-31T15:05:00Z",
            "logs": [{"ts": "2026-05-31T15:06:00Z", "level": "info", "message": "beta tick"}],
        },
    }
    audit_map = [
        {
            "entry_id": "audit_123",
            "target_type": "job",
            "target_id": "job_123",
            "action_type": "job.started",
            "tenant_id": "tenant-alpha",
            "timestamp": "2026-05-31T15:00:30Z",
        },
        {
            "entry_id": "audit_beta",
            "target_type": "job",
            "target_id": "job_beta",
            "action_type": "job.started",
            "tenant_id": "tenant-beta",
            "timestamp": "2026-05-31T15:05:30Z",
        },
    ]
    personas_map = {
        "persona_1": {
            "id": "persona_1",
            "persona_id": "persona_1",
            "name": "Observer",
            "lifecycle_state": "active",
        }
    }
    strategy_specs_map = {
        "strategy_1": {
            "id": "strategy_1",
            "strategy_id": "strategy_1",
            "name": "Paper Loop",
            "status": "active",
        }
    }

    store = create_in_memory_read_surface_ports()
    store.list_jobs = lambda **kw: list(jobs_map.values())
    store.list_jobs_bff = lambda **kw: list(jobs_map.values())
    store.get_job = lambda job_id: jobs_map.get(job_id)
    store.get_job_bff = lambda job_id: jobs_map.get(job_id)
    store.list_governance_audit_events = lambda **kw: list(audit_map)
    store.list_personas = lambda **kw: list(personas_map.values())
    store.list_strategy_specs = lambda **kw: list(strategy_specs_map.values())
    store.list_events_bff = lambda **kw: []
    store.list_persona_league = lambda **kw: []
    store.list_strategy_summaries = lambda **kw: []
    store.list_runtimes = lambda **kw: []
    store.list_runtime_instances = lambda **kw: []
    store.list_runtime_bindings = lambda **kw: []
    store.dataset_source = lambda d: "typed_store"
    return store


def _client_with_seeded_store(tmp_path, monkeypatch):
    store = _seed_store(str(tmp_path / "read_surfaces.json"))
    deps = AssistantSourceCollectorDeps(
        read_store=store,
        list_governance_audit_events=lambda **kw: store.list_governance_audit_events(**kw),
        filter_tenant_records_fn=_filter_tenant_records,
        dataset_surface_status=_dataset_surface_status,
        generic_path_collector=lambda path: None,
        persona_service=_FakePersonaService(),
        build_operator_alerts_payload=lambda snap: {"items": [], "meta": {}},
        repo_root=lambda: REPO_ROOT,
    )

    def _collect_source(source_id: str, request: Any, snapshot_at: str, identity: Any = None):
        return collect_assistant_context_source(
            source_id,
            request,
            snapshot_at,
            identity,
            deps=deps,
        )

    def _build_context_pack(session_id: str, request: Any, identity: Any) -> Any:
        return compose_context_pack(
            session_id=session_id,
            request=request,
            actor=identity,
            collect_source=_collect_source,
        )

    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Any, exc: Any) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
        )

    app.include_router(
        create_assistant_router(
            build_context_pack=_build_context_pack,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            bff_error=bff_error,
        )
    )
    return TestClient(app, raise_server_exceptions=False), None




def test_assistant_context_pack_builds_structured_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path, monkeypatch)

    resp = client.post(
        "/bff/assistant/sessions/asst_test/context",
        json={
            "mode": "kernel_debug",
            "question": "Why is the job stale?",
            "include": [
                "ui",
                "control_room",
                "jobs",
                "alerts",
                "audit",
                "recent_sse",
                "persona_health",
                "strategy_health",
                "docs_rag",
            ],
            "frontend": {
                "route": "/agora/ask",
                "selectedEntity": {"entity_type": "job", "entity_id": "job_123"},
                "visibleErrors": [{"message": "Fetch failed with Bearer test-token"}],
                "contextRefs": [{"href": "/bff/jobs/job_123", "sourceId": "jobs"}],
            },
            "focus": {"entity_type": "job", "entity_id": "job_123"},
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    data = body["data"]

    assert data["context_pack_id"].startswith("ctx_")
    assert data["session_id"] == "asst_test"
    assert data["mode"] == "kernel_debug"
    assert data["actor"]["operator_id"] == "asst-kernel"
    assert data["frontend"]["route"] == "/agora/ask"
    assert data["frontend"]["selected_entity"]["entity_id"] == "job_123"
    assert "[REDACTED_TOKEN]" in data["frontend"]["visible_errors"][0]["message"]

    assert data["backend"]["jobs"]["selected"]["job_id"] == "job_123"
    assert data["backend"]["audit"]["items"][0]["target_id"] == "job_123"
    assert isinstance(data["backend"]["recent_sse"], list)

    source_ids = {source["source_id"] for source in data["sources"]}
    assert {
        "ui",
        "control_room",
        "jobs",
        "alerts",
        "audit",
        "recent_sse",
        "persona_health",
        "strategy_health",
        "docs_rag",
    }.issubset(source_ids)
    for source in data["sources"]:
        assert source["snapshot_at"]
        assert source["href"]
        assert isinstance(source["staleness"], dict)
        assert source["staleness"]["status"] in {"fresh", "stale", "unavailable"}

    job_source = next(source for source in data["sources"] if source["source_id"] == "jobs")
    assert job_source["href"] == "/bff/jobs/job_123"
    assert data["ui_hints"]["hint_only"] is True
    assert data["ui_hints"]["authority"] == "frontend_hint_only"
    assert data["ui_hints"]["source_refs"][0]["source_kind"] == "frontend"
    assert data["bff_reads"]["rbac_enforced"] is True
    assert data["bff_reads"]["tenant_filtered"] is True
    assert data["bff_reads"]["context"]["jobs"]["selected"]["job_id"] == "job_123"
    assert data["bff_reads"]["access"]["jobs"]["tenant"]["tenant_id"] == "tenant-alpha"
    assert data["docs_rag"]["items"]
    assert data["docs_rag"]["citations"]
    assert any(ref["source_kind"] == "docs" for ref in data["docs_rag"]["source_refs"])
    assert any(ref["source_id"] == "jobs" for ref in data["source_refs"])
    assert data["redaction"]["enabled"] is True
    assert data["redaction"]["redacted_fields"] >= 1


def test_assistant_context_pack_filters_bff_reads_by_tenant(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path, monkeypatch)

    resp = client.post(
        "/bff/assistant/sessions/asst_tenant/context",
        json={
            "mode": "kernel_debug",
            "include": ["jobs", "audit"],
            "focus": {"entity_type": "job", "entity_id": "job_beta"},
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]

    jobs_context = data["backend"]["jobs"]
    assert jobs_context["selected"] is None
    assert jobs_context["selected_missing"]["reason"] == "job_not_found_or_not_visible"
    assert {job["job_id"] for job in jobs_context["items"]} == {"job_123"}
    assert data["backend"]["audit"]["items"] == []
    assert data["bff_reads"]["access"]["jobs"]["tenant"]["tenant_id"] == "tenant-alpha"
    assert data["bff_reads"]["access"]["audit"]["tenant"]["tenant_id"] == "tenant-alpha"


def test_assistant_context_pack_redacts_source_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    client, original = _client_with_seeded_store(tmp_path, monkeypatch)

    resp = client.post(
        "/bff/assistant/sessions/asst_refs/context",
        json={
            "mode": "user",
            "include": ["ui"],
            "frontend": {"route": "/agora/ask?access_token=frontend-secret-123456"},
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    rendered_refs = repr(data["source_refs"])
    assert "frontend-secret-123456" not in rendered_refs
    assert "[REDACTED_" in rendered_refs
    assert data["redaction"]["redacted_fields"] >= 1


def test_assistant_context_pack_omits_non_allowlisted_sources_and_marks_staleness(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", "stale")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path, monkeypatch)

    resp = client.post(
        "/bff/assistant/sessions/asst_test/context",
        json={
            "mode": "kernel_debug",
            "include": ["jobs", "database_credentials"],
            "focus": {"entity_type": "job", "entity_id": "job_123"},
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]

    assert [source["source_id"] for source in data["sources"]] == ["jobs"]
    omitted = data["omitted_sources"]
    assert omitted == [
        {
            "source_id": "database_credentials",
            "reason": "not_allowlisted",
            "message": "Source 'database_credentials' is not in the assistant context allowlist.",
        }
    ]
    jobs_source = data["sources"][0]
    assert jobs_source["status"] == "degraded"
    assert jobs_source["staleness"]["status"] == "stale"
    assert jobs_source["staleness"]["last_known_at"]


def test_assistant_context_pack_user_mode_rejects_kernel_only_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    client, original = _client_with_seeded_store(tmp_path, monkeypatch)

    resp = client.post(
        "/bff/assistant/sessions/asst_user/context",
        json={
            "mode": "user",
            "include": ["ui", "job_logs"],
            "focus": {"entity_type": "job", "entity_id": "job_123"},
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"]["precondition_failed"] == "assistant_context_mode_policy"
    assert body["error"]["details"]["denied_sources"] == ["job_logs"]
