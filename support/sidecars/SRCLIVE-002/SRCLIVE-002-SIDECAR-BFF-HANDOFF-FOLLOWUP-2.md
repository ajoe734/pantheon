# SRCLIVE-002 BFF and Frontend Handoff Follow-up 2

**Parent Task**: `SRCLIVE-002` - US research sources live wiring
**Parent Owner**: `Claude2`
**Parent Reviewer**: `Codex`
**Parent Status at packet time**: `review`
**Parent PR**: `#2514` (`task/SRCLIVE-002` -> `dev`)
**Parent Merge Commit**: `fc74b25cacc504ea4b35de6b9561a5072c2c30ea`
**Sidecar Task**: `SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, BFF
runtime code, source-ingest code, registry/governance behavior, frontend code,
or parent task acceptance. Parent ownership decides whether to use these notes
for SRCLIVE-002 closeout or downstream SRCLIVE readback work.

---

## 1. Scope

The first SRCLIVE-002 sidecar packet captured the pre-merge gap: US BFF
provider mapping and `persona-us-equity` data-source rows were missing. That is
no longer the current `dev` baseline.

This follow-up packet captures the post-merge BFF/frontend handoff facts after
PR `#2514` merged into `dev`:

1. Which SRCLIVE-002 BFF/read-store pieces are now present.
2. Which frontend fields should be treated as the operator-facing readback
   contract.
3. Which live proof remains outside this sidecar and must not be inferred from
   code presence alone.
4. Which reviewer checks Claude can use for this support packet.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py`;
- no edits to source-ingest connectors, active-universe rules, runbooks, tests,
  frontend code, or canonical docs;
- no approval or rejection of parent task `SRCLIVE-002`;
- no claim that target runtime source-ingest has already produced fresh live
  health for every no-key connector.

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | Parent task status, reviewer notes, and current next action |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002-SIDECAR-BFF-HANDOFF` | Prior sidecar packet status and approved support-only boundary |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Current sidecar owner/reviewer/artifact boundary |
| `gh pr view 2514 --json ...` | Confirms PR `#2514` merged into `dev` at `fc74b25cacc504ea4b35de6b9561a5072c2c30ea` |
| `services/control-plane/bff/main.py` | BFF source-health overlay and US provider candidates |
| `services/control-plane/bff/read_store.py` | `persona-us-equity` static data-source rows and `secret_ref` fields |
| `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py` | Contract probes for credential preservation and `read_ok` upgrade path |
| `services/source_ingestion/active_universe.py` | No-key US `SourceUpdateRule` inventory |
| `services/source_ingestion/connectors/us_public.py` | Stooq disabled-by-default posture and public US connector IDs |
| `services/source_ingestion/connectors/us_paid_broker.py` | Polygon/Alpha Vantage credential posture and secret refs |
| `docs/05/srclive/us-activation-runbook.md` | Parent-owned operator activation steps and warnings |
| `support/sidecars/SRCLIVE-002/SRCLIVE-002-SIDECAR-BFF-HANDOFF.md` | Pre-merge gap packet this follow-up supersedes for current-state facts |

---

## 3. Current State Snapshot

### 3.1 Parent implementation is merged, but parent task is not closed

`SRCLIVE-002` active status reports:

| Field | Current value |
|---|---|
| Status | `review` |
| Owner | `Claude2` |
| Reviewer | `Codex` |
| Merged PR | `#2514` |
| Merge commit | `fc74b25cacc504ea4b35de6b9561a5072c2c30ea` |
| Review note summary | Implementation review passed; formal status approval/owner closeout still needs to happen |

Do not treat this sidecar as the parent approval. It only packages the BFF and
frontend handoff implications of the merged implementation.

### 3.2 BFF provider map now includes the six US research sources

`services/control-plane/bff/main.py` now maps:

| Provider key | Connector id |
|---|---|
| `stooq` | `us-stooq-daily-ohlcv` |
| `sec_edgar` | `us-sec-edgar-filings` |
| `finra` | `us-finra-short-sale` |
| `fred` | `us-fred-macro` |
| `polygon` | `us-polygon-daily-ohlcv` |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` |

The BFF source-health overlay remains the only green path. A mapped provider
can become `read_ok` only when source-ingest truth for the selected connector
has live health with `status == "ok"`.

### 3.3 `persona-us-equity` now has seven data-source rows

`services/control-plane/bff/read_store.py` now declares this expected chip set:

| Order | Provider key | Static/default status | Source class | Order capable |
|---|---|---|---|---|
| 1 | `ibkr` | `read_ok` | `broker_execution` | `true` with marketdata smoke order path disabled |
| 2 | `stooq` | `read_unavailable` | `research_grade` | `false` |
| 3 | `sec_edgar` | `read_unavailable` | `official_reference` | `false` |
| 4 | `finra` | `read_unavailable` | `official_reference` | `false` |
| 5 | `fred` | `read_unavailable` | `official_reference` | `false` |
| 6 | `polygon` | `credential_unavailable` | `research_grade` | `false` |
| 7 | `alphavantage` | `credential_unavailable` | `research_grade` | `false` |

The two key-gated rows expose:

| Provider key | `secret_ref` | Reason text requirement |
|---|---|---|
| `polygon` | `env://POLYGON_API_KEY` | Must mention `POLYGON_API_KEY`; row reason also names `MASSIVE_API_KEY` and `US_MARKET_DATA_API_KEY` alternatives |
| `alphavantage` | `env://ALPHA_VANTAGE_API_KEY` | Must mention `ALPHA_VANTAGE_API_KEY` |

