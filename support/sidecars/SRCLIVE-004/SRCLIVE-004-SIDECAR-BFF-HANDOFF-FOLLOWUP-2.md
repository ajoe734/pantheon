# SRCLIVE-004 BFF and Frontend Handoff Follow-up 2

**Parent Task**: `SRCLIVE-004` - overlay regression tests and three-persona
live readback acceptance
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude2`
**Parent Status at packet time**: `todo`
**Sidecar Task**: `SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, BFF
runtime code, source-ingest code, registry/governance behavior, frontend code,
or parent task acceptance. Parent ownership and review decide whether to absorb
these notes into SRCLIVE-004.

---

## 1. Scope

The first SRCLIVE-004 BFF handoff packet captured the pre-closeout state where
SRCLIVE-002 was still not a safe baseline. That is no longer current.

This follow-up captures the latest BFF/frontend handoff facts after the task
branch was fast-forwarded to current `origin/dev`:

1. SRCLIVE-002 and SRCLIVE-003 are archived `done`; their BFF mappings and
   data-source rows are now part of the `dev` baseline.
2. SRCLIVE-001 is still blocked on Human/Ops for TW source-ingest activation.
3. SRCLIVE-004 can start contract-test work against the merged BFF/read-store
   surface, but live e2e acceptance must still fail or block until TW live
   source-ingest truth is present.
4. Frontend/operator handoff should now treat US seven-chip presence as a
   baseline expectation, not as an open mapping gap.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py`;
- no edits to BFF tests, verifier scripts, source-ingest, runbooks, frontend
  code, registry/governance implementation, or canonical docs;
- no approval or rejection of parent task `SRCLIVE-004`;
- no claim that dev BFF currently has fresh source-ingest health for every
  source-backed provider.

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001` | TW dependency remains `blocked` on Human/Ops activation |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | US dependency is archived `done`; PR `#2514` merged into `dev` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Crypto dependency is archived `done`; CoinGecko mapping and smoke evidence complete |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004` | Parent status, dependencies, and artifact targets |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF` | Prior support packet is archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Current sidecar owner/reviewer/artifact boundary |
| `services/control-plane/bff/main.py` | Current source-health overlay and provider-to-connector map |
| `services/control-plane/bff/read_store.py` | Static persona chip rows and honest fallback statuses |
| `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py` | Existing overlay regression probes parent can reuse or split into SRCLIVE-004 tests |
| `docs/05/srclive/tw-activation-runbook.md` | TW operator activation path and current source-ingest blocker |
| `docs/05/srclive/us-activation-runbook.md` | US post-merge activation path and Stooq/key-gated notes |
| `support/sidecars/SRCLIVE-002/SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Post-merge US handoff facts now inherited by SRCLIVE-004 |
| `support/sidecars/SRCLIVE-003/SRCLIVE-003-SIDECAR-BFF-HANDOFF.md` | Crypto handoff facts inherited by SRCLIVE-004 |

---

## 3. Current Dependency Snapshot

| Task | Current state | SRCLIVE-004 implication |
|---|---|---|
| `SRCLIVE-001` | active `blocked`, waiting for `Human/Ops`; next action is nonprod deploy repair plus VM-local source-ingest activation from the runbook | Parent live e2e must still block/fail on TW until BFF sees source-ingest health for TW official connectors and FinMind/counting is resolved |
| `SRCLIVE-002` | archived `done`; PR `#2514` merged at `fc74b25cacc504ea4b35de6b9561a5072c2c30ea` | US BFF provider map and seven chip rows are now current `dev` baseline; parent can test against them |
| `SRCLIVE-003` | archived `done`; PR `#2516` merged at `73f52ec7410821e25cf3cc3810369f19f24188e8` | Crypto CoinGecko mapping is baseline; parent should still require fresh BFF readback for final e2e proof |
| `SRCLIVE-004` | active `todo`; depends on `001`, `002`, `003` | Parent can start local contract tests but should not claim live e2e completion while SRCLIVE-001 is blocked |
| `SRCLIVE-004-SIDECAR-BFF-HANDOFF` | archived `done`; support packet merged and approved | Prior packet remains useful, but its SRCLIVE-002 "in_progress" snapshot is obsolete |

