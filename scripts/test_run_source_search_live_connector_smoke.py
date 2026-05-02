from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_source_search_live_connector_smoke.py")


def _run(tmp_path: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "SOURCE_SEARCH_LIVE_EVIDENCE_PATH": str(tmp_path / "evidence.json"),
    }
    for key in list(env):
        if key.startswith("SOURCE_SEARCH_LIVE_") and key != "SOURCE_SEARCH_LIVE_EVIDENCE_PATH":
            env.pop(key, None)
    env.pop("BFF_URL", None)
    env.pop("PANTHEON_BFF_URL", None)
    env.pop("OPENCLAW_ADAPTER_URL", None)
    env.pop("PANTHEON_OPENCLAW_ADAPTER_URL", None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=SCRIPT.parent.parent,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_source_search_live_smoke_records_missing_dependency(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 2
    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["task_id"] == "P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001"
    assert evidence["status"] == "dependency_missing"
    assert "SOURCE_SEARCH_LIVE_FEED_URL" in evidence["required_env"]
    assert "SOURCE_SEARCH_LIVE_FEED_URL is required" in evidence["reason"]


def test_source_search_live_smoke_requires_feed_allowlist(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {
            "SOURCE_SEARCH_LIVE_FEED_URL": "https://feeds.example.test/source-search.json",
            "SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES": "https://other.example.test/",
        },
    )

    assert result.returncode == 2
    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "dependency_missing"
    assert "outside SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES" in evidence["reason"]
