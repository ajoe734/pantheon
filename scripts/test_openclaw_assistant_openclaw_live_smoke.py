"""Focused behavioural contract tests for the OpenClaw live-smoke script."""

from __future__ import annotations

import os
import subprocess
import textwrap
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "openclaw-assistant-openclaw-live-smoke.sh"


def _fake_curl_script(readiness_steps: list[str]) -> str:
    serialized_steps = "\n".join(readiness_steps)
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        state_file="${{FAKE_CURL_STATE_FILE:?}}"
        steps_file="${{FAKE_CURL_STEPS_FILE:?}}"
        calls_file="${{FAKE_CURL_CALLS_FILE:?}}"
        url=""
        for arg in "$@"; do
          case "$arg" in
            http://*|https://*) url="$arg" ;;
          esac
        done
        if [[ -n "${{FAKE_CURL_ARGS_FILE:-}}" ]]; then
          printf '%s\\n' "$*" >> "$FAKE_CURL_ARGS_FILE"
        fi
        printf '%s\\n' "$url" >> "$calls_file"
        if [[ "$url" == *"/readiness/openclaw?auth_probe=true" ]]; then
          count=0
          if [[ -f "$state_file" ]]; then count=$(cat "$state_file"); fi
          count=$((count + 1))
          printf '%s' "$count" > "$state_file"
          step=$(sed -n "${{count}}p" "$steps_file")
          case "$step" in
            timeout) exit 28 ;;
            refused) exit 7 ;;
            retry503) printf '%s\\n503\\n' '{{"reason":"warming token=should-not-appear"}}' ;;
            ready) printf '%s\\n200\\n' '{{"ready":true}}' ;;
            notready) printf '%s\\n200\\n' '{{"ready":false,"reason":"OPENCLAW_TOKEN_NOT_CONFIGURED"}}' ;;
            *) echo "unexpected readiness step: $step" >&2; exit 88 ;;
          esac
          exit 0
        fi
        if [[ "$url" == *"/invoke/stream" ]]; then
          printf '%s\\n%s\\n' 'data: {{"type":"done","text":"OPENCLAW_LIVE","transport":"responses_http"}}' 'data: [DONE]'
          exit 0
        fi
        if [[ "$url" == *"/invoke" ]]; then
          printf '%s\\n200\\n' '{{"data":{{"status":"completed","output":{{"json_events":[{{"item":{{"type":"agent_message","text":"OPENCLAW_LIVE"}}}}],"transport":"cli"}}}}}}'
          exit 0
        fi
        echo "unexpected URL: $url" >&2
        exit 89
        """
    )


def _run_smoke(
    tmp_path: Path,
    steps: list[str],
    *,
    budget: int = 3,
    service_token: str | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(_fake_curl_script(steps), encoding="utf-8")
    fake_curl.chmod(0o755)
    state_file = tmp_path / "readiness-count"
    steps_file = tmp_path / "readiness-steps"
    calls_file = tmp_path / "curl-calls"
    args_file = tmp_path / "curl-args"
    steps_file.write_text("\n".join(steps) + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_CURL_STATE_FILE": str(state_file),
        "FAKE_CURL_STEPS_FILE": str(steps_file),
        "FAKE_CURL_CALLS_FILE": str(calls_file),
        "FAKE_CURL_ARGS_FILE": str(args_file),
        "OPENCLAW_READINESS_TOTAL_BUDGET_SECONDS": str(budget),
        "OPENCLAW_READINESS_ATTEMPT_TIMEOUT_SECONDS": "1",
        "OPENCLAW_READINESS_RETRY_DELAY_SECONDS": "1",
    }
    if service_token is not None:
        env["PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN"] = service_token
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    result.readiness_attempts = int(state_file.read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    result.calls = calls_file.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    result.curl_args = args_file.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    return result


def test_readiness_retries_http_503_then_stops_at_ready(tmp_path: Path) -> None:
    result = _run_smoke(tmp_path, ["retry503", "ready"])

    assert result.returncode == 0, result.stderr
    assert result.readiness_attempts == 2  # type: ignore[attr-defined]
    assert "HTTP_503_SHA256_" in result.stderr
    assert "should-not-appear" not in result.stderr
    assert "openclaw provider live smoke PASSED" in result.stdout


def test_readiness_retries_per_request_timeout_then_stops_at_ready(tmp_path: Path) -> None:
    result = _run_smoke(tmp_path, ["timeout", "ready"])

    assert result.returncode == 0, result.stderr
    assert result.readiness_attempts == 2  # type: ignore[attr-defined]
    assert "CURL_REQUEST_TIMEOUT" in result.stderr


def test_readiness_retries_connection_refusal_then_stops_at_ready(tmp_path: Path) -> None:
    result = _run_smoke(tmp_path, ["refused", "ready"])

    assert result.returncode == 0, result.stderr
    assert result.readiness_attempts == 2  # type: ignore[attr-defined]
    assert "CURL_CONNECTION_REFUSED" in result.stderr


def test_never_ready_http_503_fails_within_the_total_budget(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run_smoke(tmp_path, ["retry503", "retry503", "retry503", "retry503"], budget=2)
    elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert result.readiness_attempts <= 2  # type: ignore[attr-defined]
    assert elapsed < 3.5
    assert "did not converge within 2s" in result.stderr


def test_ready_false_is_not_retried(tmp_path: Path) -> None:
    result = _run_smoke(tmp_path, ["notready", "ready"])

    assert result.returncode != 0
    assert result.readiness_attempts == 1  # type: ignore[attr-defined]
    assert "READY_FALSE_OPENCLAW_TOKEN_NOT_CONFIGURED" in result.stderr


def test_live_turns_remain_single_attempts(tmp_path: Path) -> None:
    result = _run_smoke(tmp_path, ["ready"])

    assert result.returncode == 0, result.stderr
    assert sum("/readiness/openclaw" in call for call in result.calls) == 1  # type: ignore[attr-defined]
    assert sum(call.endswith("/invoke") for call in result.calls) == 1  # type: ignore[attr-defined]
    assert sum(call.endswith("/invoke/stream") for call in result.calls) == 1  # type: ignore[attr-defined]


def test_service_token_is_forwarded_to_every_adapter_request(tmp_path: Path) -> None:
    result = _run_smoke(tmp_path, ["ready"], service_token="strict-service-token")

    assert result.returncode == 0, result.stderr
    captured_args = "\n".join(result.curl_args)  # type: ignore[attr-defined]
    assert captured_args.count("X-Pantheon-Service-Token: strict-service-token") == 3
