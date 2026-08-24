# Pantheon Current Functional GAP, SA, and SD

This directory is the current code-first and runtime-backed product gap baseline.
The 2026-08-24 set supersedes earlier conclusions, but not their historical evidence.

## Current documents — 2026-08-24

- [`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md) — re-audited source,
  hosted runtime, Lifecycle storage/database, product-journey, delivery, and
  duplicate/dead-code gaps.
- [`SA_IMPLEMENTATION_PLAN_2026-08-24.md`](SA_IMPLEMENTATION_PLAN_2026-08-24.md)
  — corrected target architecture, immutable-task composition, dependency waves,
  exclusive ownership, migration/rollback, and acceptance contracts.
- [`SD_IMPLEMENTATION_DESIGN_2026-08-24.md`](SD_IMPLEMENTATION_DESIGN_2026-08-24.md)
  — file-level design for the relational Lifecycle cutover, automatic exact-pair
  proof, full Management/AI and Agora journeys, consolidation, tests, and hosted
  closure.

## Historical documents — 2026-08-22

- [`CURRENT_GAP_2026-08-22.md`](CURRENT_GAP_2026-08-22.md) — current product,
  runtime, delivery, test, and code-disposition gaps as observed on 2026-08-22.
- [`SA_IMPLEMENTATION_PLAN_2026-08-22.md`](SA_IMPLEMENTATION_PLAN_2026-08-22.md)
  — the 2026-08-22 architecture and task plan.
- [`SD_IMPLEMENTATION_DESIGN_2026-08-22.md`](SD_IMPLEMENTATION_DESIGN_2026-08-22.md)
  — the 2026-08-22 detailed design.

## Scope

The baseline covers:

- Source Ingestion with dev provider egress kept manual and bounded;
- all twelve product loops and the loop-truth projection;
- Lifecycle projection and non-production deployment data handling;
- paper RuntimeBinding, market snapshot, and execution-session behavior;
- Agora and Management/Management AI product journeys;
- hosted frontend/backend release identity; and
- duplicate, compatibility, fixture, and dead-code disposition.

Supervisor, auto-worker, V2 TaskStore, provider quota, and review-attestation
mechanics are recorded only where they affect delivery state. They are not
treated as product functionality.

## 2026-08-24 corrections

The current set makes five explicit corrections to the older plan:

- Lifecycle retirement requires PostgreSQL backfill, cutover, restart, and readback,
  but no seven-day soak or retirement-HMAC ceremony in this dev-only program;
- exact FE/BFF identity remains mandatory evidence, while repeated manual
  authorization of every candidate pair is removed;
- read-only smoke tests do not replace the full paper-domain Management/AI and Agora
  write journeys; and
- the seven pre-existing task specs and histories remain immutable; three corrective
  roots compose with them rather than replacing or superseding them; and
- two artifact-only caller-inventory sidecars start early, while their existing
  consolidation parents retain all product-change and deletion authority.

## Corrected task graph

All five materialized nodes depend on
`PFG-FUNCTIONAL-REAUDIT-DOCS-20260824`:

- `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824` — PostgreSQL projector/store and BFF
  relational core only; no Compose/deploy or legacy deletion;
- `PFG-CANDIDATE-AUTO-BINDING-20260824` — Pantheon candidate/controller output
  contract only; no `execute-plans` product or proof workflow edits;
- `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824` — existing hosted OpenClaw provider
  path only; no BFF main, Compose/deploy, or frontend edits;
- `PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY` — Pantheon BE caller
  inventory artifact only; and
- `PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY` — `execute-plans` FE caller
  inventory artifact only.

The old Lifecycle retirement and bounded-proof tasks remain blocked until their
corresponding roots land. A governed audited operator correction may then clear the
stale seven-day/HMAC, old-pair, and per-pair authorization status blockers without
rewriting the immutable original task payloads. The five nodes are not replacements,
and none supersedes the seven-task history.
