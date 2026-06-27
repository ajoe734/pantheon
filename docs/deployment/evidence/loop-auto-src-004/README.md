# Evidence: LOOP-AUTO-SRC-004 - SourceHealth Truth In Persona Panels

Task: `LOOP-AUTO-SRC-004`
Owner: Codex
Reviewer: Codex2
Date: 2026-06-27

## Delivered Surface

- Added BFF SourceHealth truth composition for persona DTOs, management persona
  fleet rows, and v5 execution persona-health rows.
- The BFF now reads source-ingest connector registry truth and
  health-usage snapshot truth, then projects:
  - connector schedule
  - connector freshness
  - last fetch timestamp
  - last successful push timestamp
  - latest watermark
  - row/reject counts
  - failure reason
  - source-ingest vs static-metadata truth source
- Added TW market persona `required_data_sources` defaults for:
  - `tw_price_daily` live pull through FinMind or TWSE/TPEx connectors
  - `tw_broker_top` live push through FinMind broker payload connectors
- Kept the BFF read-only: no source-ingest writes, no scheduler behavior
  changes, no broker/order/capital side effects.

## Acceptance Evidence

1. Persona panel shows connector schedule, last fetch, last push, and failure reason.
   - `test_source_health_truth_overlay_projects_connector_panel_fields`
   - The test injects source-ingest truth for a failed FinMind broker connector
     and verifies `connectorSchedule`, `lastFetchAt`, `lastPushAt`, and
     `failureReason` on the persona panel source row and required-source binding.

2. FinMind payload-push sources report truthful non-static health.
   - TW market persona now declares `tw_broker_top` as `source_class: live_push`
     with FinMind broker connector candidates.
   - The BFF required-source binding selects
     `tw-finmind-broker-daily-report` when source-ingest truth exists and
     reports `source_health_failed` instead of keeping the static FinMind label.

3. Static source labels are visibly not live health.
   - When source-ingest truth is absent, BFF rows carry
     `health_source: static_metadata`, `static_label: true`, and
     `dataSourceStatus.source_health_source: static_metadata`.
   - `live_ingestion_enabled` remains false unless SourceHealth or connector
     registry truth is present.

## Verification

```bash
python3 -m pytest -q services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py
```

Result: `11 passed, 4 warnings in 7.35s`.

```bash
python3 -m pytest -q services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py services/control-plane/bff/test_source_connector_service_client.py services/control-plane/bff/tests/test_bff_management_data_sources_contract.py services/source_ingestion/tests/test_scheduled_connector.py services/source_ingestion/tests/test_usage_health.py
```

Result: `62 passed, 8 warnings in 25.06s`.

Revalidated after rebasing onto `origin/dev`:
`62 passed, 8 warnings in 22.13s`.

```bash
python3 -m pytest -q services/control-plane/bff/smoke_test.py
```

Result: `25 passed, 4 warnings in 7.88s`.

Revalidated after rebasing onto `origin/dev`:
`25 passed, 4 warnings in 10.45s`.

## Maturity Boundary

This task wires operator-visible truth projection into BFF/persona panels. It
does not claim live market-data maturity by itself. Panel maturity remains
bounded by the actual SourceHealth and connector registry records available
from source-ingest. If source-ingest is missing or has no matching connector
truth, the BFF explicitly marks the row as static metadata rather than live
health.
