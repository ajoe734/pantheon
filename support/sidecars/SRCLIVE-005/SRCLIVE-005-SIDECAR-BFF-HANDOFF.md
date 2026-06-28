# SRCLIVE-005 BFF and Frontend Handoff Packet

**Parent Task**: `SRCLIVE-005` - US research source live drivers
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Claude`
**Parent Status at packet time**: `in_progress`
**Parent Anchor**: `4bd6a5b0` (`task/SRCLIVE-005`, local branch, ahead 1 / behind 5 relative to `origin/dev` at packet time)
**Sidecar Task**: `SRCLIVE-005-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, source-ingest code, registry/governance behavior,
or frontend code. Parent ownership and review decide whether to absorb any of
this into the main SRCLIVE-005 delivery.

---

## 1. Scope

SRCLIVE-005 is the US research source live-driver slice after runtime probes
showed the SRCLIVE-002 assumptions were too optimistic:

1. Stooq CSV access is blocked by a runtime anti-bot wall, so Yahoo Finance
   chart API is the replacement public US daily OHLCV source.
2. SEC EDGAR needs a real multi-step driver: company ticker map, CIK
   resolution, then submissions/company facts fetch.
3. FINRA short-volume needs a driver that chooses a recent valid trade date and
   fetches the published file.
4. FRED must use the keyed API path and remain credential-gated until
   `FRED_API_KEY` is installed.
5. Polygon and Alpha Vantage remain credential-unavailable unless paid keys and
   live source health prove otherwise.

This sidecar packages the BFF/frontend handoff facts:

1. BFF query surfaces the frontend and operator verifier should use.
2. Provider-key to connector-id mapping expected by the BFF source-health
   overlay.
3. Current parent-branch WIP gaps that can block acceptance if left unresolved.
4. Frontend chip/status rules that preserve source-ingest truth.
5. Operator smoke journey for parent closeout and reviewer acceptance.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py` persona seed truth;
- no edits to source-ingest connectors, provider adapters, active-universe
  rules, catalog templates, or runbooks;
- no new frontend route or direct source-ingest/Yahoo/SEC/FINRA/FRED browser
  call;
- no approval of parent task `SRCLIVE-005`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005` | active parent task truth, owner/reviewer, acceptance summary, and anchor note |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005-SIDECAR-BFF-HANDOFF` | sidecar scope and support-only boundary |
| `.orchestrator/task-briefs/srclive_005_sidecar_bff_handoff.md` | generated sidecar brief and helper-kind boundary |
| `task/SRCLIVE-005:.orchestrator/task-briefs/srclive_005.md` | generated parent brief with runtime probe findings and completion definition |
| `task/SRCLIVE-005:services/control-plane/bff/main.py` | parent WIP provider-candidate mapping for Yahoo/Stooq/FRED/US overlay |
| `task/SRCLIVE-005:services/control-plane/bff/read_store.py` | parent WIP `persona-us-equity` source rows and FRED credential posture |
| `task/SRCLIVE-005:services/source_ingestion/connectors/us_yahoo.py` | new Yahoo Finance chart adapter and `us-yahoo-daily-ohlcv` metadata |
| `task/SRCLIVE-005:services/source_ingestion/connectors/us_public.py` | SEC, FINRA, and FRED live-driver changes in parent WIP |
| `task/SRCLIVE-005:services/source_ingestion/provider_adapters.py` | provider-owned dispatch for Yahoo, SEC, FINRA, and keyed FRED |
| `task/SRCLIVE-005:services/source_ingestion/active_universe.py` | active-universe route replacing Stooq with Yahoo |
| `task/SRCLIVE-005:services/source_ingestion/financial_source_catalog.py` | catalog/template replacement of Stooq with Yahoo and FRED keying |
| `task/SRCLIVE-005:docs/05/srclive/us-activation-runbook.md` | parent WIP operator activation flow and endpoint correction |
| `support/sidecars/SRCLIVE-002/SRCLIVE-002-SIDECAR-BFF-HANDOFF.md` | prior US handoff assumptions superseded by SRCLIVE-005 driver findings |
| `support/sidecars/SRCLIVE-004/SRCLIVE-004-SIDECAR-BFF-HANDOFF.md` | three-persona live readback verifier expectations inherited by later SRCLIVE work |

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 3. Parent Snapshot

At packet time, the parent `task/SRCLIVE-005` branch has one local anchor commit:
`4bd6a5b0` (`SRCLIVE-005: anchor us source live drivers`). It is work in
progress, not merged canonical truth.

The parent branch is ahead of its `origin/dev` base by one commit and behind the
latest `origin/dev` by five commits. Parent closeout should rebase/merge the
current `dev` baseline before opening or refreshing its PR, then re-run focused
validation.

Parent WIP file scope:

| Layer | Parent WIP change |
|---|---|
| BFF provider map | adds `yahoo -> us-yahoo-daily-ohlcv`; keeps `stooq` as a legacy alias whose first candidate is now `us-yahoo-daily-ohlcv` before `us-stooq-daily-ohlcv` |
| BFF US persona row | replaces the visible `stooq` chip with `yahoo`; keeps `ibkr`, `sec_edgar`, `finra`, `fred`, `polygon`, and `alphavantage` rows |
| BFF credential posture | changes `fred` default from `read_unavailable` to `credential_unavailable` with `secret_ref=env://FRED_API_KEY` |
| Source-ingest connector | adds `YahooUsEquityDailyAdapter` with connector id `us-yahoo-daily-ohlcv`, dataset `us_price_daily`, keyless chart API, and `replaces_connector_id=us-stooq-daily-ohlcv` |
| Provider dispatch | adds live-driver behavior for Yahoo, payload-absent SEC/FINRA, and keyed FRED |
| Active universe/catalog | replaces Stooq daily OHLCV with Yahoo daily OHLCV and updates FRED auth metadata |
| Tests | adds focused unit/contract coverage in existing BFF and source-ingest tests |
| Runbook | corrects source-ingest activation endpoints to `/api/source-ingest/connectors` and `/api/source-ingest/jobs`; removes the old nonexistent `/run` route |

