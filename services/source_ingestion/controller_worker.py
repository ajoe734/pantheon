"""Supervised desired-state reconciler and scheduler for source ingestion."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import time
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .controller_state import ControllerState, ControllerStateStore, utc_now
from .scheduler_worker import run_tick as run_schedule_tick


DEFAULT_DESIRED_STATE_PATH = Path(__file__).with_name("default_desired_state.json")
LOOP_ID = "source_ingestion"


class ControllerTickError(RuntimeError):
    def __init__(self, stage: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.stage = stage
        self.context = context


class LoopControllerWriterLike(Protocol):
    async def record_heartbeat(self, loop_id: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None: ...
    async def record_tick(self, loop_id: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None: ...
    async def record_success(self, loop_id: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None: ...
    async def record_failure(self, loop_id: str, reason: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None: ...
    async def record_repair(self, loop_id: str, reason: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None: ...


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    bearer_token: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    body = _canonical_json(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        raise ControllerTickError("http_contract", f"expected JSON object from {url}")
    return parsed


def _load_bearer_token() -> str | None:
    token_file = str(os.getenv("SOURCE_INGEST_DESIRED_STATE_BEARER_TOKEN_FILE") or "").strip()
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ControllerTickError("desired_state_auth", "desired-state bearer token file is unreadable") from exc
        if not token:
            raise ControllerTickError("desired_state_auth", "desired-state bearer token file is empty")
        return token
    return str(os.getenv("SOURCE_INGEST_DESIRED_STATE_BEARER_TOKEN") or "").strip() or None


def _normalize_persona(raw: Mapping[str, Any]) -> dict[str, Any]:
    persona = dict(raw)
    if not persona.get("persona_id") and persona.get("id"):
        persona["persona_id"] = persona["id"]
    if "required_data_sources" not in persona and isinstance(persona.get("requiredDataSources"), list):
        persona["required_data_sources"] = persona["requiredDataSources"]
    persona_id = str(persona.get("persona_id") or "").strip()
    requirements = persona.get("required_data_sources")
    if not persona_id:
        raise ControllerTickError("desired_state_validate", "persona desired state is missing persona_id")
    if requirements is None:
        requirements = []
        persona["required_data_sources"] = requirements
    if not isinstance(requirements, list):
        raise ControllerTickError(
            "desired_state_validate",
            f"persona {persona_id} required_data_sources must be a list",
        )
    return persona


def _personas_from_payload(payload: Any) -> tuple[dict[str, Any], ...]:
    candidates: Any = payload
    if isinstance(payload, Mapping):
        for key in ("personas", "data", "items"):
            if isinstance(payload.get(key), list):
                candidates = payload[key]
                break
    if not isinstance(candidates, list):
        raise ControllerTickError("desired_state_validate", "desired-state payload must contain a persona list")
    personas = tuple(_normalize_persona(item) for item in candidates if isinstance(item, Mapping))
    if not personas:
        raise ControllerTickError("desired_state_validate", "desired-state payload contains no personas")
    return personas


def load_desired_state(*, timeout_seconds: float = 30.0) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    url = str(os.getenv("SOURCE_INGEST_DESIRED_STATE_URL") or "").strip()
    configured_path = str(os.getenv("SOURCE_INGEST_DESIRED_STATE_PATH") or "").strip()
    if url:
        payload = _request_json(
            url,
            bearer_token=_load_bearer_token(),
            timeout_seconds=timeout_seconds,
        )
        authority = url.split("?", 1)[0]
        transport = "https" if url.startswith("https://") else "internal_http"
    else:
        path = Path(configured_path) if configured_path else DEFAULT_DESIRED_STATE_PATH
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControllerTickError("desired_state_read", f"desired-state file is unreadable: {path}") from exc
        authority = str(payload.get("authority") or f"file://{path}") if isinstance(payload, Mapping) else f"file://{path}"
        transport = "deployment_file"
    personas = _personas_from_payload(payload)
    normalized = [persona for persona in personas]
    return personas, {
        "authority": authority,
        "transport": transport,
        "sha256": _digest(normalized),
        "persona_count": len(personas),
        "requirement_count": sum(len(persona.get("required_data_sources") or []) for persona in personas),
        "read_at": utc_now(),
    }


def reconcile_desired_state(
    *,
    api_url: str,
    personas: Sequence[Mapping[str, Any]],
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    response = _request_json(
        api_url.rstrip("/") + "/api/source-ingest/persona-source-provisioning/reconcile",
        method="POST",
        payload={"personas": list(personas), "authoritative_snapshot": True, "dry_run": False},
        timeout_seconds=timeout_seconds,
    )
    summary = response.get("summary")
    if not isinstance(summary, Mapping):
        raise ControllerTickError("reconcile_contract", "reconcile response is missing summary", reconcile=response)
    conflicts = int(summary.get("conflicts") or 0)
    unsupported = int(summary.get("unsupported") or 0)
    if conflicts or unsupported:
        raise ControllerTickError(
            "reconcile",
            f"desired-state reconcile failed closed: conflicts={conflicts} unsupported={unsupported}",
            reconcile=response,
        )
    return response


def _connector_ids(reconcile: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    for result in reconcile.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        for action in result.get("actions") or []:
            if isinstance(action, Mapping) and action.get("connector_id") and action.get("status") != "skipped":
                values.add(str(action["connector_id"]))
    return sorted(values)


def read_actual_state(*, api_url: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
    return _request_json(
        api_url.rstrip("/") + "/api/source-ingest/controller/readback",
        timeout_seconds=timeout_seconds,
    )


def _validate_terminal_readback(
    *,
    reconcile: Mapping[str, Any],
    schedule: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> None:
    schedule_summary = schedule.get("summary")
    if not isinstance(schedule_summary, Mapping):
        raise ControllerTickError("schedule_contract", "scheduled tick response is missing summary", schedule=schedule)
    failed = int(schedule_summary.get("total_failed") or 0)
    if failed:
        raise ControllerTickError(
            "schedule",
            f"scheduled source tick reported {failed} failed connector(s)",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    wanted = set(_connector_ids(reconcile))
    actual_connectors = {
        str(item.get("connector_id")): item
        for item in actual.get("connectors") or []
        if isinstance(item, Mapping) and item.get("connector_id")
    }
    missing = sorted(wanted - set(actual_connectors))
    if missing:
        raise ControllerTickError(
            "actual_readback",
            f"authoritative connector readback is missing: {', '.join(missing)}",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    invalid: list[str] = []
    for connector_id in sorted(wanted):
        item = actual_connectors[connector_id]
        schedule_readback = item.get("schedule") if isinstance(item.get("schedule"), Mapping) else {}
        source_class = str((item.get("desired_state") or {}).get("source_class") or "")
        if not item.get("configured"):
            invalid.append(f"{connector_id}:connector_missing")
        if source_class != "live_push" and not schedule_readback.get("enabled"):
            invalid.append(f"{connector_id}:schedule_inactive")
        if source_class != "live_push":
            record = item.get("latest_source_record")
            health = item.get("source_health")
            if not isinstance(record, Mapping) or record.get("status") != "normalized":
                invalid.append(f"{connector_id}:normalized_record_missing")
            if not isinstance(health, Mapping) or health.get("status") != "ok":
                invalid.append(f"{connector_id}:source_health_not_ok")
    if invalid:
        raise ControllerTickError(
            "actual_readback",
            "terminal source readback failed closed: " + ", ".join(invalid),
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )


def build_loop_writer(*, dsn: str, state: ControllerState) -> LoopControllerWriterLike:
    if not dsn:
        raise ControllerTickError("controller_store", "DATABASE_URL is required for durable controller truth")
    module = importlib.import_module("services.loop-control")
    return module.LoopControllerWriter(
        dsn,
        tenant_id=state.tenant_id,
        environment=state.environment,
        controller_id=state.controller_id,
        controller_name=state.controller_name,
        deployment_sha=str(state.deployment.get("git_sha") or "unknown"),
    )


def _async(call: Any) -> Any:
    return asyncio.run(call)


@dataclass(frozen=True)
class ControllerConfig:
    api_url: str
    database_url: str
    interval_seconds: int
    max_concurrency: int
    max_ticks: int
    state_path: Path
    alive_path: Path | None
    timeout_seconds: float
    lease_seconds: int
    truth_level: str


def config_from_env() -> ControllerConfig:
    interval = _env_int("SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS", 60, minimum=1)
    alive = str(os.getenv("SOURCE_INGEST_CONTROLLER_ALIVE_PATH") or "").strip()
    truth_level = str(os.getenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL") or "scheduled_tick").strip()
    if truth_level not in {
        "seed_fixture",
        "snapshot_fallback",
        "registry_metadata",
        "scheduled_tick",
        "reconciled_live_proof",
        "proven_live_evidence",
    }:
        raise ValueError("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL is invalid")
    return ControllerConfig(
        api_url=str(os.getenv("SOURCE_INGEST_API_URL") or "http://127.0.0.1:8097"),
        database_url=str(os.getenv("DATABASE_URL") or ""),
        interval_seconds=interval,
        max_concurrency=_env_int("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", 2, minimum=1),
        max_ticks=_env_int("SOURCE_INGEST_CONTROLLER_MAX_TICKS", 0, minimum=0),
        state_path=Path(os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH") or "/tmp/pantheon/source-ingest/controller_state.json"),
        alive_path=Path(alive) if alive else None,
        timeout_seconds=float(os.getenv("SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS") or "30"),
        lease_seconds=_env_int("SOURCE_INGEST_CONTROLLER_LEASE_SECONDS", interval * 2, minimum=interval),
        truth_level=truth_level,
    )


def _new_state() -> ControllerState:
    controller_id = str(os.getenv("PANTHEON_CONTROLLER_ID") or f"source-ingestion-{socket.gethostname()}")
    return ControllerState(
        controller_id=controller_id,
        controller_name=str(os.getenv("PANTHEON_CONTROLLER_NAME") or "source-ingestion-controller"),
        environment=str(os.getenv("PANTHEON_ENV") or "dev"),
        tenant_id=str(os.getenv("PANTHEON_TENANT_ID") or "default"),
        deployment={
            "git_sha": str(os.getenv("GIT_SHA") or "unknown"),
            "image_digest": str(os.getenv("IMAGE_DIGEST") or "unknown"),
            "build_time": str(os.getenv("BUILD_TIME") or "unknown"),
            "deployment_id": str(os.getenv("SOURCE_INGEST_DEPLOYMENT_ID") or "unknown"),
        },
    )


def _write_alive(path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(utc_now(), encoding="utf-8")
    os.replace(temp, path)


def run_controller_tick(
    *,
    config: ControllerConfig,
    state: ControllerState,
    store: ControllerStateStore,
    writer: LoopControllerWriterLike,
) -> dict[str, Any]:
    desired_meta: dict[str, Any] = {}
    reconcile: dict[str, Any] = {}
    schedule: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    had_failures = state.consecutive_failures > 0
    state.record_tick_started()
    store.save(state)
    try:
        _async(
            writer.record_heartbeat(
                LOOP_ID,
                config.truth_level,
                desired_state_query="persona/data requirement snapshot",
                actual_state_query=config.api_url.rstrip("/") + "/api/source-ingest/controller/readback",
                lease_duration_seconds=config.lease_seconds,
                payload={"deployment": state.deployment, "state_sequence_no": state.sequence_no},
            )
        )
        _async(writer.record_tick(LOOP_ID, config.truth_level, payload={"state_sequence_no": state.sequence_no}))
        personas, desired_meta = load_desired_state(timeout_seconds=config.timeout_seconds)
        reconcile = reconcile_desired_state(
            api_url=config.api_url,
            personas=personas,
            timeout_seconds=config.timeout_seconds,
        )
        schedule = run_schedule_tick(
            api_url=config.api_url,
            max_concurrency=config.max_concurrency,
            timeout_seconds=config.timeout_seconds,
        )
        actual = read_actual_state(api_url=config.api_url, timeout_seconds=config.timeout_seconds)
        _validate_terminal_readback(reconcile=reconcile, schedule=schedule, actual=actual)
        state.record_success(
            desired_state=desired_meta,
            reconcile={"summary": reconcile.get("summary"), "connector_ids": _connector_ids(reconcile)},
            schedule={"summary": schedule.get("summary")},
            actual_readback={
                "captured_at": actual.get("captured_at"),
                "connector_count": actual.get("connector_count"),
                "source_record_count": actual.get("source_record_count"),
                "dlq_count": actual.get("dlq_count"),
            },
        )
        store.save(state)
        evidence_refs = [
            str(item.get("latest_source_record", {}).get("source_id"))
            for item in actual.get("connectors") or []
            if isinstance(item, Mapping) and isinstance(item.get("latest_source_record"), Mapping)
        ]
        _async(
            writer.record_success(
                LOOP_ID,
                config.truth_level,
                summary="desired state reconciled; scheduled ingestion terminal readback accepted",
                backlog=int(actual.get("frontier_backlog") or 0),
                lag=int(actual.get("max_lag_seconds") or 0),
                evidence_refs=[ref for ref in evidence_refs if ref and ref != "None"],
                payload={
                    "desired_state": desired_meta,
                    "reconcile_summary": reconcile.get("summary"),
                    "schedule_summary": schedule.get("summary"),
                    "actual_readback": state.actual_readback,
                },
            )
        )
        if had_failures:
            _async(
                writer.record_repair(
                    LOOP_ID,
                    "controller recovered after explicit failed/degraded tick",
                    config.truth_level,
                    evidence_refs=[ref for ref in evidence_refs if ref and ref != "None"],
                )
            )
        return {
            "status": "ok",
            "state_sequence_no": state.sequence_no,
            "desired_state": desired_meta,
            "reconcile_summary": reconcile.get("summary"),
            "schedule_summary": schedule.get("summary"),
            "actual_readback": state.actual_readback,
        }
    except Exception as exc:
        error = exc if isinstance(exc, ControllerTickError) else ControllerTickError("controller", f"{type(exc).__name__}: {exc}")
        context = error.context
        state.record_failure(
            stage=error.stage,
            reason=str(error),
            desired_state=desired_meta or None,
            reconcile=(context.get("reconcile") or reconcile or None),
            schedule=(context.get("schedule") or schedule or None),
            actual_readback=(context.get("actual_readback") or actual or None),
        )
        store.save(state)
        try:
            _async(
                writer.record_failure(
                    LOOP_ID,
                    f"{error.stage}: {error}",
                    config.truth_level,
                    dlq_count=int((actual or {}).get("dlq_count") or 0),
                    payload={
                        "failure_stage": error.stage,
                        "state_sequence_no": state.sequence_no,
                        "desired_state": desired_meta,
                    },
                )
            )
        except Exception:
            # The original failure (including lease/store rejection) remains in
            # the checksummed local state and must not be replaced by logging.
            pass
        raise error
    finally:
        _write_alive(config.alive_path)


def main() -> int:
    config = config_from_env()
    store = ControllerStateStore(config.state_path)
    state = store.load() or _new_state()
    startup_missed = state.record_startup_missed(interval_seconds=config.interval_seconds)
    store.save(state)
    print(
        _canonical_json(
            {
                "event": "startup",
                "controller_id": state.controller_id,
                "deployment": state.deployment,
                "startup_missed_ticks": startup_missed,
                "state_sequence_no": state.sequence_no,
            }
        ),
        flush=True,
    )
    writer = build_loop_writer(dsn=config.database_url, state=state)
    tick = 0
    while True:
        tick += 1
        try:
            result = run_controller_tick(config=config, state=state, store=store, writer=writer)
            print(_canonical_json({"tick": tick, **result}), flush=True)
        except Exception as exc:
            print(
                _canonical_json(
                    {
                        "tick": tick,
                        "status": "failed",
                        "stage": getattr(exc, "stage", "controller"),
                        "error": f"{type(exc).__name__}: {exc}",
                        "state_sequence_no": state.sequence_no,
                    }
                ),
                flush=True,
            )
        if config.max_ticks and tick >= config.max_ticks:
            return 0
        time.sleep(config.interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose and smoke tests.
    raise SystemExit(main())
