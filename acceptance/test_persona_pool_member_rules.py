"""Release probe acceptance/probes/persona-pool-member-rules against recorded fixtures."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "persona-pool-member-rules"
FIXTURE = json.loads((ROOT / "fixtures" / "hosted_persona_pool_20261119.json").read_text())
PID = FIXTURE["persona_list"]["data"][0]["id"]

_loader = SourceFileLoader("persona_pool_member_rules_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)


def run_case(case, calls=None):
    def fetch(path):
        if calls is not None:
            calls.append(path)
        if path == "/bff/personas":
            return FIXTURE["persona_list"]
        assert path == f"/bff/personas/{PID}", path  # retired persona is never read
        return FIXTURE["details"][case]
    return probe.run(fetch)


def test_probe_is_executable_read_only_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_pass_and_missing_observation_are_not_failures():
    calls = []
    assert run_case("pass", calls) == {"passed": True, "checked": 1, "failed": {}}
    assert calls == ["/bff/personas", f"/bff/personas/{PID}"]
    assert run_case("no_observation")["passed"] is True


def test_fail_member_without_ok_rule_row():
    report = run_case("fail_member_rules_missing")
    assert report["passed"] is False
    assert report["failed"][PID][0].startswith("member_rules_missing:artifact-persona-paper-")


def test_fail_false_snapshot_mismatch_when_observed_members_equal_current():
    report = run_case("fail_false_mismatch")
    assert report["failed"][PID] == ["pool_health_false_mismatch:observed_v2"]


def test_real_mismatch_is_not_a_probe_failure():
    detail = json.loads(json.dumps(FIXTURE["details"]["fail_false_mismatch"]))
    detail["data"]["strategyPool"]["versions"][1]["members"][0]["risk_weight"] = 0.3
    assert probe.check_persona(detail) == []


def test_pool_surface_not_ok_is_not_applicable():
    detail = json.loads(json.dumps(FIXTURE["details"]["fail_member_rules_missing"]))
    detail["meta"]["surfaces"]["strategy_pool"]["status"] = "unavailable"
    assert probe.check_persona(detail) == []


def test_missing_credentials_skip_naming_variable_without_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:9"
    env["ACCEPTANCE_VIEWER_CLIENT_ID"] = "viewer"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 77
    assert "ACCEPTANCE_VIEWER_CLIENT_SECRET" in done.stdout and "ACCEPTANCE_VIEWER_CLIENT_ID" not in done.stdout
    env["ACCEPTANCE_VIEWER_CLIENT_ID"] = ""
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout


def test_unreachable_bff_fails_without_leaking_secret():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env.update(ACCEPTANCE_BFF_URL="http://127.0.0.1:9", ACCEPTANCE_VIEWER_CLIENT_ID="viewer",
               ACCEPTANCE_VIEWER_CLIENT_SECRET="s3cret-value")
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert "s3cret-value" not in done.stdout + done.stderr
