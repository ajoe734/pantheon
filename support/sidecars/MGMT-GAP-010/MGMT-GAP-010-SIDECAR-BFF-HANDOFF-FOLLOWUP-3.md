# MGMT-GAP-010 Sidecar BFF Handoff Follow-Up 3

Task ID: `MGMT-GAP-010-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
Parent task: `MGMT-GAP-010`
Helper kind: `bff_handoff_packet`
Owner: `Claude2`
Reviewer: `Claude`
Prepared: 2026-07-01
Mutates canonical truth: false

## Scope

This is a support-only sidecar packet. It does not change L1 canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation,
registry/governance behavior, or any parent acceptance criteria.

This follow-up complements the already approved and merged packets:

```text
support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF.md
support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-REVIEW.md
support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
```

The base packet captured the BFF query gap, operator journey, and frontend
handoff. Follow-Up 2 packaged the child-task ledger as of the point where
`MGMT-LOAD-001/002/004/005` had reached archived `done` and `MGMT-LOAD-003`,
`MGMT-LOAD-006`, `MGMT-LOAD-007` were still open. Since then, `MGMT-LOAD-003`
closed out and `MGMT-LOAD-006` landed a real release-gate implementation. This
follow-up's useful delta is: (1) recording that closeout, (2) flagging that the
release gate currently reports `pass:false` for a specific, well-documented
reason, not silently going green, and (3) reconciling the recurring gap between
`ai-status.json` truth and the archived/doc evidence.

## Inputs Read

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/mgmt_gap_010_sidecar_bff_handoff_followup_3.md`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json` (`MGMT-GAP-010`, `MGMT-GAP-006`, `MGMT-LOAD-001` through
  `MGMT-LOAD-007`)
- `support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-REVIEW.md`
- `support/sidecars/MGMT-GAP-010/MGMT-GAP-010-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md`
- `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/MGMT-GAP-010-management-load-gate.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md`
- `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json`
- `git log` / `git show` on `79ecdce3f`, `eab27928b`, `154980a97`, `23d4297cc`,
  `65ba4685b`, `edb7526bd`, `8315eb8a4`
- `gh pr view` for PR `#2709`, `#2711`, `#2712` (`ajoe734/pantheon`)
- `python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py services/control-plane/bff/test_mgmt_load_005_read_concurrency.py scripts/test_aggregate_release_gate.py -q`
- `git diff --check`

I intentionally did not read `current-work.md` or the full
`ai-activity-log.jsonl`.

## Current Coordination Snapshot

| Surface | `ai-status.json` truth | Archived/doc/PR truth |
|---|---|---|
| This sidecar | new, `todo`, owner `Claude2`, reviewer `Claude` | n/a |
| Follow-Up 2 sidecar | archived (owner finalized) | PR `#2701` merged at `de40c50e3f712e12ace91f074c195acb05e98f63` |
| Parent `MGMT-GAP-010` | `todo`, owner `Gemini2`, reviewer `Codex` | Doc/archive evidence for 5 of 7 `MGMT-LOAD-*` children is closeout-complete; gate is real but non-passing (see below) |
| `MGMT-LOAD-001` | `todo` (stale) | `done` per archive; baseline route-load + BFF fanout evidence |
| `MGMT-LOAD-002` | `todo` (stale), owner `Claude2` | `done` per archive; shell-summary + canonical `/bff/jobs` merged |
| `MGMT-LOAD-003` | `todo` (stale), owner `Claude` | Owner-closeout recorded 2026-07-01 by Codex2; execute-plans PR `#136` merged at `75a943ed3f...`; Pantheon PR `#2705` merged at `3f9c91f0c7...`; finalize-evidence commit `edb7526bd` merged via PR `#2709` at `8315eb8a4...` |
| `MGMT-LOAD-004` | `todo` (stale) | `done` per archive; hosted route-split timing precedent |
| `MGMT-LOAD-005` | `todo` (stale), owner `Gemini` | `done` per archive; local BFF read-isolation before/after evidence; hosted post-merge fanout rerun still deferred |
| `MGMT-LOAD-006` | `todo` (stale), owner `Gemini2` | Release-gate script + tests landed via PR `#2711` (`79ecdce3f` -> merge `154980a97`); evidence refreshed via PR `#2712` (`23d4297cc` -> merge `65ba4685b`); real gate run reports **`overall: fail`, `pass: false`** |
| `MGMT-LOAD-007` | `todo`, owner `Codex`, waits on `MGMT-LOAD-006` | Not yet started; correctly still blocked |

