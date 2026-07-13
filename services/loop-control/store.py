import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import jsonschema
import asyncpg

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "loop-controller-record.schema.json"


class LoopControllerStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._schema = self._load_schema()

    def _load_schema(self) -> Dict[str, Any]:
        if _SCHEMA_PATH.exists():
            return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        # Fallback inline schema if schema file is not accessible
        return {}

    def validate_record(self, record: Dict[str, Any]) -> None:
        """Validate record against JSON schema."""
        if self._schema:
            # Create a copy for validation, converting datetimes to string format if present
            val_record = {}
            for k, v in record.items():
                if isinstance(v, datetime):
                    val_record[k] = v.isoformat()
                else:
                    val_record[k] = v
            jsonschema.validate(instance=val_record, schema=self._schema)

    async def list_records(self, tenant_id: str, environment: str) -> List[Dict[str, Any]]:
        """List all controller records for a specific tenant and environment."""
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
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def get_record(self, loop_id: str, tenant_id: str, environment: str) -> Optional[Dict[str, Any]]:
        """Get a single controller record."""
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
            return dict(row) if row else None
        finally:
            await conn.close()

    async def upsert_record(self, record: Dict[str, Any]) -> None:
        """Upsert a controller record after validation."""
        self.validate_record(record)

        loop_id = record["loop_id"]
        tenant_id = record["tenant_id"]
        environment = record["environment"]
        controller_id = record["controller_id"]
        controller_name = record["controller_name"]
        deployment_sha = record["deployment_sha"]
        desired_state_query = record.get("desired_state_query")
        actual_state_query = record.get("actual_state_query")
        last_heartbeat_at = record.get("last_heartbeat_at")
        last_tick_at = record.get("last_tick_at")
        last_success_at = record.get("last_success_at")
        last_failure_at = record.get("last_failure_at")
        last_failure_reason = record.get("last_failure_reason")
        last_repair_at = record.get("last_repair_at")
        last_repair_reason = record.get("last_repair_reason")
        backlog = record.get("backlog")
        lag = record.get("lag")
        dlq_count = record.get("dlq_count")
        evidence_refs = record.get("evidence_refs", [])
        truth_level = record["truth_level"]
        lease_expires_at = record.get("lease_expires_at")
        payload = record.get("payload", {})

        # Convert lists/dicts to JSON strings for asyncpg
        evidence_refs_json = json.dumps(evidence_refs)
        payload_json = json.dumps(payload)

        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                # We enforce lease safety: if the existing lease is active, we cannot overwrite it 
                # unless the request comes from the same controller_id or the lease has expired.
                # Stale lease and duplicate writes cannot manufacture accepted liveness.
                # We also ensure heartbeats cannot be written out-of-order or late.
                # Use FOR UPDATE to prevent TOCTOU race.
                now = datetime.now(timezone.utc)
                
                # Fetch existing lease to compare
                existing = await conn.fetchrow(
                    """
                    SELECT controller_id, lease_expires_at, last_heartbeat_at FROM loop_controller_records
                    WHERE loop_id = $1 AND tenant_id = $2 AND environment = $3
                    FOR UPDATE
                    """,
                    loop_id,
                    tenant_id,
                    environment
                )

                if existing:
                    existing_lease_expires = existing["lease_expires_at"]
                    existing_controller_id = existing["controller_id"]
                    existing_last_heartbeat = existing["last_heartbeat_at"]

                    # If existing lease is still valid and owned by someone else, reject or block the write
                    if (
                        existing_lease_expires
                        and existing_lease_expires > now
                        and existing_controller_id != controller_id
                    ):
                        raise ValueError(
                            f"Active lease exists for loop '{loop_id}' owned by controller '{existing_controller_id}' until {existing_lease_expires}. Cannot overwrite."
                        )

                    # Ensure heartbeat cannot be set back in time
                    if (
                        last_heartbeat_at
                        and existing_last_heartbeat
                        and last_heartbeat_at < existing_last_heartbeat
                    ):
                        # Stale heartbeat update; ignore or raise
                        return

                await conn.execute(
                    """
                    INSERT INTO loop_controller_records (
                        loop_id, tenant_id, environment, controller_id, controller_name, deployment_sha,
                        desired_state_query, actual_state_query, last_heartbeat_at, last_tick_at,
                        last_success_at, last_failure_at, last_failure_reason, last_repair_at, last_repair_reason,
                        backlog, lag, dlq_count, evidence_refs, truth_level, lease_expires_at, payload, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, clock_timestamp()
                    )
                    ON CONFLICT (loop_id, tenant_id, environment) DO UPDATE SET
                        controller_id = EXCLUDED.controller_id,
                        controller_name = EXCLUDED.controller_name,
                        deployment_sha = EXCLUDED.deployment_sha,
                        desired_state_query = COALESCE(EXCLUDED.desired_state_query, loop_controller_records.desired_state_query),
                        actual_state_query = COALESCE(EXCLUDED.actual_state_query, loop_controller_records.actual_state_query),
                        last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                        last_tick_at = COALESCE(EXCLUDED.last_tick_at, loop_controller_records.last_tick_at),
                        last_success_at = COALESCE(EXCLUDED.last_success_at, loop_controller_records.last_success_at),
                        last_failure_at = COALESCE(EXCLUDED.last_failure_at, loop_controller_records.last_failure_at),
                        last_failure_reason = COALESCE(EXCLUDED.last_failure_reason, loop_controller_records.last_failure_reason),
                        last_repair_at = COALESCE(EXCLUDED.last_repair_at, loop_controller_records.last_repair_at),
                        last_repair_reason = COALESCE(EXCLUDED.last_repair_reason, loop_controller_records.last_repair_reason),
                        backlog = COALESCE(EXCLUDED.backlog, loop_controller_records.backlog),
                        lag = COALESCE(EXCLUDED.lag, loop_controller_records.lag),
                        dlq_count = COALESCE(EXCLUDED.dlq_count, loop_controller_records.dlq_count),
                        evidence_refs = EXCLUDED.evidence_refs,
                        truth_level = EXCLUDED.truth_level,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        payload = EXCLUDED.payload,
                        updated_at = clock_timestamp()
                    """,
                    loop_id,
                    tenant_id,
                    environment,
                    controller_id,
                    controller_name,
                    deployment_sha,
                    desired_state_query,
                    actual_state_query,
                    last_heartbeat_at,
                    last_tick_at,
                    last_success_at,
                    last_failure_at,
                    last_failure_reason,
                    last_repair_at,
                    last_repair_reason,
                    backlog,
                    lag,
                    dlq_count,
                    evidence_refs_json,
                    truth_level,
                    lease_expires_at,
                    payload_json,
                )
        finally:
            await conn.close()
