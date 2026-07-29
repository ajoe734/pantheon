from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import asyncpg
import jsonschema

from .conformance import (
    CONTROLLER_RECORD_FIELDS,
    TRUTH_LEVEL_RANK,
    assert_controller_record_conforms,
)


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "loop-controller-record.schema.json"
)
_CONTROLLER_META_KEY = "_loop_controller_record"
_MONOTONIC_TIMESTAMPS = (
    "last_heartbeat_at",
    "last_tick_at",
    "last_success_at",
    "last_failure_at",
    "last_repair_at",
)
_OPTIONAL_COPY_FIELDS = (
    "desired_state_query",
    "actual_state_query",
    "desired_state",
    "downstream_actual_state",
    "backlog",
    "lag",
    "dlq_count",
)


class ControllerLeaseConflict(ValueError):
    """The row is currently fenced by another controller generation."""


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return list(decoded) if isinstance(decoded, list) else []
    return []


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(
        f"Object of type {value.__class__.__name__} is not JSON serializable"
    )


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _newer_value(existing: Any, incoming: Any) -> Any:
    if incoming is None:
        return existing
    if existing is None:
        return incoming
    existing_at = _as_utc(existing)
    incoming_at = _as_utc(incoming)
    if existing_at is None:
        return incoming
    if incoming_at is None or incoming_at < existing_at:
        return existing
    return incoming


def _dedupe_refs(existing: Any, incoming: Any) -> List[str]:
    merged: List[str] = []
    for raw in [*_json_list(existing), *_json_list(incoming)]:
        clean = str(raw or "").strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


class LoopControllerStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._schema = self._load_schema()

    def _load_schema(self) -> Dict[str, Any]:
        if _SCHEMA_PATH.exists():
            return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        return {}

    def validate_record(self, record: Dict[str, Any]) -> None:
        """Validate one complete controller row against shared semantics."""

        val_record: Dict[str, Any] = {}
        for key, value in record.items():
            if isinstance(value, datetime):
                val_record[key] = value.isoformat()
            elif isinstance(value, Mapping):
                val_record[key] = {
                    nested_key: (
                        nested_value.isoformat()
                        if isinstance(nested_value, datetime)
                        else nested_value
                    )
                    for nested_key, nested_value in value.items()
                }
            else:
                val_record[key] = value
        if self._schema:
            jsonschema.validate(instance=val_record, schema=self._schema)
        assert_controller_record_conforms(val_record)

    @staticmethod
    def _normalize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = dict(row)
        normalized["evidence_refs"] = _json_list(normalized.get("evidence_refs"))
        payload = _json_object(normalized.get("payload"))
        controller_meta = _json_object(payload.pop(_CONTROLLER_META_KEY, {}))
        normalized["payload"] = payload
        normalized["lease_token"] = controller_meta.get("lease_token")
        normalized["desired_state"] = controller_meta.get("desired_state")
        normalized["downstream_actual_state"] = controller_meta.get(
            "downstream_actual_state"
        )
        return normalized

    async def list_records(
        self, tenant_id: str, environment: str
    ) -> List[Dict[str, Any]]:
        """List controller records for exactly one tenant and environment."""

        if not str(tenant_id or "").strip() or not str(environment or "").strip():
            raise ValueError("tenant_id and environment are required")
        conn = await asyncpg.connect(self.dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT * FROM loop_controller_records
                WHERE tenant_id = $1 AND environment = $2
                ORDER BY updated_at DESC
                """,
                tenant_id,
                environment,
            )
            return [self._normalize_row(row) for row in rows]
        finally:
            await conn.close()

    async def get_record(
        self, loop_id: str, tenant_id: str, environment: str
    ) -> Optional[Dict[str, Any]]:
        """Get one controller record within its full isolation key."""

        if not all(
            str(value or "").strip()
            for value in (loop_id, tenant_id, environment)
        ):
            raise ValueError("loop_id, tenant_id, and environment are required")
        conn = await asyncpg.connect(self.dsn)
        try:
            row = await conn.fetchrow(
                """
                SELECT * FROM loop_controller_records
                WHERE loop_id = $1 AND tenant_id = $2 AND environment = $3
                """,
                loop_id,
                tenant_id,
                environment,
            )
            return self._normalize_row(row) if row else None
        finally:
            await conn.close()

    @staticmethod
    def _empty_record(record: Mapping[str, Any]) -> Dict[str, Any]:
        merged = {field: None for field in CONTROLLER_RECORD_FIELDS}
        merged.update(
            {
                "loop_id": record.get("loop_id"),
                "tenant_id": record.get("tenant_id"),
                "environment": record.get("environment"),
                "controller_id": record.get("controller_id"),
                "controller_name": record.get("controller_name"),
                "deployment_sha": record.get("deployment_sha"),
                "evidence_refs": [],
                "payload": {},
                "truth_level": record.get("truth_level"),
            }
        )
        return merged

    @staticmethod
    def _merge_record(
        existing: Optional[Mapping[str, Any]],
        incoming: Mapping[str, Any],
        *,
        now: datetime,
    ) -> Dict[str, Any]:
        if existing is not None:
            normalized_existing = LoopControllerStore._normalize_row(existing)
            current = {
                field: normalized_existing.get(field)
                for field in CONTROLLER_RECORD_FIELDS
            }
        else:
            current = LoopControllerStore._empty_record(incoming)
        active_lease = (
            _as_utc(current.get("lease_expires_at")) is not None
            and _as_utc(current.get("lease_expires_at")) > now
        )
        current_token = str(current.get("lease_token") or "").strip()
        incoming_token = str(incoming.get("lease_token") or "").strip()
        same_controller = current.get("controller_id") == incoming.get(
            "controller_id"
        )
        same_fence = bool(current_token and incoming_token == current_token)

        if existing is not None and active_lease:
            legacy_same_owner_claim = same_controller and not current_token
            if not same_fence and not legacy_same_owner_claim:
                owner = current.get("controller_id")
                raise ControllerLeaseConflict(
                    f"Active lease exists for loop {incoming.get('loop_id')!r}; "
                    f"fenced owner is controller {owner!r}, so the stale or foreign "
                    "writer was rejected."
                )

        incoming_lease_expires = _as_utc(incoming.get("lease_expires_at"))
        if incoming_lease_expires is None or incoming_lease_expires <= now:
            raise ControllerLeaseConflict(
                "controller writes must renew a future fenced lease"
            )
        if not incoming_token:
            raise ControllerLeaseConflict(
                "controller writes require a non-empty lease fencing token"
            )

        merged = dict(current)
        for field in (
            "loop_id",
            "tenant_id",
            "environment",
            "controller_id",
            "controller_name",
            "deployment_sha",
        ):
            if incoming.get(field) is not None:
                merged[field] = incoming[field]

        for field in _OPTIONAL_COPY_FIELDS:
            if field in incoming and incoming[field] is not None:
                merged[field] = incoming[field]

        for field in _MONOTONIC_TIMESTAMPS:
            merged[field] = _newer_value(current.get(field), incoming.get(field))

        incoming_failure_at = incoming.get("last_failure_at")
        if (
            incoming_failure_at is not None
            and merged["last_failure_at"] == incoming_failure_at
        ):
            merged["last_failure_reason"] = incoming.get("last_failure_reason")

        incoming_repair_at = incoming.get("last_repair_at")
        if (
            incoming_repair_at is not None
            and merged["last_repair_at"] == incoming_repair_at
        ):
            merged["last_repair_reason"] = incoming.get("last_repair_reason")

        merged["evidence_refs"] = _dedupe_refs(
            current.get("evidence_refs"),
            incoming.get("evidence_refs"),
        )
        payload = _json_object(current.get("payload"))
        payload.update(_json_object(incoming.get("payload")))
        merged["payload"] = payload

        current_truth = str(current.get("truth_level") or "")
        incoming_truth = str(incoming.get("truth_level") or "")
        if TRUTH_LEVEL_RANK.get(incoming_truth, -1) >= TRUTH_LEVEL_RANK.get(
            current_truth, -1
        ):
            merged["truth_level"] = incoming_truth

        merged["lease_token"] = incoming_token
        merged["lease_expires_at"] = incoming_lease_expires
        return merged

    async def upsert_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically merge a fenced controller patch and return the durable row.

        A transaction-scoped advisory lock serializes both first inserts and
        updates for one ``(tenant, environment, loop)`` key.  The merge occurs
        only after the current row is locked, so partial status writes cannot
        replay a stale pre-read snapshot over newer fields.
        """

        for field in (
            "loop_id",
            "tenant_id",
            "environment",
            "controller_id",
            "controller_name",
            "deployment_sha",
            "truth_level",
        ):
            if record.get(field) is None:
                raise ValueError(f"controller patch requires {field}")

        loop_id = str(record["loop_id"])
        tenant_id = str(record["tenant_id"])
        environment = str(record["environment"])
        lock_key = "\x1f".join((tenant_id, environment, loop_id))

        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    lock_key,
                )
                existing = await conn.fetchrow(
                    """
                    SELECT * FROM loop_controller_records
                    WHERE loop_id = $1 AND tenant_id = $2 AND environment = $3
                    FOR UPDATE
                    """,
                    loop_id,
                    tenant_id,
                    environment,
                )
                merged = self._merge_record(
                    existing,
                    record,
                    now=datetime.now(timezone.utc),
                )
                self.validate_record(merged)

                persisted_payload = dict(merged["payload"])
                persisted_payload[_CONTROLLER_META_KEY] = {
                    "lease_token": merged["lease_token"],
                    "desired_state": merged["desired_state"],
                    "downstream_actual_state": merged[
                        "downstream_actual_state"
                    ],
                }
                row = await conn.fetchrow(
                    """
                    INSERT INTO loop_controller_records (
                        loop_id, tenant_id, environment, controller_id,
                        controller_name, deployment_sha, desired_state_query,
                        actual_state_query, last_heartbeat_at, last_tick_at,
                        last_success_at, last_failure_at, last_failure_reason,
                        last_repair_at, last_repair_reason, backlog, lag,
                        dlq_count, evidence_refs, truth_level,
                        lease_expires_at, payload, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, $17, $18, $19::jsonb,
                        $20, $21, $22::jsonb, clock_timestamp()
                    )
                    ON CONFLICT (loop_id, tenant_id, environment) DO UPDATE SET
                        controller_id = EXCLUDED.controller_id,
                        controller_name = EXCLUDED.controller_name,
                        deployment_sha = EXCLUDED.deployment_sha,
                        desired_state_query = EXCLUDED.desired_state_query,
                        actual_state_query = EXCLUDED.actual_state_query,
                        last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                        last_tick_at = EXCLUDED.last_tick_at,
                        last_success_at = EXCLUDED.last_success_at,
                        last_failure_at = EXCLUDED.last_failure_at,
                        last_failure_reason = EXCLUDED.last_failure_reason,
                        last_repair_at = EXCLUDED.last_repair_at,
                        last_repair_reason = EXCLUDED.last_repair_reason,
                        backlog = EXCLUDED.backlog,
                        lag = EXCLUDED.lag,
                        dlq_count = EXCLUDED.dlq_count,
                        evidence_refs = EXCLUDED.evidence_refs,
                        truth_level = EXCLUDED.truth_level,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        payload = EXCLUDED.payload,
                        updated_at = clock_timestamp()
                    RETURNING *
                    """,
                    merged["loop_id"],
                    merged["tenant_id"],
                    merged["environment"],
                    merged["controller_id"],
                    merged["controller_name"],
                    merged["deployment_sha"],
                    merged["desired_state_query"],
                    merged["actual_state_query"],
                    merged["last_heartbeat_at"],
                    merged["last_tick_at"],
                    merged["last_success_at"],
                    merged["last_failure_at"],
                    merged["last_failure_reason"],
                    merged["last_repair_at"],
                    merged["last_repair_reason"],
                    merged["backlog"],
                    merged["lag"],
                    merged["dlq_count"],
                    json.dumps(merged["evidence_refs"]),
                    merged["truth_level"],
                    merged["lease_expires_at"],
                    json.dumps(persisted_payload, default=_json_default),
                )
                return self._normalize_row(row)
        finally:
            await conn.close()
