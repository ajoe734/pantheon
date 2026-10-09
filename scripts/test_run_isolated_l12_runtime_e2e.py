from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
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
    monkeypatch.setattr(
        harness._DevEnvironmentLeaseSession,
        "_verify_heartbeat_started",
        lambda *_args: None,
    )

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


def _fake_lease_state() -> dict[str, object]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": 1,
        "repository": harness.dev_environment_lease.DEFAULT_REPOSITORY,
        "branch": harness.dev_environment_lease.DEFAULT_BRANCH,
        "path": harness.dev_environment_lease.DEFAULT_PATH,
        "resource": harness.dev_environment_lease.DEFAULT_RESOURCE,
        "mode": "qualification",
        "owner": "test-owner",
        "leaseId": "f9865193-5bb4-4e44-8ce8-e3b6d73a6c76",
        "acquiredAt": stamp,
        "heartbeatAt": stamp,
        "expiresAt": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "expectedBackendSha": "",
        "runUrl": "",
    }


def _install_fake_manager(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, object]
) -> None:
    state = _fake_lease_state()

    class FakeManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self, **_kwargs: object) -> tuple[dict[str, object], str, object]:
            return state, "a" * 40, datetime.now(timezone.utc)

        def release(self, local: object) -> None:
            calls["release"] = local

    monkeypatch.setattr(harness.dev_environment_lease, "LeaseManager", FakeManager)
    monkeypatch.setattr(harness.signal, "signal", lambda *_args: None)


def test_inherited_token_env_with_token_stdin_is_rejected_by_real_child(
    tmp_path: Path,
) -> None:
    """Reproduces the old startup rejection with a synthetic, non-credential token."""

    env = dict(os.environ)
    env[harness.dev_environment_lease.TOKEN_ENV] = "synthetic-not-a-credential"
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(harness.dev_environment_lease.__file__).resolve()),
            "heartbeat-loop",
            "--state-file",
            str(state_file),
            "--token-stdin",
        ],
        input="synthetic-not-a-credential\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 78
    assert "mutually exclusive" in result.stderr


def test_heartbeat_child_gets_token_only_on_stdin_and_verified_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_fake_manager(monkeypatch, calls)
    monkeypatch.setenv(harness.dev_environment_lease.TOKEN_ENV, "synthetic-token")
    monkeypatch.setattr(harness, "_lease_token_from_github_cli", lambda: "synthetic-token")
    seen: dict[str, object] = {}
    real_popen = subprocess.Popen

    def popen(command: list[str], **kwargs: object) -> subprocess.Popen[str]:
        seen["env"] = kwargs["env"]
        seen["command"] = command
        # Run the real CLI with a stand-in that stays alive and writes the
        # real identity file, without any network access.
        child = real_popen(
            [
                sys.executable,
                "-c",
                "import sys,runpy;sys.argv=sys.argv[1:];"
                "sys.stdin.read();"
                "import scripts.dev_environment_lease as d;"
                "import os,time,signal;"
                "a=d.build_parser().parse_args(sys.argv[1:]);"
                "d.atomic_write_json(a.identity_json_out,"
                "d.heartbeat_identity_payload(os.getpid(),expected_cli=d.__file__,"
                "state_file=a.state_file),0o644);"
                "signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));time.sleep(30)",
                *command[1:],
            ],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            text=True,
            env={**kwargs["env"], "PYTHONPATH": str(harness.REPO_ROOT)},  # type: ignore[arg-type]
            cwd=harness.REPO_ROOT,
        )
        return child

    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    monkeypatch.setattr(
        harness.dev_environment_lease,
        "verify_heartbeat_identity",
        lambda identity, **_kwargs: identity,
    )
    session = harness._DevEnvironmentLeaseSession(compose_project="unit-test")
    try:
        assert harness.dev_environment_lease.TOKEN_ENV not in seen["env"]  # type: ignore[operator]
        assert "--token-stdin" in seen["command"]  # type: ignore[operator]
        assert "synthetic-token" not in " ".join(seen["command"])  # type: ignore[arg-type]
    finally:
        session.close()
    assert "release" in calls


