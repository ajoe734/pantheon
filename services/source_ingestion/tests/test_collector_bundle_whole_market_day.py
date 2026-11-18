"""COLLECTOR-BUNDLE-WHOLE-MARKET-DAY-READ-20261118: one whole market day in one bundle.

Hosted evidence (collector host, 2026-11-17 TPEx trade date): items.jsonl 16175460 bytes for 13870
rows (about 1166 bytes per row). Every shape here is generated locally with a fixed seed.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone

import pytest

from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport

SOURCE = "collector-tw-tpex-price-history"
DAY = "2026-11-17"
RUN = "run-20261118T001529Z"
HOSTED_ROWS = 13870
SEED = 20261118


def _row_text(rng: random.Random, width: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(rng.choice(alphabet) for _ in range(width))


def make_market_day(rows: int = HOSTED_ROWS, *, seed: int = SEED, source: str = SOURCE, day: str = DAY,
                    run_id: str = RUN, with_raw: bool = True, pad: int = 666):
    """A TPEx-shaped complete bundle. Returns (files, index_entry)."""
    rng = random.Random(seed)
    base = f"bundles/{source}/{day}/{run_id}"
    finished = f"{day}T16:15:29Z"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
    items, lines, files = [], [], {}
    for n in range(rows):
        code = f"{n:06d}"
        item_id = f"tpex-price-history-{day}-{code}"
        body = f"<r>{day}-{code}-{rng.getrandbits(32)}</r>".encode() if with_raw else b""
        sha = hashlib.sha256(body or item_id.encode()).hexdigest()
        items.append({"item_id": item_id, "url": endpoint, "http_status": 200, "content_sha256": sha,
                      "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        close = None if n % 3 == 0 else round(10 + rng.random() * 900, 2)
        lines.append(json.dumps({
            "id": item_id, "url": endpoint, "fetched_at": f"{day}T16:00:00Z", "symbol": code,
            "security_name": f"name {code}", "exchange": "TPEX", "dataset": "tw_price_daily", "trade_date": day,
            "open": close, "high": close, "low": close, "close": close, "volume_shares": rng.randrange(10**6),
            "transactions": rng.randrange(10**4), "official_publication_ref": _row_text(rng, pad),
            "api_endpoint": endpoint,
        }))
        if with_raw:
            files[f"{base}/raw/{sha}.html"] = body
    manifest = {
        "schema_version": "collector_bundle.v1", "source_id": source, "run_id": run_id, "collector_version": "1.0.0",
        "started_at": f"{day}T16:00:00Z", "finished_at": finished, "fetch_policy_hash": "fph",
        "robots_decision": "allowed", "terms_ref": "https://example.test/terms", "attribution": "TPEx",
        "items": items, "status": "complete",
        "bundle_sha256": cb.compute_bundle_sha256([m["content_sha256"] for m in items]),
    }
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base}/items.jsonl"] = "\n".join(lines).encode()
    entry = {"source_id": source, "date": day, "run_id": run_id, "finished_at": finished,
             "bundle_sha256": manifest["bundle_sha256"]}
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    files["status.json"] = json.dumps({"sources": {source: {"status": "ok", "last_run_at": finished}}}).encode()
    return files, entry
