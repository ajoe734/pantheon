from __future__ import annotations

import io
import re
import subprocess
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from scripts import cross_repo_release_controller as ctl

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "compensate_cross_repo_release.sh").read_text(encoding="utf-8")


def _http_error(code: int, headers: dict[str, str], body: str = "") -> urllib.error.HTTPError:
    msg = Message()
    for k, v in headers.items():
        msg[k] = v
    return urllib.error.HTTPError("https://x", code, "err", msg, io.BytesIO(body.encode()))


class _Resp:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, outcomes):
    calls = []

    def fake(req, timeout=0):
        calls.append(req)
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Resp(out)

    monkeypatch.setattr(ctl.urllib.request, "urlopen", fake)
    return calls


def test_fe_run_lookup_uses_bounded_rate_limit_wait(monkeypatch) -> None:
    sleeps: list[float] = []
    calls = _patch_urlopen(
        monkeypatch,
        [_http_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1100"}), b'{"id": 7}'],
    )
    client = ctl.GitHubClient(
        api_url="https://api.test", token="t", repository="o/r",
        sleep=sleeps.append, clock=lambda: 1000.0, max_rate_limit_wait_seconds=3900,
    )
    assert client.get_run(7) == {"id": 7}
    assert len(calls) == 2 and sleeps == [101.0]
    # the script wires the bounded budget into the FE lookup
    assert "max_rate_limit_wait_seconds=float(" in SCRIPT
    assert "PANTHEON_RELEASE_MAX_RATE_LIMIT_WAIT_SECONDS" in SCRIPT


def test_cleanup_verify_and_release_use_rate_limit_wait() -> None:
    cleanup = SCRIPT.split("cleanup() {", 1)[1].split("trap cleanup", 1)[0]
    assert '--rate-limit-wait-seconds "${rate_limit_wait_max}"' in cleanup
    assert "lease_cmd_with_rate_limit_wait python3" in cleanup
    assert "rate_limit_wait_max <= 3900" in SCRIPT


def _run_helper(tmp_path: Path, fake_body: str, budget: str) -> subprocess.CompletedProcess[str]:
    helper = re.search(r"lease_cmd_with_rate_limit_wait\(\) \{.*?\n\}\n", SCRIPT, re.S).group(0)
    fake = tmp_path / "fake.sh"
    fake.write_text(f"#!/usr/bin/env bash\n{fake_body}\n")
    fake.chmod(0o755)
    driver = (
        f"set -uo pipefail\nRUNNER_TEMP={tmp_path}\nrate_limit_wait_max={budget}\n"
        f"PANTHEON_RELEASE_RATE_LIMIT_POLL_SECONDS=1\n{helper}\n"
        f"lease_cmd_with_rate_limit_wait {fake}\necho rc=$?\n"
    )
    return subprocess.run(["bash", "-c", driver], capture_output=True, text=True, timeout=60)


def test_release_helper_waits_out_rate_limit_then_succeeds(tmp_path: Path) -> None:
    counter = tmp_path / "n"
    body = (
        f'n=$(cat {counter} 2>/dev/null || echo 0); echo $((n+1)) > {counter}\n'
        'if [ "$n" -lt 1 ]; then echo "GitHub API rate limit exceeded" >&2; exit 78; fi\nexit 0'
    )
    out = _run_helper(tmp_path, body, "10")
    assert "rc=0" in out.stdout and counter.read_text().strip() == "2"


def test_release_helper_non_rate_limit_failure_fails_immediately(tmp_path: Path) -> None:
    counter = tmp_path / "n"
    body = f'echo x >> {counter}; echo "lease lost" >&2; exit 75'
    out = _run_helper(tmp_path, body, "10")
    assert "rc=75" in out.stdout and len(counter.read_text().split()) == 1


def test_release_helper_budget_exhausted_fails_closed(tmp_path: Path) -> None:
    out = _run_helper(tmp_path, 'echo "secondary rate limit" >&2; exit 78', "2")
    assert "rc=78" in out.stdout
