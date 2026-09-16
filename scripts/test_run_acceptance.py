"""Exercise the real acceptance shell using isolated, harmless command fixtures."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def acceptance(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    entry = scripts / "run-acceptance.sh"
    shutil.copyfile(Path(__file__).with_name("run-acceptance.sh"), entry)
    (scripts / "ci_stage0.py").touch()
    (tmp_path / "tests").mkdir()
    for name in ("binding_provenance", "capital_integrity", "new_future_verifier"):
        (scripts / f"test_verify_e2e_{name}.py").touch()
    executable = tmp_path / "fixture-python"
    executable.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$FIXTURE_CALLS"\n'
        'if [[ -n "${FIXTURE_FAIL_TARGET:-}" && "$*" == *"$FIXTURE_FAIL_TARGET"* ]]; then exit 17; fi\n'
    )
    executable.chmod(0o700)
    calls = tmp_path / "calls"
    env = {**os.environ, "PYTHON": str(executable), "FIXTURE_CALLS": str(calls)}
    return entry, env, calls


@pytest.mark.parametrize("target", ["validate", "run-baseline", "-q tests", "test_verify_e2e_"])
def test_full_mode_returns_failing_gate_exit(acceptance, target):
    entry, env, _ = acceptance
    completed = subprocess.run(["bash", str(entry), "full"], capture_output=True, text=True,
                               env={**env, "FIXTURE_FAIL_TARGET": target})
    assert completed.returncode == 17
    assert "(exit 17)" in completed.stdout
    assert "mode='full' complete" not in completed.stdout


def test_verifier_glob_runs_each_existing_and_future_test_only_once(acceptance):
    entry, env, calls = acceptance
    completed = subprocess.run(["bash", str(entry), "full"], capture_output=True, text=True, env=env)
    assert completed.returncode == 0, completed.stderr
    invocations = calls.read_text().splitlines()
    assert len(invocations) == 4  # validate, baseline, tests/, one verifier suite
    for name in ("binding_provenance", "capital_integrity", "new_future_verifier"):
        assert calls.read_text().count(f"test_verify_e2e_{name}.py") == 1
    assert "mode='full' complete" in completed.stdout