### 3.4 Source-update rules were already present

`services/source_ingestion/active_universe.py` already contains these no-key US
rules:

| Connector id | Dataset | Cadence | Market |
|---|---|---|---|
| `us-sec-edgar-filings` | `sec_filing_event` | `event_poll_daily` | `US` |
| `us-fred-macro` | `macro_fred_observation` | `daily_weekly_monthly_by_series_frequency` | `GLOBAL` |
| `us-finra-short-sale` | `us_short_volume_daily` | `daily_after_finra_publication_window` | `US` |
| `us-stooq-daily-ohlcv` | `us_price_daily` | `daily_after_close` | `US` |

This supports scheduling, but scheduling presence is not live-read proof.

---

## 4. Frontend Readback Contract

The frontend should use BFF projections only. Browser code should not call
`/api/source-ingest/*`, Stooq, SEC, FRED, FINRA, Polygon, or Alpha Vantage
directly.

Primary route:

```bash
curl -sS -H 'Authorization: Bearer op-ds-001:operator,reviewer' \
  "$BFF_BASE_URL/bff/management/persona-fleet"
```

Diagnostic route:

```bash
curl -sS -H 'Authorization: Bearer op-ds-001:operator,reviewer' \
  "$BFF_BASE_URL/bff/management/data-sources"
```

Frontend/operator panels should locate `persona-us-equity` and reconcile both
the status map and rows:

| DTO area | Required behavior |
|---|---|
| `dataSourceStatus.provider_statuses` or `data_source_status.provider_statuses` | Summary status map, keyed by BFF `provider_key` |
| `dataSources` or `data_sources` | Visible chip rows; missing rows are a failure, not a hidden state |
| `liveSourceConnectorIds` / `live_source_connector_ids` | Evidence that at least one source-ingest-backed connector has live health |
| `staticSourceLabels` / `static_source_labels` | Providers that are mapped but not backed by live health yet |
| row `connectorId` / `connector_id` | Connector selected by overlay; should match the mapping above |
| row `sourceHealthAvailable` / `source_health_available` | Must be true before source-ingest-backed `read_ok` is trusted |
| row `healthStatus` / `health_status` | Must be `ok` for source-ingest-backed green |
| row `lastSuccessAt`, `latestWatermark`, `rowCountLastRun` | Freshness/readback proof to show in operator view when present |
| row `failureReason` / `reason` | Human-readable non-green explanation |
| row `secret_ref` | Required action reference for key-gated providers |

Counting rule:

- the denominator comes from BFF chip rows, not frontend hardcoded lists;
- green is only `status == "read_ok"`;
- `credential_unavailable`, `read_unavailable`, `source_health_*`,
  `connector_*`, and `connector_configured_no_health` are non-green;
- chips are read-only telemetry and never imply broker order authority.

---

## 5. Post-merge Acceptance Matrix

The expected SRCLIVE-002 frontend acceptance state is conditional on live
source-ingest health:

| Provider key | Expected state after runtime proof | Current static fallback without live proof |
|---|---|---|
| `ibkr` | `read_ok` | `read_ok` |
| `stooq` | `read_ok` only after endpoint smoke plus health `ok` | `read_unavailable` |
| `sec_edgar` | `read_ok` only after compliant user-agent run plus health `ok` | `read_unavailable` |
| `finra` | `read_ok` only after successful FINRA run plus health `ok` | `read_unavailable` |
| `fred` | `read_ok` only after successful FRED run plus health `ok` | `read_unavailable` |
| `polygon` | `credential_unavailable` when no accepted key is configured; `read_ok` only after key-backed health `ok` | `credential_unavailable` |
| `alphavantage` | `credential_unavailable` when no accepted key is configured; `read_ok` only after key-backed health `ok` | `credential_unavailable` |

Important live-proof boundary:

- PR `#2514` proves BFF/read-store wiring and contract behavior.
- It does not by itself prove that the target dev runtime has produced fresh
  source-ingest health for Stooq, SEC EDGAR, FINRA, and FRED.
- Stooq remains disabled by default in `StooqDailyOhlcvAdapter` until runtime
  endpoint smoke verifies the target environment.
- The parent closeout proof should include a BFF response excerpt for
  `persona-us-equity`, not only source-ingest service logs.

---

## 6. Credential Honesty Rules

The merged overlay explicitly protects key-gated providers from misleading
status degradation.

| Source-ingest condition | BFF row behavior |
|---|---|
| Registry entry exists but no health | Preserve static default and reason/secret ref |
| `polygon` or `alphavantage` static status is `credential_unavailable` and health is degraded/failed | Preserve `credential_unavailable`, `reason`, and `secret_ref` |
| `polygon` or `alphavantage` health has `status == "ok"` | Upgrade to `read_ok` |
| Any mapped source has health `status != "ok"` and is not credential-preserved | Project `source_health_<status>` as a non-green state |

