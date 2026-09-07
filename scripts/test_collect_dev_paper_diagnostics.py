import json
import sys
from pathlib import Path

import pytest
import yaml

from scripts import collect_dev_paper_diagnostics as diag


def test_exception_identifiers_and_frames_survive_without_values():
    raw = '''2026-09-07T00:17:26.123Z Traceback (most recent call last):
2026-09-07T00:17:26.123Z   File "/workspace/services/control-plane/bff/personas/service.py", line 3999, in _coordinate_persona_create
2026-09-07T00:17:26.123Z     call(password="SUPERSECRET")
2026-09-07T00:17:26.123Z NameError: name 'missing_helper' is not defined
2026-09-07T00:17:26.123Z urllib.error.HTTPError: HTTP Error 403: SECRET_RESPONSE
psycopg.errors.UndefinedTable: relation "persona.ledger" does not exist
TypeError: Store.release() got an unexpected keyword argument 'lease_seconds'
ValueError: Authorization: Bearer PRIVATE_JWT
RuntimeError: postgresql://user:DB_PASSWORD@host/db
password=OTHER_SECRET
{"token":"JSON_SECRET","body":{"secret":"NESTED_SECRET"}}
-----BEGIN PRIVATE KEY-----
PRIVATE_KEY_BYTES
-----END PRIVATE KEY-----
'''
    events = diag.log_events(raw)
    assert events[0]["file"].endswith("personas/service.py")
    assert events[1]["identifier"] == "missing_helper"
    assert events[2]["http_status"] == 403
    assert events[3]["identifier"] == "persona.ledger"
    assert events[4]["identifier"] == "lease_seconds"
    encoded = json.dumps(events)
    for secret in ("SUPERSECRET", "SECRET_RESPONSE", "PRIVATE_JWT", "DB_PASSWORD", "OTHER_SECRET", "JSON_SECRET", "NESTED_SECRET", "PRIVATE_KEY_BYTES"):
        assert secret not in encoded
    assert all("message" not in event for event in events)


def test_project_exception_allowlist_admits_named_types_only():
    raw = (
        "PersonaWriteOwnerUnavailable: persona owner call failed with SECRET_TOKEN\n"
        "ProvisioningLeaseLost: lease revoked mid PRIVATE_DETAIL\n"
        "SomeUnknownFailure: raw request body SHOULD_NOT_LEAK\n"
    )
    events = diag.log_events(raw)
    assert [event["type"] for event in events] == [
        "PersonaWriteOwnerUnavailable",
        "ProvisioningLeaseLost",
    ]
    encoded = json.dumps(events)
    for secret in ("SECRET_TOKEN", "PRIVATE_DETAIL", "SomeUnknownFailure", "SHOULD_NOT_LEAK"):
        assert secret not in encoded


def test_commands_are_bounded_in_bytes_and_time(monkeypatch):
    monkeypatch.setattr(diag, "MAX_BYTES", 1024)
    raw, status = diag.command([sys.executable, "-c", "print('x' * 4096)"])
    assert len(raw) == 1024 and status == "truncated"
    monkeypatch.setattr(diag, "COMMAND_SECONDS", 0.1)
    raw, status = diag.command([sys.executable, "-c", "import time; time.sleep(10)"])
    assert status == "timeout"


def test_fixed_services_continue_after_failure_and_bind_observed_identity(monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        if args[1] == "ps":
            if args[-1].endswith("=capital"):
                return "UNTRUSTED_ERROR", "command_failed"
            return "a" * 64, "ok"
        if args[1] == "inspect":
            assert ".Config.Env" not in args[3] and ".State.Error" not in args[3]
            return json.dumps({"source_sha": "b" * 40, "status": "running", "exit_code": 0,
                               "restart_count": 0, "oom_killed": False, "image_id": "sha256:" + "c" * 64}), "ok"
        assert args[1:5] == ["logs", "--timestamps", "--since=15m", "--tail=240"]
        return "NameError: name 'missing_helper' is not defined", "ok"
    monkeypatch.setattr(diag, "command", run)
    result = diag.collect("b" * 40, run_id="34081262894", attempt="1", phase="paper_bootstrap",
                          expected_fe_sha="e" * 40, bootstrap_exit="1")
    assert "persona" in diag.SERVICES
    assert result["identity_matches"] is True
    assert result["run_id"] == "34081262894"
    assert result["bootstrap_exit"] == "1"
    assert result["container_id"] == "a" * 64
    assert result["observed_source_sha"] == "b" * 40
    assert result["services"]["persona"]["collection_status"] == "ok"
    assert result["services"]["capital"]["collection_status"] == "command_failed"
    assert result["services"]["postgres"]["events"][0]["type"] == "NameError"
    assert result["collection_status"] == "partial"
    assert "UNTRUSTED_ERROR" not in json.dumps(result)
    assert diag.collect("d" * 40)["identity_matches"] is False
    assert all(args[0] == "docker" and args[1] in {"ps", "inspect", "logs"} for args in calls)
    with pytest.raises(ValueError):
        diag.collect("dev; unsafe")


def _paper_bootstrap_steps():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text())
    steps = next(job["steps"] for job in workflow["jobs"].values() if any(step.get("id") == "paper_bootstrap" for step in job.get("steps", [])))
    names = [step.get("id") for step in steps]
    return steps, names


def test_diagnostic_collection_happens_inside_the_guarded_baseline_child():
    steps, names = _paper_bootstrap_steps()
    baseline = steps[names.index("paper_bootstrap")]
    upload = steps[names.index("paper_bootstrap_diagnostics_upload")]

    # The failure-capture ID from the broken cross-step design must not
    # reappear: a second, separate guard invocation after the guard already
    # quarantined the heartbeat can never observe a healthy lease.
    assert "paper_bootstrap_diagnostics" not in names
    assert names.index("paper_bootstrap") < names.index("deploy_compensation")

    run = baseline["run"]
    assert "run_dev_paper_baseline_with_diagnostics.sh" in run
    assert "collect_dev_paper_diagnostics.py" in run
    assert "run_with_dev_environment_lease.sh" in run
    assert "StrictHostKeyChecking=no" not in run
    # The baseline's own exit status must reach the outer step unchanged so
    # deploy_compensation still triggers on the same condition as before.
    assert 'exit "${baseline_status}"' in run
    assert "|| true" not in run

    assert upload["with"]["retention-days"] == 7
    assert upload["continue-on-error"] is True
    assert "steps.paper_bootstrap.outputs.artifact_path" in upload["if"]
    assert upload["with"]["path"] == "${{ steps.paper_bootstrap.outputs.artifact_path }}"


def test_diagnostics_script_preserves_baseline_exit_and_never_masks_it():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "run_dev_paper_baseline_with_diagnostics.sh").read_text()
    assert "|| true" not in script
    assert "bootstrap_status=$?" in script
    assert 'exit "${bootstrap_status}"' in script
    # A missing/failed/invalid collection must never overwrite the original
    # baseline failure, and must never be forced into a fabricated success.
    assert script.count('exit "${bootstrap_status}"') >= 1
