"""Release probe acceptance/probes/trade-journey-readable against recorded hosted shapes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "trade-journey-readable"
FIXTURE = json.loads((ROOT / "fixtures" / "hosted_trade_journeys_20261120.json").read_text())

_loader = SourceFileLoader("trade_journey_readable_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)

PERSONA = "persona-2448c7053e33ea48da47"


def row(jid, name, probe_flag=False):
    return {"journey_id": jid, "persona_id": PERSONA, "persona_name": name, "persona_is_probe": probe_flag}


def fetcher(rows, filtered):
    return lambda path: {"data": {"items": filtered if "attention=recon_mismatch" in path else rows}}


def test_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_passes_on_readable_rows():
    rows = [row("tj_a", FIXTURE["persona_names"][PERSONA]), row("tj_b", None)]
    report = probe.check(fetcher(rows, rows[:1]))
    assert report["passed"] and report["rows"] == 2 and report["recon_mismatch_rows"] == 1


def test_fails_without_persona_name_probe_rows_or_filter():
    rows = [row("tj_a", None), row("tj_b", None, True)]
    report = probe.check(fetcher(rows, rows))
    assert not report["passed"]
    assert {f.split(":")[0] for f in report["failed"]} == {
        "persona_name_missing", "probe_persona_rows_listed", "attention_filter_not_applied"}


def test_exit_77_without_viewer_identity():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout
