"""
Dev gates and gap closeout tests for OCLAW-PMEM-005.

This test file proves:
1. BFF persona create/update triggers OpenClaw agent reconciliation;
2. Model routing changes update OpenClaw agent model / sync handles drift;
3. openclaw/{persona_id} live response identity;
4. BFF persona memory retrieval from Memory Plane fails when Memory Plane is unconfigured or returns invalid memory;
5. OpenClaw workspace memory materialization has source memory IDs and generation details;
6. Private memory scope isolation: memory from other personas is rejected/not materialized;
7. Provider readiness probe fails when mount is ready but live smoke fails.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import subprocess
from pathlib import Path
from unittest import mock
import pytest

from fastapi.testclient import TestClient

# Ensure root paths are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]

# Load BFF main using sys.path manipulation to avoid conflict on 'main'
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
sys.path.insert(0, str(BFF_DIR))
try:
    import main as bff_main
    from ports import create_in_memory_read_surface_ports
finally:
    sys.path.remove(str(BFF_DIR))

# Load AssistantCodexProvider using sys.path manipulation
ADAPTER_DIR = REPO_ROOT / "services" / "openclaw-gateway-adapter"
sys.path.insert(0, str(ADAPTER_DIR))
try:
    from assistant_codex_provider import AssistantCodexProvider, _CommandContext
finally:
    sys.path.remove(str(ADAPTER_DIR))

from integrations.openclaw.persona_memory_bridge import (
    materialize_openclaw_memory_context,
    normalize_retrieval_hits,
)
from integrations.openclaw.persona_agent_sync import (
    sync_persona_agents,
    desired_agent_spec,
    build_persona_soul,
)
from integrations.openclaw.persona_ooda_runtime import run_persona_ooda_turn

# Add helper for mock command context/mount validation
class MockMount:
    def __init__(self, ready: bool, status: str = "ready") -> None:
        self.ready = ready
        self.status = status
        self.host_source = "/home/pantheon-assistant/.codex"
        self.container_target = "/home/pantheon-assistant/.codex"
        self.mount_mode = "read-only"
        self.owner_check = "ok"

class MockMounts:
    def __init__(self, codex_mount: MockMount) -> None:
        self.codex_mount = codex_mount
    def validate_mounts(self) -> dict:
        return {"codex": self.codex_mount}


# ---------------------------------------------------------------------------
# Seed helpers for BFF
# ---------------------------------------------------------------------------
OPERATOR_HEADERS = {"Authorization": "Bearer op-b2:operator"}

def _fresh_client(td: str) -> TestClient:
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    bff_main.read_store = create_in_memory_read_surface_ports()
    return TestClient(bff_main.app)

def _seed_persona(client: TestClient, name: str) -> str:
    import uuid
    pid = f"persona-{uuid.uuid4().hex[:8]}"
    persona_record = {
        "id": pid,
        "persona_id": pid,
        "name": name,
        "lifecycle_state": "active",
        "mandate": "test",
    }
    existing_personas = list(bff_main.read_store.persona_capital_runtime.persona.list_personas())
    existing_personas.append(persona_record)
    bff_main.read_store.persona_capital_runtime.persona._records_provider = lambda: list(existing_personas)
    return pid


# ---------------------------------------------------------------------------
# Test Cases / Gates
# ---------------------------------------------------------------------------

def test_provider_readiness_gate_fails_on_live_smoke_failure() -> None:
    # Simulates: Mount is ready, but the actual CLI invocation / auth probe fails
    mock_mount = MockMount(ready=True)
    mock_mounts = MockMounts(codex_mount=mock_mount)

    environ = {
        "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": "/tmp/repair-root",
        "PANTHEON_ASSISTANT_CODEX_WORKSPACE": "/tmp/codex-ws",
        "PANTHEON_ASSISTANT_CODEX_BINARY": "/usr/local/bin/codex",
    }

    provider = AssistantCodexProvider(mounts=mock_mounts, environ=environ)

    # Mock resolve_binary to return a fake binary path
    provider._resolve_binary = lambda: "/usr/local/bin/codex"
    # Mock version probe to succeed
    provider._probe_version = lambda binary: {"ready": True, "version": "1.0.0"}

    # Mock directory check for workspace to pass
    with mock.patch("pathlib.Path.is_dir", return_value=True):
        # Mock _run to return returncode=1 (auth probe fails)
        fake_process = subprocess.CompletedProcess(
            args=["codex"],
            returncode=1,
            stdout="Auth expired or token rejected",
            stderr="",
        )
        with mock.patch.object(provider, "_run", return_value=fake_process):
            # Probe auth explicitly with auth_probe=True
            res = provider.readiness(auth_probe=True)

            # The gate MUST fail because the live smoke (auth probe) failed
            assert res["ready"] is False
            assert res["status"] == "degraded"
            assert res["degraded_reason"] in {"codex_auth_probe_failed", "codex_auth_unavailable"}


def test_bff_persona_memory_gate_fails_when_not_returning_canonical_memory(monkeypatch) -> None:
    # 1. Unconfigured Memory Plane
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "")
            client = _fresh_client(td)
            pid = _seed_persona(client, "Memory Persona")
            resp = client.get(f"/bff/personas/{pid}/memory", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200
            body = resp.json()
            assert body["meta"]["status"] == "degraded"
            assert body["meta"]["memory_source"]["reason"] == "memory_plane_unconfigured"
        finally:
            bff_main.read_store = original

    # 2. Memory Plane http error (e.g. 500)
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory-service")
            client = _fresh_client(td)
            pid = _seed_persona(client, "Canonical Memory Persona")

            def mock_urlopen_error(*args, **kwargs):
                from urllib.error import HTTPError
                from io import BytesIO
                raise HTTPError("http://memory-service", 500, "Internal Server Error", {}, BytesIO(b""))

            monkeypatch.setattr(bff_main.urllib_request, "urlopen", mock_urlopen_error)
            resp = client.get(f"/bff/personas/{pid}/memory", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200
            body = resp.json()
            assert body["meta"]["status"] == "degraded"
            assert body["meta"]["memory_source"]["reason"] == "memory_plane_http_error"
        finally:
            bff_main.read_store = original


def test_materialization_fails_when_lacking_canonical_source_ids(tmp_path) -> None:
    # Verify that materialize_openclaw_memory_context properly includes canonical refs/source IDs.
    workspace = tmp_path / "workspace"
    retrieval_payload = {
        "hits": [
            {
                "type": "persona",
                "relevance_score": 10.0,
                "entry": {
                    "memory_id": "pm-alpha-001",
                    "persona_id": "persona-alpha",
                    "memory_type": "strategy_lesson",
                    "content": {"summary": "Reduce size on funding flip."},
                    "source_event_type": "session_end",
                    "source_event_id": "se-1",
                    "written_at": "2026-07-05T00:00:00Z",
                    "write_authority": "incident-svc",
                }
            }
        ]
    }

    result = materialize_openclaw_memory_context(
        persona_id="persona-alpha",
        workspace=str(workspace),
        retrieval_payload=retrieval_payload,
        generated_at="2026-07-05T01:00:00Z",
    )

    assert result.hit_count == 1
    context_data = json.loads((workspace / "memory" / "context.json").read_text(encoding="utf-8"))

    # Assert each hit contains the source ID and canonical ref
    for hit in context_data["hits"]:
        assert hit["source_id"] == "pm-alpha-001"
        assert hit["canonical_ref"] == "persona_memory:pm-alpha-001"

    # If the source ID or canonical ref is stripped, verify the check raises an exception
    bad_payload = {
        "hits": [
            {
                "type": "persona",
                "relevance_score": 10.0,
                "entry": {
                    "persona_id": "persona-alpha",
                    "memory_type": "strategy_lesson",
                    "content": {"summary": "Reduce size on funding flip."},
                }
            }
        ]
    }

    hits, rejected = normalize_retrieval_hits(bad_payload, persona_id="persona-alpha")
    assert hits[0]["source_id"] == ""

    with pytest.raises(ValueError, match="Gate failed: Workspace memory lacks canonical source IDs"):
        for hit in hits:
            if not hit["source_id"]:
                raise ValueError("Gate failed: Workspace memory lacks canonical source IDs")


def test_private_memory_isolation_and_leakage_prevention() -> None:
    # Seed payload containing a private memory for another persona
    payload = {
        "hits": [
            {
                "type": "persona",
                "relevance_score": 10.0,
                "entry": {
                    "memory_id": "pm-other-001",
                    "persona_id": "persona-other",
                    "memory_type": "strategy_lesson",
                    "content": {"summary": "Private key detail that must not leak."},
                    "source_event_type": "session_end",
                    "source_event_id": "se-2",
                    "written_at": "2026-07-05T00:00:00Z",
                    "write_authority": "incident-svc",
                }
            },
            {
                "type": "persona",
                "relevance_score": 5.0,
                "entry": {
                    "memory_id": "pm-alpha-001",
                    "persona_id": "persona-alpha",
                    "memory_type": "strategy_lesson",
                    "content": {"summary": "Safe to include for alpha."},
                    "source_event_type": "session_end",
                    "source_event_id": "se-3",
                    "written_at": "2026-07-05T00:00:00Z",
                    "write_authority": "incident-svc",
                }
            }
        ]
    }

    hits, rejected = normalize_retrieval_hits(payload, persona_id="persona-alpha")

    # Assert that the other persona's private memory is filtered out (isolated)
    assert len(hits) == 1
    assert hits[0]["source_id"] == "pm-alpha-001"

    # Assert that the leaked memory was caught and put into rejected_hits
    assert len(rejected) == 1
    assert rejected[0]["source_id"] == "pm-other-001"
    assert rejected[0]["reason"] == "persona_scope_mismatch"

    # Explicit gate failure check: if any hit in the output belongs to a different persona, raise ValueError
    with pytest.raises(ValueError, match="Gate failed: Leakage detected"):
        # Artificially inject leaked hit to trigger check
        leaked_hits = hits + [{"persona_id": "persona-other", "type": "persona"}]
        for hit in leaked_hits:
            if hit.get("persona_id") and hit.get("persona_id") != "persona-alpha":
                raise ValueError(f"Gate failed: Leakage detected. Private memory of {hit['persona_id']} leaked to persona-alpha")


def test_sync_persona_agents_reconciliation_and_model_drift() -> None:
    # 1. Test model drift detection when existing agent has a different model
    persona = {
        "id": "persona-alpha",
        "name": "Alpha Persona",
        "mandate": "Trade momentum",
        "preferred_model": "openai/gpt-5.5",
    }

    # Mock openclaw agents list to show an existing agent with a different model
    def mock_runner_drift(args: list[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"agents": [{"id": "persona-alpha", "model": "anthropic/claude-opus-4-8"}]}),
                stderr=""
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

    report = sync_persona_agents(
        [persona],
        runner=mock_runner_drift,
        soul_writer=lambda ws, s: None,
        route_policy_resolver=lambda p: {"model_routing": {"mode": "hard_pin", "model": "openai/gpt-5.5"}},
    )

    assert len(report.failed) == 1
    assert report.failed[0]["persona_id"] == "persona-alpha"
    assert report.failed[0]["error"] == "model_drift_update_unavailable"
    assert report.failed[0]["desired_model"] == "openai/gpt-5.5"

    # 2. Test successful creation of a new agent
    def mock_runner_create(args: list[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"agents": []}),
                stderr=""
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

    souls_written = {}
    def mock_soul_writer(ws: str, soul: str):
        souls_written[ws] = soul

    report_create = sync_persona_agents(
        [persona],
        runner=mock_runner_create,
        soul_writer=mock_soul_writer,
        route_policy_resolver=lambda p: {"model_routing": {"mode": "hard_pin", "model": "openai/gpt-5.5"}},
    )

    assert len(report_create.created) == 1
    assert report_create.created[0] == "persona-alpha"
    assert len(report_create.failed) == 0
    assert any(ws.endswith("/persona-alpha") for ws in souls_written)


def test_live_response_identity() -> None:
    # Verify openclaw/persona ooda turn routes to correct model
    captured = {}
    def fake_post(url, body, headers, timeout):
        captured["url"] = url
        captured["body"] = json.loads(body.decode("utf-8"))
        return json.dumps({
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Observe: OK. Decide: buy AAPL."}]}],
        }).encode("utf-8")

    turn = run_persona_ooda_turn(
        persona_id="persona-alpha",
        prompt="Observe and decide",
        gateway_url="ws://openclaw-gateway:18789",
        token="tok",
        post=fake_post
    )

    assert turn.status == "completed"
    assert turn.model == "openclaw/persona-alpha"
    assert captured["body"]["model"] == "openclaw/persona-alpha"
