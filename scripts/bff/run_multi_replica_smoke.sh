#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPLICA_COUNT="${PANTHEON_BFF_REPLICA_COUNT:-3}"
BASE_PORT="${PANTHEON_BFF_BASE_PORT:-18101}"
OUTPUT_DIR="${PANTHEON_BFF_MULTI_REPLICA_OUTPUT_DIR:-/tmp/pantheon-ha-007-v2}"
DATA_DIR="${PANTHEON_BFF_MULTI_REPLICA_DATA_DIR:-${OUTPUT_DIR}/shared-bff-data}"
LOG_DIR="${OUTPUT_DIR}/logs"
SECRET="${PANTHEON_BFF_SMOKE_JWT_SECRET:-ha-007-v2-dev-secret}"
PYTHON_BIN="${PANTHEON_BFF_PYTHON:-python3}"

if [[ "$REPLICA_COUNT" != "3" ]]; then
  echo "HA-007-V2 smoke requires exactly 3 BFF replicas; got ${REPLICA_COUNT}" >&2
  exit 2
fi

mkdir -p "$DATA_DIR" "$LOG_DIR"

if ! "$PYTHON_BIN" -c 'import uvicorn' >/dev/null 2>&1; then
  echo "HA-007-V2 smoke requires uvicorn in ${PYTHON_BIN}; install services/control-plane/bff/requirements.txt or set PANTHEON_BFF_PYTHON to a prepared venv." >&2
  exit 2
fi

if ! (
  cd "$ROOT_DIR/services/control-plane/bff"
  PYTHONPATH="$ROOT_DIR:$ROOT_DIR/services/control-plane/bff:${PYTHONPATH:-}" \
    BFF_DATA_DIR="$DATA_DIR" \
    PANTHEON_BFF_AUTH_STUB=true \
    PANTHEON_BFF_AUTH_MODE=permissive \
    PANTHEON_ENV=dev \
    "$PYTHON_BIN" -c 'import main' >/dev/null
); then
  echo "HA-007-V2 smoke could not import the BFF app with ${PYTHON_BIN}; install the BFF and imported service requirements, or set PANTHEON_BFF_PYTHON to a prepared venv." >&2
  exit 2
fi

PIDS=()
URLS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

start_replica() {
  local index="$1"
  local port="$2"
  local label="bff-$((index + 1))"
  local log_file="${LOG_DIR}/${label}.log"

  (
    cd "$ROOT_DIR/services/control-plane/bff"
    export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/services/control-plane/bff:${PYTHONPATH:-}"
    export BFF_DATA_DIR="$DATA_DIR"
    export PANTHEON_BFF_AUTH_STUB=true
    export PANTHEON_BFF_AUTH_MODE=permissive
    export PANTHEON_ENV=dev
    export PANTHEON_DEPLOYMENT_STAGE=dev
    export PANTHEON_BFF_JWT_SECRET="$SECRET"
    export PANTHEON_BFF_SMOKE_JWT_SECRET="$SECRET"
    exec "$PYTHON_BIN" -m uvicorn main:app --host 127.0.0.1 --port "$port" --log-level warning
  ) >"$log_file" 2>&1 &

  PIDS+=("$!")
  URLS+=("http://127.0.0.1:${port}")
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

for index in $(seq 0 $((REPLICA_COUNT - 1))); do
  start_replica "$index" "$((BASE_PORT + index))"
done

for url in "${URLS[@]}"; do
  wait_for_http "${url}/health"
done

"$PYTHON_BIN" - "$OUTPUT_DIR/multi-replica-smoke.json" "${URLS[@]}" <<'PY'
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


TASK_ID = "HA-007-V2"
TOKEN = "op-ha007:operator,approver,admin,reviewer:mfa"
AUTH_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-MFA-Token": "000000",
}
COMMAND_KEY = f"ha-007-v2-{int(time.time())}"


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


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    merged_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        merged_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method)
    started = time.time()
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
            "duration_ms": round((time.time() - started) * 1000),
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
        "duration_ms": round((time.time() - started) * 1000),
        "headers": response_headers,
        "body": parsed,
    }


