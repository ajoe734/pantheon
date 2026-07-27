from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from services.evolution import hosted_compose_probe as compose_probe
from services.evolution import hosted_dispatch_probe as probe


class _FakeEvolutionApi:
    def __init__(self) -> None:
        self.decisions: dict[str, dict] = {}

    def request(self, **kwargs):
        method = kwargs["method"]
        path = kwargs["path"]
        payload = kwargs["payload"]
        ledger = kwargs["request_ledger"]
        ledger.append({"method": method, "path": path, "status": 200})

        if method == "POST" and path == "/api/evolution/proposals":
            decision = {
                **deepcopy(payload),
                "decision_state": "proposed",
                "execution_result": None,
                "cooldown_ends_at": None,
                "observation_window_ends_at": None,
                "review_chain": [],
            }
            self.decisions[decision["decision_id"]] = decision
            return deepcopy(decision)

        decision_id = path.split("/")[4]
        decision = self.decisions[decision_id]
        if method == "POST" and path.endswith("/review"):
            decision["decision_state"] = "reviewed"
            decision["review_chain"].append(
                {"step_type": "reviewed", "actor_id": payload["actor_id"]}
            )
        elif method == "POST" and path.endswith("/approve"):
            decision["decision_state"] = "approved"
            decision["review_chain"].append(
                {"step_type": "approved", "actor_id": payload["actor_id"]}
            )
        elif method == "GET" and decision["action_type"] == "retrain":
            if decision["decision_state"] == "approved":
                decision["decision_state"] = "executed"
                decision["execution_result"] = {
                    "status": "succeeded",
                    "plane": "research",
                    "execution_ref_id": f"rrun-{decision_id}",
                    "executed_at": "2026-07-14T00:00:00Z",
                }
                decision["cooldown_ends_at"] = "2026-07-17T00:00:00Z"
                decision["observation_window_ends_at"] = "2026-07-21T00:00:00Z"
                decision["review_chain"].append(
                    {
                        "step_type": "executed",
                        "actor_id": "evolution-dispatch-worker",
                    }
                )
        return deepcopy(decision)


