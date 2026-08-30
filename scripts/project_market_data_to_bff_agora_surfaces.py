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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CONNECTOR_ID = "tw-twse-tpex-official-market"
PROJECTOR = "source-ingest-market-data"
DEFAULT_STALE_THRESHOLD_SECONDS = 86_400
SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = 300


def _get_source_records(base_url: str) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/source-ingest/source-records"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read())
    records = payload.get("source_records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError("source-ingest response has no source_records list")
    return [record for record in records if isinstance(record, dict)]


def _get_connector_readback(base_url: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/source-ingest/controller/readback"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read())
    connectors = payload.get("connectors") if isinstance(payload, dict) else None
    if not isinstance(connectors, list):
        raise ValueError("source-ingest readback has no connectors list")
    for connector in connectors:
        if isinstance(connector, dict) and connector.get("connector_id") == CONNECTOR_ID:
            return connector
    raise ValueError(f"source-ingest readback has no connector {CONNECTOR_ID}")


def _get_connector_freshness(base_url: str) -> dict[str, Any]:
    connector = _get_connector_readback(base_url)
    freshness = connector.get("freshness")
    if not isinstance(freshness, dict):
        raise ValueError(f"source-ingest readback for {CONNECTOR_ID} has no freshness object")
    return freshness


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness_metadata(
    *,
    source_timestamp: str | None,
    connector_freshness: dict[str, Any],
    now: datetime,
    default_stale_threshold_seconds: int,
    source_id: str | None = None,
    connector_id: str | None = None,
    calendar_evidence: dict[str, Any] | None = None,
    accepted_run_id: str | None = None,
    record_run_id: str | None = None,
    accepted_source_id: str | None = None,
) -> dict[str, Any]:
    parsed_source_timestamp = _parse_timestamp(source_timestamp)
    if not str(source_timestamp or "").strip():
        source_timestamp_status = "missing"
    elif parsed_source_timestamp is None:
        source_timestamp_status = "invalid"
    elif parsed_source_timestamp > now + timedelta(seconds=SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS):
        source_timestamp_status = "future"
    else:
        source_timestamp_status = "valid"
    age_seconds = (
        max(0, int((now - parsed_source_timestamp).total_seconds()))
        if parsed_source_timestamp is not None and source_timestamp_status == "valid"
        else None
    )
    threshold = max(
        1,
        int(connector_freshness.get("stale_threshold_seconds") or default_stale_threshold_seconds),
    )

    typed_failure = connector_freshness.get("last_typed_failure")
    if isinstance(typed_failure, dict):
        typed_failure = dict(typed_failure)
    elif typed_failure is not None:
        typed_failure = {"category": "freshness", "code": str(typed_failure), "retryable": False}

    cid = str(connector_id or CONNECTOR_ID)
    sid = str(source_id or "")
    lineage_invalid = False
    if cid != CONNECTOR_ID:
        lineage_invalid = True
        if typed_failure is None:
            typed_failure = {"category": "receipt_binding", "code": "mismatched_connector", "retryable": False}
    elif not sid.startswith("tw-official:"):
        lineage_invalid = True
        if typed_failure is None:
            typed_failure = {"category": "lineage", "code": "market_input_non_official_lineage", "retryable": False}

    run_mismatch = False
    if accepted_run_id:
        if not record_run_id:
            run_mismatch = True
            if typed_failure is None:
                typed_failure = {"category": "receipt_binding", "code": "missing_record_run", "retryable": False}
        elif str(record_run_id) != str(accepted_run_id):
            run_mismatch = True
            if typed_failure is None:
                typed_failure = {"category": "receipt_binding", "code": "mismatched_run", "retryable": False}

    source_mismatch = False
    if accepted_source_id:
        if not sid:
            source_mismatch = True
            if typed_failure is None:
                typed_failure = {"category": "receipt_binding", "code": "missing_record_source", "retryable": False}
        elif str(sid) != str(accepted_source_id):
            source_mismatch = True
            if typed_failure is None:
                typed_failure = {"category": "receipt_binding", "code": "mismatched_source", "retryable": False}

    tw_stale = None
    if parsed_source_timestamp is not None and source_timestamp_status == "valid" and not lineage_invalid:
        last_success_at_str = connector_freshness.get("last_success_at")
        refresh_dt = _parse_timestamp(last_success_at_str)
        if refresh_dt is None:
            tw_stale = True
            if typed_failure is None:
                failure_code = "missing_refresh_receipt" if not last_success_at_str else "unparsable_refresh_receipt"
                typed_failure = {"category": "receipt_binding", "code": failure_code, "retryable": False}
        else:
            lineage = {"connector_ids": [cid], "source_ids": [sid]}
            try:
                from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness

                tw_ok, tw_reason, tw_detail = evaluate_taiwan_market_freshness(
                    event_time_dt=parsed_source_timestamp,
                    now_dt=now,
                    refresh_receipt_dt=refresh_dt,
                    lineage=lineage,
                    max_refresh_age_seconds=threshold,
                    calendar_evidence=calendar_evidence,
                )
                tw_stale = not tw_ok
                if tw_stale and typed_failure is None:
                    typed_failure = {
                        "category": "market_session",
                        "code": tw_reason or "source_data_stale",
                        "retryable": False,
                        "detail": tw_detail,
                    }
            except Exception as exc:
                tw_stale = True
                if typed_failure is None:
                    typed_failure = {
                        "category": "market_session",
                        "code": "market_input_calendar_unverifiable",
                        "retryable": False,
                        "detail": str(exc),
                    }

    if source_timestamp_status == "future" and typed_failure is None:
        typed_failure = {"category": "source_timestamp", "code": "future_timestamp", "retryable": False}
    elif source_timestamp_status in {"missing", "invalid"} and typed_failure is None:
        typed_failure = {"category": "source_timestamp", "code": f"{source_timestamp_status}_source_timestamp", "retryable": False}

    if tw_stale is not None:
        stale = (
            connector_freshness.get("status") == "stale"
            or connector_freshness.get("source_timestamp_status") in {"missing", "invalid", "future"}
            or source_timestamp_status != "valid"
            or tw_stale
            or lineage_invalid
            or run_mismatch
            or source_mismatch
        )
    else:
        stale = (
            connector_freshness.get("status") == "stale"
            or connector_freshness.get("source_timestamp_status") in {"missing", "invalid", "future"}
            or source_timestamp_status != "valid"
            or age_seconds is None
            or age_seconds > threshold
            or lineage_invalid
            or run_mismatch
            or source_mismatch
        )
    return {
        "schemaVersion": "agora_source_freshness.v1",
        "status": "stale" if stale else "fresh",
        "stale": stale,
        "lastSuccessAt": connector_freshness.get("last_success_at"),
        "sourceTimestamp": source_timestamp or None,
        "sourceTimeStatus": source_timestamp_status,
        "ageSeconds": age_seconds,
        "staleThresholdSeconds": threshold,
        "nextRunAt": connector_freshness.get("next_run_at") or connector_freshness.get("next_due_at"),
        "lastTypedFailure": typed_failure,
    }


def _explicit_source_timestamp(row: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    for container, keys in (
        (row, ("available_time", "as_of_time", "event_time", "timestamp", "date")),
        (metadata, ("available_time", "as_of_time", "event_time", "source_timestamp", "date")),
    ):
        for key in keys:
            value = str(container.get(key) or "").strip()
            if value:
                return value
    return None


def project(
    records: list[dict[str, Any]],
    *,
    connector_freshness: dict[str, Any] | None = None,
    connector_readback: dict[str, Any] | None = None,
    now: datetime | None = None,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
) -> dict[str, dict[str, dict[str, Any]]]:
    if connector_readback is not None and isinstance(connector_readback, dict):
        freshness_readback = dict(connector_readback.get("freshness") or {})
        latest_source_record = (
            connector_readback.get("latest_source_record")
            if isinstance(connector_readback.get("latest_source_record"), dict)
            else {}
        )
    elif connector_freshness is not None and isinstance(connector_freshness, dict):
        freshness_readback = dict(connector_freshness)
        latest_source_record = {}
    else:
        freshness_readback = {}
        latest_source_record = {}

    latest_receipt = (
        freshness_readback.get("latest_receipt")
        if isinstance(freshness_readback.get("latest_receipt"), dict)
        else {}
    )
    latest_run = (
        freshness_readback.get("latest_run")
        if isinstance(freshness_readback.get("latest_run"), dict)
        else {}
    )
    accepted_run_id = str(
        latest_receipt.get("ingest_run_id")
        or latest_run.get("ingest_run_id")
        or freshness_readback.get("last_ingest_run_id")
        or ""
    )
    accepted_source_id = str(latest_source_record.get("source_id") or "")
    accepted_symbol = ""
    if latest_source_record:
        prov = (
            latest_source_record.get("provenance")
            if isinstance(latest_source_record.get("provenance"), dict)
            else {}
        )
        prov_run = str(prov.get("source_ingest_run_id") or "")
        if prov_run and not accepted_run_id:
            accepted_run_id = prov_run
        accepted_symbol = str(prov.get("symbol") or latest_source_record.get("symbol") or "")
        if not accepted_symbol and accepted_source_id.startswith("tw-official:"):
            parts = accepted_source_id.split(":")
            if len(parts) >= 4:
                accepted_symbol = parts[3]

    captured_at = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    captured_at = captured_at.astimezone(timezone.utc)

    latest: dict[str, tuple[tuple[int, int, int, datetime, str, str], str | None, dict[str, Any], dict[str, Any]]] = {}
    for record in records:
        if str(record.get("connector_id") or "") != CONNECTOR_ID:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        row = metadata.get("normalized_row") if isinstance(metadata.get("normalized_row"), dict) else {}
        if row.get("dataset") != "tw_price_daily":
            continue
        symbol = str(row.get("symbol") or "").strip()
        source_timestamp = _explicit_source_timestamp(row, metadata)
        parsed_source_timestamp = _parse_timestamp(source_timestamp)
        ordering_timestamp = parsed_source_timestamp or datetime.min.replace(tzinfo=timezone.utc)
        source_id = str(record.get("source_id") or "")
        rec_run_id = str(metadata.get("source_ingest_run_id") or metadata.get("ingest_run_id") or "")
        is_accepted_run = 1 if (accepted_run_id and rec_run_id == accepted_run_id) else 0
        is_accepted_source = 1 if (accepted_source_id and source_id == accepted_source_id) else 0
        ordering_key = (
            1 if parsed_source_timestamp is not None else 0,
            is_accepted_run,
            is_accepted_source,
            ordering_timestamp,
            source_timestamp or "",
            source_id,
        )
        if symbol and (symbol not in latest or ordering_key >= latest[symbol][0]):
            latest[symbol] = (ordering_key, source_timestamp, record, row)

    watchlist: dict[str, dict[str, Any]] = {}
    signals: dict[str, dict[str, Any]] = {}
    for symbol, (_ordering_key, source_timestamp, record, row) in latest.items():
        source_id = str(record.get("source_id") or "")
        metadata = record.get("metadata") or {}
        rec_run_id = metadata.get("source_ingest_run_id") or metadata.get("ingest_run_id")
        record_run_id = str(rec_run_id) if rec_run_id else None
        source_ref = f"source_ingest:{source_id}"
        cal_ev = metadata.get("calendar_evidence")
        if cal_ev is None and isinstance(metadata.get("normalized_row"), dict):
            cal_ev = metadata["normalized_row"].get("calendar_evidence")
        target_accepted_source = (
            accepted_source_id
            if (
                accepted_source_id
                and (not accepted_symbol or accepted_symbol.upper() == symbol.upper())
            )
            else None
        )
        freshness = _freshness_metadata(
            source_timestamp=source_timestamp,
            connector_freshness=freshness_readback,
            now=captured_at,
            default_stale_threshold_seconds=stale_threshold_seconds,
            source_id=source_id,
            connector_id=CONNECTOR_ID,
            calendar_evidence=cal_ev,
            accepted_run_id=accepted_run_id or None,
            record_run_id=record_run_id,
            accepted_source_id=target_accepted_source,
        )
        common = {
            "symbol": symbol,
            "market": row.get("market"),
            "venue": row.get("venue"),
            "asOf": source_timestamp,
            "source_ref": source_ref,
            "sourceId": source_id,
            "ingestRunId": record_run_id,
            "connectorId": CONNECTOR_ID,
            "projectionOwner": PROJECTOR,
            "freshness": freshness,
            "freshnessStatus": freshness["status"],
            "stale": freshness["stale"],
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
        signal_id = f"market-signal-{symbol}-{str(row.get('date') or source_timestamp or 'unknown')[:10]}"
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
    connector_readback = _get_connector_readback(base_url)
    freshness = connector_readback.get("freshness") if isinstance(connector_readback.get("freshness"), dict) else {}
    stores = project(
        _get_source_records(base_url),
        connector_freshness=freshness,
        connector_readback=connector_readback,
        stale_threshold_seconds=max(
            1,
            int(os.environ.get("AGORA_MARKET_STALE_THRESHOLD_SECONDS", str(DEFAULT_STALE_THRESHOLD_SECONDS))),
        ),
    )
    write_projection(stores, out_dir)
    print(f"projected {len(stores['agora_watchlist'])} markets and {len(stores['agora_signals'])} signals -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
