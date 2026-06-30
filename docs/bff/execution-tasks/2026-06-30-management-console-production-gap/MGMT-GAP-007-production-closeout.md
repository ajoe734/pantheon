# MGMT-GAP-007 - Production-Level Closeout And Archive Proof

Owner: Codex
Reviewer: Claude
Batch: 5
Fleet lane: oversight, archive, and production gate tracking
Depends on: `MGMT-GAP-006`

## Problem

The user explicitly asked for tight tracking until every gap is completed and
production-level. A task packet alone is not sufficient; final proof must be
archived and tied to merged PRs and deployed commits.

## Scope

- Track all `MGMT-GAP-*` tasks to terminal status.
- Verify every task has reviewer approval and merge evidence.
- Verify the final FE deployment reports the intended merged commit.
- Verify BFF health/OpenAPI endpoint evidence.
- Archive final probe reports, screenshots or logs, route manifest, and residual
  risks.
- Publish a closeout note that states exactly what is complete, what was
  superseded, and what remains blocked if anything remains.

## Non-Scope

- Do not mark the gap complete based only on local tests or unmerged branches.

## Acceptance

- All `MGMT-GAP-*` tasks are `done` or reviewed superseded.
- The final archive contains FE deployment evidence, BFF evidence, hosted probe
  evidence, and residual risk owners/expiry.
- The closeout references PR numbers and merge SHAs.
- The management console is either production-level by the spec, or the blocker
  is explicit and owned.
