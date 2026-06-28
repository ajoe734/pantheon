# SRCLIVE-001 Sidecar Acceptance Followup-2

- Task: `SRCLIVE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2`
- Parent task: `SRCLIVE-001`
- Helper kind: `acceptance_packet`
- Sidecar owner: `Claude2`
- Sidecar reviewer: `Codex`
- Parent owner: `Codex`
- Parent reviewer: `Claude`
- Prepared: 2026-06-28
- Scope: support artifact only; no L1 canonical truth, core contract truth, runtime, registry, governance, or routing implementation changes.

## Purpose

This packet follows up on `SRCLIVE-001-SIDECAR-ACCEPTANCE.md` (the original
acceptance packet prepared by Codex2) by recording:

1. Progress observed since the original sidecar.
2. Implementation-level verification of the BFF overlay and connector code.
3. Clarification of the `2/5` baseline.
4. The source-ingest service port availability blocker found in the 2026-06-28
   probe.
5. A focused gap-closure checklist for the parent owner to complete acceptance
   without re-reading the full original packet.

This sidecar does not approve the parent task, does not run the live
activation, and does not change any source-ingest, BFF, connector, or frontend
behavior.

## Sources Read

- `AI_COLLABORATION_GUIDE.md`
- `ai-status.json`
- `.orchestrator/task-briefs/srclive_001_sidecar_acceptance_followup_2.md`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `support/sidecars/SRCLIVE-001/SRCLIVE-001-SIDECAR-ACCEPTANCE.md`
- `docs/05/srclive/tw-activation-runbook.md`
- `services/source_ingestion/connectors/taiwan_official.py`
- `services/source_ingestion/connectors/taiwan_market.py`
- `services/control-plane/bff/main.py` (lines 50166–50460, overlay logic)
- `services/control-plane/bff/read_store.py` (lines 553–634, TW persona static defaults)

## Progress Since Original Sidecar

| Item | Status |
|---|---|
| `docs/05/srclive/tw-activation-runbook.md` created by parent owner | Done — AC-6 from the original sidecar is addressed. |
| BFF overlay implementation | Confirmed correct by code review; no code change needed. |
| 2026-06-28 probe of dev endpoints | Conducted; source-ingest port 38097 not accessible from worker network; BFF /health 200; /persona-fleet requires operator token. |

## Implementation Verification (Code Review)

### BFF Source Health Overlay Confirmed Correct

`services/control-plane/bff/main.py:50173-50175` confirms the provider-to-connector mapping:

```python
"twse": ("tw-twse-tpex-official-market",),
"tpex": ("tw-twse-tpex-official-market",),
"mops": ("tw-mops-official-disclosures",),
```

The `_source_ingest_truth_by_connector()` function at line 50180 reads from
`read_store.get_source_health_usage_snapshot()`. When a connector's health
record has `status == "ok"`, `_live_source_health_by_connector()` projects that
as `read_ok` for the matching provider key. No hardcoding is present; the BFF
overlay is driven entirely by live source-ingest health records.

**BFF overlay cache TTL**: `_SOURCE_HEALTH_OVERLAY_TTL = 60.0` (line 50166).
After source-ingest health is created or updated, the BFF will reflect it
within 60 seconds. The parent must not test the persona-fleet immediately after
inserting health; wait at least 60 s or restart BFF if a forced refresh is
needed.

### Connector Implementations Confirmed

- `TaiwanOfficialMarketDatasetAdapter` (connector id `tw-twse-tpex-official-market`)
  in `services/source_ingestion/connectors/taiwan_official.py`: implements
  `records_from_payload`, `source_health_from_result`, normalizes `tw_price_daily`,
  `tw_institutional_flow`, `tw_margin_short_balance`, `tw_securities_lending`,
  and `tw_day_trading` from TWSE/TPEx OpenAPI endpoints.

