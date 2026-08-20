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
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from .controller_state import (
    ControllerState,
    ControllerStateError,
    ControllerStateStore,
    parse_utc,
    summarize_actual_readback,
    utc_now,
)
from .controller_auth import load_controller_token
from .scheduler_worker import run_tick as run_schedule_tick


DEFAULT_DESIRED_STATE_PATH = Path(__file__).with_name("default_desired_state.json")
LOOP_ID = "source_ingestion"
NON_TERMINAL_TRUTH_LEVEL = "scheduled_tick"
RECONCILE_ONLY_MODE = "reconcile_only"
RECONCILE_AND_PULL_MODE = "reconcile_and_pull"
CONTROLLER_MODES = frozenset({RECONCILE_ONLY_MODE, RECONCILE_AND_PULL_MODE})
CADENCE_SOURCE_AGE_LIMIT_SECONDS = {
    "realtime": 300,
    "minutely": 300,
    "hourly": 7200,
    "daily": 864000,
    "weekly": 1209600,
}


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


def _env_csv(name: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value
            for value in (item.strip() for item in str(os.getenv(name) or "").split(","))
            if value
        )
    )


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
    persona_id = str(persona.get("persona_id") or "").strip()
    if not persona_id:
        raise ControllerTickError("desired_state_validate", "persona desired state is missing persona_id")
    if "required_data_sources" not in persona:
        raise ControllerTickError(
            "desired_state_validate",
            f"persona {persona_id} must explicitly contain required_data_sources",
        )
    requirements = persona["required_data_sources"]
    if not isinstance(requirements, list):
        raise ControllerTickError(
            "desired_state_validate",
            f"persona {persona_id} required_data_sources must be a list",
        )
    normalized_requirements: list[dict[str, Any]] = []
    seen_requirements: set[str] = set()
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, Mapping):
            raise ControllerTickError(
                "desired_state_validate",
                f"persona {persona_id} required_data_sources[{index}] must be an object",
            )
        normalized = dict(requirement)
        key = _canonical_json(normalized)
        if key in seen_requirements:
            raise ControllerTickError(
                "desired_state_validate",
                f"persona {persona_id} contains a duplicate data requirement",
            )
        seen_requirements.add(key)
        normalized_requirements.append(normalized)
    persona["required_data_sources"] = normalized_requirements
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
    personas_list: list[dict[str, Any]] = []
    seen_persona_ids: set[str] = set()
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            raise ControllerTickError(
                "desired_state_validate",
                f"desired-state personas[{index}] must be an object",
            )
        persona = _normalize_persona(item)
        persona_id = str(persona["persona_id"])
        if persona_id in seen_persona_ids:
            raise ControllerTickError("desired_state_validate", f"duplicate persona_id in desired state: {persona_id}")
        seen_persona_ids.add(persona_id)
        personas_list.append(persona)
    personas = tuple(personas_list)
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
    desired_state_sha256: str | None = None,
    source_authority: str | None = None,
    controller_token: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    expected_sha256 = desired_state_sha256 or _digest(list(personas))
    response = _request_json(
        api_url.rstrip("/") + "/api/source-ingest/persona-source-provisioning/reconcile",
        method="POST",
        payload={
            "personas": list(personas),
            "authoritative_snapshot": True,
            "desired_state_sha256": expected_sha256,
            "source_authority": source_authority,
            "dry_run": False,
        },
        bearer_token=controller_token,
        timeout_seconds=timeout_seconds,
    )
    summary = response.get("summary")
    if not isinstance(summary, Mapping):
        raise ControllerTickError("reconcile_contract", "reconcile response is missing summary", reconcile=response)
    if response.get("desired_state_sha256") != expected_sha256:
        raise ControllerTickError(
            "reconcile_contract",
            "reconcile response desired-state digest does not match the admitted snapshot",
            reconcile=response,
        )
    if not isinstance(response.get("pre_readback"), Mapping) or not isinstance(response.get("post_readback"), Mapping):
        raise ControllerTickError(
            "reconcile_contract",
            "reconcile response is missing authoritative pre/post readback",
            reconcile=response,
        )
    accepted_snapshot = response.get("accepted_requirement_snapshot")
    post_snapshot = response.get("post_readback", {}).get("requirement_snapshot")
    if (
        not isinstance(accepted_snapshot, Mapping)
        or accepted_snapshot.get("desired_state_sha256") != expected_sha256
        or accepted_snapshot.get("authoritative") is not True
        or not isinstance(post_snapshot, Mapping)
        or post_snapshot.get("sequence") != accepted_snapshot.get("sequence")
        or post_snapshot.get("desired_state_sha256") != expected_sha256
    ):
        raise ControllerTickError(
            "reconcile_contract",
            "reconcile response did not durably accept the authoritative requirement snapshot",
            reconcile=response,
        )
    conflicts = int(summary.get("conflicts") or 0)
    unsupported = int(summary.get("unsupported") or 0)
    if conflicts or unsupported:
        raise ControllerTickError(
            "reconcile",
            f"desired-state reconcile failed closed: conflicts={conflicts} unsupported={unsupported}",
            reconcile=response,
        )
    requirement_count = sum(
        len(persona.get("required_data_sources") or [])
        for persona in personas
        if isinstance(persona, Mapping)
    )
    live_binding_count = sum(
        1
        for persona in personas
        if isinstance(persona, Mapping)
        for requirement in (persona.get("required_data_sources") or [])
        if isinstance(requirement, Mapping) and requirement.get("source_class") in {"live_pull", "live_push"}
    )
    if requirement_count and not live_binding_count:
        raise ControllerTickError(
            "reconcile",
            "authoritative desired state contains no live source binding; seed-only state is not terminal proof",
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


def _mutated_connector_ids(reconcile: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    for result in reconcile.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        for action in result.get("actions") or []:
            if (
                isinstance(action, Mapping)
                and action.get("connector_id")
                and action.get("status") == "mutated"
            ):
                values.add(str(action["connector_id"]))
    return sorted(values)


def read_actual_state(*, api_url: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
    return _request_json(
        api_url.rstrip("/") + "/api/source-ingest/controller/readback",
        timeout_seconds=timeout_seconds,
    )


def _trusted_unresolved_dlq_count(actual: Mapping[str, Any]) -> int | None:
    expected_statuses = {
        "pending",
        "replayed",
        "duplicate_skipped",
        "replay_failed",
        "schema_rejected",
    }
    status_counts = actual.get("dlq_status_counts")
    pending_count = actual.get("pending_dlq_count")
    unresolved_count = actual.get("unresolved_dlq_count")
    total_count = actual.get("dlq_count")
    if (
        not isinstance(status_counts, Mapping)
        or set(status_counts) != expected_statuses
        or any(type(value) is not int or value < 0 for value in status_counts.values())
        or type(pending_count) is not int
        or type(unresolved_count) is not int
        or type(total_count) is not int
        or pending_count < 0
        or unresolved_count < 0
        or total_count < 0
        or status_counts["pending"] != pending_count
        or sum(status_counts.values()) != total_count
        or unresolved_count
        != sum(status_counts[status] for status in ("pending", "replay_failed", "schema_rejected"))
    ):
        return None
    return unresolved_count


def _validate_terminal_readback(
    *,
    reconcile: Mapping[str, Any],
    schedule: Mapping[str, Any],
    actual: Mapping[str, Any],
    expected_controller_id: str,
    expected_sequence_no: int,
    expected_deployment: Mapping[str, Any],
) -> None:
    if actual.get("schema_version") != "source_ingest_controller_readback.v1":
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback schema is missing or unsupported",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    captured_at = parse_utc(str(actual.get("captured_at") or ""))
    if captured_at is None or (datetime.now(timezone.utc) - captured_at).total_seconds() > 300:
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback is stale",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    controller_state = actual.get("controller_state")
    deployment = controller_state.get("deployment") if isinstance(controller_state, Mapping) else None
    identity_fields = ("git_sha", "image_digest", "build_time", "deployment_id", "runtime_instance_id")
    if (
        not isinstance(controller_state, Mapping)
        or controller_state.get("controller_id") != expected_controller_id
        or int(controller_state.get("sequence_no") or -1) != expected_sequence_no
        or not isinstance(deployment, Mapping)
        or deployment.get("identity_complete") is not True
        or any(str(deployment.get(field) or "").strip() in {"", "unknown", "unresolved", "local-dev"} for field in identity_fields)
        or any(deployment.get(field) != expected_deployment.get(field) for field in identity_fields)
    ):
        raise ControllerTickError(
            "actual_readback",
            "controller deployment identity is missing from authoritative readback",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    requirement_snapshot = actual.get("requirement_snapshot")
    if (
        not isinstance(requirement_snapshot, Mapping)
        or requirement_snapshot.get("desired_state_sha256") != reconcile.get("desired_state_sha256")
        or requirement_snapshot.get("authoritative") is not True
    ):
        raise ControllerTickError(
            "actual_readback",
            "authoritative requirement snapshot is missing or contradicted by actual readback",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
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
    pending_dlq_count = actual.get("pending_dlq_count")
    if type(pending_dlq_count) is not int or pending_dlq_count < 0:
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback is missing a valid pending_dlq_count",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    dlq_count = actual.get("dlq_count")
    unresolved_dlq_count = actual.get("unresolved_dlq_count")
    dlq_status_counts = actual.get("dlq_status_counts")
    expected_dlq_statuses = {
        "pending",
        "replayed",
        "duplicate_skipped",
        "replay_failed",
        "schema_rejected",
    }
    if (
        type(dlq_count) is not int
        or dlq_count < pending_dlq_count
        or not isinstance(dlq_status_counts, Mapping)
        or set(dlq_status_counts) != expected_dlq_statuses
        or type(dlq_status_counts.get("pending")) is not int
        or dlq_status_counts.get("pending") != pending_dlq_count
        or any(type(value) is not int or value < 0 for value in dlq_status_counts.values())
        or sum(dlq_status_counts.values()) != dlq_count
        or type(unresolved_dlq_count) is not int
        or unresolved_dlq_count < pending_dlq_count
        or unresolved_dlq_count
        != sum(
            dlq_status_counts[status]
            for status in ("pending", "replay_failed", "schema_rejected")
        )
    ):
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback has contradictory dead-letter counts",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    if unresolved_dlq_count:
        raise ControllerTickError(
            "actual_readback",
            f"authoritative source readback has {unresolved_dlq_count} unresolved dead-letter entrie(s)",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    frontier_backlog = actual.get("frontier_backlog")
    if type(frontier_backlog) is not int or frontier_backlog < 0:
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback is missing a valid frontier_backlog",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    if frontier_backlog:
        raise ControllerTickError(
            "actual_readback",
            f"authoritative source readback has {frontier_backlog} unresolved frontier item(s)",
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )
    wanted_requirements: dict[str, list[dict[str, Any]]] = {}
    for result in reconcile.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        for action in result.get("actions") or []:
            if not isinstance(action, Mapping) or not action.get("connector_id") or action.get("status") == "skipped":
                continue
            wanted_requirements.setdefault(str(action["connector_id"]), []).append(dict(action))
    wanted = set(wanted_requirements)
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
        desired_state = item.get("desired_state") if isinstance(item.get("desired_state"), Mapping) else {}
        source_class = str(desired_state.get("source_class") or "")
        policy_gates = {
            str(gate)
            for gate in (desired_state.get("policy_gates") or [])
            if str(gate)
        }
        gate_results = (
            desired_state.get("policy_gate_results")
            if isinstance(desired_state.get("policy_gate_results"), Mapping)
            else {}
        )
        expected_datasets = {str(action.get("dataset") or "") for action in wanted_requirements[connector_id]}
        if not item.get("configured"):
            invalid.append(f"{connector_id}:connector_missing")
        if str(desired_state.get("dataset") or "") not in expected_datasets:
            invalid.append(f"{connector_id}:desired_dataset_mismatch")
        for gate in sorted(policy_gates):
            result = gate_results.get(gate) if isinstance(gate_results, Mapping) else None
            if not isinstance(result, Mapping) or result.get("passed") is not True:
                invalid.append(f"{connector_id}:policy_gate_not_admitted[{gate}]")
        if source_class != "live_push" and not schedule_readback.get("enabled"):
            invalid.append(f"{connector_id}:schedule_inactive")
        if source_class != "live_push":
            record = item.get("latest_source_record")
            health = item.get("source_health")
            freshness = item.get("freshness") if isinstance(item.get("freshness"), Mapping) else {}
            if freshness.get("status") != "fresh":
                invalid.append(f"{connector_id}:freshness_not_accepted")
            staleness_seconds = freshness.get("staleness_seconds")
            interval_seconds = int(schedule_readback.get("interval_seconds") or 0)
            if staleness_seconds is None or interval_seconds <= 0 or int(staleness_seconds) > interval_seconds:
                invalid.append(f"{connector_id}:freshness_outside_cadence")
            if not isinstance(record, Mapping) or record.get("status") != "normalized":
                invalid.append(f"{connector_id}:normalized_record_missing")
            else:
                if record.get("connector_id") != connector_id:
                    invalid.append(f"{connector_id}:record_connector_mismatch")
                if not record.get("source_id") or not record.get("content_ref") or not record.get("trace_id"):
                    invalid.append(f"{connector_id}:record_identity_incomplete")
                provenance = record.get("provenance") if isinstance(record.get("provenance"), Mapping) else {}
                required_provenance = {
                    "provider",
                    "dataset",
                    "available_time",
                    "api_endpoint",
                    "access_scope",
                    "license_scope",
                    "schema_hash",
                    "source_ingest_run_id",
                }
                missing_provenance = sorted(key for key in required_provenance if provenance.get(key) in (None, "", []))
                if missing_provenance:
                    invalid.append(f"{connector_id}:provenance_missing[{','.join(missing_provenance)}]")
                if str(provenance.get("dataset") or "") not in expected_datasets:
                    invalid.append(f"{connector_id}:record_dataset_mismatch")
                available_at = None
                try:
                    available_at = parse_utc(str(provenance.get("available_time") or ""))
                except ControllerStateError:
                    pass
                cadence = str(desired_state.get("cadence") or "")
                max_source_age = (
                    86400
                    if "require_freshness_within_1d" in policy_gates
                    else CADENCE_SOURCE_AGE_LIMIT_SECONDS.get(cadence)
                )
                if (
                    available_at is None
                    or max_source_age is None
                    or (datetime.now(timezone.utc) - available_at).total_seconds() > max_source_age
                    or (available_at - datetime.now(timezone.utc)).total_seconds() > 300
                ):
                    invalid.append(f"{connector_id}:source_data_stale")
            connector_payload = item.get("connector") if isinstance(item.get("connector"), Mapping) else {}
            if "no_live_capital" in policy_gates and connector_payload.get("auth_type") == "broker_ref":
                invalid.append(f"{connector_id}:no_live_capital_gate_failed")
            if "public-source-only" in policy_gates and (
                connector_payload.get("auth_type") != "none" or connector_payload.get("secret_ref_id")
            ):
                invalid.append(f"{connector_id}:public_source_gate_failed")
            if not isinstance(health, Mapping) or health.get("status") != "ok":
                invalid.append(f"{connector_id}:source_health_not_ok")
            else:
                if health.get("source_id") != connector_id or not health.get("last_success_at"):
                    invalid.append(f"{connector_id}:source_health_identity_incomplete")
                health_metadata = health.get("metadata") if isinstance(health.get("metadata"), Mapping) else {}
                latest_run = freshness.get("latest_run") if isinstance(freshness.get("latest_run"), Mapping) else {}
                if latest_run.get("status") != "completed":
                    invalid.append(f"{connector_id}:latest_run_not_completed")
                if health_metadata.get("last_ingest_run_id") != latest_run.get("ingest_run_id"):
                    invalid.append(f"{connector_id}:health_run_mismatch")
                if isinstance(record, Mapping):
                    provenance = record.get("provenance") if isinstance(record.get("provenance"), Mapping) else {}
                    if provenance.get("source_ingest_run_id") != latest_run.get("ingest_run_id"):
                        invalid.append(f"{connector_id}:record_run_mismatch")
    if invalid:
        raise ControllerTickError(
            "actual_readback",
            "terminal source readback failed closed: " + ", ".join(invalid),
            reconcile=reconcile,
            schedule=schedule,
            actual_readback=actual,
        )


def _validate_due_state_readback(
    *,
    reconcile: Mapping[str, Any],
    pre_actual: Mapping[str, Any],
    actual: Mapping[str, Any],
    expected_controller_id: str,
    expected_sequence_no: int,
    expected_deployment: Mapping[str, Any],
) -> None:
    """Accept connector/schedule convergence without claiming provider proof.

    This is the always-safe half of the source loop.  It proves that admitted
    persona requirements became configured connectors and enabled schedules,
    while also proving that the reconciliation tick did not enqueue work,
    execute a provider, or append SourceRecords.  Provider execution remains a
    separate, explicitly governed bounded operation.
    """

    if actual.get("schema_version") != "source_ingest_controller_readback.v1":
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback schema is missing or unsupported",
            reconcile=reconcile,
            actual_readback=actual,
        )
    captured_at = parse_utc(str(actual.get("captured_at") or ""))
    if captured_at is None or (datetime.now(timezone.utc) - captured_at).total_seconds() > 300:
        raise ControllerTickError(
            "actual_readback",
            "authoritative source readback is stale",
            reconcile=reconcile,
            actual_readback=actual,
        )
    controller_state = actual.get("controller_state")
    deployment = controller_state.get("deployment") if isinstance(controller_state, Mapping) else None
    identity_fields = ("git_sha", "image_digest", "build_time", "deployment_id", "runtime_instance_id")
    if (
        not isinstance(controller_state, Mapping)
        or controller_state.get("controller_id") != expected_controller_id
        or int(controller_state.get("sequence_no") or -1) != expected_sequence_no
        or not isinstance(deployment, Mapping)
        or deployment.get("identity_complete") is not True
        or any(str(deployment.get(field) or "").strip() in {"", "unknown", "unresolved", "local-dev"} for field in identity_fields)
        or any(deployment.get(field) != expected_deployment.get(field) for field in identity_fields)
    ):
        raise ControllerTickError(
            "actual_readback",
            "controller deployment identity is missing from authoritative readback",
            reconcile=reconcile,
            actual_readback=actual,
        )
    requirement_snapshot = actual.get("requirement_snapshot")
    if (
        not isinstance(requirement_snapshot, Mapping)
        or requirement_snapshot.get("desired_state_sha256") != reconcile.get("desired_state_sha256")
        or requirement_snapshot.get("authoritative") is not True
    ):
        raise ControllerTickError(
            "actual_readback",
            "authoritative requirement snapshot is missing or contradicted by actual readback",
            reconcile=reconcile,
            actual_readback=actual,
        )

    immutable_execution_counts = ("source_record_count", "dlq_count", "frontier_backlog")
    changed_execution_counts = [
        field
        for field in immutable_execution_counts
        if type(pre_actual.get(field)) is not int
        or type(actual.get(field)) is not int
        or pre_actual.get(field) != actual.get(field)
    ]
    if changed_execution_counts:
        raise ControllerTickError(
            "provider_boundary",
            "reconcile-only tick changed provider execution state: " + ", ".join(changed_execution_counts),
            reconcile=reconcile,
            actual_readback=actual,
        )

    wanted_requirements: dict[str, list[dict[str, Any]]] = {}
    for result in reconcile.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        for action in result.get("actions") or []:
            if not isinstance(action, Mapping) or not action.get("connector_id") or action.get("status") == "skipped":
                continue
            wanted_requirements.setdefault(str(action["connector_id"]), []).append(dict(action))
    actual_connectors = {
        str(item.get("connector_id")): item
        for item in actual.get("connectors") or []
        if isinstance(item, Mapping) and item.get("connector_id")
    }
    invalid: list[str] = []
    for connector_id, requirements in sorted(wanted_requirements.items()):
        item = actual_connectors.get(connector_id)
        if item is None:
            invalid.append(f"{connector_id}:connector_missing")
            continue
        desired_state = item.get("desired_state") if isinstance(item.get("desired_state"), Mapping) else {}
        schedule = item.get("schedule") if isinstance(item.get("schedule"), Mapping) else {}
        expected_datasets = {str(action.get("dataset") or "") for action in requirements}
        if item.get("configured") is not True:
            invalid.append(f"{connector_id}:connector_unconfigured")
        if str(desired_state.get("dataset") or "") not in expected_datasets:
            invalid.append(f"{connector_id}:desired_dataset_mismatch")
        source_class = str(desired_state.get("source_class") or "")
        if source_class != "live_push" and (
            schedule.get("enabled") is not True or int(schedule.get("interval_seconds") or 0) <= 0
        ):
            invalid.append(f"{connector_id}:schedule_inactive")
        gate_results = (
            desired_state.get("policy_gate_results")
            if isinstance(desired_state.get("policy_gate_results"), Mapping)
            else {}
        )
        for gate in desired_state.get("policy_gates") or []:
            result = gate_results.get(str(gate)) if isinstance(gate_results, Mapping) else None
            if not isinstance(result, Mapping) or result.get("passed") is not True:
                invalid.append(f"{connector_id}:policy_gate_not_admitted[{gate}]")
    if invalid:
        raise ControllerTickError(
            "actual_readback",
            "due-state reconciliation failed closed: " + ", ".join(invalid),
            reconcile=reconcile,
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
    controller_token: str
    mode: str = RECONCILE_AND_PULL_MODE
    force_connector_ids: tuple[str, ...] = ()
    exclusive_connector_ids: tuple[str, ...] = ()


def config_from_env() -> ControllerConfig:
    interval = _env_int("SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS", 60, minimum=1)
    alive = str(os.getenv("SOURCE_INGEST_CONTROLLER_ALIVE_PATH") or "").strip()
    mode = str(os.getenv("SOURCE_INGEST_CONTROLLER_MODE") or RECONCILE_ONLY_MODE).strip()
    if mode not in CONTROLLER_MODES:
        raise ValueError("SOURCE_INGEST_CONTROLLER_MODE is invalid")
    default_truth = NON_TERMINAL_TRUTH_LEVEL if mode == RECONCILE_ONLY_MODE else "reconciled_live_proof"
    truth_level = str(os.getenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL") or default_truth).strip()
    if truth_level not in {"scheduled_tick", "reconciled_live_proof"}:
        raise ValueError("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL is invalid")
    if mode == RECONCILE_ONLY_MODE and truth_level != NON_TERMINAL_TRUTH_LEVEL:
        raise ValueError("reconcile_only mode must use scheduled_tick truth")
    max_ticks = _env_int("SOURCE_INGEST_CONTROLLER_MAX_TICKS", 0, minimum=0)
    force_connector_ids = _env_csv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS")
    exclusive_connector_ids = _env_csv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS")
    if mode == RECONCILE_AND_PULL_MODE and not 1 <= max_ticks <= 24:
        raise ValueError("reconcile_and_pull mode requires SOURCE_INGEST_CONTROLLER_MAX_TICKS between 1 and 24")
    if mode == RECONCILE_ONLY_MODE and (force_connector_ids or exclusive_connector_ids):
        raise ValueError("reconcile_only mode must not select provider connector execution")
    return ControllerConfig(
        api_url=str(os.getenv("SOURCE_INGEST_API_URL") or "http://127.0.0.1:8097"),
        database_url=str(os.getenv("DATABASE_URL") or ""),
        interval_seconds=interval,
        max_concurrency=_env_int("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", 2, minimum=1),
        max_ticks=max_ticks,
        state_path=Path(os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH") or "/tmp/pantheon/source-ingest/controller_state.json"),
        alive_path=Path(alive) if alive else None,
        timeout_seconds=float(os.getenv("SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS") or "30"),
        lease_seconds=_env_int("SOURCE_INGEST_CONTROLLER_LEASE_SECONDS", interval * 2, minimum=interval),
        truth_level=truth_level,
        controller_token=load_controller_token(
            token_path=os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE")
            or "/data/source-ingest/controller_token",
            create=False,
        ),
        mode=mode,
        force_connector_ids=force_connector_ids,
        exclusive_connector_ids=exclusive_connector_ids,
    )


def run_controller_once(
    *,
    config: ControllerConfig | None = None,
    mode: str = RECONCILE_AND_PULL_MODE,
    exclusive_connector_ids: Sequence[str] | None = None,
    force_connector_ids: Sequence[str] | None = None,
    api_url: str | None = None,
    state_path: Path | str | None = None,
    controller_token: str | None = None,
    database_url: str | None = None,
    timeout_seconds: float = 30.0,
    max_concurrency: int = 2,
    truth_level: str | None = None,
    writer: LoopControllerWriterLike | None = None,
) -> dict[str, Any]:
    """Execute exactly one bounded controller tick and return terminal readback summary."""
    if config is None:
        if mode not in CONTROLLER_MODES:
            raise ValueError(f"invalid controller mode: {mode}")
        resolved_truth = truth_level or (
            NON_TERMINAL_TRUTH_LEVEL if mode == RECONCILE_ONLY_MODE else "reconciled_live_proof"
        )
        if resolved_truth not in {"scheduled_tick", "reconciled_live_proof"}:
            raise ValueError("invalid truth_level")
        if mode == RECONCILE_ONLY_MODE and resolved_truth != NON_TERMINAL_TRUTH_LEVEL:
            raise ValueError("reconcile_only mode must use scheduled_tick truth")
        exclusive_tuple = tuple(
            dict.fromkeys(str(c).strip() for c in (exclusive_connector_ids or ()) if str(c).strip())
        )
        force_tuple = tuple(
            dict.fromkeys(str(c).strip() for c in (force_connector_ids or ()) if str(c).strip())
        )
        if mode == RECONCILE_ONLY_MODE and (exclusive_tuple or force_tuple):
            raise ValueError("reconcile_only mode must not select provider connector execution")
        resolved_state_path = (
            Path(state_path)
            if state_path
            else Path(
                os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH")
                or "/tmp/pantheon/source-ingest/controller_state.json"
            )
        )
        resolved_api_url = str(api_url or os.getenv("SOURCE_INGEST_API_URL") or "http://127.0.0.1:8097")
        resolved_db_url = str(database_url if database_url is not None else (os.getenv("DATABASE_URL") or ""))
        resolved_token = (
            controller_token
            if controller_token is not None
            else load_controller_token(
                token_path=os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE")
                or "/data/source-ingest/controller_token",
                create=False,
            )
        )
        config = ControllerConfig(
            api_url=resolved_api_url,
            database_url=resolved_db_url,
            interval_seconds=60,
            max_concurrency=max(1, min(4, int(max_concurrency))),
            max_ticks=1,
            state_path=resolved_state_path,
            alive_path=None,
            timeout_seconds=float(timeout_seconds),
            lease_seconds=120,
            truth_level=resolved_truth,
            controller_token=resolved_token,
            mode=mode,
            force_connector_ids=force_tuple,
            exclusive_connector_ids=exclusive_tuple,
        )

    store = ControllerStateStore(config.state_path)
    loaded_state = store.load()
    state = refresh_runtime_identity(loaded_state) if loaded_state is not None else _new_state()
    store.save(state)
    active_writer = writer if writer is not None else build_loop_writer(dsn=config.database_url, state=state)
    return run_controller_tick(config=config, state=state, store=store, writer=active_writer)


def _runtime_deployment() -> dict[str, Any]:
    runtime_instance_id = socket.gethostname()
    git_sha = str(os.getenv("GIT_SHA") or "unknown")
    package_root = Path(__file__).parent
    artifact_paths = sorted(package_root.rglob("*.py")) + [
        package_root / "requirements.txt",
        DEFAULT_DESIRED_STATE_PATH,
        package_root.parent / "control-plane" / "persona" / "required_data_sources.schema.json",
    ]
    artifact_hasher = sha256()
    for path in artifact_paths:
        artifact_hasher.update(str(path.relative_to(package_root.parent)).encode("utf-8"))
        artifact_hasher.update(path.read_bytes())
    artifact_digest = "application-sha256:" + artifact_hasher.hexdigest()
    image_digest = str(os.getenv("IMAGE_DIGEST") or artifact_digest)
    artifact_mtime = max(path.stat().st_mtime for path in artifact_paths)
    build_time = str(
        os.getenv("BUILD_TIME")
        or datetime.fromtimestamp(artifact_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return {
        "git_sha": git_sha,
        "image_digest": image_digest,
        "build_time": build_time,
        "deployment_id": str(
            os.getenv("SOURCE_INGEST_DEPLOYMENT_ID")
            or f"container:{runtime_instance_id}"
        ),
        "runtime_instance_id": runtime_instance_id,
        "identity_observed_at": utc_now(),
        "identity_complete": (
            git_sha not in {"", "unknown", "local-dev"}
            and image_digest not in {"", "unknown", "unresolved"}
        ),
    }


def _runtime_controller_id() -> str:
    base = str(os.getenv("PANTHEON_CONTROLLER_ID") or f"source-ingestion-{socket.gethostname()}")
    generation = str(os.getenv("SOURCE_INGEST_CONTROLLER_GENERATION_ID") or uuid4().hex[:12])
    return f"{base}:{generation}"


def _new_state() -> ControllerState:
    return ControllerState(
        controller_id=_runtime_controller_id(),
        controller_name=str(os.getenv("PANTHEON_CONTROLLER_NAME") or "source-ingestion-controller"),
        environment=str(os.getenv("PANTHEON_ENV") or "dev"),
        tenant_id=str(os.getenv("PANTHEON_TENANT_ID") or "default"),
        deployment=_runtime_deployment(),
    )


def refresh_runtime_identity(state: ControllerState) -> ControllerState:
    """Fence a restarted process and refresh exact deployment identity."""

    environment = str(os.getenv("PANTHEON_ENV") or "dev")
    tenant_id = str(os.getenv("PANTHEON_TENANT_ID") or "default")
    if state.environment != environment or state.tenant_id != tenant_id:
        raise ControllerStateError(
            "persisted controller state tenant/environment does not match this runtime"
        )
    state.controller_id = _runtime_controller_id()
    state.controller_name = str(os.getenv("PANTHEON_CONTROLLER_NAME") or "source-ingestion-controller")
    state.deployment = _runtime_deployment()
    state.started_at = utc_now()
    return state


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
    pre_actual: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    had_failures = state.consecutive_failures > 0
    state.record_tick_started()
    store.save(state)
    try:
        _async(
            writer.record_heartbeat(
                LOOP_ID,
                NON_TERMINAL_TRUTH_LEVEL,
                desired_state_query="persona/data requirement snapshot",
                actual_state_query=config.api_url.rstrip("/") + "/api/source-ingest/controller/readback",
                lease_duration_seconds=config.lease_seconds,
                payload={"deployment": state.deployment, "state_sequence_no": state.sequence_no},
            )
        )
        _async(writer.record_tick(LOOP_ID, NON_TERMINAL_TRUTH_LEVEL, payload={"state_sequence_no": state.sequence_no}))
        pre_actual = read_actual_state(api_url=config.api_url, timeout_seconds=config.timeout_seconds)
        personas, desired_meta = load_desired_state(timeout_seconds=config.timeout_seconds)
        reconcile = reconcile_desired_state(
            api_url=config.api_url,
            personas=personas,
            desired_state_sha256=str(desired_meta["sha256"]),
            source_authority=str(desired_meta["authority"]),
            controller_token=config.controller_token,
            timeout_seconds=config.timeout_seconds,
        )
        if config.mode == RECONCILE_ONLY_MODE:
            schedule = {
                "mode": RECONCILE_ONLY_MODE,
                "provider_egress_attempted": False,
                "summary": {
                    "total_reconciled_connectors": len(_connector_ids(reconcile)),
                    "total_provider_pulls": 0,
                },
            }
            actual = read_actual_state(api_url=config.api_url, timeout_seconds=config.timeout_seconds)
            _validate_due_state_readback(
                reconcile=reconcile,
                pre_actual=pre_actual,
                actual=actual,
                expected_controller_id=state.controller_id,
                expected_sequence_no=state.sequence_no,
                expected_deployment=state.deployment,
            )
        else:
            exclusive_connector_ids = sorted(set(config.exclusive_connector_ids))
            forced_connector_ids = (
                exclusive_connector_ids
                if exclusive_connector_ids
                else sorted(set(_mutated_connector_ids(reconcile)) | set(config.force_connector_ids))
            )
            schedule = run_schedule_tick(
                api_url=config.api_url,
                max_concurrency=config.max_concurrency,
                timeout_seconds=config.timeout_seconds,
                force_connector_ids=forced_connector_ids,
                exclusive_connector_ids=exclusive_connector_ids,
                controller_token=config.controller_token,
            )
            actual = read_actual_state(api_url=config.api_url, timeout_seconds=config.timeout_seconds)
            _validate_terminal_readback(
                reconcile=reconcile,
                schedule=schedule,
                actual=actual,
                expected_controller_id=state.controller_id,
                expected_sequence_no=state.sequence_no,
                expected_deployment=state.deployment,
            )
        wanted_connector_ids = set(_connector_ids(reconcile))
        evidence_refs = [
            str(item.get("latest_source_record", {}).get("source_id"))
            for item in actual.get("connectors") or []
            if (
                isinstance(item, Mapping)
                and str(item.get("connector_id") or "") in wanted_connector_ids
                and isinstance(item.get("latest_source_record"), Mapping)
            )
        ]
        accepted_actual = summarize_actual_readback(actual)
        accepted_actual["pre_captured_at"] = pre_actual.get("captured_at")
        _async(
            writer.record_success(
                LOOP_ID,
                config.truth_level,
                summary=(
                    "desired connector and schedule state reconciled; provider egress not attempted"
                    if config.mode == RECONCILE_ONLY_MODE
                    else "desired state reconciled; scheduled ingestion terminal readback accepted"
                ),
                backlog=int(actual.get("frontier_backlog") or 0),
                lag=int(actual.get("max_lag_seconds") or 0),
                dlq_count=int(actual.get("unresolved_dlq_count") or 0),
                evidence_refs=[ref for ref in evidence_refs if ref and ref != "None"],
                payload={
                    "controller_mode": config.mode,
                    "provider_egress_attempted": config.mode == RECONCILE_AND_PULL_MODE,
                    "desired_state": desired_meta,
                    "reconcile_summary": reconcile.get("summary"),
                    "schedule_summary": schedule.get("summary"),
                    "actual_readback": accepted_actual,
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
        state.record_success(
            desired_state=desired_meta,
            reconcile={"summary": reconcile.get("summary"), "connector_ids": sorted(wanted_connector_ids)},
            schedule={
                "mode": config.mode,
                "provider_egress_attempted": config.mode == RECONCILE_AND_PULL_MODE,
                "summary": schedule.get("summary"),
            },
            actual_readback=accepted_actual,
        )
        store.save(state)
        return {
            "status": "ok",
            "controller_mode": config.mode,
            "provider_egress_attempted": config.mode == RECONCILE_AND_PULL_MODE,
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
                    NON_TERMINAL_TRUTH_LEVEL,
                    dlq_count=_trusted_unresolved_dlq_count(actual),
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
    loaded_state = store.load()
    state = refresh_runtime_identity(loaded_state) if loaded_state is not None else _new_state()
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
    last_tick_failed = False
    while True:
        tick += 1
        try:
            result = run_controller_tick(config=config, state=state, store=store, writer=writer)
            last_tick_failed = False
            print(_canonical_json({"tick": tick, **result}), flush=True)
        except Exception as exc:
            last_tick_failed = True
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
            return 1 if last_tick_failed else 0
        time.sleep(config.interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose and smoke tests.
    raise SystemExit(main())
