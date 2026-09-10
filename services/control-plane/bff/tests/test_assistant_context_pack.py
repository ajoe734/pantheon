from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.assistant.routes import create_assistant_router
from services.control_plane.bff.assistant.context_composer import (
    AssistantCollectedSource,
    compose_context_pack,
)
from services.control_plane.bff.auth.policy import bff_error
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

REPO_ROOT = Path(__file__).resolve().parents[4]
OPERATOR_HEADERS = {"Authorization": "Bearer asst-kernel:operator"}


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


def _surface_status(snapshot_at: str) -> Dict[str, Any]:
    state = os.getenv("BFF_READ_SURFACE_STATE", "ok")
    if state in {"degraded", "stale"}:
        return {
            "status": "degraded",
            "staleness": {
                "status": "stale",
                "served_from": "test_store",
                "last_known_at": snapshot_at,
            },
        }
    return {
        "status": "ok",
        "staleness": {
            "status": "fresh",
            "served_from": "test_store",
            "last_known_at": snapshot_at,
        },
    }


def _assistant_source_access_meta(identity: Optional[OperatorIdentity]) -> Dict[str, Any]:
    roles = list(getattr(identity, "roles", []) or [])
    tenant_id = os.getenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    raw_allowed = os.getenv("PANTHEON_BFF_ALLOWED_TENANTS", "")
    allowed = [t for t in raw_allowed.split(",") if t] or [tenant_id]
    scope = "tenant" if tenant_id else "global"
    return {
        "rbac": {
            "enforced": True,
            "required_roles": ["admin", "approver", "operator", "reviewer"],
            "actor_roles": roles,
        },
        "tenant": {
            "enforced": True,
            "tenant_id": tenant_id,
            "allowed_tenants": allowed,
            "scope": scope,
        },
    }


def _attach_access_meta(
    payload: Any,
    *,
    source_id: str,
    identity: Optional[OperatorIdentity],
    snapshot_at: str,
    surface: Dict[str, Any],
) -> Dict[str, Any]:
    result = dict(payload) if isinstance(payload, dict) else {"data": payload}
    meta = dict(result.get("meta") if isinstance(result.get("meta"), dict) else {})
    meta.setdefault("snapshot_at", snapshot_at)
    meta.setdefault("surfaces", {source_id: surface})
    meta["access"] = _assistant_source_access_meta(identity)
    result["meta"] = meta
    return result


def _focus_entity(request: Any) -> tuple[Optional[str], Optional[str]]:
    focus = getattr(request, "focus", None)
    if isinstance(focus, dict):
        return focus.get("entity_type"), focus.get("entity_id")
    if focus is not None:
        return getattr(focus, "entity_type", None), getattr(focus, "entity_id", None)
    selected = getattr(request, "selected_entity", None)
    if isinstance(selected, dict):
        return selected.get("entity_type"), selected.get("entity_id")
    if selected is not None:
        return getattr(selected, "entity_type", None), getattr(selected, "entity_id", None)
    fe = getattr(request, "frontend", None)
    if fe is not None:
        fe_sel = getattr(fe, "selected_entity", None)
        if isinstance(fe_sel, dict):
            return fe_sel.get("entity_type"), fe_sel.get("entity_id")
        if fe_sel is not None:
            return getattr(fe_sel, "entity_type", None), getattr(fe_sel, "entity_id", None)
    return None, None


