from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_WRAPPER = ROOT / "scripts" / "run_loop_prod_tel_002_hosted_probe.sh"


def test_probe_wrapper_rejects_missing_required_args():
    res = subprocess.run(
        ["bash", str(PROBE_WRAPPER)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 64
    assert "Usage:" in res.stderr


def test_probe_wrapper_rejects_unsupported_mode():
    res = subprocess.run(
        [
            "bash",
            str(PROBE_WRAPPER),
            "--expected-sha", "sha123",
            "--container-output", "/tmp/c.json",
            "--remote-output", "/tmp/r.json",
            "--mode", "unsupported-mode",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 64


def test_probe_wrapper_rejects_natural_mode_without_case_key():
    res = subprocess.run(
        [
            "bash",
            str(PROBE_WRAPPER),
            "--expected-sha", "sha123",
            "--container-output", "/tmp/c.json",
            "--remote-output", "/tmp/r.json",
            "--mode", "natural",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 64


@pytest.fixture
def mock_docker(tmp_path: Path):
    log_file = tmp_path / "docker_calls.log"
    mock_bin_dir = tmp_path / "bin"
    mock_bin_dir.mkdir(parents=True, exist_ok=True)
    docker_script = mock_bin_dir / "docker"
    
    script_content = f"""#!/usr/bin/env bash
echo "$@" >> "{log_file}"
if [[ "$*" == *"compose"*"--print-high-watermark"* ]]; then
  if [[ "${{MOCK_BASELINE_FAIL:-0}}" == "1" ]]; then
    exit 1
  fi
  echo "123"
  exit 0
fi
if [[ "$*" == *"hosted_lifecycle_stimulus"* ]]; then
  if [[ "${{MOCK_STIMULUS_FAIL:-0}}" == "1" ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$*" == *"ps -q"* ]]; then
  echo "mock_container_id"
  exit 0
fi
if [[ "$1" == "cp" ]]; then
  touch "$3"
  exit 0
fi
exit 0
"""
    docker_script.write_text(script_content)
    docker_script.chmod(0o755)
    
    env = os.environ.copy()
    env["PATH"] = f"{mock_bin_dir}:{env.get('PATH', '')}"
    return env, log_file


def test_probe_wrapper_natural_mode_skips_stimulus(tmp_path: Path, mock_docker):
    env, log_file = mock_docker
    remote_out = tmp_path / "out.json"
    
    res = subprocess.run(
        [
            "bash",
            str(PROBE_WRAPPER),
            "--expected-sha", "sha12345",
            "--container-output", "/tmp/c.json",
            "--remote-output", str(remote_out),
            "--mode", "natural",
            "--case-key", "dev-paper-release-42-1",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    calls = log_file.read_text().splitlines()
    assert not any("hosted_lifecycle_stimulus" in call for call in calls), "Stimulus must not run in natural mode"
    probe_call = [call for call in calls if "hosted_lifecycle_probe" in call and "--baseline-high-watermark" in call]
    assert len(probe_call) == 1
    assert "--mode natural" in probe_call[0]
    assert "--case-key dev-paper-release-42-1" in probe_call[0]
    assert "-e PANTHEON_SOURCE_INGEST_URL=" in probe_call[0]


def test_probe_wrapper_controlled_stimulus_runs_stimulus(tmp_path: Path, mock_docker):
    env, log_file = mock_docker
    remote_out = tmp_path / "out.json"
    
    res = subprocess.run(
        [
            "bash",
            str(PROBE_WRAPPER),
            "--expected-sha", "sha12345",
            "--container-output", "/tmp/c.json",
            "--remote-output", str(remote_out),
            "--mode", "controlled-stimulus",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    calls = log_file.read_text().splitlines()
    assert any("hosted_lifecycle_stimulus" in call for call in calls), "Stimulus must run in controlled-stimulus mode"
    probe_call = [call for call in calls if "hosted_lifecycle_probe" in call and "--baseline-high-watermark" in call]
    assert len(probe_call) == 1
    assert "--mode controlled-stimulus" in probe_call[0]


def test_probe_wrapper_baseline_failure(tmp_path: Path, mock_docker):
    env, log_file = mock_docker
    env["MOCK_BASELINE_FAIL"] = "1"
    remote_out = tmp_path / "out.json"
    
    res = subprocess.run(
        [
            "bash",
            str(PROBE_WRAPPER),
            "--expected-sha", "sha12345",
            "--container-output", "/tmp/c.json",
            "--remote-output", str(remote_out),
            "--mode", "natural",
            "--case-key", "dev-paper-release-42-1",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    calls = log_file.read_text().splitlines()
    assert any("baseline_high_watermark_failed" in call for call in calls)


def test_probe_wrapper_stimulus_failure(tmp_path: Path, mock_docker):
    env, log_file = mock_docker
    env["MOCK_STIMULUS_FAIL"] = "1"
    remote_out = tmp_path / "out.json"
    
    res = subprocess.run(
        [
            "bash",
            str(PROBE_WRAPPER),
            "--expected-sha", "sha12345",
            "--container-output", "/tmp/c.json",
            "--remote-output", str(remote_out),
            "--mode", "controlled-stimulus",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    calls = log_file.read_text().splitlines()
    assert any("hosted_stimulus_failed" in call for call in calls)


def test_nonprod_workflow_wires_natural_mode_and_case_key():
    wf_text = (ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(encoding="utf-8")
    assert "--mode natural" in wf_text
    assert "--case-key" in wf_text


def _probe_args(remote_out: Path, *extra: str) -> list[str]:
    return [
        "bash", str(PROBE_WRAPPER),
        "--expected-sha", "sha12345",
        "--container-output", "/tmp/c.json",
        "--remote-output", str(remote_out),
        "--mode", "natural",
        "--case-key", "dev-paper-release-42-1",
        *extra,
    ]


def test_probe_wrapper_forwards_explicit_baseline_without_recapture(tmp_path: Path, mock_docker):
    env, log_file = mock_docker
    res = subprocess.run(
        _probe_args(tmp_path / "out.json", "--baseline-high-watermark", "5264951"),
        env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0
    calls = log_file.read_text().splitlines()
    assert not any("--print-high-watermark" in call for call in calls)
    probe_call = [call for call in calls if "--baseline-high-watermark" in call]
    assert len(probe_call) == 1
    assert "--baseline-high-watermark 5264951 " in probe_call[0]


def test_probe_wrapper_default_standalone_still_captures_baseline(tmp_path: Path, mock_docker):
    env, log_file = mock_docker
    res = subprocess.run(_probe_args(tmp_path / "out.json"), env=env, capture_output=True, text=True)
    assert res.returncode == 0
    calls = log_file.read_text().splitlines()
    assert sum("--print-high-watermark" in call for call in calls) == 1
    assert any("--baseline-high-watermark 123 " in call for call in calls)


@pytest.mark.parametrize("bad", ["", "-1", "12x", "0x10", "1.5"])
def test_probe_wrapper_rejects_malformed_explicit_baseline_before_probe(tmp_path: Path, mock_docker, bad: str):
    env, log_file = mock_docker
    res = subprocess.run(
        _probe_args(tmp_path / "out.json", "--baseline-high-watermark", bad),
        env=env, capture_output=True, text=True,
    )
    assert res.returncode == 64
    calls = log_file.read_text().splitlines()
    assert any("baseline_high_watermark_invalid" in call for call in calls)
    assert not any("--print-high-watermark" in call for call in calls)
    assert not any("hosted_lifecycle_probe" in call and "--baseline-high-watermark" in call for call in calls)


def test_prebootstrap_baseline_keeps_first_seven_events_that_late_baseline_drops(tmp_path: Path):
    """Native matcher: baseline captured before bootstrap sees the full chain."""
    import asyncio
    import sys

    sys.path.insert(0, str(ROOT))
    from services.trade_journey import hosted_lifecycle_probe as probe
    from services.trade_journey import test_hosted_lifecycle_probe as native

    rows = native._natural_lifecycle_rows()
    required = len(probe.REQUIRED_EVENT_TYPES)
    # First seven events ingest during bootstrap, the rest after probe startup.
    prefix_high = rows[required - 2]["ingested_seq"]
    root, published = native._publish(tmp_path, rows=rows, source_high_watermark=rows[-1]["ingested_seq"])

    class FilteringSource(native.IncrementalSource):
        async def snapshot_after(self, baseline: int):
            return self.high, [r for r in self.rows if r["ingested_seq"] > baseline]

    def run(baseline: int):
        return asyncio.run(
            probe.execute(
                source=FilteringSource(baseline=baseline, high=rows[-1]["ingested_seq"], rows=published),
                projection_root=root,
                case_source=native.DEFAULT_CASE_SOURCE,
                expected_sha="deployed-sha",
                output=tmp_path / f"baseline-{baseline}.json",
                timeout_seconds=0.1,
                poll_seconds=0.001,
                baseline_high_watermark=baseline,
                case_key=native.DEFAULT_TEST_CASE_KEY,
            )
        )

    code, artifact = run(0)
    assert code == 0 and artifact["outcome"] == "passed"
    late_code, late_artifact = run(prefix_high)
    assert late_code != 0 and late_artifact["outcome"] != "passed"
