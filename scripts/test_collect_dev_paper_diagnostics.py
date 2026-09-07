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
    result = diag.collect("b" * 40)
    assert result["identity_matches"] is True
    assert result["services"]["capital"]["collection_status"] == "command_failed"
    assert result["services"]["postgres"]["events"][0]["type"] == "NameError"
    assert "UNTRUSTED_ERROR" not in json.dumps(result)
    assert diag.collect("d" * 40)["identity_matches"] is False
    assert all(args[0] == "docker" and args[1] in {"ps", "inspect", "logs"} for args in calls)
    with pytest.raises(ValueError):
        diag.collect("dev; unsafe")


def test_failure_hook_precedes_compensation_and_artifact_is_bounded():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text())
    steps = next(job["steps"] for job in workflow["jobs"].values() if any(step.get("id") == "paper_bootstrap" for step in job.get("steps", [])))
    names = [step.get("id") for step in steps]
    capture = steps[names.index("paper_bootstrap_diagnostics")]
    upload = steps[names.index("paper_bootstrap_diagnostics_upload")]
    assert names.index("paper_bootstrap") < names.index("paper_bootstrap_diagnostics") < names.index("deploy_compensation")
    assert "steps.paper_bootstrap.outcome == 'failure'" in capture["if"]
    assert "always()" in capture["if"] and capture["continue-on-error"] is True
    assert capture["timeout-minutes"] == 2
    assert "StrictHostKeyChecking=no" not in capture["run"]
    assert "run_with_dev_environment_lease.sh" in capture["run"]
    assert "<" in capture["run"] and "collect_dev_paper_diagnostics.py" in capture["run"]
    assert upload["with"]["retention-days"] == 7
    assert upload["continue-on-error"] is True
