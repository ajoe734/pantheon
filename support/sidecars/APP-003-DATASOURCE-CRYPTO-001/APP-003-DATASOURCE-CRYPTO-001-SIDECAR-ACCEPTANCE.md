# APP-003-DATASOURCE-CRYPTO-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `APP-003-DATASOURCE-CRYPTO-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `APP-003-DATASOURCE-CRYPTO-001`
**Parent owner:** `Codex2`
**Parent reviewer:** `Codex`
**Parent status:** `done`
**Prepared by:** `Codex`
**Reviewer:** `Claude`
**Date:** `2026-04-24`
**Status:** `review_ready`

> Scope constraint: support artifact only. This packet summarizes the final
> repo-local acceptance state for the `Kraken` plus `CoinGecko` crypto
> datasource slice without changing L1 canonical truth, runtime/governance
> policy, or the archived parent-task record.

## Executive Summary

The parent task `APP-003-DATASOURCE-CRYPTO-001` closed as `done` at
`2026-04-24T17:32:37Z` after `Codex` re-review approved the final fixes. This
sidecar now serves as a post-closeout support packet: a compact acceptance
read, dependency map, and verification snapshot for the landed crypto support
surfaces.

This refreshed packet supersedes earlier drafts that were written while the
parent review was still open and while two sequential blockers were still
active:

1. the `CoinGecko` asset-to-`Kraken` pair join mismatch
2. the `KrakenAdapter.normalize_quote()` loss of distinct payload `last` and
   `close` values

Repo-local final state:

1. `DATA_SOURCE_SCOPE_MATRIX.md` documents the intended split: `Kraken` is the
   venue-scoped execution plus canonical venue market-data source, while
   `CoinGecko` remains reference / metadata / research-only.
2. `services/execution/kraken_adapter.py` provides the venue-scoped crypto
   execution boundary and now preserves distinct payload `last` and `close`
   values while still falling back between them when only one field is present.
3. `services/research/adapters/coingecko_client.py` preserves the
   `research_grade` governance boundary and explicitly states that CoinGecko
   does not replace `Kraken` execution truth.
4. `services/data-plane/crypto_reference.py` provides crypto-specific helpers
   for security master construction, dataset lineage records, and
   `Kraken` plus `CoinGecko` joins using truthful `CoinGeckoClient.normalize_asset()`
   output.
5. `services/execution/lean_runtime/symbol_parser.py` maps dotted and
   heuristic crypto symbols to `Market.Kraken`, aligning runtime parsing with
   the canonical crypto venue default.
6. Parent closeout evidence now shows both historical blockers are resolved
   and the full slice is recorded as completed in the archived parent task.

Disposition: this refreshed sidecar is ready for re-review as a support
artifact. Approval of this packet only means the support material is current
and accurate; it does not alter the already-closed parent task.

## Acceptance Read

Parent task acceptance:

1. `Kraken execution and market-data integration lands`
2. `CoinGecko reference path is wired without becoming execution truth`
3. `crypto venue-scoped canonical mapping is documented and implemented`

Current read:

| Criterion | Result | Note |
|---|---|---|
| `Kraken` execution and market-data integration lands | supported | `services/execution/kraken_adapter.py` plus `services/execution/test_kraken_adapter.py` cover pair normalization, order payload construction, market-data request payloads, quote normalization, and the distinct `last`/`close` regression. |
| `CoinGecko` reference path is wired without becoming execution truth | supported | `services/research/adapters/coingecko_client.py` preserves the reference-only boundary, and `services/data-plane/crypto_reference.py` plus `services/data-plane/tests/test_data_plane_schemas.py` now join truthful `CoinGeckoClient.normalize_asset()` output to `Kraken` quotes while preserving `quote_close` when `last` differs. |
| Crypto venue-scoped canonical mapping is documented and implemented | supported | `DATA_SOURCE_SCOPE_MATRIX.md` documents the provider split, `services/data-plane/crypto_reference.py` emits `Kraken`/`CoinGecko`-aware canonical records, and `services/execution/lean_runtime/symbol_parser.py` defaults crypto symbols to `Market.Kraken`. |

Support-packet caveat:

