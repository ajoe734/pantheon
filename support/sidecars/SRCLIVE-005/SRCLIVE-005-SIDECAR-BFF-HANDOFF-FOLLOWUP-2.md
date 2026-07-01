# SRCLIVE-005 BFF and Frontend Handoff Follow-up 2

**Parent Task**: `SRCLIVE-005` - US research source live drivers
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Claude2`
**Parent Status at packet time**: `review_approved`
**Parent Status at closeout**: `done`
**Parent Implementation PR**: `https://github.com/ajoe734/pantheon/pull/2543`
**Parent Implementation Merge Commit**: `bfec5636ef96084d7ada26ab75370cd9e986bec4`
**Parent Closeout PR**: `https://github.com/ajoe734/pantheon/pull/2551`
**Parent Closeout Merge Commit**: `ef68d5ef653cd629fef3ab73d7dc20eea6b2f3cc`
**Sidecar Task**: `SRCLIVE-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude2`
**Sidecar Review Status**: `review_approved`
**Sidecar PR**: `https://github.com/ajoe734/pantheon/pull/2552`
**Sidecar Merge Commit**: `e6c98e1f5167e485f67aab066101458354d7899f`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This follow-up is support material only. It does not edit canonical truth, L1
contracts, BFF runtime code, source-ingest code, registry/governance behavior,
frontend code, or parent `SRCLIVE-005` owner closeout state.

---

## 1. Why This Follow-up Exists

The first SRCLIVE-005 sidecar packet captured the parent branch while it was
still WIP. Since then, parent implementation PR #2543 and parent closeout PR
#2551 merged into `dev`, and owner `Codex2` finalized `SRCLIVE-005` to `done`.

This packet updates the BFF/frontend handoff from "WIP branch expectations" to
"merged implementation surface plus lifecycle boundary." Parent owner still
decides whether and how to absorb this packet into future docs or release notes.

Non-goals:

- no new source-ingest, BFF, registry, governance, or frontend implementation;
- no L1/L2 canonical document edits;
- no approval or mutation of parent task state by this sidecar;
- no claim that a dev-VM live ingest run or browser smoke has completed beyond
  the review evidence already recorded on the parent task.

---

## 2. Parent State at Sidecar Closeout

| Item | Current value |
|---|---|
| Parent status | `done` in `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005` archive output |
| Implementation PR | #2543, `SRCLIVE-005: wire US source live drivers` |
| Implementation PR state | `MERGED` into `dev` at `2026-06-28T15:26:16Z` |
| Implementation task commit | `c7b793a1b755acbd78e2feb18fd9a897f18c19b2` |
| Implementation merge commit | `bfec5636ef96084d7ada26ab75370cd9e986bec4` |
| Closeout PR | #2551, `SRCLIVE-005: record closeout PR` |
| Closeout task commit | `51db21d2425e3e98b12561ca2568274be10e0ceb` |
| Closeout merge commit | `ef68d5ef653cd629fef3ab73d7dc20eea6b2f3cc` |
| Visible GitHub checks | `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, and `Forward to orchestrator` succeeded |
| Review note | reviewer accepted Yahoo/Stooq replacement, SEC multi-step fetch, FINRA publication-window handling, FRED key gate, and BFF no-false-green overlay |
| Remaining lifecycle boundary | none for parent; this sidecar only needs owner closeout from `review_approved` to `done` |

Parent PR #2543 touched these surfaces:

| Layer | Files |
|---|---|
| BFF source projection | `services/control-plane/bff/main.py`, `services/control-plane/bff/read_store.py` |
| BFF tests | `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py` |
| US source adapters | `services/source_ingestion/connectors/us_public.py`, `services/source_ingestion/connectors/us_yahoo.py` |
| Source-ingest dispatch/catalog | `services/source_ingestion/provider_adapters.py`, `services/source_ingestion/active_universe.py`, `services/source_ingestion/financial_source_catalog.py`, connector init |
| Source-ingest tests | `services/source_ingestion/tests/test_us_public_connectors.py`, `test_active_universe.py`, `test_financial_source_catalog.py` |
| Operator doc | `docs/05/srclive/us-activation-runbook.md` |

---

## 3. Merged BFF Surface for Frontend

The frontend should stay behind BFF and should treat BFF status projection as
the only UI truth for US research source chips.

| Frontend surface | Route | Use |
|---|---|---|
| Persona fleet cards/chips | `GET /bff/management/persona-fleet` | primary UI readback route |
| Existing fleet alias | `GET /bff/management/fleet` | equivalent operator alias used by the runbook |
| Source detail/registry diagnostics | `GET /bff/management/data-sources` | diagnostic only; not pass/fail proof |
| Source-ingest health | `/api/source-ingest/health-usage-snapshot` | backend/operator evidence source; browser should not call it directly |

Merged BFF provider candidates:

| provider_key | Connector candidates |
|---|---|
| `yahoo` | `us-yahoo-daily-ohlcv` |
| `stooq` | `us-yahoo-daily-ohlcv`, then `us-stooq-daily-ohlcv` |
| `sec_edgar` | `us-sec-edgar-filings` |
| `finra` | `us-finra-short-sale` |
| `fred` | `us-fred-macro` |
| `polygon` | `us-polygon-daily-ohlcv` |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` |

