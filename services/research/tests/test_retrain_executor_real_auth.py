"""The approved-retrain executor against the real Registry and Training applications.

Nothing here patches ``urllib`` or ``_trigger_retrain_execution``.  The real
Registry and Training FastAPI apps are served over HTTP with strict JWT auth,
the executor's two service tokens come from the dev principal issuer, the
preview job runs through the real ``/run`` route with real vectorbt, and the
dataset is a deterministic local file.  The only stand-in is the Persona owner's
read API (a different service), which serves the persona's bound model.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import logging
import math
import os
import random
import socket
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import uvicorn

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("vectorbt", "pandas", "numpy")),
    reason="real vectorbt is not installed",
)

ROOT = Path(__file__).resolve().parents[3]
TRAINING_DIR = ROOT / "services" / "training-session"
TENANT = "tenant-dev"
SECRET = "retrain-executor-real-auth-secret-0123456789"
ISSUER = "retrain-test-issuer"
AUDIENCE = "retrain-test-audience"
PERSONA_BETTER = "persona-retrain-better"
PERSONA_WORSE = "persona-retrain-worse"
PERSONA_UNCOVERED = "persona-retrain-uncovered"
INSTRUMENTS = ("2330.TWSE", "2317.TWSE", "2454.TWSE")
BARS = 60


def _closes(seed: int = 22, bars: int = BARS) -> list[float]:
    """Autocorrelated returns: lookback 3 beats lookback 2 on this series."""
    rng = random.Random(seed)
    price, previous, out = 100.0, 0.0, []
    for _ in range(bars):
        ret = 0.004 + 0.5 * previous + rng.gauss(0, 0.01)
        previous = ret - 0.004
        price *= math.exp(ret)
        out.append(round(price, 2))
    return out


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _Server:
    def __init__(self, app: Any) -> None:
        self.port = _free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="info", log_config=None))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "_Server":
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class _AccessLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def paths(self) -> list[str]:
        out = []
        for line in self.lines:
            parts = line.split('"')
            if len(parts) > 1:
                method, path = parts[1].split(" ")[:2]
                out.append(f"{method} {path.split('?')[0]}")
        return out


def _artifact(*, artifact_id: str, lookback: int, symbols: tuple[str, ...] = ("2330.TW", "2317.TW", "2454.TW")) -> dict[str, Any]:
    registration = json.loads(
        (ROOT / "services/registry/strategy-artifacts/tw-session-momentum-v1.registration.json").read_text()
    )
    artifact = copy.deepcopy(registration["strategy_artifact"])
    artifact["artifact_id"] = artifact_id
    artifact["strategy_id"] = f"strategy-{artifact_id}"
    artifact["parameters"]["lookback_bars"] = lookback
    artifact["parameters"]["symbols"] = list(symbols)
    for control in artifact["mutation_surface"]["controls"]:
        if control["parameter_key"] == "lookback_bars":
            control["current_value"] = lookback
    artifact.pop("binding_intent", None)
    return artifact


def _binding(artifact: dict[str, Any], persona_id: str) -> dict[str, Any]:
    from services.registry.strategy_artifact import strategy_artifact_checksum

    checksum = strategy_artifact_checksum(artifact)
    metadata = {
        "version": artifact["version"], "checksum": checksum, "registry_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"], "artifact_type": "execution_bundle", "artifact_state": "approved",
        "promotion_state": "paper", "deployment_stage": "paper",
        "created_at": "2026-10-15T19:19:47Z", "approved_at": "2026-10-15T19:19:48Z",
        "lineage": copy.deepcopy(artifact["lineage"]),
        "rollback": {"target_version": "0.0.1", "target_registry_id": "artifact-baseline"},
    }
    base = f"openclaw/registry/{artifact['strategy_id']}/{artifact['version']}"
    payload = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    return {
        "status": "active", "artifact_id": artifact["artifact_id"], "artifact_version": artifact["version"],
        "execution_mode": "paper",
        "metadata": {
            "tenant_id": TENANT, "persona_id": persona_id, "strategy_id": artifact["strategy_id"],
            "artifact_checksum": checksum,
            "object_store": {f"{base}/artifact.bin": payload, f"{base}/metadata.json": metadata},
        },
    }


class _PersonaOwner:
    """Stand-in for the Persona owner's read API only."""

    def __init__(self) -> None:
        self.personas: dict[str, dict[str, Any]] = {}
        # The Training clock is pinned to the fixture time, so the owner's record
        # is stamped relative to that same instant.
        self.recorded_now = datetime.now(timezone.utc)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parts = self.path.split("?")[0].strip("/").split("/")
                persona = owner.personas.get(parts[2]) if len(parts) >= 3 else None
                if persona is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                if parts[-1] == "training-target":
                    recorded = (owner.recorded_now - timedelta(minutes=1)).replace(microsecond=0)
                    body = {"persona": {
                        "persona_id": parts[2], "tenant_id": TENANT, "status": "active", "generation": 1,
                        "authority_status": "authoritative", "controller_record_ref": f"ctrl:{parts[2]}",
                        "recorded_at": recorded.isoformat().replace("+00:00", "Z"),
                    }}
                else:
                    body = {"persona": persona}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def bind(self, persona_id: str, artifact: dict[str, Any]) -> None:
        self.personas[persona_id] = {
            "persona_id": persona_id, "tenant_id": TENANT,
            "metadata": {"tenant_id": TENANT, "provisioning_authoritative_readback": {
                "runtime_binding": _binding(artifact, persona_id)}},
        }

    def stop(self) -> None:
        self.httpd.shutdown()


