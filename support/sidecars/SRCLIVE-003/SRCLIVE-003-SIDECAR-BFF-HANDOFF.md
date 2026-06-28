# SRCLIVE-003 BFF and Frontend Handoff Packet

**Parent Task**: `SRCLIVE-003` - Crypto CoinGecko connector and wiring
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Claude2` in active status; parent PR commit still records `Reviewer: Claude`
**Parent Status at packet time**: `review`
**Parent PR**: `#2516` (`task/SRCLIVE-003` -> `dev`)
**Parent Head**: `b2eabde88517a9b007d41841998ae7b28d2d57bb`
**Sidecar Task**: `SRCLIVE-003-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, source-ingest code, registry/governance behavior,
or frontend code. Parent ownership and review decide whether to absorb any of
this into the main SRCLIVE-003 delivery.

---

## 1. Scope

SRCLIVE-003 adds a keyless CoinGecko public API connector and wires it to
`persona-crypto` so the crypto panel can move from `1/2` to `2/2` only after
source-ingest reports live health for `crypto-coingecko-spot`.

This sidecar packages the BFF/frontend handoff facts:

1. BFF query surfaces the operator/frontend should use.
2. Provider-key to connector-id mapping expected by the source-health overlay.
3. Current projection boundary between Kraken and CoinGecko.
4. Frontend chip/status rules that preserve source-ingest truth.
5. Operator smoke journey for parent closeout and reviewer acceptance.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py` persona seed truth;
- no edits to CoinGecko connector, active-universe rules, catalog, or tests;
- no new frontend route or direct source-ingest/CoinGecko browser call;
- no approval of PR `#2516`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | active parent task truth, reassigned reviewer, and acceptance summary |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003-SIDECAR-BFF-HANDOFF` | sidecar task scope and support-only boundary |
| `gh pr view 2516 --json ...` and `gh pr checks 2516` | parent PR state, head commit, files changed, and GitHub check state |
| `origin/task/SRCLIVE-003:.orchestrator/task-briefs/srclive_003.md` | parent brief generated with the CoinGecko acceptance rule |
| `origin/task/SRCLIVE-003:services/control-plane/bff/main.py` | BFF source-health overlay and `coingecko -> crypto-coingecko-spot` provider map |
| `origin/task/SRCLIVE-003:services/control-plane/bff/read_store.py` | current `persona-crypto` seed data-source rows for Kraken and CoinGecko |
| `origin/task/SRCLIVE-003:services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py` | contract test proving the overlay maps CoinGecko health to `read_ok` |
| `origin/task/SRCLIVE-003:services/source_ingestion/connectors/crypto_coingecko.py` | connector metadata, fetch config, normalization, evidence packet, and health projection |
| `origin/task/SRCLIVE-003:services/source_ingestion/provider_adapters.py` | provider-owned adapter dispatch for `CoinGeckoSpotMarketAdapter.records_from_payload` |
| `origin/task/SRCLIVE-003:services/source_ingestion/active_universe.py` | active-universe scheduling rule for `crypto-coingecko-spot` |
| `origin/task/SRCLIVE-003:services/source_ingestion/financial_source_catalog.py` | catalog entry and config template for the CoinGecko data source |
| `support/sidecars/SRCLIVE-002/SRCLIVE-002-SIDECAR-BFF-HANDOFF.md` | previous SRCLIVE BFF/frontend handoff packet pattern |

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 3. Current Truth Snapshot

### 3.1 Parent PR state

At packet time, PR `#2516` is open against `dev` with head commit
`b2eabde88517a9b007d41841998ae7b28d2d57bb`.

GitHub checks reported pass for:

- Commit trailers
- Runtime mirror guard
- Smoke acceptance
- Forward to orchestrator

The parent commit body records:

- focused pytest suite: `24 passed`;
- live source-ingest CoinGecko smoke: `ingest-443dfd49428f` completed;
- `normalized_count=9`;
- health-usage snapshot includes `crypto-coingecko-spot status=ok`;
- datasets `crypto_spot_ohlc` and `crypto_spot_price`.

