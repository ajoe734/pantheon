from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_management_list_contract_has_no_new_static_findings() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "audit_management_list_contract.py"
    source = repo_root / "services" / "control-plane" / "bff" / "main.py"
    baseline = repo_root / "docs" / "architecture" / "management-list-contract-baseline.json"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--baseline",
            str(baseline),
            "--fail-on-new",
            "--format",
            "json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
