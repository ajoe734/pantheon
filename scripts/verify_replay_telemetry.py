#!/usr/bin/env python3
"""Runbook 5.6 — Telemetry and Output Manifest Verification.

Verifies that a golden replay scenario produced the expected telemetry
events and a complete lineage trace.  Writes a ``verdict.json`` to
``--output-dir`` and exits non-zero on any failure.

Usage::

    python3 scripts/verify_replay_telemetry.py \\
      --scenario replay-golden-001 \\
      --output-dir /tmp/replay-golden-001/ \\
      --expected-verdict approved \\
      --expected-deployment-mode paper
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Required fields that every strategy_cycle_completed event must carry.
_REQUIRED_TELEMETRY_FIELDS = [
    "event_type",
    "strategy_id",
    "dataset_version_id",
    "regime_id",
    "allocation_id",
    "risk_adjudication_id",
    "deployment_plan_id",
    "runtime_binding_id",
    "cycle_at",
    "verdict",
    "deployment_mode",
    "execution_feedback",
]

# Ordered lineage node keys that must all appear in the lineage_trace document.
# The script checks presence of each key; the exact nesting / format is left
# to the lineage service (flat dict, nested dict, or list of dicts are all
# supported as long as every key appears somewhere in the JSON).
_REQUIRED_LINEAGE_KEYS = [
    "raw_dataset_id",
    "normalized_dataset_id",
    "feature_dataset_id",
    "dataset_version_id",
    "regime_id",
    "universe_id",
    "signal_id",
    "allocation_id",
    "risk_adjudication_id",
    "approval_decision_id",
    "deployment_plan_id",
    "runtime_binding_id",
]

_EX001_SENTINEL = "MOCKED_EX001_DEFERRED"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify telemetry and lineage trace for a golden replay scenario.",
    )
    p.add_argument(
        "--scenario",
        required=True,
        help="Scenario ID, e.g. replay-golden-001",
    )
    p.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory produced by run_golden_replay.py",
    )
    p.add_argument(
        "--expected-verdict",
        default="approved",
        help="Expected 'verdict' field in the telemetry event (default: approved)",
    )
    p.add_argument(
        "--expected-deployment-mode",
        default="paper",
        help="Expected 'deployment_mode' field in the telemetry event (default: paper)",
    )
    p.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Print the verdict.json to stdout instead of a human-readable summary",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> tuple[object | None, str | None]:
    """Return (data, None) on success or (None, error_message) on failure."""
    if not path.exists():
        return None, f"file not found: {path}"
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh), None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error in {path}: {exc}"


def _flatten_keys(obj: object, _seen: set[str] | None = None) -> set[str]:
    """Return the set of all string keys that appear anywhere in a JSON object."""
    if _seen is None:
        _seen = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                _seen.add(k)
            _flatten_keys(v, _seen)
    elif isinstance(obj, list):
        for item in obj:
            _flatten_keys(item, _seen)
    return _seen


# ---------------------------------------------------------------------------
# Check functions — each returns (passed: bool, detail: str)
# ---------------------------------------------------------------------------

def _check_telemetry_emitted(
    events: list[dict],
) -> tuple[bool, str]:
    cycle_events = [e for e in events if e.get("event_type") == "strategy_cycle_completed"]
    if not cycle_events:
        return False, "no 'strategy_cycle_completed' event found in telemetry_events.json"
    return True, f"{len(cycle_events)} strategy_cycle_completed event(s) found"


def _check_telemetry_fields(
    events: list[dict],
) -> tuple[bool, str]:
    cycle_events = [e for e in events if e.get("event_type") == "strategy_cycle_completed"]
    if not cycle_events:
        return False, "no strategy_cycle_completed event to inspect"
    event = cycle_events[0]
    missing = [f for f in _REQUIRED_TELEMETRY_FIELDS if f not in event]
    if missing:
        return False, f"strategy_cycle_completed event missing required fields: {missing}"
    return True, "all required telemetry fields present"


def _check_verdict(
    events: list[dict],
    expected: str,
) -> tuple[bool, str]:
    cycle_events = [e for e in events if e.get("event_type") == "strategy_cycle_completed"]
    if not cycle_events:
        return False, "no strategy_cycle_completed event"
    actual = cycle_events[0].get("verdict")
    if actual != expected:
        return False, f"verdict mismatch: expected '{expected}', got '{actual}'"
    return True, f"verdict = '{actual}'"


def _check_deployment_mode(
    events: list[dict],
    expected: str,
) -> tuple[bool, str]:
    cycle_events = [e for e in events if e.get("event_type") == "strategy_cycle_completed"]
    if not cycle_events:
        return False, "no strategy_cycle_completed event"
    actual = cycle_events[0].get("deployment_mode")
    if actual != expected:
        return False, f"deployment_mode mismatch: expected '{expected}', got '{actual}'"
    return True, f"deployment_mode = '{actual}'"


def _check_ex001_mock(events: list[dict]) -> tuple[bool, str]:
    cycle_events = [e for e in events if e.get("event_type") == "strategy_cycle_completed"]
    if not cycle_events:
        return False, "no strategy_cycle_completed event"
    actual = cycle_events[0].get("execution_feedback")
    if actual != _EX001_SENTINEL:
        return False, (
            f"execution_feedback must be '{_EX001_SENTINEL}' (EX-001 deferred); "
            f"got '{actual}'"
        )
    return True, f"execution_feedback = '{_EX001_SENTINEL}'"


def _check_lineage_complete(lineage: object) -> tuple[bool, str]:
    present = _flatten_keys(lineage)
    missing = [k for k in _REQUIRED_LINEAGE_KEYS if k not in present]
    if missing:
        return False, f"lineage_trace.json is missing required keys: {missing}"
    return True, "all required lineage keys present"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = _parse_args()
    output_dir: Path = args.output_dir.resolve()

    results: list[dict] = []

    def _record(criterion: str, passed: bool, detail: str) -> None:
        results.append({"criterion": criterion, "passed": passed, "detail": detail})

    # ---- Load telemetry_events.json ----------------------------------------
    tel_data, tel_err = _load_json(output_dir / "telemetry_events.json")
    if tel_err:
        _record("telemetry_file_readable", False, tel_err)
        events: list[dict] = []
    else:
        _record("telemetry_file_readable", True, f"loaded {output_dir / 'telemetry_events.json'}")
        events = tel_data if isinstance(tel_data, list) else [tel_data]

    # ---- Load lineage_trace.json -------------------------------------------
    lin_data, lin_err = _load_json(output_dir / "lineage_trace.json")
    if lin_err:
        _record("lineage_file_readable", False, lin_err)
        lineage = None
    else:
        _record("lineage_file_readable", True, f"loaded {output_dir / 'lineage_trace.json'}")
        lineage = lin_data

    # ---- Telemetry checks ---------------------------------------------------
    _record("telemetry_emitted", *_check_telemetry_emitted(events))
    _record("telemetry_fields_complete", *_check_telemetry_fields(events))
    _record(
        "verdict_matches",
        *_check_verdict(events, args.expected_verdict),
    )
    _record(
        "deployment_mode_paper",
        *_check_deployment_mode(events, args.expected_deployment_mode),
    )
    _record("ex001_mock_recorded", *_check_ex001_mock(events))

    # ---- Lineage check ------------------------------------------------------
    if lineage is not None:
        _record("lineage_trace_complete", *_check_lineage_complete(lineage))
    else:
        _record("lineage_trace_complete", False, "lineage_trace.json could not be loaded")

    # ---- Build verdict ------------------------------------------------------
    all_passed = all(r["passed"] for r in results)
    verdict_doc = {
        "scenario": args.scenario,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "overall": "PASS" if all_passed else "FAIL",
        "expected_verdict": args.expected_verdict,
        "expected_deployment_mode": args.expected_deployment_mode,
        "criteria": results,
    }

    # Write verdict.json into the output directory (best-effort).
    verdict_path = output_dir / "verdict.json"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text(
            json.dumps(verdict_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"WARNING: could not write {verdict_path}: {exc}", file=sys.stderr)

    # ---- Output -------------------------------------------------------------
    if args.output_json:
        print(json.dumps(verdict_doc, indent=2, ensure_ascii=False))
    else:
        status_label = "PASS" if all_passed else "FAIL"
        print(f"[{status_label}] scenario={args.scenario}  output_dir={output_dir}")
        for r in results:
            icon = "✓" if r["passed"] else "✗"
            print(f"  {icon} {r['criterion']}: {r['detail']}")
        print(f"\nverdict written to {verdict_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