This packet does not claim that parent live curl acceptance has passed. The
parent status says broader validation is still next.

---

## 4. BFF Surface Boundary

The frontend and operator verifier should stay behind BFF.

| Surface | Route | Parent/frontend use |
|---|---|---|
| Persona chips / fleet panel | `GET /bff/management/persona-fleet` | primary frontend and verifier readback for `persona-us-equity` |
| Persona fleet alias | `GET /bff/management/fleet` | operator smoke alias already present in BFF; prefer `/persona-fleet` for SRCLIVE docs unless the parent standardizes the alias |
| Source registry/operator list | `GET /bff/management/data-sources` | diagnostic BFF envelope over source-ingest registry; useful for connector/template visibility |
| Source-ingest health snapshot | source-ingest service route, not browser route | backend/operator diagnostic only; frontend should not call it directly |

The BFF overlay must remain the only green path for source-backed providers:
`health.status == "ok"` from source-ingest can promote a provider to
`read_ok`. Registry presence, configured fetch metadata, local frontend cache,
or parent branch intent is not sufficient.

---

## 5. Provider Mapping Handoff

Expected parent mapping after SRCLIVE-005 lands:

| Provider key | Candidate connector ids | Frontend meaning |
|---|---|---|
| `ibkr` | broker readback/static evidence path | existing broker market-data readback; order path remains disabled for market-data smoke |
| `yahoo` | `us-yahoo-daily-ohlcv` | public US daily OHLCV replacement for blocked Stooq |
| `stooq` | `us-yahoo-daily-ohlcv`, then `us-stooq-daily-ohlcv` | legacy lookup alias only; should not be the visible target chip if parent keeps the new `yahoo` row |
| `sec_edgar` | `us-sec-edgar-filings` | official SEC filing events/company facts; requires compliant user agent |
| `finra` | `us-finra-short-sale` | FINRA public daily short-volume files; freshness depends on publication window |
| `fred` | `us-fred-macro` | keyed FRED macro API; remains credential-unavailable until `FRED_API_KEY` and health are ok |
| `polygon` | `us-polygon-daily-ohlcv` | paid key-gated OHLCV; remains credential-unavailable without accepted key |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` | paid/low-quota key-gated fallback; remains credential-unavailable without accepted key |

Expected visible `persona-us-equity` chip order after parent implementation:

1. `ibkr`
2. `yahoo`
3. `sec_edgar`
4. `finra`
5. `fred`
6. `polygon`
7. `alphavantage`

Frontend code should treat `provider_key` as an opaque BFF key. Do not rewrite
`sec_edgar`, `alphavantage`, or `yahoo` into display slugs before lookup. Do
not keep rendering a `stooq` chip unless the parent explicitly preserves a
legacy migration row.

---

## 6. BFF Query and Projection Gap Matrix

| Gap or boundary | Why it matters | Parent/reviewer implication |
|---|---|---|
| Parent branch is an anchor, not merged | The BFF/frontend handoff is based on WIP branch `4bd6a5b0` | Parent must rebase/merge latest `dev`, push PR, and re-run validation before claiming delivered behavior |
| Visible provider key changes from `stooq` to `yahoo` | Existing frontend copy or tests may still expect Stooq from SRCLIVE-002 | Parent/frontend should update US chip expectations and callouts to Yahoo, while treating `stooq` as legacy alias only |
| `fred` becomes credential-gated | SRCLIVE-002 treated FRED as keyless fallback; runtime showed keyed API is the reliable path | UI must show `credential_unavailable` with `env://FRED_API_KEY` until the key and health are present |
| Yahoo is keyless but not canonical broker truth | Yahoo chart API is research data only and replaced a blocked fallback | UI must not imply order authority, broker authority, RuntimeBinding authority, or capital mutation |
| SEC requires user-agent-driven driver | Empty request payload no longer proves real SEC readback | Parent closeout should prove CIK resolution plus submissions/company facts rows through provider-owned dispatch |
| FINRA requires recent-file selection | A current-day file can be legitimately unpublished | Parent closeout should show the selected trade date and health outcome, including publication-window behavior |
| BFF cache TTL can hide fresh source-ingest health for up to 60 seconds | Operator smoke may run immediately after jobs | Parent smoke should wait one TTL or restart BFF before reading persona-fleet |
| Data-sources route is diagnostic, not the frontend pass/fail source | Registry configured does not mean live health ok | Parent closeout must include BFF `persona-us-equity` response excerpt, not only source-ingest registry/health output |

