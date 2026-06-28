# SRCLIVE-001 TW Official Source Activation Runbook

Status: task runbook for live dev source-ingest activation
Task: SRCLIVE-001
Owner: Codex
Reviewer: Claude
Last updated: 2026-06-28

## Scope

Activate the official Taiwan source-ingest connectors that feed the BFF
source-health overlay:

- `tw-twse-tpex-official-market` for both provider keys `twse` and `tpex`
- `tw-mops-official-disclosures` for provider key `mops`

Do not hard-code BFF `read_ok`. The only valid green path is:

1. BFF provider key maps to a source-ingest connector id.
2. `/api/source-ingest/health-usage-snapshot` returns that connector with
   `health.status == "ok"`, non-empty `last_success_at`, and `row_count_last_run > 0`.

## Endpoint Setup

Run these commands from the dev VM, or from a shell that can reach the
source-ingest service port.

```bash
export SOURCE_INGEST_BASE="${SOURCE_INGEST_BASE:-http://127.0.0.1:38097}"
export TRACE_TS="$(date -u +%Y%m%dT%H%M%SZ)"
```

Inside Docker Compose, use:

```bash
export SOURCE_INGEST_BASE="http://source-ingest:8097"
```

## Configure Connectors

```bash
curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/connectors" \
  -H 'Content-Type: application/json' \
  -d '{"connector":{"connector_id":"tw-twse-tpex-official-market","source_type":"market","provider":"TWSE/TPEx","license_scope":"official_reference","auth_type":"none","supported_modes":["batch"],"status":"enabled","metadata":{"source_class":"official_reference","official_reference_truth":true}},"fetch":{"mode":"provider_owned_adapter","adapter":"TaiwanOfficialMarketDatasetAdapter.records_from_payload","adapter_config":{"max_records":1000},"request":{"dataset":"tw_price_daily","venues":["TWSE","TPEx"],"timeout_seconds":20},"max_records":1000}}'
```

```bash
curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/connectors" \
  -H 'Content-Type: application/json' \
  -d '{"connector":{"connector_id":"tw-mops-official-disclosures","source_type":"filing","provider":"MOPS","license_scope":"official_reference","auth_type":"none","supported_modes":["batch"],"status":"enabled","metadata":{"source_class":"official_reference","official_reference_truth":true}},"fetch":{"mode":"provider_owned_adapter","adapter":"MopsSourceIngestAdapter.records_from_payload","adapter_config":{"max_records":100},"request":{"route_id":"t05sr01_1","params":{"count":8,"marketKind":""}},"max_records":100}}'
```

## Trigger One Real Ingest Run

```bash
curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/jobs" \
  -H 'Content-Type: application/json' \
  -d "{\"connector_id\":\"tw-twse-tpex-official-market\",\"trace_id\":\"srclive-001-tw-official-$TRACE_TS\",\"trigger_type\":\"srclive_001_activation\"}"
```

```bash
curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/jobs" \
  -H 'Content-Type: application/json' \
  -d "{\"connector_id\":\"tw-mops-official-disclosures\",\"trace_id\":\"srclive-001-mops-$TRACE_TS\",\"trigger_type\":\"srclive_001_activation\"}"
```

Optional scheduler setup after the manual activation succeeds:

```bash
curl -fsS -X PUT "$SOURCE_INGEST_BASE/api/source-ingest/connectors/tw-twse-tpex-official-market/schedule" \
  -H 'Content-Type: application/json' \
  -d '{"interval_seconds":86400,"enabled":true}'

curl -fsS -X PUT "$SOURCE_INGEST_BASE/api/source-ingest/connectors/tw-mops-official-disclosures/schedule" \
  -H 'Content-Type: application/json' \
  -d '{"interval_seconds":600,"enabled":true}'

curl -fsS -X POST "$SOURCE_INGEST_BASE/api/source-ingest/run-scheduled" \
  -H 'Content-Type: application/json' \
  -d '{"max_concurrency":2}'
```

## Verify Source-Ingest Truth

```bash
curl -fsS "$SOURCE_INGEST_BASE/api/source-ingest/health-usage-snapshot" \
  | jq '.sources[]
      | select(["tw-twse-tpex-official-market","tw-mops-official-disclosures"] | index(.health.source_id))
      | {
          source_id: .health.source_id,
          status: .health.status,
          last_success_at: .health.last_success_at,
          row_count_last_run: .health.row_count_last_run,
          latest_watermark: .health.latest_watermark
        }'
```

Expected:

- `tw-twse-tpex-official-market`: `status` is `ok`, `last_success_at` is non-empty, `row_count_last_run > 0`
- `tw-mops-official-disclosures`: `status` is `ok`, `last_success_at` is non-empty, `row_count_last_run > 0`

