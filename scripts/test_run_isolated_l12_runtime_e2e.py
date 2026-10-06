from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from scripts import run_isolated_l12_runtime_e2e as harness


def test_direct_invocation_has_no_supervisor_resource_contract() -> None:
    assert harness._supervised_execution_resources({}) is None


def test_supervised_invocation_requires_well_formed_resource_metadata() -> None:
    with pytest.raises(ValueError, match="missing ORCH_TASK_EXECUTION_RESOURCES"):
        harness._supervised_execution_resources({"ORCH_TASK_ID": "TASK-1"})

    with pytest.raises(ValueError, match="invalid ORCH_TASK_EXECUTION_RESOURCES"):
        harness._supervised_execution_resources(
            {
                "ORCH_TASK_ID": "TASK-1",
                "ORCH_TASK_EXECUTION_RESOURCES": "not-json",
            }
        )


def test_supervised_compose_requires_existing_pantheon_dev_resource() -> None:
    task = ("TASK-1", set())

    with pytest.raises(ValueError, match="must declare execution_resources"):
        harness._validate_supervised_compose_admission(
            task,
            provision_services=True,
            teardown=False,
            preserve_provisioned_stack=False,
        )

    harness._validate_supervised_compose_admission(
        ("TASK-1", {"pantheon-dev"}),
        provision_services=True,
        teardown=False,
        preserve_provisioned_stack=False,
    )


def test_supervised_compose_cannot_preserve_stack() -> None:
    with pytest.raises(ValueError, match="cannot preserve provisioned Compose"):
        harness._validate_supervised_compose_admission(
            ("TASK-1", {"pantheon-dev"}),
            provision_services=True,
            teardown=False,
            preserve_provisioned_stack=True,
        )


def test_compose_lease_uses_existing_cas_lease_and_releases_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = {
        "schemaVersion": 1,
        "repository": harness.dev_environment_lease.DEFAULT_REPOSITORY,
        "branch": harness.dev_environment_lease.DEFAULT_BRANCH,
        "path": harness.dev_environment_lease.DEFAULT_PATH,
        "resource": harness.dev_environment_lease.DEFAULT_RESOURCE,
        "mode": "qualification",
        "owner": "test-owner",
        "leaseId": "f9865193-5bb4-4e44-8ce8-e3b6d73a6c76",
        "acquiredAt": now.isoformat().replace("+00:00", "Z"),
        "heartbeatAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "expectedBackendSha": "",
        "runUrl": "",
    }
    manager_calls: dict[str, object] = {}

    class FakeManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self, **kwargs: object) -> tuple[dict[str, object], str, object]:
            manager_calls["acquire"] = kwargs
            return state, "a" * 40, now

        def release(self, local: object) -> None:
            manager_calls["release"] = local

    class FakeHeartbeat:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.stdin = io.StringIO()
            self.signals: list[int] = []

        def poll(self) -> None:
            return None

        def send_signal(self, signal_number: int) -> None:
            self.signals.append(signal_number)

        def wait(self, timeout: float) -> int:
            assert timeout == 5
            return 0

    monkeypatch.setattr(harness, "_lease_token_from_github_cli", lambda: "test-token")
    monkeypatch.setattr(harness.dev_environment_lease, "LeaseManager", FakeManager)
    monkeypatch.setattr(harness.subprocess, "Popen", FakeHeartbeat)
    monkeypatch.setattr(harness.signal, "signal", lambda *_args: None)

    session = harness._DevEnvironmentLeaseSession(compose_project="unit-test")
    acquire = manager_calls["acquire"]
    assert isinstance(acquire, dict)
    assert acquire["mode"] == "qualification"
    assert acquire["ttl_seconds"] == 300
    assert acquire["wait_seconds"] == 0
    assert acquire["poll_seconds"] == 1.0
    assert acquire["expected_backend_sha"] == ""
    assert acquire["run_url"] == ""
    assert str(acquire["owner"]).startswith("l12-compose:")
    session.close()
    released = manager_calls["release"]
    assert isinstance(released, dict)
    assert released["leaseId"] == state["leaseId"]


def test_busy_shared_lease_returns_before_compose_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BusySession:
        def __init__(self, **_kwargs: object) -> None:
            raise harness.DevEnvironmentLeaseBusy("held by deployment")

    monkeypatch.setattr(harness, "_DevEnvironmentLeaseSession", BusySession)
    assert harness.main(["--provision-services"]) == 75


def test_stimulus_gate_stack_covers_every_domain_suite_url() -> None:
    assert harness.STIMULUS_GATE_SUITE.endswith("test_stimulus_cross_loop_deployed_e2e.py")
    assert set(harness.STIMULUS_SERVICES).isdisjoint(harness.SERVICES)
    assert {"research", "training", "policy_learning", "consultation"} == set(
        harness.STIMULUS_SERVICES
    )
    assert set(harness.STIMULUS_COMPOSE_SERVICES).isdisjoint(
        harness.REQUIRED_COMPOSE_SERVICES
    )


def test_teardown_down_command_carries_all_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        seen.append(list(cmd))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "_project_container_ids", lambda _p: [])
    result = harness._teardown_project("proj", ["a.yml"], {})
    command = result["command"]
    assert command[command.index("--profile") : command.index("--profile") + 2] == [
        "--profile",
        "*",
    ]
    assert command.index("--profile") < command.index("down")
    assert result["zero_project_containers"] is True


def test_teardown_fails_closed_when_containers_remain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda cmd, **_k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr(harness, "_project_container_ids", lambda _p: ["abc"])
    assert harness._teardown_project("proj", ["a.yml"], {})["zero_project_containers"] is False


def test_one_shot_projector_is_not_in_wait_set() -> None:
    assert harness.STIMULUS_PROJECTOR_SERVICE == "source-ingest-agora-projector"
    assert harness.STIMULUS_PROJECTOR_SERVICE not in harness.STIMULUS_COMPOSE_SERVICES
    assert harness.STIMULUS_PROJECTOR_SERVICE not in harness.REQUIRED_COMPOSE_SERVICES


def test_failure_diagnostics_capture_ps_and_unhealthy_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json as _json

    calls: list[list[str]] = []
    rows = [
        {"Service": "ok", "State": "running", "Health": "healthy"},
        {"Service": "projector", "State": "exited", "Health": ""},
        {"Service": "sick", "State": "running", "Health": "unhealthy"},
    ]

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        out = "\n".join(_json.dumps(r) for r in rows) if "ps" in cmd else "log-tail"
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    result = harness._capture_failure_diagnostics("proj", ["a.yml"], {}, tmp_path)
    assert result["captured_services"] == ["projector", "sick"]
    assert (tmp_path / "compose-ps.txt").is_file()
    assert (tmp_path / "logs-projector.txt").read_text().endswith("log-tail\n")
    assert not (tmp_path / "logs-ok.txt").exists()
    log_calls = [c for c in calls if "logs" in c]
    assert all("--tail" in c and "200" in c and "--no-color" in c for c in log_calls)
