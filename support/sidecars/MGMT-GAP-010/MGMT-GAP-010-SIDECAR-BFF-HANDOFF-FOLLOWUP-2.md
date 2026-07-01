# MGMT-GAP-010 Sidecar BFF Handoff Follow-Up 2

Task ID: `MGMT-GAP-010-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
Parent task: `MGMT-GAP-010`
Helper kind: `bff_handoff_packet`
Owner: `Codex2`
Reviewer: `Claude`
Prepared: 2026-07-01
Mutates canonical truth: false

## Scope

This is a support-only sidecar packet. It does not change L1 canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation,
registry/governance behavior, or any parent acceptance criteria.

This follow-up complements the already approved and merged base packet:

```text
support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF.md
support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-REVIEW.md
```

The useful delta is not another implementation recipe. The base packet already
captured the BFF query gap, operator journey, and frontend handoff. This
follow-up packages the current child-task ledger and the exact absorption
boundary for `MGMT-GAP-010`, `MGMT-LOAD-006`, `MGMT-LOAD-007`, and downstream
`MGMT-GAP-006`.

## Inputs Read

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/mgmt_gap_010_sidecar_bff_handoff_followup_2.md`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `AI_NAME=Codex2 ./scripts/ai-status.sh show ...` for this sidecar,
  `MGMT-GAP-010`, and `MGMT-LOAD-001` through `MGMT-LOAD-007`
- `support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-REVIEW.md`
- `support/sidecars/MGMT-LOAD-003/*FOLLOWUP-2.md` through `*FOLLOWUP-4.md`
- `support/sidecars/MGMT-LOAD-006/*FOLLOWUP-2.md` and `*FOLLOWUP-3.md`
- `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/MGMT-GAP-010-management-load-gate.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-review.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-LOAD-001-closeout-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-local-before-after-2026-07-01.md`

I intentionally did not read `current-work.md` or the full
`ai-activity-log.jsonl`.

## Current Coordination Snapshot

| Surface | Current state |
|---|---|
| This sidecar | `in_progress`, owner `Codex2`, reviewer `Claude`, artifact path `support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`. |
| Parent `MGMT-GAP-010` | Active `todo`, owner `Claude`, reviewer `Codex`; depends on `MGMT-LOAD-007`. |
| Base MGMT-GAP-010 sidecar | Approved by Claude and merged in Pantheon PR #2699 at `fb8f19aa415741617dafa950834ab199d6413124`. |
| `MGMT-LOAD-001` | Archived `done`; route-load and BFF fanout baselines are durable. |
| `MGMT-LOAD-002` | Archived `done`; `GET /bff/management/shell-summary` and one canonical `GET /bff/jobs` are merged. |
| `MGMT-LOAD-003` | Active `in_progress`, owner `Claude`, reviewer `Codex`; frontend shell fanout proof is not terminal. |
| `MGMT-LOAD-004` | Archived `done`; execute-plans PR #134 merged at `255e60414e0ca36e29c1b2e39f0543d23d2eea80`; hosted route timing passed. |
| `MGMT-LOAD-005` | Archived `done`; read-concurrency isolation merged; hosted post-merge fanout rerun remains deferred to `MGMT-LOAD-007`. |
| `MGMT-LOAD-006` | Active `todo`, owner `Claude`, reviewer `Codex`; release-load gate still needs implementation/closeout. |
| `MGMT-LOAD-007` | Active `todo`, owner `Codex`, reviewer `Claude`; waits on `MGMT-LOAD-006` and owns parent closeout proof. |

## Absorption Delta Since The Base Packet

The base packet was correct to flag that status truth had not caught up with
several implementation/review records. The current status ledger has improved:
`MGMT-LOAD-001`, `MGMT-LOAD-002`, `MGMT-LOAD-004`, and `MGMT-LOAD-005` are now
archived `done`.

That does not close `MGMT-GAP-010`. The parent still has three non-terminal
surfaces:

1. `MGMT-LOAD-003` must prove the frontend shell no longer starts more than
   two non-primary BFF requests before Evidence first row or empty state, and
   that `/bff/jobs` is not duplicated before that milestone.
2. `MGMT-LOAD-006` must turn the route-load, startup request, bundle, and BFF
   fanout checks into a fail-capable release gate with JSON/Markdown artifacts.
