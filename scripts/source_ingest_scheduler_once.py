#!/usr/bin/env python3
"""Run one bounded source-ingest scheduler tick against the HTTP service."""

from __future__ import annotations

import json
import os

from services.source_ingestion.scheduler_worker import run_tick


def main() -> int:
    api_url = os.getenv("SOURCE_INGEST_API_URL", "http://127.0.0.1:8097")
    max_concurrency = int(os.getenv("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", "2"))
    result = run_tick(api_url=api_url, max_concurrency=max_concurrency)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
