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

HOSTED = json.loads((ROOT / "fixtures" / "hosted_trade_journey_probe_pages_20261120.json").read_text())

PERSONA = "persona-2448c7053e33ea48da47"


def row(jid, name, probe_flag=False):
    return {"journey_id": jid, "persona_id": PERSONA, "persona_name": name, "persona_is_probe": probe_flag}


def fetcher(rows, filtered, total=None, filtered_total=None):
    def fetch(path):
        flt = "attention=recon_mismatch" in path
        body = {"data": {"items": filtered if flt else rows}, "meta": {}}
        t = filtered_total if flt else total
        if t is not None:
            body["page_info"] = {"total": t}  # top level, as the BFF returns it
        return body
    return fetch


def variance(jid):
    return {**row(jid, "n"), "status": "completed_with_variance"}


def test_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_passes_on_readable_rows():
    rows = [row("tj_a", FIXTURE["persona_names"][PERSONA]), row("tj_b", None)]
    report = probe.check(fetcher(rows, [variance("tj_a")], 2, 1))
    assert report["passed"] and report["rows"] == 2 and report["recon_mismatch_rows"] == 1


def test_hosted_20261120_recorded_bodies_pass():
    assert set(HOSTED["unfiltered"]) == {"data", "page_info", "meta"}
    bodies = {False: HOSTED["unfiltered"], True: HOSTED["recon_mismatch"]}
    report = probe.check(lambda path: bodies["attention=recon_mismatch" in path])
    assert report["passed"], report
    assert report["total"] == 158033 and report["recon_mismatch_total"] == 6941


def test_page_info_nested_under_data_fails():
    def fetch(path):
        flt = "attention=recon_mismatch" in path
        items = [variance("tj_a")] if flt else [row("tj_a", "n")]
        return {"data": {"items": items, "page_info": {"total": 1 if flt else 2}}}
    assert probe.check(fetch)["failed"] == ["attention_filter_not_applied"]


def test_fails_when_server_ignores_filter():
    rows = [row("tj_a", "n")]
    report = probe.check(fetcher(rows, [variance("tj_a")], 158033, 158033))
    assert report["failed"] == ["attention_filter_not_applied"]
    assert probe.check(fetcher(rows, [variance("tj_a")]))["failed"] == ["attention_filter_not_applied"]


def test_fails_when_filtered_row_is_not_variance():
    rows = [row("tj_a", "n")]
    bad = {**row("tj_bad", "n"), "status": "completed"}
    ok = {**row("tj_ok", "n"), "outcome": {"kind": "variance"}}
    report = probe.check(fetcher(rows, [ok, bad], 10, 2))
    assert report["failed"] == ["attention_row_not_variance:tj_bad"]


def test_fails_without_persona_name_or_probe_rows():
    rows = [row("tj_a", None), row("tj_b", None, True)]
    report = probe.check(fetcher(rows, [variance("tj_a")], 2, 1))
    assert {f.split(":")[0] for f in report["failed"]} == {
        "persona_name_missing", "probe_persona_rows_listed"}


def test_exit_77_without_viewer_identity():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout


def test_fails_when_rejected_row_exposes_expression_choice_code():
    bad_rej = {
        **row("tj_79726064-4cc2-5510-8e11-7f5e59ff89bd", "澄川"),
        "status": "failed",
        "outcome": {"kind": "rejected", "reason_code": "long_cash"},
    }
    report = probe.check(fetcher([bad_rej], [variance("tj_a")], 2, 1))
    assert "rejected_row_has_expression_reason_code:tj_79726064-4cc2-5510-8e11-7f5e59ff89bd" in report["failed"]


def test_fails_when_deferred_reconciliation_or_unproven_accounting_variance_is_reported():
    deferred_row = {
        **row("tj_57418ba7-ef37-5015-9b5e-b6423872da89", "澄川"),
        "status": "completed_with_variance",
        "outcome": {
            "kind": "reconciliation_deferred",
            "classification": "broker_unavailable",
            "reason_code": "broker_unavailable_at_reconciliation",
        },
    }
    report = probe.check(fetcher([row("tj_a", "n")], [deferred_row], 2, 1))
    assert "deferred_reconciliation_in_recon_mismatch:tj_57418ba7-ef37-5015-9b5e-b6423872da89" in report["failed"]

    unproven_row = {
        **row("tj_unproven", "澄川"),
        "status": "completed_with_variance",
        "outcome": {
            "kind": "variance",
            "classification": "accounting_variance",
            "accounting_variance": True,
            "reason_code": "cash_variance",
            "discrepancies": [],
        },
    }
    report2 = probe.check(fetcher([row("tj_a", "n")], [unproven_row], 2, 1))
    assert "accounting_variance_missing_discrepancy:tj_unproven" in report2["failed"]

    proven_row = {
        **row("tj_proven", "澄川"),
        "status": "completed_with_variance",
        "outcome": {
            "kind": "variance",
            "classification": "accounting_variance",
            "accounting_variance": True,
            "reason_code": "cash_variance",
            "discrepancies": [{"check": "cash_change", "expected": 217.535, "actual": 172.535, "delta": -45.0}],
        },
    }
    report3 = probe.check(fetcher([row("tj_a", "n")], [proven_row], 2, 1))
    assert report3["passed"] is True

    conflicting_delta_row = {
        **row("tj_conflicting_delta", "澄川"),
        "status": "completed_with_variance",
        "outcome": {
            "kind": "variance",
            "classification": "accounting_variance",
            "accounting_variance": True,
            "reason_code": "cash_variance",
            "discrepancies": [{"check": "cash_change", "expected": 100.0, "actual": 100.0, "delta": -45.0}],
        },
    }
    report4 = probe.check(fetcher([row("tj_a", "n")], [conflicting_delta_row], 2, 1))
    assert "accounting_variance_missing_discrepancy:tj_conflicting_delta" in report4["failed"]
