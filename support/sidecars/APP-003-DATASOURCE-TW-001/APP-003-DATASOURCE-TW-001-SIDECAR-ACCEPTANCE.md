# APP-003-DATASOURCE-TW-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `APP-003-DATASOURCE-TW-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `APP-003-DATASOURCE-TW-001`
**Parent owner:** `Codex2`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude2`
**Reviewer:** `Claude`
**Date:** `2026-04-24`
**Status:** `done`

> Scope constraint: support artifact only. This packet summarizes the current
> Taiwan data-source slice for Shioaji execution plus TWSE, TPEx, MOPS, and TEJ
> reference/research ingestion without changing Pantheon canonical truth,
> runtime, or governance implementations.

## Executive Summary

The parent task `APP-003-DATASOURCE-TW-001` is currently in `review` with
`Codex2` as owner and `Codex` as reviewer. The scope matrix, execution
boundary, and research/reference adapters for Taiwan are all present in the
working tree; this packet records the current support read so the parent
reviewer can confirm acceptance without re-scanning the whole slice.

Verified current state:

1. Shioaji execution and quote boundary is implemented at the contract level in
   `services/execution/shioaji_adapter.py` with 6 passing adapter tests.
2. The LEAN `Symbol.Create()` parser explicitly excludes Taiwan venue symbols
   and routes them through Shioaji (`services/execution/lean_runtime/symbol_parser.py`
   lines 10-11 and 60-61), matched by a regression test in
   `services/execution/test_shioaji_adapter.py::TestTaiwanLeanSymbolParsing`.
3. Taiwan reference helpers for security master, calendar session, and dataset
   lineage are available in `services/data-plane/taiwan_reference.py` and
   covered by `services/data-plane/tests/test_data_plane_schemas.py::TestTaiwanReferenceHelpers`.
4. TWSE, TPEx, MOPS, and TEJ ingest paths are contract-complete in
   `services/research/adapters/taiwan_market_client.py` with normalization
   tests in `services/research/adapters/test_adapters.py::TestTaiwanMarketClient`
   (4 passing).
5. Scope governance is documented in `DATA_SOURCE_SCOPE_MATRIX.md` §2.2 and the
   vendor-class row asserts `TEJ API` stays as research/reference and does not
   replace `TWSE OpenAPI`, `TPEx E-Data`, or `MOPS` official disclosure truth.

Disposition: sidecar remains support-only and does not modify canonical truth.
Awaiting reviewer sign-off before owner finalization; parent-task absorption
remains with `Codex` as the parent reviewer.

## Acceptance Read

Parent task acceptance:

1. `Shioaji execution and quote boundary is implemented`
2. `TWSE TPEx and MOPS ingest paths are wired or contract-complete`
3. `TEJ API adapter lands as Taiwan research/reference integration without replacing official disclosure truth`

Current read:

| Criterion | Result | Note |
|---|---|---|
| Shioaji execution and quote boundary is implemented | pass | `services/execution/shioaji_adapter.py` exposes `ShioajiAdapter`, `ShioajiConfig`, `ShioajiOrderIntent`, contract build, order build, quote subscription, and quote normalization; 6 tests pass in `services/execution/test_shioaji_adapter.py` |
| Taiwan symbols never fall through LEAN parser | pass | `services/execution/lean_runtime/symbol_parser.py` documents the Shioaji boundary in its module docstring and raises `SymbolParseError` for Taiwan venue suffixes; regression asserted in `TestTaiwanLeanSymbolParsing` |
| TWSE ingest path is contract-complete | pass | `services/research/adapters/taiwan_market_client.py` exposes a `TaiwanMarketClient.normalize_twse_listing` entry with governance metadata tagged `source_key="twse"`; normalization covered by `test_twse_listing_normalization` |
| TPEx ingest path is contract-complete | pass | `TaiwanMarketClient.normalize_tpex_listing` returns `venue="TPEx"` with governance metadata tagged `source_key="tpex"`; covered by `test_tpex_listing_normalization` |
| MOPS disclosure ingest path is contract-complete | pass | `TaiwanMarketClient.normalize_mops_disclosure` yields a `TaiwanDisclosureRecord` with governance metadata tagged `source_key="mops"`; covered by `test_mops_disclosure_normalization` |
| TEJ API stays as research/reference, not disclosure truth | pass | `DATA_SOURCE_SCOPE_MATRIX.md` line 49 classifies `TEJ API` under `research_grade`; line 56 explicitly states TEJ does not replace TWSE/TPEx/MOPS; `TaiwanMarketClient.normalize_tej_dataset` preserves the vendor boundary via `test_tej_dataset_normalization_keeps_vendor_boundary` |
| Data-plane reference helpers exist for Taiwan canonical models | pass | `services/data-plane/taiwan_reference.py` builds `SecurityMaster`, `MarketCalendarSession`, and dataset lineage source records keyed to `market="TW"`; `TestTaiwanReferenceHelpers` exercises all three |

## Evidence Snapshot

- Execution boundary:
  - `services/execution/shioaji_adapter.py` — contract-level Shioaji adapter
    with venue aliasing, order intent, and quote normalization.
  - `services/execution/test_shioaji_adapter.py` — 6 tests covering listing
    suffixes, TAIFEX contract inference, account defaulting, quote
    subscription, numeric quote mapping, and LEAN parser rejection.
  - `services/execution/lean_runtime/symbol_parser.py` — docstring plus the
    `parse()` boundary call-out that Taiwan venues stay on Shioaji.
- Data-plane reference:
  - `services/data-plane/taiwan_reference.py` — `build_tw_security_master`,
    `build_tw_calendar_session`, `build_tw_dataset_lineage_source`.
  - `services/data-plane/tests/test_data_plane_schemas.py::TestTaiwanReferenceHelpers`.
- Research/reference adapters:
  - `services/research/adapters/taiwan_market_client.py` — TWSE, TPEx, MOPS,
    TEJ normalization plus shared governance metadata.
  - `services/research/adapters/test_adapters.py::TestTaiwanMarketClient` — 4
    tests covering TWSE, TPEx, MOPS, and TEJ normalization boundaries.
- Scope governance:
  - `DATA_SOURCE_SCOPE_MATRIX.md` §2.2 (Taiwan Market) plus lines 45-56 in the
    top-of-file vendor classification.
- Test runs (this session):
  - `python3 -m unittest services.execution.test_shioaji_adapter` — 6 ok.
  - `python3 -m unittest test_adapters.TestTaiwanMarketClient` (run from
    `services/research/adapters`) — 4 ok.

## Dependency Map

| Surface | Role in review | Current read |
|---|---|---|
| `DATA_SOURCE_SCOPE_MATRIX.md` | Scope governance | Parent-task canonical scope reference; confirms Taiwan vendor classification and TEJ research boundary |
| `services/execution/shioaji_adapter.py` | Execution contract | Primary Shioaji execution/quote adapter at the contract boundary |
| `services/execution/test_shioaji_adapter.py` | Execution evidence | 6 tests including explicit LEAN parser rejection of Taiwan suffixes |
| `services/execution/lean_runtime/symbol_parser.py` | LEAN boundary guard | Excludes Taiwan venue codes from `Symbol.Create()` routing |
| `services/data-plane/taiwan_reference.py` | Canonical reference shaping | Normalizes Taiwan listings, calendar sessions, and dataset lineage source records |
| `services/data-plane/tests/test_data_plane_schemas.py` | Data-plane evidence | `TestTaiwanReferenceHelpers` exercises the Taiwan reference helpers |
| `services/research/adapters/taiwan_market_client.py` | Research/reference ingest | TWSE, TPEx, MOPS, TEJ normalization plus governance metadata |
| `services/research/adapters/test_adapters.py` | Research evidence | `TestTaiwanMarketClient` covers normalization for all four sources |

## Known Non-Blocking Observations

1. Parent-task files (`services/data-plane/taiwan_reference.py`,
   `services/execution/shioaji_adapter.py`,
   `services/execution/test_shioaji_adapter.py`,
   `services/research/adapters/taiwan_market_client.py`) are tracked in HEAD
   via commit `244825c` ("APP-003-DATASOURCE-TW-001 implement Taiwan
   datasource boundary"). The earlier "still untracked" note from an
   in-progress working tree is therefore resolved as of this finalization.
2. The sidecar does not reopen the parent contract scope. Parent owner decides
   whether to fold these observations into the canonical review trail.

## Reviewer Checklist

Before approving this sidecar, confirm:

1. The sidecar stays support-only and does not assert any canonical truth
   changes beyond referencing checked-in and currently-on-disk artifacts.
2. The three parent-task acceptance items are reflected truthfully in the
   Acceptance Read table, with each row pointing to a concrete file path and
   test.
3. The dependency map accurately reflects the surfaces a parent reviewer would
   consult for Shioaji execution, Taiwan reference helpers, and TWSE/TPEx/MOPS/TEJ
   research ingestion.
4. The commit-status observation in "Known Non-Blocking Observations" is flagged
   to the parent owner but does not block sidecar closure.
5. If review rejects this bundle, the rejection should cite a specific truth
   mismatch in the acceptance read or dependency map rather than a missing
   summary line.
