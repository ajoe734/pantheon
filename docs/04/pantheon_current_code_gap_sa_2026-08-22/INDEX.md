# Pantheon Current Code GAP and SA Plan — 2026-08-22

This directory is the current code-first and runtime-backed product gap baseline.
It supersedes the conclusions, but not the historical evidence, of the 2026-08-18
and 2026-08-20 gap documents.

## Documents

- [`CURRENT_GAP_2026-08-22.md`](CURRENT_GAP_2026-08-22.md) — current product,
  runtime, delivery, test, and code-disposition gaps.
- [`SA_IMPLEMENTATION_PLAN_2026-08-22.md`](SA_IMPLEMENTATION_PLAN_2026-08-22.md)
  — target architecture, migration sequence, implementable work packages,
  dependency graph, and acceptance contracts.
- [`SD_IMPLEMENTATION_DESIGN_2026-08-22.md`](SD_IMPLEMENTATION_DESIGN_2026-08-22.md)
  — file-level detailed design, data and state contracts, migration mechanics,
  positive/negative tests, and worker-ready task boundaries.

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