Merged visible `persona-us-equity` static order:

1. `ibkr`
2. `yahoo`
3. `sec_edgar`
4. `finra`
5. `fred`
6. `polygon`
7. `alphavantage`

`stooq` is no longer the visible US daily OHLCV chip in the static BFF seed.
It remains a legacy BFF lookup alias so old references can resolve through the
new Yahoo connector first.

---

## 4. No-False-Green Rules

The merged BFF overlay promotes source-backed providers through
`_overlay_source_health_truth`. Frontend code should preserve these semantics:

| Case | UI status behavior |
|---|---|
| No source-ingest health for connector | keep static `read_unavailable` or `credential_unavailable`; do not infer green from registry/config |
| `health.status == "ok"` | BFF projects provider status to `read_ok` and includes connector health fields |
| `health.status != "ok"` for normal source | BFF projects `source_health_<status>` unless the source is credential-gated |
| Credential-gated source with missing/degraded key health | keep `credential_unavailable` and `secret_ref` |
| Credential-gated source with `health.status == "ok"` | may upgrade to `read_ok` |

Important fields for frontend detail rows:

- `dataSourceStatus.provider_statuses`
- `dataSourceStatus.source_health_source`
- `dataSourceStatus.live_ingestion_enabled`
- `dataSourceStatus.live_source_connector_ids`
- `dataSources[].connectorId`
- `dataSources[].sourceHealthAvailable`
- `dataSources[].healthStatus`
- `dataSources[].lastSuccessAt`
- `dataSources[].latestWatermark`
- `dataSources[].rowCountLastRun`
- `dataSources[].failureReason`
- `dataSources[].secret_ref`

Do not collapse `read_unavailable`, `credential_unavailable`,
`source_health_failed`, `source_health_degraded`,
`connector_configured_no_health`, and `static_metadata` into one generic
"offline" bucket. Those states drive different operator actions.

---

## 5. Source-Specific Frontend Handoff

| Provider | Expected frontend treatment |
|---|---|
| `ibkr` | Existing broker readback remains `read_ok`; order path remains disabled for market-data smoke and must not imply live order authority |
| `yahoo` | Render as Yahoo Finance daily OHLCV, not Stooq; keyless research source only |
| `sec_edgar` | Render as official filings/company facts; if non-green, show user-agent/connector health reason when BFF provides it |
| `finra` | Render publication-window-aware status; missing current-day file can be pending until the expected window passes |
| `fred` | Show `credential_unavailable` with `env://FRED_API_KEY` until keyed source-ingest health is `ok` |
| `polygon` | Keep credential-gated unless paid key and source-ingest health prove ok |
| `alphavantage` | Keep credential-gated unless key and source-ingest health prove ok |

Recommended chip counting:

- denominator comes from the BFF `dataSources` rows;
- green count uses BFF statuses only;
- `fred` should not be counted green before `FRED_API_KEY` is installed and
  source-ingest health is `ok`;
- a missing `yahoo`, `sec_edgar`, `finra`, or `fred` row is a visible BFF
  projection gap, not a frontend fallback opportunity.

---

## 6. Operator Verification Boundary

The merged runbook now uses the real source-ingest activation endpoints:

1. `GET /api/source-ingest/connectors`
2. `GET /api/source-ingest/registry`
3. `GET /api/source-ingest/health-usage-snapshot`
4. `POST /api/source-ingest/connectors`
5. `POST /api/source-ingest/jobs`

There is no `/api/source-ingest/run` endpoint for this flow.

After a source-ingest job succeeds, wait one BFF overlay TTL (60 seconds) or
restart BFF before reading the persona fleet projection.

Expected honest states:

