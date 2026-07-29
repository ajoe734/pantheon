# L12 Manifest Review Reopen Gap — Three-Pass Audit

Observation time: `2026-07-29T02:25:44Z`

Repository base: `origin/dev = 6fe626252d10af27eed0aba79530506d192857ca`

Active task: `L12-MANIFEST-001`

Open PR: `#4329`

Rejected exact head:
`114bfce34dbe1d6abf48c9d7759bd2e7bff5aa87`

Canonical review gate status:
`Pantheon canonical review gate = failure`

This audit supersedes the earlier `2026-07-29T01:00:00Z` dispatch assumption
that the manifest work only needed review/root gate closure. The current truth
is harder: the runtime manifest PR now passes structural validators and branch
checks, but the exact-head reviewer correctly rejected substantive acceptance
criteria for worker health, heartbeat, auth, durability, and restart proof.

## Boundaries

- Do not edit `.orchestrator/config.json`.
- Do not use Codex conversation subagents as fleet work.
- Dispatch means real supervisor/auto-worker work or assistant dev-bridge
  packet drain.
- Prefer Antigravity and Claude-family lanes when they are actually healthy;
  if they fail closed, record the provider failure and use a healthy real
  worker instead of stalling the program.
- Because `L12-MANIFEST-001` owns `docker-compose.yml`,
  `scripts/deploy_nonprod_vm.sh`, and its evidence directory, new parallel
  workstreams must not become competing canonical tasks that mutate the same
  guarded artifacts independently. They are workstreams under the manifest
  repair, with one final owner integration and one exact-head review.

## Current Reviewer Rejection

The reviewer rejected #4329 head
`114bfce34dbe1d6abf48c9d7759bd2e7bff5aa87` for these exact acceptance gaps:

1. Bare Compose independently renders only `20/27` healthchecks.
2. Seven required worker services still lack health or heartbeat proof:
   - `alpha-replication-worker`
   - `policy-learning-shadow-eval-scheduler`
   - `paper-signal-producer`
   - `reconciliation-drift-consumer`
   - `reconciliation-drift-scheduler`
   - `reconciliation-drift-incident-listener`
   - `search-index-scheduler`
3. The evidence does not provide a per-worker auth and durable-volume
   applicability matrix.
4. The manifest readback inventory has no auth column.
5. The manifest readback shows seven zero-volume services without adjudicating
   whether that is acceptable.
6. The required kill-one worker restart proof is still missing: live inspect
   shows `RestartCount=0` and `ExitCode=0`, so daemon auto-restart was not
   proven.
7. PR/readback wording is stale: the PR still describes older v1.0.2
   Claude2/Antigravity context, and readback says only two files move even
   though `evidence.sha256` also moves.

Passing checks do not override these gaps. The reviewer also reran the relevant
local validators successfully, so this is not a CI omission; it is a product
acceptance omission.

## Pass 1 — Development Gaps by Runtime Surface

| Surface | Current state | Missing development |
| --- | --- | --- |
| Worker healthchecks | Compose renders `20/27` healthchecks | Add healthcheck or governed non-applicability decision for the seven missing services |
| Worker heartbeat | Seven services have no independent heartbeat proof | Add service-level heartbeat/readiness path, metrics, file, or explicit waiver tied to each worker contract |
| Durable volumes | Readback lists seven zero-volume services | Add required durable volumes where state must survive restart, or document why the worker is stateless/idempotent |
| Auth/applicability | Inventory has no auth column | Add per-worker auth/readback matrix and validator support where needed |
| Graceful stop | Claimed as part of AC2 | Preserve stop timeout and signal behavior for every required worker while adding health/heartbeat |
| Restart proof | Evidence records operator recovery, not daemon restart | Run isolated PID1 crash proof showing `RestartCount` increment, or obtain explicit governed waiver |
| Evidence wording | PR/readback stale | Update PR title/body/readback/version/file-movement wording after real repair |

## Pass 2 — Missing Tests and Validation

The following validation was done and passed on #4329 head
`114bfce34dbe1d6abf48c9d7759bd2e7bff5aa87`:

- evidence schema/checksum/json/diff checks
- `scripts/test_validate_twelve_loop_gap_evidence.py`
- activation contract tests
- deploy contract tests
- root merge-freeze status on exact head

The following validation is still missing:

1. A Compose health inventory asserting either `27/27` health/heartbeat
   coverage or an explicit governed waiver per exception.
2. Per-worker matrix validation for:
   - healthcheck
   - heartbeat
   - restart policy
   - stop timeout
   - durable volume applicability
   - auth applicability
   - safe write/no-live-capital state
3. Direct evidence for each of the seven missing workers after repair.
4. Isolated/non-shared worker crash proof where `RestartCount` increases.
5. Evidence checksum regeneration after all real files move.
6. Exact-head reviewer rerun after the above evidence exists.

## Pass 3 — Parallel Workstream Dispatch

The repair should be split into parallel workstreams, but final integration
must remain under `L12-MANIFEST-001` to respect the artifact guard.

Immediate workstreams:

1. `L12-MANIFEST-HC-ALPHA-SRC-20260729`
   - services: `alpha-replication-worker`, `search-index-scheduler`
   - output: health/heartbeat patch plan and tests for alpha/source workers
2. `L12-MANIFEST-HC-IMIT-CAP-20260729`
   - services: `policy-learning-shadow-eval-scheduler`,
     `paper-signal-producer`
   - output: health/heartbeat patch plan and tests for learning/capital workers
3. `L12-MANIFEST-HC-REC-20260729`
   - services: `reconciliation-drift-consumer`,
     `reconciliation-drift-scheduler`,
     `reconciliation-drift-incident-listener`
   - output: health/heartbeat patch plan and tests for reconciliation workers
4. `L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729`
   - output: per-worker auth/durability applicability matrix and validator
     changes
5. `L12-MANIFEST-RESTART-PROOF-20260729`
   - output: isolated restart proof or explicit governed waiver packet
6. `L12-MANIFEST-CLOSEOUT-ALIGN-20260729`
   - output: final evidence/readback/PR wording alignment after all prior
     streams land
7. `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`
   - output: supervisor/auto-worker reliability readback for Antigravity,
     Claude2, Codex, and Codex2 lanes without config edits

## Consolidated Answer: What Was Missing

The previous repair rounds did real work, but they did not make all twelve
loops operational because they stopped at structural and partial evidence:

- Compose existed, but did not prove every required worker had health and
  heartbeat.
- Evidence existed, but did not prove auth and durable-volume applicability per
  worker.
- Restart configuration existed, but no actual daemon auto-restart was proven.
- Validators passed schema and formatting, but did not enforce the substantive
  seven-worker runtime gap strongly enough until review.
- Fleet dispatch existed, but provider failures and stale workers were not
  closed into an operational reliability proof.
- PR wording and evidence wording drifted from the actual evidence.

The correct next action is therefore not to claim done, and not to re-run the
same stale closeout. The correct next action is to dispatch the above real
workstreams, integrate their outputs into `L12-MANIFEST-001`, rerun validators,
post a new exact-head review, merge, archive, and only then unblock
`L12-TRUTH-001`.