`ai-status.json` has not caught up with any of this doc/PR evidence because none
of the recent `MGMT-LOAD-*` commits ran `scripts/ai-status.sh done` (or even
`progress`) — they only edited task docs, archive files, and code. This is the
same status-truth-lag pattern the base packet and Follow-Up 2 already flagged;
it has not resolved itself and now spans more children.

## What Changed Since Follow-Up 2

1. **`MGMT-LOAD-003` closed out.** Frontend delivery
   (`ajoe734/execute-plans#136`, merged `75a943ed3fb007c61f056496e5b8f7dfdb305a53`)
   and Pantheon evidence (`ajoe734/pantheon#2705`, merged
   `3f9c91f0c70f37e6645b14cf03611890e645df1a`) are both merged. The follow-up FE
   commit `6dae62a7a697e8427ce2623c1ee0dca48e4dd418` added
   `routePrimaryReady.ts` so `TopBar` and `JobProgressDrawer` gate their
   non-primary/full-list reads on route-primary-ready + idle, and
   `e2e/23-management-shell-fanout.spec.ts` hard-asserts the only budgeted
   non-primary BFF requests before that marker are `/bff/me` and
   `/bff/management/shell-summary`. Owner-finalization metadata was recorded by
   Codex2 in `edb7526bd`, merged via PR `#2709`.
2. **`MGMT-LOAD-006` shipped a real, tested release gate**, not a placeholder:
   `scripts/aggregate-release-gate.mjs` aggregates dependency pass-eligibility,
   bundle budget, route-timing/readiness, startup-request waterfall
   classification (with duplicate-`/bff/jobs` detection), and BFF fanout
   latency into one fail-closed JSON+Markdown artifact.
   `scripts/test_aggregate_release_gate.py` has 8 passing cases covering each
   failure mode plus fail-closed-on-missing-evidence. This landed via PR
   `#2711` (merge `154980a97940e2cb78f6f325bca2c5413f63e32a`).
3. **`MGMT-LOAD-006` re-ran after `execute-plans` PR `#138` merged**
   (`cbd833c49edc`, adds `bundle-budget-check.mjs` and wires
   `probe:bundle-budget` into `pantheon-integration-gate.yml`). This landed via
   PR `#2712` (merge `65ba4685badf07f533e100ad1c7e822a299762ea`). Bundle sizes
   were byte-identical to the pre-merge run; only `feCommit` changed.
4. **The real gate result is still `pass: false`, and the doc explains exactly
   why** — see next section. This is not a regression to flag; it is the gate
   correctly refusing to go green on stale evidence.

## Why `MGMT-LOAD-006` Is Not Green (and should not be forced green)

`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md`
(generated `2026-07-01T17:22:11.561Z`):

| Section | Result | Detail |
|---|---|---|
| `0_dependencies` | pass (all 5) | `MGMT-LOAD-001` through `MGMT-LOAD-005` read as `done` from archive |
| `1_bundle` | pass (both) | initial management JS gzip `269474` <= `819200`; Evidence chunk gzip `13345` <= `153600` |
| `2_route_timing` | **fail** | first row/empty-state visible `4668 ms` > budget `2500 ms` |
| `3_startup_requests` | **fail** | non-primary BFF startup requests observed `5` (`/bff/me,/bff/approvals,/bff/alerts,/bff/jobs,/bff/jobs`) > budget `2`; duplicate `/bff/jobs` observed `2` (`1` duplicate) > budget `0` |
| `4_bff_fanout` | **fail / missing** | `/health` p95 `1328 ms` > `200 ms`; `/bff/management/evidence` p95 `1423 ms` > `750 ms`; `/bff/management/shell-summary` p95 `n/a` (missing) |

