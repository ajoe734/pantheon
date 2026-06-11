#!/usr/bin/env python3
"""CLI wrapper for source-ingestion market-data gap reports."""

from __future__ import annotations

from services.source_ingestion.gap_report import main


if __name__ == "__main__":
    raise SystemExit(main())
