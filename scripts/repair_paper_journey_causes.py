#!/usr/bin/env python3
"""CLI entry point for bounded historical paper Trade Journey cause repair.

Delegates to :mod:`services.trade_journey.cause_repair` (also reachable via
``python3 -m services.trade_journey.lifecycle_projector repair-causes``).
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.trade_journey.cause_repair import (  # noqa: E402
    BEFORE_IMAGE_FILENAME,
    CURSOR_FILENAME,
    DEFAULT_BATCH_SIZE,
    DEFAULT_ENVIRONMENT,
    DEFAULT_MAX_BATCHES,
    DEFAULT_MAX_BEFORE_IMAGE_BYTES,
    DEFAULT_MAX_BEFORE_IMAGE_RECORDS,
    DEFAULT_TEMP_DIR,
    DEFAULT_TEMP_RETENTION_SECONDS,
    DEFAULT_TENANT_ID,
    MAX_BATCH_SIZE,
    REPAIR_SCHEMA_VERSION,
    CandidateStageRow,
    InMemoryCauseRepairBackend,
    PostgresCauseRepairBackend,
    RepairRunReport,
    StageRepairDecision,
    cleanup_temp_evidence,
    configure_cli_parser,
    evaluation_storage_record_id,
    main as _repair_main,
    run_cause_repair,
    run_cause_rollback,
    run_from_cli_args,
)

__all__ = [
    "BEFORE_IMAGE_FILENAME",
    "CURSOR_FILENAME",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_MAX_BATCHES",
    "DEFAULT_MAX_BEFORE_IMAGE_BYTES",
    "DEFAULT_MAX_BEFORE_IMAGE_RECORDS",
    "DEFAULT_TEMP_DIR",
    "DEFAULT_TEMP_RETENTION_SECONDS",
    "DEFAULT_TENANT_ID",
    "MAX_BATCH_SIZE",
    "REPAIR_SCHEMA_VERSION",
    "CandidateStageRow",
    "InMemoryCauseRepairBackend",
    "PostgresCauseRepairBackend",
    "RepairRunReport",
    "StageRepairDecision",
    "cleanup_temp_evidence",
    "configure_cli_parser",
    "evaluation_storage_record_id",
    "main",
    "run_cause_repair",
    "run_cause_rollback",
    "run_from_cli_args",
]


def main(argv: Sequence[str] | None = None) -> int:
    return _repair_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