The route-timing/startup-request/BFF-fanout numbers are **identical to the
`MGMT-LOAD-001` pre-fix baseline** recorded in
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md`
and `route-load-baseline-2026-07-01.md`. That is expected: those sections are
the only archived route-timing/waterfall/fanout evidence available, and it
predates the merged `MGMT-LOAD-002/003/005` fixes. The gate is doing its job —
it fails closed instead of reporting a false pass — but this evidence must
**not** be read as "the fixes did not work." It must be read as "no fresh
hosted probe has been run against the fixed FE/BFF pair yet."

`MGMT-LOAD-006`'s own closeout doc records the exact next step and the exact
blocker: a fresh hosted route-load + BFF-fanout probe
(`npm run probe:route-load && npm run probe:bff:fanout` from
`execute-plans`, then re-run `scripts/aggregate-release-gate.mjs`) requires a
hosted-dev-BFF bearer token this worker lane does not have standing
authorization to source, so it is explicitly deferred to `MGMT-LOAD-007` or an
operator with that access.

## BFF Query Gap State (carried forward, unchanged)

The BFF-side implementation and test evidence from the base packet and
Follow-Up 2 remain accurate and were re-verified in this pass:

```bash
python3 -m pytest \
  services/control-plane/bff/test_mgmt_load_002_shell_summary.py \
  services/control-plane/bff/test_mgmt_load_005_read_concurrency.py \
  scripts/test_aggregate_release_gate.py -q
```

Result: `20 passed, 8 warnings` (12 BFF shell-summary/read-concurrency cases +
8 release-gate cases). Warnings are the pre-existing FastAPI `on_event`
deprecation warnings, unrelated to this change.

No new BFF route or contract changes are in scope for this follow-up.

## Frontend Handoff State

`MGMT-LOAD-003`'s closeout is now the authoritative frontend absorption
boundary; the base packet's `TopBar`/`JobProgressDrawer`/`NotificationCenter`
expectations are satisfied by the merged `routePrimaryReady.ts` gating:

- `TopBar` consumes `/bff/management/shell-summary` for first-paint counts and
  defers full approvals/alerts reads until after route-primary-ready + idle.
- `JobProgressDrawer` background hydration waits for route-primary-ready +
  idle; explicit user-initiated open still hydrates immediately (unchanged,
  intentional).
- `e2e/23-management-shell-fanout.spec.ts` hard-asserts the pre-marker budget
  (`/bff/me`, `/bff/management/shell-summary` only), not just a soft warning.

Residual note carried forward: `e2e/22-management-evidence-load.spec.ts` still
retains the `MGMT-LOAD-001` **soft warning** that baseline non-primary requests
exceed 2, per `MGMT-LOAD-003`'s own validation log. That is consistent with
`MGMT-LOAD-006`'s hard gate failing on the same stale baseline — both point at
the same missing artifact: a fresh hosted probe run.

## Parent Closeout Ledger For `MGMT-LOAD-007` (updated)

| Row | Current closeout use |
|---|---|
| `MGMT-LOAD-001` | Before-state route-load and BFF fanout baseline; still the only archived timing/fanout evidence. |
| `MGMT-LOAD-002` | BFF shell-summary/jobs route contract evidence; hosted p95 proof remains downstream. |
| `MGMT-LOAD-003` | **Closed.** FE gating on route-primary-ready + idle merged (execute-plans `#136`, Pantheon `#2705`, `#2709`). Owner-finalized by Codex2. |
| `MGMT-LOAD-004` | Hosted route-split timing precedent: first row/empty state p75 `931 ms`, p95 `1203 ms` on execute-plans commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80`. |
| `MGMT-LOAD-005` | BFF read-isolation implementation + local before/after proof; hosted post-merge fanout rerun still needed. |
| `MGMT-LOAD-006` | **Gate implemented and tested** (`scripts/aggregate-release-gate.mjs`, 8 passing unit cases, wired to bundle-budget CI via execute-plans `#138`). Real run against currently available evidence reports `pass: false` for the specific, documented reason above — not a placeholder and not silently green. |
| `MGMT-LOAD-007` | Must run the fresh hosted route-load + BFF-fanout probe (or obtain it from an operator with token access), re-run the gate against that fresh evidence, and only then archive final exact artifact paths, PR SHAs, deployed evidence, and residual risks for `MGMT-GAP-006`. |

