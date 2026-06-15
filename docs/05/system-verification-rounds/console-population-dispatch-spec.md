# Console population — per-surface work order (fleet-dispatchable)

**Date:** 2026-06-15
**Context:** After the FE fix (E2E-R21) the operator console loads, but most
left-half pages are empty. Root cause (proven end-to-end): the BFF read-model is
**disconnected per-surface** from the live services — producing real data in a
backend service does NOT uniformly reach its console page; each surface has its
own read-path quirk (wired / unwired-store-env / transformed / cached).

This is therefore **bespoke per-surface work**, best parallelized across the
fleet. This is the work order.

## Proven pattern (the template each task follows)

1. **Produce real data** via the domain's real producer (no fabrication). E.g.
   research: `POST /api/research-orchestrator/tasks` → `/runs` → `/runs/{id}/artifacts`
   → `/complete` → `/registry-writeback` (stub dispatch = dev safety posture).
2. **Reconcile the BFF read-path**: either wire the surface's
   `PANTHEON_BFF_*_STORE` env to a store the BFF reads, point it at the live
   service, or add a projection from the service into that store.
3. **Verify** the `/bff/...` surface returns the real records (count>0, surface ok).

Reference implementation shipped: `scripts/project_research_to_bff_surfaces.py`
+ docker-compose store-env wiring (PR for the research slice). Strategies &
Artifacts pages now render real data.

## Status

- **DONE (real data on page):** strategies, artifacts (research slice, PR merged).
- **Real data produced, BFF read-path needs reconciliation:** evolution
  (`evo-vslice-1` proposal created in the evolution service, but
  `/bff/evolution-programs` still 0 — proposals→programs mapping is bespoke).
- **48 operational surfaces** already render real fleet-derived data (no work).

## Per-surface tasks (each = one fleet task: produce → reconcile read-path → verify)

| Domain / surface | Producer (real) | Read-path gap |
|---|---|---|
| evolution-programs | evolution svc `POST /api/evolution/proposals` (✓ data exists) | BFF proposals→programs mapping / cache |
| approvals | promotion svc `POST /api/v1/approvals` | BFF `PANTHEON_GOVERNANCE_APPROVAL_API_URL` wiring |
| incidents / reviews | incidents svc / review flow | well-formed records + read wiring |
| skills / tools / mcp-servers / mcp-tools | registry create APIs | `PANTHEON_BFF_*_STORE` unset |
| agora/* (20 surfaces) | consultation-svc / agora sessions | source unavailable — needs producers + wiring |
| knowledge / research-analyses / research/tasks | research + memory svcs | store/service wiring |
| rankings / ranking-formulas / quarterly | ranking producers | source unavailable |
| ooda/packets | OODA loop producers | `PANTHEON_BFF_OODA_PACKET_STORE` unset |
| route-policies / workflows / hooks / jobs | respective svcs | source unavailable |

(Full machine-readable list: the census in the campaign notes — 50 `source:
unavailable` + 32 `empty-but-source-ok` surfaces.)

## Dispatch

Each row → one fleet task following the proven pattern. Owner order per house
rule: Claude → Claude2 → Codex. These are independent (parallelizable). The
research slice PR is the worked example to clone.
