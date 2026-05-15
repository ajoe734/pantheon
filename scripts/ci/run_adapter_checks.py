#!/usr/bin/env python3
"""CI checks for bounded source/search and fail-closed adapter posture.

This runner is intentionally dependency-light so it can run in Cloud Build's
plain Python builder before manifest emission. Docker and pytest coverage lives
in the GitHub Actions jobs; this step proves the CI wiring is present, key
adapter smoke files are syntactically valid, and research production activation
remains closed without an evidence packet.
"""

from __future__ import annotations

import argparse
import json
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

COMPILE_TARGETS = (
    "scripts/smoke_source_search_bounded.py",
    "scripts/run_research_activation_gates.py",
    "scripts/smoke_openclaw_activation_ready_e2e.py",
    "services/search/tests/test_search_refresh.py",
    "services/source_ingestion/tests/test_bounded_ingestion.py",
)

REQUIRED_TEXT = {
    "cloudbuild.yaml": (
        "id: run-adapter-checks",
        "scripts/ci/run_adapter_checks.py",
        "waitFor:",
        "run-adapter-checks",
    ),
    ".github/workflows/p0-bridge-guards.yml": (
        "source-search-bounded",
        "research-fail-closed",
        "openclaw-facade-fail-closed",
        "scripts/run_research_activation_gates.py --as-of 2026-05-01",
        "scripts/smoke_openclaw_activation_ready_e2e.py",
    ),
    "docker-compose.yml": (
        "source-search-bounded-smoke",
        "scripts/smoke_source_search_bounded.py",
        "SOURCE_INGEST_EXTERNAL_FEED_HOST",
        "SEARCH_INGEST_NOTIFY_URL",
    ),
}


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def compile_targets() -> list[str]:
    checked: list[str] = []
    for rel_path in COMPILE_TARGETS:
        path = ROOT / rel_path
        if not path.exists():
            raise RuntimeError(f"required adapter check target is missing: {rel_path}")
        py_compile.compile(str(path), doraise=True)
        checked.append(rel_path)
    return checked


def check_required_wiring() -> dict[str, list[str]]:
    checked: dict[str, list[str]] = {}
    for rel_path, needles in REQUIRED_TEXT.items():
        path = ROOT / rel_path
        if not path.exists():
            raise RuntimeError(f"required CI wiring file is missing: {rel_path}")
        text = path.read_text(encoding="utf-8")
        missing = [needle for needle in needles if needle not in text]
        if missing:
            raise RuntimeError(f"{rel_path} is missing required adapter CI wiring: {missing}")
        checked[rel_path] = list(needles)
    return checked


def run_research_activation_gate() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="pantheon_adapter_gate_") as tmp:
        out_dir = Path(tmp)
        cmd = [
            sys.executable,
            "scripts/run_research_activation_gates.py",
            "--as-of",
            "2026-05-01",
            "--output-dir",
            str(out_dir),
        ]
        result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 2:
            raise RuntimeError(
                "research activation gates must fail closed without evidence; "
                f"got exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        report_path = out_dir / "research-oss-activation-gate-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "activation_gates_blocked":
            raise RuntimeError(f"unexpected activation gate status: {report.get('status')}")
        if report.get("production_activated_rows"):
            raise RuntimeError(f"production rows activated without evidence: {report['production_activated_rows']}")
        expected_blocked = {"Qlib", "TRL", "RL stack", "W&B"}
        blocked = set(report.get("blocked_rows") or [])
        if not expected_blocked.issubset(blocked):
            raise RuntimeError(f"missing blocked research rows: {sorted(expected_blocked - blocked)}")
        return {
            "exit_code": result.returncode,
            "status": report["status"],
            "blocked_rows": sorted(blocked),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="Optional path for a JSON check report.")
    args = parser.parse_args(argv)

    report = {
        "status": "passed",
        "compiled": compile_targets(),
        "wiring": check_required_wiring(),
        "research_activation_gate": run_research_activation_gate(),
    }

    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