---

## 7. Frontend Handoff Rules

| Rule | Required behavior |
|---|---|
| Transport | browser calls BFF only; no direct `/api/source-ingest/*`, Yahoo, SEC, FINRA, FRED, Polygon, or Alpha Vantage fetches |
| Green state | source-backed providers render green only for top-level BFF `status == "read_ok"` with source-health overlay fields present |
| Yahoo label | render the US public daily OHLCV chip as Yahoo, not Stooq, if parent keeps the `yahoo` row |
| Legacy Stooq | do not show Stooq as green from the old connector; if shown at all, label it as legacy/replaced and non-primary |
| Credential state | `fred`, `polygon`, and `alphavantage` remain non-green `credential_unavailable` until source-ingest health reports ok |
| Required secret | show BFF `secret_ref`/reason when present, especially `env://FRED_API_KEY`, `env://POLYGON_API_KEY`, and `env://ALPHA_VANTAGE_API_KEY` |
| Degraded state | preserve `read_unavailable`, `credential_unavailable`, `source_health_*`, `connector_*`, and `connector_configured_no_health` as distinct non-green states |
| Freshness | display BFF-projected `lastSuccessAt`, `latestWatermark`, `rowCountLastRun`, and `failureReason` when present |
| Counting | denominator comes from BFF chip rows; missing `yahoo`, `sec_edgar`, `finra`, or `fred` rows are visible gaps |
| Authority | all US research source chips are read-only truth indicators and do not imply order, broker write, capital, approval, or runtime authority |

Suggested accepted BFF shape after Yahoo, SEC, FINRA, and keyed FRED are live:

```json
{
  "dataSourceStatus": {
    "provider_statuses": {
      "ibkr": "read_ok",
      "yahoo": "read_ok",
      "sec_edgar": "read_ok",
      "finra": "read_ok",
      "fred": "read_ok",
      "polygon": "credential_unavailable",
      "alphavantage": "credential_unavailable"
    },
    "source_health_source": "source_ingest",
    "live_ingestion_enabled": true,
    "live_source_connector_ids": [
      "us-yahoo-daily-ohlcv",
      "us-sec-edgar-filings",
      "us-finra-short-sale",
      "us-fred-macro"
    ]
  }
}
```

Before FRED key installation, the honest expected status is `fred:
credential_unavailable`; do not count it as read-ok until the keyed job produces
source-ingest health `status=ok`.

---

## 8. Operator Journey for Parent Closeout

Recommended parent smoke path:

1. Rebase/merge parent `task/SRCLIVE-005` onto latest `origin/dev`.
2. Run focused parent tests for BFF overlay, US public connectors,
   active-universe, and financial catalog templates.
3. Confirm source-ingest registry/catalog exposes `us-yahoo-daily-ohlcv`,
   `us-sec-edgar-filings`, `us-finra-short-sale`, and `us-fred-macro`.