def command_payload(reason: str) -> dict[str, Any]:
    return {
        "command": "PauseExecution",
        "target": {"type": "Runtime", "id": "runtime-ha-007-v2"},
        "params": {"pause_new_entries": True, "cancel_open_orders": False},
        "audit_context": {"reason": reason},
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


def header_value(headers: dict[str, str], key: str) -> str:
    wanted = key.lower()
    for current_key, value in headers.items():
        if current_key.lower() == wanted:
            return str(value)
    return ""


def parse_sse_block(lines: list[str]) -> dict[str, Any]:
    fields: dict[str, list[str]] = {}
    for line in lines:
        if not line or line.startswith(":"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip(), []).append(value.lstrip())
    raw_data = "\n".join(fields.get("data", []))
    try:
        data: Any = json.loads(raw_data) if raw_data else None
    except json.JSONDecodeError:
        data = None
    return {
        "id": fields.get("id", [""])[-1],
        "event": fields.get("event", [""])[-1],
        "data": data,
    }


def first_sse_event(base_url: str, *, channel: str, last_event_id: str | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode({"channel": channel})
    headers = {
        **AUTH_HEADERS,
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    req = urllib.request.Request(f"{base_url}/bff/events/stream?{query}", headers=headers, method="GET")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8.0) as response:
            lines: list[str] = []
            while True:
                raw = response.readline()
                if raw == b"":
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    if lines:
                        event = parse_sse_block(lines)
                        return {
                            "status": int(response.status),
                            "ok": bool(event.get("id")),
                            "duration_ms": round((time.time() - started) * 1000),
                            "headers": dict(response.headers.items()),
                            "event": event,
                        }
                    continue
                lines.append(line)
            return {
                "status": int(response.status),
                "ok": False,
                "duration_ms": round((time.time() - started) * 1000),
                "headers": dict(response.headers.items()),
                "error": "stream ended before first event",
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = {"raw": raw[:1000]}
        return {
            "status": int(exc.code),
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "headers": dict(exc.headers.items()),
            "body": body,
            "error_code": bff_error_code(body if isinstance(body, dict) else None),
        }
    except Exception as exc:
        return {
            "status": 0,
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "headers": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def publish_sse(base_url: str, label: str) -> dict[str, Any]:
    path = "/api/v1/internal/sse/publish?" + urllib.parse.urlencode(
        {"channel": "approval", "event_type": "approval.created"}
    )
    payload = {
        "approval_id": f"appr-{TASK_ID.lower()}-{label}",
        "target_type": "ApprovalDecision",
        "target_id": f"decision-{label}",
        "metadata": {"task_id": TASK_ID, "label": label},
    }
    return request_json("POST", f"{base_url}{path}", body=payload, headers=AUTH_HEADERS)


def main() -> int:
    output_path = Path(sys.argv[1])
    urls = [url.rstrip("/") for url in sys.argv[2:]]
    rows: list[Row] = []

    health = [request_json("GET", f"{url}/health") for url in urls]
    if len(urls) == 3 and all(item["status"] == 200 for item in health):
        rows.append(passed("replica-health", {"urls": urls, "statuses": [item["status"] for item in health]}))
    else:
        rows.append(failed("replica-health", "all three replicas must pass /health", {"urls": urls, "health": health}))

    first = request_json(
        "POST",
        f"{urls[0]}/bff/v1/commands",
        body=command_payload("HA-007-V2 first command"),
        headers={
            **AUTH_HEADERS,
            "Idempotency-Key": COMMAND_KEY,
            "X-Trace-Id": "trace-ha-007-v2",
            "X-Correlation-Id": "corr-ha-007-v2",
            "X-Request-Id": "req-ha-007-v2",
        },
    )
    replay = request_json(
        "POST",
        f"{urls[1]}/bff/v1/commands",
        body=command_payload("HA-007-V2 first command"),
        headers={**AUTH_HEADERS, "Idempotency-Key": COMMAND_KEY},
    )
    first_receipt = receipt_id(first.get("body"))
    replay_receipt = receipt_id(replay.get("body"))
    if first["status"] == 202 and replay["status"] == 202 and first_receipt and first_receipt == replay_receipt:
        rows.append(
            passed(
                "idempotency-replay-across-replicas",
                {
                    "first_replica": urls[0],
                    "replay_replica": urls[1],
                    "idempotency_key": COMMAND_KEY,
                    "receipt_id": first_receipt,
                    "replay_receipt_id": replay_receipt,
                },
            )
        )
    else:
        rows.append(
            failed(
                "idempotency-replay-across-replicas",
                "same Idempotency-Key must replay the first receipt on another replica",
                {"first": first, "replay": replay, "first_receipt": first_receipt, "replay_receipt": replay_receipt},
            )
        )

    conflict = request_json(
        "POST",
        f"{urls[2]}/bff/v1/commands",
        body=command_payload("HA-007-V2 changed payload must conflict"),
        headers={**AUTH_HEADERS, "Idempotency-Key": COMMAND_KEY},
    )
    conflict_code = bff_error_code(conflict.get("body"))
    if conflict["status"] == 409 and conflict_code == "IDEMPOTENCY_CONFLICT":
        rows.append(
            passed(
                "idempotency-conflict-across-replicas",
                {"conflict_replica": urls[2], "status": conflict["status"], "error_code": conflict_code},
            )
        )
    else:
        rows.append(
            failed(
                "idempotency-conflict-across-replicas",
                "changed payload with same Idempotency-Key must return 409 IDEMPOTENCY_CONFLICT",
                {"conflict": conflict, "error_code": conflict_code},
            )
        )

    status = request_json("GET", f"{urls[2]}/api/v1/operator/commands/{first_receipt}", headers=AUTH_HEADERS)
    status_body = status.get("body") if isinstance(status.get("body"), dict) else {}
    audit = status_body.get("audit") if isinstance(status_body.get("audit"), dict) else {}
    foundation = audit.get("foundation") if isinstance(audit.get("foundation"), dict) else {}
    idem = foundation.get("idempotency_record") if isinstance(foundation.get("idempotency_record"), dict) else {}
    if status["status"] == 200 and status_body.get("command_id") == first_receipt and idem.get("idempotency_key") == COMMAND_KEY:
        rows.append(
            passed(
                "audit-readable-across-replicas",
                {
                    "read_replica": urls[2],
                    "command_id": status_body.get("command_id"),
                    "stored_status": status_body.get("status"),
                    "audit_operator_id": audit.get("operator_id"),
                    "idempotency_key": idem.get("idempotency_key"),
                    "request_hash_present": bool(idem.get("request_hash")),
                },
            )
        )
    else:
        rows.append(
            failed(
                "audit-readable-across-replicas",
                "command status read from another replica must include audit foundation idempotency data",
                {"status_response": status},
            )
        )

    sse_evidence = []
    sse_ok = True
    for index, url in enumerate(urls, start=1):
        publish = publish_sse(url, f"replica-{index}")
        published_id = ""
        if isinstance(publish.get("body"), dict):
            published_id = str(publish["body"].get("event_id") or "")
        event = first_sse_event(url, channel="approval")
        observed_id = ""
        if isinstance(event.get("event"), dict):
            data = event["event"].get("data") if isinstance(event["event"].get("data"), dict) else {}
            observed_id = str(data.get("id") or event["event"].get("id") or "")
        row_ok = publish["status"] == 200 and event["status"] == 200 and published_id and observed_id == published_id
        sse_ok = sse_ok and row_ok
        sse_evidence.append(
            {
                "replica": url,
                "publish_status": publish["status"],
                "published_event_id": published_id,
                "stream_status": event["status"],
                "streamed_event_id": observed_id,
                "replay_store": header_value(event.get("headers", {}), "X-SSE-Replay-Store"),
                "ok": row_ok,
            }
        )
    if sse_ok:
        rows.append(passed("sse-per-replica-transport", {"channel": "approval", "replicas": sse_evidence}))
    else:
        rows.append(failed("sse-per-replica-transport", "each replica must publish and stream its own approval event", {"replicas": sse_evidence}))

    cross_publish = publish_sse(urls[0], "cross-replica")
    cross_event_id = ""
    if isinstance(cross_publish.get("body"), dict):
        cross_event_id = str(cross_publish["body"].get("event_id") or "")
    cross_replay = first_sse_event(urls[1], channel="approval", last_event_id=cross_event_id)
    if (
        cross_event_id
        and cross_replay["status"] == 409
        and cross_replay.get("error_code") == "SSE_REPLAY_UNAVAILABLE"
        and header_value(cross_replay.get("headers", {}), "X-SSE-Replay-Store") == "in-memory"
    ):
        rows.append(
            passed(
                "sse-cross-replica-replay-fail-closed",
                {
                    "publish_replica": urls[0],
                    "reconnect_replica": urls[1],
                    "event_id": cross_event_id,
                    "status": cross_replay["status"],
                    "error_code": cross_replay.get("error_code"),
                    "replay_store": header_value(cross_replay.get("headers", {}), "X-SSE-Replay-Store"),
                    "shared_fanout_proven": False,
                },
            )
        )
    else:
        rows.append(
            failed(
                "sse-cross-replica-replay-fail-closed",
                "without shared fanout, another replica must fail closed instead of silently serving a stale stream",
                {"publish": cross_publish, "replay": cross_replay, "event_id": cross_event_id},
            )
        )

    failed_rows = [row for row in rows if row.status != "passed"]
    report = {
        "task_id": TASK_ID,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "replica_count": len(urls),
        "replicas": urls,
        "status": "passed" if not failed_rows else "failed",
        "summary": {
            "smoke_passed": not failed_rows,
            "idempotency_and_audit_shared_store": True,
            "sse_same_replica_transport": True,
            "sse_cross_replica_shared_fanout_proven": False,
            "sse_cross_replica_behavior": "fail_closed_in_memory_replay_store",
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
