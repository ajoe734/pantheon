# SRCLIVE-004 BFF and Frontend Handoff Packet

**Parent Task**: `SRCLIVE-004` - overlay regression tests and three-persona
live readback acceptance
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude2`
**Parent Status at packet time**: `todo`
**Sidecar Task**: `SRCLIVE-004-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, source-ingest code, registry/governance behavior,
or frontend code. Parent ownership and review decide whether to absorb any of
this into the main SRCLIVE-004 delivery.

---

## 1. Scope

SRCLIVE-004 is the guardrail and live acceptance slice for the SRCLIVE readback
work. It should prove that the BFF source-health overlay is the only green path
for live provider chips, then run a dev BFF readback over the three market
personas:

1. `persona-tw-equity`
2. `persona-us-equity`
3. `persona-crypto`

This sidecar packages the handoff facts for the parent owner:

1. BFF query surfaces to use for contract tests and live e2e.
2. Current provider-key to connector-id mapping state.
3. Gaps that can block SRCLIVE-004 until SRCLIVE-001 and SRCLIVE-002 finish.
4. Frontend/operator chip rules that avoid cached or hardcoded green states.
5. Suggested verifier shape for `scripts/verify_srclive_readback.py`.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py` persona seed truth;
- no edits to BFF tests, source-ingest connectors, active-universe rules, or
  runbooks;
- no new frontend route or direct source-ingest browser call;
- no approval of parent task `SRCLIVE-004`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001` | TW dependency status; code PR merged but live acceptance is blocked on dev deploy/source-ingest availability |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | US dependency status; still `in_progress` and required before seven-chip US acceptance |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Crypto dependency status; archived `done` with CoinGecko mapping and smoke evidence |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004` | parent acceptance summary and artifact targets |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF` | sidecar scope and support-only boundary |
| `services/control-plane/bff/main.py` | `_overlay_source_health_truth`, provider candidates, source-health projection, persona-fleet use site |
| `services/control-plane/bff/read_store.py` | current market persona seed data-source rows and required-source bindings |
| `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py` | existing overlay contract coverage for TW/FinMind and CoinGecko |
| `docs/05/srclive/tw-activation-runbook.md` | TW live activation and BFF overlay verification path |
| `support/sidecars/SRCLIVE-002/SRCLIVE-002-SIDECAR-BFF-HANDOFF.md` | US BFF/frontend gap packet inherited by SRCLIVE-004 |
| `support/sidecars/SRCLIVE-003/SRCLIVE-003-SIDECAR-BFF-HANDOFF.md` | crypto BFF/frontend handoff packet inherited by SRCLIVE-004 |

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 3. Dependency Snapshot

| Task | Packet-time state | SRCLIVE-004 implication |
|---|---|---|
| `SRCLIVE-001` | `blocked`; PR `#2517` merged at `8da3d35766a041bfbb7b85aa018ee4ef65114cfd`, but dev deploy is blocked by dirty VM state and source-ingest external port still times out | TW e2e cannot be claimed until dev BFF and source-ingest expose live `status: ok` health for the TW connectors |
| `SRCLIVE-002` | `in_progress`; US research source wiring is not yet complete in task status | US e2e cannot be claimed until BFF has seven chips and the six US provider mappings/rows from SRCLIVE-002 |
| `SRCLIVE-003` | archived `done`; PR `#2516` merged at `73f52ec7410821e25cf3cc3810369f19f24188e8` | Crypto e2e can use `coingecko -> crypto-coingecko-spot` as the merged mapping baseline |
| `SRCLIVE-004` | `todo`; depends on 001/002/003 | Parent owner should start after dependencies are green or record a blocker instead of weakening live assertions |

SRCLIVE-004 should not convert dependency gaps into local pass conditions. If a
persona lacks BFF rows, provider mappings, source-ingest health, or deploy
freshness, the verifier should fail or report a blocked dependency.

---

## 4. BFF Surface Boundary

The frontend and operator verifier should stay behind BFF.

| Surface | Route | Parent use |
|---|---|---|
| Persona chips / fleet panel | `GET /bff/management/persona-fleet` | primary e2e readback for all three personas |
| Source registry/operator list | `GET /bff/management/data-sources` | optional diagnostic to confirm source-ingest registry is visible through BFF |
| Source-ingest health snapshot | source-ingest service route, not browser route | backend/operator diagnostic only; frontend should not call it directly |

The parent verifier should require an authorized BFF token and should fail
closed when the BFF returns `401`, degraded source-ingest projection, missing
persona rows, or missing provider chips.

---

## 5. Current Provider Mapping State

Current `origin/dev` BFF provider candidates:

| Provider key | Candidate connector ids | State |
|---|---|---|
| `finmind` | `tw-finmind-datasets`, `tw-finmind-broker-daily-report`, `tw-finmind-broker-bulk-parquet` | present |
| `twse` | `tw-twse-tpex-official-market` | present |
| `tpex` | `tw-twse-tpex-official-market` | present |
| `mops` | `tw-mops-official-disclosures` | present |
| `coingecko` | `crypto-coingecko-spot` | present from SRCLIVE-003 |

