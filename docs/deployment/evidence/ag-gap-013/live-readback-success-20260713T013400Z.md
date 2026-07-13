# AG-GAP-013 dev live readback success — 2026-07-13 01:34:00 UTC

Target: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` (or local dev VM target: `http://localhost:18001`)

The market-data projector was successfully run against the live source-ingest volume and projected real official-market records into Agora read surfaces.

## Results

### 1. `/bff/agora/markets` contains the official 2330 row and its source refs

Query response for `2330`:
```json
{
  "id": "market-2330",
  "watchlist_id": "market-2330",
  "name": "台積電",
  "close": 2460.0,
  "change": 15.0,
  "volume": 21041918,
  "symbol": "2330",
  "market": "TW",
  "venue": "TWSE",
  "asOf": "2026-07-06",
  "source_ref": "source_ingest:tw-official:tw_price_daily:TWSE:2330:794c58f3580b4a47",
  "sourceId": "tw-official:tw_price_daily:TWSE:2330:794c58f3580b4a47",
  "ingestRunId": null,
  "connectorId": "tw-twse-tpex-official-market",
  "projectionOwner": "source-ingest-market-data"
}
```

### 2. `/bff/agora/daily` brief contains non-zero watchlist/signal KPIs and same provenance

Query response excerpt:
```json
{
  "data": {
    "id": "agora-daily-2026-07-13",
    "date": "2026-07-13",
    "generatedAt": "2026-07-13T01:34:28Z",
    "kpis": {
      "watchlistMoveCount": 1001,
      "signalReviewQueue": 1001,
      "personaBriefCount": 0,
      "researchQuestionCount": 0
    },
    "sections": {
      "signals": [
        {
          "id": "market-signal-00400A-2026-07-06",
          "signal_id": "market-signal-00400A-2026-07-06",
          "title": "00400A official daily market readback",
          "body": "Official close 15.0; change -0.3",
          "status": "open",
          "reviewStatus": "pending_trader_review",
          "severity": "info",
          "symbol": "00400A",
          "market": "TW",
          "venue": "TWSE",
          "asOf": "2026-07-06",
          "source_ref": "source_ingest:tw-official:tw_price_daily:TWSE:00400A:e11444b9559ad325",
          "sourceId": "tw-official:tw_price_daily:TWSE:00400A:e11444b9559ad325",
          "ingestRunId": null,
          "connectorId": "tw-twse-tpex-official-market",
          "projectionOwner": "source-ingest-market-data"
        }
      ],
      "watchlist": [
        {
          "id": "market-00400A",
          "watchlist_id": "market-00400A",
          "name": "主動國泰動能高息",
          "close": 15.0,
          "change": -0.3,
          "volume": 68129433,
          "symbol": "00400A",
          "market": "TW",
          "venue": "TWSE",
          "asOf": "2026-07-06",
          "source_ref": "source_ingest:tw-official:tw_price_daily:TWSE:00400A:e11444b9559ad325",
          "sourceId": "tw-official:tw_price_daily:TWSE:00400A:e11444b9559ad325",
          "ingestRunId": null,
          "connectorId": "tw-twse-tpex-official-market",
          "projectionOwner": "source-ingest-market-data"
        }
      ]
    }
  }
}
```

## Verification

The projector ran inside the `pantheon-operator-bff-1` container, fetching 1001 records from the `tw-twse-tpex-official-market` connector whose dataset was `tw_price_daily`, identifying the newest record per symbol, and updating both the watchlist and daily signals in the BFF volume.
No test/mock/fixture data was generated or injected.