| Stage | Expected BFF status |
|---|---|
| Before source-ingest health | `ibkr=read_ok`; `yahoo/sec_edgar/finra=read_unavailable`; `fred/polygon/alphavantage=credential_unavailable` |
| After Yahoo/SEC/FINRA ok health, before FRED key | `ibkr/yahoo/sec_edgar/finra=read_ok`; `fred/polygon/alphavantage=credential_unavailable` |
| After FRED key and FRED ok health | `ibkr/yahoo/sec_edgar/finra/fred=read_ok`; `polygon/alphavantage=credential_unavailable` |

The frontend can display the second state as partially live. It should not
require Polygon or Alpha Vantage to turn green for SRCLIVE-005 acceptance.

---

## 7. Closeout and Absorption Notes

Parent owner closeout has recorded the final `SRCLIVE-005` PR/merge and status
transition. This sidecar did not move the parent to `done`.

Parent closeout facts to preserve if this packet is absorbed elsewhere:

- PR #2543 merged into `dev` at merge commit
  `bfec5636ef96084d7ada26ab75370cd9e986bec4`;
- closeout PR #2551 merged into `dev` at merge commit
  `ef68d5ef653cd629fef3ab73d7dc20eea6b2f3cc`;
- final owner delivery commit was
  `51db21d2425e3e98b12561ca2568274be10e0ceb`;
- reviewer gate passed with all visible GitHub checks green;
- source-ingest/BFF tests were accepted by reviewer notes as 13
  `us_public` tests and 11 BFF/active-universe related tests;
- follow-up warning: `docs/05/srclive/us-activation-runbook.md` still lists
  `Reviewer: Claude` near the top while status truth says reviewer `Claude2`.
  Reviewer marked this non-blocking.

If frontend work absorbs this packet, it should update expectations from the
old SRCLIVE-002/Stooq language to the merged SRCLIVE-005/Yahoo language and
preserve the BFF-only browser boundary.

---

## 8. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support-only scope | PASS if only this follow-up packet and task-scoped brief are changed |
| Canonical truth untouched | PASS if no L1/L2 canonical docs or runtime/frontend code changed |
| Parent lifecycle boundary | PASS if packet records parent `done` as owner closeout fact and does not claim this sidecar changed it |
| PR state updated | PASS if packet records PR #2543 and merge commit `bfec5636...` |
| BFF mapping current | PASS if visible US chip is `yahoo`, with `stooq` as legacy alias only |
| No false green | PASS if `read_ok` requires source-ingest `health.status == "ok"` |
| Credential gates preserved | PASS if FRED/Polygon/Alpha Vantage stay non-green until keys and health prove ok |
| Frontend boundary preserved | PASS if browser uses BFF routes only |

---

## 9. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; only generated task brief was dirty before this packet |
| `git diff --check -- support/sidecars/SRCLIVE-005/SRCLIVE-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md .orchestrator/task-briefs/srclive_005_sidecar_bff_handoff_followup_2.md` | PASS; no whitespace errors |
| `git fetch origin --prune` | Refreshed remote state; current branch HEAD is already an ancestor of `origin/dev` before this final closeout commit |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Sidecar is active `review_approved`, owner `Codex`, reviewer `Claude2`, review notes record all 8 checklist items as PASS |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005` | Parent is archived `done`, owner `Codex2`, reviewer `Claude2`, delivery commit `51db21d2`, merge target `ef68d5ef` |
| `gh pr list --repo ajoe734/pantheon --head task/SRCLIVE-005 --state all --json ...` | PR #2543 is `MERGED` into `dev`; merge commit `bfec5636...`; visible checks succeeded |
| `gh pr list --repo ajoe734/pantheon --head task/SRCLIVE-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 --state all --json ...` | Sidecar PR #2552 is `MERGED` into `dev`; merge commit `e6c98e1f...`; visible checks succeeded |
| `git show --stat --oneline bfec5636ef96084d7ada26ab75370cd9e986bec4` | Confirmed merged parent surfaces: BFF source projection, US source adapters, provider dispatch/catalog, tests, and runbook |
| `rg`/`sed` reads of BFF and source-ingest files | Confirmed merged provider candidates, `persona-us-equity` static chip order, source-health overlay behavior, and runbook endpoint flow |

No runtime tests were run for this sidecar because it changes only support
artifacts. Parent `SRCLIVE-005` owns runtime verification.

---

## 10. Handoff Status

This packet was review-approved by `Claude2` as support material and is ready
for owner closeout. It should be used as a post-merge handoff for frontend and
BFF consumers, not as a new canonical contract or parent task approval.
