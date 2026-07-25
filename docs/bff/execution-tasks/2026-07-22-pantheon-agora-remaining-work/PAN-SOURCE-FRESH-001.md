# PAN-SOURCE-FRESH-001 — Formalize guarded source refresh and Agora freshness truth

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Antigravity
Reviewer: Codex2
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

Runtime reassignment: the governed task row reassigned ownership to Codex on
2026-07-22 after the Antigravity quota terminal. The packet's original owner
line is retained as dispatch history; `ai-status.json` remains lifecycle truth.

## Objective

Turn the current live-only deny-all egress rescue into versioned policy and
restore bounded, allowlisted source refresh with observable freshness for
Agora market/daily projections.

## Current evidence

- Live source-ingest has `PANTHEON_EXTERNAL_EGRESS=deny` and an empty allowed
  host list.
- The source-ingest API is healthy and serves persisted records.
- No source-ingest scheduler container is running.
- The shared checkout contains uncommitted connector/compose egress-guard work;
  it is temporary live repair, not repository delivery.

## Owned scope

- shared external-egress policy boundary and connector adoption
- source-ingest scheduler compose/profile/configuration
- freshness state/readiness and Agora market projection metadata
- tests, deploy runbook, and task-scoped dev proof

## Required work

1. Read and reconcile the live emergency diff without committing unrelated
   shared-checkout state or secrets.
2. Enforce deny by default for all connectors and an explicit per-environment
   HTTPS host allowlist with redirect/DNS/IP revalidation.
3. Enable a bounded dev scheduler with concurrency, retry/backoff, record cap,
   and evidence receipt. Secrets remain in Secret Manager/files, not config.
4. Expose last success, source timestamp, age, stale threshold, next run, and
   last typed failure to Agora surfaces.
5. Fail closed on unapproved host, private/loopback target, redirect escape,
   missing credentials, or stale data.

## Acceptance

- Focused SSRF/redirect/DNS/allowlist tests pass for every production connector.
- Live compose defaults remain deny-by-default; only the bounded scheduler gets
  the reviewed dev allowlist.
- One approved source run creates a receipt and advances a real Agora market or
  daily projection with provenance/as-of metadata.
- A blocked-host test performs no outbound request and records the typed denial.
- Stale persisted data remains readable only with an explicit stale status.
- PR merges, deploys, and archives safe live evidence without credential values.

## Exclusions

- No broad Internet egress.
- No production credential rotation or source purchase.
- No silent synthetic refresh when a source is unavailable.

## Delivered implementation contract

The task branch formalizes the emergency repair with these fail-closed
boundaries:

- `services.external_egress.open_external_url` is the only third-party
  connector transport. Every environment defaults to `deny`; `allowlist`
  accepts exact HTTPS host names only.
- The guard rejects inline credentials, non-443 ports, IP literals,
  single-label/internal hosts, non-global DNS answers, mixed public/private
  answers, redirect escapes, and excessive redirects. The TCP connection is
  pinned to the last revalidated global IP while TLS SNI and certificate
  validation retain the approved hostname. Same-origin redirects preserve
  request credentials; cross-origin redirects strip authorization, cookie,
  API-key, and token headers before constructing the redirected request.
- Internal compose smoke feeds use an explicit `internal_service` scope and a
  separate redirect guard. They do not weaken the external connector policy.
- Every terminal source run persists a `source_ingest_receipt.v1` receipt with
  counts, watermark, evidence/storage refs, source timestamp, and a secret-free
  typed failure when applicable.
- Connector freshness v2 exposes last success, source timestamp, source age,
  stale threshold, next run, and last typed failure. Stale persisted records
  remain readable, while readiness and Agora watchlist/signal rows explicitly
  report `stale`. Only explicit provider timestamps count as source time;
  missing, invalid, and materially future values never inherit ingest time and
  carry explicit source-time status.
- The `source-ingest-scheduler` profile is one-shot by default, never restarts,
  caps ticks/concurrency/records at deploy preflight, exclusively selects and
  forces the declared connector across both schedule enqueue and existing
  frontier claims, and gates the Agora projector on a successful controller exit.
  The deploy waits for both one-shot containers, requires zero exit codes, and
  verifies a new receipt/controller/Agora projection correlation before it can
  continue to unrelated root-stack checks.

