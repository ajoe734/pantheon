# SRCLIVE-002 BFF and Frontend Handoff Packet

**Parent Task**: `SRCLIVE-002` - US research sources live wiring
**Parent Owner**: `Claude2`
**Parent Reviewer**: `Codex`
**Parent Status at packet time**: `in_progress`
**Sidecar Task**: `SRCLIVE-002-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude2`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, source-ingest code, registry/governance behavior,
or frontend code. Parent ownership decides whether to absorb any of this into
the main SRCLIVE-002 implementation.

---

## 1. Scope

SRCLIVE-002 is wiring all existing US research connectors into
`persona-us-equity` without pretending unavailable providers are green.

This sidecar only packages the BFF/frontend handoff facts:

1. BFF query surfaces that the operator/frontend should use.
2. Provider-key to connector-id mapping expected by the source-health overlay.
3. Current projection gaps that can block the parent acceptance if left as-is.
4. Frontend chip/status rules that preserve source-ingest truth.
5. Operator smoke journey for parent closeout.

Non-goals:

- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py` persona seed truth;
- no edits to source-ingest connectors, active-universe rules, or runbooks;
- no new frontend route or direct source-ingest browser call.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `ai-status.json` via `AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-002` | live parent task truth and acceptance summary |
| `.orchestrator/task-briefs/srclive_002_sidecar_bff_handoff.md` | sidecar scope and support-only boundary |
| `services/control-plane/bff/main.py` | BFF source-health overlay and persona-fleet projection |
| `services/control-plane/bff/read_store.py` | current `persona-us-equity` seed data-source truth |
| `services/control-plane/bff/tests/test_bff_management_data_sources_contract.py` | contract for `GET /bff/management/data-sources` degraded/ok envelope |
| `services/source_ingestion/connectors/us_public.py` | SEC/FRED/FINRA/Stooq connector IDs and auth posture |
| `services/source_ingestion/connectors/us_paid_broker.py` | Polygon/Alpha Vantage credential-gated posture |
| `services/source_ingestion/active_universe.py` | scheduled active-universe source update rules |
| `services/source_ingestion/financial_source_catalog.py` | US source catalog and config-template metadata |

---

## 3. Current Truth Snapshot

### 3.1 BFF surfaces

The frontend should stay behind BFF:

| Surface | Route | Current role |
|---|---|---|
| Source registry/operator list | `GET /bff/management/data-sources` | BFF envelope over source-ingest registry; includes degraded `missing`/`unavailable` states |
| Persona chips / fleet panel | `GET /bff/management/persona-fleet` | projects `data_source_status`, `data_sources`, `required_data_sources`, and source-health overlay fields |
| Persona detail DTOs | BFF persona detail surfaces | same overlay path as fleet, for drilldowns that render gap reasons |

Browser/frontend code should not call `/api/source-ingest/*` directly. The BFF
already reads:

- `/api/source-ingest/registry`
- `/api/source-ingest/health-usage-snapshot`

### 3.2 Source-ingest US connector inventory exists

Current source-ingest code already defines these connector IDs:

| Provider key expected by parent | Connector id | Auth posture | Current implementation note |
|---|---|---|---|
| `stooq` | `us-stooq-daily-ohlcv` | `AuthType.NONE` | disabled until runtime endpoint smoke verifies Stooq from the target environment |
| `sec_edgar` | `us-sec-edgar-filings` | `AuthType.NONE` | requires compliant `SEC_EDGAR_USER_AGENT`; no API key |
| `finra` | `us-finra-short-sale` | `AuthType.NONE` | public daily short-volume files; expected publication delay is 26 hours |
| `fred` | `us-fred-macro` | `AuthType.NONE` | optional `env://FRED_API_KEY`, with public CSV fallback |
| `polygon` | `us-polygon-daily-ohlcv` | `AuthType.API_KEY` | key-gated; envs are `POLYGON_API_KEY`, `MASSIVE_API_KEY`, `US_MARKET_DATA_API_KEY` |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` | `AuthType.API_KEY` | disabled/low-quota fallback; env is `ALPHA_VANTAGE_API_KEY` |

`active_universe.py` already includes rules for the four no-key US connectors in
this worktree: SEC, FRED, FINRA, and Stooq. Stooq's rule and connector metadata
still mark it as disabled until endpoint verification.

### 3.3 BFF provider-candidate map is still TW-only here

`_SOURCE_PROVIDER_CONNECTOR_CANDIDATES` currently maps only:

- `finmind`
- `twse`
- `tpex`
- `mops`

Therefore parent SRCLIVE-002 still needs the US provider-key map before
`_overlay_source_health_truth` can bind `persona-us-equity` chips to live
source-ingest health by provider key:

| Provider key | Candidate connector ids |
|---|---|
| `stooq` | `us-stooq-daily-ohlcv` |
| `sec_edgar` | `us-sec-edgar-filings` |
| `finra` | `us-finra-short-sale` |
| `fred` | `us-fred-macro` |
| `polygon` | `us-polygon-daily-ohlcv` |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` |

Frontend must treat `provider_key` as an opaque BFF key. Do not convert
`sec_edgar` to `sec-edgar`, `alpha_vantage`, or display text before lookup.

### 3.4 `persona-us-equity` is still IBKR-only here

The current read model for US persona data sources declares only:

- `provider_key: ibkr`
- `source_class: broker_execution`
- `status: read_ok`
- `order_capable_provider: true`
- `order_path: disabled_for_marketdata_smoke`

Parent SRCLIVE-002 still needs to add the six research-grade or
official-reference US sources to `persona-us-equity` with
`order_capable_provider: false`. Without those entries, the frontend has no chip
rows to render and the overlay has no provider-key rows to enrich.

---

## 4. BFF Query and Projection Gap Matrix

| Gap | Why it matters | Parent implementation implication |
|---|---|---|
| US provider keys are absent from `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES` | overlay cannot select source-ingest truth by `provider_key` | add the six provider-key mappings above |
| `persona-us-equity` seed truth only lists IBKR | frontend cannot show the required seven chips | add six `data_sources` rows plus provider statuses |
| Stooq is currently disabled until endpoint smoke | task acceptance wants Stooq `read_ok`; current code prevents honest green without runtime proof | parent must run/record endpoint smoke and only then allow `read_ok` |
| paid credential health projects as source health unless normalized | `_provider_status_from_truth` maps health `degraded` to `source_health_degraded`; task acceptance asks for `credential_unavailable` | parent may need a BFF projection rule that promotes `sourceHealth.metadata.credential_status` to top-level status |
| paid health metadata lists env vars, not a single top-level `secret_ref` | acceptance asks reason and required `secret_ref`; current health metadata exposes `credential_env_vars` and note | parent should surface `secret_ref_id` from connector/auth metadata or static row reason |
| source-ingest missing/down must stay degraded | no service means no live truth | frontend should render unavailable/degraded, not cached green |

The key rule remains: BFF must not hardcode `read_ok`. A provider can become
green only when the BFF mapping points to a connector and
`/api/source-ingest/health-usage-snapshot` reports that connector healthy.

---

## 5. Frontend Handoff Rules

Expected chip order for the SRCLIVE-002 acceptance view:

1. `ibkr`
2. `stooq`
3. `sec_edgar`
4. `finra`
5. `fred`
6. `polygon`
7. `alphavantage`

Frontend rendering rules:

| Rule | Required behavior |
|---|---|
| Transport | call BFF only; never call source-ingest from browser |
| Green state | render green only for top-level `status == "read_ok"` |
| Credential state | render key-gated providers as credential unavailable when BFF status or nested `sourceHealth.metadata.credential_status` says `credential_unavailable` |
| Unknown/degraded state | preserve `source_health_*`, `connector_*`, `read_unavailable`, and `credential_unavailable` as distinct non-green states |
| Freshness | prefer BFF fields such as `lastSuccessAt`, `latestWatermark`, `rowCountLastRun`, `failureReason`, and `sourceHealth` over local timers |
| Ordering | keep BFF/provider order if supplied; otherwise use the acceptance order above |
| Write authority | data-source chips are read-only; they do not imply order authority or broker write capability |

The frontend should show an honest reason for:

- Stooq if endpoint smoke has not proven it healthy;
- Polygon if no accepted key exists for one of `POLYGON_API_KEY`,
  `MASSIVE_API_KEY`, or `US_MARKET_DATA_API_KEY`;
- Alpha Vantage if `ALPHA_VANTAGE_API_KEY` is absent or if the connector remains
  disabled for quota policy.

---

## 6. Operator Journey for Parent Closeout

Recommended parent smoke path:

1. Confirm source-ingest registry lists all six US research connector IDs.
2. Confirm `active_universe_policy` contains the four no-key US rules.
3. Run or replay no-key ingest for SEC, FRED, FINRA, and Stooq from the target
   environment.
4. For SEC, confirm the run used a compliant user agent.
5. For Stooq, confirm the endpoint smoke removed the disabled/unverified
   posture before claiming `read_ok`.
6. Confirm `/api/source-ingest/health-usage-snapshot` reports `ok` for the
   four no-key connector IDs.
7. Confirm Polygon and Alpha Vantage report credential-unavailable posture when
   secrets are absent, including the env/secret reference a human must set.
8. Confirm BFF has `PANTHEON_SOURCE_INGEST_API_URL` or equivalent source-ingest
   base URL configured.
9. Query `GET /bff/management/data-sources` and confirm source `service_client`
   with registry items.
10. Query `GET /bff/management/persona-fleet` and confirm `persona-us-equity`
    exposes seven data-source chips with source-ingest overlay fields.

Suggested BFF smoke targets:

```bash
curl -sS -H 'Authorization: Bearer op-ds-001:operator,reviewer' \
  "$BFF_BASE_URL/bff/management/data-sources"

curl -sS -H 'Authorization: Bearer op-ds-001:operator,reviewer' \
  "$BFF_BASE_URL/bff/management/persona-fleet"
```

The closeout proof should include the BFF response excerpt for
`persona-us-equity`, not only source-ingest service output, because the frontend
consumes the BFF projection.

---

## 7. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet and task-scoped sidecar context are changed |
| Canonical truth untouched | PASS if no L1 docs, BFF code, source-ingest code, or frontend code changed by this sidecar |
| BFF query gap identified | PASS if the packet names the missing US provider-key map |
| Persona chip gap identified | PASS if the packet names the IBKR-only current state |
| Credential honesty preserved | PASS if paid providers cannot become green without secrets |
| Stooq overclaim avoided | PASS if Stooq requires runtime endpoint proof before `read_ok` |
| Frontend boundary preserved | PASS if browser calls BFF only and treats chips as read-only state |

---

## 8. Handoff Status

Reviewed by `Claude2` and approved for owner closeout per
`AI_NAME=Codex ./scripts/ai-status.sh show SRCLIVE-002-SIDECAR-BFF-HANDOFF`.
The status record reports all 7 reviewer checklist items passing, with the
sidecar remaining support-only and canonical truth untouched.

This packet is intentionally sidecar support material. It should not be treated
as the canonical SRCLIVE-002 implementation or as proof that the seven-chip
acceptance state is live. Parent owner `Claude2` still decides whether and how
to absorb these BFF/frontend handoff notes into the main SRCLIVE-002 work.
