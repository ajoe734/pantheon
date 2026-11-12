"""Post-reconciliation minute snapshot writer (SD_INTRADAY_MINUTE_BARS §9 K4).

This is the **only** writer of ``tw_price_1m`` latest snapshots.  It consumes the K3
``ReconciliationResult`` list, takes ``pass`` days only, re-reads the rows through the
hash-verified ``read_reconciled_rows`` (so a tampered/withheld/waived/quarantined day
yields nothing) and writes them through the existing
``LatestMarketSnapshotStore.append_normalized_records`` with ``dataset="tw_price_1m"``.

``pipeline.persist_latest_market_snapshots`` keeps excluding minute records permanently:
the generic post-ingest path never writes minute data.  ``pending_*``, quarantine,
``minute_missing`` and every other outcome write nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from .intraday_reconcile import OUTCOME_PASS, ReconciliationResult, read_reconciled_rows
from .intraday_store import DATASET, IntradayStore
from .requirement_state import MINUTE_SNAPSHOT_DATASET, LatestMarketSnapshotStore

SCHEMA_VERSION = "tw_price_1m_snapshot_projection.v1"
MINUTE_SOURCE_ID = "finmind:TaiwanStockKBar"
MINUTE_CONNECTOR_ID = "tw-finmind-kbar-1m"


def _record(row: Mapping[str, Any], *, content_ref: str) -> Any:
    event_time = row["bar_end"]
    return SimpleNamespace(
        source_id=MINUTE_SOURCE_ID,
        connector_id=MINUTE_CONNECTOR_ID,
        content_ref=content_ref,
        is_rejected=False,
        created_at=row.get("bar_availability_ts"),
        metadata={
            "normalized_row": {
                "dataset": MINUTE_SNAPSHOT_DATASET,
                "symbol": row["symbol"],
                "close": row["close"],
                "event_time": event_time,
            },
            "dataset": MINUTE_SNAPSHOT_DATASET,
        },
    )


def _withdraw(store: LatestMarketSnapshotStore, res: ReconciliationResult, out: list[dict[str, str]]) -> None:
    """Durably remove points an earlier pass published for a day that is no longer a clean pass."""
    if store.withdraw_sessions(res.symbol, MINUTE_SNAPSHOT_DATASET, [res.session_date]):
        out.append({"symbol": res.symbol, "session_date": res.session_date, "outcome": res.outcome})


def project_reconciled_minute_snapshots(
    store: LatestMarketSnapshotStore,
    results: Iterable[ReconciliationResult],
    *,
    intraday_store: IntradayStore,
    ingest_run_id: str | None = None,
) -> dict[str, Any]:
    """Project reconciled ``pass`` symbol-days into the minute-keyed latest snapshot store."""
    passed: list[ReconciliationResult] = []
    skipped: dict[str, int] = {}
    withdrawn: list[dict[str, str]] = []
    for res in results:
        if res.outcome == OUTCOME_PASS:
            passed.append(res)
        else:
            skipped[res.outcome] = skipped.get(res.outcome, 0) + 1
            _withdraw(store, res, withdrawn)  # a day that no longer passes is un-published
    passed.sort(key=lambda r: (r.session_date, r.symbol))

    projected = 0
    withheld: list[dict[str, str]] = []
    updated = 0
    for res in passed:
        rows = read_reconciled_rows(intraday_store, res.symbol, res.session_date)
        if not rows:  # tampered, withheld, waived or no longer reconciled: never project
            withheld.append({"symbol": res.symbol, "session_date": res.session_date})
            _withdraw(store, res, withdrawn)
            continue
        minute_ref = (res.source_refs or {}).get("minute") or {}
        sha = minute_ref.get("shard_sha256") or minute_ref.get("readmitted_rows_sha256") or "unknown"
        content_ref = f"intraday-store://{DATASET}/{res.session_date}/{res.symbol}?sha256={sha}"
        observed_at = max(str(r["bar_availability_ts"]) for r in rows)
        batch = store.append_normalized_records(
            [_record(r, content_ref=content_ref) for r in rows],
            ingest_run_id=ingest_run_id or f"intraday-reconcile-{res.session_date}",
            observed_at=observed_at,
            dataset=MINUTE_SNAPSHOT_DATASET,
        )
        projected += 1
        updated += int(batch["updated_snapshot_count"])
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset": MINUTE_SNAPSHOT_DATASET,
        "projected_symbol_days": projected,
        "updated_snapshot_count": updated,
        "withheld": withheld,
        "withdrawn": withdrawn,
        "skipped_outcome_counts": dict(sorted(skipped.items())),
    }


__all__ = ["project_reconciled_minute_snapshots"]
