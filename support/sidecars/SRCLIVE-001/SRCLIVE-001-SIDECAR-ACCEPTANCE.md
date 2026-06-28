# SRCLIVE-001 Sidecar Acceptance Packet

- Task: `SRCLIVE-001-SIDECAR-ACCEPTANCE`
- Parent task: `SRCLIVE-001`
- Helper kind: `acceptance_packet`
- Sidecar owner: `Codex2`
- Sidecar reviewer: `Codex`
- Parent owner: `Codex`
- Parent reviewer: `Claude`
- Prepared: 2026-06-28
- Scope: support artifact only; no L1 canonical truth, core contract truth, runtime, registry, governance, or routing implementation changes.

## Purpose

This packet supports `SRCLIVE-001` by giving the parent owner a focused
acceptance checklist and dependency map for the Taiwan official source live
activation slice.

Parent acceptance, as currently assigned, is narrow:

1. Run a live dev activation for TW official source-ingest providers.
2. Verify `persona-tw-equity` in `GET /bff/management/persona-fleet` reports
   `provider_statuses.twse=read_ok`, `provider_statuses.tpex=read_ok`, and
   `provider_statuses.mops=read_ok`.
3. Verify the panel moves from `2/5` provider readback to `5/5`.
4. Archive a rerunnable runbook at `docs/05/srclive/tw-activation-runbook.md`.

This sidecar does not approve the parent task, does not run the live activation,
and does not change any source-ingest, BFF, connector, or frontend behavior.
The parent owner decides whether to absorb this packet into the main delivery.

## Sources Read

