#!/usr/bin/env python3
"""OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 — runtime readback probe.

Bounded local nonprod readback of the strict-auth infrastructure health route
against the **running** Flask telemetry service over real HTTP, backed by a real
NATS JetStream file-storage work queue. It is committed with the evidence so the
readback is reproducible by a reviewer rather than being an unverifiable
transcript: re-running it must reproduce the recorded observations.

What it exercises, all against real processes and a real broker:

* strict-auth admission, retry deduplication, and event_id conflict;
* RuntimeBinding evidence refusal at the top level **and nested past the depth
  the scan used to stop at**, plus a clean deeply nested payload that must still
  be admitted so depth itself is not the gate;
* a stranded reservation before and after lease expiry;
* ``SIGKILL`` of the telemetry process after a committed receipt, and the replay
  after restart;
* a second service booted on a volatile buffer, which must refuse every
  admission and write no ledger record at all.

Usage (from the repository root, with a JetStream server already listening):

    python3 docs/deployment/evidence/twelve-loop-gap/\
OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001/readback_probe.py \
        --nats-url nats://127.0.0.1:14222 --out /tmp/readback.json

Nothing hosted is touched and no live capital path is involved.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT))

from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

TENANT = "tenant-alpha"
OTHER_TENANT = "tenant-beta"
PRODUCER = "control-plane-bff"
OTHER_PRODUCER = "rogue-probe-agent"
JWT_SECRET = "infra-health-readback-secret"
LEASE_SECONDS = 2.0
LEDGER_FILENAME = "infrastructure_health_admissions.jsonl"

INFRA_ROUTE = "/api/v1/telemetry/infrastructure-health"
TRADING_ROUTE = "/api/telemetry/ingest"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _request(method: str, url: str, *, body=None, headers=None, timeout=15.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw or "{}")
        except ValueError:
            return exc.code, {"raw": raw}


def _token(*, tenants=(TENANT,), producers=(PRODUCER,), roles=("service",)):
    return encode_jwt_hs256(
        {
            "sub": "control-plane-bff-probe",
            "roles": list(roles),
            "allowed_tenants": list(tenants),
            "allowed_producers": list(producers),
        },
        secret=JWT_SECRET,
    )


# ---------------------------------------------------------------------------
# Event fixtures
# ---------------------------------------------------------------------------


def infra_event(event_id: str, **overrides) -> dict:
    event = {
        "schema_version": "pantheon.infrastructure-health/1",
        "event_id": event_id,
        "event_type": "infrastructure_health",
        "created_at": "2026-07-26T12:00:00Z",
        "tenant_id": TENANT,
        "producer": PRODUCER,
        "component": {
            "service_name": "runtime-manager",
            "component_kind": "http_service",
            "endpoint": "http://runtime-manager:8081/__health__",
        },
        "health_status": "degraded",
        "severity": "warning",
        "observation": {
            "probe_kind": "interval_probe",
            "observed_at": "2026-07-26T12:00:00Z",
            "window_seconds": 60,
            "sample_count": 10,
            "failure_count": 4,
            "error_rate": 0.4,
            "latency_ms": 1200.5,
        },
    }
    event.update(overrides)
    return event


def nested(leaf: dict, levels: int, *, alternate: bool = False):
    """Wrap *leaf* in *levels* of nesting, optionally alternating list/object."""
    value: object = leaf
    for index in range(levels):
        value = [value] if alternate and index % 2 else {"nested": value}
    return value


# ---------------------------------------------------------------------------
# Service process control
# ---------------------------------------------------------------------------


class TelemetryService:
    def __init__(self, *, port: int, storage_dir: str, env: dict[str, str]):
        self.port = port
        self.storage_dir = storage_dir
        self.env = env
        self.process: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ledger_path(self) -> str:
        return str(Path(self.storage_dir) / LEDGER_FILENAME)

    def start(self, *, timeout: float = 60.0) -> int:
        environment = dict(os.environ)
        environment.update(self.env)
        environment["PORT"] = str(self.port)
        environment["PYTHONPATH"] = str(REPO_ROOT)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "services.telemetry.main"],
            cwd=str(REPO_ROOT),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"telemetry service on :{self.port} exited with "
                    f"{self.process.returncode} before becoming healthy"
                )
            try:
                status, _ = _request("GET", f"{self.base}/healthz", timeout=3.0)
                if status == 200:
                    return status
            except Exception:  # noqa: BLE001 - still booting
                pass
            time.sleep(0.3)
        raise RuntimeError(f"telemetry service on :{self.port} never became healthy")

    def sigkill(self) -> int:
        assert self.process is not None
        self.process.send_signal(signal.SIGKILL)
        self.process.wait(timeout=30)
        return self.process.returncode

    def terminate(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)

    # -- probes --

    def post_infra(self, event, *, token=None, tenant=None):
        headers = {"X-Tenant-Id": tenant or TENANT}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return _request("POST", f"{self.base}{INFRA_ROUTE}", body=event, headers=headers)

    def post_trading(self, event, *, token=None):
        headers = {"X-Tenant-Id": TENANT}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return _request(
            "POST", f"{self.base}{TRADING_ROUTE}", body=event, headers=headers
        )

    def healthz(self):
        return _request("GET", f"{self.base}/healthz")

    def metrics(self):
        return _request("GET", f"{self.base}/metrics")

    def stats(self):
        # The stats surface is itself authority-gated, so the readback must
        # present a verified token to read it.
        return _request(
            "GET",
            f"{self.base}/api/telemetry/stats",
            headers={
                "Authorization": f"Bearer {_token()}",
                "X-Tenant-Id": TENANT,
            },
        )

    def ledger_records(self) -> list[dict]:
        path = Path(self.ledger_path)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


# ---------------------------------------------------------------------------
# JetStream inspection
# ---------------------------------------------------------------------------


def stream_snapshot(nats_url: str, stream: str) -> dict:
    """Read the stream without consuming it, via the direct get-message API."""
    import asyncio

    async def _run() -> dict:
        import nats

        connection = await nats.connect(servers=[nats_url], connect_timeout=5)
        try:
            js = connection.jetstream(timeout=5)
            info = await js.stream_info(stream)
            first = info.state.first_seq
            last = info.state.last_seq
            event_ids: list[str] = []
            for sequence in range(first, last + 1):
                try:
                    message = await js.get_msg(stream, sequence)
                except Exception:  # noqa: BLE001 - purged or acked message
                    continue
                try:
                    payload = json.loads(message.data.decode("utf-8"))
                except ValueError:
                    continue
                event_id = payload.get("event_id")
                if event_id:
                    event_ids.append(str(event_id))
            return {
                "stream": stream,
                "messages": int(info.state.messages),
                "storage": str(getattr(info.config.storage, "value", info.config.storage)),
                "retention": str(
                    getattr(info.config.retention, "value", info.config.retention)
                ),
                "event_ids": event_ids,
            }
        finally:
            await connection.close()

    return asyncio.run(_run())


def delete_stream(nats_url: str, stream: str) -> None:
    import asyncio

    async def _run() -> None:
        import nats

        connection = await nats.connect(servers=[nats_url], connect_timeout=5)
        try:
            js = connection.jetstream(timeout=5)
            try:
                await js.delete_stream(stream)
            except Exception:  # noqa: BLE001 - absent is the desired state
                pass
        finally:
            await connection.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Readback
# ---------------------------------------------------------------------------


def durable_env(*, storage_dir: str, nats_url: str, stream: str) -> dict[str, str]:
    return {
        "TELEMETRY_BUFFER_BACKEND": "jetstream",
        "PANTHEON_NATS_URL": nats_url,
        "TELEMETRY_BUFFER_STREAM_NAME": stream,
        "TELEMETRY_BUFFER_SUBJECT": "pantheon.telemetry.infra_readback",
        "TELEMETRY_BUFFER_DURABLE_NAME": "infra-readback-writer",
        "TELEMETRY_STORAGE_DIR": storage_dir,
        "TELEMETRY_SCHEMA_PATH": str(
            REPO_ROOT / "services/telemetry/telemetry_event.schema.json"
        ),
        "TELEMETRY_INFRASTRUCTURE_HEALTH_LEASE_SECONDS": str(LEASE_SECONDS),
        # Keep the canonical writer from acknowledging inside the readback
        # window, so what the broker holds is what admission put there.
        "TELEMETRY_BATCH_INTERVAL": "600",
        "TELEMETRY_BATCH_SIZE": "10000",
        "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
        "PANTHEON_TELEMETRY_JWT_SECRET": JWT_SECRET,
        "PANTHEON_TELEMETRY_INFRA_PRODUCERS": PRODUCER,
        # Authoritative binding lookups must fail closed during the readback.
        "PANTHEON_RUNTIME_MANAGER_URL": "http://127.0.0.1:1",
    }


def run(nats_url: str, stream: str) -> dict:
    observations: dict[str, object] = {}
    storage_dir = tempfile.mkdtemp(prefix="infra-health-readback5-durable-")
    volatile_dir = tempfile.mkdtemp(prefix="infra-health-readback5-volatile-")

    delete_stream(nats_url, stream)

    service = TelemetryService(
        port=18150,
        storage_dir=storage_dir,
        env=durable_env(storage_dir=storage_dir, nats_url=nats_url, stream=stream),
    )
    restarted: TelemetryService | None = None
    volatile: TelemetryService | None = None
    try:
        health_http = service.start()
        observations["service_boot"] = {
            "buffer_backend": "jetstream",
            "nats_url": nats_url,
            "health_http": health_http,
        }

        status, payload = service.healthz()
        metrics = payload.get("metrics", {})
        observations["healthz_metrics"] = {
            "http": status,
            "overall_status": payload.get("status"),
            "infrastructure_health_buffer_durable": metrics.get(
                "infrastructure_health_buffer_durable"
            ),
            "infrastructure_health_schema_loaded": metrics.get(
                "infrastructure_health_schema_loaded"
            ),
        }

        # -- 1. admission and retry deduplication --
        admitted = infra_event("readback5-infra-001")
        status, payload = service.post_infra(admitted, token=_token())
        observations["valid_ingest"] = {
            "http": status,
            "event_id": payload.get("event_id"),
            "duplicate": payload.get("duplicate"),
            "fingerprint": payload.get("fingerprint"),
        }

        status, payload = service.post_infra(admitted, token=_token())
        observations["retry_replay"] = {
            "http": status,
            "duplicate": payload.get("duplicate"),
        }

        observations["durable_broker_after_admission"] = stream_snapshot(
            nats_url, stream
        )

        # -- 2. RuntimeBinding evidence refusal, at the surface and deep --
        status, payload = service.post_infra(
            infra_event("readback5-binding-spoof-001", binding_id="b-1"),
            token=_token(),
        )
        observations["binding_spoof_on_infra_route"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        status, payload = service.post_infra(
            infra_event(
                "readback5-binding-deep-spoof-001",
                metadata=nested({"binding_id": "b-1"}, 12),
            ),
            token=_token(),
        )
        observations["binding_spoof_nested_depth_12"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        status, payload = service.post_infra(
            infra_event(
                "readback5-binding-deep-list-spoof-001",
                metadata={"probe": nested({"runtime_id": "r-1"}, 40, alternate=True)},
            ),
            token=_token(),
        )
        observations["binding_spoof_nested_depth_40_mixed_containers"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        clean_deep = infra_event(
            "readback5-clean-deep-001",
            metadata={"probe": nested({"note": "no binding evidence"}, 40, alternate=True)},
        )
        status, payload = service.post_infra(clean_deep, token=_token())
        observations["clean_deeply_nested_metadata_is_admitted"] = {
            "http": status,
            "duplicate": payload.get("duplicate"),
            "nesting_levels": 40,
        }

        # -- 3. event_id conflict --
        status, payload = service.post_infra(
            infra_event("readback5-infra-001", severity="critical"), token=_token()
        )
        observations["event_id_conflict"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        # -- 4. authentication and scope --
        status, _ = service.post_infra(infra_event("readback5-no-token-001"))
        observations["missing_token"] = {"http": status}

        status, payload = service.post_infra(
            infra_event("readback5-wrong-tenant-001", tenant_id=OTHER_TENANT),
            token=_token(),
            tenant=OTHER_TENANT,
        )
        observations["wrong_tenant"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        status, payload = service.post_infra(
            infra_event("readback5-tenant-mismatch-001", tenant_id=OTHER_TENANT),
            token=_token(),
        )
        observations["payload_tenant_mismatch"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        status, payload = service.post_infra(
            infra_event("readback5-wrong-producer-001", producer=OTHER_PRODUCER),
            token=_token(producers=(OTHER_PRODUCER,)),
        )
        observations["wrong_producer"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
        }

        # -- 5. the trading route is unaffected and refuses infra events --
        status, payload = service.post_trading(
            {
                "tenant_id": TENANT,
                "event_id": "readback5-runtime-health-spoof-001",
                "event_type": "runtime_health",
                "created_at": "2026-04-15T12:00:00Z",
                "execution_mode": "paper",
                "environment": "paper",
                "deployment_stage": "paper",
                "binding_id": "unknown-binding-999",
                "runtime_id": "lean-worker-1",
                "capital_pool_id": "pool-alpha",
                "artifact_id": "artifact-123",
                "artifact_version": "1.0.0",
                "plan_id": "plan-456",
                "persona_capital_binding_id": "pcb-789",
                "metrics": {"action": "probe"},
                "metadata": {"infrastructure_probe": {"service_name": "runtime-manager"}},
            },
            token=_token(),
        )
        observations["runtime_health_spoof_on_trading_route"] = {
            "http": status,
            "status_field": payload.get("status"),
        }

        status, payload = service.post_trading(
            infra_event("readback5-infra-via-trading-001"), token=_token()
        )
        observations["infra_event_on_trading_route"] = {
            "http": status,
            "status_field": payload.get("status"),
        }

        # -- 6. a stranded reservation, before and after lease expiry --
        stranded = infra_event("readback5-inflight-001")
        sys.path.insert(0, str(REPO_ROOT))
        from services.telemetry.ingest_svc import (
            InfrastructureHealthAdmissionLedger,
            infrastructure_health_fingerprint,
        )

        ledger = InfrastructureHealthAdmissionLedger(
            service.ledger_path, lease_seconds=LEASE_SECONDS
        )
        reservation = ledger.begin(
            stranded["event_id"], infrastructure_health_fingerprint(stranded)
        )
        status, payload = service.post_infra(stranded, token=_token())
        observations["stranded_reservation_before_lease_expiry"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
            "reservation_state": reservation.outcome,
        }

        time.sleep(LEASE_SECONDS + 0.5)
        status, payload = service.post_infra(stranded, token=_token())
        observations["stranded_reservation_recovered_after_lease"] = {
            "http": status,
            "duplicate": payload.get("duplicate"),
        }

        before_crash = stream_snapshot(nats_url, stream)

        # -- 7. SIGKILL after the committed receipt --
        exitcode = service.sigkill()
        observations["process_crash_after_commit"] = {
            "signal": "SIGKILL",
            "exitcode": exitcode,
        }
        after_crash = stream_snapshot(nats_url, stream)
        observations["durable_broker_after_crash"] = after_crash
        observations["durable_broker_retained_admitted_event"] = {
            "before_crash_messages": before_crash["messages"],
            "after_crash_messages": after_crash["messages"],
            "admitted_event_still_durable": admitted["event_id"]
            in after_crash["event_ids"],
        }

        # -- 8. restart replay over the same durable files --
        restarted = TelemetryService(
            port=18150,
            storage_dir=storage_dir,
            env=durable_env(
                storage_dir=storage_dir, nats_url=nats_url, stream=stream
            ),
        )
        restart_health = restarted.start()
        status, payload = restarted.post_infra(admitted, token=_token())
        observations["restart_replay"] = {
            "health_http": restart_health,
            "http": status,
            "duplicate": payload.get("duplicate"),
        }

        status, payload = restarted.stats()
        observations["stats_after_restart"] = {
            "http": status,
            "infrastructure_health": payload.get("infrastructure_health"),
        }

        records = restarted.ledger_records()
        committed = sorted(
            {
                record["event_id"]
                for record in records
                if record.get("state") == "committed"
            }
        )
        open_reservations = sorted(
            {
                record["event_id"]
                for record in records
                if record.get("state") == "reserved"
            }
            - set(committed)
        )
        observations["durable_ledger"] = {
            "record_count": len(records),
            "committed_event_ids": committed,
            "open_or_expired_reservation_event_ids": open_reservations,
        }
        restarted.terminate()

        # -- 9. a volatile deployment must refuse everything --
        volatile_env = durable_env(
            storage_dir=volatile_dir, nats_url=nats_url, stream=stream
        )
        volatile_env["TELEMETRY_BUFFER_BACKEND"] = "memory"
        volatile = TelemetryService(
            port=18151, storage_dir=volatile_dir, env=volatile_env
        )
        volatile_health = volatile.start()
        status, payload = volatile.healthz()
        observations["volatile_backend_boot"] = {
            "buffer_backend": "memory",
            "health_http": volatile_health,
            "infrastructure_health_buffer_durable": payload.get("metrics", {}).get(
                "infrastructure_health_buffer_durable"
            ),
        }

        status, payload = volatile.post_infra(
            infra_event("readback5-volatile-001"), token=_token()
        )
        observations["volatile_backend_admission"] = {
            "http": status,
            "code": payload.get("error", {}).get("code"),
            "message": payload.get("error", {}).get("message"),
        }

        status, payload = volatile.metrics()
        volatile_metrics = payload.get("metrics", payload)
        observations["volatile_backend_metrics"] = {
            "infrastructure_health_buffer_durable": volatile_metrics.get(
                "infrastructure_health_buffer_durable"
            ),
            "infrastructure_health_admitted": volatile_metrics.get(
                "infrastructure_health_admitted"
            ),
            "infrastructure_health_non_durable_rejections": volatile_metrics.get(
                "infrastructure_health_non_durable_rejections"
            ),
        }
        volatile_records = volatile.ledger_records()
        observations["volatile_backend_ledger"] = {
            "record_count": len(volatile_records),
            "reserved_or_committed_anything": bool(volatile_records),
        }
    finally:
        for instance in (service, restarted, volatile):
            if instance is not None:
                instance.terminate()

    return {
        "task_id": "OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001",
        "observed_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "environment": (
            "bounded local nonprod telemetry over real HTTP on 127.0.0.1:18150 "
            "(durable) and :18151 (volatile refusal)"
        ),
        "buffer_backend": "jetstream (real NATS JetStream, file storage, work queue)",
        "runtime_manager_url": "http://127.0.0.1:1 (unreachable; binding lookups fail closed)",
        "hosted_activation_claimed": False,
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nats-url", default="nats://127.0.0.1:14222")
    parser.add_argument("--stream", default="PANTHEON_INFRA_HEALTH_READBACK5")
    parser.add_argument("--out", default="-")
    args = parser.parse_args()

    result = run(args.nats_url, args.stream)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(rendered)
    else:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