def _make_collect_source(store: Any):
    def collect_source(
        source_id: str,
        request: Any,
        snapshot_at: str,
        identity: Optional[OperatorIdentity] = None,
    ) -> Optional[AssistantCollectedSource]:
        surface = _surface_status(snapshot_at)
        entity_type, entity_id = _focus_entity(request)
        tenant_id = os.getenv("PANTHEON_BFF_TENANT_ID", "")

        if source_id == "jobs":
            raw_jobs = store.list_jobs()
            jobs = [j for j in raw_jobs if not tenant_id or j.get("tenant_id") == tenant_id]
            selected_job = None
            href = "/bff/jobs"
            if entity_id:
                href = f"/bff/jobs/{entity_id}"
                raw_job = store.get_job(entity_id)
                if raw_job and (not tenant_id or raw_job.get("tenant_id") == tenant_id):
                    selected_job = raw_job
            payload: Dict[str, Any] = {
                "items": jobs[:20],
                "selected": selected_job,
                "page_info": {"next_page_token": None, "total": len(jobs)},
            }
            if entity_id and selected_job is None:
                payload["selected_missing"] = {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "reason": "job_not_found_or_not_visible",
                }
            return AssistantCollectedSource(
                source_id="jobs",
                href=href,
                payload=_attach_access_meta(payload, source_id="jobs", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "audit":
            href = "/bff/audit"
            if entity_type and entity_id:
                href = f"/bff/audit/entities/{entity_type}/{entity_id}"
                events = [
                    e for e in store.list_governance_audit_events(target_type=entity_type)
                    if str(e.get("target_id") or e.get("entity_id") or "") == entity_id
                ]
            else:
                events = store.list_governance_audit_events()
            if tenant_id:
                events = [e for e in events if e.get("tenant_id") == tenant_id]
            payload = {
                "items": events[:50],
                "page_info": {"next_page_token": None, "total": len(events)},
            }
            return AssistantCollectedSource(
                source_id="audit",
                href=href,
                payload=_attach_access_meta(payload, source_id="audit", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "recent_sse":
            events = store.list_events_bff(page_size=25)
            return AssistantCollectedSource(
                source_id="recent_sse",
                href="/bff/events",
                payload=_attach_access_meta({"items": events[:25], "page_info": {"next_page_token": None}}, source_id="recent_sse", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "control_room":
            return AssistantCollectedSource(
                source_id="control_room",
                href="/bff/v5/control-room",
                payload=_attach_access_meta({"items": []}, source_id="control_room", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "alerts":
            return AssistantCollectedSource(
                source_id="alerts",
                href="/bff/alerts",
                payload=_attach_access_meta({"alerts": []}, source_id="alerts", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "persona_health":
            personas = store.list_personas()
            return AssistantCollectedSource(
                source_id="persona_health",
                href="/bff/v5/execution/persona-health",
                payload=_attach_access_meta({"items": personas}, source_id="persona_health", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "strategy_health":
            specs = store.list_strategy_specs()
            return AssistantCollectedSource(
                source_id="strategy_health",
                href="/bff/v5/execution/strategy-health",
                payload=_attach_access_meta({"items": specs}, source_id="strategy_health", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "job_logs":
            return AssistantCollectedSource(
                source_id="job_logs",
                href=f"/bff/jobs/{entity_id}/logs" if entity_id else "/bff/jobs/logs",
                payload=_attach_access_meta({"job_id": entity_id, "logs": []}, source_id="job_logs", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
            )

        if source_id == "docs_rag":
            doc_file = REPO_ROOT / "AI_COLLABORATION_GUIDE.md"
            snippet = doc_file.read_text(encoding="utf-8")[:500] if doc_file.exists() else "guide text"
            citation = {
                "ref_id": "doc:ai_collaboration_guide",
                "title": "Pantheon AI collaboration guide",
                "snippet": snippet,
                "href": "docs://assistant/context/ai_collaboration_guide",
                "source_kind": "docs",
            }
            source_ref = {
                "source_id": "docs_rag",
                "source_kind": "docs",
                "href": "docs://assistant/context",
                "status": "ok",
                "staleness": {"status": "fresh", "last_known_at": snapshot_at},
            }
            payload = {
                "items": [{"slug": "ai_collaboration_guide", "title": "AI Guide"}],
                "citations": [citation],
            }
            return AssistantCollectedSource(
                source_id="docs_rag",
                href="docs://assistant/context",
                payload=_attach_access_meta(payload, source_id="docs_rag", identity=identity, snapshot_at=snapshot_at, surface=surface),
                status=surface["status"],
                staleness=surface.get("staleness"),
                source_refs=[source_ref],
            )

        return None
    return collect_source


def _client_with_seeded_store(tmp_path):
    store = _seed_store(str(tmp_path / "read_surfaces.json"))
    collector = _make_collect_source(store)

    def build_context_pack(session_id, request, identity):
        return compose_context_pack(
            session_id=session_id,
            request=request,
            actor=identity,
            collect_source=collector,
        )

    def extract_identity(auth=None):
        return OperatorIdentity(
            operator_id="asst-kernel" if "asst-kernel" in str(auth or "") else "asst-user",
            roles=["operator", "admin"],
        )

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    router = create_assistant_router(
        build_context_pack=build_context_pack,
        extract_identity=extract_identity,
        require_read_role=lambda id: None,
        bff_error=bff_error,
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False), None


def test_assistant_context_pack_builds_structured_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path)

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
    client, original = _client_with_seeded_store(tmp_path)

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
    client, original = _client_with_seeded_store(tmp_path)

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
    client, original = _client_with_seeded_store(tmp_path)

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
    client, original = _client_with_seeded_store(tmp_path)

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