3. `MGMT-LOAD-007` must close the umbrella with merged PRs, deployed FE/BFF
   evidence, hosted probe results, artifact paths, and residual risks.

## BFF Query Gap State

The BFF side has strong implementation evidence, but the parent still needs
deployed gate evidence.

Durable BFF pieces now available on the Pantheon side:

- `GET /bff/management/shell-summary` supplies cheap counts, redacted session,
  transport state, and `meta.surfaces` freshness/degraded metadata.
- The shell-summary route does not return full approvals, alerts, or jobs list
  payloads.
- One canonical `GET /bff/jobs` route remains.
- Evidence, alerts, approvals, and jobs read aggregation are offloaded through
  bounded read isolation and return explicit degraded metadata on timeout.
- `/health` remains independent from management read aggregation.

Evidence already recorded:

| Source | Useful result |
|---|---|
| `MGMT-LOAD-001` hosted baseline | Before-state fanout showed `/health` p95 1328 ms, Evidence p95 1423 ms, alerts p95 1513 ms, approvals p95 1537 ms, jobs p95 1538 ms under concurrent fanout. |
| `MGMT-LOAD-002` closeout/review | Shell-summary and jobs canonicalization passed focused BFF tests; hosted timing gap accepted via the later probe path. |
| `MGMT-LOAD-005` local before/after | Synthetic 400 ms slow-read fanout improved `/health` p95 from 1629 ms pre-fix shape to 189 ms after read isolation; Evidence/alerts/approvals/jobs stayed 425-591 ms. |

Residual BFF proof that must stay visible for the parent:

- Hosted dev BFF post-merge fanout rerun is still required for final release
  evidence. Local before/after proof is useful implementation evidence, not
  deployed release proof.
- The rerun must cover `/health`, `/bff/management/evidence`,
  `/bff/management/shell-summary`, `/bff/alerts`, `/bff/approvals`, and
  `/bff/jobs`.
- Expected budgets remain `/health` p95 <= 200 ms during fanout, Evidence p95
  <= 750 ms during fanout, and shell-summary p95 <= 200 ms under 10 concurrent
  requests.
- Any blocker must identify the exact route/read path and include explicit
  degraded/timeout metadata evidence instead of silently treating empty data as
  success.

## Frontend Handoff State

The frontend side should use the base packet plus the later `MGMT-LOAD-003`
follow-ups. Do not ask the parent to re-add the BFF contract; ask it to prove
the first-row budget and duplicate jobs behavior.

Current known absorption boundary from `MGMT-LOAD-003` follow-up support:

- `TopBar` should consume `/bff/management/shell-summary` for first-paint
  badge/session/transport state instead of full approvals, alerts, and jobs
  list payloads.
- Shell-summary degraded/unavailable state must render honestly and must not
  immediately trigger full-list fallback before route primary content.
- `NotificationCenter` full-list hydration should remain gated behind open
  state.
- `JobProgressDrawer` should not start `/bff/jobs` before first row or empty
  state unless the user explicitly opens it or the parent documents and proves
  the permitted post-primary timing.
- `/bff/events/stream` is a long-lived realtime stream and must stay excluded
  from readiness and bounded request timing.

Parent review questions for `MGMT-LOAD-003`:

| Question | Required proof |
|---|---|
| Are full approvals/alerts/jobs payloads gone from first mount? | Component or e2e request-log evidence for shell-summary success and degraded paths. |
| Do `/bff/me`, `/health`, and shell-summary overrun the budget together? | First-row request log with non-primary BFF count `<= 2`, or a reviewed acceptance update if the parent intentionally changes the budget. |
| Does jobs hydration happen too early? | Delayed-primary-route fixture or hosted request waterfall proving `/bff/jobs` is not duplicated before first row or empty state. |
| Is the e2e gate hard enough? | Hard assertion for non-primary BFF count and duplicate jobs before first row, not only warnings/annotations. |

## Operator Journey To Preserve

The parent closeout should preserve the user-visible cold entry to Evidence:

