from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "audit_management_list_contract.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_audit_detects_duplicate_envelope_aliases_and_source_records(tmp_path: Path) -> None:
    source = tmp_path / "sample_bff.py"
    source.write_text(
        """
class App:
    def get(self, path):
        def decorate(fn):
            return fn
        return decorate

app = App()

@app.get("/bff/management/sample")
async def bff_management_sample():
    rows = [{
        "id": "row-1",
        "riskLevel": "high",
        "risk_level": "high",
        "sourceRecord": {"raw": True},
        "source_record": {"raw": True},
    }]
    return {
        "data": {"items": rows, "rows": rows},
        "items": rows,
        "rows": rows,
    }
""",
        encoding="utf-8",
    )

    result = _run("--source", str(source), "--format", "json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    categories = {issue["category"] for issue in payload["issues"]}
    assert "duplicate-envelope" in categories
    assert "duplicate-list-alias" in categories
    assert "camel-snake-duplicate" in categories
    assert "source-record-in-list-dto" in categories


def test_audit_allows_link_only_related_aggregates(tmp_path: Path) -> None:
    source = tmp_path / "sample_bff.py"
    source.write_text(
        """
class App:
    def get(self, path):
        def decorate(fn):
            return fn
        return decorate

app = App()

@app.get("/bff/management/sample")
async def bff_management_sample():
    rows = [{"id": "row-1"}]
    return {
        "data": {"items": rows, "summary": {"total": 1}},
        "page_info": {"total": 1},
        "meta": {
            "related": {
                "persona_league": {"href": "/bff/management/persona-league"},
                "human_inbox": {"href": "/bff/management/human-inbox"},
            }
        },
    }
""",
        encoding="utf-8",
    )

    result = _run("--source", str(source), "--format", "json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    categories = {issue["category"] for issue in payload["issues"]}
    assert "embedded-aggregate-payload" not in categories
    assert "duplicate-envelope" not in categories


def test_audit_detects_persona_fleet_detail_helper_regression(tmp_path: Path) -> None:
    source = tmp_path / "sample_bff.py"
    source.write_text(
        """
class App:
    def get(self, path):
        def decorate(fn):
            return fn
        return decorate

app = App()

def _build_persona_health_items():
    return []

@app.get("/bff/management/persona-fleet")
async def bff_management_persona_fleet():
    rows = _build_persona_health_items()
    return {"data": {"items": rows, "summary": {}}}
""",
        encoding="utf-8",
    )

    result = _run("--source", str(source), "--format", "json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    issues = payload["issues"]
    assert any(
        issue["category"] == "project-before-page"
        and issue["function"] == "bff_management_persona_fleet"
        for issue in issues
    )


def test_audit_baseline_allows_current_debt_but_fails_new_findings(tmp_path: Path) -> None:
    source = tmp_path / "sample_bff.py"
    source.write_text(
        """
class App:
    def get(self, path):
        def decorate(fn):
            return fn
        return decorate

app = App()

@app.get("/bff/management/sample")
async def bff_management_sample():
    rows = [{"id": "row-1"}]
    return {"data": rows, "items": rows}
""",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"

    write_result = _run(
        "--source",
        str(source),
        "--write-baseline",
        str(baseline),
        "--format",
        "json",
    )
    assert write_result.returncode == 0, write_result.stderr

    allowed_result = _run(
        "--source",
        str(source),
        "--baseline",
        str(baseline),
        "--fail-on-new",
        "--format",
        "json",
    )
    assert allowed_result.returncode == 0, allowed_result.stdout + allowed_result.stderr

    source.write_text(
        source.read_text(encoding="utf-8")
        + """

@app.get("/bff/management/new-sample")
async def bff_management_new_sample():
    rows = [{"id": "row-2"}]
    return {"data": {"items": rows, "samples": rows}, "items": rows}
""",
        encoding="utf-8",
    )

    failed_result = _run(
        "--source",
        str(source),
        "--baseline",
        str(baseline),
        "--fail-on-new",
        "--format",
        "json",
    )
    assert failed_result.returncode == 1
    payload = json.loads(failed_result.stdout)
    assert payload["new_issue_count"] > 0
