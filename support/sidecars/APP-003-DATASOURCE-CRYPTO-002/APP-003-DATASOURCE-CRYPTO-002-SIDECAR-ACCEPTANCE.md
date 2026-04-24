# APP-003-DATASOURCE-CRYPTO-002 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `APP-003-DATASOURCE-CRYPTO-002-SIDECAR-ACCEPTANCE`
**Helper parent:** `APP-003-DATASOURCE-CRYPTO-002`
**Parent owner:** `Codex2`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Reviewer:** `Codex2`
**Date:** `2026-04-24`
**Status:** `review_ready`

> Scope constraint: support artifact only. This packet prepares the acceptance
> checklist and dependency map for the Kraken WebSocket realtime plus
> execution-sync slice before the parent owner begins implementation. It does
> not change L1 canonical truth, runtime/registry/governance implementations,
> or the parent task's ownership.

## Executive Summary

The parent task `APP-003-DATASOURCE-CRYPTO-002` is `todo` with `Codex2` as
owner and `Codex` as reviewer. It depends on `APP-003-DATASOURCE-CRYPTO-001`,
which landed on `master` as commit `46ed8ab` (`APP-003-DATASOURCE-CRYPTO-001
finalize crypto datasource integration`). CRYPTO-001 established the REST-side
execution and reference boundary for Kraken plus CoinGecko; CRYPTO-002 extends
that boundary to realtime WebSocket ingest and REST/websocket execution-sync
truth.

This packet gives the sidecar reviewer three things without touching canonical
truth:

1. a repo-local acceptance read for each of the three parent acceptance
   criteria, referencing the landed CRYPTO-001 surfaces that CRYPTO-002 must
   extend,
2. a dependency map pointing the parent reviewer at the specific execution,
   data-plane, and runtime surfaces CRYPTO-002 will need to touch or mirror,
3. a verification snapshot recording what is currently on disk for the
   CRYPTO-002 scope (nothing new landed yet) so the reviewer has an accurate
   baseline before parent work begins.

Disposition: this sidecar is ready for review as a pre-implementation support
packet. Approval of this packet means the packet is accurate and useful as a
reviewer aid; it does not approve the parent task, does not preempt parent
review, and does not treat "`todo` scope captured" as "scope delivered."

## Acceptance Read

Parent task acceptance (from `ai-status.json`):

1. `Kraken WebSocket path lands for realtime market data`
2. `execution-sync logic is aligned with REST snapshots`
3. `crypto venue-scoped runtime can replay websocket-backed state truthfully`

Current read:

| Criterion | Result | Note |
|---|---|---|
| Kraken WebSocket path lands for realtime market data | not-started | `services/execution/kraken_adapter.py` currently provides REST-level pair/order/market-data/quote construction only. No `KrakenWebSocket*` surface, no `build_ws_subscribe_payload`, and `test_kraken_adapter.py` has no websocket coverage. CRYPTO-002 must introduce the realtime boundary. |
| execution-sync logic is aligned with REST snapshots | not-started | `KrakenAdapter.build_market_data_request()` and `KrakenAdapter.normalize_quote()` give REST snapshot truth; there is no websocket-to-REST reconciliation helper yet. CRYPTO-002 must introduce a sync/reconcile path and evidence that ws-derived state cannot diverge silently from REST truth. |
| Crypto venue-scoped runtime can replay websocket-backed state truthfully | not-started | `services/execution/lean_runtime/symbol_parser.py` already routes dotted and heuristic crypto pairs to `Market.Kraken` (landed in CRYPTO-001), but there is no crypto-specific pending-signal or replay surface. CRYPTO-002 must wire ws-backed realtime state into the existing runtime/replay machinery (`pending_signal_store.py`, `paper_runtime.py`, or equivalent) without breaking existing non-crypto consumers. |

Support-packet caveat:

1. "not-started" here means the parent acceptance criterion has no landed
   implementation in the current working tree; it is not a judgment on the
   parent owner.
