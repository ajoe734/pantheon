# MGMT-LOAD-007 Sidecar BFF / Frontend Handoff

Date: 2026-07-01
Owner: Codex2
Reviewer: Codex
Parent task: `MGMT-LOAD-007`
Helper kind: `bff_handoff_packet`
Scope: support-only packet. This does not change canonical truth, BFF runtime
code, frontend runtime code, release-gate implementation, CI configuration,
route registry behavior, governance policy, or parent closeout status.

## Purpose

This packet gives the `MGMT-LOAD-007` owner a compact handoff for closing the
management-console load gap after `MGMT-LOAD-006` produced the release-load
gate artifacts. It summarizes which evidence is already usable, which evidence
is still only baseline or local proof, and the exact artifact paths that must
be reconciled before `MGMT-GAP-010` or downstream production acceptance can be
claimed.

`MGMT-LOAD-007` should treat this file as a support checklist, not as the
parent closeout itself. The parent owner still decides whether to absorb any
row into the final archive, residual-risk table, or `MGMT-GAP-006` handoff.

## Sources Read

| Source | Use in this packet |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0/L1 boundary, sidecar discipline, status command, and PR closeout rules. |
| `.orchestrator/task-briefs/mgmt_load_007_sidecar_bff_handoff.md` | Task-scoped assignment and support-only scope. |
| `.orchestrator/skills/worker-anchor-commit.md` | Worker-safe commit and anchor boundary for support artifacts. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout rule after reviewer approval and PR merge. |
| central `ai-status.json` through `jq` | Confirmed this sidecar is active, `in_progress`, owned by Codex2, reviewed by Codex, and that `MGMT-LOAD-007` is active owner Codex/reviewer Claude. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | Original route-load diagnosis, operator journey, BFF fanout gaps, and target budgets. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | Fleet sequencing and global closeout acceptance. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md` | Hosted before-state route-load and BFF fanout evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md` | Shell-summary and canonical `/bff/jobs` implementation evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | Frontend shell-fanout reduction and route-primary-ready follow-up evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md` | Hosted route-split timing and FE deploy evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md` | BFF read-isolation implementation proof and hosted fanout deferral. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md` | Release-gate artifact list, fail-closed result, and residual-risk handoff. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md` | Parent closeout acceptance requirements. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/*` | Baseline, hosted route-load, local BFF fanout, and release-gate artifacts. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF*.md` | Earlier release-gate support packets to complement, not replace. |

## Current Parent State

Central L0 state at this handoff has:

| Task | State | Meaning for this packet |
|---|---|---|
| `MGMT-LOAD-007` | `in_progress`, owner Codex, reviewer Claude | Parent closeout is active; this sidecar should hand off to Codex for absorption. |
| `MGMT-LOAD-007-SIDECAR-BFF-HANDOFF` | `in_progress`, owner Codex2, reviewer Codex | This support packet is the sidecar deliverable. |
| `MGMT-LOAD-001..006` | archived/done in central status archive, and `MGMT-LOAD-006` release-gate dependency rows pass | Child tasks are terminal enough for closeout review, but the release-gate result still fails on stale hosted probe inputs. |

The local worktree's checked-in `ai-status.json` is older and still lists
`MGMT-LOAD-001..007` as `todo`; do not use that stale copy to override the
central status-root state or the task archive.

## Evidence Ledger For Parent Closeout

| Evidence area | Current artifact | What it proves | What it does not prove |
|---|---|---|---|
| Hosted baseline route load | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md` | Pre-fix `/management/evidence` first row/empty state was 4668 ms; startup waterfall had duplicate `/bff/jobs`; readiness did not use `networkidle`. | It is before-state only and should keep the release gate red after fixes land. |
| Hosted baseline BFF fanout | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md` | Pre-fix fanout delayed `/health` to p95 1328 ms and Evidence to p95 1423 ms. | It is not hosted post-merge BFF proof. |
| Shell-summary / jobs canonicalization | `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md` | `GET /bff/management/shell-summary` exists and avoids full list payloads; `/bff/jobs` has one canonical implementation. | Hosted concurrent shell-summary p95 was explicitly deferred to later probe/gate evidence. |
| Frontend shell fanout | `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | TopBar consumes shell-summary, fallback reads wait for route-primary-ready, and jobs drawer hydration is deferred; PRs and validation are recorded. | Parent still needs the final hosted waterfall/gate run against the deployed FE/BFF pair to claim startup-request budgets. |
| Hosted route split | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md` | Five hosted samples on FE commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80` showed first row/empty-state p75 931 ms and p95 1203 ms. | It predates the final combined release gate and cannot by itself prove BFF fanout or shell-fanout budgets. |
| Local BFF read isolation | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-local-before-after-2026-07-01.md` | Synthetic slow-read repro moved `/health` p95 from 1629 ms to 189 ms and Evidence p95 to 425 ms under concurrent fanout. | It is local TestClient proof, not hosted dev-BFF post-merge proof. |
| Release gate artifact | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md` and `.json` | Gate implementation fails closed and includes dependency, bundle, route timing, startup request, and BFF fanout checks. Bundle budgets pass. | The current run is red because it still consumes pre-fix hosted route/fanout evidence; it is not a final green production load gate. |

## Closeout-Blocking Questions For `MGMT-LOAD-007`

Before parent closeout can mark the load gap fixed, the owner should answer
these questions in the final archive or residual-risk table:

| Question | Current answer from available artifacts | Required parent disposition |
|---|---|---|
| Did `/management/evidence` first row/empty-state p75 <= 1.5 s and p95 <= 2.5 s on the final deployed FE/BFF pair? | `MGMT-LOAD-004` hosted route split proves p75 931 ms / p95 1203 ms on FE commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80`; `MGMT-LOAD-006` final gate still uses stale pre-fix route timing at 4668 ms. | Either rerun route-load against the final deployed pair and archive green evidence, or record a reviewer-approved residual with owner and expiry. |
| Did startup BFF fanout meet `non_primary_bff_before_first_row <= 2` and duplicate `/bff/jobs == 0`? | The red release gate shows stale baseline values: 5 non-primary BFF requests and duplicate `/bff/jobs`. | Rerun the hosted waterfall after the shell-fanout deployment, or keep this as a blocking residual. |
| Did hosted BFF fanout meet `/health` p95 <= 200 ms, Evidence p95 <= 750 ms, and shell-summary p95 <= 200 ms? | Local proof passes `/health` 189 ms and Evidence 425 ms; current release gate still reports stale hosted `/health` 1328 ms, Evidence 1423 ms, and missing shell-summary p95. | Rerun hosted BFF fanout including `/bff/management/shell-summary`, or explicitly classify the missing hosted run as residual risk. |
| Are FE and BFF deploy SHAs recorded for the probed environment? | FE deploy evidence exists for route split and execute-plans bundle gate; current release gate summary does not by itself prove the final BFF deploy SHA under test. | Parent archive should name exact execute-plans SHA, Pantheon/BFF SHA or deployment evidence, and the probe timestamp. |
| Are `MGMT-GAP-006` artifact paths exact? | `MGMT-LOAD-006` names the release artifact path family, but current result is red/stale. | Hand off exact JSON/Markdown/waterfall/bundle/fanout paths plus pass/fail state; do not hand off a prose-only claim. |

## BFF Handoff For Parent Owner

`MGMT-LOAD-007` should keep the BFF fanout route family explicit:

| Route | Parent closeout expectation |
|---|---|
| `GET /health` | p95 <= 200 ms during concurrent management fanout; must not queue behind management read aggregation. |
| `GET /bff/management/evidence` | p95 <= 750 ms during shell fanout. |
| `GET /bff/management/shell-summary` | p95 <= 200 ms under concurrent load; response must not include full approvals, alerts, or jobs list payloads. |
| `GET /bff/alerts` | Return or explicitly degrade inside the read-timeout envelope; must not block `/health`. |
| `GET /bff/approvals` | Return or explicitly degrade inside the read-timeout envelope; must not block `/health`. |
| `GET /bff/jobs` | One canonical server route remains; client waterfall must have zero duplicate bounded jobs reads before primary content. |
| `GET /bff/events/stream` | Record as long-lived SSE and exclude from bounded p95/readiness calculations. |

If the hosted BFF credential is unavailable, record the credential gap as a
specific blocker or residual with owner and expiry. Do not silently promote
the local TestClient before/after result into hosted release evidence.

## Frontend Handoff For Parent Owner

The final operator journey should be measured as direct cold entry to
`/management/evidence` on the Pantheon-owned dev FE host:

1. Verify `deployment.json` points at the intended execute-plans commit and
   BFF host, with `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and safe
   write defaults.
2. Run the route-load probe with `domcontentloaded`, shell visible, route
   heading visible, primary Evidence API complete, and first row/empty-state
   visible milestones.
3. Confirm `usedNetworkidle=false`; `/bff/events/stream` must be a recorded
   realtime stream, not readiness evidence.
4. Classify requests before first row by `primary`, `non_primary_bff`,
   `deferred_shell`, `asset`, `sse`, and `other_api`.
5. Confirm full approvals, alerts, and jobs lists are not required before
   primary Evidence content; fallback/degraded shell state must remain honest.
6. Confirm duplicate bounded `/bff/jobs` requests before first row/empty-state
   are zero.

The raw `requests before first row` value may include FE assets and chunks. The
parent gate should fail on classified early BFF fanout and duplicate bounded
requests, not on an undifferentiated asset count.

## Exact Artifact Paths To Hand To `MGMT-GAP-006`

When `MGMT-LOAD-007` closes or defers the parent gate, it should hand
`MGMT-GAP-006` these exact paths and their pass/fail state:

| Artifact | Required path |
|---|---|
| Release manifest | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json` |
| Markdown summary | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md` |
| Route timing | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-route-timing-2026-07-01.json` |
| Request waterfall | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-request-waterfall-2026-07-01.json` |
| Bundle evidence | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bundle-2026-07-01.json` |
| BFF fanout | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bff-fanout-2026-07-01.json` |
| Baseline route-load reference | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md` and `.json` |
| Baseline BFF fanout reference | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md` and `.json` |
| Local BFF before/after reference | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-local-before-after-2026-07-01.md` and `.json` |
| Hosted route-split reference | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md` |

If a final hosted rerun supersedes the `2026-07-01` release artifacts, the
parent owner should list both the superseding paths and the stale red paths so
reviewers can see the before/after sequence.

## Do Not Treat These As Final Green Evidence

| Evidence | Why it is insufficient alone |
|---|---|
| `release-load-gate-2026-07-01.*` as currently checked in | It is a real fail-closed gate, but its route/fanout rows are stale pre-fix inputs and `overall: fail`. |
| `mgmt-load-004-route-load-hosted-2026-07-01.md` | It proves route-split timing precedent only; it does not include the final shell-fanout and BFF fanout combined gate. |
| `bff-fanout-local-before-after-2026-07-01.md` | It proves BFF read isolation locally, not hosted post-merge behavior. |
| Bundle-budget pass | Bundle size is only one gate dimension; startup request and BFF fanout gates can still fail. |
| Central archive terminal state alone | Done child tasks prove reviewed task closure, not necessarily a green hosted production load gate. |

## Recommended Parent Closeout Sequence

1. Verify child task archive and PR/merge SHAs for `MGMT-LOAD-001..006`.
2. Verify current FE deployment commit and Pantheon/BFF deployment commit.
3. If credentials are available, rerun hosted route-load and BFF fanout probes
   against the final deployed FE/BFF pair, then rerun the release gate.
4. If credentials are unavailable, record an explicit blocker/residual risk
   with owner, expiry, and whether it blocks `MGMT-GAP-010`.
5. Archive the final artifact paths, including stale red artifacts if they are
   retained for before/after audit.
6. Hand `MGMT-GAP-006` the exact artifact paths and pass/fail disposition.
7. Only close `MGMT-GAP-010` after reviewer approval covers either green hosted
   evidence or a bounded accepted deferral.

## Reviewer Focus

Codex should review this sidecar as support-only material:

- confirm the diff is limited to this task brief and this support packet;
- confirm no canonical truth, BFF/frontend runtime, CI, route registry, or
  governance policy changed;
- confirm the packet distinguishes stale hosted baseline, local proof, hosted
  route-split precedent, and final release-gate evidence;
- confirm the handoff gives `MGMT-LOAD-007` exact artifact paths and residual
  ownership boundaries;
- confirm the sidecar is handed to the assigned reviewer and does not mark the
  parent task as done.