Reviewer routing note: active task status has reviewer `Claude2` after chair
reassignment. The parent commit trailer still says `Reviewer: Claude`, because
the commit was made before reassignment. GitHub trailer checks are currently
green; the parent reviewer should decide whether that mismatch needs a small
follow-up before approval/closeout.

### 3.2 BFF surfaces

The frontend should stay behind BFF:

| Surface | Route | Current role |
|---|---|---|
| Persona chips / fleet panel | `GET /bff/management/persona-fleet` | projects `data_source_status`, `data_sources`, `required_data_sources`, and source-health overlay fields |
| Source registry/operator list | `GET /bff/management/data-sources` | BFF envelope over source-ingest registry; useful to confirm the CoinGecko connector/template is visible |
| Persona detail DTOs | BFF persona detail surfaces | should preserve the same overlay result if a frontend detail route renders provider chips |

Browser/frontend code should not call `/api/source-ingest/*` or
`https://api.coingecko.com/api/v3/*` directly. The BFF already composes
source-ingest registry and health-usage data.

### 3.3 Provider-candidate map

SRCLIVE-003 PR `#2516` adds this BFF source-health candidate:

| Provider key | Candidate connector id |
|---|---|
| `coingecko` | `crypto-coingecko-spot` |

This is the lookup that lets `_overlay_source_health_truth` bind the static
`persona-crypto` CoinGecko row to live source-ingest health. The rule remains:
`coingecko` can become green only if source-ingest health-usage snapshot
reports `crypto-coingecko-spot` with `status: ok`.

### 3.4 `persona-crypto` seed truth

The current BFF read model already has two crypto provider rows:

| Provider key | Static status | Source class | Order posture | Handoff note |
|---|---|---|---|---|
| `kraken` | `datasource_smoke_ok` | `broker_execution` | `order_capable_provider=true`, `order_path=validate_only` | Existing 1/2 smoke state; SRCLIVE-003 does not change it. |
| `coingecko` | `read_unavailable` | `research_grade` | `order_capable_provider=false`, `order_path=not_applicable` | Becomes `read_ok` only through source-health overlay. |

No frontend should infer order authority from the CoinGecko row. CoinGecko is
research evidence only, not a broker, order, capital, RuntimeBinding, or live
trading path.

### 3.5 Source-ingest CoinGecko inventory in PR `#2516`

| Layer | Added fact |
|---|---|
| Connector | `CoinGeckoSpotMarketAdapter`, connector id `crypto-coingecko-spot`, auth type `none`, API base `/api/v3` |
| Normalized datasets | `crypto_spot_ohlc`, `crypto_spot_price` |
| Symbols | default mapping includes `BTC -> bitcoin`, `ETH -> ethereum`, `SOL -> solana`, plus other common crypto ids |
| Governance metadata | `order_capable_provider=false`, `direct_execution_allowed=false`, broker consumption `not_direct_action` |
| Fetch dispatch | provider-owned adapter token `CoinGeckoSpotMarketAdapter.records_from_payload` |
| Active universe | `daily_crypto_24x7_poll`, market `CRYPTO`, max symbols per run `100`, priority `55` |
| Catalog/template | `ds-coingecko-crypto-spot`, `template-crypto-coingecko-spot`, keyless public API |

---

## 4. BFF Query and Projection Gap Matrix