4. Configure each source-ingest connector through
   `POST /api/source-ingest/connectors`; do not use the old nonexistent
   `/api/source-ingest/run` route.
5. Trigger bounded jobs with `POST /api/source-ingest/jobs`.
6. For Yahoo, prove normalized `us_price_daily` rows for a small symbol set
   such as `SPY`, `AAPL`, and `MSFT`.
7. For SEC, prove the configured user agent, CIK resolution, and at least one
   normalized `sec_filing_event` row.
8. For FINRA, record the selected trade date and whether any missing file was
   still inside the expected publication window.
9. For FRED, first show `credential_unavailable` without `FRED_API_KEY`, then
   after key installation prove keyed API rows and health `status=ok`.
10. Confirm `/api/source-ingest/health-usage-snapshot` reports ok health for the
    source-backed connectors being claimed green.
11. Confirm BFF has its source-ingest base URL configured for the target dev
    environment.
12. Wait one BFF overlay TTL, or restart BFF, before the BFF readback.
13. Query `GET /bff/management/persona-fleet` and capture the
    `persona-us-equity` provider statuses plus `dataSources` overlay fields.
14. Optionally query `GET /bff/management/data-sources` as diagnostics, but do
    not use registry-only output as the acceptance proof.

Suggested BFF smoke target:

```bash
curl -sS -H 'Authorization: Bearer op-pathreon-fleet:operator,reviewer,admin:mfa' \
  "$BFF_BASE_URL/bff/management/persona-fleet"
```

The closeout proof should include the BFF response excerpt for
`persona-us-equity`, not only source-ingest service output, because the frontend
consumes the BFF projection.

---

## 9. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet and task-scoped brief are changed |
| Canonical truth untouched | PASS if no L1 docs, BFF code, source-ingest code, registry/governance code, or frontend code changed by this sidecar |
| Parent WIP state captured | PASS if anchor `4bd6a5b0`, WIP branch state, and validation-not-complete boundary are recorded |
| BFF query gap identified | PASS if `/persona-fleet` is the primary frontend proof and `/data-sources` is diagnostic only |
| Yahoo replacement captured | PASS if `yahoo -> us-yahoo-daily-ohlcv` and legacy `stooq` alias behavior are named |
| FRED credential boundary preserved | PASS if FRED remains `credential_unavailable` without `env://FRED_API_KEY` |
| Frontend boundary preserved | PASS if browser calls BFF only and chips remain read-only truth |
| No false green | PASS if read-ok requires source-ingest health `status=ok`, not registry presence or WIP branch intent |

---

## 10. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-005-SIDECAR-BFF-HANDOFF`; only generated sidecar task brief was dirty before this packet |
| `git merge --ff-only origin/dev` | Fast-forwarded sidecar branch to `54128abe` before writing this packet, then to `6d9b6e41` before commit when `origin/dev` advanced |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005` | Parent is active `in_progress`; owner `Codex2`; reviewer `Claude`; anchor commit `4bd6a5b0`; broader validation still next |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-005-SIDECAR-BFF-HANDOFF` | Sidecar is active `in_progress`; owner `Codex`; reviewer `Codex2`; artifact target is this packet |
| `git branch -vv --list '*SRCLIVE-005*'` | Parent local branch is ahead 1 / behind 5 relative to `origin/dev`; sidecar branch is separate |
| `git diff --name-status origin/dev...task/SRCLIVE-005` | Parent WIP touches BFF provider/read-store, US source connectors, provider dispatch, active-universe, catalog, tests, and runbook |
| `rg '@app.get("/bff/management/(persona-fleet|fleet)")' services/control-plane/bff/main.py` | BFF exposes both `/bff/management/persona-fleet` and `/bff/management/fleet` aliases |
| `git diff --check -- .orchestrator/task-briefs/srclive_005_sidecar_bff_handoff.md support/sidecars/SRCLIVE-005/SRCLIVE-005-SIDECAR-BFF-HANDOFF.md` | No whitespace errors in this sidecar diff |

No runtime tests were run for this sidecar because it changes only support
artifacts. The parent task owns source-ingest/BFF implementation tests and live
operator smoke verification.

---

## 11. Handoff Status

At packet creation time, this packet is ready for `Codex2` review as support
material. It should not be treated as approval of new canonical
implementation, runtime wiring, BFF code changes, source-ingest changes,
registry/governance changes, or frontend code changes from this sidecar.

Parent owner `Codex2` should decide whether to absorb these notes into
SRCLIVE-005, refresh the parent branch against latest `dev`, or request a
narrow correction to this packet.
