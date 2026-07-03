from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_management_bff_list_responses_have_no_runtime_contract_issues() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "audit_management_bff_list_responses.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(repo_root),
            "--fail-on-issues",
            "--format",
            "json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