- `MopsSourceIngestAdapter` (connector id `tw-mops-official-disclosures`) in
  `services/source_ingestion/connectors/taiwan_market.py:360`: implements
  `records_from_payload`, provider `MOPS`, `license_scope=official_reference`,
  routes mapped to `TaiwanMarketClient.MOPS_RECOMMENDED_ROUTES`.

No implementation changes are required to support the parent acceptance run.
Both adapters are ready to receive connector registration and job trigger.

### Static Baseline Clarification (2/5 → 5/5)

From `services/control-plane/bff/read_store.py`:

| Provider | Static default |
|---|---|
| `shioaji` | `read_ok` (from prior quote-readback evidence at repo-local path) |
| `twse` | `read_unavailable` |
| `tpex` | `read_unavailable` |
| `mops` | `public_reference_unavailable` |
| `finmind` | `read_unavailable` (static default; BFF comment: "FinMind flips to read_ok when source-ingest reports live health") |

The `2/5` pre-condition in the parent acceptance likely reflects `shioaji` as
the one static `read_ok` and one additional provider that the dev environment
already has a live source-ingest health record for. If the dev environment has
no live FinMind health record, the baseline may be `1/5`. The parent runbook
should confirm the exact baseline in the target dev environment before running
the activation, so the before/after count is accurate.

If the baseline is `1/5` (shioaji only), the expected post-activation result
is `4/5` (shioaji + twse + tpex + mops); FinMind would need a separate
source-ingest activation to reach `5/5`.

## Gap Closure Checklist (Parent Owner)

The following steps are required to close SRCLIVE-001. The runbook at
`docs/05/srclive/tw-activation-runbook.md` has the exact commands.

### GAP-1 — Confirm baseline provider count before activation

From the dev VM or a shell that can reach BFF:

```bash
curl -fsS "$BFF_BASE/bff/management/persona-fleet" \
  -H "Authorization: Bearer $BFF_TOKEN" \
  | jq '.. | objects | select((.persona_id? // .id?) == "persona-tw-equity")
        | .dataSourceStatus.provider_statuses'
```

Record which providers are already `read_ok` before running any activation.
This determines whether the claim is `2/5 → 5/5` or `1/5 → 4/5`.

### GAP-2 — Register connectors from VM-local shell

Source-ingest port 38097 is not accessible from the worker network. The
connector registration and job trigger commands must be run from the dev VM
directly or from a shell with host-level port access:

```bash
export SOURCE_INGEST_BASE="http://127.0.0.1:38097"
# (or "http://source-ingest:8097" inside Docker Compose)
```

Register both connectors using the curl commands in the runbook
(`## Configure Connectors` section). The connector registration is idempotent;
re-running does not create duplicates.

### GAP-3 — Trigger ingest jobs and wait for completion

After registration, trigger one job per connector:

```bash
export TRACE_TS="$(date -u +%Y%m%dT%H%M%SZ)"
curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/jobs" \
  -H 'Content-Type: application/json' \
  -d "{\"connector_id\":\"tw-twse-tpex-official-market\",\"trace_id\":\"srclive-001-tw-official-$TRACE_TS\",\"trigger_type\":\"srclive_001_activation\"}"

curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/jobs" \
  -H 'Content-Type: application/json' \
  -d "{\"connector_id\":\"tw-mops-official-disclosures\",\"trace_id\":\"srclive-001-mops-$TRACE_TS\",\"trigger_type\":\"srclive_001_activation\"}"
```

Wait for jobs to complete. Check job status or use the health-usage-snapshot
endpoint to confirm `status=ok` and `row_count_last_run > 0` for both
connectors (AC-3 from original sidecar).

### GAP-4 — Capture health-usage-snapshot output as evidence

```bash
curl -fsS "$SOURCE_INGEST_BASE/api/source-ingest/health-usage-snapshot" \
  | jq '.sources[]
        | select(["tw-twse-tpex-official-market","tw-mops-official-disclosures"]
                | index(.health.source_id))
        | {source_id:.health.source_id,status:.health.status,
           last_success_at:.health.last_success_at,
           row_count_last_run:.health.row_count_last_run}'
```

