# Pantheon Current Functional GAP, SA, and SD

This directory is the current code-first and runtime-backed product gap baseline.
The 2026-08-24 set supersedes earlier conclusions, but not their historical evidence.

## Current documents — 2026-08-24

- [`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md) — re-audited source,
  hosted runtime, Lifecycle storage/database, product-journey, delivery, and
  duplicate/dead-code gaps.
- [`SA_IMPLEMENTATION_PLAN_2026-08-24.md`](SA_IMPLEMENTATION_PLAN_2026-08-24.md)
  — corrected target architecture, work-package reuse, dependency graph, maximum
  parallelism, migration/rollback, and acceptance contracts.
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

The current set makes four explicit corrections to the older plan:

- Lifecycle retirement requires PostgreSQL backfill, cutover, restart, and readback,
  but no seven-day soak or retirement-HMAC ceremony in this dev-only program;
- exact FE/BFF identity remains mandatory evidence, while repeated manual
  authorization of every candidate pair is removed;
- read-only smoke tests do not replace the full paper-domain Management/AI and Agora
  write journeys; and
- existing canonical task IDs remain in place, while read-only caller audits start
  early and destructive cleanup waits for replacement proof.
