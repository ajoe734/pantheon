from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer asst-kernel:operator"}


def _seed_store(path: str) -> ReadSurfaceStore:
    store = ReadSurfaceStore(path, allow_local_snapshot_fallback=True)
    store._data["jobs"] = {
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
    store._data["governance_audit_events"] = [
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
    store._data["personas"] = {
        "persona_1": {
            "id": "persona_1",
            "persona_id": "persona_1",
            "name": "Observer",
            "lifecycle_state": "active",
        }
    }
    store._data["strategy_specs"] = {
        "strategy_1": {
            "id": "strategy_1",
            "strategy_id": "strategy_1",
            "name": "Paper Loop",
            "status": "active",
        }
    }
    return store


def _client_with_seeded_store(tmp_path):
    original = bff_main.read_store
    bff_main.read_store = _seed_store(str(tmp_path / "read_surfaces.json"))
    return TestClient(bff_main.app), original


def test_assistant_context_pack_builds_structured_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path)
    try:
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
    finally:
        bff_main.read_store = original


def test_assistant_context_pack_filters_bff_reads_by_tenant(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path)
    try:
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
    finally:
        bff_main.read_store = original


def test_assistant_context_pack_redacts_source_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    client, original = _client_with_seeded_store(tmp_path)
    try:
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
    finally:
        bff_main.read_store = original


def test_assistant_context_pack_omits_non_allowlisted_sources_and_marks_staleness(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", "stale")
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, original = _client_with_seeded_store(tmp_path)
    try:
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
    finally:
        bff_main.read_store = original


def test_assistant_context_pack_user_mode_rejects_kernel_only_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    client, original = _client_with_seeded_store(tmp_path)
    try:
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
    finally:
        bff_main.read_store = original
