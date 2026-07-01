# MGMT-LOAD-006 Sidecar BFF / Frontend Handoff Follow-Up 4

Date: 2026-07-01
Owner: Codex
Reviewer: Claude
Parent task: `MGMT-LOAD-006`
Helper kind: `bff_handoff_packet`
Scope: support-only follow-up packet. This does not change canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation, CI
configuration, route registry behavior, or governance policy.

## Purpose

This follow-up gives the `MGMT-LOAD-006` owner a final support handoff focused
on dependency reconciliation and reviewer-facing closeout shape. It complements
the earlier sidecars:

- the base packet defines the BFF query gap, operator journey, and artifact
  classes;
- Follow-Up 2 defines pass/fail thresholds and request classification;
- Follow-Up 3 defines the manifest, dependency ledger, failure codes, and
  `MGMT-LOAD-007` handoff;
- this Follow-Up 4 identifies the live dependency/review caveat and the minimum
  absorption sequence before the parent release gate may claim pass.

The key risk here is premature parent closure: `MGMT-LOAD-006` can implement a
gate skeleton while the frontend shell-fanout slice is merged but still waiting
for review approval/owner closeout, or while hosted BFF fanout evidence remains
deferred. That state is useful as an implementation checkpoint, but it is not
release evidence.

## Sources Read