- `AI_COLLABORATION_GUIDE.md`
- `ai-status.json`
- `.orchestrator/task-briefs/srclive_001_sidecar_acceptance.md`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001-SIDECAR-ACCEPTANCE`
- `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001`
- `services/source_ingestion/connectors/taiwan_official.py`
- `services/source_ingestion/connectors/taiwan_market.py`
- `services/source_ingestion/active_universe.py`
- `services/source_ingestion/main.py`
- `services/source_ingestion/source_health.py`
- `services/source_ingestion/tests/test_taiwan_official_connectors.py`
- `services/source_ingestion/tests/test_taiwan_market_connectors.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py`
- `docs/deployment/source-search-prod-hardening.md`
- `docs/deployment/external-data-integration-materialization-audit.md`
- `support/sidecars/P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001/P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001-SIDECAR-BFF-HANDOFF.md`

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

## Current Task Snapshot

| Item | State |
|---|---|
| Parent task | `SRCLIVE-001` |
| Parent status | `in_progress` |
| Parent owner / reviewer | `Codex` / `Claude` |
| Parent declared artifacts | `services/source_ingestion/connectors/taiwan_official.py`, `services/source_ingestion/connectors/taiwan_market.py`, `services/source_ingestion/active_universe.py`, `docs/05/srclive/tw-activation-runbook.md` |
| Parent live acceptance | `persona-tw-equity.provider_statuses` has `twse`, `tpex`, and `mops` at `read_ok`; panel count is `5/5`; runbook is archived |
| Sidecar artifact | `support/sidecars/SRCLIVE-001/SRCLIVE-001-SIDECAR-ACCEPTANCE.md` |

## Non-Scope Guardrails

- Do not change L1 policy, source/evidence/search contracts, BFF route truth, or
  registry governance from this sidecar.
- Do not mark `SRCLIVE-001` accepted based on repo-local fixture tests alone.
- Do not treat source-ingest service health as sufficient unless BFF
  `persona-tw-equity` also projects the provider statuses required by the parent
  acceptance.
- Do not conflate TEJ with official TW source truth. TEJ is a paid
  research-grade fallback/backfill lane and does not replace TWSE/TPEx/MOPS
  official-reference truth.
- Do not claim broker, order, RuntimeBinding, capital, paper, canary, or live
  trading authority from this task. The slice is read-only source activation.

## Dependency Map

| Dependency surface | Current relevance to `SRCLIVE-001` | Acceptance implication |
|---|---|---|
| `P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001` | Established the non-ordering external source/search pattern and the `dependency_missing` convention when a live/test feed is absent. | Parent should record explicit unavailable/dependency evidence instead of claiming live proof when a provider cannot be reached. |
| Source/search production posture | `docs/deployment/source-search-prod-hardening.md` separates durability posture from live-data enablement. | Parent runbook should state whether dev live activation uses dev posture or production posture; posture checks are not a substitute for TW provider readback. |
| `TaiwanOfficialMarketDatasetAdapter` | Provides `tw-twse-tpex-official-market`, TWSE/TPEx endpoint inventory, `official_reference_truth=true`, and `SourceHealth` projection with `status="ok"` and `row_count_last_run`. | One connector backs both `twse` and `tpex` provider keys in BFF. Parent must prove both providers project to `read_ok`, not only that the shared connector exists. |
| `MopsSourceIngestAdapter` | Provides `tw-mops-official-disclosures`, official filing/disclosure records, and MOPS route metadata. | Parent must write/observe an `ok` source health record for this connector so BFF can flip `mops` from static unavailable to `read_ok`. |
| `active_universe.py` | Gives TWSE/TPEx official daily price priority 5 and MOPS material/revenue/financial official routes priority 25/30/31. | Parent runbook should say which active-universe symbols/routes were used for the dev live run and why they are bounded. |
| BFF source health overlay | `services/control-plane/bff/main.py` maps `twse`/`tpex` to `tw-twse-tpex-official-market` and `mops` to `tw-mops-official-disclosures`; source health `ok` maps to `read_ok`. | Parent verification must capture the BFF response after source-ingest health is present, not only source-ingest direct API output. |
| BFF static market persona defaults | `services/control-plane/bff/read_store.py` starts TW persona with `shioaji=read_ok`, while `twse`, `tpex`, `mops`, and `finmind` are unavailable in repo-local static evidence. | The parent's `2/5 -> 5/5` claim should identify the two pre-existing readbacks and the three newly activated official providers. |

## Parent Acceptance Checklist

### AC-1 - TWSE/TPEx official connector live readback is real

Required evidence:

- A read-only network run reaches a TWSE official endpoint and normalizes at
  least one row into `SourceRecord` metadata with:
  - `provider=TWSE/TPEx`
  - `dataset=tw_price_daily` or another parent-selected official dataset
  - `venue=TWSE`
  - `license_scope=official_reference`
  - `available_time`
  - nonzero `row_count_last_run`
- A read-only network run reaches a TPEx official endpoint and normalizes at
  least one row with the same official-reference invariants and `venue=TPEx`.
- `SourceHealth` for `tw-twse-tpex-official-market` is persisted with
  `status=ok`, `row_count_last_run > 0`, and a current `latest_watermark` or
  `last_success_at`.
- No broker/order/capital/RuntimeBinding route is called.

Suggested local regression:

```bash
python3 -m pytest -q services/source_ingestion/tests/test_taiwan_official_connectors.py
```

Optional network smoke already exists but is skip-gated:

```bash
PANTHEON_TW_OFFICIAL_LIVE_SMOKE=1 \
python3 -m pytest -q services/source_ingestion/tests/test_taiwan_official_connectors.py::test_taiwan_official_live_read_only_smoke_for_one_twse_and_tpex_symbol
```

### AC-2 - MOPS official disclosure live readback is real

Required evidence:

- A read-only MOPS route smoke reaches an official MOPS route selected by the
  parent runbook and records at least one normalized `SourceRecord`.
- The record keeps `source_class=official_reference`, `provider=MOPS`,
  `license_scope=official_reference`, `available_time`, route id, and a
  normalized target such as `tw_material_event`, `tw_monthly_revenue`, or
  `tw_financial_statement`.
- `SourceHealth` for `tw-mops-official-disclosures` is persisted with
  `status=ok`, `row_count_last_run > 0`, and current success metadata.
- Any route with no rows must be recorded as explicit read-unavailable or
  dependency evidence, not as `read_ok`.

Suggested local regression:

```bash
python3 -m pytest -q services/source_ingestion/tests/test_taiwan_market_connectors.py
```

### AC-3 - Source-ingest health-usage snapshot proves the service layer

Required evidence:

```bash
curl -fsS "$SOURCE_INGEST_URL/api/source-ingest/health-usage-snapshot"
```

The captured JSON should show:

- `sources[].health.source_id == "tw-twse-tpex-official-market"` with
  `status=ok` and `row_count_last_run > 0`.
- `sources[].health.source_id == "tw-mops-official-disclosures"` with
  `status=ok` and `row_count_last_run > 0`.
- `recommendation` data, if present, does not recommend retirement for either
  freshly read source.
- Any failed/degraded provider has a concrete failure reason.

This is necessary but not sufficient; BFF projection is still required.

### AC-4 - BFF persona fleet projects the accepted provider statuses

Required evidence:

```bash
curl -fsS \
  -H "Authorization: Bearer <operator-token>" \
  "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/management/persona-fleet"
