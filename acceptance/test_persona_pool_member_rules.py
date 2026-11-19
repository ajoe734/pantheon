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
LIST = json.loads((ROOT / "fixtures" / "hosted_persona_list_20261119.json").read_text())["pages"]
PID = FIXTURE["persona_list"]["data"][0]["id"]

_loader = SourceFileLoader("persona_pool_member_rules_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)


def run_case(case, calls=None):
    def fetch(path):
        if calls is not None:
            calls.append(path)
        if path.startswith("/bff/personas?"):
            return FIXTURE["persona_list"]
        assert path == f"/bff/personas/{PID}", path  # retired persona is never read
        return FIXTURE["details"][case]
    return probe.run(fetch)


def test_probe_is_executable_read_only_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_pass_and_missing_observation_are_not_failures():
    calls = []
    report = run_case("pass", calls)
    assert report["passed"] is True and report["checked"] == 1 and report["failed"] == {}
    assert report["results"] == {PID: "ok"} and report["read"] == 2 and report["skipped"] == 1
    assert calls == ["/bff/personas?page_size=200", f"/bff/personas/{PID}"]
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


def _paged_fetch(calls, detail):
    def fetch(path):
        calls.append(path)
        if path == "/bff/personas?page_size=200":
            return LIST["page1"]
        if path == "/bff/personas?page_size=200&page_token=tok-2":
            return LIST["page2"]
        pid = path.rsplit("/", 1)[1]
        assert pid in ("persona-2448c7053e33ea48da47", "persona-nopool"), path
        if pid == "persona-nopool":
            return {"meta": {"surfaces": {"strategy_pool": {"status": "unavailable"}}}, "data": {}}
        return detail
    return fetch


def test_first_page_all_retired_then_live_page_is_checked():
    calls = []
    report = probe.run(_paged_fetch(calls, FIXTURE["details"]["pass"]))
    assert report["passed"] is True and report["checked"] == 1
    assert report["results"] == {"persona-2448c7053e33ea48da47": "ok"}
    assert report["read"] == 6 and report["skipped"] == 5 and report["skipped_no_pool"] == 1
    assert calls[:2] == ["/bff/personas?page_size=200", "/bff/personas?page_size=200&page_token=tok-2"]
    assert not any("retired" in c or "archived" in c for c in calls)


def test_paged_failure_is_reported_per_persona():
    report = probe.run(_paged_fetch([], FIXTURE["details"]["fail_member_rules_missing"]))
    assert report["passed"] is False and report["results"] == {"persona-2448c7053e33ea48da47": "failed"}


def test_zero_checked_personas_fails_with_reason():
    only_retired = {"data": LIST["page1"]["data"]}
    report = probe.run(lambda path: only_retired)
    assert report["passed"] is False and report["checked"] == 0 and report["reason"] == "no_persona_checked"
    assert probe.run(lambda path: {"data": []})["reason"] == "no_persona_checked"
    no_pool = {"meta": {"surfaces": {"strategy_pool": {"status": "unavailable"}}}, "data": {}}
    live = {"data": [{"id": "p1", "lifecycle_state": "paper_running"}]}
    assert probe.run(lambda path: live if path.startswith("/bff/personas?") else no_pool)["passed"] is False
