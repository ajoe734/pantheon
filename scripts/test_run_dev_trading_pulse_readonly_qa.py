from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dev_trading_pulse_readonly_qa as qa  # noqa: E402

FE, BFF = "a" * 40, "b" * 40
SECRET = "VIEWER-SECRET-XYZ"
TOKEN = "BEARER-XYZ"
ENV = {"DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID": "viewer-id", "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET": SECRET}
T = "tenant-1"
PEOPLE = [{"persona_id": "p1", "tenant_id": T, "name": "澄川"}, {"persona_id": "p2", "tenant_id": T, "name": "Probe"},
          {"persona_id": "px", "tenant_id": "other", "name": "Foreign"}]
BINDINGS = [
    {"runtime_id": "rt-1", "runtime_binding_id": "rb-1", "persona_id": "p1", "deployment_stage": "paper",
     "tenant_id": T, "underlying_symbol": "2317.TWSE"},
    {"runtime_id": "rt-2", "runtime_binding_id": "rb-2", "persona_id": "p2", "deployment_stage": "paper",
     "tenant_id": T, "underlying_symbol": "0050.TWSE"},
    {"runtime_id": "rt-3", "runtime_binding_id": "rb-3", "persona_id": "p1", "deployment_stage": "paper",
     "tenant_id": T, "status": "retired"},
]
TELEMETRY = {"tenant_id": T, "binding_id": "rb-1", "ledger_fill_count": 0, "pnl": 0,
             "pnl_currency": "TWD", "pnl_period_start_at": "s", "pnl_at": "e",
             "fill_rate_details": {"numerator": 0, "denominator": 452}}
JOURNEY = {"journey_id": "j1", "persona_id": "p1", "symbol": "2317.TWSE",
           "outcome": {"reason_code": "insufficient_cash", "submitted_to_broker": False,
                       "reason_detail": {"required": 100034.3461, "available": 100000, "requested_quantity": 452}}}
TIMELINE = {"items": [{"source_event_id": "ev1", "occurred_at": "2026-11-19T23:46:17Z",
                       "reason_code": "insufficient_cash", "submitted_to_broker": False,
                       "reason_detail": {"required": 100034.3461, "available": 100000, "requested_quantity": 452}}]}
SUMMARY = {"schema": qa.SUMMARY_SCHEMA, "mode": "acceptance", "pass": True,
           "runs": [{"locale": "zh-TW", "width": 1440}, {"locale": "zh-TW", "width": 390},
                    {"locale": "en-US", "width": 1440}]}


class Net:
    def __init__(self, drift=False, tenant=T, roster_change=False, telemetry=None):
        self.calls, self.version_calls, self.roster_calls = [], 0, 0
        self.drift, self.tenant, self.roster_change = drift, tenant, roster_change
        self.telemetry = telemetry or TELEMETRY

    def __call__(self, url, method, headers, body, timeout):
        self.calls.append((method, url))
        path = url.split("mvl-cap.tw", 1)[1]
        if path == "/bff/auth/dev-login":
            return 200, {"access_token": TOKEN}
        if path.endswith("/deployment.json") or path == "/deployment.json":
            return 200, {"frontendSha": FE}
        if path == "/bff/version":
            self.version_calls += 1
            sha = "c" * 40 if self.drift and self.version_calls > 1 else BFF
            return 200, {"source_commit_sha": sha, "config_posture": {"auth_mode": "strict", "auth_stub": False}}
        assert headers["Authorization"] == f"Bearer {TOKEN}", path
        assert method == "GET" or path == "/bff/auth/dev-login"
        assert "trading-pulse" not in path
        if path == "/bff/me":
            return 200, {"data": {"tenant_id": self.tenant}}
        if path == "/api/v1/personas":
            return 200, {"data": PEOPLE}
        if path == "/api/v1/runtime-bindings":
            self.roster_calls += 1
            extra = [{"runtime_id": "rt-n", "persona_id": "p1", "deployment_stage": "paper", "tenant_id": T}]
            return 200, {"data": BINDINGS + (extra if self.roster_change and self.roster_calls > 1 else [])}
        if path == "/api/v1/telemetry/rt-1/summary":
            return 200, {"data": self.telemetry}
        if path.startswith("/bff/management/trade-journeys/j1/timeline"):
            return 200, {"data": TIMELINE}
        if path.startswith("/bff/management/trade-journeys/j1"):
            return 200, {"data": JOURNEY}
        raise AssertionError(path)