US provider mappings are not present in this branch at packet time:

| Provider key | Required connector id | Expected parent source |
|---|---|---|
| `stooq` | `us-stooq-daily-ohlcv` | SRCLIVE-002 |
| `sec_edgar` | `us-sec-edgar-filings` | SRCLIVE-002 |
| `finra` | `us-finra-short-sale` | SRCLIVE-002 |
| `fred` | `us-fred-macro` | SRCLIVE-002 |
| `polygon` | `us-polygon-daily-ohlcv` | SRCLIVE-002 |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` | SRCLIVE-002 |

The parent SRCLIVE-004 contract tests should therefore either run after
SRCLIVE-002 lands or explicitly fail with a dependency blocker. Do not invent
US mapping in the verifier or frontend.

---

## 6. Persona Acceptance Matrix

### 6.1 TW

Expected SRCLIVE-004 target is `persona-tw-equity=5/5`.

Current seed rows:

| Provider key | Static status | Source class | Notes |
|---|---|---|---|
| `shioaji` | `read_ok` | `broker_execution` | existing quote readback; order path disabled for marketdata smoke |
| `twse` | `read_unavailable` | `official_reference` | can become `read_ok` only through `tw-twse-tpex-official-market` health |
| `tpex` | `read_unavailable` | `official_reference` | shares `tw-twse-tpex-official-market` health |
| `mops` | `public_reference_unavailable` | `official_reference` | can become `read_ok` only through `tw-mops-official-disclosures` health |
| `finmind` | `read_unavailable` | `research_grade` | can become `read_ok` only through mapped FinMind source-ingest health |

Handoff risk: SRCLIVE-001's current runbook proves official TWSE/TPEx/MOPS
activation, but the parent `5/5` claim also requires FinMind to be green or the
parent owner to explain the counting contract. SRCLIVE-004 should not count
`shioaji` plus only the three official connectors as `5/5`.

### 6.2 US

Expected SRCLIVE-004 target is seven chips:

| Provider key | Expected status | Notes |
|---|---|---|
| `ibkr` | `read_ok` | existing broker readback; order path disabled for marketdata smoke |
| `stooq` | `read_ok` | requires SRCLIVE-002 mapping, row, active rule, and live health `ok` |
| `sec_edgar` | `read_ok` | requires compliant SEC user agent and live health `ok` |
| `finra` | `read_ok` | requires live health `ok`; publication delay can affect freshness |
| `fred` | `read_ok` | public CSV fallback may be keyless, but live health must still be `ok` |
| `polygon` | `credential_unavailable` | must include a reason and secret reference when no accepted key exists |
| `alphavantage` | `credential_unavailable` | must include a reason and secret reference when no accepted key exists |

Current `origin/dev` seed truth still has only `ibkr`. SRCLIVE-004 should treat
missing US rows or missing US provider mappings as dependency failure, not as a
partial pass.

### 6.3 Crypto

Expected SRCLIVE-004 target is `persona-crypto` with CoinGecko live:

| Provider key | Expected status | Notes |
|---|---|---|
| `kraken` | existing `datasource_smoke_ok` unless parent defines stricter green semantics | broker-adjacent readback; do not change order posture |
| `coingecko` | `read_ok` | only when `crypto-coingecko-spot` health reports `status: ok` |

CoinGecko is research-grade and non-order-capable. The frontend must not infer
broker authority, order authority, capital authority, RuntimeBinding authority,
or live trading permission from the `coingecko` chip.

---

## 7. Contract Test Handoff

Parent artifact target: `services/control-plane/bff/test_srclive_overlay_contract.py`.

Recommended cases:

| Case | Fixture | Required assertion |
|---|---|---|
| TW positive | source rows for `twse`, `tpex`, `mops`, and `finmind`; truth map has matching connector health with `status: ok` | provider statuses flip to `read_ok`, `source_health_source == "source_ingest"`, and live connector ids are populated |
| TW missing health | same source rows, empty or unrelated truth map | static statuses remain non-green; no provider becomes `read_ok` except existing static broker row |
| US positive/free + credential-gated | seven US source rows; truth map has `ok` health for `stooq`, `sec_edgar`, `finra`, `fred`; paid rows remain credential-unavailable | free providers are `read_ok`; `polygon` and `alphavantage` remain `credential_unavailable` with secret/reason metadata |
| US missing health | seven US source rows, no matching health | `stooq`, `sec_edgar`, `finra`, and `fred` do not become `read_ok`; paid providers stay credential-unavailable |
| Crypto positive | `coingecko` source row with `crypto-coingecko-spot status: ok` | `coingecko == "read_ok"`, `sourceHealthAvailable == true`, connector id is projected |
| Crypto missing health | `coingecko` row, no matching truth | `coingecko` remains `read_unavailable`; no cached live connector id appears |

Important assertion: `read_ok` is allowed only when the provider row resolves to
a connector id and source-ingest truth for that connector has
`health.status == "ok"`. Registry presence alone and frontend cached state are
not enough.

---

## 8. E2E Verifier Handoff

Parent artifact target: `scripts/verify_srclive_readback.py`.

Recommended verifier behavior:

1. Read `BFF_BASE` / `PANTHEON_BFF_BASE_URL` and `BFF_TOKEN` /
   `PANTHEON_BFF_TOKEN`.
2. Call `GET /bff/management/persona-fleet` with the Bearer token.
3. Locate persona items by `persona_id`, `personaId`, or `id`.
4. Read provider statuses from `dataSourceStatus.provider_statuses` or
   `data_source_status.provider_statuses`.
5. Cross-check `dataSources` / `data_sources` rows so a provider status without
   a chip row cannot pass.
6. For green providers, require overlay evidence fields such as
   `sourceHealthSource == "source_ingest"`, `sourceHealthAvailable == true`,
   `connectorId`, `lastSuccessAt` or `latestWatermark`, and
   `rowCountLastRun > 0` when the provider is source-ingest backed.
7. For credential-gated providers, require `credential_unavailable` plus a
   reason and a secret reference or env reference.
8. Emit a JSON summary with persona, provider, expected status, actual status,
   connector id, last success, row count, and failure reason.
9. Exit nonzero on missing rows, missing live health, unexpected green,
   unexpected credential state, or BFF auth/degraded transport failure.

Suggested final status expectations:

| Persona | Required providers |
|---|---|
| `persona-tw-equity` | `shioaji`, `twse`, `tpex`, `mops`, `finmind`; source-ingest-backed providers should be literal `read_ok`, and parent must explain any non-literal count semantics |
| `persona-us-equity` | `ibkr`, `stooq`, `sec_edgar`, `finra`, `fred`, `polygon`, `alphavantage` |
| `persona-crypto` | `kraken`, `coingecko` |

The script should not call source-ingest directly for pass/fail. Source-ingest
queries are useful diagnostics, but SRCLIVE-004 acceptance is about the BFF
projection consumed by frontend/operator surfaces.

---

## 9. Frontend Handoff Rules

| Rule | Required behavior |
|---|---|
| Transport | browser calls BFF only; no direct `/api/source-ingest/*`, TWSE, TPEx, MOPS, SEC, FRED, FINRA, Stooq, Polygon, Alpha Vantage, Kraken, or CoinGecko fetches |
| Green state | source-ingest-backed providers render green only for top-level BFF `read_ok` with live source-health fields present |
| Credential state | `credential_unavailable` is a non-green action-required state with secret/ref guidance, not a failure to hide |
| Missing health | `read_unavailable`, `public_reference_unavailable`, `source_health_*`, `connector_*`, and `connector_configured_no_health` remain non-green |
| Counting | denominator comes from BFF chip rows, not hardcoded local lists; missing chip rows are visible as gaps |
| Freshness | display BFF-projected `lastSuccessAt`, `latestWatermark`, `rowCountLastRun`, and `failureReason` when present |
| Cache | do not keep a provider green after a fresh BFF response removes source-ingest health |
| Authority | data-source chips are read-only truth indicators and do not imply order, broker write, capital, approval, or runtime authority |

---

## 10. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet and task-scoped brief are changed |
| Canonical truth untouched | PASS if no L1 docs, BFF code, source-ingest code, registry/governance code, or frontend code changed by this sidecar |
| Dependency snapshot captured | PASS if SRCLIVE-001 blocked, SRCLIVE-002 in progress, and SRCLIVE-003 done are reflected |
| BFF query gap identified | PASS if US mapping/row gap and TW FinMind counting risk are named |
| Contract-test handoff useful | PASS if parent can derive positive and missing-health cases for TW/US/Crypto |
| E2E verifier handoff useful | PASS if parent can derive `scripts/verify_srclive_readback.py` shape and failure semantics |
| Frontend boundary preserved | PASS if browser calls BFF only and chips remain read-only truth |

---

## 11. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-004-SIDECAR-BFF-HANDOFF`; only task brief was dirty before this packet |
| `git merge --ff-only origin/dev` | Fast-forwarded branch to latest `origin/dev` before writing this packet |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001` | Dependency is `blocked`; PR merged but live deploy/source-ingest acceptance is blocked |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | Dependency is `in_progress`; US rows/mapping are still not a safe SRCLIVE-004 baseline |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Dependency is archived `done`; CoinGecko mapping and smoke evidence are complete |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004` | Parent is `todo`; artifacts target BFF overlay tests and e2e verifier script |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`; artifact path matches this packet |

No runtime tests were run for this sidecar because it changes only support
artifacts. The parent task owns BFF contract tests and live e2e verification.

---

## 12. Handoff Status

At packet creation time, this packet is ready for `Codex` review as support
material. It should not be treated as approval of new canonical
implementation, runtime wiring, BFF code changes, source-ingest changes,
registry/governance changes, or frontend code changes from this sidecar.

Parent owner `Codex` should decide whether to absorb these notes into
SRCLIVE-004, wait for SRCLIVE-001/SRCLIVE-002 to unblock, or request a narrow
correction to this packet.
