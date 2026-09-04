#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPLICA_COUNT="${PANTHEON_BFF_FAILOVER_REPLICA_COUNT:-2}"
BASE_PORT="${PANTHEON_BFF_FAILOVER_BASE_PORT:-18201}"
OUTPUT_DIR="${PANTHEON_BFF_FAILOVER_OUTPUT_DIR:-/tmp/pantheon-ha-010-v2}"
DATA_DIR="${PANTHEON_BFF_FAILOVER_DATA_DIR:-${OUTPUT_DIR}/shared-bff-data}"
LOG_DIR="${OUTPUT_DIR}/logs"
SLA_FILE="${PANTHEON_BFF_FAILOVER_SLA_FILE:-${ROOT_DIR}/services/bff/ha/sla_targets.json}"
SLA_ENV="${PANTHEON_BFF_FAILOVER_SLA_ENV:-dev}"
SECRET="${PANTHEON_BFF_FAILOVER_JWT_SECRET:-ha-010-v2-dev-secret}"
PYTHON_BIN="${PANTHEON_BFF_PYTHON:-python3}"
REPORT_PATH="${OUTPUT_DIR}/failover-demo.json"

if [[ "$REPLICA_COUNT" != "2" ]]; then
  echo "HA-010-V2 failover demo requires exactly 2 BFF replicas; got ${REPLICA_COUNT}" >&2
  exit 2
fi

mkdir -p "$DATA_DIR" "$LOG_DIR"

if ! "$PYTHON_BIN" -c 'import uvicorn' >/dev/null 2>&1; then
  echo "HA-010-V2 failover demo requires uvicorn in ${PYTHON_BIN}; install services/control-plane/bff/requirements.txt or set PANTHEON_BFF_PYTHON to a prepared venv." >&2
  exit 2
fi

if ! (
  cd "$ROOT_DIR"
  PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}" \
    BFF_DATA_DIR="$DATA_DIR" \
    PANTHEON_BFF_AUTH_STUB=true \
    PANTHEON_BFF_AUTH_MODE=permissive \
    PANTHEON_ENV=dev \
    "$PYTHON_BIN" -c 'import services.control_plane.bff.main' >/dev/null
); then
  echo "HA-010-V2 failover demo could not import the BFF app with ${PYTHON_BIN}; install the BFF and imported service requirements, or set PANTHEON_BFF_PYTHON to a prepared venv." >&2
  exit 2
fi

PIDS=()
URLS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT

start_replica() {
  local index="$1"
  local port="$2"
  local label="$3"
  local log_file="${LOG_DIR}/${label}.log"

  (
    cd "$ROOT_DIR"
    export PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"
    export BFF_DATA_DIR="$DATA_DIR"
    export PANTHEON_BFF_AUTH_STUB=true
    export PANTHEON_BFF_AUTH_MODE=permissive
    export PANTHEON_ENV=dev
    export PANTHEON_DEPLOYMENT_STAGE=dev
    export PANTHEON_BFF_JWT_SECRET="$SECRET"
    export PANTHEON_BFF_SMOKE_JWT_SECRET="$SECRET"
    exec "$PYTHON_BIN" -m uvicorn services.control_plane.bff.main:app --host 127.0.0.1 --port "$port" --log-level warning
  ) >"$log_file" 2>&1 &

  PIDS[index]="$!"
  URLS[index]="http://127.0.0.1:${port}"
}

wait_for_http() {
  local url="$1"
  local attempt

  for attempt in $(seq 1 80); do
    if "$PYTHON_BIN" - "$url" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=1.0) as response:
        raise SystemExit(0 if int(response.status) == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 0.25
  done

  echo "Timed out waiting for ${url}" >&2
  return 1
}

start_replica 0 "$BASE_PORT" "bff-a"
start_replica 1 "$((BASE_PORT + 1))" "bff-b"

wait_for_http "${URLS[0]}/health"
wait_for_http "${URLS[1]}/health"

"$PYTHON_BIN" - "$REPORT_PATH" "$SLA_FILE" "$SLA_ENV" "${URLS[0]}" "${URLS[1]}" "${PIDS[0]}" <<'PY'
from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_ID = "HA-010-V2"
TOKEN = "op-ha010:operator,approver,admin,reviewer:mfa"
AUTH_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-MFA-Token": "000000",
}
RUN_KEY = str(int(time.time() * 1000))


