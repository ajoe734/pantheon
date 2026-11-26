#!/usr/bin/env python3
"""Job-summary and pass/fail gate for the Dev Taiwan Market Daily Refresh run.

Three refresh steps (TAIFEX, FinMind broker, FinMind fundamentals) use
continue-on-error so later steps and the steady restore still run. That also
hides their failures. This script runs last, reads every step outcome plus every
outcome file the run uploads, writes one table to the job summary and exits 1
when anything failed or is unknown. It only reads local files: no SSH, no
hosted change, no persistent writer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Step outcomes that are not failures. "skipped" covers schedule-gated steps.
OK_STEP_OUTCOMES = frozenset({"success", "skipped"})

# Connector outcome statuses that are not failures. Written by
# scripts/deploy_nonprod_vm.sh write_bounded_refresh_outcome():
#   completed         - "completed"/"refreshed" after a verified readback
#   noop              - preflight says already fresh (check_taiwan_refresh_preflight)
#   skipped           - preflight skip (weekend/holiday/session_not_closed), no active
#                       symbols, or FinMind missing_credential (also written by the
#                       workflow's own missing_credential branches)
#   not_yet_published - provider has not yet published the session (readback path)
OK_CONNECTOR_STATUSES = frozenset({"completed", "noop", "skipped", "not_yet_published"})

# Deliberately NOT ok: "not_run" is written by scripts/deploy_nonprod_vm.sh for a profile that was
# not started because a Taiwan trading day was about to open (00:55Z): the steady posture was restored
# first and the run ends failed through this summary.

# steady_restore_check status written by execute_steady_posture_check(); the workflow
# step itself exits 1 on anything but healthy, so "restored" stays failing here.
OK_STEADY_STATUSES = frozenset({"healthy"})

# step id -> (label, outcome files the step writes, relative to the outcomes dir)
STEP_PROFILES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "taifex_refresh": ("Execute bounded TAIFEX contract refresh", [("tw-taifex-futures-options-chip", "taifex-ssf-refresh-outcome.json")]),
    "finmind_refresh": ("Execute bounded FinMind broker branch refresh", [("tw-finmind-broker-daily-report", "finmind-broker-refresh-outcome.json")]),
    "refresh": ("Execute bounded Taiwan market refresh", [("tw-twse-tpex-official-market", "tw-refresh-outcome.json")]),
    "chip_refresh": ("Execute bounded Taiwan chip refresh", [
        (c, f"{c}-refresh-outcome.json") for c in (
            "tw-official-twse-margin-balances", "tw-official-tpex-margin-balances",
            "tw-official-tpex-institutional-flow", "tw-taifex-institutional-positions",
            "tw-taifex-put-call", "tw-taifex-tx-daily")]),
    "finmind_fundamentals_refresh": ("Execute bounded FinMind fundamentals refresh", [
        (c, f"{c}-refresh-outcome.json") for c in (
            "tw-finmind-monthly-revenue", "tw-finmind-quarterly-statements", "tw-finmind-valuation")]),
}


# Hosts each profile needs (egress allowlist) and the profiles each schedule runs. The one bounded runtime of
# a run allows exactly the union of the hosts of the profiles of that run, and carries the FinMind token
# only when a FinMind profile is in it. Keep in step with dev-tw-market-refresh.yml (tests compare them).
TAIFEX_HOSTS = "openapi.taifex.com.tw,www.taifex.com.tw"
OFFICIAL_HOSTS = "openapi.twse.com.tw,www.tpex.org.tw"
FINMIND_BROKER_HOSTS = "api.finmindtrade.com,storage.googleapis.com"
FINMIND_HOSTS = "api.finmindtrade.com"
PROFILE_HOSTS: dict[str, str] = {
    "tw-taifex-futures-options-chip": TAIFEX_HOSTS,
    "tw-finmind-broker-daily-report": FINMIND_BROKER_HOSTS,
    "tw-twse-tpex-official-market": OFFICIAL_HOSTS,
    "tw-official-twse-margin-balances": OFFICIAL_HOSTS,
    "tw-official-tpex-margin-balances": OFFICIAL_HOSTS,
    "tw-official-tpex-institutional-flow": OFFICIAL_HOSTS,
    "tw-taifex-institutional-positions": TAIFEX_HOSTS,
    "tw-taifex-put-call": TAIFEX_HOSTS,
    "tw-taifex-tx-daily": TAIFEX_HOSTS,
    "tw-finmind-monthly-revenue": FINMIND_HOSTS,
    "tw-finmind-quarterly-statements": FINMIND_HOSTS,
    "tw-finmind-valuation": FINMIND_HOSTS,
}
MORNING_SCHEDULES = frozenset({"0 7 * * 1-5", "0 8 * * 1-5"})
AFTERNOON_SCHEDULES = frozenset({"30 10 * * 1-5", "30 12 * * 1-5"})
FINMIND_PROFILES = frozenset(c for c in PROFILE_HOSTS if c.startswith("tw-finmind-"))
DEFAULT_RUN_MAX_RECORDS = 500


def _profile_max_records(connector: str) -> int:
    """Per-job record budget of a profile, read from the connector definitions (never a constant here)."""
    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES

    profile = TW_CHIP_REFRESH_PROFILES.get(connector) or {}
    return int(profile.get("max_records", DEFAULT_RUN_MAX_RECORDS))


def plan_run(schedule: str, finmind_token_present: bool) -> dict[str, object]:
    """Profiles, egress union and FinMind token flag of one run of the given cron schedule.

    Morning crons skip the FinMind steps, afternoon crons skip TAIFEX/official/chip steps, a manual
    dispatch (empty schedule) runs everything. FinMind profiles without the secret are skipped by their
    steps (missing_credential) and so do not count.
    """
    schedule = (schedule or "").strip()
    profiles = [
        c for c in PROFILE_HOSTS
        if not (schedule in MORNING_SCHEDULES and c in FINMIND_PROFILES)
        and not (schedule in AFTERNOON_SCHEDULES and c not in FINMIND_PROFILES)
        and not (c in FINMIND_PROFILES and not finmind_token_present)
    ]
    hosts: list[str] = []
    for connector in profiles:
        for host in PROFILE_HOSTS[connector].split(","):
            if host not in hosts:
                hosts.append(host)
    return {
        "profiles": profiles,
        "hosts": ",".join(hosts),
        "finmind": any(c in FINMIND_PROFILES for c in profiles),
        "max_records": max([DEFAULT_RUN_MAX_RECORDS] + [_profile_max_records(c) for c in profiles]),
    }


def _read_json(path: Path) -> dict | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def evaluate(step_outcomes: dict[str, str], outcomes_dir: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Return (rows, failures). Each row: kind, name, status, reason, ok."""
    rows: list[dict[str, str]] = []
    failures: list[str] = []

    def add(kind: str, name: str, status: str, reason: str, ok: bool) -> None:
        rows.append({"kind": kind, "name": name, "status": status, "reason": reason, "ok": "yes" if ok else "no"})
        if not ok:
            failures.append(f"{kind} {name} ({status}{': ' + reason if reason else ''})")

    for step_id, (label, files) in STEP_PROFILES.items():
        outcome = (step_outcomes.get(step_id) or "").strip().lower()
        if step_id not in step_outcomes:
            continue
        ok = outcome in OK_STEP_OUTCOMES
        add("step", label, outcome or "unknown", "" if ok else "step outcome is not success/skipped", ok)
        if outcome == "skipped":
            continue  # step did not run: its outcome files are not expected or read
        for connector, filename in files:
            path = outcomes_dir / filename
            if not path.is_file():
                add("connector", connector, "missing", f"outcome file {filename} not found", False)
                continue
            data = _read_json(path)
            if data is None:
                add("connector", connector, "unreadable", f"outcome file {filename} is not a JSON object", False)
                continue
            status = str(data.get("status") or "").strip().lower() or "unknown"
            reason = str(data.get("reason") or "")
            add("connector", connector, status, reason, status in OK_CONNECTOR_STATUSES)

    steady_outcome = (step_outcomes.get("steady_check") or "").strip().lower()
    if "steady_check" in step_outcomes:
        ok = steady_outcome in OK_STEP_OUTCOMES
        add("step", "Verify and restore steady source-ingest posture", steady_outcome or "unknown", "" if ok else "step outcome is not success/skipped", ok)
    steady = None
    for filename, key in (("tw-refresh-outcome.json", "steady_restore_check"), ("steady-restore-check.json", None)):
        data = _read_json(outcomes_dir / filename)
        if data is not None:
            steady = data.get(key) if key else data
            if isinstance(steady, dict):
                break
            steady = None
    if steady is None:
        if steady_outcome != "skipped":
            add("check", "steady_restore_check", "missing", "no steady_restore_check status in outcome files", False)
    else:
        status = str(steady.get("status") or "").strip().lower() or "unknown"
        add("check", "steady_restore_check", status, str(steady.get("reason") or ""), status in OK_STEADY_STATUSES)
    return rows, failures