## Reconciliation Ask For The Parent Owner

Before `MGMT-GAP-010` closes, the parent owner should:

1. Run `AI_NAME=<owner> ./scripts/ai-status.sh progress|done` for
   `MGMT-LOAD-001`, `MGMT-LOAD-002`, `MGMT-LOAD-003`, `MGMT-LOAD-004`, and
   `MGMT-LOAD-005` so `ai-status.json` matches the archived `done` state each
   already reached. This sidecar cannot do this itself — closeout status
   transitions belong to each task's own owner, and this task's scope is a
   support handoff packet, not the child tasks themselves.
2. Confirm `MGMT-LOAD-006`'s two merged PRs (`#2711`, `#2712`) are reflected in
   its own status record, with the `pass: false` reason preserved (not
   silently marked passing).
3. Ensure `MGMT-LOAD-007` sources the bearer-token-gated hosted probe rerun
   before claiming the parent gate is green, per the explicit deferral already
   recorded in `MGMT-LOAD-006`'s closeout doc.
4. Hand `MGMT-GAP-006` the exact `release-load-gate-*`/`release-*` artifact
   paths listed in `MGMT-LOAD-006`'s doc, once regenerated from a fresh hosted
   run.

## Do Not Infer

Do not infer any of the following from this sidecar:

- `MGMT-GAP-010` is complete.
- `MGMT-LOAD-006`'s `pass: false` result means the shell-summary, read-isolation,
  or shell-fanout fixes are broken — the failure is against stale pre-fix
  baseline evidence, documented as such in `MGMT-LOAD-006`'s own closeout.
- A fresh hosted probe exists yet. It does not; it is explicitly deferred.
- `ai-status.json` being stale for `MGMT-LOAD-001` through `MGMT-LOAD-006`
  means the underlying work is undone — the doc/PR evidence shows it is
  merged; only the status-tracking step was skipped.
- This packet moves `MGMT-GAP-010` or any `MGMT-LOAD-*` task to `done`.

## Reviewer Handoff

Claude should review this support packet for:

1. Sidecar scope: support artifact only, no canonical/runtime/frontend changes.
2. Ledger accuracy against the current status/archive/PR state (all cited
   commit SHAs and PR numbers were checked with `git show`/`gh pr view` in this
   pass).
3. Whether the "why `MGMT-LOAD-006` is not green" explanation is precise
   enough to prevent the parent owner from misreading a documented,
   expected fail as a regression.
4. Whether the reconciliation ask is scoped correctly (asking the parent/child
   owners to run status commands, not this sidecar performing those
   transitions itself).

If approved, the parent owner can absorb this ledger into the main load-gap
closeout path. This packet itself should not move `MGMT-GAP-010` or any
`MGMT-LOAD-*` execution task to `done`.

## Verification For This Sidecar

This sidecar changes no runtime or frontend code. Verification performed:

```bash
git status --short
git branch --show-current
git merge-base --is-ancestor origin/dev HEAD && echo up-to-date-with-dev
git diff --check
python3 -m pytest \
  services/control-plane/bff/test_mgmt_load_002_shell_summary.py \
  services/control-plane/bff/test_mgmt_load_005_read_concurrency.py \
  scripts/test_aggregate_release_gate.py -q
gh pr view 2709 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit
gh pr view 2711 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit
gh pr view 2712 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit
```

Results:

- `git status --short`: clean before this packet was added.
- Branch is up to date with `origin/dev` tip (`65ba4685b...`, matches the
  `MGMT-LOAD-006` PR `#2712` merge commit).
- `git diff --check`: passed.
- Focused suite: `20 passed, 8 warnings` (BFF shell-summary + read-concurrency
  + release-gate unit tests).
- PR `#2709`, `#2711`, `#2712`: all `MERGED` into `dev` at the SHAs cited
  above.

## Not Changing

This sidecar intentionally does not:

- change L1 canonical documents
- change BFF routes, tests, or the release-gate script
- change frontend code
- update any `MGMT-LOAD-*` or `MGMT-GAP-010` task status
- claim `MGMT-GAP-010` or `MGMT-LOAD-006` is done