@dataclass
class Row:
    row: str
    status: str
    evidence: dict[str, Any]
    error: str = ""


def passed(row: str, evidence: dict[str, Any] | None = None) -> Row:
    return Row(row=row, status="passed", evidence=evidence or {})


def failed(row: str, error: str, evidence: dict[str, Any] | None = None) -> Row:
    return Row(row=row, status="failed", evidence=evidence or {}, error=error)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_sla(path: Path, env: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = payload.get("targets") if isinstance(payload.get("targets"), dict) else {}
    selected = targets.get(env) if isinstance(targets.get(env), dict) else targets.get("dev", {})
    return {
        "source": str(path),
        "environment": env,
        "rto_seconds": int(selected.get("rto_seconds", 300)),
        "rpo_seconds": int(selected.get("rpo_seconds", 60)),
    }


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 6.0,
) -> dict[str, Any]:
    merged_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        merged_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method)
    started = time.monotonic()
    raw = b""
    response_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(response.status)
            response_headers = dict(response.headers.items())
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_headers = dict(exc.headers.items())
        raw = exc.read()
    except Exception as exc:
        return {
            "status": 0,
            "ok": False,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "headers": {},
            "body": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = {"raw": text[:1000]}
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "headers": response_headers,
        "body": parsed,
    }


def command_payload(target_id: str, reason: str) -> dict[str, Any]:
    return {
        "command": "RuntimeAction",
        "target": {"type": "Runtime", "id": target_id},
        "params": {
            "action_id": "ha_010_v2_pause_demo",
            "entity_type": "runtime",
            "entity_id": target_id,
            "audit_event": "ha_010_v2_failover_demo",
            "live_capital_side_effects": False,
        },
        "audit_context": {"reason": reason, "timestamp": utc_now()},
    }


