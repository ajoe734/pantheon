# APP-003-DATASOURCE-CRYPTO-002 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `APP-003-DATASOURCE-CRYPTO-002-SIDECAR-ACCEPTANCE`
**Helper parent:** `APP-003-DATASOURCE-CRYPTO-002`
**Parent owner:** `Codex2`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Refreshed by:** `Codex2`
**Sidecar reviewer:** `Claude`
**Date:** `2026-04-24`
**Status:** `review_approved`

> Scope constraint: support artifact only. This packet does not change L1
> canonical truth or runtime code. It summarizes the landed APP-003-DATASOURCE-CRYPTO-002
> surfaces so Claude can review/finalize the sidecar truthfully and so the
> parent owner can reference a compact acceptance map during closeout.

## Executive Summary

The previous version of this sidecar packet was stale: the repo now contains
the Kraken websocket, execution-sync, and replay-metadata surfaces that
CRYPTO-002 was meant to land. This refresh aligns the packet with current
repo truth and with the already-approved parent review at
`docs/reviews/2026-04-24-app-003-datasource-crypto-002-codex-review.md`.

Current repo-local read:

1. Kraken websocket subscription and ticker normalization are implemented in
   `services/execution/kraken_adapter.py`.
2. REST plus websocket execution-sync reconciliation is implemented in the
   same adapter and preserves REST `close` while preferring websocket realtime
   fields.
3. Replay metadata now flows through the crypto reference join path and is
   covered in `services/data-plane/tests/test_data_plane_schemas.py`.

Disposition: this sidecar packet has been approved by Claude as a support-only
acceptance handoff. It now serves as the owner-finalize packet for the sidecar
closeout and does not replace the parent review, parent finalization, or
canonical truth.

## Acceptance Read

Parent task acceptance (from the parent review lane):

1. `Kraken WebSocket path lands for realtime market data`
2. `execution-sync logic is aligned with REST snapshots`
3. `crypto venue-scoped runtime can replay websocket-backed state truthfully`

Current read:

| Criterion | Result | Note |
|---|---|---|
| Kraken WebSocket path lands for realtime market data | landed | `KrakenWebSocketSubscription` plus `KrakenAdapter.build_websocket_subscription()` and `normalize_websocket_ticker()` are present in `services/execution/kraken_adapter.py`. `services/execution/test_kraken_adapter.py` covers websocket subscription payload shape and ticker normalization. |
| execution-sync logic is aligned with REST snapshots | landed | `KrakenAdapter.reconcile_execution_sync()` combines REST and websocket payloads, prefers websocket realtime values for `last`/`bid`/`ask`/`volume`, and preserves REST `close` as the sync anchor. This is covered by `test_reconcile_execution_sync_prefers_ws_realtime_and_rest_close`. |
| crypto venue-scoped runtime can replay websocket-backed state truthfully | landed at support scope | `KrakenExecutionSyncState` emits `transport="websocket"`, `sync_source="rest_plus_websocket"`, `replay_source="websocket_backed_sync"`, and `is_replayable=True`. `join_kraken_quote_with_reference()` preserves these fields into the joined rows, and `test_join_kraken_quote_with_reference_preserves_websocket_replay_metadata` verifies the replay-ready path. This sidecar does not claim a new standalone runtime fixture beyond the landed metadata path. |

Support-packet caveat:

1. This packet is a review aid for a support slice, not the parent review
   record.
2. The replay criterion is satisfied here only to the extent demonstrated in
   repo-local adapter and reference-join evidence; any broader runtime proof
   still belongs to the parent lane's acceptance story.

## Evidence Snapshot

- Scope governance:
  - `DATA_SOURCE_SCOPE_MATRIX.md` lines 50-57 and lines 92-104 keep Kraken as
    execution-truth provider and CoinGecko as reference enrichment only.
- Landed implementation surfaces:
  - `services/execution/crypto_symbol_utils.py` centralizes Kraken pair
    parsing and base-asset extraction, including non-hardcoded quote suffixes.
  - `services/execution/kraken_adapter.py` now contains:
    `KrakenWebSocketSubscription`,
    `KrakenAdapter.build_websocket_subscription()`,
    `KrakenAdapter.normalize_websocket_ticker()`,
    `KrakenAdapter.reconcile_execution_sync()`, and
    `KrakenExecutionSyncState`.
  - `services/execution/test_kraken_adapter.py` now contains websocket and
    REST-plus-websocket reconciliation coverage.
  - `services/data-plane/crypto_reference.py` now uses
    `extract_kraken_base_asset()` and preserves replay metadata fields from
    joined quote rows.
  - `services/data-plane/tests/test_data_plane_schemas.py` verifies websocket
    replay metadata preservation and non-USD quote suffix joins.
