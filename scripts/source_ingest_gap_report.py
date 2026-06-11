#!/usr/bin/env python3
"""CLI wrapper for source-ingestion market-data gap reports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.source_ingestion.gap_report import main


if __name__ == "__main__":
    raise SystemExit(main())
