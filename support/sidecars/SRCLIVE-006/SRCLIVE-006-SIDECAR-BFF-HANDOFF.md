# SRCLIVE-006 BFF and Frontend Handoff Packet

**Parent Task**: `SRCLIVE-006` - fix `data_source_status.state` badge after full live readback
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude2`
**Parent Status at packet time**: `in_progress`
**Sidecar Task**: `SRCLIVE-006-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, source-ingest code, registry/governance behavior,
or frontend code. Parent ownership and review decide whether to absorb any of
this into the main SRCLIVE-006 delivery.

---

## 1. Scope

SRCLIVE-006 fixes a projection mismatch in the Management AI persona fleet
surface:

1. BFF source-health overlay can make every TW provider in
   `data_source_status.provider_statuses` green.
2. The same overlay currently leaves `data_source_status.state` at the static
   read-store seed value, usually `partial_readback`.
3. The frontend badge tone uses `dataSourceStatus.state`, while the `X/Y`
   provider count uses `providerStatuses`.
4. Result: the row can honestly show `5/5` readable while the state badge still
   renders amber as `partial readback`.

This sidecar packages the BFF/frontend handoff facts:

1. Exact BFF projection location and current gap.
2. Frontend badge/count split that makes the bug visible.
3. Parent implementation guardrails for full-green state promotion.
4. Contract-test and live-smoke expectations.
5. Reviewer checklist for support-only acceptance.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py` seed truth;
- no edits to source-ingest connectors, health snapshots, registry, or
  governance code;
- no edits to execute-plans frontend code;
- no change to provider-specific green rules, credential gates, or order/capital
  authority;
- no approval of parent task `SRCLIVE-006`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-006` | active parent task truth, bug summary, owner/reviewer, and completion definition |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-006-SIDECAR-BFF-HANDOFF` | sidecar scope and support-only boundary |
| `.orchestrator/task-briefs/srclive_006_sidecar_bff_handoff.md` | generated sidecar brief and helper-kind boundary |
| `services/control-plane/bff/main.py` | `_overlay_source_health_truth` currently updates provider rows/statuses but returns without recomputing `data_source_status.state` |
| `services/control-plane/bff/read_store.py` | static TW/US/crypto market-persona seed states and summaries |
| `services/control-plane/bff/test_srclive_overlay_contract.py` | focused SRCLIVE overlay tests that should receive the new state-promotion regression coverage |
| `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py` | broader Management persona-fleet contract coverage and current overlay behavior |
| `scripts/verify_srclive_readback.py` | live BFF verifier should assert both provider statuses and `dataSourceStatus.state` for TW |
| `/home/lupin/code/execute-plans/src/management/pages/oversight/_core.tsx` | frontend badge tone and readable-count behavior, inspected read-only from the active frontend checkout |
| `support/sidecars/SRCLIVE-004/SRCLIVE-004-SIDECAR-BFF-HANDOFF.md` | inherited three-persona live readback and BFF-only frontend boundary |
| `support/sidecars/SRCLIVE-005/SRCLIVE-005-SIDECAR-BFF-HANDOFF.md` | latest US source-driver handoff and non-green credential boundary |

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 3. Current Bug Snapshot

### 3.1 BFF projection path

The affected path is the Management persona-fleet projection:

1. `_build_persona_health_items(...)` reads `metadata["data_source_status"]`,
   `metadata["data_sources"]`, and `persona["required_data_sources"]`.
2. It calls `_overlay_source_health_truth(...)`.
3. It returns both snake-case and camel-case copies:
   `data_source_status` / `dataSourceStatus` and `data_sources` /
   `dataSources`.

Within `_overlay_source_health_truth(...)`, source-ingest truth is correctly
projected into each matching data-source row:

- row `status`;
- provider-level `provider_statuses[provider_key]`;
- `connector_health` / `connectorHealth`;
- `live_source_connector_ids` / `liveSourceConnectorIds`;
- `required_source_health` / `requiredSourceHealth`;
- source-health availability, connector id, freshness, row counts, and failure
  reason fields.

The current gap is at the end of `_overlay_source_health_truth(...)`: after
`provider_statuses` has been finalized, `dss["state"]` and `dss["summary"]`
are not recomputed. A static `partial_readback` state from `read_store.py` can
therefore survive a live all-green overlay.

### 3.2 Frontend visible symptom

The active frontend checkout renders these two signals independently:

| UI element | Frontend input | Consequence |
|---|---|---|
| State badge label | `formatToken(r.dataSourceStatus?.state)` | `partial_readback` becomes `partial readback` |
| State badge tone | `dataSourceTone(r.dataSourceStatus?.state)` | only tokens containing `read_ok`, `readback_ok`, or `smoke_ok` become green |
| Provider count | `providerOkCount(r).ok/total` from `providerStatuses` | can show `5/5` when all providers are `read_ok` |
| Provider chips | each `dataSources[*].status` | can also show green readback details |

This means the frontend is not the root cause for SRCLIVE-006. It is faithfully
rendering a BFF DTO whose aggregate state is stale relative to the provider map.

### 3.3 Static seed states that must be preserved unless all-green

| Persona | Static state | Parent implication |
|---|---|---|
| `persona-tw-equity` | `partial_readback` | eligible for promotion only when every provider value is ok-tone |
| `persona-us-equity` | `partial_readback` | should remain partial while any provider is `read_unavailable`, `credential_unavailable`, failed, or degraded |
| `persona-crypto` | `datasource_smoke_ok` | already ok-tone; do not rewrite just because CoinGecko flips to `read_ok` |

The parent fix should target aggregate-state promotion after overlay, not
static seed truth in `read_store.py`.

---

## 4. Recommended Parent Fix Boundary

Parent implementation should remain inside the BFF projection layer.

Recommended shape:

1. Add a small helper near `_overlay_source_health_truth(...)` that classifies
   provider-status tokens as frontend ok-tone when they contain one of:
   `read_ok`, `readback_ok`, or `smoke_ok`.
2. After all provider rows have been overlaid and before returning from
   `_overlay_source_health_truth(...)`, inspect the finalized
   `provider_statuses`.
3. If there is at least one provider and every provider is ok-tone, promote the
   aggregate state only when the current `dss["state"]` is not already ok-tone.
4. Use a token containing `readback_ok`, for example `live_readback_ok`, so the
   current frontend `dataSourceTone(...)` treats the badge as green without a
   frontend change.
5. Replace the stale static summary with an honest live-readback summary, for
   example noting that all declared provider readbacks are currently green via
   BFF/source-ingest overlay.
6. Leave `order_side_effects_allowed` and `capital_side_effects_allowed` as
   read-only guard fields; this task must not imply any write authority.

Boundaries to preserve:

| Boundary | Required behavior |
|---|---|
| Partial personas | any `read_unavailable`, `credential_unavailable`, `source_health_failed`, `source_health_degraded`, missing provider, or non-ok token prevents state promotion |
| Already ok-tone state | leave `datasource_smoke_ok`, `quote_readback_ok`, or other ok-tone state unchanged |
| Empty provider map | do not promote; no providers means no aggregate proof |
| Credential gates | missing paid provider credentials remain non-green and keep their `secret_ref`/reason |
| Source-ingest truth | do not hardcode individual providers to `read_ok`; green still comes from connector health or existing broker smoke truth |
| Frontend transport | browser continues to call BFF only |

---

## 5. Contract Test Handoff

Recommended target: `services/control-plane/bff/test_srclive_overlay_contract.py`.

Add focused tests that call `_overlay_source_health_truth(...)` directly:

| Case | Fixture | Required assertion |
|---|---|---|
| TW all-green promotion | `state=partial_readback`; provider statuses include `shioaji=read_ok`, `twse/tpex/mops/finmind=read_unavailable`; source-ingest truth marks TWSE/TPEx, MOPS, and FinMind connector health `ok` | finalized provider statuses are all `read_ok`; `out_dss["state"] == "live_readback_ok"` or another token containing `readback_ok`; summary no longer says default unavailable |
| US partial no-promotion | `state=partial_readback`; `ibkr=read_ok`, public providers maybe `read_ok`, paid providers remain `credential_unavailable` | `out_dss["state"] == "partial_readback"` and credential reasons/secret refs remain present |
| Existing ok-tone no rewrite | `state=datasource_smoke_ok`; `coingecko` flips from `read_unavailable` to `read_ok` | state remains `datasource_smoke_ok`; provider status flips; CoinGecko remains read-only |
| Missing health no-promotion | mapped provider has registry or no truth but no healthy source-ingest health | provider stays non-green and state remains partial |

The test should use the same token grammar as the frontend:
`read_ok|readback_ok|smoke_ok`. That keeps BFF and UI behavior aligned without
depending on frontend code in this repo.

Optional broader test: update
`test_pathreon_market_persona_fleet_contract.py` only if the parent wants an
end-to-end API route assertion. The direct overlay tests are sufficient for the
bug if they cover all-green and non-all-green cases.

---

## 6. Live Operator Journey

Recommended parent smoke path after code and focused tests pass:

1. Deploy or restart the dev BFF with the parent branch.
2. Ensure source-ingest health snapshot has `status: ok` for the TW connectors
   used by the persona:
   `tw-twse-tpex-official-market`, `tw-mops-official-disclosures`, and
   `tw-finmind-datasets` or the selected FinMind candidate.
3. Wait one BFF overlay/cache TTL or restart BFF before persona-fleet readback.
4. Query the BFF persona fleet:

   ```bash
   curl -sS -H 'Authorization: Bearer op-pathreon-fleet:operator,reviewer,admin:mfa' \
     "$BFF_BASE_URL/bff/management/persona-fleet"
   ```

5. Locate `persona-tw-equity`.
6. Confirm `dataSourceStatus.provider_statuses` has all five declared providers
   green:
   `shioaji`, `twse`, `tpex`, `mops`, and `finmind`.
7. Confirm `dataSourceStatus.state` contains `readback_ok` and is not
   `partial_readback`.
8. Confirm `dataSourceStatus.summary` no longer claims TWSE/TPEx/MOPS/FinMind
   default to unavailable when the live response shows them green.
9. Confirm read-only guard fields remain false:
   `order_side_effects_allowed=false` and
   `capital_side_effects_allowed=false`.
10. Query or inspect US and crypto rows to prove non-target behavior:
    US stays partial while any credential/unavailable provider remains, and
    crypto keeps its existing ok-tone state.

Suggested verifier update: `scripts/verify_srclive_readback.py` should fail if
`persona-tw-equity` provider statuses are all expected green but
`dataSourceStatus.state` does not contain `readback_ok`. It should not require
US state promotion while Polygon/Alpha Vantage remain credential-unavailable.

---

## 7. Frontend Handoff Rules

No frontend code change is required for the parent fix if BFF emits an ok-tone
state token.

| Rule | Required behavior |
|---|---|
| Badge source | continue using `dataSourceStatus.state` for the aggregate badge |
| Count source | continue using `dataSourceStatus.providerStatuses` for the `X/Y` count |
| Green state | aggregate state becomes green only when the BFF state token contains `read_ok`, `readback_ok`, or `smoke_ok` |
| Partial state | keep partial/credential/unavailable states amber or non-green |
| Provider chips | display BFF provider statuses as supplied; no local promotion |
| Transport | browser calls BFF only; no direct source-ingest or market-data provider fetches |
| Authority | data-source chips and aggregate state are read-only readback indicators, not order, capital, RuntimeBinding, or governance-write authority |

Frontend smoke expected after SRCLIVE-006 lands:

| Persona | Expected badge | Expected count | Notes |
|---|---|---|---|
| `persona-tw-equity` | green `live readback ok` or equivalent ok-tone label | `5/5` | all provider statuses green |
| `persona-us-equity` | partial/non-green until all providers are ok-tone | depends on credentials and source health | credential-unavailable paid providers must block promotion |
| `persona-crypto` | unchanged ok-tone if already `datasource_smoke_ok` | CoinGecko can be green independently | do not rewrite existing ok-tone state |

---

## 8. BFF Query and Projection Gap Matrix

| Gap or boundary | Why it matters | Parent/reviewer implication |
|---|---|---|
| `provider_statuses` and `state` are independent today | Frontend can show `5/5` and amber badge in the same cell | Parent should recompute aggregate state after overlay finalizes statuses |
| `summary` remains static seed text | Live all-green TW row can still say unavailable sources default unavailable | Parent should replace stale summary only when aggregate promotion happens |
| US has intentional credential gates | All-green promotion would be false while Polygon/Alpha Vantage lack keys | Parent tests must prove non-all-green stays partial |
| Crypto state is already ok-tone | Rewriting it can create unnecessary contract churn | Parent fix should leave ok-tone aggregate states unchanged |
| Source-ingest health remains the only green path | Prevents hardcoded BFF or frontend green state | Reviewer should reject fixes that mark providers green without health/broker evidence |
| Frontend already has ok-token grammar | A BFF token containing `readback_ok` is enough to turn the badge green | No frontend patch is necessary unless product copy wants a different label |
| Readback is not authority | A green data-source badge can be confused with trading readiness | Summary and UI should preserve read-only side-effect guards |

---

## 9. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet and task-scoped brief are changed by this sidecar |
| Canonical truth untouched | PASS if no L1 docs, BFF code, source-ingest code, registry/governance code, or frontend code changed by this sidecar |
| Parent bug captured | PASS if packet names `_overlay_source_health_truth` as the stale aggregate-state boundary |
| Frontend symptom explained | PASS if packet explains badge uses `state` while count uses `providerStatuses` |
| Promotion guardrails present | PASS if all-green promotes only non-ok-tone aggregate states |
| Non-target behavior preserved | PASS if US partial/credential states and crypto ok-tone state are explicitly protected |
| Live operator proof defined | PASS if `/bff/management/persona-fleet` TW `state` and `provider_statuses` are both required |
| No false authority | PASS if readback success remains read-only and does not imply order/capital/governance write permission |

---

## 10. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-006-SIDECAR-BFF-HANDOFF`; only generated sidecar task brief was dirty before this packet |
| `git branch --show-current` | `task/SRCLIVE-006-SIDECAR-BFF-HANDOFF` |
| `git remote -v` | remote is `origin` at `https://github.com/ajoe734/pantheon.git` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-006` | Parent is active `in_progress`; owner `Codex`; reviewer `Claude2`; bug and completion definition confirmed |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-006-SIDECAR-BFF-HANDOFF` | Sidecar is active `in_progress`; owner `Codex2`; reviewer `Codex`; artifact target is this packet |
| `rg 'def _overlay_source_health_truth|dataSourceTone|provider_statuses' ...` | Confirmed BFF projection path and frontend badge/count split |
| `sed -n '50280,50580p' services/control-plane/bff/main.py` | Confirmed overlay returns without aggregate `state` recomputation |
| `sed -n '320,490p' /home/lupin/code/execute-plans/src/management/pages/oversight/_core.tsx` | Confirmed frontend ok-token grammar and independent provider count |

No runtime tests were run for this sidecar because it changes only support
artifacts. The parent task owns BFF implementation tests and live operator smoke
verification.

---

## 11. Handoff Status

At packet creation time, this packet is ready for `Codex` review as support
material. It should not be treated as approval of new canonical implementation,
runtime wiring, BFF code changes, source-ingest changes, registry/governance
changes, or frontend code changes from this sidecar.

Parent owner `Codex` should decide whether to absorb these notes into
SRCLIVE-006, adjust the parent implementation plan, or request a narrow
correction to this packet.