## Verify BFF Overlay

The BFF route requires an authorized operator/admin Bearer token.

```bash
export BFF_BASE="${BFF_BASE:-https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io}"
export BFF_TOKEN="<operator-or-admin-token>"

curl -fsS "$BFF_BASE/bff/management/persona-fleet" \
  -H "Authorization: Bearer $BFF_TOKEN" \
  | jq '.. | objects
      | select((.persona_id? // .personaId? // .id?) == "persona-tw-equity")
      | (.dataSourceStatus.provider_statuses? // .data_source_status.provider_statuses?)'
```

Expected provider statuses after source-ingest snapshot is green:

```json
{
  "twse": "read_ok",
  "tpex": "read_ok",
  "mops": "read_ok"
}
```

## VM Dirty Worktree Repair (2026-06-28)

**Blocker**: Deploy run #28319397634 failed because the managed deploy worktree
at `~/pantheon-ci-deploy/dev-root` on `pantheon-lupin-dev` was dirty.
`docker-compose.yml`, `bff/main.py`, and other tracked files had local
modifications that the deploy script (`scripts/deploy_nonprod_vm.sh`) refuses
to overwrite without an explicit override flag.

**Root-cause analysis** (no direct SSH required):

The deploy script (`scripts/deploy_nonprod_vm.sh`) runs `require_clean_checkout`
before every deploy. It first stashes known runtime-state paths
(`.orchestrator/state.json`, `.orchestrator/approval-queue.json`, etc.) but does
NOT auto-stash general tracked files such as `docker-compose.yml` or
`bff/main.py`. If any tracked file is modified outside the known runtime-state
list, the deploy exits with:

```
managed deploy worktree is dirty; refusing deploy without --allow-dirty
```

The `--allow-dirty` path (line 449 in `scripts/deploy_nonprod_vm.sh`) auto-stashes
**all** remaining dirty changes with a labeled stash before checkout:

```bash
git stash push --include-untracked -m "$stash_label"
```

This path is exposed as the `allow_dirty` input to the `Pantheon Nonprod Deploy`
GitHub Actions workflow (`nonprod-deploy.yml`).

**Repair steps (no SSH required, runs via GitHub Actions)**:

1. Open **Actions → Pantheon Nonprod Deploy → Run workflow** on the
   `ajoe734/pantheon` repository.
2. Set the inputs:
   - **environment**: `dev`
   - **component**: `root` (or leave as `auto`)
   - **ref**: leave blank (picks up the latest `publish/v*` auto-deploy tip or
     the current `dev` HEAD), or specify a `publish/v*` branch / commit SHA.
   - **allow_dirty**: ✓ checked (`true`)
3. Run the workflow.

The deploy script will:
- SSH into `pantheon-lupin-dev` (via gcloud IAP tunnel, no port 22 exposure needed)
- In `~/pantheon-ci-deploy/dev-root`: stash all dirty changes including
  `docker-compose.yml` and `bff/main.py` under a timestamped stash label
- Fetch origin, checkout the target SHA cleanly, and bring up the full `dev-root`
  Docker Compose stack
- Run the public BFF smoke check automatically

After a successful deploy the source-ingest service runs at `http://127.0.0.1:38097`
on the VM (not publicly exposed) and is reachable from the Docker Compose
network as `http://source-ingest:8097`.

**Running the activation commands from within the VM**:

Source-ingest port 38097 is not exposed to the internet. Use gcloud SSH to
run the activation curl commands inside the VM:

```bash
gcloud compute ssh pantheon-lupin-dev \
  --zone=asia-east1-b \
  --project=pantheon-benjamin-20260528 \
  --command="bash -l -c '
    export SOURCE_INGEST_BASE=http://127.0.0.1:38097
    export TRACE_TS=\$(date -u +%Y%m%dT%H%M%SZ)

    # 1. Register connectors
    curl -fsS -X POST \"\$SOURCE_INGEST_BASE/api/source-ingest/connectors\" \
      -H \"Content-Type: application/json\" \
      -d \"{\\\"connector\\\":{\\\"connector_id\\\":\\\"tw-twse-tpex-official-market\\\",\\\"source_type\\\":\\\"market\\\",\\\"provider\\\":\\\"TWSE/TPEx\\\",\\\"license_scope\\\":\\\"official_reference\\\",\\\"auth_type\\\":\\\"none\\\",\\\"supported_modes\\\":[\\\"batch\\\"],\\\"status\\\":\\\"enabled\\\",\\\"metadata\\\":{\\\"source_class\\\":\\\"official_reference\\\",\\\"official_reference_truth\\\":true}},\\\"fetch\\\":{\\\"mode\\\":\\\"provider_owned_adapter\\\",\\\"adapter\\\":\\\"TaiwanOfficialMarketDatasetAdapter.records_from_payload\\\",\\\"adapter_config\\\":{\\\"max_records\\\":1000},\\\"request\\\":{\\\"dataset\\\":\\\"tw_price_daily\\\",\\\"venues\\\":[\\\"TWSE\\\",\\\"TPEx\\\"],\\\"timeout_seconds\\\":20},\\\"max_records\\\":1000}}\"

    curl -fsS -X POST \"\$SOURCE_INGEST_BASE/api/source-ingest/connectors\" \
      -H \"Content-Type: application/json\" \
      -d \"{\\\"connector\\\":{\\\"connector_id\\\":\\\"tw-mops-official-disclosures\\\",\\\"source_type\\\":\\\"filing\\\",\\\"provider\\\":\\\"MOPS\\\",\\\"license_scope\\\":\\\"official_reference\\\",\\\"auth_type\\\":\\\"none\\\",\\\"supported_modes\\\":[\\\"batch\\\"],\\\"status\\\":\\\"enabled\\\",\\\"metadata\\\":{\\\"source_class\\\":\\\"official_reference\\\",\\\"official_reference_truth\\\":true}},\\\"fetch\\\":{\\\"mode\\\":\\\"provider_owned_adapter\\\",\\\"adapter\\\":\\\"MopsSourceIngestAdapter.records_from_payload\\\",\\\"adapter_config\\\":{\\\"max_records\\\":100},\\\"request\\\":{\\\"route_id\\\":\\\"t05sr01_1\\\",\\\"params\\\":{\\\"count\\\":8,\\\"marketKind\\\":\\\"\\\"}},\\\"max_records\\\":100}}\"

    # 2. Trigger ingest jobs
    curl -fsS -X POST \"\$SOURCE_INGEST_BASE/api/source-ingest/jobs\" \
      -H \"Content-Type: application/json\" \
      -d \"{\\\"connector_id\\\":\\\"tw-twse-tpex-official-market\\\",\\\"trace_id\\\":\\\"srclive-001-tw-official-\$TRACE_TS\\\",\\\"trigger_type\\\":\\\"srclive_001_activation\\\"}\"

    curl -fsS -X POST \"\$SOURCE_INGEST_BASE/api/source-ingest/jobs\" \
      -H \"Content-Type: application/json\" \
      -d \"{\\\"connector_id\\\":\\\"tw-mops-official-disclosures\\\",\\\"trace_id\\\":\\\"srclive-001-mops-\$TRACE_TS\\\",\\\"trigger_type\\\":\\\"srclive_001_activation\\\"}\"

    # 3. Verify health snapshot
    curl -fsS \"\$SOURCE_INGEST_BASE/api/source-ingest/health-usage-snapshot\" \
      | python3 -c \"
import json, sys
snap = json.load(sys.stdin)
for s in snap.get('"'"'sources'"'"', []):
    h = s.get('"'"'health'"'"', {})
    if h.get('"'"'source_id'"'"') in ('"'"'tw-twse-tpex-official-market'"'"', '"'"'tw-mops-official-disclosures'"'"'):
        print(h.get('"'"'source_id'"'"'), h.get('"'"'status'"'"'), '"'"'rows='"'"'+str(h.get('"'"'row_count_last_run'"'"')))
\"
  '"
```

Or split into individual gcloud ssh commands per step (easier to read output).

## 2026-06-28 Probe Notes

From the worker network:

- `GET https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` returned HTTP 200.
- `GET /bff/management/persona-fleet` without a Bearer token returned HTTP 401 `AUTH_REQUIRED`.
- `GET http://35.201.239.38:38097/health` and
  `GET http://35.201.239.38:38097/api/source-ingest/health-usage-snapshot`
  timed out after 12 seconds — source-ingest port 38097 is not publicly exposed;
  use gcloud SSH or the VM-internal Docker Compose network (`http://source-ingest:8097`).
- `GET /bff/source-ingest/health-usage-snapshot` returned HTTP 404 — BFF does
  not proxy source-ingest directly; use the source-ingest service URL.

Root blocker as of 2026-06-28: the managed deploy worktree on the dev VM is
dirty (see **VM Dirty Worktree Repair** section above). Repair the worktree via
the GitHub Actions `allow_dirty` flag, then run the activation commands above
via gcloud SSH.

Do not mark BFF providers green manually; keep the overlay degraded until
source-ingest health truth is present.