| Gap or boundary | Why it matters | Parent/reviewer implication |
|---|---|---|
| `coingecko` mapping exists only in PR `#2516` until merge | `origin/dev` cannot project CoinGecko health to the crypto chip without it | Parent approval/merge must include the BFF mapping line |
| `persona-crypto` already has a CoinGecko row | Parent does not need a read-store row addition for this slice | Reviewer should verify the row remains `order_capable_provider=false` |
| Source-ingest health is the only green path | Prevents hardcoded `read_ok` or cached frontend green state | Parent closeout must include BFF response after health snapshot has `status=ok` |
| Registry-only connector is not enough | `_provider_status_from_truth` may report `connector_*` or configured-without-health statuses when no health exists | Frontend must render non-green until `sourceHealthAvailable=true` and top-level status is `read_ok` |
| CoinGecko is keyless but rate-limited | A missing secret prompt would be misleading, but API/rate-limit failures are possible | Render provider/network/rate-limit failure reasons as non-green; do not ask for an API key unless a future contract changes |
| Kraken remains broker readback | It is adjacent crypto evidence, not a CoinGecko replacement | Do not mark parent accepted from Kraken `datasource_smoke_ok` alone |
| Parent reviewer changed after commit | Status reviewer is now `Claude2`, commit trailer says `Claude` | Reviewer should decide if current green checks are sufficient or request a trailer-alignment follow-up |

The key rule remains: BFF must not hardcode `read_ok`. A provider can become
green only when the BFF mapping points to a connector and
`/api/source-ingest/health-usage-snapshot` reports that connector healthy.

---

## 5. Frontend Handoff Rules

Expected chip order for the SRCLIVE-003 acceptance view:

1. `kraken`
2. `coingecko`

Frontend rendering rules:

| Rule | Required behavior |
|---|---|
| Transport | call BFF only; never call source-ingest or CoinGecko directly from browser code |
| Green state | render CoinGecko green only for top-level BFF status `read_ok` |
| Live proof fields | prefer BFF fields `connectorId`, `sourceHealthAvailable`, `lastSuccessAt`, `latestWatermark`, `rowCountLastRun`, `failureReason`, and `sourceHealth` |
| Keyless state | do not show a missing-secret callout for CoinGecko; it is keyless public API |
| Degraded state | preserve `read_unavailable`, `source_health_failed`, `source_health_degraded`, `connector_configured_no_health`, and network/rate-limit reasons as non-green |
| Ordering | keep BFF/provider order if supplied; otherwise use `kraken`, then `coingecko` |
| Write authority | data-source chips are read-only and do not imply broker order authority |

The frontend should consider CoinGecko accepted only when the BFF response for
`persona-crypto` contains a shape equivalent to:

```json
{
  "dataSourceStatus": {
    "provider_statuses": {
      "kraken": "datasource_smoke_ok",
      "coingecko": "read_ok"
    },
    "live_source_connector_ids": ["crypto-coingecko-spot"],
    "source_health_source": "source_ingest",
    "live_ingestion_enabled": true
  },
  "dataSources": [
    {
      "provider_key": "coingecko",
      "status": "read_ok",
      "connectorId": "crypto-coingecko-spot",
      "sourceHealthAvailable": true,
      "rowCountLastRun": 1
    }
  ]
}
```

The exact row count may differ. It should be nonzero for parent acceptance, and
the archived evidence should show current `lastSuccessAt` or `latestWatermark`.

---

## 6. Operator Journey for Parent Closeout

Recommended parent smoke path:

1. Confirm source-ingest registry lists `crypto-coingecko-spot`.
2. Confirm financial source catalog exposes `template-crypto-coingecko-spot`.
3. Confirm active-universe policy includes `crypto-coingecko-spot` with
   `daily_crypto_24x7_poll` and market `CRYPTO`.
4. Trigger or replay a bounded ingest for `bitcoin`, `ethereum`, and/or
   `solana` through the provider-owned adapter.
5. Confirm normalized records include both `crypto_spot_ohlc` and
   `crypto_spot_price`, with public/research access scope and no execution
   authority.
6. Confirm `/api/source-ingest/health-usage-snapshot` reports:
   - `health.source_id == "crypto-coingecko-spot"`
   - `health.status == "ok"`
   - `health.row_count_last_run > 0`
   - `health.metadata.normalized_datasets` includes `crypto_spot_ohlc` and
     `crypto_spot_price`
