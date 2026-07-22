# PAN-SOURCE-FRESH-001 — Formalize guarded source refresh and Agora freshness truth

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex2
Reviewer: Codex
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

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