class Cli:
    def __init__(self, code=0, summary=SUMMARY, images=1, ready=0):
        self.code, self.summary, self.images, self.ready, self.calls = code, summary, images, ready, []

    def __call__(self, argv, stdin, timeout, cwd=None):
        self.calls.append((list(argv), stdin))
        if argv[:2] == ["node", "--input-type=module"]:
            return self.ready, "", "not ready" if self.ready else ""
        out = Path(argv[argv.index("--out-dir") + 1]) / "trading-pulse-x"
        out.mkdir(parents=True)
        if self.summary is not None:
            (out / "summary.json").write_text(json.dumps({**self.summary, "leak": f"{SECRET} {TOKEN}"}))
        for i in range(self.images):
            (out / f"shot{i}.png").write_bytes(b"png")
        return self.code, f"stdout {SECRET}", f"stderr {TOKEN}"


def make(tmp_path, **over):
    cli = tmp_path / "fe" / qa.CLI_RELATIVE
    cli.parent.mkdir(parents=True)
    cli.write_text("// mode trading-pulse")
    opts = dict(fe_url="https://app.dev.mvl-cap.tw", bff_url="https://api.dev.mvl-cap.tw", fe_sha=FE, bff_sha=BFF,
                root_run_id="9", run_attempt="1", tenant_id=T, history_journey_id="j1", history_event_id="ev1",
                history_environment="paper", frontend_checkout=str(tmp_path / "fe"), out_dir=str(tmp_path / "out"),
                evidence_dir=str(tmp_path / "ev"), evidence_out=str(tmp_path / "ev" / "r.json"))
    opts.update(over)
    import argparse
    return argparse.Namespace(**opts)


def probe(person):
    return person["name"] == "Probe"


def run(tmp_path, net=None, cli=None, env=ENV, **over):
    net, cli = net or Net(), cli or Cli()
    caller = qa.Caller(make(tmp_path, **over), env, http=net, cli_runner=cli, probe=probe)
    return caller.run(), net, cli


def test_pass_maps_exit_zero_and_builds_independent_witness(tmp_path):
    ev, net, cli = run(tmp_path)
    assert ev["outcome"] == "pass", ev
    argv, stdin = cli.calls[-1]
    assert argv[argv.index("--mode") + 1] == "trading-pulse"
    assert SECRET not in " ".join(argv) and TOKEN not in " ".join(argv)
    payload = json.loads(stdin)
    assert payload["profile"] == {"client_id": "viewer-id", "client_secret": SECRET}
    assert payload["expected"] == {"fe": FE, "bff": BFF, "tenant_id": T}
    src = payload["sources"]
    assert [r["runtime_id"] for r in src["rows"]] == ["rt-1"]  # probe excluded, retired and foreign skipped
    assert src["excluded"] == [{"persona_id": "p2", "persona_name": "Probe", "runtime_id": "rt-2"}]
    assert src["rows"][0]["requested_quantity"] == 452 and src["rows"][0]["ledger_fill_count"] == 0
    assert src["pnl_groups"][0]["covered_persona_count"] == 1
    assert all("trading-pulse" not in p["source"] for p in src["provenance"])
    assert payload["history"]["event_id"] == "ev1" and payload["history"]["submitted_to_broker"] is False
    assert payload["history"]["reason_text"]["zh-TW"] == "現金不足"
    assert not any("trading-pulse" in url for _, url in net.calls)


