# MGMT-LOAD-006 Sidecar BFF / Frontend Handoff Follow-Up 3

Date: 2026-07-01
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-LOAD-006`
Helper kind: `bff_handoff_packet`
Scope: support-only follow-up packet. This does not change canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation, CI
configuration, route registry behavior, or governance policy.

## Purpose

This follow-up gives the `MGMT-LOAD-006` owner and reviewer a concrete
absorption handoff for the release-load gate evidence contract. It complements
the base sidecar packet and Follow-Up 2:

- the base packet defines the BFF query gap, operator journey, artifact classes,
  and residual evidence needs;
- Follow-Up 2 defines pass/fail thresholds and request classification;
- this Follow-Up 3 defines the merge-ready evidence ledger, release-gate
  manifest shape, PR/review checklist, and handoff boundary to `MGMT-LOAD-007`.

The main risk addressed here is dependency and artifact drift: the parent gate
can look complete if a route timing sample passes, while the gate still lacks
the exact dependency ledger, artifact paths, or BFF fanout evidence that
`MGMT-LOAD-007` and `MGMT-GAP-006` need for production acceptance.

## Sources Read

| Source | Use in this follow-up |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0/L1 boundary and sidecar support discipline. |
| `.orchestrator/task-briefs/mgmt_load_006_sidecar_bff_handoff_followup_3.md` | Task-scoped assignment and support-only scope. |
| `.orchestrator/skills/worker-anchor-commit.md` | Commit boundary for task-scoped support artifacts. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout boundary; this task must only move from `review_approved` to `done` after owner finalization and a merged closeout PR. |
| `ai-status.json` and `AI_NAME=Codex2 ./scripts/ai-status.sh show ...` | Active task context, review approval, and status-root caveat. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | Budget targets, route-load root cause, and phase-5 release-gate expectations. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | Fleet sequencing and global acceptance requirements. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md` | Required route-load and BFF fanout probe behavior. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md` | Shell-summary endpoint and hosted p95 deferral boundary. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | FE shell fanout and duplicate jobs acceptance. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md` | Route split hosted timing and deployment evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md` | Local BFF read-isolation evidence and hosted rerun deferral. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md` | Parent release-gate scope and acceptance. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md` | Downstream exact artifact path and residual-risk needs. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF.md` | Base support packet to complement, not replace. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Threshold and minimum artifact shape to refine. |

## Status-Root Caveat

The task status wrapper resolves this sidecar as active and `review_approved`
from the configured status root (`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`),
while this task worktree's local `ai-status.json` does not contain the sidecar
row. This packet and closeout note do not reconcile L0 state by hand; the owner
must use `AI_NAME=Codex2 ./scripts/ai-status.sh done ...` after the closeout PR
merges. The parent owner should not use raw local status rows as release proof;
use merged PRs, deployed commits, and the release-gate artifact paths recorded
in the final manifest.

## Closeout Evidence

| Evidence | Result |
|---|---|
| Reviewer approval | Claude approved this packet as support-scope-correct in `/tmp/review-mgmt-load-006-followup-3.md`. |
| Reviewed delivery | PR #2692 merged into `dev` at merge commit `0dcfcf79fafd8feec38827ef76c066c4ca184fdd`; task commit `4069b9de69ea9087cbc219f2ba4d5dc2a4302a40`. |
| Scope check | Review confirmed the delivery touched only this support packet and the generated task brief. |
| Local finalization checks | `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`; `git merge-base --is-ancestor origin/task/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 origin/dev`; `git diff --check`. |

## Release-Gate Absorption Boundary

`MGMT-LOAD-006` should absorb this sidecar as implementation guidance only.

| Layer | Parent gate should absorb | Parent gate should not infer |
|---|---|---|
| Dependency ledger | Per-child task evidence status, PR/deploy refs, artifact paths, and residuals. | That every `MGMT-LOAD-*` status row is already terminal. |
| Browser route load | Content-milestone route timings and classified startup waterfall for `/management/evidence`. | That raw total request count alone is a fail condition. |
| BFF fanout | Concurrent p95s for `/health`, Evidence, shell-summary, alerts, approvals, and jobs. | That local-only fanout evidence is hosted production proof. |
| Bundle budget | Initial management JS and Evidence route chunk gzip sizes with pass/fail or reviewer exception. | That a route timing pass waives bundle-size evidence. |
| Closeout handoff | Exact JSON/Markdown artifact paths for `MGMT-LOAD-007` and `MGMT-GAP-006`. | That prose in a PR description is enough for production acceptance. |

## Evidence Ledger To Emit

The release gate should include a dependency/evidence ledger in its JSON and
Markdown outputs. Minimum rows:

