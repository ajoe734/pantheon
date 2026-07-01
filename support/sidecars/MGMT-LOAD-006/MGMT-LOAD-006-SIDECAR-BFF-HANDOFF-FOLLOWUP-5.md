# MGMT-LOAD-006 Sidecar BFF / Frontend Handoff Follow-Up 5

Date: 2026-07-01
Owner: Codex
Reviewer: Claude
Parent task: `MGMT-LOAD-006`
Sidecar task: `MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`
Helper kind: `bff_handoff_packet`
Scope: support-only follow-up packet. This does not change canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation, CI
configuration, route registry behavior, or governance policy.

## Purpose

This follow-up updates the `MGMT-LOAD-006` support handoff after Follow-Up 4.
The dependency state has moved from "review caveat" to "hard release-pass
precondition":

- `MGMT-LOAD-003` is currently `in_progress`, not `review`, not
  `review_approved`, and not `done`.
- `MGMT-LOAD-006` is still `todo`.
- The existing archive contains baseline route-load evidence, route-split
  hosted timing, and local BFF read-isolation evidence, but not a final
  combined release-load gate artifact.

The parent owner may still implement the release-gate skeleton now. The parent
must not publish a green release-gate result until the frontend shell-fanout
slice is reviewer-approved or explicitly superseded/deferred by the reviewer,
and until the release manifest names the exact final FE/BFF artifact paths.

## Sources Read

| Source | Use in this follow-up |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0/L1 boundary, status-command usage, and sidecar support discipline. |
| `.orchestrator/task-briefs/mgmt_load_006_sidecar_bff_handoff_followup_5.md` | Task-scoped assignment and support-only scope. |
| `.orchestrator/skills/worker-anchor-commit.md` | Worker-safe commit and anchor boundary for support artifacts. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout rule after reviewer approval and PR merge. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirmed this sidecar is active, owner `Codex`, reviewer `Claude`, status `in_progress`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001/002/003/004/005/006/007` | Current dependency and parent/closeout status snapshot. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | Original route-load root cause, operator journey, and release-gate target behavior. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | Fleet sequencing and global acceptance requirements. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md` | Baseline readiness and BFF fanout probe contract. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | Frontend shell-fanout implementation evidence and remaining hosted-probe boundary. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md` | Parent release-gate acceptance. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF*.md` | Earlier sidecar packets to complement, not replace. |

## Current Dependency Snapshot

Status observed with `AI_NAME=Codex ./scripts/ai-status.sh show ...`:

| Task | Current state | Release-gate meaning |
|---|---|---|
| `MGMT-LOAD-001` | `done` | Baseline route timing, request waterfall, and BFF fanout evidence exist. |
| `MGMT-LOAD-002` | `done` | `/bff/management/shell-summary` and single canonical `/bff/jobs` are accepted. Hosted timing gap was accepted through the later probe path. |
| `MGMT-LOAD-003` | `in_progress`, owner `Codex2`, reviewer `Codex` | The frontend shell-fanout guard is not review-approved. Parent gate cannot claim the final startup request count or duplicate-jobs guard as passing. |
| `MGMT-LOAD-004` | `done` | Route splitting and hosted route-load precedent exist. Treat this as timing precedent, not final combined release proof. |
| `MGMT-LOAD-005` | `done` | Local BFF read-isolation proof exists. Hosted post-merge fanout remains a release/closeout evidence item. |
| `MGMT-LOAD-006` | `todo`, owner `Claude`, reviewer `Codex` | Parent release-gate implementation has not started in L0 state. |
| `MGMT-LOAD-007` | `todo`, waits on `MGMT-LOAD-006` | Downstream closeout still needs exact final artifact paths and residual risks. |

## Parent Gate Pass Eligibility

`MGMT-LOAD-006` should separate "gate can run" from "gate can pass".

| Condition | Gate may run? | Gate may pass? | Required result if unmet |
|---|---:|---:|---|
| `MGMT-LOAD-003` reviewer-approved, done, superseded, or explicitly deferred | Yes | No | Emit `result.pass=false` with `missing_dependency_evidence`. |
| Final FE deploy commit includes the shell-fanout work under test | Yes | No | Emit the FE commit/deploy as missing or mismatched dependency evidence. |
| Startup waterfall classification ran against the final deployed FE | Yes | No | Emit missing classification and do not infer zero early BFF reads. |
| Hosted or reviewer-accepted BFF fanout artifact exists | Yes | No without explicit exception | Emit missing fanout evidence or a bounded reviewer-approved residual. |
| Manifest and Markdown artifact paths are exact repo-relative paths | Yes | No | Emit `missing_artifact_path`. |

This lets the parent owner build the mechanics before every dependency closes,
while preventing a pending dependency from becoming a false release pass.

## Required Non-Pass Manifest For Early Runs

If `MGMT-LOAD-006` runs before `MGMT-LOAD-003` is approved or closed, the JSON
manifest should make that state machine-readable:

```json
{
  "schemaVersion": 1,
  "taskId": "MGMT-LOAD-006",
  "dependencyEvidence": [
    {
      "taskId": "MGMT-LOAD-003",
      "status": "in_progress",
      "passEligible": false,
      "artifactPaths": [
        "docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md"
      ],
      "residualRisk": "Frontend shell-fanout slice is not reviewer-approved or closed; startup request-count and duplicate-jobs guards cannot be final-pass evidence."
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

The real manifest should include all dependency rows, observed route/load
values, and exact artifact paths. This snippet only defines the minimum
non-pass behavior for the current dependency state.

## BFF Handoff Delta

The BFF side of the release gate remains the same route family defined in the
earlier sidecars. The current delta is pass eligibility:

| BFF surface | Current handoff rule |
|---|---|
| `/health` | Must be measured during concurrent management fanout; p95 <= 200 ms for pass. |
| `/bff/management/evidence` | Must be measured during fanout; p95 <= 750 ms for pass. |
| `/bff/management/shell-summary` | Must be measured under 10 concurrent requests; p95 <= 200 ms and no full approvals/alerts/jobs lists in payload. |
| `/bff/alerts`, `/bff/approvals`, `/bff/jobs` | Must return or explicitly degrade inside the bounded timeout envelope and must not block `/health`. |
| `/bff/events/stream` | Record separately as long-lived SSE; exclude from readiness and bounded p95. |

If hosted post-merge fanout has not been run, the parent artifact should stay
non-pass unless the reviewer records a specific exception with owner and
expiry. The local `MGMT-LOAD-005` before/after proof is task evidence, not final
hosted release proof by itself.

## Frontend Handoff Delta

The frontend side now has a sharper dependency guard:

| Frontend surface | Required before parent pass |
|---|---|
| Shell-summary consumption | The probed FE commit must include `TopBar` using `/bff/management/shell-summary`, not full list fanout for badges. |
| Unavailable/degraded summary | Fallback full-list reads must be deferred after primary content and surfaced as honest stale/degraded UI. |
| Jobs drawer hydration | The probed FE must not issue duplicate bounded `/bff/jobs` before first row or empty state. |
| Route probe | Content milestones must drive readiness; `usedNetworkidle=false` is required. |
| Request classifier | The waterfall must classify `primary`, `non_primary_bff`, `deferred_shell`, `asset`, `sse`, and `other_api`. |

Because `MGMT-LOAD-003` is currently `in_progress`, a parent gate run against
the current deployed FE can be useful for diagnostics, but it cannot prove the
final shell-fanout acceptance.

## Do Not Treat These As A Green Parent Gate

| Evidence | Why it is insufficient |
|---|---|
| `MGMT-LOAD-004` hosted route-load p75/p95 | It predates final reviewed shell-fanout closure and does not prove startup fanout stayed out of the critical path. |
| `MGMT-LOAD-003` implementation notes without reviewer approval | The active L0 task state is still `in_progress`; the parent gate must wait for review/closeout or explicit supersession. |
| Local BFF read-isolation proof from `MGMT-LOAD-005` | It proves local behavior, not hosted post-merge fanout. |
| A manifest with empty dependency rows | Empty rows hide whether child fixes are merged, deployed, or deferred. |
| Zero duplicate `/bff/jobs` due to missing classifier data | Missing waterfall classification is missing evidence, not a pass. |

## Handoff To Parent Owner

Recommended parent-owner sequence:

1. Implement the release-gate manifest and failure-code plumbing first.
2. Make unresolved dependency rows fail closed with `missing_dependency_evidence`.
3. Wire the browser route probe and BFF fanout probe into the manifest.
4. After `MGMT-LOAD-003` is review-approved or closed, rerun against the final
   deployed FE/BFF combination.
5. Hand `MGMT-LOAD-007` exact paths for the manifest JSON, summary Markdown,
   route timing, request waterfall, bundle artifact, BFF fanout artifact, FE
   deploy evidence, BFF deploy evidence, and residual-risk table.

## Reviewer Focus

Claude should review this follow-up as a support-only packet:

- confirm the diff is limited to the task brief and this support packet;
- confirm no canonical truth, runtime implementation, CI config, route
  registry, or frontend code changed;
- confirm the `MGMT-LOAD-003` current `in_progress` state is represented as a
  parent pass blocker, not a dependency graph edit;
- confirm the parent owner can still use this packet to implement a pending
  non-pass gate artifact before all child evidence closes;
- confirm downstream `MGMT-LOAD-007` gets exact artifact expectations instead
  of prose-only release claims.

## Handoff

This Follow-Up 5 packet is ready for Claude review as a support-scope sidecar.
The parent owner should absorb it as a guard against premature release-pass
claims while `MGMT-LOAD-003` and hosted combined evidence remain unresolved.