1. This table is a review aid for the sidecar reviewer, not a second approval
   path for the parent task. Parent closure already lives in the archived task
   record and the parent re-review file.
2. Earlier packet drafts that marked criterion 2 as blocked are now historical
   only and should not be treated as the current repo-local state.

## Evidence Snapshot

- Scope governance:
  - `DATA_SOURCE_SCOPE_MATRIX.md` classifies `Kraken` as the primary
    venue-scoped execution plus canonical market-data source and `CoinGecko`
    as reference / metadata / research supplement only.
- Execution boundary:
  - `services/execution/kraken_adapter.py` introduces
    `normalize_kraken_symbol` plus `KrakenAdapter.build_contract`,
    `build_order`, `build_market_data_request`, and `normalize_quote`.
  - `services/execution/test_kraken_adapter.py` verifies compact/slashed symbol
    formats, `provider="Kraken"`, `sourceClass="broker_execution"`,
    `boundaryRole="venue_canonical"`, `Kraken` LEAN symbol parsing, and the
    distinct `last`/`close` regression.
- Runtime symbol truth:
  - `services/execution/lean_runtime/symbol_parser.py` maps both `.KRAKEN` and
    heuristic no-dot crypto pairs to `Market.Kraken`, replacing the stale
    Coinbase/Binance-default mismatch called out in earlier drafts.
- Research/reference boundary:
  - `services/research/adapters/coingecko_client.py` defines
    `CoinGeckoClient.SOURCE_SPEC` with `source_class="research_grade"` and a
    governance note that CoinGecko does not replace `Kraken` execution truth.
  - `services/research/adapters/test_adapters.py` includes
    `TestCoinGeckoClient.test_asset_normalization_keeps_reference_boundary`,
    which asserts that governance metadata preserves that rule.
- Data-plane helpers:
  - `services/data-plane/crypto_reference.py` builds
    `SecurityMaster.symbol_canonical` as `{ALTNAME}.KRAKEN`, emits raw and
    normalized dataset lineage records with
    `execution_truth_provider="Kraken"` and
    `reference_provider="CoinGecko"`, and joins venue quotes with metadata
    from real `CoinGecko`-normalized output.
  - `services/data-plane/tests/test_data_plane_schemas.py` contains dedicated
    `TestCryptoReferenceHelpers` coverage for security master construction,
    raw/normalized dataset lineage, lineage-source payloads, truthful
    `Kraken` plus `CoinGecko` joins, and preservation of a payload-provided
    `close` when `last` differs.
- Parent closeout evidence:
  - `docs/reviews/2026-04-24-app-003-datasource-crypto-001-codex-rereview.md`
    records the final parent approval after both historical blockers were
    closed.
  - `ai-task-archive/tasks/APP-003-DATASOURCE-CRYPTO-001.json` records the
    archived parent task as `done` with the closeout summary that `Kraken`
    execution, `CoinGecko` reference wiring, and distinct `Kraken`
    `last`/`close` semantics are complete.

## Dependency Map

| Surface | Role in review | Current read |
|---|---|---|
| `DATA_SOURCE_SCOPE_MATRIX.md` | Canonical scope anchor | Defines `Kraken` as execution truth and `CoinGecko` as research/reference-only |
| `services/execution/kraken_adapter.py` | Execution integration surface | Venue-scoped pair/order/market-data contract builder for crypto, with distinct payload `last`/`close` preservation |
| `services/execution/test_kraken_adapter.py` | Execution proof surface | Verifies Kraken adapter payloads, LEAN symbol parsing alignment, and the distinct-price regression |
| `services/execution/lean_runtime/symbol_parser.py` | Runtime symbol truth | Aligns dotted and heuristic crypto parsing to `Market.Kraken` |
| `services/research/adapters/coingecko_client.py` | Governed reference adapter | Preserves reference-only governance metadata for `CoinGecko` and exposes the truthful normalized asset payload used by the join |
| `services/research/adapters/test_adapters.py` | Research-boundary proof | Confirms `CoinGecko` normalization keeps the reference boundary intact |
| `services/data-plane/crypto_reference.py` | Data-plane helper surface | Emits crypto security master, raw/normalized lineage, and the now-closed quote/reference join |
| `services/data-plane/tests/test_data_plane_schemas.py` | Data-plane proof surface | Covers crypto helper payloads, real `CoinGecko` join output, and preservation of `quote_close` when `last` differs |
| `docs/reviews/2026-04-24-app-003-datasource-crypto-001-codex-rereview.md` | Parent re-review record | Canonical review evidence for the final approval state |
| `ai-task-archive/tasks/APP-003-DATASOURCE-CRYPTO-001.json` | Parent terminal record | Durable closeout record showing the parent task is already `done` |