| Source | Use in this follow-up |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0/L1 boundary, sidecar discipline, status command, and PR closeout rules. |
| `.orchestrator/task-briefs/mgmt_load_006_sidecar_bff_handoff_followup_4.md` | Task-scoped assignment and support-only scope. |
| `.orchestrator/skills/worker-anchor-commit.md` | Anchor/commit boundary for task-scoped support artifacts. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout requirement after reviewer approval and PR merge. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Confirmed this sidecar is active and `in_progress`, not `review_approved`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-006` | Confirmed parent is still active and has been reassigned in L0 state to Claude. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001/002/003/004/005` | Dependency state snapshot; after merging latest `origin/dev`, `MGMT-LOAD-003` is in `review`. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | Original root-cause map and target release-gate behavior. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | Fleet order, global acceptance, and `MGMT-LOAD-006`/`007` handoff expectations. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | Frontend shell-fanout acceptance that must still be represented in the parent gate. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md` | Post-route-split hosted route-load result and bundle budget precedent. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md` | Local BFF read-isolation proof and hosted rerun deferral. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF*.md` | Existing support packets to complement instead of replace. |

## Live Dependency Caveat

The active `MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` task row depends on
`MGMT-LOAD-001`, `MGMT-LOAD-002`, `MGMT-LOAD-004`, and `MGMT-LOAD-005`.
However, the parent release-gate document and fleet `INDEX.md` still list
`MGMT-LOAD-003` as a parent dependency because the frontend shell must stop
early full-list fanout and duplicate `/bff/jobs` reads before the startup guard
can be truthfully green.

Current status snapshot from `AI_NAME=Codex ./scripts/ai-status.sh show`:

| Task | Status observed | Gate meaning |
|---|---|---|
| `MGMT-LOAD-001` | `done` | Baseline route-load and BFF fanout evidence exists. |
| `MGMT-LOAD-002` | `done` | `shell-summary` and canonical `/bff/jobs` are merged. |
| `MGMT-LOAD-003` | `review`; execute-plans PR #136 and Pantheon artifact PR #2696 are merged per status text, but reviewer approval/owner closeout is still pending. | Parent gate must treat FE shell-fanout proof as pending until reviewer-approved final evidence or supersession is recorded. |
| `MGMT-LOAD-004` | `done` | Route splitting and hosted route-load precedent exist. |
| `MGMT-LOAD-005` | `done` | Local read-isolation proof exists; hosted post-merge BFF fanout remains deferred. |
| `MGMT-LOAD-006` | active parent, L0 owner currently Claude | Parent gate implementation and release artifact are not yet release proof by themselves. |

This sidecar does not change the dependency graph. It flags that the parent
owner/reviewer should reject a green parent result if `MGMT-LOAD-003` evidence
is not reviewer-approved/done and no explicit reviewer-approved deferral or
supersession exists.

## Absorption Sequence For Parent Owner

`MGMT-LOAD-006` should absorb the earlier sidecar requirements in this order:

1. Pin the dependency ledger first. Record each `MGMT-LOAD-001` through
   `MGMT-LOAD-005` row with status, merge commit, PR, deployment evidence,
   artifact paths, and residual risk.
2. Classify the browser waterfall next. Promote the route-load probe fields
   into hard guards: `usedNetworkidle=false`, first row/empty-state p75/p95,
   non-primary BFF count before first row, duplicate `/bff/jobs`, SSE excluded,
   and raw total request count kept diagnostic.
3. Attach bundle evidence from the exact tested frontend commit. Do not reuse
   a build artifact from a different deploy or branch.
4. Attach BFF fanout evidence. If hosted post-merge fanout is still missing,
   emit `result.pass=false` or a reviewer-approved warning with owner/expiry;
   do not silently use the local `MGMT-LOAD-005` reproduction as hosted proof.
5. Emit the manifest and Markdown summary with stable failure codes from
   Follow-Up 3. `MGMT-LOAD-007` should be able to consume paths without reading
   PR prose.

## Parent Gate Minimum Review Packet

The `MGMT-LOAD-006` PR or review handoff should include this reviewer-facing
table before approval:

| Review question | Required answer before pass |
|---|---|
| Which FE commit and deploy were probed? | Exact execute-plans SHA plus deploy run or `deployment.json` evidence. |
| Which BFF commit/deploy was probed? | Exact Pantheon SHA or hosted BFF deployment evidence. |
| Is `MGMT-LOAD-003` done or explicitly deferred? | Task status, PR/merge evidence, or reviewer-approved deferral/supersession. |
| Did the route probe avoid `networkidle`? | `usedNetworkidle=false` in JSON and Markdown. |
| Did startup request classification run? | Counts for primary, non-primary BFF, duplicate `/bff/jobs`, SSE, deferred shell, assets, and other API. |
| Is BFF fanout hosted post-merge? | Hosted artifact path, or explicit non-pass residual with owner and expiry. |
| Are artifact paths exact? | Manifest JSON, summary Markdown, route timing, waterfall, bundle, and BFF fanout paths. |
| Does the gate fail on missing data? | Missing dependency, artifact, or fanout coverage produces stable failure codes, not green-with-empty-fields. |

## BFF Handoff Focus

For the BFF side, the parent gate should not only check endpoint existence. It
must prove that the BFF is safe under the same management startup fanout shape:

| BFF surface | Parent gate expectation |
|---|---|
| `/health` | p95 <= 200 ms during concurrent management fanout; not blocked by evidence/alerts/approvals/jobs reads. |
| `/bff/management/evidence` | p95 <= 750 ms during shell fanout; explicit failure if omitted. |
| `/bff/management/shell-summary` | 10-concurrent p95 <= 200 ms; response must not carry full approvals, alerts, or jobs lists. |
| `/bff/alerts`, `/bff/approvals`, `/bff/jobs` | Return within timeout/degraded envelope; degraded metadata must be explicit. |
| `/bff/events/stream` | Recorded as long-lived SSE and excluded from bounded p95/readiness calculations. |

If any route is missing from the fanout artifact, use
`bff_fanout_coverage_missing`. If hosted post-merge fanout was not run, the
result should remain non-pass unless the reviewer writes a bounded exception.

## Frontend Handoff Focus

For the execute-plans side, the parent gate should prove the operator journey
after the shell-fanout work is reviewed, closed, and deployed:

| Frontend surface | Parent gate expectation |
|---|---|
| Direct `/management/evidence` entry | Content milestones drive readiness; no `networkidle` readiness. |
| Startup shell reads | `nonPrimaryBffBeforeFirstRow <= 2`; shell-summary is cheap and full list hydration is deferred. |
| Jobs hydration | Duplicate bounded `/bff/jobs` requests before first row/empty state = 0. |
| Route split | Initial management JS and Evidence route chunk budgets are tied to the deployed commit. |
| Degraded shell state | Missing or degraded shell-summary shows honest stale/degraded UI without full-list startup fanout. |

`MGMT-LOAD-004` proved route split timing on commit
`255e60414e0ca36e29c1b2e39f0543d23d2eea80`, but that proof predates final
absorption of the frontend shell-fanout slice. Treat it as precedent until the
parent gate probes the final deployed combination.

## Do Not Treat These As Release Pass

| Evidence | Why it is not enough alone |
|---|---|
| A green gate run with empty dependency rows | It does not prove the child fixes or residual deferrals are merged and reviewer-approved. |
| Route timing pass from `MGMT-LOAD-004` only | It proves route split precedent, not the final combined FE/BFF release gate. |
| Local `MGMT-LOAD-005` BFF fanout reproduction only | It is accepted task evidence, but not hosted post-merge deployment proof. |
| `MGMT-LOAD-002` shell-summary tests only | They do not prove frontend shell fanout stopped before first row. |
| A raw request count such as `70 requests before first row` | It mixes assets/chunks with BFF calls; only classified startup BFF counts should fail the shell budget. |

## Handoff To `MGMT-LOAD-007`

When `MGMT-LOAD-006` completes, `MGMT-LOAD-007` should receive exact paths and
not reinterpret this sidecar. The handoff should name:

- final manifest JSON;
- final summary Markdown;
- route timing JSON;
- request waterfall JSON;
- bundle budget artifact;
- hosted BFF fanout artifact;
- FE deploy evidence;
- BFF deploy evidence;
- child dependency ledger;
- remaining residual risks with owner, expiry, and blocking/non-blocking state.

If any of those are absent, the parent closeout should say whether the absence
is a hard blocker, a reviewer-approved deferral, or a superseded requirement.

## Reviewer Focus

Claude should review this follow-up as a support-only handoff:

- confirm the diff is limited to this task brief and support packet;
- confirm no canonical truth, runtime implementation, CI config, or frontend
  code is changed;
- confirm the `MGMT-LOAD-003` dependency caveat is called out without editing
  the canonical dependency graph;
- confirm the parent owner can use this packet to avoid premature
  `MGMT-LOAD-006` pass/closeout claims;
- confirm `MGMT-LOAD-007` receives exact artifact expectations and residual
  ownership boundaries.

## Handoff

This Follow-Up 4 packet is ready for Claude review as a support-scope sidecar.
The parent owner should decide which checklist items to copy into the
`MGMT-LOAD-006` release-gate PR, manifest, and closeout handoff.