---

## 4. Current BFF Mapping Baseline

`services/control-plane/bff/main.py` now maps all source-backed SRCLIVE provider
keys needed by the parent verifier:

| Provider key | Candidate connector ids |
|---|---|
| `finmind` | `tw-finmind-datasets`, `tw-finmind-broker-daily-report`, `tw-finmind-broker-bulk-parquet` |
| `twse` | `tw-twse-tpex-official-market` |
| `tpex` | `tw-twse-tpex-official-market` |
| `mops` | `tw-mops-official-disclosures` |
| `stooq` | `us-stooq-daily-ohlcv` |
| `sec_edgar` | `us-sec-edgar-filings` |
| `finra` | `us-finra-short-sale` |
| `fred` | `us-fred-macro` |
| `polygon` | `us-polygon-daily-ohlcv` |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` |
| `coingecko` | `crypto-coingecko-spot` |

Green rule for every mapped source-backed provider:

1. the persona row must exist in `dataSources` / `data_sources`;
2. the provider key must resolve to a connector id;
3. BFF must project source-ingest health for that connector;
4. `health.status` must be `ok`;
5. source-backed `read_ok` must carry overlay evidence such as
   `sourceHealthAvailable`, `connectorId`, `lastSuccessAt`,
   `latestWatermark`, or `rowCountLastRun` when present.

Registry presence, hardcoded frontend lists, and cached browser state are not
green proof.

---

## 5. Persona Readback Matrix

### 5.1 TW

Current static TW rows:

| Provider key | Static status | Green path |
|---|---|---|
| `shioaji` | `read_ok` | Existing quote readback; order path remains disabled for marketdata smoke |
| `twse` | `read_unavailable` | `tw-twse-tpex-official-market` health `ok` |
| `tpex` | `read_unavailable` | `tw-twse-tpex-official-market` health `ok` |
| `mops` | `public_reference_unavailable` | `tw-mops-official-disclosures` health `ok` |
| `finmind` | `read_unavailable` | FinMind source-ingest health `ok` through mapped FinMind connector candidates |

SRCLIVE-001's current runbook activates TWSE/TPEx and MOPS. SRCLIVE-004 still
needs to be explicit about FinMind before claiming `persona-tw-equity=5/5`.
If TWSE/TPEx/MOPS are green but FinMind is not, the parent should either fail
the `5/5` assertion or record a narrow dependency/counting blocker.

### 5.2 US

Current US rows after SRCLIVE-002:

| Provider key | Static/default status | Expected SRCLIVE-004 state |
|---|---|---|
| `ibkr` | `read_ok` | Existing broker readback; order path disabled for marketdata smoke |
| `stooq` | `read_unavailable` | `read_ok` only after endpoint smoke and health `ok` |
| `sec_edgar` | `read_unavailable` | `read_ok` only after compliant `SEC_EDGAR_USER_AGENT` run and health `ok` |
| `finra` | `read_unavailable` | `read_ok` only after successful FINRA run and health `ok` |
| `fred` | `read_unavailable` | `read_ok` only after successful FRED run and health `ok` |
| `polygon` | `credential_unavailable` with `env://POLYGON_API_KEY` | Non-green action-required state unless a key-backed run reports health `ok` |
| `alphavantage` | `credential_unavailable` with `env://ALPHA_VANTAGE_API_KEY` | Non-green action-required state unless a key-backed run reports health `ok` |

Stooq is a special operator risk: the US runbook says the Stooq adapter remains
disabled by default until endpoint smoke verifies the target runtime. The
SRCLIVE-004 verifier should not convert a disabled Stooq connector into a pass.

### 5.3 Crypto

Current crypto rows:

| Provider key | Static/default status | Expected SRCLIVE-004 state |
|---|---|---|
| `kraken` | `datasource_smoke_ok` | Existing broker-adjacent datasource smoke; do not infer order authority |
| `coingecko` | `read_unavailable` | `read_ok` only when `crypto-coingecko-spot` health is `ok` |