## Verification Snapshot

This sidecar did not add or modify runtime code. Verification for this refresh
was limited to confirming the final parent closeout state and rerunning the
same repo-local evidence bundle after parent closure.

Test runs from this refresh:

1. `python3 -m unittest services.execution.test_kraken_adapter services.research.adapters.test_adapters services.data-plane.tests.test_data_plane_schemas -v`
   Result: 80 tests passed.
2. `python3 services/data-plane/smoke_test.py`
   Result: 47 / 47 checks passed.
3. `python3 -c "from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig; import importlib.util, pathlib; path=pathlib.Path('services/data-plane/crypto_reference.py'); spec=importlib.util.spec_from_file_location('crypto_reference', path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); from services.research.adapters.coingecko_client import CoinGeckoClient; adapter=KrakenAdapter(KrakenConfig(api_key='k', api_secret='s')); quote=adapter.normalize_quote({'ts':'2026-04-24T16:00:00Z','last':'64321.1','close':'64320.4','bid':'64320.1','ask':'64321.0','volume':'128.55'}, 'BTCUSD.KRAKEN').to_dict(); metadata=CoinGeckoClient(rate_limit_delay=0).normalize_asset({'id':'bitcoin','symbol':'btc','name':'Bitcoin','market_cap_rank':1}).to_dict(); joined=mod.join_kraken_quote_with_reference([quote],[metadata]); print({'quote_last': quote['last'], 'quote_close': quote['close'], 'joined_quote_close': joined[0]['quote_close']})"`
   Result: `{'quote_last': 64321.1, 'quote_close': 64320.4, 'joined_quote_close': 64320.4}`

Verification note:

1. The unittest run covers the `Kraken` execution boundary, the
   `CoinGecko` governance boundary, and the crypto data-plane helpers together.
2. The smoke test confirms the broader schema/model lineage baseline still
   holds after the parent closeout.
3. The direct reproduction confirms the earlier lossy `last`/`close`
   normalization issue is resolved end to end at the adapter-to-join boundary.
4. These checks support the packet's accuracy, but the packet remains
   support-only and does not alter the already-closed parent task.

## Known Non-Blocking Observations

1. Earlier packet and review drafts referenced two historical blockers: the
   `CoinGecko` asset-to-pair join mismatch and the later distinct
   `last`/`close` normalization lossiness. Both are resolved in the parent
   closeout and should no longer be treated as open issues in this packet.
2. The earlier routing risk around stale `Coinbase` / `Binance` crypto defaults
   remains resolved by the landed `Market.Kraken` mapping in
   `services/execution/lean_runtime/symbol_parser.py`.
3. The durable sidecar reviewer assignment is `Claude`; this refresh aligns
   the packet header with the current `ai-status.json` task record.
4. Because the parent task is already archived as `done`, this packet now
   functions as support evidence for record consistency rather than as a live
   blocker-tracking aid.

## Reviewer Checklist

Before approving this refreshed sidecar, confirm:

1. The packet stays support-only and does not claim ownership of parent review
   or parent finalization.
2. The acceptance read matches the final parent state: all three parent
   criteria are now supported.
3. The dependency map points the reviewer at the specific landed files that
   carry the `Kraken`/`CoinGecko` support evidence plus the parent re-review
   and terminal record.
4. The verification snapshot records the actual tests rerun for this refresh:
   80 unittest cases, 47 / 47 smoke checks, and the direct distinct-price
   reproduction.
5. Approval of this sidecar should be interpreted only as "the refreshed
   support packet is current and accurate," not as a change to the already
   completed parent task.