2. This packet is explicitly a pre-implementation baseline. Parent approval
   still belongs to `Codex` in the main task lane once CRYPTO-002 lands.

## Evidence Snapshot

- Dependency baseline (landed in CRYPTO-001):
  - `services/execution/kraken_adapter.py` — REST-level Kraken boundary
    (`normalize_kraken_symbol`, `KrakenAdapter.build_contract`, `build_order`,
    `build_market_data_request`, `normalize_quote`).
  - `services/execution/test_kraken_adapter.py` — REST-side adapter coverage.
  - `services/data-plane/crypto_reference.py` — crypto security master,
    dataset lineage, and `join_kraken_quote_with_reference()` helpers.
  - `services/data-plane/tests/test_data_plane_schemas.py` — crypto reference
    helper coverage including the join path.
  - `services/execution/lean_runtime/symbol_parser.py` — Kraken venue routing
    for dotted and heuristic crypto symbols.
  - `services/research/adapters/coingecko_client.py` — governed reference
    adapter for CoinGecko.
- Scope governance:
  - `DATA_SOURCE_SCOPE_MATRIX.md` §2.3 lines 92-104 — crypto market data needs
    (spot OHLCV, perpetual OHLCV + funding, dated futures OHLCV, open
    interest, liquidation data, on-chain data).
  - `DATA_SOURCE_SCOPE_MATRIX.md` line 50 — Kraken is the primary venue-scoped
    execution plus canonical venue market-data source.
- Parent-task deliverables NOT yet present in repo:
  - No `services/execution/kraken_websocket*.py` or equivalent realtime
    adapter.
  - No `services/execution/test_kraken_websocket*.py` coverage.
  - No crypto-specific REST/ws reconciliation helper in either
    `services/execution/` or `services/data-plane/`.
  - No ws-backed replay fixture in `services/execution/lean_runtime/` test
    coverage.
- Parent-review baseline that CRYPTO-002 must not regress:
  - `docs/reviews/2026-04-24-app-003-datasource-crypto-001-codex-rereview.md`
    — the rereview record that closed the CoinGecko/Kraken asset-to-pair join
    mismatch flagged during the CRYPTO-001 review. CRYPTO-002 must not break
    the now-passing `join_kraken_quote_with_reference()` path when introducing
    ws-sourced quote snapshots.

## Dependency Map

| Surface | Role for CRYPTO-002 | Current read |
|---|---|---|
| `DATA_SOURCE_SCOPE_MATRIX.md` | Canonical scope anchor | Defines Kraken as venue-scoped execution truth and lists the crypto data needs realtime ingest must satisfy. Parent must not widen scope beyond this matrix. |
| `services/execution/kraken_adapter.py` | REST truth baseline | New websocket surface must either extend this module or sit alongside it without changing the REST contract. Quote shape should stay compatible with `KrakenQuoteSnapshot`. |
| `services/execution/test_kraken_adapter.py` | REST coverage baseline | Existing REST tests must keep passing; new ws tests should be added in a parallel file (e.g. `test_kraken_websocket_adapter.py`) or extend this file without regressing REST cases. |
| `services/execution/lean_runtime/symbol_parser.py` | Runtime symbol truth | Already routes dotted and heuristic crypto to `Market.Kraken`. Any ws-to-runtime bridge must use the same symbol parsing to avoid a second crypto routing path. |
| `services/execution/lean_runtime/pending_signal_store.py` | Runtime replay surface | Likely consumer of ws-backed state. CRYPTO-002 must show whether ws quotes feed signals through this path or a distinct crypto-only path, and keep replay semantics truthful. |
| `services/execution/lean_runtime/paper_runtime.py` | Paper/canary runtime | Any "replay websocket-backed state truthfully" claim must be exercised against this runtime's existing replay or be paired with a dedicated crypto replay fixture. |
| `services/data-plane/crypto_reference.py` | Reference join surface | `join_kraken_quote_with_reference()` currently joins REST quote snapshots. CRYPTO-002 must confirm whether ws-derived quotes are mapped into the same join shape or go through a new helper that preserves `reference_provider="CoinGecko"` boundary. |
| `services/data-plane/tests/test_data_plane_schemas.py` | Data-plane proof surface | `TestCryptoReferenceHelpers` coverage must keep passing. Any new ws-to-reference helper should be tested here (or a sibling test module) with the same governance assertions. |
| `docs/reviews/2026-04-24-app-003-datasource-crypto-001-codex-rereview.md` | Parent review precedent | Sets the asset-to-pair normalization truth CRYPTO-002 must preserve when wiring ws-derived quote symbols. |

