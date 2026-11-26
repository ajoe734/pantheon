"""Tests for the CI test inventory guard (scripts/ci_test_inventory.py)."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_test_inventory as inv  # noqa: E402

SWEEP_STEP = """
  test-sweep:
    strategy:
      matrix:
        shard: [0, 1]
    steps:
      - run: python3 scripts/ci_test_inventory.py run-shard --shard ${{ matrix.shard }} --of 2
"""


def make_repo(tmp_path: Path, files: dict[str, str], workflow: str, exemptions=None) -> Path:
    files = dict(files)
    files[".github/workflows/branch-ci.yml"] = textwrap.dedent(workflow)
    files[inv.INVENTORY_PATH] = json.dumps({"schema_version": 1, "exemptions": exemptions or []})
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def run_check(root: Path) -> list[str]:
    return inv.check(root, sources=(".github/workflows/branch-ci.yml",))


CMD_WORKFLOW = """
    jobs:
      a:
        steps:
          - run: |
              python3 -m pytest -q \\
                scripts/test_one.py \\
                svc/tests
"""


def test_new_unrun_test_file_fails(tmp_path):
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "svc/tests/test_x.py": "", "scripts/test_new.py": ""}, CMD_WORKFLOW)
    errors = run_check(root)
    assert len(errors) == 1 and "scripts/test_new.py" in errors[0]


def test_star_test_suffix_files_are_test_files(tmp_path):
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "pkg/thing_test.py": "", "pkg/helper.py": ""}, CMD_WORKFLOW)
    errors = run_check(root)
    assert len(errors) == 1 and "pkg/thing_test.py" in errors[0]


def test_vendored_test_files_are_ignored(tmp_path):
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "vendor/lib/test_v.py": "", "node_modules/x/test_y.py": ""}, CMD_WORKFLOW)
    assert run_check(root) == []


def test_listed_file_with_reason_and_owner_passes(tmp_path):
    ex = [{"path": "scripts/test_new.py", "reason": "needs postgres", "owner": "domain:x"}]
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "scripts/test_new.py": ""}, CMD_WORKFLOW, ex)
    assert run_check(root) == []


@pytest.mark.parametrize("entry, needle", [
    ({"path": "scripts/test_new.py", "reason": "", "owner": "domain:x"}, "no reason"),
    ({"path": "scripts/test_new.py", "reason": "r", "owner": " "}, "no owning"),
])
def test_exemption_without_reason_or_owner_fails(tmp_path, entry, needle):
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "scripts/test_new.py": ""}, CMD_WORKFLOW, [entry])
    assert any(needle in e for e in run_check(root))


def test_stale_inventory_entries_fail(tmp_path):
    ex = [
        {"path": "scripts/test_one.py", "reason": "r", "owner": "o"},   # now run by CI
        {"path": "scripts/test_gone.py", "reason": "r", "owner": "o"},  # deleted
    ]
    root = make_repo(tmp_path, {"scripts/test_one.py": ""}, CMD_WORKFLOW, ex)
    errors = run_check(root)
    assert any("test_one.py" in e and "now runs it" in e for e in errors)
    assert any("test_gone.py" in e and "not a tracked test file" in e for e in errors)


def test_whole_directory_pytest_counts_as_run(tmp_path):
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "svc/tests/test_x.py": "", "svc/tests/deep/test_y.py": ""}, CMD_WORKFLOW)
    cov = inv.covered_files(root, sources=(".github/workflows/branch-ci.yml",))
    assert cov["svc/tests/deep/test_y.py"] == "whole"
    assert run_check(root) == []


def test_unittest_discover_counts_as_run(tmp_path):
    wf = """
    jobs:
      a:
        steps:
          - run: python3 -m unittest discover -s svc/tests -p 'test_*.py'
    """
    root = make_repo(tmp_path, {"svc/tests/test_x.py": "", "svc/tests/check_y.py": "", "other/test_z.py": ""}, wf)
    cov = inv.covered_files(root, sources=(".github/workflows/branch-ci.yml",))
    assert "svc/tests/test_x.py" in cov and "other/test_z.py" not in cov


def test_unittest_dotted_modules_and_pythonpath_root(tmp_path):
    wf = """
    jobs:
      a:
        steps:
          - run: |
              PYTHONPATH=.orchestrator python3 -m unittest \\
                test_pi rewrite.test_task scripts.test_s
    """
    files = {".orchestrator/test_pi.py": "", ".orchestrator/rewrite/test_task.py": "", "scripts/test_s.py": ""}
    root = make_repo(tmp_path, files, wf)
    assert run_check(root) == []


def test_node_selection_is_partial_but_collected(tmp_path):
    wf = """
    jobs:
      a:
        steps:
          - run: python3 -m pytest -q scripts/test_one.py::test_a
    """
    root = make_repo(tmp_path, {"scripts/test_one.py": ""}, wf)
    assert inv.covered_files(root, sources=(".github/workflows/branch-ci.yml",)) == {"scripts/test_one.py": "partial"}


def test_ignore_option_excludes_files(tmp_path):
    wf = """
    jobs:
      a:
        steps:
          - run: python3 -m pytest -q svc --ignore=svc/skip
    """
    root = make_repo(tmp_path, {"svc/test_a.py": "", "svc/skip/test_b.py": ""}, wf)
    errors = run_check(root)
    assert len(errors) == 1 and "svc/skip/test_b.py" in errors[0]


def test_stage0_matrix_json_commands_are_read(tmp_path):
    root = make_repo(tmp_path, {"scripts/test_m.py": ""}, "jobs: {}")
    (root / ".github/pantheon-stage0-matrix.json").write_text(
        json.dumps({"baseline": {"commands": ["python3 -m pytest -q scripts/test_m.py"]}})
    )
    cov = inv.covered_files(root, sources=(".github/pantheon-stage0-matrix.json",))
    assert cov == {"scripts/test_m.py": "whole"}


def test_sweep_runs_everything_not_exempt_by_default(tmp_path):
    wf = CMD_WORKFLOW + SWEEP_STEP
    files = {"scripts/test_one.py": "", "scripts/test_new.py": "", "scripts/test_red.py": ""}
    ex = [{"path": "scripts/test_red.py", "reason": "fails", "owner": "o"}]
    root = make_repo(tmp_path, files, wf, ex)
    assert run_check(root) == []
    cov = inv.covered_files(root, sources=(".github/workflows/branch-ci.yml",))
    assert cov["scripts/test_new.py"] == "sweep" and "scripts/test_red.py" not in cov
    assert inv.sweep_files(root, sources=(".github/workflows/branch-ci.yml",)) == ["scripts/test_new.py"]


def test_sweep_with_missing_shard_does_not_count(tmp_path):
    wf = CMD_WORKFLOW + SWEEP_STEP.replace("[0, 1]", "[0]")
    root = make_repo(tmp_path, {"scripts/test_one.py": "", "scripts/test_new.py": ""}, wf)
    errors = run_check(root)
    assert any("must be exactly 0..1" in e for e in errors)
    assert any("scripts/test_new.py" in e for e in errors)


def test_shards_partition_every_file_exactly_once():
    files = [f"a/test_{i}.py" for i in range(200)]
    seen = [f for s in range(12) for f in files if inv.shard_of(f, 12) == s]
    assert sorted(seen) == sorted(files)


def test_real_repository_passes_guard():
    assert inv.check() == []


def test_real_workflow_sweep_is_wired():
    ok, problems = inv._sweep_declared(inv.REPO_ROOT, inv.WORKFLOW_SOURCES)
    assert ok, problems