def render(rows: list[dict[str, str]], failures: list[str]) -> str:
    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")[:300]

    lines = ["## Taiwan market refresh outcome", "", "| Step / connector | Kind | Status | Reason | OK |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {cell(r['name'])} | {r['kind']} | {cell(r['status'])} | {cell(r['reason'])} | {r['ok']} |")
    lines.append("")
    lines.append(f"**Result: FAILED** - {len(failures)} failing: " + "; ".join(failures) if failures else "**Result: all statuses passing**")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", metavar="SCHEDULE", default=None, help="print the bounded runtime plan (GITHUB_OUTPUT lines) for this cron schedule and exit")
    parser.add_argument("--finmind-token-present", action="store_true")
    parser.add_argument("--outcomes-dir", type=Path, default=None)
    parser.add_argument("--summary-file", type=Path, default=None, help="job summary file (GITHUB_STEP_SUMMARY)")
    parser.add_argument("--step", action="append", default=[], metavar="ID=OUTCOME")
    args = parser.parse_args(argv)
    if args.plan is not None:
        plan = plan_run(args.plan, args.finmind_token_present)
        print(f"hosts={plan['hosts']}")
        print(f"finmind={'true' if plan['finmind'] else 'false'}")
        print(f"max_records={plan['max_records']}")
        return 0
    if args.outcomes_dir is None:
        parser.error("--outcomes-dir is required")
    step_outcomes: dict[str, str] = {}
    for item in args.step:
        key, _, value = item.partition("=")
        step_outcomes[key] = value
    rows, failures = evaluate(step_outcomes, args.outcomes_dir)
    text = render(rows, failures)
    if args.summary_file:
        with args.summary_file.open("a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if failures:
        print("::error::Taiwan refresh failed: " + "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
