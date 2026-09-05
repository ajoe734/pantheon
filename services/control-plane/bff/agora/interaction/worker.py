"""Durable Agora Persona interaction background worker."""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

try:
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
except ImportError:  # pragma: no cover - package entrypoint fallback
    from ...openclaw_ops_client import OpenClawOpsClient
from .runner import drain_interaction_outbox, run_selected_persona_interaction
from .store import InteractionLifecycleStore

logger = logging.getLogger("agora.interaction.worker")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _InteractionHeartbeat:
    """Background context manager that periodically renews the interaction lease while work runs."""

    def __init__(
        self,
        store: InteractionLifecycleStore,
        interaction_id: str,
        lease_owner: str,
        lease_duration_seconds: int = 300,
    ) -> None:
        self.store = store
        self.interaction_id = interaction_id
        self.lease_owner = lease_owner
        self.lease_duration_seconds = lease_duration_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "_InteractionHeartbeat":
        interval = max(0.5, self.lease_duration_seconds / 3.0)

        def _loop() -> None:
            while not self._stop_event.wait(timeout=interval):
                try:
                    self.store.heartbeat_interaction(
                        self.interaction_id,
                        lease_owner=self.lease_owner,
                        lease_duration_seconds=self.lease_duration_seconds,
                    )
                except Exception as exc:
                    logger.debug("Heartbeat renewal error on %s: %s", self.interaction_id, exc)

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
        lifecycle_store: InteractionLifecycleStore,
        workshop_store: Any,
        read_store: Any,
        client_factory: Optional[Callable[[], OpenClawOpsClient]] = None,
        proposal_store: Optional[Any] = None,
        worker_id: Optional[str] = None,
        lease_duration_seconds: int = 300,
    ) -> None:
        self.lifecycle_store = lifecycle_store
        self.workshop_store = workshop_store
        self.read_store = read_store
        self.client_factory = client_factory
        self.proposal_store = proposal_store
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

    def drain_outbox(self) -> int:
        try:
            return drain_interaction_outbox(self.lifecycle_store, self.workshop_store)
        except Exception as exc:
            logger.warning("Failed draining interaction outbox: %s", exc)
            return 0

    def claim_and_process_one(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Claim next queued or expired-lease interaction and execute it."""
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

        return self._execute_and_finalize(resource)

    def run_once(
        self,
        *,
        limit: int = 1,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Claim and process up to limit interactions."""
        processed = 0
        for _ in range(max(1, limit)):
            item = self.claim_and_process_one(tenant_id=tenant_id, user_id=user_id)
            if item is None:
                break
            processed += 1
        return processed

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

    def run_once(
        self,
        *,
        limit: int = 100,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Process eligible pending interactions up to limit. Returns processed count."""
        processed = 0
        while processed < limit:
            res = self.claim_and_process_one(tenant_id=tenant_id, user_id=user_id)
            if res is None:
                break
            processed += 1
        self.drain_outbox()
        return processed

    def run_loop(
        self,
        *,
        poll_interval: float = 1.0,
        max_ticks: int = 0,
        stop_event: Optional[threading.Event] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        """Continuous polling loop for processing interactions."""
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
            try:
                processed = self.run_once(limit=25, tenant_id=tenant_id)
                if processed == 0:
                    time.sleep(poll_interval)
            except Exception as exc:
                logger.error("Error in worker tick %d: %s", ticks, exc)
                time.sleep(poll_interval)

        logger.info("Worker %s stopped. Total processed: %d", self.worker_id, self.metrics["admissions_processed"])