## Verification Snapshot

This sidecar did not add or modify runtime code. Verification was limited to
confirming the CRYPTO-001 baseline still holds and that no CRYPTO-002 surfaces
have landed yet.

Test runs from this session:

1. `python3 -m unittest services.execution.test_kraken_adapter -v`
   Result: 7 tests passed (REST-side adapter contract plus Kraken LEAN symbol
   parsing regression intact).
2. `python3 -m unittest services.data-plane.tests.test_data_plane_schemas -v`
   Result: 52 tests passed, including all 5 `TestCryptoReferenceHelpers`
   cases (crypto security master, raw/normalized dataset, dataset lineage
   source, and both `join_kraken_quote_with_reference` variants) — the join
   path is intact post CRYPTO-001 rereview.
3. Working-tree scan for `websocket` / `WebSocket` / `ws_` in
   `services/execution/` returned only `ibkr_adapter.py` hits and no Kraken
   ws surface.

Verification note:

1. The passing REST suites confirm that CRYPTO-002 has a stable REST baseline
   to build on, so any failure after CRYPTO-002 lands should be attributed to
   the new ws or sync layer, not to CRYPTO-001 drift.
2. The empty Kraken-ws scan confirms this packet correctly treats the parent
   task as not yet started.
3. These support runs are evidence of the CRYPTO-001 baseline only. Parent
   approval still belongs to `Codex` once the CRYPTO-002 ws and sync surfaces
   land and the parent-review reproduction passes.

## Known Non-Blocking Observations

1. The CRYPTO-001 rereview closed the CoinGecko asset-to-pair normalization
   gap; CRYPTO-002 should preserve that fix rather than recreating its own
   mapping inside a ws path.
2. `services/execution/ibkr_adapter.py` already has ws-style patterns the
   parent owner may reference as prior art for shape, but Kraken's auth and
   channel model is distinct and should not be copy-ported blindly.
3. `DATA_SOURCE_SCOPE_MATRIX.md` §2.3 marks open interest, liquidations, and
   on-chain data as `crypto_analytics` (not `broker_execution`). Parent scope
   is limited to Kraken execution realtime and its REST/ws sync; expanding
   into `crypto_analytics` ingest belongs to a separate slice.
4. No `crypto_analytics` adapter is required by CRYPTO-002 acceptance, so the
   reviewer should not read scope expansion into this packet.

## Reviewer Checklist

Before approving this sidecar, confirm:

1. The packet stays support-only and does not claim ownership of parent review
   or parent finalization.
2. The acceptance read is accurate for the pre-implementation baseline: all
   three criteria are "not-started" in repo, and each row points at a
   concrete existing surface that CRYPTO-002 must extend or preserve.
3. The dependency map points the reviewer at the specific landed files that
   CRYPTO-002 work will touch or must not regress — especially the Kraken
   REST adapter, the LEAN symbol parser, the pending-signal/replay surfaces,
   and the `join_kraken_quote_with_reference()` path.
4. The verification snapshot records the actual REST-baseline test runs from
   this session and does not claim ws or sync verification that the parent
   task has not yet produced.
5. Approval of this sidecar should be interpreted only as "the packet is an
   accurate pre-implementation baseline," not as "the parent task is ready to
   close."
