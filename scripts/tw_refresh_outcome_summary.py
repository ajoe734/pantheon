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
    parser.add_argument("--outcomes-dir", required=True, type=Path)
    parser.add_argument("--summary-file", type=Path, default=None, help="job summary file (GITHUB_STEP_SUMMARY)")
    parser.add_argument("--step", action="append", default=[], metavar="ID=OUTCOME")
    args = parser.parse_args(argv)
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