def test_heartbeat_startup_exit_releases_exact_owner_and_blocks_compose(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: dict[str, object] = {}
    _install_fake_manager(monkeypatch, calls)
    monkeypatch.setattr(harness, "_lease_token_from_github_cli", lambda: "synthetic-token")
    real_popen = subprocess.Popen

    def popen_with_old_inherited_env(
        command: list[str], **kwargs: object
    ) -> subprocess.Popen[str]:
        env = dict(kwargs["env"])  # type: ignore[arg-type]
        env[harness.dev_environment_lease.TOKEN_ENV] = "synthetic-token"
        return real_popen(command, **{**kwargs, "env": env})  # type: ignore[arg-type]

    monkeypatch.setattr(harness.subprocess, "Popen", popen_with_old_inherited_env)
    compose_calls: list[object] = []
    monkeypatch.setattr(harness, "_run", lambda *a, **k: compose_calls.append(a))
    monkeypatch.delenv(harness.WORKER_TASK_ID_ENV, raising=False)

    assert harness.main(["--provision-services"]) == 78
    err = capsys.readouterr().err
    assert "exited during startup" in err
    assert "mutually exclusive" in err
    assert "synthetic-token" not in err
    assert compose_calls == []
    released = calls["release"]
    assert isinstance(released, dict)
    assert released["leaseId"] == _fake_lease_state()["leaseId"]


def test_busy_shared_lease_returns_before_compose_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BusySession:
        def __init__(self, **_kwargs: object) -> None:
            raise harness.DevEnvironmentLeaseBusy("held by deployment")

    monkeypatch.setattr(harness, "_DevEnvironmentLeaseSession", BusySession)
    monkeypatch.delenv(harness.WORKER_TASK_ID_ENV, raising=False)
    assert harness.main(["--provision-services"]) == 75


def test_stimulus_gate_stack_covers_every_domain_suite_url() -> None:
    assert harness.STIMULUS_GATE_SUITE.endswith("test_stimulus_cross_loop_deployed_e2e.py")
    assert set(harness.STIMULUS_SERVICES).isdisjoint(harness.SERVICES)
    assert {"research", "training", "policy_learning", "consultation", "persona"} == set(
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
    down_args = command[command.index("down") :]
    assert down_args[down_args.index("--rmi") + 1] == "local"
    assert "all" not in down_args


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
    full_log_calls = [c for c in calls if "logs" in c and "200" in c]
    assert sorted(c[-1] for c in full_log_calls) == ["projector", "sick"]
    assert all("--tail" in c and "--no-color" in c for c in full_log_calls)
    scan_calls = [c for c in calls if "logs" in c and harness.SERVICE_ERROR_SCAN_TAIL in c]
    assert sorted(c[-1] for c in scan_calls) == ["ok", "projector", "sick"]
    assert (tmp_path / "service-error-lines.txt").read_text().splitlines() == [
        "## ok: 0 error-signal line(s)",
        "## projector: 0 error-signal line(s)",
        "## sick: 0 error-signal line(s)",
    ]


def test_failure_diagnostics_keep_error_lines_of_healthy_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json as _json

    rows = [
        {"Service": "training-session-svc", "State": "running", "Health": "healthy"},
        {"Service": "training-session-preview-worker", "State": "running", "Health": "healthy"},
        {"Service": "quiet", "State": "running", "Health": "healthy"},
    ]
    worker_log = "\n".join(
        ["INFO tick ok"]
        + [f"ERROR job_id=j{i} terminal_session_http_error=409 proof required" for i in range(60)]
    )
    logs = {
        "training-session-svc": "\n".join(
            [
                'INFO: 172.18.0.9:4100 - "GET /readyz HTTP/1.1" 200 OK',
                'INFO: 172.18.0.9:4101 - "POST /api/training/sessions/trn-1/complete HTTP/1.1" 409 Conflict',
                "Traceback (most recent call last):",
            ]
        ),
        "training-session-preview-worker": worker_log,
        "quiet": "INFO all good",
    }

    def fake_run(cmd, **_kwargs):
        if "ps" in cmd:
            out = "\n".join(_json.dumps(r) for r in rows)
        else:
            out = logs.get(cmd[-1], "")
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    result = harness._capture_failure_diagnostics("proj", ["a.yml"], {}, tmp_path)

    assert result["captured_services"] == []
    assert not list(tmp_path.glob("logs-*.txt"))
    lines = (tmp_path / "service-error-lines.txt").read_text().splitlines()
    assert "## quiet: 0 error-signal line(s)" in lines
    svc = lines.index("## training-session-svc: 2 error-signal line(s)")
    assert lines[svc + 1].endswith("409 Conflict")
    assert lines[svc + 2] == "Traceback (most recent call last):"
    worker = lines.index("## training-session-preview-worker: 60 error-signal line(s)")
    kept = lines[worker + 1 : worker + 1 + harness.SERVICE_ERROR_LINES_PER_SERVICE]
    assert len(kept) == harness.SERVICE_ERROR_LINES_PER_SERVICE
    assert kept[-1].startswith("ERROR job_id=j59")


def test_service_error_line_matches_worker_json_and_skips_idle_ticks() -> None:
    match = harness.SERVICE_ERROR_LINE.search
    failed_tick = (
        '{"tick": 2, "result": {"jobs_found": 1, "completed": 1, "failed": 1, "errors": '
        '["job_id=pvjob terminal_session_http_error=409 {\\"detail\\":\\"authority changed\\"}"]}}'
    )
    assert match(failed_tick)
    assert match('{"tick": 3, "result": {"failed": 0, "errors": ["TimeoutError"]}}')
    assert match('{"tick": 4, "result": {"failed": 2, "errors": []}}')
    assert match('{"result": {"error": "boom"}}')
    assert match("asyncio.exceptions.TimeoutError: timed out")
    assert not match('{"tick": 1, "result": {"jobs_found": 0, "completed": 0, "failed": 0, "errors": []}}')
    assert not match('{"result": {"error": null}}')
    detail = "CONSULTATION_HANDOFF_SINK_URL is not configured; downstream acknowledgement is required"
    for outcome in ("blocked", "dead_letter"):
        assert match(
            '{"errors": [], "outcomes": [{"request_id": "r", "outcome": "%s", "detail": "%s"}]}'
            % (outcome, detail)
        )
    assert not match('{"errors": [], "outcomes": [{"request_id": "r", "outcome": "completed"}]}')
    assert not match('{"errors": [], "outcomes": [], "blocked": 0, "dead_lettered": 0}')
    assert not match("INFO tick ok")


def test_projection_bootstrap_pipes_postgres_config_into_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("input")))
        stdout = '{"services": {"postgres": {}}}' if "config" in cmd else ""
        return type("P", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    harness._bootstrap_trade_journey_projection("proj", ["a.yml"], {})

    config_cmd, _ = calls[0]
    assert config_cmd[config_cmd.index("--profile") : config_cmd.index("--profile") + 2] == [
        "--profile",
        "core",
    ]
    assert config_cmd[-4:] == ["config", "--format", "json", "postgres"]
    run_cmd, run_input = calls[1]
    assert run_input == '{"services": {"postgres": {}}}'
    assert "--no-deps" in run_cmd
    assert run_cmd[run_cmd.index("--entrypoint") + 1] == "python"
    assert harness.PROJECTION_BOOTSTRAP_SERVICE in run_cmd
    assert run_cmd[-4:] == [
        "scripts.lifecycle_projector_migrate",
        "--bootstrap-only",
        "--compose-config-stdin",
        "--reconcile-runtime-role",
    ]


def test_projection_bootstrap_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, **_kwargs):
        code = 0 if "config" in cmd else 3
        return type("P", (), {"returncode": code, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="projection bootstrap failed"):
        harness._bootstrap_trade_journey_projection("proj", ["a.yml"], {})


def test_mint_projector_service_jwt_has_required_role_and_claims() -> None:
    secret = "test-isolated-secret-48"
    token = harness._mint_projector_service_jwt(
        secret,
        tenant_id="tenant-isolated",
        issuer="pantheon-l12-isolated-e2e",
        audience="pantheon-operator-bff",
    )
    import base64
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    assert claims["roles"] == ["source_ingest_reader"]
    assert claims["tenant_id"] == "tenant-isolated"
    assert claims["iss"] == "pantheon-l12-isolated-e2e"
    assert claims["aud"] == "pantheon-operator-bff"
    assert claims["sub"] == "agora-market-projector"


def test_stimulus_gate_projector_credential_passed_only_to_projector_and_redacted(
    tmp_path: Path,
) -> None:
    secret = "secret-isolated-jwt-token"
    token = harness._mint_projector_service_jwt(secret, tenant_id="tenant-dev")

    compose_env = {
        "PANTHEON_RUNTIME_JWT_SECRET": secret,
        "PANTHEON_TENANT_ID": "tenant-dev",
    }
    projector_env = dict(compose_env)
    projector_env["AGORA_PROJECTOR_SERVICE_JWT"] = token
    projector_env["PANTHEON_TENANT_ID"] = "tenant-dev"

    # Token must only be in projector_env, not in compose_env
    assert "AGORA_PROJECTOR_SERVICE_JWT" not in compose_env
    assert projector_env["AGORA_PROJECTOR_SERVICE_JWT"] == token
    assert projector_env["PANTHEON_TENANT_ID"] == "tenant-dev"

    # Ensure token redaction works for diagnostics
    diag_file = tmp_path / "diagnostics" / f"{harness.STIMULUS_PROJECTOR_SERVICE}.txt"
    diag_file.parent.mkdir(parents=True)
    raw_output = f"# exit=0\nprojected with {token}\n"
    sanitized = raw_output.replace(token, "[REDACTED]")
    diag_file.write_text(sanitized, encoding="utf-8")

    content = diag_file.read_text(encoding="utf-8")
    assert token not in content
    assert "[REDACTED]" in content


def test_projector_run_rebuilds_its_image() -> None:
    command = harness._projector_run_command("proj", ["a.yml"])
    assert command[command.index("run") :] == [
        "run",
        "--rm",
        "--build",
        harness.STIMULUS_PROJECTOR_SERVICE,
    ]


def test_suite_url_env_covers_every_url_the_domain_suites_read() -> None:
    import re
    from pathlib import Path

    root = Path(harness.__file__).resolve().parents[1]
    services = {**harness.SERVICES, **harness.STIMULUS_SERVICES}
    provided = harness._suite_url_env({name: f"http://127.0.0.1/{name}" for name in services})
    assert provided["PANTHEON_L12_SOURCE_URL"] == provided["PANTHEON_L12_SOURCE_INGEST_URL"]
    for suite in (
        "tests/integration/l12/test_current_research_loops_deployed_e2e.py",
        "tests/integration/l12/test_current_human_learning_deployed_e2e.py",
        "tests/integration/l12/test_current_runtime_loops_deployed_e2e.py",
    ):
        source = (root / suite).read_text(encoding="utf-8")
        read = set(re.findall(r'getenv\(\s*"(PANTHEON_L12_[A-Z_]+_URL)"', source))
        assert read <= set(provided), f"{suite} reads unprovided URLs: {sorted(read - set(provided))}"


def test_isolated_reader_token_is_a_signed_tenant_scoped_reader_jwt() -> None:
    import base64
    import hashlib
    import hmac
    import json

    env = {"PANTHEON_BFF_JWT_SECRET": "s3cret", "PANTHEON_BFF_JWT_ISSUER": "iss", "PANTHEON_BFF_JWT_AUDIENCE": "aud"}
    token, tenant = harness._isolated_reader_token(env, "l12-domain-suites")
    header, claims, signature = token.split(".")
    pad = lambda part: part + "=" * (-len(part) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert tenant == "default"
    assert decoded["sub"] == "l12-domain-suites"
    assert decoded["roles"] == ["source_ingest_reader"]
    assert decoded["tenant_id"] == "default"
    assert (decoded["iss"], decoded["aud"]) == ("iss", "aud")
    expected = hmac.new(b"s3cret", f"{header}.{claims}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(pad(signature)) == expected


def _decoded_claims(token: str, secret: str) -> dict:
    import base64
    import hashlib
    import hmac

    header, claims, signature = token.split(".")
    pad = lambda part: part + "=" * (-len(part) % 4)
    expected = hmac.new(secret.encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(pad(signature)) == expected
    return json.loads(base64.urlsafe_b64decode(pad(claims)))


def _isolated_signer_env() -> dict[str, str]:
    return {
        "PANTHEON_BFF_JWT_SECRET": "x" * 64,
        "PANTHEON_BFF_JWT_ISSUER": harness.ISOLATED_SAFE_CONTROLS["PANTHEON_BFF_JWT_ISSUER"],
        "PANTHEON_BFF_JWT_AUDIENCE": harness.ISOLATED_SAFE_CONTROLS["PANTHEON_BFF_JWT_AUDIENCE"],
    }


def test_isolated_stack_binds_owner_verifiers_and_principals_like_the_dev_deploy() -> None:
    signer = _isolated_signer_env()
    env = harness._isolated_dev_principal_env(signer)

    assert env["PANTHEON_BFF_TENANT_ID"] == env["PANTHEON_DEPLOYMENT_TENANT_ID"] == "tenant-dev"
    assert env["PANTHEON_REGISTRY_JWT_SECRET"] == env["PANTHEON_GOVERNANCE_JWT_SECRET"] == signer["PANTHEON_BFF_JWT_SECRET"]
    assert env["PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED"] == "true"
    assert env["DISTILLATION_REGISTRY_SERVICE_TOKEN_FILE"] == "/run/pantheon-principals/DISTILLATION_REGISTRY_SERVICE_TOKEN"
    writer = _decoded_claims(env["DISTILLATION_REGISTRY_SERVICE_TOKEN"], signer["PANTHEON_BFF_JWT_SECRET"])
    assert writer["roles"] == ["registry-writer"] and writer["tenant_id"] == "tenant-dev"
    assert (writer["iss"], writer["aud"]) == (env["PANTHEON_REGISTRY_JWT_ISSUER"], env["PANTHEON_REGISTRY_JWT_AUDIENCE"])
    # The evidence report records ISOLATED_SAFE_CONTROLS; no principal may leak into it.
    assert not set(env) & set(harness.ISOLATED_SAFE_CONTROLS)


def test_human_tokens_carry_the_identity_strict_registry_and_governance_require() -> None:
    env = {**_isolated_signer_env()}
    env.update(harness._isolated_dev_principal_env(env))
    token = harness._isolated_human_token(env, "l12-reviewer", "governance_reviewer")

    for owner in ("REGISTRY", "GOVERNANCE"):
        claims = _decoded_claims(token, env[f"PANTHEON_{owner}_JWT_SECRET"])
        assert (claims["iss"], claims["aud"]) == (
            env.get(f"PANTHEON_{owner}_JWT_ISSUER"), env.get(f"PANTHEON_{owner}_JWT_AUDIENCE"),
        )
    assert claims["sub"] == "l12-reviewer" and claims["exp"] > 0
    assert claims["roles"] == ["governance_reviewer"]
    assert claims["tenant_id"] == "tenant-dev"


def test_principal_issuer_starts_before_the_owner_stack() -> None:
    from pathlib import Path

    source = Path(harness.__file__).read_text(encoding="utf-8")
    issuer = source.index("PRINCIPAL_ISSUER_SERVICE,\n")
    owners = source.index("for name in required_services if name != TW_OFFICIAL_PULL_SERVICE]")
    assert source.index("_bootstrap_trade_journey_projection(\n                args") < issuer < owners


def test_tw_official_pull_env_mirrors_bounded_refresh_entrypoint() -> None:
    env = harness._tw_official_pull_env({"KEEP": "1", "SOURCE_INGEST_TW_HISTORY_SYMBOLS": "2317.TW"})

    assert env["KEEP"] == "1"
    assert env["PANTHEON_EXTERNAL_EGRESS"] == "allowlist"
    assert env["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == (
        "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw"
    )
    assert env["SOURCE_INGEST_CONTROLLER_MODE"] == "reconcile_and_pull"
    assert env["SOURCE_INGEST_CONTROLLER_RESTART_POLICY"] == "no"
    assert env["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] == "1"
    assert env["SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS"] == "tw-twse-tpex-official-market"
    assert env["SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS"] == "tw-twse-tpex-official-market"
    # 2330.TW is always part of the history symbols the runtime suite reads.
    assert env["SOURCE_INGEST_TW_HISTORY_SYMBOLS"] == "2317.TW,2330.TW"


def test_tw_official_pull_commands_reuse_compose_services() -> None:
    commands = harness._tw_official_pull_commands("proj", ["a.yml"])

    assert commands["source_ingest_up"][:6] == ["docker", "compose", "-p", "proj", "-f", "a.yml"]
    assert commands["source_ingest_up"][-1] == "source-ingest"
    assert "--no-deps" in commands["scheduler_tick"]
    assert commands["scheduler_tick"][-1] == "source-ingest-scheduler"
    assert "run" in commands["scheduler_tick"]


def _fake_pull_run(monkeypatch: pytest.MonkeyPatch, tick_rc: int, snapshot: object):
    import subprocess

    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, env=None, **_kwargs):
        calls.append((list(cmd), dict(env or {})))
        rc = tick_rc if "run" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="out", stderr="err")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "_get_json", lambda url, headers=None: snapshot)
    return calls


def test_tw_official_pull_runs_tick_then_restores_egress(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = _fake_pull_run(monkeypatch, 0, {"closes": [1.0, 2.0]})

    result = harness._run_tw_official_pull(
        "proj", ["a.yml"], {"X": "y"}, snapshot_url="http://s", reader_headers={}, diagnostics_dir=tmp_path
    )

    assert [("run" in c) for c, _ in calls] == [False, True, False]
    assert calls[0][1]["PANTHEON_EXTERNAL_EGRESS"] == "allowlist"
    assert calls[2][1].get("PANTHEON_EXTERNAL_EGRESS") is None
    assert result["snapshot_closes"] == 2
    assert (tmp_path / "tw-official-pull-scheduler.txt").exists()


def test_tw_official_pull_fails_on_scheduler_error_and_still_restores(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = _fake_pull_run(monkeypatch, 3, {"closes": [1.0, 2.0]})

    with pytest.raises(RuntimeError, match="tick exited 3"):
        harness._run_tw_official_pull(
            "proj", ["a.yml"], {}, snapshot_url="http://s", reader_headers={}, diagnostics_dir=tmp_path
        )
    # up, tick, source-ingest logs (kept before the restore recreates it), restore
    assert len(calls) == 4
    assert "logs" in calls[2][0] and calls[2][0][-1] == "source-ingest"
    assert "up" in calls[3][0] and calls[3][0][-1] == "source-ingest"
    assert (tmp_path / "tw-official-pull-source-ingest-logs.txt").exists()


def test_tw_official_pull_fails_on_empty_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _fake_pull_run(monkeypatch, 0, {"error": "404"})

    with pytest.raises(RuntimeError, match="no usable 2330.TW snapshot"):
        harness._run_tw_official_pull(
            "proj", ["a.yml"], {}, snapshot_url="http://s", reader_headers={}, diagnostics_dir=tmp_path
        )


def test_tw_official_pull_takes_a_short_lease_and_builds_the_scheduler() -> None:
    env = harness._tw_official_pull_env({})
    commands = harness._tw_official_pull_commands("proj", ["a.yml"])

    lease = str(harness.TW_OFFICIAL_PULL_LEASE_SECONDS)
    assert env["SOURCE_INGEST_CONTROLLER_LEASE_SECONDS"] == lease
    assert env["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS"] == lease
    assert "--build" in commands["scheduler_tick"]
    assert commands["scheduler_start"][-1] == "source-ingest-scheduler"
    assert "up" in commands["scheduler_start"] and "--wait" in commands["scheduler_start"]


def test_resident_scheduler_starts_after_the_pull_with_steady_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = _fake_pull_run(monkeypatch, 0, {"closes": [1.0, 2.0]})
    sleeps: list[float] = []
    monkeypatch.setattr(harness.time, "sleep", sleeps.append)

    harness._run_tw_official_pull(
        "proj", ["a.yml"], {"X": "y"}, snapshot_url="http://s", reader_headers={},
        diagnostics_dir=tmp_path, start_resident_scheduler=True,
    )

    assert len(calls) == 4
    start_cmd, start_env = calls[3]
    assert start_cmd[-1] == "source-ingest-scheduler" and "up" in start_cmd
    assert start_env.get("SOURCE_INGEST_CONTROLLER_MODE") is None
    assert sleeps == [harness.TW_OFFICIAL_PULL_LEASE_SECONDS + 5]


def test_initial_provisioning_leaves_the_resident_scheduler_for_after_the_pull() -> None:
    from pathlib import Path

    source = Path(harness.__file__).read_text(encoding="utf-8")
    provision = source.index("for name in required_services if name != TW_OFFICIAL_PULL_SERVICE]")
    pull = source.index("tw_official_pull = _run_tw_official_pull(")
    assert provision < pull
    assert "start_resident_scheduler=TW_OFFICIAL_PULL_SERVICE in required_services" in source


def test_capital_verifier_secret_matches_the_secret_signing_capital_reader_tokens() -> None:
    signer = _isolated_signer_env()
    env = harness._isolated_dev_principal_env(signer)

    secret = signer["PANTHEON_BFF_JWT_SECRET"]
    assert env["CAPITAL_JWT_SECRET"] == env["PANTHEON_CAPITAL_JWT_SECRET"] == secret
    for variable in ("RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN", "DEPLOYMENT_CAPITAL_SERVICE_TOKEN"):
        claims = _decoded_claims(env[variable], env["CAPITAL_JWT_SECRET"])
        assert claims["roles"] == ["capital-reader"]


def test_handoff_credentials_are_per_run_and_tenant_aligned() -> None:
    env = {**_isolated_signer_env(), **harness._isolated_dev_principal_env(_isolated_signer_env())}
    first = harness._isolated_handoff_env(env)
    second = harness._isolated_handoff_env(env)

    assert first["AGORA_HANDOFF_SERVICE_TOKEN"] != second["AGORA_HANDOFF_SERVICE_TOKEN"]
    assert not first["POLICY_LEARNING_SERVICE_TOKEN"].startswith("pantheon-local-")
    assert {
        first["POLICY_LEARNING_AGORA_TENANT_ID"],
        first["POLICY_LEARNING_SERVICE_TENANTS"],
        first["AGORA_HANDOFF_SERVICE_TENANTS"],
    } == {env["PANTHEON_BFF_TENANT_ID"]}


def test_suites_read_the_compose_file_list_the_harness_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from pathlib import Path

    l12_dir = str(Path(__file__).resolve().parents[1] / "tests" / "integration" / "l12")
    monkeypatch.syspath_prepend(l12_dir)
    sys.modules.pop("l12_owner_auth", None)
    import l12_owner_auth

    monkeypatch.delenv("PANTHEON_L12_COMPOSE_FILE", raising=False)
    monkeypatch.setenv(
        "PANTHEON_L12_COMPOSE_FILES", os.pathsep.join(["/x/base.yml", "/x/override.yml"])
    )
    assert l12_owner_auth.compose_file_args() == ["-f", "/x/base.yml", "-f", "/x/override.yml"]
    monkeypatch.delenv("PANTHEON_L12_COMPOSE_FILES")
    assert l12_owner_auth.compose_file_args() == []
    for suite in ("human_learning", "research_loops"):
        source = (Path(l12_dir) / f"test_current_{suite}_deployed_e2e.py").read_text()
        assert "compose_file_args()" in source and "PANTHEON_L12_COMPOSE_FILE\"" not in source


def test_l12_suites_import_every_owner_auth_helper_they_call() -> None:
    """L12-RESEARCH-SUITE-IMPORT-20261008: a suite called compose_file_args()
    without importing it, which only surfaced as a NameError inside the
    deployed gate.  Catch that class statically in ordinary CI."""

    import ast
    from pathlib import Path

    l12_dir = Path(harness.__file__).resolve().parents[1] / "tests" / "integration" / "l12"
    helper_tree = ast.parse((l12_dir / "l12_owner_auth.py").read_text(encoding="utf-8"))
    helpers = {
        node.name
        for node in helper_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = []
    for suite in sorted(l12_dir.glob("test_*.py")):
        tree = ast.parse(suite.read_text(encoding="utf-8"))
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        missing.extend(f"{suite.name}: {name}" for name in sorted((called & helpers) - bound))
    assert not missing, f"L12 suites call l12_owner_auth helpers without importing them: {missing}"


def test_tw_official_pull_keeps_source_ingest_logs_when_tick_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """L12-HARNESS-TW-PULL-DIAGNOSTICS-20261008: gates 37738094930 and
    37736344787 kept only the scheduler summary line; the per-connector error
    lived in the source-ingest container that the restore recreated."""
    import subprocess

    calls: list[list[str]] = []

    def fake_run(cmd, env=None, **_kwargs):
        calls.append(list(cmd))
        if "run" in cmd:
            raise subprocess.TimeoutExpired(cmd, 1)
        return subprocess.CompletedProcess(cmd, 0, stdout="connector tw-official failed: HTTP 503", stderr="")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="tick timed out"):
        harness._run_tw_official_pull(
            "proj", ["a.yml"], {}, snapshot_url="http://s", reader_headers={}, diagnostics_dir=tmp_path
        )
    assert ["logs" in c for c in calls] == [False, False, True, False]
    assert "HTTP 503" in (tmp_path / "tw-official-pull-source-ingest-logs.txt").read_text(encoding="utf-8")


def test_tw_official_pull_success_does_not_collect_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = _fake_pull_run(monkeypatch, 0, {"closes": [1.0, 2.0]})

    harness._run_tw_official_pull(
        "proj", ["a.yml"], {}, snapshot_url="http://s", reader_headers={}, diagnostics_dir=tmp_path
    )

    assert not any("logs" in c for c, _ in calls)
    assert not (tmp_path / "tw-official-pull-source-ingest-logs.txt").exists()


def _training_authenticate(monkeypatch: pytest.MonkeyPatch, env: dict, token: str, *, tenant: str = "tenant-dev", service: str = "training-session-preview-worker"):
    """Run the real Training inbound authority with the Compose verifier wiring."""
    import sys
    from pathlib import Path

    service_dir = str(Path(harness.__file__).resolve().parents[1] / "services" / "training-session")
    monkeypatch.syspath_prepend(service_dir)
    import inbound_authority

    for key in ("TRAINING_SESSION_JWT_SECRET", "TRAINING_SESSION_JWT_ISSUER", "TRAINING_SESSION_JWT_AUDIENCE",
                "PANTHEON_RUNTIME_JWT_SECRET", "PANTHEON_RUNTIME_JWT_ISSUER", "PANTHEON_RUNTIME_JWT_AUDIENCE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRAINING_SESSION_AUTH_MODE", "strict")
    # docker-compose.yml: TRAINING_SESSION_JWT_SECRET defaults to PANTHEON_DEV_BFF_JWT_SECRET.
    monkeypatch.setenv("TRAINING_SESSION_JWT_SECRET", env["PANTHEON_DEV_BFF_JWT_SECRET"])
    return inbound_authority.authenticate_training_request(
        authorization=f"Bearer {token}", mfa_token=None, tenant_id=tenant, actor_service=service,
        method="GET", path="/api/training/preview-jobs", persistence_enforced=True,
    )


def _composed_isolated_env() -> dict[str, str]:
    env = {**_isolated_signer_env()}
    env.update(harness._isolated_dev_principal_env(env))
    return env


def test_preview_worker_token_authenticates_against_the_training_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _composed_isolated_env()
    authority = _training_authenticate(monkeypatch, env, env["TRAINING_SESSION_WORKER_TOKEN"])

    assert authority.actor_service == "training-session-preview-worker"
    assert authority.tenant_id == "tenant-dev"
    assert "training-service" in authority.roles
    claims = _decoded_claims(env["TRAINING_SESSION_WORKER_TOKEN"], env["PANTHEON_DEV_BFF_JWT_SECRET"])
    assert "*" not in claims["tenant_id"] and claims["roles"] == ["training-service"]
    assert claims["allowed_tenants"] == ["tenant-dev"] and claims["service"] == "training-session-preview-worker"
    # One issuance mechanism: the composer no longer carries its own worker minter.
    assert "TRAINING_WORKER_SERVICE_ID" not in vars(harness)
    assert env["TRAINING_SESSION_WORKER_TOKEN_FILE"] == "/run/pantheon-principals/TRAINING_SESSION_WORKER_TOKEN"


def test_preview_worker_token_negative_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _composed_isolated_env()
    token = env["TRAINING_SESSION_WORKER_TOKEN"]

    def rejected(token: str = token, **kw):
        import sys

        error = None
        try:
            _training_authenticate(monkeypatch, env, token, **kw)
        except Exception as exc:  # noqa: BLE001 - narrowed to the authority error below
            error = exc
        assert type(error) is sys.modules["inbound_authority"].TrainingInboundAuthorityError
        return error

    # Wrong signer: the former static Compose fixture token style, signed by another key.
    foreign = harness._mint_projector_service_jwt(
        "y" * 64, tenant_id="tenant-dev", subject="training-session-preview-worker",
        roles=("training-service",), extra_claims={"service": "training-session-preview-worker"},
    )
    assert "BAD_SIGNATURE" in str(rejected(token=foreign).code).upper()
    # Authority is tenant- and service-bound, never wildcard.
    assert rejected(tenant="tenant-other").status_code == 403
    assert rejected(service="control-plane-bff").status_code == 403
    # Wrong role is refused even with the correct signer.
    unauthorized = harness._mint_projector_service_jwt(
        env["PANTHEON_DEV_BFF_JWT_SECRET"], tenant_id="tenant-dev", subject="training-session-preview-worker",
        roles=("source_ingest_reader",), extra_claims={"service": "training-session-preview-worker"},
    )
    assert rejected(token=unauthorized).status_code in (401, 403)


def test_bff_health_telemetry_principal_comes_from_the_same_issuer_and_verifies() -> None:
    from services.runtime_auth_inbound import AuthError, validate_request_auth

    env = _composed_isolated_env()
    token = env["PANTHEON_BFF_HEALTH_TELEMETRY_JWT"]
    verifier = {"PANTHEON_RUNTIME_AUTH_MODE": "strict",
                "PANTHEON_RUNTIME_JWT_SECRET": env["PANTHEON_DEV_BFF_JWT_SECRET"]}
    context = validate_request_auth(authorization=f"Bearer {token}", required_roles=("service",), env=verifier)
    assert context.claims["allowed_tenants"] == ["tenant-dev"]
    assert context.claims["allowed_producers"] == ["control-plane-bff"]
    assert context.actor_id == "control-plane-bff-health-monitor"
    assert env["PANTHEON_BFF_HEALTH_TELEMETRY_JWT_FILE"] == "/run/pantheon-principals/PANTHEON_BFF_HEALTH_TELEMETRY_JWT"
    # No second minter: the composer defines no health-telemetry token of its own.
    assert not [name for name in vars(harness) if "HEALTH_TELEMETRY" in name.upper()]
    with pytest.raises(AuthError):
        validate_request_auth(authorization=f"Bearer {token}", required_roles=("service",),
                              env={**verifier, "PANTHEON_RUNTIME_JWT_SECRET": "z" * 64})


def test_research_suite_training_token_is_the_issued_worker_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _composed_isolated_env()
    env.update(harness._isolated_handoff_env(env))  # same composition order as the harness run
    suite_env = harness._suite_service_token_env(env)

    token = suite_env["PANTHEON_L12_TRAINING_TOKEN"]
    assert token == env["TRAINING_SESSION_WORKER_TOKEN"]
    authority = _training_authenticate(monkeypatch, env, token)
    assert authority.actor_service == "training-session-preview-worker"
    assert authority.tenant_id == "tenant-dev"


def test_former_published_training_fixture_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    env = _composed_isolated_env()
    # The fixture the research suite used to fall back to (gate run 37769132664).
    published_fixture = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJhbGxvd2VkX3RlbmFudHMiOlsiKiJdLCJyb2xlcyI6WyJ0cmFpbmluZy1zZXJ2aWNlIl0s"
        "InNlcnZpY2UiOiJ0cmFpbmluZy1zZXNzaW9uLXByZXZpZXctd29ya2VyIiwic3ViIjoidHJh"
        "aW5pbmctc2Vzc2lvbi1wcmV2aWV3LXdvcmtlciJ9."
        "eb4LoU20NsZEfH8VYjhl1xyOaa37bzzg7yC-D87Uu2g"
    )
    with pytest.raises(Exception) as raised:
        _training_authenticate(monkeypatch, env, published_fixture)
    assert type(raised.value) is sys.modules["inbound_authority"].TrainingInboundAuthorityError
    assert "BAD_SIGNATURE" in str(raised.value.code).upper()


def test_required_compose_services_include_loop_10_scheduler() -> None:
    # The scheduler is the single Loop 10 controller writer; the isolated
    # stack must start it or telemetry_reconciliation has no controller truth.
    assert "reconciliation-drift-scheduler" in harness.REQUIRED_COMPOSE_SERVICES
