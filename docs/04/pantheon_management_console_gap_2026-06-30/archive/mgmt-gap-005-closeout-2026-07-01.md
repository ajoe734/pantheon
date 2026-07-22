# MGMT-GAP-005 Closeout Evidence - 2026-07-01

Status: implementation merged in `execute-plans`; Pantheon lifecycle closeout
is pending canonical status sync.

Owner closeout actor: Codex2
Expected reviewer in Pantheon task board: Claude
Reviewer trailer in implementation commits: Claude2

## Scope Closed

The delivered implementation chooses the demotion/fail-closed path rather than
adding new Pantheon BFF runner endpoints. Formula Studio, Skill Sandbox, and the
Tools/MCP/Skills capability surfaces no longer present local mock execution as
production success when no governed command, job id, runner trace, or readback
contract exists.

## Merged Frontend Delivery

- Repository: `ajoe734/execute-plans`
- PR: `https://github.com/ajoe734/execute-plans/pull/129`
- PR title: `MGMT-GAP-005: harden studios and capability truth gates`
- Base: `dev`
- Merged at: `2026-07-01T07:00:52Z`
- Merge commit: `9f846697f03c89e72216749ee9b39d0a849e80a8`
- Implementation commits:
  - `ad17ad05703c5495e630253acc8cd108d30cd92a` -
    `MGMT-GAP-005: anchor capability truth gates`
  - `d7cda93a8f12f4237242d65e16644fbe531df513` -
    `MGMT-GAP-005: retrigger integration gate`

Changed frontend files included Formula Studio, Skill Sandbox, Tools/MCP/Skills
detail panels, capability lists, and the focused regression
`src/management/pages/capabilitiesProductionTruth.test.ts`.

## Behavior Evidence

Confirmed from `execute-plans` `origin/dev` after PR #129:

- `FormulaBacktestChart.tsx` is removed from the production path.
- Formula Studio renders `Backtest runner unavailable` and uses
  `NonProductionActionButton` for the backtest action when no governed runner
  exists.
- Skill Sandbox renders `Skill runner unavailable`, disables execution, and
  does not render generated traces, token costs, or local live-success output.
- Tools, MCP, and Skills list pages pass
  `createBehavior={capabilityCreateDisabled}` and name live-empty registry
  states.
- Tool/MCP/Skill detail write paths use `NonProductionActionButton` and no
  longer route local-success paths through `runActionSafe`, `HighRiskConfirm`,
  or `toast.success` when command receipts are absent.

## Verification

Implementation PR validation:

- `npm run test -- src/management/pages/capabilitiesProductionTruth.test.ts`
- `npm run lint`
- `npm run build`
- GitHub check `Pantheon FE-BFF Integration Gate / integration-gate`: success
  on run `28499187881`

Closeout verification performed from this Pantheon worktree:

- `gh pr view 129 --repo ajoe734/execute-plans --json ...`
- `git -C /home/lupin/code/execute-plans show origin/dev:src/management/pages/capabilitiesProductionTruth.test.ts`
- `git -C /home/lupin/code/execute-plans show origin/dev:src/management/pages/studios/FormulaStudio.tsx`
- `git -C /home/lupin/code/execute-plans show origin/dev:src/management/pages/studios/SkillSandboxStudio.tsx`
- `git -C /home/lupin/code/execute-plans show origin/dev:src/management/pages/CapabilitiesLists.tsx`

## Residuals

- This task does not add Pantheon BFF backtest or skill-runner endpoints. The
  UI is fail-closed until those governed contracts exist.
- The release gate summary for PR #129 was overall `WARN` because broader
  create dry-run and F10 rollback-saga checks remained warnings. Those are
  outside this task's capability truth gate and remain covered by the
  management gap acceptance/load lanes.
- Pantheon `ai-status.json` still showed `MGMT-GAP-005` as `todo` with owner
  `Gemini` after the frontend PR had merged, while the worker dispatch brief
  resumed Codex2 for `owned_finalize_dispatch`. The owner must not bypass the
  reviewer-only `review_approved` transition by directly editing status JSON.

## Closeout Gate

`scripts/ai-status.sh done MGMT-GAP-005` should run only after canonical task
state is synchronized to owner `Codex2` and `review_approved`, or after the
assigned reviewer records approval through the status tool.
