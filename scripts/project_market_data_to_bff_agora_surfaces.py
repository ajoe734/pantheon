#!/usr/bin/env python3
"""Project normalized source-ingest market records into Agora read surfaces.

Only real ``tw_price_daily`` SourceRecords are accepted. Existing records owned
by other projectors are preserved; rows previously owned by this projector are
replaced on each run.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any


CONNECTOR_ID = "tw-twse-tpex-official-market"
PROJECTOR = "source-ingest-market-data"


def _get_source_records(base_url: str) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/source-ingest/source-records"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read())
    records = payload.get("source_records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError("source-ingest response has no source_records list")
    return [record for record in records if isinstance(record, dict)]


def project(records: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    latest: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for record in records:
        if str(record.get("connector_id") or "") != CONNECTOR_ID:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        row = metadata.get("normalized_row") if isinstance(metadata.get("normalized_row"), dict) else {}
        if row.get("dataset") != "tw_price_daily":
            continue
        symbol = str(row.get("symbol") or "").strip()
        observed_at = str(row.get("available_time") or row.get("date") or record.get("created_at") or "")
        if symbol and (symbol not in latest or observed_at >= latest[symbol][0]):
            latest[symbol] = (observed_at, record, row)

    watchlist: dict[str, dict[str, Any]] = {}
    signals: dict[str, dict[str, Any]] = {}
    for symbol, (observed_at, record, row) in latest.items():
        source_id = str(record.get("source_id") or "")
        metadata = record.get("metadata") or {}
        source_ref = f"source_ingest:{source_id}"
        common = {
            "symbol": symbol,
            "market": row.get("market"),
            "venue": row.get("venue"),
            "asOf": observed_at,
            "source_ref": source_ref,
            "sourceId": source_id,
            "ingestRunId": metadata.get("source_ingest_run_id") or metadata.get("ingest_run_id"),
            "connectorId": CONNECTOR_ID,
            "projectionOwner": PROJECTOR,
        }
        watchlist_id = f"market-{symbol}"
        watchlist[watchlist_id] = {
            "id": watchlist_id,
            "watchlist_id": watchlist_id,
            "name": row.get("name"),
            "close": row.get("close"),
            "change": row.get("change"),
            "volume": row.get("volume"),
            **common,
        }
        signal_id = f"market-signal-{symbol}-{str(row.get('date') or observed_at)[:10]}"
        signals[signal_id] = {
            "id": signal_id,
            "signal_id": signal_id,
            "title": f"{symbol} official daily market readback",
            "body": f"Official close {row.get('close')}; change {row.get('change')}",
            "status": "open",
            "reviewStatus": "pending_trader_review",
            "severity": "info",
            **common,
        }
    return {"agora_watchlist": watchlist, "agora_signals": signals}


def _load(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_projection(stores: dict[str, dict[str, dict[str, Any]]], out_dir: str | os.PathLike[str]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for dataset, projected in stores.items():
        path = out / f"{dataset}.json"
        merged = {
            key: value
            for key, value in _load(path).items()
            if not isinstance(value, dict) or value.get("projectionOwner") != PROJECTOR
        }
        merged.update(projected)
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> int:
    base_url = os.environ.get("SOURCE_INGEST_URL", "http://source-ingest:8097")
    out_dir = os.environ.get("OUT_DIR", "/data/bff")
    stores = project(_get_source_records(base_url))
    write_projection(stores, out_dir)
    print(f"projected {len(stores['agora_watchlist'])} markets and {len(stores['agora_signals'])} signals -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