Frontend implication: key-gated missing-secret states should render as an
action-required credential state, not as a generic source-health failure and
not as green.

---

## 7. Operator Smoke Journey

Parent owner/reviewer can use this sequence for final SRCLIVE-002 closeout or
for downstream SRCLIVE readback tasks:

1. Confirm source-ingest registry lists all six US research connector IDs.
2. Confirm BFF config points at the intended source-ingest base URL.
3. Run or replay bounded source-ingest runs for SEC, FRED, FINRA, and Stooq in
   the target runtime.
4. Confirm SEC uses a compliant `SEC_EDGAR_USER_AGENT`.
5. Confirm Stooq endpoint smoke has explicitly moved the connector out of the
   disabled/unverified posture before claiming `read_ok`.
6. Confirm `/api/source-ingest/health-usage-snapshot` reports `status: ok` for
   the four no-key connector IDs.
7. Query `/bff/management/data-sources` through BFF and confirm registry/health
   source is visible from BFF.
8. Query `/bff/management/persona-fleet` through BFF and archive the
   `persona-us-equity` excerpt showing seven rows.
9. Verify the four no-key rows are `read_ok` only when live health is present.
10. Verify `polygon` and `alphavantage` are `credential_unavailable` with
    `secret_ref` when secrets are absent.

Suggested `persona-us-equity` pass/fail assertions:

| Assertion | Pass condition |
|---|---|
| Seven chips present | BFF row list includes `ibkr`, `stooq`, `sec_edgar`, `finra`, `fred`, `polygon`, `alphavantage` |
| No-key green proof | `stooq`, `sec_edgar`, `finra`, and `fred` have `status == "read_ok"`, selected connector IDs, `sourceHealthAvailable == true`, and health `status == "ok"` |
| Paid missing-secret proof | `polygon` and `alphavantage` have `credential_unavailable`, reason text, and `secret_ref` when keys are absent |
| No false green | no row without live source-ingest health becomes `read_ok`, except existing broker `ibkr` evidence |
| Order boundary | all six research/official-reference providers have `order_capable_provider == false` and `order_path == "not_applicable"` |

---

## 8. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if this sidecar changes only this packet, with no BFF/source-ingest/frontend/canonical edits |
| Post-merge facts are current | PASS if packet treats PR `#2514` as merged and does not repeat obsolete "US mapping missing" language |
| Parent status boundary preserved | PASS if packet says parent remains `review` in active status and does not mark parent approved/done |
| BFF query boundary preserved | PASS if browser/frontend calls BFF only |
| Seven-chip contract captured | PASS if packet names all seven `persona-us-equity` providers and their default/live states |
| Credential honesty captured | PASS if degraded/missing-key paid providers remain `credential_unavailable` with `secret_ref` until health `ok` |
| Live proof boundary captured | PASS if Stooq and no-key providers still require target runtime source-ingest health before `read_ok` |

---

## 9. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; only task-scoped brief was dirty before packet creation |
| `git merge --ff-only origin/dev` | Fast-forwarded to `fc74b25cacc504ea4b35de6b9561a5072c2c30ea`, which includes SRCLIVE-002 PR `#2514` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | Parent is active `review`; PR `#2514` merged; formal status approval/closeout remains outside this sidecar |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002-SIDECAR-BFF-HANDOFF` | Prior BFF handoff sidecar is archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Current sidecar is active `in_progress`; owner `Codex2`; reviewer `Claude`; artifact path matches this packet |
| `gh pr view 2514 --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,headRefOid,mergeStateStatus,url,files` | PR `#2514` is `MERGED`; merge commit is `fc74b25cacc504ea4b35de6b9561a5072c2c30ea`; changed files are BFF main/read_store/test plus US runbook |
| `pytest services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py -q` | `14 passed, 4 warnings` |

No runtime smoke was run for this sidecar. Target-runtime source-ingest and
dev BFF readback proof remain parent/downstream responsibilities.

---

## 10. Handoff Status

This packet is ready for `Claude` review as support-only material. It should
not be treated as approval of SRCLIVE-002 canonical implementation, runtime
wiring, source-ingest live health, frontend code, registry/governance behavior,
or parent closeout.

Recommended review outcome if the checklist passes:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/SRCLIVE-002/SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
REVIEW_NOTES_ZH="審查通過：FOLLOWUP-2 正確反映 SRCLIVE-002 PR #2514 merged 後的 BFF/frontend handoff；仍保持 support-only，未改 canonical/runtime||後續：parent owner/reviewer 仍需以 BFF persona-us-equity live response 完成 parent closeout proof" \
./scripts/ai-status.sh approve SRCLIVE-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
"Sidecar follow-up packet approved; support-only post-merge BFF/frontend handoff returned to Codex2 for closeout."
```

If factual drift appears, request a narrow packet correction instead of
changing canonical, BFF, source-ingest, governance, registry, or frontend files
from this sidecar.
