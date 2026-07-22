# SRCLIVE-006 BFF and Frontend Handoff Follow-up 2

**Parent Task**: `SRCLIVE-006`
**Parent Status at packet time**: `review_approved`
**Sidecar Task**: `SRCLIVE-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change L1 canonical truth, BFF
runtime behavior, source-ingest behavior, registry/governance behavior, or
frontend code. The parent owner/reviewer decide whether any of this support
material is absorbed into the main SRCLIVE-006 closeout.

---

## 1. Follow-up Purpose

The original sidecar packet already captured the root SRCLIVE-006 bug:
`provider_statuses` could be all green while `data_source_status.state` stayed
at the static `partial_readback` seed. At this follow-up point, the parent
branch already contains the BFF projection helper
`_upgrade_all_green_data_source_state(...)` and focused SRCLIVE contract tests.

This packet therefore focuses on handoff material that remains easy to miss
after the parent fix:

1. Which BFF route and response envelope operators and the frontend should
   query.
2. Which state/provider fields prove the all-green TW badge fix.
3. How the live verifier treats BFF provider keys versus source-ingest-only
   health proofs.
4. How to smoke the frontend without accidentally falling back to seed data.
5. Which UI details can make a correct BFF response look incomplete during a
   manual browser check.

Non-goals:

- no BFF code changes;
- no test changes;
- no verifier changes;
- no execute-plans frontend changes;
- no change to credential gates, source-ingest connector truth, trading
  authority, RuntimeBinding, governance writes, or capital/order side effects.

---

## 2. Current Source Snapshot

| Surface | Current observation |
|---|---|
| `services/control-plane/bff/main.py` | `_overlay_source_health_truth(...)` calls `_upgrade_all_green_data_source_state(dss)` after provider statuses, connector health, live connector ids, and required-source bindings are finalized. |
| `services/control-plane/bff/test_srclive_overlay_contract.py` | Focused tests cover TW all-green promotion, missing source-ingest no-promotion, US credential gates, and crypto ok-tone preservation. |
| `scripts/verify_srclive_readback.py` | Live verifier checks `/bff/management/persona-fleet`, requires TW all-green providers, requires TW/US `source_health_source == source_ingest`, allows source-only Stooq as optional BFF provider evidence, and can also inspect source-ingest health. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | `mgmtPersonaFleet()` currently calls `/bff/management/fleet`, not the legacy `/bff/management/persona-fleet` alias. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/management.ts` | `adaptManagementPersonaFleet(...)` accepts `items`, `persona_fleet`, or `personaFleet` under either the top-level payload or `data`. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts` | `VITE_BFF_FALLBACK=strict` is required when browser smoke must fail instead of masking live transport errors with seed data. |
| `/home/lupin/code/execute-plans/src/management/pages/oversight/_core.tsx` | Badge tone uses `dataSourceStatus.state`; provider count uses `providerStatuses`; provider chips use `visibleDataSources(...)`. |
| `/home/lupin/code/execute-plans/src/management/pages/oversight/personaFleetDataSources.ts` | Provider chips are sorted and sliced to the first four providers, so a TW `5/5` response may not show all five provider chips in the visible row. |

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 3. BFF Query Gap Matrix

| Gap or decision point | Handoff guidance |
|---|---|
| Route name mismatch | The live frontend calls `/bff/management/fleet`. Operator scripts and old UI builds may call `/bff/management/persona-fleet`. The BFF keeps `/bff/management/persona-fleet` as an alias, so both routes should compose the same persona-fleet rows. |
| Response envelope variants | Query tooling should tolerate `payload.items`, `payload.data.items`, and `payload.data.persona_fleet`. The frontend adapter already does this; ad hoc `jq` should not assume only one envelope. |
| Aggregate state proof | The SRCLIVE-006 proof is not only `provider_statuses == read_ok`. For `persona-tw-equity`, `dataSourceStatus.state` or `data_source_status.state` must contain `readback_ok`, currently expected as `live_readback_ok`. |
| Provider count proof | Frontend count comes from `providerStatuses`; the visible count should be `5/5` for TW after live overlay. This count is a better all-provider browser check than the provider chips because the chips display only four items. |
| Summary proof | A promoted TW row should not retain the old static text saying TWSE/TPEx/MOPS/FinMind default to unavailable. The parent helper rewrites the summary only when all declared providers are ok-tone. |
| US provider-map nuance | The verifier no longer requires a BFF `stooq` provider key. Stooq may remain source-ingest-only health proof while the BFF row exposes other US provider keys. If `stooq` is present, it must be `read_ok`; if absent, that is not a verifier failure. |
| US credential gates | `polygon` and `alphavantage` must remain `credential_unavailable` until source-ingest reports credential-backed ok health. Any US non-ok provider keeps aggregate state non-promoted unless the current aggregate state is already ok-tone for another valid reason. |
| Crypto ok-tone preservation | Crypto can flip `coingecko` to `read_ok` while preserving `datasource_smoke_ok`. The all-green helper intentionally does not rewrite an aggregate state that already has an ok-tone token. |
| Side-effect authority | Data-source readback is a read-only health signal. Green readback does not imply order, capital, RuntimeBinding, governance, or write authority. |

---

## 4. Operator Readback Journey

Use the dev BFF base and operator token from the environment when available:

```bash
export BFF_BASE_URL="${BFF_BASE_URL:-https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io}"
export BFF_TOKEN="${BFF_TOKEN:-op-dev:admin:mfa}"
```

Read the persona fleet through the legacy operator route or the current
frontend route:

```bash
curl -sS -H "Authorization: Bearer ${BFF_TOKEN}" \
  "${BFF_BASE_URL}/bff/management/persona-fleet" > /tmp/srclive-persona-fleet.json

