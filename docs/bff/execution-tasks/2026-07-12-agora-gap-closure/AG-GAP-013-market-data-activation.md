# AG-GAP-013: Agora market-data activation readback

Status: implementation ready for review; live deployment proof pending.

## Upstream gate

SRCLIVE-001 live activation evidence is accepted and merged in Pantheon PR
#3047 (merge `488ed686cfe38f83acdea10ea1e9fdca6018005a`). The evidence records
1000 normalized official-market rows and a passing authenticated readback.

## Projection

`scripts/project_market_data_to_bff_agora_surfaces.py` reads
`GET /api/source-ingest/source-records`, selects only records from
`tw-twse-tpex-official-market` whose `metadata.normalized_row.dataset` is
`tw_price_daily`, and keeps the newest row per symbol. It writes owned rows to
`agora_watchlist.json` and `agora_signals.json`, preserving rows written by
other projectors.

Every projected row includes `source_ref`, `sourceId`, `ingestRunId`, and
`connectorId`. No fallback or fixture rows are generated.

Repeatable dev command:

```bash
SOURCE_INGEST_URL=http://source-ingest:8097 OUT_DIR=/data/bff \
  python3 scripts/project_market_data_to_bff_agora_surfaces.py
```

Run it after the daily official-market ingest completes (on demand or from the
deployment scheduler). Re-running replaces only rows with
`projectionOwner=source-ingest-market-data`.

## Verification

Local focused verification:

```bash
python3 -m pytest scripts/test_project_market_data_to_bff_agora_surfaces.py -q
python3 -m py_compile scripts/project_market_data_to_bff_agora_surfaces.py
```

Expected: two tests pass. Tests prove latest-row selection, source/run
provenance, rejection of non-price records, and preservation of records owned
by another projector.

## Remaining live gate

After merge and dev deployment, run the projector against the live
source-ingest volume and archive authenticated responses under
`docs/deployment/evidence/ag-gap-013/` proving:

1. `/bff/agora/markets` contains the official 2330 row and its source/run refs.
2. `/bff/agora/daily` has non-zero watchlist/signal KPIs and sections containing
   the same provenance.

Do not mark this task done until that evidence is merged.