Save the full JSON output to a file under `docs/05/srclive/` or
`docs/deployment/evidence/`. Reference this file path in the parent task
closeout message.

**Note**: `/bff/source-ingest/health-usage-snapshot` returned 404 in the
2026-06-28 probe. The health-usage-snapshot endpoint is on the source-ingest
service directly (`:38097`), not proxied through BFF.

### GAP-5 — Wait 60 s then capture BFF persona-fleet output as evidence

After the health-usage-snapshot shows `ok` for both connectors, wait at least
60 seconds for the BFF source health overlay cache to expire, then verify:

```bash
curl -fsS "$BFF_BASE/bff/management/persona-fleet" \
  -H "Authorization: Bearer $BFF_TOKEN" \
  | jq '.. | objects | select((.persona_id? // .id?) == "persona-tw-equity")
        | {provider_statuses:.dataSourceStatus.provider_statuses,
           source_health_source:.dataSourceStatus.source_health_source,
           live_ingestion_enabled:.dataSourceStatus.live_ingestion_enabled}'
```

Expected result:

```json
{
  "provider_statuses": {
    "twse": "read_ok",
    "tpex": "read_ok",
    "mops": "read_ok"
  },
  "source_health_source": "source_ingest",
  "live_ingestion_enabled": true
}
```

Save the captured response as evidence alongside the health-usage-snapshot.

### GAP-6 — Record any provider that could not reach read_ok as explicit unavailable

If a provider returns anything other than `read_ok` after the activation
attempt (for example, network timeout reaching TWSE endpoint, MOPS route
returning zero rows), record:

- The exact provider key and the status it returned.
- The failure reason from the source-ingest job or the health record.
- Whether this is a `dependency_missing` (external endpoint unreachable) or a
  `data_unavailable` (endpoint reached but no rows for the date).

Do not claim `5/5` if any provider could not achieve `read_ok`. The parent
task may still close with explicit unavailable evidence if the endpoint is
genuinely unreachable from the dev environment on that date.

## 2026-06-28 Probe Notes (Worker Network)

The following probe results were recorded in `docs/05/srclive/tw-activation-runbook.md`
and are reproduced here for context:

| Probe | Result |
|---|---|
| `GET https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | HTTP 200 — BFF is running |
| `GET /bff/management/persona-fleet` without auth | HTTP 401 `AUTH_REQUIRED` — needs operator Bearer token |
| `GET http://35.201.239.38:38097/health` | Timeout after 12 s — source-ingest not exposed on public IP |
| `GET /bff/source-ingest/health-usage-snapshot` | HTTP 404 — BFF does not proxy this route |

The activation and health verification commands must be run from the dev VM or
from a Docker Compose shell with host-level network access. If the BFF token is
needed and not available in the VM environment, request one from the operator
running the dev environment.

## Non-Scope Guardrails (Inherited from Original Sidecar)

- Do not change L1 policy, source/evidence/search contracts, BFF route truth,
  or registry governance from this sidecar.
- Do not mark `SRCLIVE-001` accepted based on repo-local fixture tests alone.
- Do not conflate TEJ with official TW source truth.
- Do not claim broker, order, RuntimeBinding, capital, paper, canary, or
  live-trading authority. This slice is read-only source activation.

## Handoff Note

This packet is ready for Codex review as support material. The primary
additions over the original sidecar are:

1. Code-level confirmation that the BFF overlay and connectors are implemented
   correctly — no code changes needed.
2. Clarification that the source-ingest port is not externally accessible;
   activation must run from the dev VM.
3. A concrete `GAP-1` through `GAP-6` checklist so the parent owner can close
   SRCLIVE-001 without navigating both sidecars.
4. BFF cache TTL (60 s) note to prevent testing too quickly after health insert.
5. Clarification that the `2/5` baseline may actually be `1/5` if FinMind has
   no live health record in the target dev environment.