def test_request_ledger_records_per_request_timestamps(monkeypatch):
    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(probe.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    ledger = []

    result = probe._request_json(
        api_url="http://evolution.test",
        method="GET",
        path="/health",
        payload=None,
        timeout_seconds=1,
        request_ledger=ledger,
    )

    assert result == {"ok": True}
    assert ledger[0]["requested_at"].endswith("Z")
    assert ledger[0]["completed_at"].endswith("Z")


def test_initial_probe_never_executes_directly_and_leaves_freeze_approved(
    monkeypatch,
):
    fake = _FakeEvolutionApi()
    monkeypatch.setattr(probe, "_request_json", fake.request)

    output = probe.run_initial_probe(
        api_url="http://evolution.test",
        prefix="evoloop-test",
        timeout_seconds=1,
        poll_timeout_seconds=1,
        poll_interval_seconds=0,
        freeze_observation_seconds=0,
    )

    assert output["phase"] == "initial"
    assert output["direct_execute_calls_by_probe"] == 0
    assert output["mutating_request_count"] == 6
    assert all(
        entry["path"].endswith(("/review", "/approve"))
        or entry["path"] == "/api/evolution/proposals"
        for entry in output["mutating_requests"]
    )
    assert output["research"]["decision_state"] == "executed"
    assert output["research"]["execution_result"]["status"] == "succeeded"
    assert output["research"]["execution_result"]["execution_ref_id"] == (
        "rrun-evoloop-test-research"
    )
    assert output["freeze"]["decision_state"] == "approved"
    assert output["freeze"]["execution_result"] is None
    assert output["freeze"]["executed_step_count"] == 0
    assert output["freeze"]["metadata_has_active_runtime"] is False


def test_verify_probe_is_read_only_and_preserves_exact_execution_ref(monkeypatch):
    fake = _FakeEvolutionApi()
    monkeypatch.setattr(probe, "_request_json", fake.request)
    initial = probe.run_initial_probe(
        api_url="http://evolution.test",
        prefix="evoloop-restart",
        timeout_seconds=1,
        poll_timeout_seconds=1,
        poll_interval_seconds=0,
        freeze_observation_seconds=0,
    )

    output = probe.run_verify_probe(
        api_url="http://evolution.test",
        initial=initial,
        timeout_seconds=1,
    )

    assert output["phase"] == "verify"
    assert output["mutating_request_count"] == 0
    assert output["direct_execute_calls_by_probe"] == 0
    assert output["research"]["execution_result"]["execution_ref_id"] == (
        initial["research"]["execution_result"]["execution_ref_id"]
    )
    assert output["research"]["executed_step_count"] == 1
    assert output["freeze"]["decision_state"] == "approved"


def test_compose_ownership_snapshot_requires_exact_labels_and_source(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "services/evolution/dispatch_worker.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('exact ref')\n", encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_sha = "a" * 40
    container_id = "b" * 64
    labels = {
        "com.docker.compose.project": "pantheon",
        "com.docker.compose.service": "evolution-dispatch-worker",
        "com.docker.compose.project.working_dir": str(tmp_path),
        "com.docker.compose.project.config_files": str(tmp_path / "docker-compose.yml"),
        "com.docker.compose.config-hash": "config-hash",
    }
    inspect = [
        {
            "Name": "/pantheon-evolution-dispatch-worker-1",
            "Image": "sha256:image",
            "Config": {"Labels": labels, "Cmd": ["python", "-m", "worker"]},
            "State": {
                "Running": True,
                "StartedAt": "2026-07-14T00:00:00.000000000Z",
                "Health": {"Status": "healthy"},
            },
        }
    ]

    def fake_run(args):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return expected_sha
        if args[:2] == ["git", "status"]:
            return ""
        if args[-2:] == ["config", "--services"]:
            return "evolution\nevolution-dispatch-worker"
        if args[-3:] == ["config", "--hash", "evolution-dispatch-worker"]:
            return "evolution-dispatch-worker config-hash"
        if "ps" in args and "-q" in args:
            return container_id
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(inspect)
        if args[:2] == ["docker", "exec"]:
            return source_hash
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(compose_probe, "_run", fake_run)

    snapshot = compose_probe._ownership_snapshot(expected_sha=expected_sha)

    assert snapshot["checkout_sha"] == expected_sha
    assert snapshot["container_id"] == container_id
    assert snapshot["labels"] == labels
    assert snapshot["worktree"]["task_scope_clean"] is True
    assert snapshot["rendered_service_config_hash"] == "config-hash"
    assert snapshot["host_source_sha256"] == source_hash
    assert snapshot["container_source_sha256"] == source_hash


def test_compose_ownership_snapshot_rejects_orphan_label(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "services/evolution/dispatch_worker.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('exact ref')\n", encoding="utf-8")
    expected_sha = "a" * 40

    def fake_run(args):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return expected_sha
        if args[:2] == ["git", "status"]:
            return ""
        if args[-2:] == ["config", "--services"]:
            return "evolution-dispatch-worker"
        if "ps" in args and "-q" in args:
            return "container-id"
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "different-project"
                            }
                        },
                        "State": {"Running": True, "Health": {"Status": "healthy"}},
                    }
                ]
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(compose_probe, "_run", fake_run)

    with pytest.raises(probe.ProbeError, match="container label"):
        compose_probe._ownership_snapshot(expected_sha=expected_sha)


def test_runtime_scope_allows_only_known_live_task_brief_drift(monkeypatch):
    def fake_run(args):
        if args[:2] == ["git", "status"] and "--" in args:
            return ""
        if args[:2] == ["git", "status"]:
            # Simulate _run().strip() removing the first porcelain line's
            # leading space without removing the path's leading dot.
            return "M .orchestrator/task-briefs/other_task.md"
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(compose_probe, "_run", fake_run)

    snapshot = compose_probe._runtime_scope_snapshot()

    assert snapshot["task_scope_clean"] is True
    assert snapshot["full_worktree_clean"] is False
    assert snapshot["allowed_runtime_dirty_paths"] == [
        ".orchestrator/task-briefs/other_task.md"
    ]


def test_runtime_scope_rejects_unexpected_dirty_path(monkeypatch):
    def fake_run(args):
        if args[:2] == ["git", "status"] and "--" in args:
            return ""
        if args[:2] == ["git", "status"]:
            return " M services/evolution/main.py\n"
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(compose_probe, "_run", fake_run)

    with pytest.raises(probe.ProbeError, match="unexpected dirty paths"):
        compose_probe._runtime_scope_snapshot()


def test_restart_log_parser_requires_exact_first_tick_one():
    with pytest.raises(probe.ProbeError, match="exact tick 1"):
        compose_probe._validated_restart_events(
            'evolution-dispatch-worker-1 | {"tick": 10, "result": {}}\n'
        )

    events, ticks = compose_probe._validated_restart_events(
        'evolution-dispatch-worker-1 | {"tick": 1, "result": {}}\n'
        'evolution-dispatch-worker-1 | {"tick": 2, "result": {}}\n'
    )
    assert ticks == [1, 2]
    assert len(events) == 2