```

The archived response should include the `persona-tw-equity` row with:

```json
{
  "dataSourceStatus": {
    "provider_statuses": {
      "twse": "read_ok",
      "tpex": "read_ok",
      "mops": "read_ok"
    },
    "source_health_source": "source_ingest",
    "live_ingestion_enabled": true
  }
}
```

The row should also retain:

- `order_side_effects_allowed=false`
- `capital_side_effects_allowed=false`
- no live broker/order/capital side effects
- connector health details with current `lastSuccessAt`/`rowCountLastRun`

### AC-5 - Panel count moves from 2/5 to 5/5 without overclaiming

The parent closeout should include a before/after note:

| Provider | Before static/default status | Expected after `SRCLIVE-001` |
|---|---|---|
| `shioaji` | `read_ok` from prior quote-readback evidence | unchanged |
| `finmind` or other existing live-read provider | existing `read_ok` if present in dev | unchanged and named explicitly |
| `twse` | `read_unavailable` unless source-ingest health overlay is present | `read_ok` |
| `tpex` | `read_unavailable` unless source-ingest health overlay is present | `read_ok` |
| `mops` | `public_reference_unavailable` unless source-ingest health overlay is present | `read_ok` |

If the second pre-existing readback is not `finmind` in the target dev
environment, the parent runbook should name the actual provider that makes the
baseline `2/5`.

### AC-6 - Runbook is rerunnable

`docs/05/srclive/tw-activation-runbook.md` should be created by the parent task
and include:

- exact env vars, service URLs, and operator-token assumptions
- bounded TWSE, TPEx, and MOPS provider/run selection
- source-ingest commands or scripts used to create health records
- BFF persona-fleet curl command
- expected JSON paths and accepted values
- evidence file locations
- rollback/disable instructions for connector schedules or health writes
- explicit no-order/no-capital/no-RuntimeBinding statement

## Reviewer Focus

For `SRCLIVE-001`, reviewer should check:

| Check | Expected |
|---|---|
| Support-only separation | This sidecar only adds this packet; parent implementation and runbook remain parent-owned. |
| Live evidence quality | Parent has live dev evidence for TWSE, TPEx, and MOPS, or explicit unavailable/dependency evidence for any provider not accepted. |
| SourceHealth to BFF chain | `health-usage-snapshot` and `persona-fleet` are both captured after the run. |
| Provider projection | BFF response shows `twse`, `tpex`, and `mops` as `read_ok` under `persona-tw-equity.dataSourceStatus.provider_statuses`. |
| Safety boundary | Evidence shows read-only official/public source access only; no broker/order/capital/live-trading route calls. |
| Runbook replayability | A fresh operator can rerun the activation and BFF verification without reading this packet. |

## Handoff Note

This packet is ready for Codex review as support material. The parent owner can
use it as the acceptance checklist while completing `SRCLIVE-001`, especially to
avoid two common overclaims:

1. Treating source-ingest direct health as enough without the BFF
   `persona-tw-equity` provider projection.
2. Treating a missing TW official provider as successful instead of recording
   explicit unavailable/dependency evidence.