CoinGecko is research-grade and order-incapable. A green CoinGecko chip must
not imply broker, order, capital, RuntimeBinding, or live trading authority.

---

## 6. BFF Query and Operator Journey

Primary parent e2e surface:

```bash
curl -fsS "$BFF_BASE/bff/management/persona-fleet" \
  -H "Authorization: Bearer $BFF_TOKEN"
```

Optional BFF diagnostic surface:

```bash
curl -fsS "$BFF_BASE/bff/management/data-sources" \
  -H "Authorization: Bearer $BFF_TOKEN"
```

Operator flow for SRCLIVE-004:

1. Confirm the request is authenticated. `/health` returning 200 is not enough;
   unauthenticated `/bff/management/persona-fleet` returning 401 is expected.
2. Query `GET /bff/management/persona-fleet` through BFF and archive the full
   three-persona excerpt.
3. Locate personas by `persona_id`, `personaId`, or `id`.
4. Reconcile both status map and row list:
   `dataSourceStatus.provider_statuses` / `data_source_status.provider_statuses`
   and `dataSources` / `data_sources`.
5. Treat missing rows as failure. The frontend should not hide missing chips or
   fill them from a hardcoded local denominator.
6. For every source-ingest-backed green provider, require row overlay fields:
   `sourceHealthAvailable == true`, `connectorId`, `healthStatus == "ok"`, and
   freshness/readback fields when present.
7. For `polygon` and `alphavantage`, accept `credential_unavailable` only when
   reason text and `secret_ref` remain present.
8. Use `/bff/management/data-sources` only as a diagnostic. Do not let the
   browser call `/api/source-ingest/*` or external provider APIs directly.
9. If TW remains blocked, record SRCLIVE-004 as blocked on SRCLIVE-001/Human/Ops
   rather than weakening the e2e assertion.

Important route boundary: `docs/05/srclive/tw-activation-runbook.md` records
that `/bff/source-ingest/health-usage-snapshot` returned 404. Parent/frontend
work should not invent that route as a browser contract.

---

## 7. Frontend Handoff Rules

| Rule | Required behavior |
|---|---|
| Transport | Browser calls BFF only; no direct source-ingest, provider, or external API fetches |
| Denominator | Provider count comes from BFF chip rows per persona |
| Green state | Source-backed green is literal `read_ok` plus live source-health overlay evidence |
| Non-green state | `read_unavailable`, `public_reference_unavailable`, `credential_unavailable`, `datasource_smoke_ok`, `source_health_*`, `connector_*`, and `connector_configured_no_health` are not source-backed `read_ok` |
| Credentials | `credential_unavailable` renders as action-required with `secret_ref`; do not hide it or call it failed health |
| Freshness | Display `lastSuccessAt`, `latestWatermark`, `rowCountLastRun`, `failureReason`, and connector ids when BFF provides them |
| Cache | A fresh BFF response that removes live source health must remove green state from the UI |
| Authority | Data-source chips are read-only truth indicators and never imply order, broker write, capital, approval, or runtime authority |

Frontend delta from the first SRCLIVE-004 packet: `persona-us-equity` should now
have seven rows in the BFF baseline. If a frontend still sees only `ibkr`, that
is a deploy freshness or BFF target problem, not the expected current contract.

---

## 8. Parent Contract-Test Handoff

Parent artifact target remains
`services/control-plane/bff/test_srclive_overlay_contract.py`.

Suggested cases now enabled by the merged baseline:

| Case | Required assertion |
|---|---|
| TW official positive | `twse` and `tpex` both bind to `tw-twse-tpex-official-market`; `mops` binds to `tw-mops-official-disclosures`; all become `read_ok` only with health `ok` |
| TW FinMind counting | `persona-tw-equity=5/5` requires FinMind green or an explicit parent blocker/counting explanation |
| TW missing health | static non-green statuses remain when health is missing; no registry-only or cached green |
| US seven rows | row set includes `ibkr`, `stooq`, `sec_edgar`, `finra`, `fred`, `polygon`, `alphavantage` |
| US no-key positive | `stooq`, `sec_edgar`, `finra`, and `fred` become `read_ok` only when connector health is `ok` |
| US credential preserved | `polygon` and `alphavantage` stay `credential_unavailable` with `secret_ref` when health is missing/degraded due to absent keys |
| Crypto positive | `coingecko` maps to `crypto-coingecko-spot` and becomes `read_ok` only with health `ok` |
| Crypto authority boundary | CoinGecko green does not change Kraken/order/capital/runtime authority |

