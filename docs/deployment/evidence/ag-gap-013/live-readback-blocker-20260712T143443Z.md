# AG-GAP-013 dev live readback blocker — 2026-07-12 14:34:43 UTC

Target: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

The market-data projector was merged through Pantheon PR #3429 before this
readback. Authenticated, read-only probes used the repository's dev operator
smoke identity.

## Results

- `GET /health`: HTTP 200; `operator-bff` reported healthy at
  `2026-07-12T14:34:43Z`.
- `GET /bff/agora/markets`: HTTP 200, but no row for symbol `2330` and zero
  rows with `projectionOwner=source-ingest-market-data`.
- `GET /bff/agora/daily`: HTTP 200, but `watchlist` and `signals` were both
  empty, with no `2330` row or source-ingest projection provenance.

## Disposition

The live acceptance gate is blocked. Do not mark AG-GAP-013 done. The dev
deployment must run `scripts/project_market_data_to_bff_agora_surfaces.py`
against the activated source-ingest service with `OUT_DIR` bound to the BFF
data volume, then repeat and archive the authenticated readback.

No live write was performed by this probe.