def test_secrets_never_reach_evidence_or_files(tmp_path):
    ev, _, _ = run(tmp_path)
    blob = json.dumps(ev) + "".join(p.read_text() for p in (tmp_path / "ev").glob("*.json"))
    assert SECRET not in blob and TOKEN not in blob
    assert sorted(p.name for p in (tmp_path / "ev").iterdir()) == ["trading-pulse-shot0.png", "trading-pulse-summary.json"]


@pytest.mark.parametrize("code,outcome,exit_code", [(0, "pass", 0), (1, "failed", 1), (2, "unproven", 2), (9, "unproven", 2)])
def test_cli_exit_codes_mapped_without_rewriting(tmp_path, code, outcome, exit_code):
    ev, _, _ = run(tmp_path, cli=Cli(code=code))
    assert ev["outcome"] == outcome and qa.EXIT_BY_OUTCOME[ev["outcome"]] == exit_code


def test_exit_zero_without_positive_summary_is_unproven(tmp_path):
    for summary in (None, {**SUMMARY, "pass": False}, {**SUMMARY, "mode": "baseline_failure_record_only"},
                    {**SUMMARY, "runs": SUMMARY["runs"][:2]}):
        ev, _, _ = run(tmp_path / str(id(summary)), cli=Cli(summary=summary))
        assert ev["outcome"] == "unproven"


def test_too_many_screenshots_is_unproven(tmp_path):
    ev, _, _ = run(tmp_path, cli=Cli(images=3))
    assert ev["reason_code"] == "evidence_budget"


def test_missing_viewer_credentials(tmp_path):
    ev, _, cli = run(tmp_path, env={})
    assert ev["reason_code"] == "missing_profile" and not cli.calls


def test_pair_mismatch_and_drift(tmp_path):
    ev, _, _ = run(tmp_path / "a", fe_sha="d" * 40)
    assert ev["reason_code"] == "pair_mismatch"
    ev, _, _ = run(tmp_path / "b", net=Net(drift=True))
    assert ev["reason_code"] == "pair_mismatch" and "cli_result" in ev


def test_wrong_tenant_and_roster_change_are_unproven(tmp_path):
    ev, _, _ = run(tmp_path / "a", net=Net(tenant="other"))
    assert ev["reason_code"] == "viewer_tenant_mismatch"
    ev, _, _ = run(tmp_path / "b", net=Net(roster_change=True))
    assert ev["reason_code"] == "roster_changed_during_read"


def test_missing_history_and_cli(tmp_path):
    ev, _, _ = run(tmp_path / "a", history_event_id="")
    assert ev["reason_code"] == "missing_history"
    ev, _, _ = run(tmp_path / "b", history_event_id="nope")
    assert ev["reason_code"] == "history_unavailable"
    args = make(tmp_path / "c")
    (Path(args.frontend_checkout) / qa.CLI_RELATIVE).write_text("// old persona-strategy only")
    ev = qa.Caller(args, ENV, http=Net(), cli_runner=Cli(), probe=probe).run()
    assert ev["reason_code"] == "missing_cli_mode"


def test_not_ready_blocks_before_any_credential_use(tmp_path):
    ev, net, _ = run(tmp_path, cli=Cli(ready=3))
    assert ev["reason_code"] == "cli_not_ready" and not net.calls


def test_main_exit_code_and_required_args(tmp_path, capsys):
    assert qa.main([], ENV) == 2
    argv = ["--fe-url", "https://app.dev.mvl-cap.tw", "--bff-url", "https://api.dev.mvl-cap.tw", "--fe-sha", FE,
            "--bff-sha", BFF, "--root-run-id", "9", "--tenant-id", T, "--history-journey-id", "j1",
            "--history-event-id", "ev1", "--frontend-checkout", str(tmp_path), "--out-dir", str(tmp_path / "o"),
            "--evidence-out", str(tmp_path / "e.json")]
    assert qa.main(argv, {}) == 2  # no CLI / credentials: unproven, never pass
