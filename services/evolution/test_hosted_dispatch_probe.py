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
                    "status": "submitted",
                    "plane": "research",
                    "execution_ref_id": f"dispatch-{decision_id}",
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
    assert output["research"]["execution_result"]["execution_ref_id"] == (
        "dispatch-evoloop-test-research"
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
    }
    inspect = [
        {
            "Name": "/pantheon-evolution-dispatch-worker-1",
            "Image": "sha256:image",
            "Config": {"Labels": labels},
            "State": {"Running": True, "Health": {"Status": "healthy"}},
        }
    ]

    def fake_run(args):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return expected_sha
        if args[-2:] == ["config", "--services"]:
            return "evolution\nevolution-dispatch-worker"
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