def receipt_id(response_body: dict[str, Any] | None) -> str:
    body = response_body if isinstance(response_body, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return str(data.get("receipt_id") or data.get("command_id") or data.get("commandId") or "")


def bff_error_code(response_body: dict[str, Any] | None) -> str:
    body = response_body if isinstance(response_body, dict) else {}
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
    return str(error.get("code") or "")


def wait_until_down(url: str, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attempts += 1
        last = request_json("GET", f"{url}/health", timeout=0.4)
        if last.get("status") != 200:
            return {
                "down": True,
                "attempts": attempts,
                "duration_ms": last.get("duration_ms", 0),
                "last": last,
            }
        time.sleep(0.1)
    return {"down": False, "attempts": attempts, "last": last}


def wait_for_ready(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout_seconds
    attempts = 0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attempts += 1
        last = request_json("GET", f"{url}/health", timeout=0.8)
        if last.get("status") == 200:
            return {
                "ready": True,
                "attempts": attempts,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "last": last,
            }
        time.sleep(0.2)
    return {
        "ready": False,
        "attempts": attempts,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "last": last,
    }


def main() -> int:
    output_path = Path(sys.argv[1])
    sla = load_sla(Path(sys.argv[2]), sys.argv[3])
    replica_a = sys.argv[4].rstrip("/")
    replica_b = sys.argv[5].rstrip("/")
    replica_a_pid = int(sys.argv[6])
    rows: list[Row] = []

    initial_health = {
        "replica_a": request_json("GET", f"{replica_a}/health"),
        "replica_b": request_json("GET", f"{replica_b}/health"),
    }
    if initial_health["replica_a"]["status"] == 200 and initial_health["replica_b"]["status"] == 200:
        rows.append(passed("initial-replica-health", {"replica_a": replica_a, "replica_b": replica_b}))
    else:
        rows.append(failed("initial-replica-health", "both replicas must be healthy before failover", initial_health))

    committed_key = f"ha-010-v2-committed-{RUN_KEY}"
    committed_payload = command_payload(
        "runtime-ha-010-v2-committed",
        "HA-010-V2 command committed on replica A before failover",
    )
    committed = request_json(
        "POST",
        f"{replica_a}/bff/v1/commands",
        body=committed_payload,
        headers={
            **AUTH_HEADERS,
            "Idempotency-Key": committed_key,
            "X-Trace-Id": f"trace-ha-010-v2-{RUN_KEY}",
            "X-Correlation-Id": f"corr-ha-010-v2-{RUN_KEY}",
            "X-Request-Id": f"req-ha-010-v2-{RUN_KEY}",
        },
    )
    committed_receipt = receipt_id(committed.get("body"))
    if committed["status"] == 202 and committed_receipt:
        rows.append(
            passed(
                "pre-failover-command-accepted",
                {
                    "replica": replica_a,
                    "idempotency_key": committed_key,
                    "receipt_id": committed_receipt,
                    "duration_ms": committed["duration_ms"],
                },
            )
        )
    else:
        rows.append(failed("pre-failover-command-accepted", "replica A must accept the committed command", {"response": committed}))

    failover_started_monotonic = time.monotonic()
    failover_started_at = utc_now()
    os.kill(replica_a_pid, signal.SIGTERM)
    down_probe = wait_until_down(replica_a)
    ready_probe = wait_for_ready(replica_b, timeout_seconds=float(sla["rto_seconds"]))
    replay = request_json(
        "POST",
        f"{replica_b}/bff/v1/commands",
        body=committed_payload,
        headers={**AUTH_HEADERS, "Idempotency-Key": committed_key},
    )
    failover_rto_seconds = round(time.monotonic() - failover_started_monotonic, 3)
    replay_receipt = receipt_id(replay.get("body"))
    rto_met = (
        bool(down_probe.get("down"))
        and bool(ready_probe.get("ready"))
        and replay["status"] == 202
        and replay_receipt == committed_receipt
        and failover_rto_seconds <= sla["rto_seconds"]
    )
    if rto_met:
        rows.append(
            passed(
                "failover-rto-met",
                {
                    "triggered_at": failover_started_at,
                    "failed_replica": replica_a,
                    "active_replica": replica_b,
                    "replica_a_down_probe": down_probe,
                    "replica_b_ready_probe": ready_probe,
                    "observed_rto_seconds": failover_rto_seconds,
                    "target_rto_seconds": sla["rto_seconds"],
                    "replay_receipt_id": replay_receipt,
                },
            )
        )
    else:
        rows.append(
            failed(
                "failover-rto-met",
                "replica B must become the active command endpoint within the SLA RTO and replay the committed receipt",
                {
                    "down_probe": down_probe,
                    "ready_probe": ready_probe,
                    "replay": replay,
                    "observed_rto_seconds": failover_rto_seconds,
                    "target_rto_seconds": sla["rto_seconds"],
                    "committed_receipt_id": committed_receipt,
                    "replay_receipt_id": replay_receipt,
                },
            )
        )

    status = request_json("GET", f"{replica_b}/api/v1/operator/commands/{committed_receipt}", headers=AUTH_HEADERS)
    status_body = status.get("body") if isinstance(status.get("body"), dict) else {}
    audit = status_body.get("audit") if isinstance(status_body.get("audit"), dict) else {}
    foundation = audit.get("foundation") if isinstance(audit.get("foundation"), dict) else {}
    idem = foundation.get("idempotency_record") if isinstance(foundation.get("idempotency_record"), dict) else {}
    rpo_observed_seconds = 0 if status["status"] == 200 and idem.get("idempotency_key") == committed_key else None
    if rpo_observed_seconds == 0 and rpo_observed_seconds <= sla["rpo_seconds"]:
        rows.append(
            passed(
                "committed-command-rpo-met",
                {
                    "read_replica": replica_b,
                    "receipt_id": status_body.get("command_id"),
                    "stored_status": status_body.get("status"),
                    "idempotency_key": idem.get("idempotency_key"),
                    "request_hash_present": bool(idem.get("request_hash")),
                    "observed_rpo_seconds": rpo_observed_seconds,
                    "target_rpo_seconds": sla["rpo_seconds"],
                    "lost_committed_commands": 0,
                },
            )
        )
    else:
        rows.append(
            failed(
                "committed-command-rpo-met",
                "a command accepted before failover must be visible from replica B with no committed receipt loss",
                {"status_response": status, "target_rpo_seconds": sla["rpo_seconds"]},
            )
        )

    conflict = request_json(
        "POST",
        f"{replica_b}/bff/v1/commands",
        body=command_payload("runtime-ha-010-v2-committed", "HA-010-V2 changed retry must conflict"),
        headers={**AUTH_HEADERS, "Idempotency-Key": committed_key},
    )
    conflict_code = bff_error_code(conflict.get("body"))
    if conflict["status"] == 409 and conflict_code == "IDEMPOTENCY_CONFLICT":
        rows.append(
            passed(
                "changed-retry-fails-closed",
                {
                    "replica": replica_b,
                    "idempotency_key": committed_key,
                    "status": conflict["status"],
                    "error_code": conflict_code,
                    "silent_duplicate_created": False,
                },
            )
        )
    else:
        rows.append(
            failed(
                "changed-retry-fails-closed",
                "changed payload with a committed idempotency key must fail closed with IDEMPOTENCY_CONFLICT",
                {"conflict": conflict, "error_code": conflict_code},
            )
        )

    inflight_key = f"ha-010-v2-inflight-{RUN_KEY}"
    inflight_payload = command_payload(
        "runtime-ha-010-v2-inflight",
        "HA-010-V2 in-flight command during replica A outage",
    )
    dead_replica_attempt = request_json(
        "POST",
        f"{replica_a}/bff/v1/commands",
        body=inflight_payload,
        headers={**AUTH_HEADERS, "Idempotency-Key": inflight_key},
        timeout=1.0,
    )
    retry_on_b = request_json(
        "POST",
        f"{replica_b}/bff/v1/commands",
        body=inflight_payload,
        headers={**AUTH_HEADERS, "Idempotency-Key": inflight_key},
    )
    retry_receipt = receipt_id(retry_on_b.get("body"))
    fail_closed_attempt = dead_replica_attempt["status"] == 0 or dead_replica_attempt["status"] >= 500
    retry_succeeded = retry_on_b["status"] == 202 and bool(retry_receipt)
    if fail_closed_attempt and retry_succeeded:
        rows.append(
            passed(
                "inflight-command-fail-closed-no-silent-loss",
                {
                    "failed_replica_attempt": {
                        "replica": replica_a,
                        "status": dead_replica_attempt["status"],
                        "error": dead_replica_attempt.get("error", ""),
                        "duration_ms": dead_replica_attempt["duration_ms"],
                    },
                    "retry_replica": replica_b,
                    "retry_status": retry_on_b["status"],
                    "retry_receipt_id": retry_receipt,
                    "idempotency_key": inflight_key,
                    "posture": "transport_fail_closed_then_retry_succeeded",
                    "silent_loss_detected": False,
                },
            )
        )
    else:
        rows.append(
            failed(
                "inflight-command-fail-closed-no-silent-loss",
                "an in-flight command to the failed replica must visibly fail closed and then succeed or replay via replica B with the same idempotency key",
                {"failed_replica_attempt": dead_replica_attempt, "retry_on_b": retry_on_b},
            )
        )

    failed_rows = [row for row in rows if row.status != "passed"]
    report = {
        "task_id": TASK_ID,
        "generated_at": utc_now(),
        "status": "passed" if not failed_rows else "failed",
        "topology": {
            "mode": "dev_only_direct_replica_failover",
            "replica_a": replica_a,
            "replica_b": replica_b,
            "shared_store": "BFF_DATA_DIR",
            "load_balancer_changed": False,
            "production_topology_changed": False,
            "l1_policy_changed": False,
        },
        "sla": sla,
        "summary": {
            "failover_triggered": True,
            "active_before": "replica_a",
            "active_after": "replica_b",
            "observed_rto_seconds": failover_rto_seconds,
            "rto_met": rto_met,
            "observed_rpo_seconds": rpo_observed_seconds,
            "rpo_met": rpo_observed_seconds == 0,
            "committed_command_replayed_after_failover": replay_receipt == committed_receipt,
            "inflight_failure_posture": "transport_fail_closed_then_retry_succeeded"
            if fail_closed_attempt and retry_succeeded
            else "failed",
            "silent_loss_detected": False if fail_closed_attempt and retry_succeeded and rpo_observed_seconds == 0 else True,
            "live_capital_side_effects": False,
            "production_topology_changed": False,
        },
        "rows": [asdict(row) for row in rows],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    print(str(output_path))
    return 0 if not failed_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
