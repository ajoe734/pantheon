"""Durable Agora Persona interaction background worker."""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
from .runner import drain_interaction_outbox, run_selected_persona_interaction
from .store import InteractionLifecycleStore

logger = logging.getLogger("agora.interaction.worker")


LOOP_ID = "agora_interaction_evidence"
CONTROLLER_NAME = "agora-interaction-worker"
DESIRED_SOURCE = "agora.interaction_lifecycle_store.claim"
ACTUAL_SOURCE = "agora.interaction_worker.outcomes"
# Liveness is refreshed at least this often (the brief caps it at 300s).
MAX_LOOP_HEARTBEAT_SECONDS = 300
DEFAULT_LOOP_HEARTBEAT_SECONDS = 120
# Lease covers the longest gap between two writes, never a request timeout.
DEFAULT_LOOP_LEASE_SECONDS = 900


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def loop_heartbeat_interval_seconds() -> int:
    raw = os.getenv("PANTHEON_AGORA_LOOP_HEARTBEAT_SECONDS", str(DEFAULT_LOOP_HEARTBEAT_SECONDS))
    return max(1, min(int(raw), MAX_LOOP_HEARTBEAT_SECONDS))


def loop_lease_seconds(heartbeat_interval: Optional[int] = None) -> int:
    """Configured controller lease (PANTHEON_AGORA_LOOP_LEASE_SECONDS, default 900s)."""
    interval = heartbeat_interval or loop_heartbeat_interval_seconds()
    configured = int(os.getenv("PANTHEON_AGORA_LOOP_LEASE_SECONDS", str(DEFAULT_LOOP_LEASE_SECONDS)))
    return max(configured, 2 * interval)


