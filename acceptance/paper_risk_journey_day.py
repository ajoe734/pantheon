#!/usr/bin/env python3
"""Daily, read-only judgement of the currently served release's paper risk journey samples.

Release time (acceptance/probes/paper-risk-journey) reports missing fresh samples as pending (77).
This check runs after the Taiwan session from persona-day-acceptance.yml, reuses the probe's ``check``
unchanged for every sample check, and turns "pending" into a deadline: a complete trading session
without a governed decision per persona, or five complete sessions without a refusal or a reconciled
fill, is failed. Sessions come from a governed calendar read (never weekday arithmetic). GET only after
the viewer token exchange; writes nothing but its stdout artifact. See acceptance/README.md.

Exit: 0 passed or pending, 1 failed, 77 missing configuration. The verdict is in the JSON ``verdict``.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_loader = importlib.machinery.SourceFileLoader(
    "paper_risk_journey_probe", str(Path(__file__).with_name("probes") / "paper-risk-journey"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
probe = importlib.util.module_from_spec(_spec)
_loader.exec_module(probe)
binding = probe.binding
binding.MAX_REQUESTS = 74  # probe budget (72) + one calendar read + one deployment manifest read
binding.MAX_SECONDS = 240

CALENDAR_PATH = "/bff/market/trading-calendar"  # overridable with ACCEPTANCE_TRADING_CALENDAR_PATH
FILL_OR_REFUSAL_SESSIONS = 5
CALENDAR_UNREADABLE_DAYS = 7
DECISION_FINDING = probe.NO_SAMPLE_PREFIX
LATE_FINDINGS = ("no_fresh_pre_broker_rejection_with_amounts", "no_fresh_broker_position_cash_reconciled_fill")


def complete_sessions(calendar, cutoff, now):
    """Closing times of complete sessions in (cutoff, now]; None when the calendar cannot be used."""
    rows = binding.data(calendar).get("sessions") if isinstance(calendar, dict) else None
    if not isinstance(rows, list):
        return None
    ended = []
    for row in rows:
        if not isinstance(row, dict) or row.get("is_trading_day") is False:
            continue  # holidays and closed days never count
        try:
            closes = probe.timestamp(row["closes_at"])
        except (KeyError, ValueError, TypeError, AttributeError):
            return None
        if row.get("complete", True) is not False and cutoff < closes <= now:
            ended.append(closes)
    return sorted(ended)


def judge(report, sessions, now):
    """Pure verdict. ``sessions`` is the list from complete_sessions or None when unreadable."""
    failed = list(report.get("failed") or [])
    cutoff = probe.timestamp(report["deployment_cutoff"])
    out = {"verdict": "passed", "failed": failed, "pending": [], "reasons": [],
           "sessions_after_cutoff": None if sessions is None else len(sessions)}
    other = [f for f in failed if not probe.is_no_sample(f)]
    if other:
        out.update(verdict="failed", reasons=["non_sample_findings"])
        return out
    if not failed:
        return out
    out["pending"] = list(failed)
    if sessions is None:
        if now - cutoff >= timedelta(days=CALENDAR_UNREADABLE_DAYS):
            out.update(verdict="failed", reasons=["trading_calendar_unreadable_after_%d_days" % CALENDAR_UNREADABLE_DAYS])
        else:
            out.update(verdict="pending", reasons=["trading_calendar_unreadable"])
        return out
    reasons = []
    if sessions and any(f.startswith(DECISION_FINDING) for f in failed):
        reasons.append("no_governed_decision_after_a_complete_session")
    if len(sessions) >= FILL_OR_REFUSAL_SESSIONS and any(f in LATE_FINDINGS for f in failed):
        reasons.append("no_refusal_or_reconciled_fill_after_%d_complete_sessions" % FILL_OR_REFUSAL_SESSIONS)
    out.update(verdict="failed" if reasons else "pending", reasons=reasons)
    return out


def evaluate(fetch, manifest_fetch, source_read, *, expected, pool_policy_reader=None, now=None):
    now = now or datetime.now(timezone.utc)
    manifest = manifest_fetch()
    report = probe.check(fetch, lambda: manifest_fetch(), source_read, expected=expected,
                         backend_sha=str(manifest.get("bffCommit", "")), frontend_sha=str(manifest.get("frontendSha", "")),
                         pool_policy_reader=pool_policy_reader, now=now)
    sessions, calendar_error = None, None
    try:
        sessions = complete_sessions(
            fetch(os.environ.get("ACCEPTANCE_TRADING_CALENDAR_PATH") or CALENDAR_PATH),
            probe.timestamp(report["deployment_cutoff"]), now)
    except (RuntimeError, ValueError, KeyError) as exc:
        calendar_error = str(exc)
    # The sample checks and their report stay as the probe produced them; only the deadline is added.
    report["day"] = judge(report, sessions, now)
    if calendar_error:
        report["day"]["calendar_error"] = calendar_error
    report["verdict"] = report["day"]["verdict"]
    report["exit_code"] = 1 if report["verdict"] == "failed" else 0
    report["passed"] = report["verdict"] == "passed"
    return report


def main():
    def runner(fetch, expected, pool_reader, public_read, owner_read):
        captured["report"] = evaluate(fetch, public_read, lambda eid, tenant: owner_read("telemetry", eid, tenant),
                                      expected=expected, pool_policy_reader=pool_reader)
        return captured["report"]
    captured = {}
    if not os.environ.get("ACCEPTANCE_FE_URL"):
        print("skipped: missing ACCEPTANCE_FE_URL")
        return 77
    code = binding.main(runner)
    return captured["report"]["exit_code"] if "report" in captured else code


if __name__ == "__main__":
    sys.exit(main())
