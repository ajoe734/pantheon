"""Release probe acceptance/probes/mgmt-strategy-meeting-research-readable against recorded hosted shapes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "mgmt-strategy-meeting-research-readable"
FX = json.loads((ROOT / "fixtures" / "hosted_mgmt_strategy_meeting_research_20261120.json").read_text())

_loader = SourceFileLoader("mgmt_strategy_meeting_research_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)


def fetcher(strategies, meetings, research):
    def fetch(path):
        if path.startswith("/bff/strategies"):
            return FX[strategies]
        if path.startswith("/bff/meetings"):
            return FX[meetings]
        if path.startswith("/bff/research-tasks"):
            return FX[research]
        raise AssertionError(path)
    return fetch


def test_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_passes_on_fixed_hosted_shapes():
    report = probe.check(fetcher("strategies_after_fix", "meetings_after_fix", "research_after_fix"))
    assert report["passed"], report
    assert report["strategy_rows"] == 4


def test_fails_on_recorded_pre_fix_hosted_shapes():
    report = probe.check(fetcher("strategies_before_fix", "meetings_before_fix", "research_before_fix"))
    assert not report["passed"]
    assert {f.split(":")[0] for f in report["failed"]} == {
        "stub_or_probe_strategies_listed", "meta_hidden_unbound_stub_strategy_count_missing",
        "meta_hidden_test_persona_strategy_count_missing", "dead_letter_meetings_listed",
        "non_real_research_tasks_listed", "meta_hidden_non_real_count_missing"}
    strategy_failure = next(f for f in report["failed"] if f.startswith("stub_or_probe"))
    assert "strat-tw-official" in strategy_failure and "dev-product-20260913" in strategy_failure


def test_live_probe_named_release_row_is_not_flagged():
    # "Pantheon Dev Paper Release 43688794313" is bound to a running persona on hosted: allowed.
    report = probe.check(fetcher("strategies_after_fix", "meetings_after_fix", "research_after_fix"))
    assert not any("59b8a11bc277c249c89d" in f for f in report["failed"])


def test_exit_77_names_missing_variable_without_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout
    env["ACCEPTANCE_VIEWER_CLIENT_ID"] = "viewer"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_SECRET" in done.stdout
