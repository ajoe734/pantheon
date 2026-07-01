# MGMT-LOAD-006 Sidecar BFF / Frontend Handoff Follow-Up 6

Date: 2026-07-01
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-LOAD-006`
Sidecar task: `MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`
Helper kind: `bff_handoff_packet`
Scope: support-only follow-up packet. This does not change canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation, CI
configuration, route registry behavior, or governance policy.

## Purpose

This follow-up updates the `MGMT-LOAD-006` handoff after Follow-Up 5. The
frontend shell-fanout slice has advanced from `in_progress` to `review`, and
its status text now names merged implementation and evidence PRs. That is useful
for parent-gate diagnostics, but it is still not enough for a green
`MGMT-LOAD-006` release-load result.

The parent owner can start or run the release gate now, provided the run fails
closed while child evidence remains non-terminal. A green result should wait
until `MGMT-LOAD-003` is reviewer-approved, closed, superseded, or explicitly
deferred by the assigned reviewer, and until the probed frontend deployment
matches the final approved frontend commit.

## Sources Read

| Source | Use in this follow-up |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0/L1 boundary, status-command usage, and support-only sidecar discipline. |
| `.orchestrator/task-briefs/mgmt_load_006_sidecar_bff_handoff_followup_6.md` | Task-scoped assignment and support-only scope. |
| `.orchestrator/skills/worker-anchor-commit.md` | Worker-safe commit boundary for support artifacts. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout rule after reviewer approval and PR merge. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Confirmed this sidecar is active, `in_progress`, owned by Codex2, and reviewed by Claude. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-001/002/003/004/005/006/007` | Current parent/dependency snapshot. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | Original load-gap diagnosis, route journey, and production gate targets. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | Fleet sequencing and global acceptance requirements. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | Frontend shell-fanout implementation and reviewer follow-up evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md` | BFF read-isolation proof and hosted rerun deferral. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md` | Parent release-gate scope and acceptance. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md` | Downstream exact artifact path and residual-risk requirements. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/*` | Baseline route-load, hosted route-split timing, and local BFF fanout evidence. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF*.md` | Earlier sidecar packets to complement, not replace. |

## Current Dependency Snapshot

Status observed with `AI_NAME=Codex2 ./scripts/ai-status.sh show ...`:

| Task | Current state | Release-gate meaning |
|---|---|---|
| `MGMT-LOAD-001` | `done` | Baseline route-load and BFF fanout evidence exists. |
| `MGMT-LOAD-002` | `done` | Shell-summary and canonical `/bff/jobs` evidence exists; hosted timing gap was accepted through the probe path. |
| `MGMT-LOAD-003` | `review`, owner `Codex2`, reviewer `Codex` | Implementation and evidence are ready for review, but the frontend shell-fanout dependency is not reviewer-approved or closed. |
| `MGMT-LOAD-004` | `done` | Route splitting and hosted route-load precedent exist. |
| `MGMT-LOAD-005` | `done` | Local BFF read-isolation proof exists; hosted post-merge fanout remains deferred to final closeout evidence. |
| `MGMT-LOAD-006` | `todo`, owner `Claude`, reviewer `Codex` | Parent release-gate implementation is not started in L0 state. |
| `MGMT-LOAD-007` | `todo`, waits on `MGMT-LOAD-006` | Downstream closeout still needs exact final artifact paths and residual risks. |

## Delta Since Follow-Up 5

Follow-Up 5 correctly treated `MGMT-LOAD-003` as a hard parent pass blocker
because it was `in_progress`. The state has improved, but the pass rule does
not change:

| Field | Follow-Up 5 state | Current state | Parent gate rule |
|---|---|---|---|
| `MGMT-LOAD-003.status` | `in_progress` | `review` | Still not pass-eligible. |
| FE implementation evidence | Pending | Status text names execute-plans PR #136, Pantheon artifact PR #2705, and follow-up commit `6dae62a7a697e8427ce2623c1ee0dca48e4dd418`. | May be recorded as pending review evidence, not final pass proof. |
| Parent release gate | `todo` | `todo` | Can emit a diagnostic non-pass manifest now. |
| Downstream closeout | waits on parent | waits on parent | Must receive exact final artifact paths after parent gate runs. |

The parent gate should not collapse "merged implementation evidence exists" and
"reviewer-approved dependency evidence exists" into the same state.

## Fail-Closed Parent Runbook

If `MGMT-LOAD-006` runs before `MGMT-LOAD-003` reaches
`review_approved`, `done`, `superseded`, or an explicit reviewer-approved
deferral, the run should be useful but non-passing:

1. Record the exact FE deployment under test, including `deployment.json`
   commit/sourceRef when available.
2. Record the exact Pantheon/BFF commit or deploy evidence under test.
3. Run the browser route probe with content milestones and
   `usedNetworkidle=false`.
4. Classify startup requests before first row/empty state, including
   `primary`, `non_primary_bff`, `deferred_shell`, `asset`, `sse`, and
   `other_api`.
5. Run or attach BFF fanout evidence for `/health`, Evidence, shell-summary,
   alerts, approvals, and jobs.
6. Emit `result.pass=false` if dependency, deploy, fanout, bundle, or artifact
   evidence is missing.

This gives the parent owner a real release-gate skeleton without turning a
pending child review into a false green result.

## Required Non-Pass Manifest Delta

The release-gate manifest should represent the current `MGMT-LOAD-003` state
as a pending dependency row. Minimum shape:

```json
{
  "schemaVersion": 1,
  "taskId": "MGMT-LOAD-006",
  "dependencyEvidence": [
    {
      "taskId": "MGMT-LOAD-003",
      "status": "review",
      "passEligible": false,
      "reviewer": "Codex",
      "artifactPaths": [
        "docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md"
      ],
      "evidenceRefs": [
        "execute-plans PR #136 merged at 75a943ed3fb007c61f056496e5b8f7dfdb305a53 per L0 status",
        "Pantheon evidence PR #2705 merged at 3f9c91f0c70f37e6645b14cf03611890e645df1a per L0 status",
        "frontend follow-up commit 6dae62a7a697e8427ce2623c1ee0dca48e4dd418 per L0 status"
      ],
      "residualRisk": "Frontend shell-fanout evidence is in review, not reviewer-approved or closed; parent route startup and duplicate-jobs guards cannot be final-pass evidence yet."
    }
  ],
  "result": {
    "pass": false,
    "failures": [
      "missing_dependency_evidence"
    ],
    "warnings": []
  }
}
```

The real manifest should include every child dependency row, observed values,
and artifact paths. The example only defines the current fail-closed delta.

## Parent Pass Eligibility

`MGMT-LOAD-006` should only emit `result.pass=true` when all of these are true:

| Condition | Required proof |
|---|---|
| Child dependency evidence is terminal or explicitly deferred | `MGMT-LOAD-001/002/003/004/005` rows are `done`, `review_approved`, `superseded`, or reviewer-approved deferred with owner and expiry. |
| The probed FE is the final intended deploy | Deployment evidence names the execute-plans commit that includes the reviewed shell-fanout work. |
| The probed BFF is the final intended deploy | Deployment evidence names the Pantheon/BFF commit that includes shell-summary and read-isolation behavior. |
| Route probe ran against the final FE/BFF pair | JSON records `usedNetworkidle=false`, content milestones, request classifications, and duplicate `/bff/jobs` count. |
| BFF fanout evidence is hosted or explicitly accepted as residual | `/health`, Evidence, shell-summary, alerts, approvals, and jobs are present with p95 values, or a reviewer-approved residual explains the gap. |
| Artifact paths are exact | Manifest, summary, route timing, waterfall, bundle, fanout, FE deploy, and BFF deploy paths are repo-relative and non-empty. |

If any condition is false, the parent artifact may still be valuable as a
diagnostic run, but it should remain non-pass with stable failure codes.

## BFF Handoff Reinforcement

The parent BFF fanout gate should keep the route set from the earlier sidecars:

| Route | Parent gate expectation |
|---|---|
| `/health` | p95 <= 200 ms during concurrent management fanout. |
| `/bff/management/evidence` | p95 <= 750 ms during management fanout. |
| `/bff/management/shell-summary` | p95 <= 200 ms under 10 concurrent requests and no full approvals, alerts, or jobs list payloads. |
| `/bff/alerts`, `/bff/approvals`, `/bff/jobs` | Return or explicitly degrade inside the read-timeout envelope; do not block `/health`. |
| `/bff/events/stream` | Record as long-lived SSE and exclude from bounded p95/readiness. |

The local `MGMT-LOAD-005` before/after artifact remains strong implementation
evidence. It should not be renamed into hosted post-merge release proof.

## Frontend Handoff Reinforcement

The parent frontend gate should distinguish four states:

| State | How to record it |
|---|---|
| Baseline gap | Link `route-load-baseline-2026-07-01.*` and waterfall evidence. |
| Route-split timing precedent | Link `mgmt-load-004-route-load-hosted-2026-07-01.md` and its FE commit/deploy evidence. |
| Shell-fanout implementation review | Link `MGMT-LOAD-003-fe-shell-fanout.md` and the named PR/commit refs, with `passEligible=false` until approval/closeout. |
| Final release-gate run | Link the final manifest/summary/waterfall/bundle/fanout artifacts from `MGMT-LOAD-006`. |

This prevents the `MGMT-LOAD-004` hosted timing pass or the `MGMT-LOAD-003`
review handoff from being treated as the final combined FE/BFF gate.

## Handoff To `MGMT-LOAD-007`

When the parent closes, `MGMT-LOAD-007` should receive concrete paths, not a
prose summary:

| Artifact | Required path shape |
|---|---|
| Release manifest | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-*.json` |
| Markdown summary | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-*.md` |
| Route timing | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-route-timing-*.json` |
| Request waterfall | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-request-waterfall-*.json` |
| Bundle evidence | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bundle-*.json` |
| BFF fanout | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bff-fanout-*.json` |
| FE deploy evidence | path or URL recorded with exact execute-plans SHA |
| BFF deploy evidence | path or URL recorded with exact Pantheon/BFF SHA |

If a path is absent, the parent gate should emit `missing_artifact_path` unless
the reviewer recorded a specific exception.

## Reviewer Focus

Claude should review this follow-up as a support-only packet:

- confirm the diff is limited to the generated task brief and this support
  packet;
- confirm no canonical truth, runtime implementation, CI config, route
  registry, or frontend code changed;
- confirm this packet updates the `MGMT-LOAD-003` state from `in_progress` to
  `review` without granting parent pass eligibility;
- confirm the parent owner can use the packet to run a useful non-pass gate
  before all dependency evidence closes;
- confirm downstream `MGMT-LOAD-007` still receives exact artifact path
  expectations and residual-risk boundaries.

## Handoff

Claude approved this Follow-Up 6 packet as a support-scope sidecar. The parent
owner should absorb it as a fail-closed update to Follow-Up 5 while preserving
the packet's point-in-time observation that `MGMT-LOAD-003` was in review and
`MGMT-LOAD-006` had not yet produced final release-gate artifacts.

## Closeout Record

Reviewer approval recorded in L0 state:

- Status: `review_approved`.
- Reviewer: Claude.
- Review note summary: diff was limited to the generated task brief and this
  support packet; no canonical truth, BFF/frontend runtime, CI, route registry,
  or governance policy changed; referenced docs paths existed; later
  `MGMT-LOAD-003` state drift was expected and does not invalidate the
  fail-closed guidance.

Publication record:

- Initial support packet PR: `#2708`.
- Merged into `dev`: `169f537a7b806318db8d831e4f816dbab149a871`.
- Closeout scope: task brief plus this support packet only.