7. Confirm BFF has its source-ingest base URL configured for the target dev
   environment.
8. Query `GET /bff/management/data-sources` and confirm the BFF registry
   envelope is not missing/unavailable for CoinGecko.
9. Query `GET /bff/management/persona-fleet` and confirm `persona-crypto`
   exposes `kraken` plus `coingecko`, with CoinGecko overlay fields sourced from
   source-ingest health.

Suggested BFF smoke targets:

```bash
curl -sS -H 'Authorization: Bearer op-ds-001:operator,reviewer' \
  "$BFF_BASE_URL/bff/management/data-sources"

curl -sS -H 'Authorization: Bearer op-ds-001:operator,reviewer' \
  "$BFF_BASE_URL/bff/management/persona-fleet"
```

The closeout proof should include the BFF response excerpt for
`persona-crypto`, not only the source-ingest service output, because the
frontend consumes the BFF projection.

---

## 7. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet and task-scoped sidecar context are changed |
| Canonical truth untouched | PASS if no L1 docs, BFF code, source-ingest code, registry/governance code, or frontend code changed by this sidecar |
| BFF provider map identified | PASS if the packet names `coingecko -> crypto-coingecko-spot` |
| Persona chip boundary identified | PASS if Kraken remains existing smoke and CoinGecko is research-only overlay |
| Green path preserved | PASS if CoinGecko can become `read_ok` only through source-ingest health `status=ok` |
| Frontend boundary preserved | PASS if browser calls BFF only and treats chips as read-only state |
| Parent PR state captured | PASS if PR `#2516`, head commit, checks, and reviewer-routing note are recorded |

---

## 8. Verification Performed

| Command | Result |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Active parent task is `review`; owner `Codex2`; reviewer `Claude2`; parent PR noted green and awaiting review. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003-SIDECAR-BFF-HANDOFF` | Sidecar task is active `in_progress`; owner `Codex2`; reviewer `Codex`; artifact path matches this packet. |
| `gh pr view 2516 --json ...` | PR `#2516` open from `task/SRCLIVE-003` to `dev`; head `b2eabde88517a9b007d41841998ae7b28d2d57bb`; changed files match parent scope. |
| `gh pr checks 2516` | Commit trailers, Runtime mirror guard, Smoke acceptance, and Forward to orchestrator all pass. |
| `git diff --check origin/dev...origin/task/SRCLIVE-003` | No whitespace errors in the parent PR diff. |
| `git diff --check -- .orchestrator/task-briefs/srclive_003_sidecar_bff_handoff.md support/sidecars/SRCLIVE-003/SRCLIVE-003-SIDECAR-BFF-HANDOFF.md` | No whitespace errors in this sidecar diff. |

No runtime tests were run for this sidecar because it changes only support
artifacts. Parent PR `#2516` carries the focused pytest and live CoinGecko smoke
evidence for implementation behavior.

---

## 9. Handoff Status

This packet is ready for `Codex` review as support material. It should not be
treated as approval of SRCLIVE-003 canonical implementation, runtime wiring,
BFF code changes, source-ingest changes, registry/governance changes, or
frontend code changes.

Recommended review command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/SRCLIVE-003/SRCLIVE-003-SIDECAR-BFF-HANDOFF-REVIEW.md \
REVIEW_NOTES_ZH="審查通過：sidecar packet accurately captures PR #2516 BFF/frontend handoff facts and remains support-only||後續：parent reviewer should decide whether the Claude/Claude2 reviewer routing note requires parent PR trailer alignment" \
./scripts/ai-status.sh approve SRCLIVE-003-SIDECAR-BFF-HANDOFF \
"Sidecar packet approved; support-only BFF/frontend handoff returned to owner for closeout."
```

If review finds a factual mismatch, reopen with the exact packet correction
needed instead of changing canonical or parent runtime files from this sidecar.