| Ledger row | Required fields | Gate use |
|---|---|---|
| `MGMT-LOAD-001` | route probe script/ref, baseline route timing path, baseline waterfall path, baseline BFF fanout path, reviewer state. | Establishes before-state and proves SSE-safe readiness instrumentation exists. |
| `MGMT-LOAD-002` | BFF commit/PR, shell-summary route evidence, jobs canonicalization evidence, hosted p95 state or explicit deferral. | Prevents shell-summary from being treated as implemented but unmeasured. |
| `MGMT-LOAD-003` | FE commit/PR, shell-summary consumption evidence, non-primary request count, duplicate jobs count. | Proves frontend shell fanout was actually removed from first-row critical path. |
| `MGMT-LOAD-004` | execute-plans merge commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80`, deploy run, hosted route-load samples. | Supplies known post-route-split timing precedent, not final gate proof by itself. |
| `MGMT-LOAD-005` | Pantheon BFF commit/PR, local before/after fanout artifact, hosted post-merge fanout state. | Separates local read-isolation proof from hosted release evidence. |
| `MGMT-LOAD-006` | gate script/config refs, budget file ref, aggregate-release-gate integration, pass/fail artifact paths. | The release-gate deliverable itself. |
| `MGMT-LOAD-007` | downstream closeout consumer and exact artifact handoff paths. | Ensures the parent closeout can cite evidence without reinterpretation. |

If any row is missing a merged commit, deployed environment, or explicit
reviewer-approved deferral, the gate should emit `result.pass=false` with a
`missing_dependency_evidence` failure unless the parent reviewer records a
specific supersession.

## Release-Gate Manifest Shape

Follow-Up 2 defined the minimum pass/fail fields. The parent gate should add a
top-level evidence ledger and artifact manifest so downstream closeout can
consume it directly:

```json
{
  "schemaVersion": 1,
  "taskId": "MGMT-LOAD-006",
  "generatedAt": "2026-07-01T00:00:00Z",
  "environment": {
    "feBaseUrl": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
    "bffBaseUrl": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
    "routePath": "/management/evidence",
    "primaryApiPath": "/bff/management/evidence",
    "authTokenShape": "op-<id>:admin",
    "viteBffMode": "live",
    "viteBffFallback": "strict",
    "realWrites": false
  },
  "build": {
    "executePlansCommit": "",
    "pantheonCommit": "",
    "frontendDeployRun": "",
    "bffDeployEvidence": "",
    "bundleManifestPath": ""
  },
  "dependencyEvidence": [
    {
      "taskId": "MGMT-LOAD-001",
      "status": "done|review_approved|superseded|deferred|missing",
      "mergeCommit": "",
      "artifactPaths": [],
      "residualRisk": null
    }
  ],
  "routeLoad": {
    "usedNetworkidle": false,
    "headingVisibleMs": {"p75": 0, "p95": 0},
    "firstRowOrEmptyVisibleMs": {"p75": 0, "p95": 0},
    "primaryApiCompleteMs": {"p75": 0, "p95": 0},
    "startupGuards": {
      "nonPrimaryBffBeforeFirstRow": 0,
      "duplicateStartupRequests": {"/bff/jobs": 0},
      "totalRequestsBeforeFirstRow": 0
    }
  },
  "bundleBudgets": {
    "initialManagementJsGzipBytes": 0,
    "evidenceRouteChunkGzipBytes": 0,
    "reviewerException": null
  },
  "bffFanout": {
    "/health": {"count": 0, "p95Ms": 0},
    "/bff/management/evidence": {"count": 0, "p95Ms": 0},
    "/bff/management/shell-summary": {"count": 0, "p95Ms": 0},
    "/bff/alerts": {"count": 0, "p95Ms": 0},
    "/bff/approvals": {"count": 0, "p95Ms": 0},
    "/bff/jobs": {"count": 0, "p95Ms": 0}
  },
  "artifactPaths": {
    "summaryMarkdown": "",
    "manifestJson": "",
    "routeTimingJson": "",
    "requestWaterfallJson": "",
    "bundleJson": "",
    "bffFanoutJson": ""
  },
  "result": {
    "pass": false,
    "failures": [],
    "warnings": []
  }
}
```

Zero values are placeholders. The real manifest must contain observed values,
paths, and failure entries. Empty strings should fail validation unless the
field is explicitly not applicable and explained in `result.warnings`.

## Failure Codes For Parent Gate

Use stable failure codes in JSON so `MGMT-LOAD-007` and `MGMT-GAP-006` can
aggregate without parsing prose:

| Code | Trigger |
|---|---|
| `readiness_networkidle_used` | Probe uses `networkidle`, omits `usedNetworkidle`, or reports anything other than `false`. |
| `route_first_row_budget_exceeded` | First row or empty-state p75 > 1500 ms or p95 > 2500 ms. |
| `route_heading_budget_exceeded` | Heading p95 > 1500 ms when heading percentiles are emitted. |
| `startup_non_primary_bff_budget_exceeded` | Non-primary BFF requests before first row > 2. |
| `startup_duplicate_jobs` | Duplicate bounded `/bff/jobs` request before first row > 0. |
| `bundle_budget_exceeded` | Initial management JS or Evidence route chunk exceeds budget without reviewer exception. |
| `bff_health_fanout_budget_exceeded` | `/health` p95 > 200 ms during concurrent management fanout. |
| `bff_evidence_fanout_budget_exceeded` | `/bff/management/evidence` p95 > 750 ms during fanout. |
| `bff_shell_summary_budget_exceeded` | `/bff/management/shell-summary` p95 > 200 ms under 10 concurrent requests. |
| `bff_fanout_coverage_missing` | Fanout artifact omits any required route row. |
| `missing_dependency_evidence` | A required child evidence row lacks merge/deploy/artifact refs and no approved deferral is recorded. |
| `missing_artifact_path` | Summary, manifest, route timing, waterfall, bundle, or BFF fanout path is absent. |

## Operator Journey To Preserve

The release gate should continue to model the user-visible cold entry:

1. Navigate directly to `/management/evidence` on the Pantheon-owned dev FE
   host, with live BFF and strict fallback.
2. Wait for document load, shell attachment, route heading, primary Evidence
   API completion, and first row or empty state.
3. Record the SSE request as long-lived realtime traffic and exclude it from
   readiness and bounded p95 calculations.
4. Classify shell-summary, health, alerts, approvals, jobs, assets, chunks, and
   primary Evidence calls separately.
5. Record post-primary drawer/list hydration separately so it can be diagnosed
   without failing first-row startup unless it starts too early.

## Frontend Handoff Notes

For the execute-plans side of `MGMT-LOAD-006`, the gate should require:

| Surface | Handoff expectation |
|---|---|
| Route probe | Reuse or extend the `MGMT-LOAD-001` route-load probe instead of adding a new readiness model. |
| Request classifier | Emit `primary`, `non_primary_bff`, `deferred_shell`, `asset`, `sse`, and `other_api` classifications from Follow-Up 2. |
| Bundle evidence | Emit initial management JS gzip and Evidence route chunk gzip from the build or deploy artifact that matches the tested commit. |
| Degraded UI | Shell-summary unavailable or degraded count state should be visible without full-list startup fanout. |
| Safe defaults | Preserve `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false` for dev release smoke unless an operator explicitly enables writes. |

## BFF Handoff Notes

For the Pantheon BFF side of `MGMT-LOAD-006`, the gate should require:

| Surface | Handoff expectation |
|---|---|
| Fanout route set | `/health`, `/bff/management/evidence`, `/bff/management/shell-summary`, `/bff/alerts`, `/bff/approvals`, and `/bff/jobs`. |
| Shell-summary | Measured under 10 concurrent requests; no full approvals, alerts, or jobs list payloads. |
| Health independence | `/health` p95 stays <= 200 ms during management read fanout. |
| Evidence fanout | Evidence p95 stays <= 750 ms during shell fanout. |
| Degraded metadata | Timeout/degraded routes return explicit degraded metadata instead of silent empty success or hanging. |
| SSE | `/bff/events/stream` is recorded separately and not treated as a bounded request. |

## PR And Review Checklist

`MGMT-LOAD-006` PR or closeout should include this checklist in reviewer-facing
form:

| Check | Required answer |
|---|---|
| Which FE commit was probed? | Exact execute-plans SHA and deploy run/manifest. |
| Which BFF commit was probed? | Exact Pantheon SHA or deploy evidence. |
| Where is the manifest JSON? | Repo-relative archive path. |
| Where is the summary Markdown? | Repo-relative archive path. |
| Where are the raw route timing and waterfall files? | Repo-relative archive paths. |
| Where are the bundle and BFF fanout files? | Repo-relative archive paths. |
| Did the probe avoid `networkidle`? | `usedNetworkidle=false` in JSON and summary. |
| Did request classification run? | Counts for non-primary BFF, duplicate jobs, total requests, and SSE. |
| Did dependency evidence pass? | Ledger row for each `MGMT-LOAD-001` through `MGMT-LOAD-005`. |
| What remains for `MGMT-LOAD-007`? | Exact artifact paths and residual risks, not a prose-only claim. |

## Reviewer Focus

Claude should review this follow-up as a support-only handoff:

- confirm the diff is limited to this task brief and support packet;
- confirm no canonical truth, runtime implementation, CI config, or frontend
  code is changed;
- confirm this packet composes with `MGMT-LOAD-006` by defining artifact and
  review inputs, not by approving the parent release-gate implementation;
- confirm the status-root caveat is truthful and does not mutate L0 state;
- confirm `MGMT-LOAD-007` receives exact evidence requirements and no hidden
  assumption that local-only or route-only proof closes the load gate.

## Handoff

Claude approved this Follow-Up 3 packet as a support-scope-correct sidecar, and
the reviewed delivery is merged in PR #2692. The parent owner should decide
whether to copy the ledger, manifest fields, and failure codes into the
`MGMT-LOAD-006` release-gate implementation and closeout artifacts.
