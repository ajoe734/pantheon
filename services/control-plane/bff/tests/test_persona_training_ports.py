"""Tests for the Persona/Training-Session domain ports.

Validates:
1. PersonaRegistryReadsPort delegates persona/session/teaching/capability
   reads to an injected Persona Registry-backed store and merges bindings
2. TrainingSessionTrainerPort delegates every trainer/replay method to an
   injected Training Session-backed store
3. RapidEvaluationPort exposes a fixed Training Session owner (ACG-02-017)
   and only executes once a caller binds a backend
4. PersonaTrainingDomainPort composes all three ports
"""

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    pass

from ports.persona_training import (
    PersonaRegistryReadsPort,
    PersonaTrainingDomainPort,
    RapidEvaluationOwnership,
    RapidEvaluationPort,
    TrainingSessionHttpStore,
    TrainingSessionTrainerPort,
)
from services.control_plane.bff.core.owner_reads import authorization, selected_tenant


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakePersonaRegistryStore:
    def __init__(self) -> None:
        self.personas = {
            "persona-alpha": {"persona_id": "persona-alpha", "lifecycle_state": "active"},
        }
        self.bindings = {"persona-alpha": [{"binding_id": "bind-1"}]}
        self.sessions = {"persona-alpha": [{"session_id": "sess-1"}]}
        self.teaching_sessions = {"persona-alpha": [{"session_id": "teach-1"}]}
        self.capabilities = {"persona-alpha": {"snapshot_id": "cap-1"}}
        self.list_calls: List[Dict[str, Any]] = []

    def list_personas(self, *, lifecycle_state=None, mandate=None, strategy_family=None):
        self.list_calls.append(
            {
                "lifecycle_state": lifecycle_state,
                "mandate": mandate,
                "strategy_family": strategy_family,
            }
        )
        return list(self.personas.values())

    def get_persona(self, persona_id):
        return self.personas.get(persona_id)

    def get_bindings_for_persona(self, persona_id):
        return self.bindings.get(persona_id, [])

    def list_sessions_for_persona(self, persona_id, *, status=None):
        return self.sessions.get(persona_id, [])

    def list_teaching_sessions_for_persona(self, persona_id, *, status=None):
        return self.teaching_sessions.get(persona_id, [])

    def get_capability_snapshot_for_persona(self, persona_id):
        return self.capabilities.get(persona_id)


class _FakeTrainingSessionStore:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def create_trainer_session(self, **kwargs):
        self.calls.append(("create_trainer_session", kwargs))
        return {"session_id": "trn-1", **kwargs}

    def list_trainer_sessions(self, **kwargs):
        self.calls.append(("list_trainer_sessions", kwargs))
        return [{"session_id": "trn-1"}]

    def get_trainer_session(self, session_id):
        self.calls.append(("get_trainer_session", session_id))
        return {"session_id": session_id}

    def get_trainer_controls(self, session_id, **kwargs):
        self.calls.append(("get_trainer_controls", session_id))
        return {"session_id": session_id, "controls": [], **kwargs}

    def patch_trainer_controls(self, session_id, **kwargs):
        self.calls.append(("patch_trainer_controls", session_id, kwargs))
        return {"session_id": session_id, **kwargs}

    def append_trainer_message(self, session_id, **kwargs):
        self.calls.append(("append_trainer_message", session_id, kwargs))
        return {"session_id": session_id, **kwargs}

    def get_trainer_preview(self, session_id, **kwargs):
        self.calls.append(("get_trainer_preview", session_id, kwargs))
        return {"session_id": session_id}

    def refresh_trainer_preview(self, session_id, **kwargs):
        self.calls.append(("refresh_trainer_preview", session_id, kwargs))
        return {"session_id": session_id}

    def list_trainer_replays(self, **kwargs):
        self.calls.append(("list_trainer_replays", kwargs))
        return [{"session_id": "trn-1"}]

    def get_trainer_replay(self, session_id):
        self.calls.append(("get_trainer_replay", session_id))
        return {"session_id": session_id}

    def commit_trainer_replay(self, session_id, **kwargs):
        self.calls.append(("commit_trainer_replay", session_id, kwargs))
        return {"session_id": session_id, "status": "committed"}

    def discard_trainer_replay(self, session_id, **kwargs):
        self.calls.append(("discard_trainer_replay", session_id, kwargs))
        return {"session_id": session_id, "status": "discarded"}


