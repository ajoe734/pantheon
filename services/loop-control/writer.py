import json
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from .store import LoopControllerStore
class LoopControllerWriter:
    def __init__(
        self,
        dsn: str,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        controller_id: Optional[str] = None,
        controller_name: Optional[str] = None,
        deployment_sha: Optional[str] = None,
    ):
        self.store = LoopControllerStore(dsn)
        self.tenant_id = tenant_id or os.environ.get("PANTHEON_TENANT_ID", "default")
        self.environment = environment or os.environ.get("PANTHEON_ENV", "dev")
        self.controller_id = controller_id or os.environ.get(
            "PANTHEON_CONTROLLER_ID",
            f"{socket.gethostname()}-{os.getpid()}"
        )
        self.controller_name = controller_name or os.environ.get(
            "PANTHEON_CONTROLLER_NAME",
            "unknown-controller"
        )
        self.deployment_sha = deployment_sha or os.environ.get(
            "PANTHEON_DEPLOYMENT_SHA",
            os.environ.get("GIT_SHA", "unknown")
        )

    async def _write_status(
        self,
        loop_id: str,
        truth_level: str,
        *,
        desired_state_query: Optional[str] = None,
        actual_state_query: Optional[str] = None,
        last_heartbeat_at: Optional[datetime] = None,
        last_tick_at: Optional[datetime] = None,
        last_success_at: Optional[datetime] = None,
        last_failure_at: Optional[datetime] = None,
        last_failure_reason: Optional[str] = None,
        last_repair_at: Optional[datetime] = None,
        last_repair_reason: Optional[str] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Internal helper to build and upsert the controller record."""
        now = datetime.now(timezone.utc)
        
        # Calculate lease expiry if duration provided
        lease_expires_at = None
        if lease_duration_seconds is not None:
            lease_expires_at = now + timedelta(seconds=lease_duration_seconds)

        # Get existing record to merge state if it exists
        existing = await self.store.get_record(loop_id, self.tenant_id, self.environment)
        
        # Merge logic: preserve existing fields if new fields are not provided
        merged_desired = desired_state_query or (existing.get("desired_state_query") if existing else None)
        merged_actual = actual_state_query or (existing.get("actual_state_query") if existing else None)
        merged_heartbeat = last_heartbeat_at or now
        merged_tick = last_tick_at or (existing.get("last_tick_at") if existing else None)
        merged_success = last_success_at or (existing.get("last_success_at") if existing else None)
        merged_failure = last_failure_at or (existing.get("last_failure_at") if existing else None)
        merged_failure_reason = last_failure_reason or (existing.get("last_failure_reason") if existing else None)
        merged_repair = last_repair_at or (existing.get("last_repair_at") if existing else None)
        merged_repair_reason = last_repair_reason or (existing.get("last_repair_reason") if existing else None)
        merged_backlog = backlog if backlog is not None else (existing.get("backlog") if existing else None)
        merged_lag = lag if lag is not None else (existing.get("lag") if existing else None)
        merged_dlq = dlq_count if dlq_count is not None else (existing.get("dlq_count") if existing else None)
        
        # Merge evidence refs
        merged_refs = list(evidence_refs or [])
        if existing and existing.get("evidence_refs"):
            existing_refs = existing["evidence_refs"]
            if isinstance(existing_refs, list):
                # Deduplicate and keep order
                for r in existing_refs:
                    if r not in merged_refs:
                        merged_refs.append(r)
            elif isinstance(existing_refs, str):
                try:
                    for r in json.loads(existing_refs):
                        if r not in merged_refs:
                            merged_refs.append(r)
                except Exception:
                    pass

        merged_payload = dict(payload or {})
        if existing and existing.get("payload"):
            existing_payload = existing["payload"]
            if isinstance(existing_payload, dict):
                # Shallow merge
                merged_payload = {**existing_payload, **merged_payload}
            elif isinstance(existing_payload, str):
                try:
                    merged_payload = {**json.loads(existing_payload), **merged_payload}
                except Exception:
                    pass

        record = {
            "loop_id": loop_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "controller_id": self.controller_id,
            "controller_name": self.controller_name,
            "deployment_sha": self.deployment_sha,
            "desired_state_query": merged_desired,
            "actual_state_query": merged_actual,
            "last_heartbeat_at": merged_heartbeat,
            "last_tick_at": merged_tick,
            "last_success_at": merged_success,
            "last_failure_at": merged_failure,
            "last_failure_reason": merged_failure_reason,
            "last_repair_at": merged_repair,
            "last_repair_reason": merged_repair_reason,
            "backlog": merged_backlog,
            "lag": merged_lag,
            "dlq_count": merged_dlq,
            "evidence_refs": merged_refs,
            "truth_level": truth_level,
            "lease_expires_at": lease_expires_at or (existing.get("lease_expires_at") if existing else None),
            "payload": merged_payload,
        }

        await self.store.upsert_record(record)

    async def record_heartbeat(
        self,
        loop_id: str,
        truth_level: str = "reconciled_live_proof",
        *,
        desired_state_query: Optional[str] = None,
        actual_state_query: Optional[str] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record standard heartbeat showing controller liveness."""
        await self._write_status(
            loop_id,
            truth_level,
            desired_state_query=desired_state_query,
            actual_state_query=actual_state_query,
            last_heartbeat_at=datetime.now(timezone.utc),
            backlog=backlog,
            lag=lag,
            dlq_count=dlq_count,
            evidence_refs=evidence_refs,
            lease_duration_seconds=lease_duration_seconds,
            payload=payload,
        )

    async def record_tick(
        self,
        loop_id: str,
        truth_level: str = "reconciled_live_proof",
        *,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a loop controller tick."""
        now = datetime.now(timezone.utc)
        await self._write_status(
            loop_id,
            truth_level,
            last_heartbeat_at=now,
            last_tick_at=now,
            backlog=backlog,
            lag=lag,
            evidence_refs=evidence_refs,
            payload=payload,
        )

    async def record_success(
        self,
        loop_id: str,
        truth_level: str = "reconciled_live_proof",
        *,
        summary: Optional[str] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a successful loop execution pass."""
        now = datetime.now(timezone.utc)
        extra_payload = {"last_success_summary": summary} if summary else {}
        if payload:
            extra_payload.update(payload)
        await self._write_status(
            loop_id,
            truth_level,
            last_heartbeat_at=now,
            last_success_at=now,
            backlog=backlog,
            lag=lag,
            evidence_refs=evidence_refs,
            payload=extra_payload,
        )

    async def record_failure(
        self,
        loop_id: str,
        reason: str,
        truth_level: str = "reconciled_live_proof",
        *,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a failed loop execution pass."""
        now = datetime.now(timezone.utc)
        await self._write_status(
            loop_id,
            truth_level,
            last_heartbeat_at=now,
            last_failure_at=now,
            last_failure_reason=reason,
            dlq_count=dlq_count,
            evidence_refs=evidence_refs,
            payload=payload,
        )

    async def record_repair(
        self,
        loop_id: str,
        reason: str,
        truth_level: str = "reconciled_live_proof",
        *,
        evidence_refs: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a drift repair action by the controller."""
        now = datetime.now(timezone.utc)
        await self._write_status(
            loop_id,
            truth_level,
            last_heartbeat_at=now,
            last_repair_at=now,
            last_repair_reason=reason,
            evidence_refs=evidence_refs,
            payload=payload,
        )