```text
operator opens /management/evidence
  -> FE document loads from the Pantheon-owned dev FE host
  -> shell attaches without starting heavyweight list fanout
  -> Evidence route chunk loads
  -> primary GET /bff/management/evidence starts and completes
  -> heading and first row or honest empty state become visible
  -> shell-summary provides first-paint counts/session/transport
  -> full list/drawer hydration happens only after primary content or user open
  -> /bff/events/stream is recorded as SSE and excluded from readiness
```

Minimum route-load artifact fields for `MGMT-LOAD-006`/`MGMT-LOAD-007`:

- FE base URL, BFF base URL, route path, primary API path, auth token shape
  without secret value.
- FE commit, BFF commit or deploy evidence, deploy run or manifest.
- `usedNetworkidle=false`.
- `domcontentloaded`, shell attached, heading visible, primary API complete,
  and first row or empty state visible timestamps.
- Classified request waterfall with `primary`, `non_primary_bff`,
  `deferred_shell`, `asset`, `sse`, and `other_api`.
- Duplicate startup request counts, especially `/bff/jobs`.
- Bundle budget outputs matching the probed FE commit.
- BFF fanout p95 rows for the full route set.

## Parent Closeout Ledger For MGMT-LOAD-007

`MGMT-LOAD-007` should not close the umbrella until each row is either terminal
with artifact paths or explicitly superseded by reviewer-approved replacement
evidence.

| Row | Current closeout use |
|---|---|
| `MGMT-LOAD-001` | Use as before-state route-load and BFF fanout baseline. It proves instrumentation and SSE-safe readiness, not the final fixed state. |
| `MGMT-LOAD-002` | Use as BFF shell-summary/jobs route contract evidence. Hosted p95 proof remains downstream, not waived. |
| `MGMT-LOAD-003` | Must supply FE shell-summary consumption, first-row non-primary count, duplicate jobs count, and tests. Currently not terminal. |
| `MGMT-LOAD-004` | Use hosted route-split timing precedent: first row/empty state p75 931 ms, p95 1203 ms on execute-plans commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80`. Do not treat the raw 70 total requests before first row as a classified pass/fail result. |
| `MGMT-LOAD-005` | Use as BFF read-isolation implementation proof and local before/after evidence. Hosted post-merge fanout rerun is still needed for release proof. |
| `MGMT-LOAD-006` | Must emit the release-gate manifest, budget results, classified waterfall, bundle outputs, BFF fanout, failure codes, and summary Markdown. Currently not terminal. |
| `MGMT-LOAD-007` | Must archive final exact artifact paths, PR SHAs, deployed evidence, residual risks, and the handoff paths required by `MGMT-GAP-006`. |

## Do Not Infer

Do not infer any of the following from this sidecar:

- `MGMT-GAP-010` is complete.
- `MGMT-LOAD-003`, `MGMT-LOAD-006`, or `MGMT-LOAD-007` have passed review.
- Local BFF read-isolation evidence replaces hosted post-merge fanout evidence.
- A route timing pass alone waives startup request classification or bundle
  budget evidence.
- Raw total browser request count is directly comparable across local dev,
  hosted production build, and classified BFF-only budgets.
- Shell-summary unavailable means all counts are live zero.

## Reviewer Handoff

Claude should review this support packet for:

1. Sidecar scope: support artifact only, no canonical/runtime/frontend changes.
2. Ledger accuracy against the current status/archive state.
3. Whether the parent absorption boundary is precise enough for `MGMT-GAP-010`,
   `MGMT-LOAD-006`, `MGMT-LOAD-007`, and `MGMT-GAP-006`.
4. Whether any statement accidentally upgrades sidecar evidence into parent
   acceptance.

If approved, the parent owner can absorb the checklist into the main load-gap
closeout path. This packet itself should not move `MGMT-GAP-010` or any
`MGMT-LOAD-*` execution task to done.

## Verification For This Sidecar

This sidecar changes no runtime or frontend code. Verification performed for
this support artifact:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-GAP-010-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-GAP-010
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-001
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-002
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-003
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-004
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-005
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-006
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-LOAD-007
git diff --check
python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py services/control-plane/bff/test_mgmt_load_005_read_concurrency.py -q
```

Results:

- `git diff --check`: passed.
- Focused BFF suite: `12 passed, 8 warnings in 13.68s`.
- Warnings were existing FastAPI `on_event` deprecation warnings from
  `services/control-plane/bff/main.py` and FastAPI internals.