## Bounded dev refresh runbook

The normal root deployment excludes `source-ingest-scheduler` and forces empty
allowlist/deny mode. An operator or governed deployment workflow may opt into
one bounded run by setting all of the following before invoking the existing
non-prod deploy command:

```sh
export PANTHEON_DEV_COMPOSE_PROFILES=source-ingest-scheduler
export PANTHEON_EXTERNAL_EGRESS=allowlist
export PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=openapi.twse.com.tw,www.tpex.org.tw
export SOURCE_INGEST_BOUNDED_CONNECTOR_ID=tw-twse-tpex-official-market
export SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=1800
export SOURCE_INGEST_CONTROLLER_MAX_TICKS=1
export SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1
export SOURCE_INGEST_MAX_RECORDS=100
bash scripts/deploy_nonprod_vm.sh \
  --environment dev \
  --component root \
  --sha <full-merged-dev-sha>
```

The host list covers both venues fetched by the official TWSE/TPEx connector;
it is not a default. The deploy preflight rejects this connector when either
exact host is absent. Add every redirect host explicitly after review. Do not
add wildcard domains, IP literals, credentials, tokens, or signed query
strings. Provider secrets continue to come from the existing secret
file/manager paths.

The deploy itself waits for the scheduler and projector to exit zero, then
requires a receipt created after deployment start, matching controller
readback, and an Agora watchlist row with the same run/source IDs and explicit
source-time/freshness metadata. After the run, archive only secret-free
readback:

```sh
docker compose -p pantheon -f docker-compose.yml ps -a \
  source-ingest-scheduler source-ingest-agora-projector
curl -fsS http://127.0.0.1:18097/api/source-ingest/receipts?connector_id=tw-twse-tpex-official-market
curl -fsS http://127.0.0.1:18097/api/source-ingest/controller/readback
curl -fsS http://127.0.0.1:18097/readyz
```

Acceptance evidence must bind the merged/deployed SHA, scheduler and projector
exit code zero, the new receipt/run/source IDs, an advanced real Agora row with
matching `ingestRunId` and `sourceId`, explicit freshness metadata, and a
blocked-host denial proving no transport was constructed. The hosted proof is
recorded during owner finalization after independent review and merge; local
fixtures or an unmerged branch are not hosted acceptance.

## Local verification for review

- `python -m compileall` passed for the egress module, source-ingestion
  package, research adapters, projector, and bounded smoke scripts.
- `bash -n scripts/deploy_nonprod_vm.sh` passed.
- `docker compose -f docker-compose.yml config --quiet` passed for both the
  default profile set and an explicit bounded `source-ingest-scheduler`
  profile with one exact host, one tick, concurrency one, and record cap 100.
- The final task-focused suite passed with `779 passed, 2 skipped`; it added
  adversarial redirect credential, final receipt append/restart recovery,
  missing/invalid/future source-time, exclusive schedule/frontier selection,
  unavailable selected-connector failure, and one-shot zero/non-zero
  exit/readback gate coverage. It also covered
  external egress, all source-ingestion tests, research adapter tests, Agora
  projection tests, and the deploy contract. Skips were pre-existing optional
  live/provider paths, and only deprecation warnings were emitted.

## Owner closeout verification

After Codex2 approved the final remediation, the owner merged current
`origin/dev` commit `9f3e9ac8d5d1ebc114b0f0dcde6c57442836af8f` into the task branch without
conflicts and repeated the independent review gate. The following checks passed
on the integrated branch:

- the six-file focused pytest command recorded in the reviewer evidence:
  `127 passed, 1 warning`;
- `python -m compileall -q` for the source-ingestion main, scheduler,
  scheduler-worker, and controller-worker modules;
- `bash -n scripts/deploy_nonprod_vm.sh`;
- default and bounded `source-ingest-scheduler` Compose configuration checks,
  with the exact TWSE/TPEx allowlist, connector selection, one tick,
  concurrency one, and record cap 100; and
- `git diff --check`.

This owner closeout validates the merged-code candidate only. It did not make
an external request, use provider credentials, deploy the branch, or alter
production/live-capital state. Replacement-VM source/projection readback and
the accepted hosted manifest remain the explicitly downstream
`AG-HOSTED-CLOSE-001` evidence gate.
