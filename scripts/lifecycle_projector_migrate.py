#!/usr/bin/env python3
"""LIFECYCLE-PROJ-MIGRATE-001 CLI: resumable relational-projection backfill.

Normally reads committed ``telemetry_events`` rows in monotonic
``ingested_seq`` order and folds them into the relational Trade Journey projection
(``services.trade_journey.projection_store``) through a migration-scoped
controller row that never touches the live controller identity and never
runs in ``live`` mode. See ``services/trade_journey/projection_migration.py``
for the full contract this tool implements.

For the explicitly reviewed target-dev recovery where the retained telemetry
window was truncated before reader cutover, ``--legacy-controller-state`` plus
an exact ``--expected-legacy-sha256`` imports the intact folded JSON baseline
and seeds a non-live recovery cursor. That path still cannot grant live reads;
the shadow worker must poll PostgreSQL to zero backlog afterward.

Usage::

    python3 scripts/lifecycle_projector_migrate.py \\
      --dsn "$LIFECYCLE_PROJECTION_DSN" \\
      --controller-id tj-projector \\
      --tenant-scope "" --environment-scope paper \\
      --snapshot-path /var/run/lifecycle-migrate/tj-projector.snapshot.json

Re-invoking with the same ``--snapshot-path`` resumes from the last durably
committed batch instead of replaying from ``ingested_seq`` 0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Sequence

from services.trade_journey.lifecycle_projector import PostgresLifecycleSource
from services.trade_journey.projection_migration import (
    BackfillCoordinator,
    LegacyBundleBackfillCoordinator,
)
from services.trade_journey.projection_store import ProjectionStore


def _sync_fetch(source: PostgresLifecycleSource):
    def fetch(after_seq: int, limit: int) -> list[dict]:
        return asyncio.run(source.fetch_after(after_seq, limit=limit))

    return fetch


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.getenv("LIFECYCLE_PROJECTION_DSN", ""))
    parser.add_argument(
        "--schema", default=os.getenv("LIFECYCLE_PROJECTION_SCHEMA", "trade_journey_projection")
    )
    parser.add_argument("--controller-id", required=True, help="the live controller id this job backfills for")
    parser.add_argument("--tenant-scope", default="")
    parser.add_argument("--environment-scope", default="")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--snapshot-path", type=Path, required=True)
    parser.add_argument("--deployment-sha", default=os.getenv("GIT_SHA", "unknown"))
    parser.add_argument("--evidence-out", type=Path, default=None)
    parser.add_argument(
        "--legacy-controller-state",
        type=Path,
        default=None,
        help="operator-accepted legacy controller_state.json baseline",
    )
    parser.add_argument(
        "--expected-legacy-sha256",
        default="",
        help="required exact SHA-256 for --legacy-controller-state",
    )
    parser.add_argument(
        "--legacy-checkpoint",
        type=int,
        default=None,
        help="reviewed controller checkpoint bound to the exact legacy checksum",
    )
    parser.add_argument(
        "--legacy-controller-deployment-sha",
        default="",
        help="allowlisted legacy controller deployment identity for evidence",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        parser.error("--dsn or LIFECYCLE_PROJECTION_DSN is required")

    store = ProjectionStore(args.dsn, schema=args.schema)
    if args.legacy_controller_state is not None:
        if not args.expected_legacy_sha256:
            parser.error("--expected-legacy-sha256 is required with --legacy-controller-state")
        if args.legacy_checkpoint is None:
            parser.error("--legacy-checkpoint is required with --legacy-controller-state")
        coordinator = LegacyBundleBackfillCoordinator(
            store,
            controller_id=args.controller_id,
            tenant_scope=args.tenant_scope,
            environment_scope=args.environment_scope,
            controller_state_path=args.legacy_controller_state,
            expected_sha256=args.expected_legacy_sha256,
            snapshot_path=args.snapshot_path,
            accepted_checkpoint=args.legacy_checkpoint,
            accepted_controller_deployment_sha=args.legacy_controller_deployment_sha,
            deployment_sha=args.deployment_sha,
            batch_size=args.batch_size,
        )
    else:
        if args.expected_legacy_sha256:
            parser.error("--expected-legacy-sha256 requires --legacy-controller-state")
        if args.legacy_checkpoint is not None or args.legacy_controller_deployment_sha:
            parser.error("legacy controller metadata requires --legacy-controller-state")
        # Include ignored rows so the migration controller can advance a
        # contiguous global source checkpoint across non-lifecycle telemetry.
        source = PostgresLifecycleSource(args.dsn, include_non_lifecycle=True)
        coordinator = BackfillCoordinator(
            store,
            controller_id=args.controller_id,
            tenant_scope=args.tenant_scope,
            environment_scope=args.environment_scope,
            fetch_batch=_sync_fetch(source),
            snapshot_path=args.snapshot_path,
            deployment_sha=args.deployment_sha,
            batch_size=args.batch_size,
        )
    totals = coordinator.run(max_batches=args.max_batches)
    rendered = json.dumps(totals, sort_keys=True)
    print(rendered)
    if args.evidence_out is not None:
        args.evidence_out.write_text(json.dumps(totals, sort_keys=True, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