class World:
    pass


def _http(method: str, url: str, token: str, body: dict | None = None, tenant: str = TENANT, service: str | None = None):
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant, "Content-Type": "application/json"}
    if service:
        headers["X-Pantheon-Service"] = service
    request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from scripts.issue_dev_paper_principals import issue_environment

    mp = pytest.MonkeyPatch()
    w = World()
    root = tmp_path_factory.mktemp("retrain-real-auth")
    issued = issue_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true", "PANTHEON_DEV_BFF_JWT_SECRET": SECRET,
        "PANTHEON_DEV_BFF_JWT_ISSUER": ISSUER, "PANTHEON_DEV_BFF_JWT_AUDIENCE": AUDIENCE,
    })
    token_dir = root / "principals"
    token_dir.mkdir(mode=0o700)
    for variable in ("RESEARCH_REGISTRY_SERVICE_TOKEN", "RESEARCH_TRAINING_SERVICE_TOKEN"):
        path = token_dir / variable
        path.write_text(issued[variable])
        path.chmod(0o600)
        mp.setenv(variable + "_FILE", str(path))
        mp.delenv(variable, raising=False)
    w.tokens = {name: issued[name] for name in ("RESEARCH_REGISTRY_SERVICE_TOKEN", "RESEARCH_TRAINING_SERVICE_TOKEN")}
    w.token_dir = token_dir

    # Deterministic local dataset in the Training authority fixture.
    sys.path.insert(0, str(TRAINING_DIR / "tests"))
    import strict_test_support as support

    series = _closes()

    def canonical_records(last_date: date, bars: int) -> list[dict[str, Any]]:
        first = last_date - timedelta(days=bars - 1)
        records = []
        for offset in range(bars):
            for index, instrument in enumerate(INSTRUMENTS):
                close = round(series[offset] + 10.0 * index, 2)
                records.append({
                    "instrument": instrument, "date": (first + timedelta(days=offset)).isoformat(),
                    "open": close, "high": close, "low": close, "close": close, "volume": 1000.0,
                })
        return records

    mp.setattr(support, "_INSTRUMENTS", INSTRUMENTS)
    mp.setattr(support, "_canonical_records", canonical_records)
    # Two days behind the wall clock: the strict authority allows one day of
    # staleness, so only a pinned Training clock can accept this dataset.  That
    # is what a suite straddling UTC midnight looks like.
    trusted_now = datetime.now(timezone.utc) - timedelta(days=2)
    w.trusted_now = trusted_now
    fixture = support.materialize_strict_authority(root / "authority", trusted_now=trusted_now, bars_per_instrument=BARS)

    persona = _PersonaOwner()
    persona.recorded_now = trusted_now
    w.persona = persona
    better = _artifact(artifact_id="artifact-retrain-better-v1", lookback=2)
    worse = _artifact(artifact_id="artifact-retrain-worse-v1", lookback=3)
    uncovered = _artifact(artifact_id="artifact-retrain-uncovered-v1", lookback=2, symbols=("2330.TW", "9999.TW"))
    w.artifacts = {PERSONA_BETTER: better, PERSONA_WORSE: worse, PERSONA_UNCOVERED: uncovered}
    for persona_id, artifact in w.artifacts.items():
        persona.bind(persona_id, artifact)

    env = {
        k: v for k, v in fixture.environment().items()
        if k not in {"TRAINING_SESSION_AUTH_DISABLED", "TRAINING_SESSION_TEST_TENANT_ID"}
    }
    env.update({
        "TRAINING_SESSION_AUTH_MODE": "strict", "TRAINING_SESSION_JWT_SECRET": SECRET,
        "TRAINING_SESSION_JWT_ISSUER": ISSUER, "TRAINING_SESSION_JWT_AUDIENCE": AUDIENCE,
        "TRAINING_SESSION_MFA_REQUIRED": "false",
        "TRAINING_SESSION_ALLOWED_CALLER_SERVICES": "control-plane-bff,training-session-preview-worker,research-orchestrator",
        "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN": "persona-owner-read-token",
        "TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE": f"http://127.0.0.1:{persona.port}/api/personas/{{persona_id}}/training-target",
        "PANTHEON_REGISTRY_AUTH_MODE": "strict", "PANTHEON_REGISTRY_JWT_SECRET": SECRET,
        "PANTHEON_REGISTRY_JWT_ISSUER": ISSUER, "PANTHEON_REGISTRY_JWT_AUDIENCE": AUDIENCE,
        "REGISTRY_STORE_BACKEND": "memory",
        "RESEARCH_RETRAIN_EVAL_POLL_SECONDS": "0.2",
    })
    for key, value in env.items():
        mp.setenv(key, value)

    sys.path.insert(0, str(TRAINING_DIR))
    spec = importlib.util.spec_from_file_location("training_session_retrain_real_auth", TRAINING_DIR / "main.py")
    training = importlib.util.module_from_spec(spec)
    sys.modules["training_session_retrain_real_auth"] = training
    spec.loader.exec_module(training)
    mp.setattr(training, "_trusted_now", lambda: trusted_now)  # same pin as strict_test_support
    training.store = training.TrainingSessionStore(fixture.data_dir)
    w.training = training
    sys.path.remove(str(TRAINING_DIR))
    sys.modules.pop("store", None)

    from services.registry import service as registry_service
    from services.registry.storage import reset_store

    reset_store()
    w.log = _AccessLog()
    logging.getLogger("uvicorn.access").addHandler(w.log)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    w.registry_server = _Server(registry_service.app).start()
    w.training_server = _Server(training.app).start()
    mp.setenv("REGISTRY_URL", w.registry_server.url)
    mp.setenv("TRAINING_SESSION_URL", w.training_server.url)

    # Research orchestrator (real module, own data dir, executor runs in its thread).
    service_dir = ROOT / "services" / "research"
    mp.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", tempfile.mkdtemp())
    sys.path.insert(0, str(service_dir))
    try:
        spec = importlib.util.spec_from_file_location("research_orchestrator_retrain_real_auth", service_dir / "main.py")
        research = importlib.util.module_from_spec(spec)
        sys.modules["research_orchestrator_retrain_real_auth"] = research
        spec.loader.exec_module(research)
    finally:
        sys.modules.pop("store", None)
        sys.path.remove(str(service_dir))
    w.research = research

    for persona_id, artifact in w.artifacts.items():
        status, body = _http(
            "POST", w.registry_server.url + "/api/registry/strategy-artifacts", w.tokens["RESEARCH_REGISTRY_SERVICE_TOKEN"],
            {"registry_id": artifact["artifact_id"], "artifact_state": "candidate", "strategy_artifact": artifact,
             "metadata": {"persona_id": persona_id}},
        )
        assert status in (200, 201), body
    # A target whose entry names no persona, and one whose persona does not exist.
    nopersona = _artifact(artifact_id="artifact-retrain-nopersona-v1", lookback=2)
    unknown = _artifact(artifact_id="artifact-retrain-unknown-persona-v1", lookback=2)
    for artifact, metadata in ((nopersona, {}), (unknown, {"persona_id": "persona-does-not-exist"})):
        status, body = _http(
            "POST", w.registry_server.url + "/api/registry/strategy-artifacts", w.tokens["RESEARCH_REGISTRY_SERVICE_TOKEN"],
            {"registry_id": artifact["artifact_id"], "artifact_state": "candidate", "strategy_artifact": artifact, "metadata": metadata},
        )
        assert status in (200, 201), body
    yield w
    w.registry_server.stop()
    w.training_server.stop()
    persona.stop()
    logging.getLogger("uvicorn.access").removeHandler(w.log)
    if str(TRAINING_DIR / "tests") in sys.path:
        sys.path.remove(str(TRAINING_DIR / "tests"))
    mp.undo()


