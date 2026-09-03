from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"


FAKE_DOCKER = r'''#!/usr/bin/env python3
import io
import json
import os
import sys
import urllib.error
import urllib.request

args = sys.argv[1:]
if "ps" in args and "--format" in args:
    print(json.dumps({"Service": args[-1] if args[-1] != "json" else "all", "Health": "healthy"}))
    raise SystemExit(0)
if "exec" not in args or "telemetry" not in args or "python" not in args:
    raise SystemExit(0)

for index, arg in enumerate(args):
    if arg == "-e":
        key, value = args[index + 1].split("=", 1)
        os.environ[key] = value

class Response:
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        return False
    def read(self):
        return b'{"replayed": 3}'

def urlopen(request, timeout):
    record = {
        "authorization": request.get_header("Authorization"),
        "tenant": request.get_header("X-tenant-id"),
        "timeout": timeout,
    }
    Path = __import__("pathlib").Path
    Path(os.environ["FAKE_REPLAY_RECORD"]).write_text(json.dumps(record))
    if os.environ.get("FAKE_REPLAY_STATUS") == "rejected":
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"error":"denied"}'))
    return Response()

urllib.request.urlopen = urlopen
exec(compile(sys.stdin.read(), "<bootstrap-replay>", "exec"))
'''


def _run(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    run_env = os.environ.copy()
    run_env.update(
        {
            "PATH": f"{bin_dir}:{run_env['PATH']}",
            "FAKE_REPLAY_RECORD": str(tmp_path / "replay.json"),
        }
    )
    run_env.pop("PANTHEON_TELEMETRY_OPERATOR_TOKEN", None)
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(BOOTSTRAP), "--skip-migration", *args],
        cwd=ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_no_token_skips_replay_and_reaches_final_status(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "telemetry DLQ replay skipped" in result.stderr
    assert "==> [5/5] Final service status:" in result.stdout
    assert not (tmp_path / "replay.json").exists()


def test_explicit_skip_does_not_attempt_replay(tmp_path: Path) -> None:
    result = _run(tmp_path, "--skip-telemetry-replay")

    assert result.returncode == 0
    assert "--skip-telemetry-replay flag set" in result.stdout
    assert "==> [5/5] Final service status:" in result.stdout
    assert not (tmp_path / "replay.json").exists()


def test_operator_token_and_first_service_tenant_are_forwarded(tmp_path: Path) -> None:
    secret = "operator-secret-value"
    result = _run(
        tmp_path,
        env={
            "PANTHEON_TELEMETRY_OPERATOR_TOKEN": secret,
            "PANTHEON_TELEMETRY_SERVICE_TENANTS": " tenant-a,tenant-b ",
            "PANTHEON_TENANT_ID": "tenant-fallback",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "telemetry DLQ replay: replayed=3" in result.stdout
    assert "==> [5/5] Final service status:" in result.stdout
    assert secret not in result.stdout + result.stderr
    assert json.loads((tmp_path / "replay.json").read_text()) == {
        "authorization": f"Bearer {secret}",
        "tenant": "tenant-a",
        "timeout": 30,
    }


def test_tenant_fallback_and_rejected_token_fail_closed_without_leak(tmp_path: Path) -> None:
    secret = "rejected-secret-value"
    result = _run(
        tmp_path,
        env={
            "PANTHEON_TELEMETRY_OPERATOR_TOKEN": secret,
            "PANTHEON_TELEMETRY_SERVICE_TENANTS": "",
            "PANTHEON_TENANT_ID": "tenant-fallback",
            "FAKE_REPLAY_STATUS": "rejected",
        },
    )

    assert result.returncode != 0
    assert "telemetry DLQ replay failed: HTTP 403" in result.stderr
    assert "==> [5/5] Final service status:" not in result.stdout
    assert secret not in result.stdout + result.stderr
    assert json.loads((tmp_path / "replay.json").read_text())["tenant"] == "tenant-fallback"


def test_default_tenant_is_used_when_tenant_inputs_are_empty(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        env={
            "PANTHEON_TELEMETRY_OPERATOR_TOKEN": "operator-secret-value",
            "PANTHEON_TELEMETRY_SERVICE_TENANTS": "",
            "PANTHEON_TENANT_ID": "",
        },
    )

    assert result.returncode == 0
    assert json.loads((tmp_path / "replay.json").read_text())["tenant"] == "default"
