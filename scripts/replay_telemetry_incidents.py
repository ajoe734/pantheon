"""
scripts/replay_telemetry_incidents.py — LOOP-AUTO-TEL-005

Operator replay script that drives the telemetry incident scenarios against
live (or locally-running) services and prints a structured evidence summary.

Scenarios:
  1. order_rejection_spike  — inject order rejection spike threshold breach
  2. heartbeat_loss         — inject heartbeat loss threshold breach
  3. pnl_drift              — inject PnL drift telemetry into reconciliation-drift,
                              then forward resulting DriftReport to the incident service
  4. recovery               — inject recovery telemetry (no drift expected)

Usage:
    python3 scripts/replay_telemetry_incidents.py [--incidents-url URL] [--drift-url URL]

Defaults:
    INCIDENTS_URL  = http://localhost:8090   (or $INCIDENTS_URL env var)
    DRIFT_URL      = http://localhost:8102   (or $RECONCILIATION_DRIFT_URL env var)

The script prints a JSON evidence summary to stdout and exits with:
  0  all acceptance criteria passed
  1  one or more criteria failed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INCIDENTS_FIXTURE_DIR = _REPO_ROOT / "services" / "incidents" / "fixtures"
_DRIFT_FIXTURE_DIR = _REPO_ROOT / "services" / "reconciliation-drift" / "fixtures"

ORDER_REJECTION_FIXTURE = _INCIDENTS_FIXTURE_DIR / "order_rejection_spike_telemetry.json"
HEARTBEAT_LOSS_FIXTURE = _INCIDENTS_FIXTURE_DIR / "heartbeat_loss_telemetry.json"
PNL_DRIFT_FIXTURE = _DRIFT_FIXTURE_DIR / "pnl_drift_telemetry_event.json"
RECOVERY_FIXTURE = _DRIFT_FIXTURE_DIR / "recovery_telemetry_event.json"


def _post(url: str, payload: Any) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return exc.code, parsed


def _get(url: str) -> tuple[int, Any]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_drift_report_from_event(event: dict) -> dict | None:
    import importlib.util
    key = "_replay_recon_consumer"
    spec = importlib.util.spec_from_file_location(
        key,
        _REPO_ROOT / "services" / "reconciliation-drift" / "consumer.py",
    )
    assert spec and spec.loader
    import sys
    if key not in sys.modules:
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    else:
        module = sys.modules[key]
    return module.build_drift_report_from_event(event, existing_report_ids=set())


def replay(incidents_url: str, drift_url: str) -> dict:
    results: list[dict] = []
    passed = 0
    failed = 0

    def _check(scenario: str, criterion: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        entry = {"scenario": scenario, "criterion": criterion, "status": status}
        if detail:
            entry["detail"] = detail
        results.append(entry)
        prefix = "[PASS]" if ok else "[FAIL]"
        print(f"  {prefix} {scenario}: {criterion}", flush=True)
        if detail and not ok:
            print(f"         {detail}", flush=True)

    # ------------------------------------------------------------------
    # Scenario 1: Order rejection spike
    # ------------------------------------------------------------------
    print("\n=== Scenario 1: Order rejection spike ===")
    order_payload = _load(ORDER_REJECTION_FIXTURE)
    status, body = _post(f"{incidents_url}/api/incidents/consume-threshold", order_payload)
    _check(
        "order_rejection_spike",
        "consume-threshold returns 201",
        status == 201,
        f"status={status} body={body!r}",
    )
    if status == 201:
        incident_id = body.get("incident_id", "")
        _check(
            "order_rejection_spike",
            "incident_id matches fixture",
            incident_id == "inc-tel005-order-rejection-spike-001",
            f"got {incident_id!r}",
        )
        _check(
            "order_rejection_spike",
            "incident status is open",
            body.get("status") == "open",
            f"got {body.get('status')!r}",
        )
        _check(
            "order_rejection_spike",
            "telemetry_event_ids populated",
            "tel-tel005-order-reject-spike-001" in body.get("telemetry_event_ids", []),
            f"got {body.get('telemetry_event_ids')!r}",
        )

        # Operator payload check
        op_status, op_body = _get(f"{incidents_url}/api/incidents/{incident_id}/operator-payload")
        _check(
            "order_rejection_spike",
            "operator-payload returns 200",
            op_status == 200,
            f"status={op_status}",
        )
        if op_status == 200:
            _check(
                "order_rejection_spike",
                "operator-payload.is_open agrees with incident",
                op_body.get("is_open") is True,
                f"got is_open={op_body.get('is_open')!r}",
            )
            _check(
                "order_rejection_spike",
                "operator-payload.incident_id agrees",
                op_body.get("incident_id") == body.get("incident_id"),
                f"op={op_body.get('incident_id')!r} inc={body.get('incident_id')!r}",
            )

        # Idempotent replay
        status2, body2 = _post(f"{incidents_url}/api/incidents/consume-threshold", order_payload)
        _check(
            "order_rejection_spike",
            "idempotent replay returns 200",
            status2 == 200,
            f"status={status2}",
        )

    # ------------------------------------------------------------------
    # Scenario 2: Heartbeat loss
    # ------------------------------------------------------------------
    print("\n=== Scenario 2: Heartbeat loss ===")
    hb_payload = _load(HEARTBEAT_LOSS_FIXTURE)
    status, body = _post(f"{incidents_url}/api/incidents/consume-threshold", hb_payload)
    _check(
        "heartbeat_loss",
        "consume-threshold returns 201",
        status == 201,
        f"status={status} body={body!r}",
    )
    if status == 201:
        incident_id = body.get("incident_id", "")
        _check(
            "heartbeat_loss",
            "incident_id matches fixture",
            incident_id == "inc-tel005-heartbeat-loss-001",
            f"got {incident_id!r}",
        )
        _check(
            "heartbeat_loss",
            "incident status is open",
            body.get("status") == "open",
            f"got {body.get('status')!r}",
        )
        _check(
            "heartbeat_loss",
            "telemetry_event_ids populated",
            "tel-tel005-heartbeat-loss-001" in body.get("telemetry_event_ids", []),
            f"got {body.get('telemetry_event_ids')!r}",
        )

        # Operator payload check
        op_status, op_body = _get(f"{incidents_url}/api/incidents/{incident_id}/operator-payload")
        _check(
            "heartbeat_loss",
            "operator-payload returns 200",
            op_status == 200,
            f"status={op_status}",
        )
        if op_status == 200:
            _check(
                "heartbeat_loss",
                "operator-payload.is_open agrees with incident",
                op_body.get("is_open") is True,
                f"got is_open={op_body.get('is_open')!r}",
            )

        # Idempotent replay
        status2, body2 = _post(f"{incidents_url}/api/incidents/consume-threshold", hb_payload)
        _check(
            "heartbeat_loss",
            "idempotent replay returns 200",
            status2 == 200,
            f"status={status2}",
        )

    # ------------------------------------------------------------------
    # Scenario 3: PnL drift → DriftReport → IncidentCase
    # ------------------------------------------------------------------
    print("\n=== Scenario 3: PnL drift ===")
    pnl_event = _load(PNL_DRIFT_FIXTURE)

    # First post the drift event to the reconciliation-drift service
    drift_status, drift_body = _post(
        f"{drift_url}/api/reconciliation-drift/telemetry-events/consume",
        {"events": [pnl_event]},
    )
    _check(
        "pnl_drift",
        "reconciliation-drift consume returns 2xx",
        drift_status in {200, 201},
        f"status={drift_status} body={drift_body!r}",
    )
    if drift_status in {200, 201}:
        drift_count = drift_body.get("drift_report_count", 0)
        _check(
            "pnl_drift",
            "drift_report_count == 1",
            drift_count == 1,
            f"got {drift_count}",
        )
        if drift_count == 1:
            dr = drift_body["drift_reports"][0]
            _check(
                "pnl_drift",
                "drift report links pnl telemetry event",
                "evt-tel005-pnl-drift-001" in dr.get("telemetry_event_ids", []),
                f"got {dr.get('telemetry_event_ids')!r}",
            )

            # Forward DriftReport to incident service
            inc_status, inc_body = _post(
                f"{incidents_url}/api/incidents/consume-drift-report",
                {"drift_report": dr},
            )
            _check(
                "pnl_drift",
                "incident service accepts DriftReport (201 or 200)",
                inc_status in {200, 201},
                f"status={inc_status} body={inc_body!r}",
            )
            if inc_status in {200, 201}:
                _check(
                    "pnl_drift",
                    "incident binding_id matches drift report",
                    inc_body.get("binding_id") == dr.get("binding_id"),
                    f"inc={inc_body.get('binding_id')!r} dr={dr.get('binding_id')!r}",
                )

    # ------------------------------------------------------------------
    # Scenario 4: Recovery — no drift, no new incident
    # ------------------------------------------------------------------
    print("\n=== Scenario 4: Recovery ===")
    recovery_event = _load(RECOVERY_FIXTURE)
    drift_status, drift_body = _post(
        f"{drift_url}/api/reconciliation-drift/telemetry-events/consume",
        {"events": [recovery_event]},
    )
    _check(
        "recovery",
        "reconciliation-drift consume returns 2xx",
        drift_status in {200, 201},
        f"status={drift_status}",
    )
    if drift_status in {200, 201}:
        _check(
            "recovery",
            "drift_report_count == 0 (all metrics within threshold)",
            drift_body.get("drift_report_count", -1) == 0,
            f"got {drift_body.get('drift_report_count')!r}",
        )
        _check(
            "recovery",
            "recovery event_id in ignored_event_ids",
            "evt-tel005-recovery-001" in drift_body.get("ignored_event_ids", []),
            f"got {drift_body.get('ignored_event_ids')!r}",
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    summary = {
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "overall": "PASS" if failed == 0 else "FAIL",
        "results": results,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOOP-AUTO-TEL-005 telemetry incident replay")
    parser.add_argument(
        "--incidents-url",
        default=os.getenv("INCIDENTS_URL", "http://localhost:8090"),
    )
    parser.add_argument(
        "--drift-url",
        default=os.getenv("RECONCILIATION_DRIFT_URL", "http://localhost:8102"),
    )
    parser.add_argument("--json-output", help="Write JSON summary to this file path")
    args = parser.parse_args(argv)

    print(f"Incidents service: {args.incidents_url}")
    print(f"Reconciliation-drift service: {args.drift_url}")

    summary = replay(
        incidents_url=args.incidents_url,
        drift_url=args.drift_url,
    )

    print(f"\n{'='*60}")
    print(f"Overall: {summary['overall']}  ({summary['passed']}/{summary['total']} passed)")
    print('='*60)
    print(json.dumps(summary, indent=2))

    if args.json_output:
        Path(args.json_output).write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(f"\nJSON evidence written to: {args.json_output}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