def _run(world: World, target: str, decision_id: str, *, tenant: str = TENANT, wait: float = 120.0) -> dict[str, Any]:
    """Start the real executor through the run-creation route and wait for a terminal run."""
    from fastapi.testclient import TestClient

    client = TestClient(world.research.app)
    task = client.post("/api/research-orchestrator/tasks", json={
        "title": f"retrain {decision_id}", "objective": "approved retrain",
        "source_refs": [{"type": "evolution_decision", "id": decision_id}],
        "constraints": {"tenant_id": tenant}, "actor_id": "evolution-dispatch-worker",
        "idempotency_key": f"task-{decision_id}",
    })
    assert task.status_code in (200, 201), task.text
    task_id = task.json()["task_id"]
    run = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "parameters": {
            "tenant_id": tenant, "decision_id": decision_id, "action_type": "retrain",
            "target_artifact_id": target, "target_version": "1.0.0", "work_item_id": task_id,
        },
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"run-{decision_id}",
    })
    assert run.status_code in (200, 201), run.text
    return _wait(client, run.json()["run_id"], wait)


def _wait(client: Any, run_id: str, wait: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        run = client.get(f"/api/research-orchestrator/runs/{run_id}").json()
        if run["status"] in {"completed", "failed"}:
            return run
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish: {run}")


def _registry_entry(world: World, artifact_id: str) -> tuple[int, dict]:
    return _http("GET", f"{world.registry_server.url}/api/registry/strategy-artifacts/{artifact_id}",
                 world.tokens["RESEARCH_REGISTRY_SERVICE_TOKEN"])


def _sessions(world: World, persona_id: str) -> list[dict]:
    return [s for s in world.training.store.list_sessions() if s.get("persona_id") == persona_id]


def test_better_candidate_creates_a_candidate_child_and_leaves_the_session_active(world):
    run = _run(world, "artifact-retrain-better-v1", "dec-better-001")
    assert run["status"] == "completed", run
    assert run["outcome"] == "candidate_created", (run.get("outcome_reason"), run.get("rejection"))
    assert run["baseline_metrics"]["sharpe_ratio"] < run["candidate_metrics"]["sharpe_ratio"]
    child_id = run["candidate_artifact_id"]
    assert child_id and child_id != "artifact-retrain-better-v1"
    assert run["parameter_updates"] == {"lookback_bars": 3}
    for field in ("session_id", "baseline_job_id", "candidate_job_id", "baseline_eval_id", "candidate_eval_id"):
        assert run[field], field

    status, child = _registry_entry(world, child_id)
    assert status == 200, child
    assert child["entry"]["artifact_state"] == "candidate"
    assert child["entry"]["metadata"]["strategy_artifact"]["parameters"]["lookback_bars"] == 3
    source_run_ids = child["entry"]["lineage"]["source_run_ids"]
    assert "dec-better-001" in source_run_ids and run["session_id"] in source_run_ids
    assert run["candidate_proof_ref"] in source_run_ids

    session = world.training.store.get_session(run["session_id"])
    assert session["status"] == "active" and session.get("ended_at") is None
    assert "dec-better-001" in session["objective"]
    assert {"type": "evolution_decision", "id": "dec-better-001"} in session["context_refs"]
    assert session["persona_id"] == PERSONA_BETTER and session["tenant_id"] == TENANT

    baseline = world.training.store.get_preview_job(run["baseline_job_id"])
    candidate = world.training.store.get_preview_job(run["candidate_job_id"])
    assert baseline["mode"] == "baseline" and baseline["status"] == "completed"
    assert candidate["status"] == "completed"
    assert candidate["preview"]["evaluation_proof"]["backend"]["run_id"].startswith("vbt-real-")
    assert candidate["governance_gate_state"] == "passed"


def test_evaluation_started_after_the_fixture_date_has_passed_reaches_the_same_outcome(world):
    assert world.training._trusted_now() == world.trusted_now
    assert datetime.now(timezone.utc).date() > world.trusted_now.date()
    run = _run(world, "artifact-retrain-better-v1", "dec-clock-pin-001")
    assert run["status"] == "completed", run
    assert run["outcome"] == "candidate_created", (run.get("outcome_reason"), run.get("rejection"))


def test_executor_only_uses_the_evaluation_routes_and_never_the_commit_path(world):
    run = _run(world, "artifact-retrain-better-v1", "dec-boundary-001")
    assert run["status"] == "completed", run
    paths = world.log.paths()
    session_id = run["session_id"]
    mine = [p for p in paths if session_id in p or run["baseline_job_id"] in p or run["candidate_job_id"] in p]
    assert mine, paths
    forbidden = ("/complete", "/commit", "/discard", "/advance", "runtime", "deployment", "capital", "binding")
    assert not [p for p in mine if any(word in p for word in forbidden)], mine
    registry_writes = [p for p in paths if p.startswith("POST /api/registry/")]
    assert all(p.endswith("/mutate") or p.endswith("/strategy-artifacts") for p in registry_writes), registry_writes
    # Promotion stays with the approver actions: the child is still a candidate.
    status, child = _registry_entry(world, run["candidate_artifact_id"])
    assert child["entry"]["artifact_state"] == "candidate"
    assert child.get("deployment_stage", "none") in ("none", None)


def test_worse_candidate_creates_no_child(world):
    run = _run(world, "artifact-retrain-worse-v1", "dec-worse-001")
    assert run["status"] == "completed", run
    assert run["outcome"] == "no_candidate" and run["outcome_reason"] == "not_better_than_baseline", run
    assert run["candidate_metrics"]["sharpe_ratio"] <= run["baseline_metrics"]["sharpe_ratio"]
    assert not run.get("candidate_artifact_id")
    assert run["parameter_updates"] == {"lookback_bars": 2}
    assert world.training.store.get_session(run["session_id"])["status"] == "active"
    status, _ = _registry_entry(world, "artifact-retrain-worse-v1-retrain-" + __import__("hashlib").sha256(b"dec-worse-001").hexdigest()[:10])
    assert status == 404


def test_missing_token_fails_the_run_naming_the_step_and_opens_no_session(world, monkeypatch):
    before = len(world.training.store.list_sessions())
    monkeypatch.setenv("RESEARCH_REGISTRY_SERVICE_TOKEN_FILE", "")
    monkeypatch.delenv("RESEARCH_REGISTRY_SERVICE_TOKEN", raising=False)
    run = _run(world, "artifact-retrain-better-v1", "dec-notoken-001")
    assert run["status"] == "failed", run
    assert run["rejection"]["reason"] == "retrain_failed" and run["rejection"]["step"] == "read_target_artifact"
    assert run["failed_step"] == "read_target_artifact"
    assert len(world.training.store.list_sessions()) == before


def test_wrong_tenant_fails_without_a_session(world):
    before = len(world.training.store.list_sessions())
    run = _run(world, "artifact-retrain-better-v1", "dec-tenant-001", tenant="tenant-other")
    assert run["status"] == "failed", run
    assert run["failed_step"] == "read_target_artifact"
    assert len(world.training.store.list_sessions()) == before


def test_target_without_persona_ends_with_a_reason_and_no_session_or_mutation(world):
    before = len(world.training.store.list_sessions())
    run = _run(world, "artifact-retrain-nopersona-v1", "dec-nopersona-001")
    assert run["status"] == "completed" and run["outcome"] == "evaluation_rejected", run
    assert run["outcome_reason"] == "target_has_no_persona"
    assert not run.get("session_id") and not run.get("candidate_artifact_id")
    assert len(world.training.store.list_sessions()) == before


def test_unknown_persona_is_an_owner_rejection(world):
    run = _run(world, "artifact-retrain-unknown-persona-v1", "dec-unknown-001")
    assert run["status"] == "completed" and run["outcome"] == "evaluation_rejected", run
    assert "persona not found" in run["outcome_reason"]
    assert not run.get("candidate_artifact_id")


def test_dataset_not_covering_the_symbol_is_an_owner_rejection_visible_in_retrain_health(world):
    run = _run(world, "artifact-retrain-uncovered-v1", "dec-uncovered-001")
    assert run["status"] == "completed" and run["outcome"] == "evaluation_rejected", run
    assert "does not cover the strategy artifact symbols" in run["outcome_reason"]
    assert not run.get("candidate_artifact_id")
    health = world.training._functional_health()
    assert health["status"] == "ok" and health["failure_count"] == 0
    assert health["retrain_evaluation_failure_count"] >= 1
    with urlopen(world.training_server.url + "/readyz", timeout=10) as response:
        assert response.status == 200


def test_retry_after_a_restart_during_evaluation_resumes_without_a_second_session_or_child(world, monkeypatch):
    from fastapi.testclient import TestClient

    # First attempt: the bounded wait runs out while the baseline job is still queued.
    monkeypatch.setenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS", "0.001")
    first = _run(world, "artifact-retrain-better-v1", "dec-resume-001")
    assert first["status"] == "failed" and first["failed_step"] == "wait_baseline_evaluation", first
    assert first["session_id"] and first["baseline_job_id"]
    assert not first.get("candidate_artifact_id")
    monkeypatch.delenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS")

    client = TestClient(world.research.app)
    retry = client.post(f"/api/research-orchestrator/runs/{first['run_id']}/retry", json={"actor_id": "operator-1"})
    assert retry.status_code == 201, retry.text
    second = _wait(client, retry.json()["run_id"])
    assert second["status"] == "completed" and second["outcome"] == "candidate_created", second
    assert second["session_id"] == first["session_id"]
    assert second["baseline_job_id"] == first["baseline_job_id"]
    decision_sessions = [
        s for s in _sessions(world, PERSONA_BETTER)
        if {"type": "evolution_decision", "id": "dec-resume-001"} in s["context_refs"]
    ]
    assert len(decision_sessions) == 1
    children = [
        e for e in world.research.get_store().list_runs()
        if (e.get("parameters") or {}).get("decision_id") == "dec-resume-001" and e.get("candidate_artifact_id")
    ]
    assert [c["candidate_artifact_id"] for c in children] == [second["candidate_artifact_id"]]


# --- startup resume ---------------------------------------------------------
#
# A restart leaves the run exactly as the dying process last persisted it:
# status ``running`` with the fields recorded so far.  Each test brings the real
# run to that state, then runs the real startup handler ``resume_retrain_runs``.

def _settle(world: World, run_id: str) -> None:
    """Wait until this process's executor thread for the run has fully exited."""
    deadline = time.monotonic() + 30
    while run_id in world.research._retrain_active_runs:
        assert time.monotonic() < deadline, "executor thread never exited"
        time.sleep(0.05)


def _as_left_by_a_restart(world: World, run: dict[str, Any], *, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    _settle(world, run["run_id"])
    left = {k: v for k, v in run.items() if k not in {"failed_step", "rejection", *drop}}
    left["status"] = "running"
    world.research.get_store().put_run(left)
    return left


def _decision_sessions(world: World, persona_id: str, decision_id: str) -> list[dict]:
    return [s for s in _sessions(world, persona_id) if {"type": "evolution_decision", "id": decision_id} in s["context_refs"]]


def _decision_runs(world: World, decision_id: str) -> list[dict]:
    return [r for r in world.research.get_store().list_runs() if (r.get("parameters") or {}).get("decision_id") == decision_id]


def _no_second_session_run_or_child(world: World, decision_id: str, persona_id: str, first: dict, resumed: dict) -> None:
    assert resumed["run_id"] == first["run_id"]
    assert [r["run_id"] for r in _decision_runs(world, decision_id)] == [first["run_id"]]
    assert len(_decision_sessions(world, persona_id, decision_id)) == 1
    assert resumed["session_id"] == first["session_id"]
    children = [r["candidate_artifact_id"] for r in _decision_runs(world, decision_id) if r.get("candidate_artifact_id")]
    assert len(children) == len(set(children)) <= 1


def test_startup_resumes_a_run_that_was_in_the_baseline_evaluation(world, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS", "0.001")
    first = _run(world, "artifact-retrain-better-v1", "dec-restart-baseline")
    assert first["failed_step"] == "wait_baseline_evaluation" and first["baseline_job_id"] and not first.get("baseline_metrics")
    monkeypatch.delenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS")
    _as_left_by_a_restart(world, first)

    world.research.resume_retrain_runs()
    resumed = _wait(TestClient(world.research.app), first["run_id"])
    assert resumed["status"] == "completed" and resumed["outcome"] == "candidate_created", resumed
    assert resumed["baseline_job_id"] == first["baseline_job_id"]
    _no_second_session_run_or_child(world, "dec-restart-baseline", PERSONA_BETTER, first, resumed)


def test_startup_resumes_a_run_that_was_in_the_candidate_evaluation(world, monkeypatch):
    from fastapi.testclient import TestClient

    original = world.research._retrain_evaluate

    def candidate_wait_runs_out(**kwargs):
        if kwargs["kind"] != "candidate":
            return original(**kwargs)
        monkeypatch.setenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS", "0.001")
        try:
            return original(**kwargs)
        finally:
            monkeypatch.delenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS")

    monkeypatch.setattr(world.research, "_retrain_evaluate", candidate_wait_runs_out)
    first = _run(world, "artifact-retrain-better-v1", "dec-restart-candidate")
    assert first["failed_step"] == "wait_candidate_evaluation", first
    assert first["baseline_metrics"] and first["candidate_job_id"] and not first.get("candidate_metrics")
    monkeypatch.setattr(world.research, "_retrain_evaluate", original)
    _as_left_by_a_restart(world, first)

    world.research.resume_retrain_runs()
    resumed = _wait(TestClient(world.research.app), first["run_id"])
    assert resumed["status"] == "completed" and resumed["outcome"] == "candidate_created", resumed
    assert resumed["candidate_job_id"] == first["candidate_job_id"]
    assert resumed["baseline_metrics"] == first["baseline_metrics"]
    _no_second_session_run_or_child(world, "dec-restart-candidate", PERSONA_BETTER, first, resumed)


def test_startup_resumes_a_run_whose_child_was_registered_but_not_marked_terminal(world):
    from fastapi.testclient import TestClient

    done = _run(world, "artifact-retrain-better-v1", "dec-restart-child")
    assert done["outcome"] == "candidate_created" and done["candidate_artifact_id"]
    child_status, _ = _registry_entry(world, done["candidate_artifact_id"])
    assert child_status == 200
    mutates_before = [p for p in world.log.paths() if p.endswith("/mutate")]
    _as_left_by_a_restart(world, done, drop=("outcome", "outcome_reason"))

    world.research.resume_retrain_runs()
    resumed = _wait(TestClient(world.research.app), done["run_id"])
    assert resumed["status"] == "completed" and resumed["outcome"] == "candidate_created", resumed
    assert resumed["candidate_artifact_id"] == done["candidate_artifact_id"]
    assert [p for p in world.log.paths() if p.endswith("/mutate")] == mutates_before
    _no_second_session_run_or_child(world, "dec-restart-child", PERSONA_BETTER, done, resumed)


def test_startup_fails_a_run_that_cannot_resume_naming_the_step(world, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS", "0.001")
    first = _run(world, "artifact-retrain-better-v1", "dec-restart-unresumable")
    monkeypatch.delenv("RESEARCH_RETRAIN_EVAL_WAIT_SECONDS")
    _as_left_by_a_restart(world, first)
    monkeypatch.setenv("RESEARCH_TRAINING_SERVICE_TOKEN_FILE", "")
    monkeypatch.delenv("RESEARCH_TRAINING_SERVICE_TOKEN", raising=False)

    world.research.resume_retrain_runs()
    resumed = _wait(TestClient(world.research.app), first["run_id"])
    assert resumed["status"] == "failed" and resumed["failed_step"] == "resume_session", resumed
    assert resumed["rejection"]["reason"] == "retrain_failed" and resumed["rejection"]["step"] == "resume_session"
    assert [r["run_id"] for r in _decision_runs(world, "dec-restart-unresumable")] == [first["run_id"]]


def test_startup_leaves_non_retrain_and_finished_runs_alone(world):
    store = world.research.get_store()
    stub = {"run_id": "run-not-retrain-restart", "status": "queued", "tenant_id": TENANT,
            "parameters": {"decision_id": "dec-other", "target_artifact_id": "artifact-x", "action_type": "rebalance"}}
    finished = {"run_id": "run-finished-retrain-restart", "status": "completed", "tenant_id": TENANT, "outcome": "no_candidate",
                "parameters": {"tenant_id": TENANT, "decision_id": "dec-fin", "target_artifact_id": "artifact-x", "action_type": "retrain"}}
    store.put_run(dict(stub))
    store.put_run(dict(finished))
    sessions_before = len(world.training.store.list_sessions())

    world.research.resume_retrain_runs()
    time.sleep(0.5)
    # The store adds its own ``id`` key on write; nothing else may change.
    assert {k: v for k, v in store.get_run("run-not-retrain-restart").items() if k != "id"} == stub
    assert {k: v for k, v in store.get_run("run-finished-retrain-restart").items() if k != "id"} == finished
    assert len(world.training.store.list_sessions()) == sessions_before


# --- Run identity and terminal transition (RESEARCH-RETRAIN-RUN-TRUTH-20261107) ---

NON_REAL = {"stub", "manual", "unknown", "handoff_only", ""}


def _task(world: World, run: dict[str, Any]) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    return TestClient(world.research.app).get(f"/api/research-orchestrator/tasks/{run['task_id']}").json()


def _assert_labelled_as_training_evaluation(run: dict[str, Any]) -> None:
    for field in ("adapter", "requested_mode", "dispatch_mode"):
        assert str(run[field]).lower() not in NON_REAL, (field, run[field])
    queued = [e for e in run["events"] if e["event_type"] == "run_queued"]
    assert len(queued) == 1 and "tub" not in queued[0]["summary"], queued


def _completed_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in run["events"] if e["event_type"] == "run_completed"]


def test_candidate_created_run_is_labelled_and_ends_its_task(world):
    run = _run(world, "artifact-retrain-better-v1", "dec-truth-candidate")
    assert run["status"] == "completed" and run["outcome"] == "candidate_created", run
    _assert_labelled_as_training_evaluation(run)
    done = _completed_events(run)
    assert len(done) == 1 and "candidate_created" in done[0]["summary"] and run["outcome_reason"] in done[0]["summary"]
    assert _task(world, run)["status"] == "completed"


def test_no_candidate_run_is_labelled_and_ends_its_task(world):
    run = _run(world, "artifact-retrain-worse-v1", "dec-truth-nocandidate")
    assert run["status"] == "completed" and run["outcome"] == "no_candidate", run
    assert run["outcome_reason"] == "not_better_than_baseline"
    _assert_labelled_as_training_evaluation(run)
    done = _completed_events(run)
    assert len(done) == 1 and "no_candidate" in done[0]["summary"] and "not_better_than_baseline" in done[0]["summary"]
    assert _task(world, run)["status"] == "completed"


def test_failed_run_is_labelled_and_fails_its_task(world):
    run = _run(world, "artifact-retrain-better-v1", "dec-truth-failed", tenant="tenant-other")
    assert run["status"] == "failed" and run["failed_step"] == "read_target_artifact", run
    _assert_labelled_as_training_evaluation(run)
    done = _completed_events(run)
    assert len(done) == 1 and "read_target_artifact" in done[0]["summary"]
    assert _task(world, run)["status"] == "failed"


def test_non_retrain_run_keeps_its_stub_identity(world):
    from fastapi.testclient import TestClient

    client = TestClient(world.research.app)
    task = client.post("/api/research-orchestrator/tasks", json={"title": "t", "objective": "o", "idempotency_key": "task-truth-other"}).json()
    run = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={
        "parameters": {"decision_id": "dec-x", "target_artifact_id": "artifact-x", "action_type": "rebalance"},
        "idempotency_key": "run-truth-other",
    }).json()
    assert (run["adapter"], run["requested_mode"], run["dispatch_mode"]) == ("stub", "stub", "stub")
    assert run["status"] == "queued" and "Stub" in run["events"][0]["summary"]


def test_startup_repairs_a_task_left_running_by_a_finished_retrain_run(world):
    from fastapi.testclient import TestClient

    done = _run(world, "artifact-retrain-worse-v1", "dec-truth-repair")
    _settle(world, done["run_id"])
    store = world.research.get_store()
    # The shape earlier hosted retrain runs left: stub identity, only the queued event,
    # terminal run, task still running.
    left = {k: v for k, v in done.items() if k != "completed_at"}
    left.update(adapter="stub", requested_mode="stub", dispatch_mode="stub", events=done["events"][:1])
    store.put_run(left)
    task = store.get_task(done["task_id"])
    task["status"] = "running"
    store.put_task(task)
    runs_before = len(store.list_runs())
    sessions_before = len(world.training.store.list_sessions())
    run_before = dict(store.get_run(done["run_id"]))

    world.research.repair_retrain_tasks_left_running()
    world.research.repair_retrain_tasks_left_running()  # once: a second pass changes nothing more

    assert store.get_task(done["task_id"])["status"] == "completed"
    assert store.get_run(done["run_id"]) == run_before
    assert len(store.list_runs()) == runs_before
    assert len(world.training.store.list_sessions()) == sessions_before
    assert TestClient(world.research.app).get(f"/api/research-orchestrator/runs/{done['run_id']}").json() == run_before


def test_startup_repair_leaves_a_task_with_an_active_run_running(world):
    store = world.research.get_store()
    store.put_task({"task_id": "rtask-active", "id": "rtask-active", "status": "running", "title": "t", "objective": "o"})
    params = {"tenant_id": TENANT, "decision_id": "dec-active", "target_artifact_id": "artifact-x", "action_type": "retrain"}
    store.put_run({"run_id": "rrun-active-old", "task_id": "rtask-active", "status": "completed", "parameters": params})
    store.put_run({"run_id": "rrun-active-now", "task_id": "rtask-active", "status": "running", "cancellation_fence": "fenced", "parameters": params})
    world.research.repair_retrain_tasks_left_running()
    assert store.get_task("rtask-active")["status"] == "running"
