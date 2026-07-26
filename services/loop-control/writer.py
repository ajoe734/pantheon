from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .store import LoopControllerStore


_DEFAULT_LEASE_SECONDS = 60


class LoopControllerWriter:
    """Fenced partial-update SDK shared by the twelve canonical loops."""

    def __init__(
        self,
        dsn: str,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        controller_id: Optional[str] = None,
        controller_name: Optional[str] = None,
        deployment_sha: Optional[str] = None,
        lease_token: Optional[str] = None,
        lease_duration_seconds: Optional[int] = None,
    ):
        self.store = LoopControllerStore(dsn)
        self.tenant_id = tenant_id or os.environ.get(
            "PANTHEON_TENANT_ID", "default"
        )
        self.environment = environment or os.environ.get("PANTHEON_ENV", "dev")
        self.controller_id = controller_id or os.environ.get(
            "PANTHEON_CONTROLLER_ID",
            f"{socket.gethostname()}-{os.getpid()}",
        )
        self.controller_name = controller_name or os.environ.get(
            "PANTHEON_CONTROLLER_NAME",
            "unknown-controller",
        )
        self.deployment_sha = deployment_sha or os.environ.get(
            "PANTHEON_DEPLOYMENT_SHA",
            os.environ.get("GIT_SHA", "unknown"),
        )
        self.lease_token = (
            lease_token
            or os.environ.get("PANTHEON_CONTROLLER_LEASE_TOKEN")
            or uuid.uuid4().hex
        )
        configured_lease = (
            lease_duration_seconds
            if lease_duration_seconds is not None
            else int(
                os.environ.get(
                    "PANTHEON_CONTROLLER_LEASE_SECONDS",
                    str(_DEFAULT_LEASE_SECONDS),
                )
            )
        )
        if configured_lease <= 0:
            raise ValueError("lease_duration_seconds must be positive")
        self.lease_duration_seconds = configured_lease

    async def _write_status(
        self,
        loop_id: str,
        truth_level: str,
        *,
        desired_state_query: Optional[str] = None,
        actual_state_query: Optional[str] = None,
        desired_state: Optional[Dict[str, Any]] = None,
        downstream_actual_state: Optional[Dict[str, Any]] = None,
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
        """Atomically merge one status patch and renew this writer's lease."""

        now = datetime.now(timezone.utc)
        lease_seconds = (
            self.lease_duration_seconds
            if lease_duration_seconds is None
            else lease_duration_seconds
        )
        if lease_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive")

        record: Dict[str, Any] = {
            "loop_id": loop_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "controller_id": self.controller_id,
            "controller_name": self.controller_name,
            "deployment_sha": self.deployment_sha,
            "last_heartbeat_at": last_heartbeat_at or now,
            "truth_level": truth_level,
            "lease_token": self.lease_token,
            "lease_expires_at": now + timedelta(seconds=lease_seconds),
        }
        optional_values = {
            "desired_state_query": desired_state_query,
            "actual_state_query": actual_state_query,
            "desired_state": desired_state,
            "downstream_actual_state": downstream_actual_state,
            "last_tick_at": last_tick_at,
            "last_success_at": last_success_at,
            "last_failure_at": last_failure_at,
            "last_failure_reason": last_failure_reason,
            "last_repair_at": last_repair_at,
            "last_repair_reason": last_repair_reason,
            "backlog": backlog,
            "lag": lag,
            "dlq_count": dlq_count,
            "evidence_refs": evidence_refs,
            "payload": payload,
        }
        record.update(
            {
                key: value
                for key, value in optional_values.items()
                if value is not None
            }
        )
        await self.store.upsert_record(record)

    async def record_heartbeat(
        self,
        loop_id: str,
        truth_level: str = "reconciled_live_proof",
        *,
        desired_state_query: Optional[str] = None,
        actual_state_query: Optional[str] = None,
        desired_state: Optional[Dict[str, Any]] = None,
        downstream_actual_state: Optional[Dict[str, Any]] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._write_status(
            loop_id,
            truth_level,
            desired_state_query=desired_state_query,
            actual_state_query=actual_state_query,
            desired_state=desired_state,
            downstream_actual_state=downstream_actual_state,
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
        desired_state: Optional[Dict[str, Any]] = None,
        downstream_actual_state: Optional[Dict[str, Any]] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._write_status(
            loop_id,
            truth_level,
            desired_state=desired_state,
            downstream_actual_state=downstream_actual_state,
            last_heartbeat_at=now,
            last_tick_at=now,
            backlog=backlog,
            lag=lag,
            dlq_count=dlq_count,
            evidence_refs=evidence_refs,
            lease_duration_seconds=lease_duration_seconds,
            payload=payload,
        )

    async def record_success(
        self,
        loop_id: str,
        truth_level: str = "reconciled_live_proof",
        *,
        summary: Optional[str] = None,
        desired_state: Optional[Dict[str, Any]] = None,
        downstream_actual_state: Optional[Dict[str, Any]] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        extra_payload = {"last_success_summary": summary} if summary else {}
        if payload:
            extra_payload.update(payload)
        await self._write_status(
            loop_id,
            truth_level,
            desired_state=desired_state,
            downstream_actual_state=downstream_actual_state,
            last_heartbeat_at=now,
            last_success_at=now,
            backlog=backlog,
            lag=lag,
            dlq_count=dlq_count,
            evidence_refs=evidence_refs,
            lease_duration_seconds=lease_duration_seconds,
            payload=extra_payload or None,
        )

    async def record_failure(
        self,
        loop_id: str,
        reason: str,
        truth_level: str = "reconciled_live_proof",
        *,
        desired_state: Optional[Dict[str, Any]] = None,
        downstream_actual_state: Optional[Dict[str, Any]] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._write_status(
            loop_id,
            truth_level,
            desired_state=desired_state,
            downstream_actual_state=downstream_actual_state,
            last_heartbeat_at=now,
            last_failure_at=now,
            last_failure_reason=reason,
            backlog=backlog,
            lag=lag,
            dlq_count=dlq_count,
            evidence_refs=evidence_refs,
            lease_duration_seconds=lease_duration_seconds,
            payload=payload,
        )

    async def record_repair(
        self,
        loop_id: str,
        reason: str,
        truth_level: str = "reconciled_live_proof",
        *,
        desired_state: Optional[Dict[str, Any]] = None,
        downstream_actual_state: Optional[Dict[str, Any]] = None,
        backlog: Optional[int] = None,
        lag: Optional[int] = None,
        dlq_count: Optional[int] = None,
        evidence_refs: Optional[List[str]] = None,
        lease_duration_seconds: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self._write_status(
            loop_id,
            truth_level,
            desired_state=desired_state,
            downstream_actual_state=downstream_actual_state,
            last_heartbeat_at=now,
            last_repair_at=now,
            last_repair_reason=reason,
            backlog=backlog,
            lag=lag,
            dlq_count=dlq_count,
            evidence_refs=evidence_refs,
            lease_duration_seconds=lease_duration_seconds,
            payload=payload,
        )