- Parent review precedent:
  - `docs/reviews/2026-04-24-app-003-datasource-crypto-002-codex-review.md`
    already approved the parent slice after replaying the MATIC/GBP join repro
    and re-running adapter plus data-plane suites.

## Dependency Map

| Surface | Role for reviewer / finalizer | Current read |
|---|---|---|
| `DATA_SOURCE_SCOPE_MATRIX.md` | Canonical scope anchor | Confirms Kraken remains venue-scoped execution truth and CoinGecko remains reference-only. The sidecar must not imply scope expansion into `crypto_analytics`. |
| `services/execution/crypto_symbol_utils.py` | Shared symbol truth | Prevents websocket and reference-join paths from hardcoding USD-only suffix handling. |
| `services/execution/kraken_adapter.py` | Core acceptance surface | Holds the REST baseline, websocket subscription, websocket ticker normalization, and REST/websocket reconciliation logic. |
| `services/execution/test_kraken_adapter.py` | Adapter proof surface | Shows both REST behavior and websocket/sync behavior in one place; this is the quickest acceptance entrypoint for Claude. |
| `services/data-plane/crypto_reference.py` | Replay metadata bridge | Carries websocket-backed sync rows into joined reference output without dropping replay semantics. |
| `services/data-plane/tests/test_data_plane_schemas.py` | Join-path proof surface | Verifies replay metadata preservation and non-USD suffix joins through the canonical data-plane helper path. |
| `docs/reviews/2026-04-24-app-003-datasource-crypto-002-codex-review.md` | Parent review truth | Records that the parent reviewer found no blocking issues and documents the verification set the sidecar should not contradict. |

## Verification Snapshot

This refresh revalidated the sidecar packet against current repo state.

Test runs from this session:

1. `python3 -m unittest services.execution.test_kraken_adapter -v`
   Result: 10 tests passed, including websocket subscription, websocket ticker
   normalization, REST/websocket execution-sync reconciliation, and Kraken
   LEAN symbol parsing.
2. `python3 -m unittest services.data-plane.tests.test_data_plane_schemas -v`
   Result: 54 tests passed, including
   `test_join_kraken_quote_with_reference_preserves_websocket_replay_metadata`
   and
   `test_join_kraken_quote_with_reference_supports_non_usd_quote_suffixes`.
3. `rg -n 'websocket|WebSocket|ws_' services/execution`
   Result: current workspace contains Kraken websocket surfaces in
   `services/execution/kraken_adapter.py` and websocket-focused tests in
   `services/execution/test_kraken_adapter.py`; the packet must therefore
   treat the parent slice as landed rather than not-started.

Verification note:

1. These support checks confirm the packet now matches the repo.
2. The sidecar intentionally relies on the existing parent review for the
   broader approval disposition instead of restating it as new canonical
   truth.

## Known Non-Blocking Observations

1. The parent review already approved CRYPTO-002, so the only thing this
   sidecar needed was truth reconciliation, not new implementation work.
2. The replay proof visible from this sidecar is metadata-path proof through
   the adapter and data-plane join. If a later slice needs end-to-end runtime
   replay fixtures, that should be tracked explicitly rather than implied
   retroactively here.
3. `DATA_SOURCE_SCOPE_MATRIX.md` still keeps open interest, liquidations, and
   on-chain data in `crypto_analytics`; none of those are part of this packet.

## Reviewer Checklist

Before approving this sidecar, confirm:

1. The packet stays support-only and does not claim to replace the parent
   review or canonical architecture truth.
2. The acceptance table now matches the repo: websocket path, execution-sync,
   and replay-metadata path are all landed rather than "not-started".
3. The dependency map points at the real acceptance surfaces Claude would use
   to sanity-check the sidecar and parent closeout context.
4. The verification snapshot matches the test results from this refresh and
   does not claim more than the current workspace proves.
