"""Restart-loop detection for the dev steady stack (two samples, read-only)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import dev_steady_state_rows as rows_mod
from scripts import fetch_dev_artifact_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]


def row(service, restarts, oom=False, state="running"):
    return {"service": service, "state": state, "image_id": "sha256:" + "a" * 64,
            "restart_count": restarts, "oom_killed": oom}


def test_rows_parse_restart_fields_and_keep_four_field_input():
    out = rows_mod.rows(["svc|False|running|sha256:x|3|true\n", "old|False|running|sha256:y\n",
                         "job|True|running|sha256:z|1|false\n"])
    assert out[0]["restart_count"] == 3 and out[0]["oom_killed"] is True
    assert "restart_count" not in out[1] and len(out) == 2


def test_service_restarting_between_samples_fails_and_is_named():
    loops = evidence.steady_service_restart_loop([row("a", 0), row("b", 2)], [row("a", 0), row("b", 3)])
    assert list(loops) == ["b"]


def test_single_startup_restart_before_first_sample_passes():
    assert evidence.steady_service_restart_loop([row("a", 1)], [row("a", 1)]) == {}


def test_oom_killed_and_restarting_state_fail():
    assert "a" in evidence.steady_service_restart_loop([row("a", 1, oom=True)], [row("a", 1)])
    assert "a" in evidence.steady_service_restart_loop([row("a", 1)], [row("a", 1, state="restarting")])


def test_2026_11_17_adapter_case_restart_every_45_seconds():
    # 90 s gap, count growing by one about every 45 s -> grew by two.
    loops = evidence.steady_service_restart_loop(
        [row("openclaw-gateway-adapter", 54), row("operator-bff", 0)],
        [row("openclaw-gateway-adapter", 56), row("operator-bff", 0)])
    assert list(loops) == ["openclaw-gateway-adapter"]


def test_exited_one_shot_and_healthcheck_status_are_not_criteria():
    exited = row("migrate", 0, state="exited")
    healthy_flag = dict(row("src", 0), health="unhealthy")
    assert evidence.steady_service_restart_loop([exited, healthy_flag], [exited, healthy_flag]) == {}


def test_missing_restart_fields_are_rejected():
    with pytest.raises(evidence.CaptureError):
        evidence.steady_service_restart_loop([{"service": "a", "state": "running"}], [row("a", 0)])


def _cli(tmp_path, first, second, gap):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(first)); b.write_text(json.dumps(second))
    return subprocess.run([sys.executable, str(ROOT / "scripts/fetch_dev_artifact_evidence.py"), "--mode", "verify-restart-loop",
                           "--steady-state-file", str(a), "--steady-state-second-file", str(b),
                           "--sample-gap-seconds", str(gap)], capture_output=True, text=True, cwd=ROOT)


def test_cli_exit_codes_and_reason(tmp_path):
    bad = _cli(tmp_path, [row("a", 0)], [row("a", 1)], 91)
    assert bad.returncode == 76 and "steady_service_restart_loop: a" in bad.stderr
    assert _cli(tmp_path, [row("a", 1)], [row("a", 1)], 91).returncode == 0
    assert _cli(tmp_path, [row("a", 0)], [row("a", 0)], 30).returncode != 0  # gap below 90 s refused