# ---------------------------------------------------------------------------
# 1. PersonaRegistryReadsPort
# ---------------------------------------------------------------------------

class TestPersonaRegistryReadsPort:
    def test_requires_store(self):
        port = PersonaRegistryReadsPort()
        with pytest.raises(RuntimeError):
            port.list_personas()

    def test_list_personas_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        result = port.list_personas(lifecycle_state="active")
        assert result == [{"persona_id": "persona-alpha", "lifecycle_state": "active"}]
        assert store.list_calls == [
            {"lifecycle_state": "active", "mandate": None, "strategy_family": None}
        ]

    def test_get_persona_merges_bindings(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        result = port.get_persona("persona-alpha")
        assert result["persona_id"] == "persona-alpha"
        assert result["bindings"] == [{"binding_id": "bind-1"}]

    def test_get_persona_missing_returns_none(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.get_persona("persona-missing") is None

    def test_list_persona_sessions_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.list_persona_sessions("persona-alpha") == [{"session_id": "sess-1"}]

    def test_list_persona_teaching_sessions_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.list_persona_teaching_sessions("persona-alpha") == [
            {"session_id": "teach-1"}
        ]

    def test_get_persona_capabilities_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.get_persona_capabilities("persona-alpha") == {"snapshot_id": "cap-1"}


# ---------------------------------------------------------------------------
# 2. TrainingSessionTrainerPort
# ---------------------------------------------------------------------------

class TestTrainingSessionTrainerPort:
    def test_requires_training_store(self):
        port = TrainingSessionTrainerPort()
        with pytest.raises(RuntimeError):
            port.list_trainer_sessions()

    def test_create_trainer_session_delegates(self):
        training = _FakeTrainingSessionStore()
        port = TrainingSessionTrainerPort(training=training)
        result = port.create_trainer_session(persona_id="persona-alpha", objective="obj")
        assert result["session_id"] == "trn-1"
        assert training.calls[0][0] == "create_trainer_session"

    def test_replay_methods_delegate(self):
        training = _FakeTrainingSessionStore()
        port = TrainingSessionTrainerPort(training=training)

        assert port.list_trainer_replays() == [{"session_id": "trn-1"}]
        assert port.get_trainer_replay("trn-1") == {"session_id": "trn-1"}
        assert port.commit_trainer_replay("trn-1", actor_id="op-1")["status"] == "committed"
        assert port.discard_trainer_replay("trn-1", actor_id="op-1")["status"] == "discarded"

        called_methods = [call[0] for call in training.calls]
        assert called_methods == [
            "list_trainer_replays",
            "get_trainer_replay",
            "commit_trainer_replay",
            "discard_trainer_replay",
        ]

    def test_control_and_preview_methods_delegate(self):
        training = _FakeTrainingSessionStore()
        port = TrainingSessionTrainerPort(training=training)

        assert port.get_trainer_controls("trn-1")["session_id"] == "trn-1"
        assert port.get_trainer_controls("trn-1", snapshot_at="2026-10-15T10:19:36Z")["snapshot_at"] == "2026-10-15T10:19:36Z"
        assert port.patch_trainer_controls("trn-1", patch={})["session_id"] == "trn-1"
        assert port.append_trainer_message("trn-1", text="hi")["session_id"] == "trn-1"
        assert port.get_trainer_preview("trn-1")["session_id"] == "trn-1"
        assert port.refresh_trainer_preview("trn-1")["session_id"] == "trn-1"


# ---------------------------------------------------------------------------
# 3. RapidEvaluationPort
# ---------------------------------------------------------------------------

class TestRapidEvaluationPort:
    def test_owner_is_training_session(self):
        port = RapidEvaluationPort()
        assert port.owner == "training-session"
        assert isinstance(port.ownership, RapidEvaluationOwnership)
        assert port.ownership.implementation_symbol == "run_rapid_eval"
        assert (
            port.ownership.implementation_module
            == "services/training-session/rapid_eval_integration.py"
        )

    def test_create_without_backend_raises(self):
        port = RapidEvaluationPort()
        with pytest.raises(RuntimeError, match="training-session"):
            port.create_rapid_eval("trn-1", eval_scope="full")

    def test_get_without_backend_raises(self):
        port = RapidEvaluationPort()
        with pytest.raises(RuntimeError, match="training-session"):
            port.get_rapid_eval("reval-1")

    def test_create_and_get_delegate_to_bound_backend(self):
        created: List[Any] = []
        fetched: List[Any] = []

        def fake_create(session_id, **kwargs):
            created.append((session_id, kwargs))
            return {"rapid_eval_id": "reval-1", "session_id": session_id}

        def fake_get(eval_id, **kwargs):
            fetched.append((eval_id, kwargs))
            return {"rapid_eval_id": eval_id}

        port = RapidEvaluationPort(create=fake_create, get=fake_get)
        result = port.create_rapid_eval("trn-1", eval_scope="full")
        assert result == {"rapid_eval_id": "reval-1", "session_id": "trn-1"}
        assert created == [("trn-1", {"eval_scope": "full"})]

        result = port.get_rapid_eval("reval-1")
        assert result == {"rapid_eval_id": "reval-1"}
        assert fetched == [("reval-1", {})]


# ---------------------------------------------------------------------------
# 4. PersonaTrainingDomainPort
# ---------------------------------------------------------------------------

class TestPersonaTrainingDomainPort:
    def test_defaults_to_unbound_component_ports(self):
        domain_port = PersonaTrainingDomainPort()
        assert domain_port.rapid_eval_owner == "training-session"
        with pytest.raises(RuntimeError):
            domain_port.list_personas()

    def test_composes_injected_ports(self):
        persona_store = _FakePersonaRegistryStore()
        training_store = _FakeTrainingSessionStore()
        rapid_eval_calls: List[Any] = []

        domain_port = PersonaTrainingDomainPort(
            persona_port=PersonaRegistryReadsPort(store=persona_store),
            trainer_port=TrainingSessionTrainerPort(training=training_store),
            rapid_eval_port=RapidEvaluationPort(
                create=lambda session_id, **kwargs: rapid_eval_calls.append(
                    (session_id, kwargs)
                )
                or {"rapid_eval_id": "reval-1"},
            ),
        )

        assert domain_port.list_personas() == [
            {"persona_id": "persona-alpha", "lifecycle_state": "active"}
        ]
        assert domain_port.get_persona("persona-alpha")["bindings"] == [
            {"binding_id": "bind-1"}
        ]
        assert domain_port.list_trainer_sessions() == [{"session_id": "trn-1"}]
        assert domain_port.get_trainer_session("trn-1") == {"session_id": "trn-1"}
        assert domain_port.create_rapid_eval("trn-1", eval_scope="full") == {
            "rapid_eval_id": "reval-1"
        }
        assert rapid_eval_calls == [("trn-1", {"eval_scope": "full"})]
        assert domain_port.rapid_eval_owner == "training-session"


# ---------------------------------------------------------------------------
# 5. Production Default Wiring & Live Owner Error Reproduction (AC-1, AC-5)
# ---------------------------------------------------------------------------

class TestProductionDefaultOwnerWiring:
    def test_unbound_trainer_port_reproduces_live_500_root_cause(self):
        """Reproduce the live 500 root cause: TrainingSessionTrainerPort(training=None)."""
        port = TrainingSessionTrainerPort(training=None)
        with pytest.raises(
            RuntimeError,
            match="TrainingSessionTrainerPort requires a Training Session-backed store",
        ):
            port.list_trainer_sessions()

    def test_unbound_rapid_eval_port_raises_runtime_error(self):
        """Reproduce unbound rapid-eval port: RapidEvaluationPort(create=None)."""
        port = RapidEvaluationPort()
        with pytest.raises(
            RuntimeError,
            match="RapidEvaluationPort has no backend bound to its owner",
        ):
            port.create_rapid_eval("trn-1")

    def test_default_read_surface_ports_composition(self):
        """Verify default create_read_surface_ports binds TrainingSessionHttpStore and leaves RapidEval unbound."""
        from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports
        read_ports = create_read_surface_ports()
        assert (
            read_ports.persona_training.trainer._training.__class__.__name__
            == "TrainingSessionHttpStore"
        )
        assert read_ports.persona_training.rapid_eval._create is None
        assert read_ports.persona_training.rapid_eval._get is None

    def test_default_read_surface_ports_rejects_fabricated_rapid_eval(self):
        """Verify default ReadSurfacePorts rejects fabricated rapid-eval queued state."""
        from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports
        read_ports = create_read_surface_ports()
        with pytest.raises(
            RuntimeError,
            match="RapidEvaluationPort has no backend bound to its owner",
        ):
            read_ports.create_rapid_eval("trn-20261012-001", eval_scope="persona_patch")

    def test_default_read_surface_ports_get_trainer_controls_signature_accepts_snapshot_at(self):
        """Verify default ReadSurfacePorts.get_trainer_controls accepts kwargs / snapshot_at without signature mismatch."""
        import inspect
        from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports

        read_ports = create_read_surface_ports()
        sig = inspect.signature(read_ports.get_trainer_controls)
        assert "kwargs" in sig.parameters or "snapshot_at" in sig.parameters


class TestTrainingSessionHttpStoreUnit:
    """Unit tests for TrainingSessionHttpStore against an ephemeral HTTP test server."""

    @pytest.fixture
    def mock_server(self):
        requests_log = []

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))

            def do_GET(self):
                body = None
                requests_log.append(("GET", self.path, dict(self.headers), body))
                if self.path.startswith("/api/training/sessions/trn-1"):
                    self._send_json(200, {
                        "session_id": "trn-1",
                        "persona_id": "persona-1",
                        "objective": "test objective",
                        "status": "active",
                        "events": [{"sequence_number": 1, "message_body": "hello", "actor": "operator"}],
                    })
                elif self.path.startswith("/api/training/sessions"):
                    self._send_json(200, [
                        {
                            "session_id": "trn-1",
                            "persona_id": "persona-1",
                            "objective": "test objective",
                            "status": "active",
                            "started_at": "2026-10-12T00:00:00Z",
                            "events": [],
                        }
                    ])
                elif self.path.startswith("/api/training/controls/trn-1"):
                    self._send_json(200, {"session_id": "trn-1", "controls": [{"parameter_key": "param1", "current_value": 1.0}]})
                elif self.path.startswith("/api/training/preview-jobs"):
                    jobs = []
                    if "session_id=trn-new" in self.path:
                        for r in requests_log:
                            if r[0] == "POST" and "trn-new/preview-jobs" in r[1]:
                                jobs.append({
                                    "job_id": "job-new-1",
                                    "session_id": "trn-new",
                                    "eval_id": "teval-new-001",
                                    "status": "queued",
                                    "requested_at": "2026-10-12T00:01:00Z",
                                })
                    elif "session_id=trn-failed" in self.path:
                        jobs.append({
                            "job_id": "job-failed-1",
                            "session_id": "trn-failed",
                            "eval_id": "teval-failed-001",
                            "status": "failed",
                            "error_code": "evaluation_admission_rejected",
                            "failure_reason": "candidate controls do not contain an effective change",
                            "requested_at": "2026-10-12T00:01:00Z",
                        })
                    elif "session_id=trn-malformed" in self.path:
                        self._send_json(200, {"malformed": "not a list"})
                        return
                    elif "session_id=trn-running-no-jobid" in self.path:
                        jobs.append({
                            "session_id": "trn-running-no-jobid",
                            "eval_id": "eval-source",
                            "status": "running",
                        })
                    elif "session_id=trn-completed-no-eval" in self.path:
                        jobs.append({
                            "job_id": "job-source",
                            "session_id": "trn-completed-no-eval",
                            "eval_id": "eval-source",
                            "status": "completed",
                        })
                    self._send_json(200, jobs)
                elif self.path.startswith("/api/training/previews/trn-running-no-jobid"):
                    self._send_json(200, {
                        "session_id": "trn-running-no-jobid",
                        "evaluations": {},
                        "preview": {"session_id": "trn-running-no-jobid", "status": "preview_unavailable"},
                    })
                elif self.path.startswith("/api/training/previews/trn-completed-no-eval"):
                    self._send_json(200, {
                        "session_id": "trn-completed-no-eval",
                        "evaluations": {},
                        "preview": {"session_id": "trn-completed-no-eval", "status": "preview_unavailable"},
                    })
                elif self.path.startswith("/api/training/previews/trn-failed"):
                    self._send_json(200, {
                        "session_id": "trn-failed",
                        "evaluations": {},
                        "preview": {
                            "eval_id": "teval-failed-001",
                            "session_id": "trn-failed",
                            "status": "preview_unavailable",
                            "metric_delta": {},
                            "control_diff": [],
                        },
                    })
                elif self.path.startswith("/api/training/previews/trn-malformed"):
                    self._send_json(200, {
                        "session_id": "trn-malformed",
                        "evaluations": {},
                        "preview": {"session_id": "trn-malformed", "status": "preview_unavailable"},
                    })
                elif self.path.startswith("/api/training/previews/trn-new"):
                    self._send_json(200, {
                        "session_id": "trn-new",
                        "evaluations": {},
                        "preview": {
                            "eval_id": "teval-new-001",
                            "session_id": "trn-new",
                            "status": "preview_unavailable",
                            "metric_delta": {},
                            "control_diff": [],
                        },
                    })
                elif self.path.startswith("/api/training/previews/trn-1"):
                    self._send_json(200, {
                        "latest_eval_id": "teval-1",
                        "evaluations": {
                            "teval-1": {
                                "eval_id": "teval-1",
                                "status": "complete",
                                "warnings": [{"level": "medium", "message": "caution"}],
                            }
                        }
                    })
                elif self.path.startswith("/api/training/replays"):
                    self._send_json(200, [
                        {
                            "session_id": "trn-1",
                            "persona_id": "persona-1",
                            "objective": "replay obj",
                            "status": "completed",
                            "events": [{"sequence_number": 1, "event_type": "preview_trigger", "outcome_signal": "ok"}],
                            "replay_resolution": {"state": "pending_decision"},
                            "artifacts": {"candidate_artifact_ref": "art-1"},
                        }
                    ])
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length).decode("utf-8") if length else ""
                body = json.loads(raw_body) if raw_body else {}
                requests_log.append(("POST", self.path, dict(self.headers), body))
                if self.path == "/api/training/sessions":
                    self._send_json(200, {
                        "session_id": "trn-new",
                        "persona_id": body.get("persona_id"),
                        "objective": body.get("objective"),
                        "status": "active",
                        "events": [],
                    })
                elif self.path.startswith("/api/training/sessions/trn-1/controls"):
                    self._send_json(200, {
                        "session_id": "trn-1",
                        "status": "accepted",
                        "controls": [{"parameter_key": "p1", "current_value": 2.0}],
                        "patch_delta": [{"parameter_key": "p1", "previous_value": 1.0, "new_value": 2.0}],
                    })
                elif self.path.startswith("/api/training/sessions/trn-1/events"):
                    self._send_json(200, {
                        "accepted_at": "2026-10-12T00:01:00Z",
                        "event": {
                            "event_id": "tevt-001",
                            "session_id": "trn-1",
                            "actor": body.get("actor", "operator"),
                            "message_body": body.get("message_body"),
                            "sequence_number": 1,
                        },
                        "session": {
                            "session_id": "trn-1",
                            "status": "active",
                            "events": [{
                                "event_id": "tevt-001",
                                "actor": body.get("actor", "operator"),
                                "message_body": body.get("message_body"),
                                "sequence_number": 1,
                            }],
                        },
                    })
                elif self.path.startswith("/api/training/sessions/trn-failed/preview-jobs"):
                    self._send_json(200, {"eval_id": "teval-failed-002", "job_id": "job-failed-2", "status": "queued"})
                elif self.path.startswith("/api/training/sessions/trn-new/preview-jobs"):
                    self._send_json(200, {"eval_id": "teval-new-001", "job_id": "job-new-1", "status": "queued"})
                elif self.path.startswith("/api/training/sessions/trn-1/preview-jobs"):
                    self._send_json(200, {"eval_id": "teval-job-1", "job_id": "job-123", "status": "queued"})
                elif self.path.startswith("/api/training/sessions/trn-malformed/preview-jobs"):
                    self._send_json(200, {"status": "queued"})
                elif self.path.startswith("/api/training/replays/trn-1/commit"):
                    self._send_json(200, {
                        "session_id": "trn-1",
                        "status": "completed",
                        "replay_resolution": {"state": "committed", "decision_at": "2026-10-12T00:02:00Z", "decision_by": "op-1"},
                        "artifacts": {"candidate_artifact_ref": "art-1"},
                        "events": [],
                    })
                elif self.path.startswith("/api/training/replays/trn-1/discard"):
                    self._send_json(200, {
                        "session_id": "trn-1",
                        "status": "completed",
                        "replay_resolution": {"state": "discarded", "decision_at": "2026-10-12T00:02:00Z", "decision_by": "op-1"},
                        "artifacts": {"candidate_artifact_ref": "art-1"},
                        "events": [],
                    })
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        try:
            yield f"http://127.0.0.1:{port}", requests_log
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_store_auth_and_tenant_forwarding(self, mock_server):
        base_url, requests_log = mock_server
        store = TrainingSessionHttpStore(base_url=base_url)

        claims = base64.urlsafe_b64encode(json.dumps({"tenant_id": "tenant-test"}).encode()).decode().rstrip("=")
        auth_token = authorization.set(f"Bearer header.{claims}.sig")
        tenant_token = selected_tenant.set("tenant-test")
        try:
            sessions = store.list_trainer_sessions()
            assert len(sessions) == 1
            assert sessions[0]["session_id"] == "trn-1"
            assert sessions[0]["allowedActions"]["canSendMessage"] is True

            last_req = requests_log[-1]
            headers = last_req[2]
            assert "Authorization" in headers
            assert headers.get("X-Tenant-Id") == "tenant-test"
        finally:
            authorization.reset(auth_token)
            selected_tenant.reset(tenant_token)

    def test_http_store_all_methods(self, mock_server):
        base_url, requests_log = mock_server
        store = TrainingSessionHttpStore(base_url=base_url)

        claims = base64.urlsafe_b64encode(json.dumps({"tenant_id": "tenant-test"}).encode()).decode().rstrip("=")
        auth_token = authorization.set(f"Bearer header.{claims}.sig")
        tenant_token = selected_tenant.set("tenant-test")
        try:
            # 1. create
            sess = store.create_trainer_session(persona_id="p-1", objective="test obj")
            assert sess["session_id"] == "trn-new"

            # 1a. initial preview: newly created session with no computation yet has canRefreshPreview=True
            init_prev = store.get_trainer_preview("trn-new", session_status="active")
            assert init_prev["status"] == "preview_unavailable"
            assert init_prev["allowedActions"]["canRefreshPreview"] is True

            # 1b. first preview refresh: queues native job
            new_job = store.refresh_trainer_preview("trn-new")
            assert new_job["status"] == "pending"
            assert new_job["job_id"] == "job-new-1"
            assert new_job["eval_id"] == "teval-new-001"
            assert new_job["owner_job_ref"] == {"job_id": "job-new-1", "eval_id": "teval-new-001"}
            assert new_job["allowedActions"]["canRefreshPreview"] is False

            # 1c. pending reread & no second admission
            reread = store.get_trainer_preview("trn-new", session_status="active")
            assert reread["status"] == "pending"
            assert reread["allowedActions"]["canRefreshPreview"] is False

            post_count_before = len([r for r in requests_log if r[0] == "POST" and "trn-new/preview-jobs" in r[1]])
            repeat_job = store.refresh_trainer_preview("trn-new")
            assert repeat_job["status"] == "pending"
            assert repeat_job["job_id"] == "job-new-1"
            post_count_after = len([r for r in requests_log if r[0] == "POST" and "trn-new/preview-jobs" in r[1]])
            assert post_count_before == post_count_after

            # 1d. failed job readback and truth propagation
            failed_prev = store.get_trainer_preview("trn-failed", session_status="active")
            assert failed_prev["status"] == "failed"
            assert failed_prev["job_id"] == "job-failed-1"
            assert failed_prev["eval_id"] == "teval-failed-001"
            assert failed_prev["owner_job_ref"] == {"job_id": "job-failed-1", "eval_id": "teval-failed-001"}
            assert failed_prev["error_code"] == "evaluation_admission_rejected"
            assert failed_prev["failure_reason"] == "candidate controls do not contain an effective change"
            assert failed_prev["allowedActions"]["canRefreshPreview"] is True
            assert failed_prev["polling"]["enabled"] is False
            assert len(failed_prev["warnings"]) == 1
            assert failed_prev["warnings"][0]["level"] == "high"
            assert failed_prev["warnings"][0]["message"] == "candidate controls do not contain an effective change"
            assert failed_prev["warning_count_by_level"]["high"] == 1
            assert failed_prev["preview_quality"] == "insufficient_data"
            assert failed_prev["meta"]["surfaces"]["trainer_preview"] == "degraded"

            # 1e. failed job eval_id lookup vs unknown eval_id
            by_eval = store.get_trainer_preview("trn-failed", eval_id="teval-failed-001", session_status="active")
            assert by_eval["status"] == "failed"
            assert by_eval["job_id"] == "job-failed-1"
            assert store.get_trainer_preview("trn-failed", eval_id="teval-nonexistent", session_status="active") is None

            # 1f. retry refresh after failure triggers new job
            retry_job = store.refresh_trainer_preview("trn-failed")
            assert retry_job["status"] == "pending"
            assert retry_job["job_id"] == "job-failed-2"

            # 1g. malformed queue response raises 503 DEPENDENCY_UNAVAILABLE
            with pytest.raises(HTTPException) as exc_malformed:
                store.get_trainer_preview("trn-malformed", session_status="active")
            assert exc_malformed.value.status_code == 503

            # 1h. running without job_id raises 503 DEPENDENCY_UNAVAILABLE
            with pytest.raises(HTTPException) as exc_no_jobid:
                store.get_trainer_preview("trn-running-no-jobid", session_status="active")
            assert exc_no_jobid.value.status_code == 503

            # 1i. completed without evaluation returns None on specific eval_id, and preview_unavailable without eval_id (truthfully unavailable/error, not pending)
            assert store.get_trainer_preview("trn-completed-no-eval", eval_id="eval-source", session_status="active") is None
            completed_prev = store.get_trainer_preview("trn-completed-no-eval", session_status="active")
            assert completed_prev is not None
            assert completed_prev["status"] == "preview_unavailable"

            # 2. get session
            detail = store.get_trainer_session("trn-1")
            assert detail["session_id"] == "trn-1"

            # 3. get controls
            ctrls = store.get_trainer_controls("trn-1")
            assert ctrls["session_id"] == "trn-1"
            assert ctrls["meta"]["snapshot_at"] is not None

            # 3b. get controls with explicit snapshot_at
            ctrls_snapshot = store.get_trainer_controls("trn-1", snapshot_at="2026-10-15T10:19:36Z")
            assert ctrls_snapshot["session_id"] == "trn-1"
            assert ctrls_snapshot["meta"]["snapshot_at"] == "2026-10-15T10:19:36Z"

            # 4. patch controls
            patched = store.patch_trainer_controls("trn-1", patches=[{"parameter_key": "p1", "proposed_value": 2.0}])
            assert patched["status"] == "accepted"

            # 5. append message
            msg = store.append_trainer_message("trn-1", message_body="hi")
            assert msg["event"]["message_body"] == "hi"

            # 6. preview
            prev = store.get_trainer_preview("trn-1", session_status="active")
            assert prev["status"] == "complete"
            assert prev["allowedActions"]["canRefreshPreview"] is True

            # 7. refresh preview
            refreshed = store.refresh_trainer_preview("trn-1")
            assert refreshed["status"] == "pending"
            assert refreshed["job_id"] == "job-123"
            assert refreshed["eval_id"] == "teval-job-1"
            assert refreshed["owner_job_ref"] == {"job_id": "job-123", "eval_id": "teval-job-1"}

            with pytest.raises(HTTPException) as exc_info:
                store.refresh_trainer_preview("trn-malformed")
            assert exc_info.value.status_code == 503
            assert exc_info.value.detail["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

            # 8. preview unavailable
            unavail = store.build_trainer_preview_unavailable("trn-1")
            assert unavail["status"] == "preview_unavailable"
            assert unavail["allowedActions"]["canRefreshPreview"] is False

            # 9. list replays
            replays, surface = store.list_trainer_replays()
            assert len(replays) == 1
            assert replays[0]["session_id"] == "trn-1"

            # 10. commit & discard replay
            committed = store.commit_trainer_replay("trn-1", expected_candidate_snapshot_at="2026-10-12T00:00:00Z")
            assert committed["status"] == "completed"
            assert committed["replay_resolution"]["state"] == "committed"

            discarded = store.discard_trainer_replay("trn-1", expected_candidate_snapshot_at="2026-10-12T00:00:00Z")
            assert discarded["status"] == "completed"
            assert discarded["replay_resolution"]["state"] == "discarded"

            # 11. caller Idempotency-Key is forwarded to the owner only when supplied
            def _decision_headers(action):
                return [r[2] for r in requests_log if r[0] == "POST" and r[1].endswith(f"/replays/trn-1/{action}")]

            assert all("Idempotency-Key" not in h for h in _decision_headers("commit") + _decision_headers("discard"))
            store.commit_trainer_replay("trn-1", expected_candidate_snapshot_at="2026-10-12T00:00:00Z", idempotency_key="key-commit-1")
            store.discard_trainer_replay("trn-1", expected_candidate_snapshot_at="2026-10-12T00:00:00Z", idempotency_key="key-discard-1")
            assert _decision_headers("commit")[-1]["Idempotency-Key"] == "key-commit-1"
            assert _decision_headers("discard")[-1]["Idempotency-Key"] == "key-discard-1"
        finally:
            authorization.reset(auth_token)
            selected_tenant.reset(tenant_token)

    def test_http_store_outage_raises_503(self):
        store = TrainingSessionHttpStore(base_url="http://127.0.0.1:1")
        with pytest.raises(HTTPException) as exc_info:
            store.list_trainer_sessions()
        assert exc_info.value.status_code == 503
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail.get("error", {}).get("code") == "DEPENDENCY_UNAVAILABLE"



class TestReplayDetailProjection:
    def test_replay_detail_keeps_event_type_and_eval_ref_without_changing_teaching_events(self):
        from ports.persona_training import _extract_sorted_events, _project_trainer_replay_detail

        session = {
            "session_id": "trn-1",
            "status": "completed",
            "artifacts": {"candidate_artifact_ref": "art-1"},
            "events": [
                {"sequence_number": 2, "event_type": "preview_trigger", "eval_ref": {"candidate_snapshot_at": "t1"}},
                {"sequence_number": 1, "event_type": "message", "message_body": "hi"},
            ],
        }
        detail = _project_trainer_replay_detail(session, snapshot_at="2026-10-13T00:00:00Z")
        assert [e["event_type"] for e in detail["events"]] == ["message", "preview_trigger"]
        assert "eval_ref" not in detail["events"][0]
        assert detail["events"][1]["eval_ref"] == {"candidate_snapshot_at": "t1"}
        assert detail["allowedActions"]["canCommit"] is True
        assert all("event_type" not in e and "eval_ref" not in e for e in _extract_sorted_events("trn-1", session))