curl -sS -H "Authorization: Bearer ${BFF_TOKEN}" \
  "${BFF_BASE_URL}/bff/management/fleet" > /tmp/srclive-management-fleet.json
```

When inspecting the payload, first normalize the envelope:

```bash
jq '
  (.items // .data.items // .data.persona_fleet) as $rows
  | $rows[]
  | select((.personaId // .persona_id // .id) == "persona-tw-equity")
  | {
      persona: (.personaId // .persona_id // .id),
      state: (.dataSourceStatus.state // .data_source_status.state),
      summary: (.dataSourceStatus.summary // .data_source_status.summary),
      providers: (.dataSourceStatus.providerStatuses // .dataSourceStatus.provider_statuses // .data_source_status.provider_statuses),
      source_health_source: (.dataSourceStatus.sourceHealthSource // .dataSourceStatus.source_health_source // .data_source_status.source_health_source),
      live_ingestion_enabled: (.dataSourceStatus.liveIngestionEnabled // .dataSourceStatus.live_ingestion_enabled // .data_source_status.live_ingestion_enabled),
      order_side_effects_allowed: (.dataSourceStatus.orderSideEffectsAllowed // .dataSourceStatus.order_side_effects_allowed // .data_source_status.order_side_effects_allowed),
      capital_side_effects_allowed: (.dataSourceStatus.capitalSideEffectsAllowed // .dataSourceStatus.capital_side_effects_allowed // .data_source_status.capital_side_effects_allowed)
    }
' /tmp/srclive-persona-fleet.json
```

Expected TW assertions after SRCLIVE-006 is deployed:

| Field | Expected result |
|---|---|
| `state` | contains `readback_ok`, expected `live_readback_ok` |
| `providers.shioaji` | `read_ok` |
| `providers.twse` | `read_ok` |
| `providers.tpex` | `read_ok` |
| `providers.mops` | `read_ok` |
| `providers.finmind` | `read_ok` |
| `source_health_source` | `source_ingest` |
| `live_ingestion_enabled` | `true` |
| `summary` | mentions all declared providers reporting readback OK; does not say default unavailable |
| `order_side_effects_allowed` | `false` |
| `capital_side_effects_allowed` | `false` |

Expected non-target assertions:

| Persona | Expected result |
|---|---|
| `persona-us-equity` | Source-ingest-backed public sources may be green, but credential-gated providers remain `credential_unavailable`; do not require an all-green aggregate state unless every provider is ok-tone. |
| `persona-crypto` | `coingecko` may be `read_ok`, but aggregate `state` should remain the existing ok-tone state such as `datasource_smoke_ok`. |

The packaged verifier can be used for the BFF and optional source-ingest check:

```bash
python3 scripts/verify_srclive_readback.py \
  --bff-base "${BFF_BASE_URL}" \
  --token "${BFF_TOKEN}" \
  --source-ingest-base "${SOURCE_INGEST_BASE:-}"
```

If `--source-ingest-base` is omitted or empty, the verifier only checks the BFF
projection. If it is set, it also checks source-ingest health rows for the
declared connector ids.

---

## 5. Frontend Handoff Rules

| Rule | Handoff guidance |
|---|---|
| Live path | The active frontend helper uses `mgmt.personaFleet.get(...)`, which calls `paths.mgmtPersonaFleet()` and currently resolves to `/bff/management/fleet`. |
| Strict browser smoke | Build or run dev frontend with `VITE_BFF_MODE=live`, `VITE_BFF_BASE_URL=<dev BFF>`, and `VITE_BFF_FALLBACK=strict`. Strict fallback prevents seed data from hiding a broken BFF response or CORS/auth failure. |
| Seed caveat | `PERSONA_FLEET_SEED` still contains older fallback TW/US provider text. Seeing seed-era provider names or summaries in a smoke test usually means the browser is in mock mode, fallback mode, or failed live adaptation. |
| Badge source | The aggregate badge label and color come from `dataSourceStatus.state`. Any BFF ok-tone token containing `read_ok`, `readback_ok`, or `smoke_ok` renders green. |
| Count source | The `X/Y` count comes from `dataSourceStatus.providerStatuses`; it can prove `5/5` even when only four provider chips are visible. |
| Provider chip source | Provider chips come from `dataSources`, sorted by status priority and sliced to four entries. Do not use missing fifth chip visibility as evidence that TW is not `5/5`. |
| No local promotion | The frontend should not locally promote `partial_readback` to green. The BFF owns aggregate state; the frontend only renders the supplied DTO. |
| Read-only authority | The row should continue to show side effects off. A green source badge is not a permission to trade, allocate capital, mutate governance state, or bypass human gates. |

Browser smoke expected after SRCLIVE-006 lands:

| UI observation | Expected result |
|---|---|
| TW data-source badge | Green `live readback ok` or equivalent ok-tone label |
| TW provider count | `5/5` |
| TW visible provider chips | Up to four chips shown; all visible chips should be ok-tone, but the fifth provider may be hidden by the top-four slice |
| TW live text | Live ingestion on; side effects off |
| US row | Does not falsely become all-green while credential-gated providers are unavailable |
| Crypto row | Existing ok-tone aggregate state remains stable while CoinGecko readback can be green |

---

## 6. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support-only scope | PASS if this sidecar changes only this support artifact, excluding generated local task brief dirtiness. |
| Canonical truth untouched | PASS if no L1 docs, runtime code, BFF code, source-ingest code, registry/governance code, verifier code, or frontend code changed. |
| Parent state acknowledged | PASS if the packet treats SRCLIVE-006 as already review-approved and does not request duplicate implementation. |
| BFF route handoff clear | PASS if both `/bff/management/fleet` and `/bff/management/persona-fleet` are explained. |
| Envelope handling clear | PASS if operator extraction tolerates `items`, `data.items`, and `data.persona_fleet`. |
| TW proof complete | PASS if the packet requires both all five provider statuses and aggregate state containing `readback_ok`. |
| Frontend smoke caveat clear | PASS if strict live mode and seed fallback risk are called out. |
| UI chip limitation clear | PASS if reviewer knows visible provider chips are limited to four and should not replace the provider count/API check. |
| No false authority | PASS if readback success remains read-only and side-effect guards remain false. |

---

## 7. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; only generated task brief was dirty before this packet. |
| `git branch --show-current` | `task/SRCLIVE-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| `git remote -v` | remote is `origin` at `https://github.com/ajoe734/pantheon.git` |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-006` | Parent is `review_approved`; owner `Codex2`; reviewer `Claude2`; all-green state fix approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Sidecar is active `in_progress`; owner `Codex`; reviewer `Codex2`; artifact target is this packet. |
| `git merge --ff-only origin/dev` | Fast-forwarded the task branch to current `origin/dev` before writing the packet. |
| `sed`/`rg` reads of BFF, verifier, and frontend paths | Confirmed route/envelope behavior, aggregate-state helper, verifier provider map, strict fallback behavior, and top-four provider chip display. |
| `git diff --check -- support/sidecars/SRCLIVE-006/SRCLIVE-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Passed. |
| `python3 -m pytest services/control-plane/bff/test_srclive_overlay_contract.py` | Passed: `5 passed`, with existing FastAPI `on_event` deprecation warnings. |

---

## 8. Handoff Status

This packet is ready for `Codex2` support-scope review once committed and
published on the task branch. It is a handoff supplement for SRCLIVE-006, not a
new parent implementation or approval of any additional runtime behavior.