Existing probes in
`services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py`
already cover several ingredients: TW source-health projection, credential
preservation/upgrade, and CoinGecko mapping. SRCLIVE-004 should still own a
narrow parent test file or verifier because its acceptance is cross-persona and
live-readback oriented.

---

## 9. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if this packet and task-scoped brief are the only changed files |
| Canonical/runtime untouched | PASS if no L1 docs, BFF runtime, source-ingest, registry/governance, or frontend code changed |
| Dependency snapshot current | PASS if packet treats SRCLIVE-002 and SRCLIVE-003 as done, SRCLIVE-001 as blocked, and SRCLIVE-004 as todo |
| Obsolete US gap removed | PASS if packet says US seven rows/mappings are now baseline |
| Remaining TW blocker preserved | PASS if packet does not claim live e2e can pass while SRCLIVE-001 is blocked |
| Operator journey useful | PASS if parent can derive BFF-only curl/readback steps and failure semantics |
| Frontend boundary preserved | PASS if browser remains behind BFF and chips remain read-only truth |

---

## 10. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; only task-scoped brief was dirty before packet creation |
| `git fetch origin dev` | Updated local `origin/dev` from GitHub |
| `git merge --ff-only origin/dev` | Fast-forwarded branch to `356c46ec8aa85f90d40875f5705d80ac87b8e0e9` before writing this packet |
| `git merge --no-edit origin/dev` | Refreshed branch after `origin/dev` advanced to `e65c1c09685ededf5677d8373fadc316cdd0c1b6`; merge was clean |
| `git diff --name-status origin/dev...HEAD` | PR diff remains limited to this task brief and follow-up packet |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001` | Dependency remains active `blocked`, waiting for `Human/Ops` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | Dependency is archived `done`; PR `#2514` merged and US BFF/read-store baseline is present |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Dependency is archived `done`; CoinGecko mapping and smoke evidence complete |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004` | Parent remains active `todo`; artifact targets are BFF contract test and verifier script |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF` | Prior sidecar is archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Current sidecar is active `in_progress`; owner `Codex2`; reviewer `Codex`; artifact path matches this packet |
| `rg` over `services/control-plane/bff/main.py` and `read_store.py` | Confirmed current provider mappings, US seven rows, key-gated `secret_ref`, and source-health overlay preservation behavior |
| `pytest services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py -q` | `14 passed, 4 warnings` |

No runtime smoke was run for this sidecar. Dev BFF live readback remains parent
SRCLIVE-004 responsibility after SRCLIVE-001 is unblocked.

---

## 11. Handoff Status

This packet is ready for `Codex` review as support-only material. It should not
be treated as approval of SRCLIVE-004 canonical implementation, runtime wiring,
BFF/source-ingest/frontend code, registry/governance behavior, or live e2e
acceptance.

Recommended review outcome if the checklist passes:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/SRCLIVE-004/SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
REVIEW_NOTES_ZH="審查通過：FOLLOWUP-2 正確反映 SRCLIVE-004 當前 BFF/frontend handoff；SRCLIVE-002/003 已 done，SRCLIVE-001 仍 blocked，packet 保持 support-only 且未改 canonical/runtime||後續：parent SRCLIVE-004 可先做合約測試，但 live e2e closeout 仍需等待 TW activation proof" \
./scripts/ai-status.sh approve SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
"Sidecar follow-up packet approved; support-only SRCLIVE-004 BFF/frontend handoff returned to Codex2 for closeout."
```

If factual drift appears, request a narrow packet correction instead of
changing canonical, BFF, source-ingest, governance, registry, or frontend files
from this sidecar.
