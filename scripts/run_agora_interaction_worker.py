#!/usr/bin/env python3
"""CLI launcher for Agora Persona interaction background worker."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

# Add repository root and services/control-plane/bff to path
ROOT = Path(__file__).resolve().parents[1]
for path in (
    str(ROOT),
    str(ROOT / "services" / "control-plane" / "bff"),
):
    if path not in sys.path:
        sys.path.insert(0, path)

from agora.governance.store import ProposalStore
from agora.interaction.persona_client import build_canonical_persona_client
from agora.interaction.store import InteractionLifecycleStore
from agora.interaction.worker import AgoraInteractionWorker
from agora.strategy_workshop.store import MemoryWorkshopStore, PostgresWorkshopStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agora-interaction-worker")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agora Persona interaction background worker")
    parser.add_argument("--once", action="store_true", help="Process pending interactions once and exit")
    parser.add_argument("--max-ticks", type=int, default=0, help="Maximum loop ticks (0 = infinite)")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Poll interval in seconds")
    parser.add_argument("--tenant-id", type=str, default=None, help="Optional tenant scope")
    parser.add_argument("--healthcheck", action="store_true", help="Run quick liveness healthcheck and exit")
    args = parser.parse_args()

    if args.healthcheck:
        # A healthcheck must not return before required dependency factories
        # are proven constructible. It skips the long-running loop and any
        # live database mutation, but a Persona discovery client that cannot
        # be built is a real startup failure, not something to hide.
        try:
            build_canonical_persona_client()
        except Exception:
            logger.exception("Healthcheck failed: could not construct required Persona discovery client")
            return 1
        logger.info("Healthcheck OK")
        return 0

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

    tenant_id = args.tenant_id or os.getenv("PANTHEON_TENANT_ID")

    worker = AgoraInteractionWorker(
        lifecycle_store=lifecycle_store,
        workshop_store=workshop_store,
        read_store=read_store,
        proposal_store=proposal_store,
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
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