def build_loop_writer(*, lease_duration_seconds: Optional[int] = None) -> Any:
    """One writer per process; None (disabled) when no DSN is configured."""
    dsn = str(os.getenv("PANTHEON_LOOP_CONTROL_DSN") or os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        return None
    module = importlib.import_module("services.loop-control")
    return module.LoopControllerWriter(
        dsn,
        tenant_id=str(os.getenv("PANTHEON_TENANT_ID") or "default"),
        environment=str(os.getenv("PANTHEON_ENV") or "dev"),
        controller_id=str(
            os.getenv("PANTHEON_CONTROLLER_ID")
            or f"{CONTROLLER_NAME}-{socket.gethostname()}-{os.getpid()}"
        ),
        controller_name=CONTROLLER_NAME,
        deployment_sha=str(os.getenv("PANTHEON_DEPLOYMENT_SHA") or os.getenv("GIT_SHA") or "unknown"),
        lease_duration_seconds=lease_duration_seconds or loop_lease_seconds(),
    )


def build_loop_truth(
    *, worker_id: str, claimed: int, outcomes: Dict[str, int], interaction_ids: List[str],
    outbox_drained: int, checked_at: str,
) -> Dict[str, Any]:
    """Derive writer fields only from values the tick already read."""
    bad = outcomes.get("degraded", 0) + outcomes.get("failed", 0)
    return {
        "desired_state": {
            "present": claimed > 0,
            "source": DESIRED_SOURCE,
            "checked_at": checked_at,
            "summary": f"{claimed} interaction(s) claimed",
        },
        # Provider failure is downstream state, not a controller failure.
        "downstream_actual_state": {
            "status": "degraded" if bad else "ready",
            "source": ACTUAL_SOURCE,
            "checked_at": checked_at,
            "summary": (
                f"completed={outcomes.get('completed', 0)} degraded={outcomes.get('degraded', 0)} "
                f"failed={outcomes.get('failed', 0)} outbox_drained={outbox_drained}"
            ),
        },
        "evidence_refs": [
            f"agora-interaction://worker-ticks/{worker_id}/{checked_at}",
            *[f"agora-interaction://interactions/{i}" for i in interaction_ids],
        ],
    }


class _InteractionHeartbeat:
    """Background context manager that periodically renews the interaction lease while work runs."""

    def __init__(
        self,
        store: InteractionLifecycleStore,
        interaction_id: str,
        lease_owner: str,
        lease_duration_seconds: int = 300,
        liveness: Optional[Callable[[], None]] = None,
        liveness_interval: float = float(MAX_LOOP_HEARTBEAT_SECONDS),
    ) -> None:
        self.store = store
        self.interaction_id = interaction_id
        self.lease_owner = lease_owner
        self.lease_duration_seconds = lease_duration_seconds
        self.liveness = liveness
        self.liveness_interval = liveness_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "_InteractionHeartbeat":
        interval = max(0.5, self.lease_duration_seconds / 3.0)

        def _loop() -> None:
            tick = min(interval, self.liveness_interval) if self.liveness else interval
            next_lease = time.monotonic() + interval
            next_live = time.monotonic() + self.liveness_interval
            while not self._stop_event.wait(timeout=tick):
                now = time.monotonic()
                if now >= next_lease:
                    next_lease = now + interval
                    try:
                        self.store.heartbeat_interaction(
                            self.interaction_id,
                            lease_owner=self.lease_owner,
                            lease_duration_seconds=self.lease_duration_seconds,
                        )
                    except Exception as exc:
                        logger.debug("Heartbeat renewal error on %s: %s", self.interaction_id, exc)
                if self.liveness is not None and now >= next_live:
                    next_live = now + self.liveness_interval
                    try:
                        self.liveness()
                    except Exception as exc:
                        logger.debug("Loop liveness error on %s: %s", self.interaction_id, exc)

        self._thread = threading.Thread(
            target=_loop,
            daemon=True,
            name=f"interaction-heartbeat-{self.interaction_id[:8]}",
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)


class AgoraInteractionWorker:
    """Independent background worker for processing queued Agora Persona interactions."""

    def __init__(
        self,
        *,
        lifecycle_store: Optional[InteractionLifecycleStore] = None,
        workshop_store: Optional[Any] = None,
        read_store: Optional[Any] = None,
        client_factory: Optional[Callable[[], OpenClawOpsClient]] = None,
        proposal_store: Optional[Any] = None,
        research_store: Optional[Any] = None,
        dataset_store: Optional[Any] = None,
        worker_id: Optional[str] = None,
        lease_duration_seconds: int = 300,
        store: Optional[Any] = None,
        loop_writer: Optional[Any] = None,
        loop_heartbeat_seconds: Optional[int] = None,
    ) -> None:
        self.loop_writer = loop_writer
        self.loop_heartbeat_seconds = (
            loop_heartbeat_seconds if loop_heartbeat_seconds is not None
            else loop_heartbeat_interval_seconds()
        )
        self._tick: Optional[Dict[str, Any]] = None
        self._last_loop_write = 0.0
        self.lifecycle_store = lifecycle_store or store
        self.workshop_store = workshop_store
        self.read_store = read_store
        self.client_factory = client_factory
        self.proposal_store = proposal_store
        self.dataset_store = dataset_store
        self.research_store = research_store
        self.worker_id = worker_id or os.getenv(
            "PANTHEON_AGORA_WORKER_ID", f"agora-worker-{uuid.uuid4().hex[:12]}"
        )
        self.lease_duration_seconds = int(
            os.getenv("PANTHEON_AGORA_LEASE_DURATION_SECONDS", str(lease_duration_seconds))
        )
        self._metrics: Dict[str, Any] = {
            "admissions_processed": 0,
            "completed_count": 0,
            "degraded_count": 0,
            "failed_count": 0,
            "lease_recoveries": 0,
            "total_execution_seconds": 0.0,
            "last_processed_at": None,
        }
        self._lock = threading.Lock()

    @property
    def metrics(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._metrics)

    def drain_research_outbox(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> int:
        """Deprecated: research execution belongs to the authoritative Research service owner."""
        return 0

    def drain_outbox(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> int:
        total = 0
        if self.lifecycle_store is not None and self.workshop_store is not None:
            try:
                total += drain_interaction_outbox(self.lifecycle_store, self.workshop_store)
            except Exception as exc:
                logger.warning("Failed draining interaction outbox: %s", exc)
        total += self.drain_research_outbox(tenant_id=tenant_id, user_id=user_id, limit=limit)
        return total

    def claim_and_process_one(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Claim next queued or expired-lease interaction and execute it."""
        if self.lifecycle_store is None:
            return None
        resource = self.lifecycle_store.claim_interaction(
            lease_owner=self.worker_id,
            lease_duration_seconds=self.lease_duration_seconds,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if resource is None:
            return None

        # If it was previously running with an expired lease, record recovery
        if resource.get("status") == "running" and resource.get("lease_owner") != self.worker_id:
            with self._lock:
                self._metrics["lease_recoveries"] += 1

        if self._tick is not None:
            self._tick["claimed"] += 1
            self._tick["ids"].append(str(resource.get("interaction_id")))
        return self._execute_and_finalize(resource)

    # -- loop controller truth -------------------------------------------------

    def _loop_write(self, method: str, *args: Any, **kwargs: Any) -> None:
        if self.loop_writer is None:
            return
        try:
            asyncio.run(getattr(self.loop_writer, method)(LOOP_ID, *args, **kwargs))
            self._last_loop_write = time.monotonic()
        except Exception as exc:
            logger.warning("Failed to write loop controller truth: %s", exc)

    def _loop_liveness(self) -> None:
        """Liveness only: omits desired/actual so no older checked_at is re-stamped."""
        self._loop_write(
            "record_heartbeat",
            evidence_refs=[f"agora-interaction://worker-heartbeats/{self.worker_id}/{_utc_now()}"],
        )

    def _publish_tick(self, tick: Dict[str, Any], outbox_drained: int) -> None:
        if self.loop_writer is None:
            return
        idle = tick["claimed"] == 0
        # Idle 1s polls must not write every poll.
        if idle and self._last_loop_write and (
            time.monotonic() - self._last_loop_write < self.loop_heartbeat_seconds
        ):
            return
        truth = build_loop_truth(
            worker_id=self.worker_id,
            claimed=tick["claimed"],
            outcomes=tick["outcomes"],
            interaction_ids=tick["ids"],
            outbox_drained=outbox_drained,
            checked_at=_utc_now(),
        )
        if idle:
            self._loop_write("record_tick", **truth)
        else:
            self._loop_write(
                "record_success",
                summary=f"Processed {tick['claimed']} interaction(s)",
                **truth,
            )

    def process_interaction(
        self,
        interaction_id: str,
        tenant_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Explicitly claim and process a specific interaction (e.g. for testing or targeted dispatch)."""
        resource = self.lifecycle_store.claim_interaction(
            lease_owner=self.worker_id,
            lease_duration_seconds=self.lease_duration_seconds,
            interaction_id=interaction_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if resource is None:
            return self.lifecycle_store.get(interaction_id, tenant_id, user_id)

        return self._execute_and_finalize(resource)

    def _execute_and_finalize(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.monotonic()
        interaction_id = str(resource["interaction_id"])
        tenant_id = str(resource["tenant_id"])
        user_id = str(resource["owner_user_id"])
        workshop_id = str(resource["workshop_id"])

        binding = resource.get("_context_binding") or {}
        advice_environment = resource.get("_legacy_environment") or binding.get("advice_environment")
        if not advice_environment or advice_environment not in {"analysis", "research", "shadow", "paper"}:
            advice_environment = "research"

        human = resource.get("human_request") or {}
        snapshot = resource.get("context_snapshot") or {}
        mode = str(human.get("mode") or snapshot.get("initial_mode") or "consult")
        topic = str(human.get("request_text") or "")
        operator_id = str(human.get("operator_id") or user_id)
        submitted_at = human.get("submitted_at")
        trace_id = str(resource.get("trace_id") or f"trace-{interaction_id}")
        selected_personas = list(snapshot.get("selected_persona_ids") or [])
        if not selected_personas and resource.get("participants"):
            selected_personas = [
                str(p.get("persona_id")) for p in resource["participants"] if p.get("persona_id")
            ]

        raw_context_refs = snapshot.get("context_refs") or []
        context_refs = [
            {
                "type": item.get("kind") or item.get("type"),
                "id": item.get("id"),
                "version_id": item.get("version") or item.get("version_id"),
            }
            for item in raw_context_refs
        ]

        attempt = int(resource.get("retry_count", 0))

        try:
            with _InteractionHeartbeat(
                self.lifecycle_store,
                interaction_id,
                self.worker_id,
                self.lease_duration_seconds,
                liveness=self._loop_liveness if self.loop_writer is not None else None,
                liveness_interval=float(self.loop_heartbeat_seconds),
            ):
                result = run_selected_persona_interaction(
                    workshop_store=self.workshop_store,
                    read_store=self.read_store,
                    workshop_id=workshop_id,
                    interaction_id=interaction_id,
                    topic=topic,
                    mode=mode,
                    participants=selected_personas,
                    context_refs=context_refs,
                    environment=advice_environment,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    operator_id=operator_id,
                    trace_id=trace_id,
                    proposal_snapshot=resource.get("proposal"),
                    proposal_etag=resource.get("proposal_etag"),
                    occurred_at=str(resource.get("admitted_at") or resource.get("created_at") or _utc_now()),
                    human_submitted_at=submitted_at,
                    client_factory=self.client_factory,
                    lifecycle_store=self.lifecycle_store,
                    frozen_participants=resource.get("_frozen_personas"),
                    invocation_attempt=attempt,
                    lease_owner=self.worker_id,
                    lease_duration_seconds=self.lease_duration_seconds,
                )
            elapsed = time.monotonic() - start_time
            final_status = result.get("status", "completed")
            self._count_outcome(final_status)

            with self._lock:
                self._metrics["admissions_processed"] += 1
                self._metrics["total_execution_seconds"] += elapsed
                self._metrics["last_processed_at"] = _utc_now()
                if final_status == "completed":
                    self._metrics["completed_count"] += 1
                elif final_status == "degraded":
                    self._metrics["degraded_count"] += 1
                else:
                    self._metrics["failed_count"] += 1

            self.drain_outbox()
            loaded = self.lifecycle_store.get(interaction_id, tenant_id, user_id)
            return loaded if loaded is not None else resource

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            self._count_outcome("failed")
            logger.exception("Worker execution error on interaction %s: %s", interaction_id, exc)
            with self._lock:
                self._metrics["failed_count"] += 1
                self._metrics["total_execution_seconds"] += elapsed
                self._metrics["last_processed_at"] = _utc_now()
            # Release or mark failed
            self.lifecycle_store.release_interaction_lease(
                interaction_id, lease_owner=self.worker_id, reset_to_queued=True
            )
            raise

    def _count_outcome(self, final_status: Any) -> None:
        if self._tick is None:
            return
        key = final_status if final_status in {"completed", "degraded"} else "failed"
        self._tick["outcomes"][key] = self._tick["outcomes"].get(key, 0) + 1

    def run_once(
        self,
        *,
        limit: int = 100,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Process eligible pending interactions up to limit and drain research outbox."""
        processed = 0
        tick: Dict[str, Any] = {"claimed": 0, "outcomes": {}, "ids": []}
        self._tick = tick
        drained = 0
        try:
            while processed < limit:
                res = self.claim_and_process_one(tenant_id=tenant_id, user_id=user_id)
                if res is None:
                    break
                processed += 1
            drained = self.drain_outbox(tenant_id=tenant_id, user_id=user_id, limit=limit)
            processed += drained
            return processed
        finally:
            # A raising interaction is still published (as downstream degraded).
            self._tick = None
            self._publish_tick(tick, drained)

    def run_loop(
        self,
        *,
        poll_interval: float = 1.0,
        max_ticks: int = 0,
        stop_event: Optional[threading.Event] = None,
        tenant_id: Optional[str] = None,
        heartbeat_path: Optional[Path] = None,
    ) -> None:
        """Continuous polling loop for processing interactions.

        When ``heartbeat_path`` is set the file is touched every tick so an
        external probe can tell a live loop from a stalled one.
        """
        logger.info(
            "Agora interaction worker %s starting loop (poll=%.1fs, max_ticks=%d, tenant=%s)",
            self.worker_id, poll_interval, max_ticks, tenant_id or "all"
        )
        ticks = 0
        while True:
            if stop_event and stop_event.is_set():
                logger.info("Worker %s received stop event", self.worker_id)
                break
            if max_ticks > 0 and ticks >= max_ticks:
                logger.info("Worker %s reached max_ticks (%d)", self.worker_id, max_ticks)
                break

            ticks += 1
            if heartbeat_path is not None:
                heartbeat_path.touch()
            try:
                processed = self.run_once(limit=25, tenant_id=tenant_id)
                if processed == 0:
                    time.sleep(poll_interval)
            except Exception as exc:
                logger.error("Error in worker tick %d: %s", ticks, exc)
                time.sleep(poll_interval)

        logger.info("Worker %s stopped. Total processed: %d", self.worker_id, self.metrics["admissions_processed"])
