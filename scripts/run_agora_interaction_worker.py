#!/usr/bin/env python3
"""CLI launcher for Agora Persona interaction background worker."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Add repository root and services/control-plane/bff to path
ROOT = Path(__file__).resolve().parents[1]
for path in (
    str(ROOT),
    str(ROOT / "services" / "control-plane" / "bff"),
):
    if path not in sys.path:
        sys.path.insert(0, path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agora-interaction-worker")

HEARTBEAT_PATH = Path(os.getenv("AGORA_WORKER_HEARTBEAT_PATH", "/tmp/agora-interaction-worker.heartbeat"))
HEARTBEAT_MAX_AGE_SECONDS = float(os.getenv("AGORA_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "300"))


def check_heartbeat(path: Path = HEARTBEAT_PATH, max_age: float = HEARTBEAT_MAX_AGE_SECONDS) -> bool:
    """Stdlib-only liveness probe: the running loop must have touched the heartbeat recently."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        logger.error("Healthcheck failed: no heartbeat at %s", path)
        return False
    if age > max_age:
        logger.error("Healthcheck failed: heartbeat is %.0fs old (max %.0fs)", age, max_age)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Agora Persona interaction background worker")
    parser.add_argument("--once", action="store_true", help="Process pending interactions once and exit")
    parser.add_argument("--max-ticks", type=int, default=0, help="Maximum loop ticks (0 = infinite)")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Poll interval in seconds")
    parser.add_argument("--tenant-id", type=str, default=None, help="Optional tenant scope")
    parser.add_argument("--healthcheck", action="store_true", help="Run quick liveness healthcheck and exit")
    args = parser.parse_args()

    if args.healthcheck:
        if not check_heartbeat():
            return 1
        logger.info("Healthcheck OK")
        return 0

    from agora.governance.store import ProposalStore
    from agora.interaction.persona_client import build_canonical_persona_client
    from agora.interaction.store import InteractionLifecycleStore
    from agora.interaction.worker import AgoraInteractionWorker
    from agora.research.routes.common import publish_research_progress
    from agora.research.store import (
        MemoryResearchPlanStore,
        PostgresResearchPlanStore,
        make_research_plan_store,
    )
    from agora.strategy_workshop.store import MemoryWorkshopStore, PostgresWorkshopStore

    workshop_backend = os.getenv("AGORA_WORKSHOP_STORE_BACKEND", "postgres")
    dsn = (
        os.getenv("AGORA_WORKSHOP_STORE_DSN")
        or os.getenv("DATABASE_URL")
        or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
    )
    workshop_schema = os.getenv("AGORA_WORKSHOP_STORE_SCHEMA", "agora")

    gov_backend = os.getenv("AGORA_GOVERNANCE_STORE_BACKEND", "postgres")
    gov_dsn = (
        os.getenv("AGORA_GOVERNANCE_STORE_DSN")
        or os.getenv("DATABASE_URL")
        or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
    )
    gov_schema = os.getenv("AGORA_GOVERNANCE_STORE_SCHEMA", "agora")

    if workshop_backend == "postgres":
        workshop_store = PostgresWorkshopStore(dsn=dsn, schema=workshop_schema)
    else:
        workshop_store = MemoryWorkshopStore()

    proposal_store = ProposalStore(backend=gov_backend, dsn=gov_dsn, schema=gov_schema)
    lifecycle_store = InteractionLifecycleStore(backend=gov_backend, dsn=gov_dsn, schema=gov_schema)

    # Persona discovery is a required dependency: if the canonical client
    # cannot be constructed, startup fails rather than substituting an
    # always-empty implementation.
    read_store = build_canonical_persona_client()
    HEARTBEAT_PATH.unlink(missing_ok=True)

    # Durable research store and dispatcher
    # Research store is a required dependency: wire the same durable owner store (postgres in production)
    # and fail startup if unavailable.
    research_backend = (
        os.getenv("AGORA_RESEARCH_STORE_BACKEND")
        or os.getenv("AGORA_RESEARCH_PLAN_STORE_BACKEND")
        or (workshop_backend if workshop_backend == "postgres" else "off")
    ).strip().lower()
    research_dsn = (
        os.getenv("AGORA_RESEARCH_STORE_DSN")
        or os.getenv("DATABASE_URL")
        or gov_dsn
    )
    research_schema = os.getenv("AGORA_RESEARCH_STORE_SCHEMA", "agora_research")
    storage_path = os.getenv("AGORA_RESEARCH_STORE_STORAGE_PATH")

    if research_backend == "postgres":
        research_store = PostgresResearchPlanStore(dsn=research_dsn, schema=research_schema)
    elif research_backend in ("off", "memory"):
        research_store = MemoryResearchPlanStore(storage_path=storage_path)
    else:
        raise ValueError(f"Unsupported AGORA_RESEARCH_STORE_BACKEND: {research_backend}")

    # Durable dataset store: wire the same durable owner store (postgres in production)
    dataset_backend = (
        os.getenv("AGORA_DATASET_STORE_BACKEND")
        or (workshop_backend if workshop_backend == "postgres" else "off")
    ).strip().lower()
    dataset_dsn = (
        os.getenv("AGORA_DATASET_STORE_DSN")
        or os.getenv("DATABASE_URL")
        or dsn
    )
    dataset_schema = os.getenv("AGORA_DATASET_STORE_SCHEMA", "agora")

    from agora.dataset_extraction.extractor import AgoraDatasetStore
    import agora.dataset_extraction.router as dataset_router

    dataset_store = AgoraDatasetStore(
        backend=dataset_backend,
        dsn=dataset_dsn,
        schema=dataset_schema,
    )
    dataset_router._STORE = dataset_store

    tenant_id = args.tenant_id or os.getenv("PANTHEON_TENANT_ID")

    worker = AgoraInteractionWorker(
        lifecycle_store=lifecycle_store,
        workshop_store=workshop_store,
        read_store=read_store,
        proposal_store=proposal_store,
        research_store=research_store,
        dataset_store=dataset_store,
        worker_id=os.getenv("PANTHEON_AGORA_WORKER_ID", "agora-interaction-worker"),
    )

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Received signal %d, stopping worker...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.once:
        processed = worker.run_once(tenant_id=tenant_id)
        logger.info("Processed %d interaction(s)", processed)
        return 0

    worker.run_loop(
        poll_interval=args.poll_interval,
        max_ticks=args.max_ticks,
        stop_event=stop_event,
        tenant_id=tenant_id,
        heartbeat_path=HEARTBEAT_PATH,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
