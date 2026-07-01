# MGMT-GAP-005 - Studios And Capability Surfaces To Production Level

Owner: Gemini
Reviewer: Claude
Batch: 4
Fleet lane: runtime worker operations and capability integration
Depends on: `MGMT-GAP-003`

## Problem

Formula Studio and Skill Sandbox are still mounted as first-level management
surfaces, but the current source identifies them as mock backtest and mock
execute flows. Tools/MCP/Skills also expose create-style controls while the live
registries are often empty or degraded.

The 2026-07-01 route/control re-audit also found 10 mock-visible routes or
details, including Evidence demo/readiness paths, Alpha Factory, loop execution,
seed strategy/capital/rebalance detail ids, and alias variants. Those routes are
not all owned by this task, but this task owns the capability/studio decision:
real runner/job/readback evidence, or demotion from production operations.

## Scope

- Decide for Formula Studio:
  - wire a real backtest job/readback contract; or
  - demote it from first-level nav until a runner exists.
- Decide for Skill Sandbox:
  - wire a real skill-runner trace/readback contract; or
  - demote it from first-level nav until a runner exists.
- For Tools/MCP/Skills:
  - expose admission/import/publish/retire only when a governed command exists;
  - otherwise disable the action with explicit non-production state.
- For Alpha Factory and demo/seed capability routes:
  - demote or fixture-gate mock lanes when they cannot produce live discovery,
    scaffold, runner, or registry readback evidence.

## Non-Scope

- Do not fabricate deterministic mock backtest curves as production evidence.
- Do not run real external tool calls without governed sandbox limits.
- Do not keep a page first-class only because it has a polished scaffold.

## Acceptance

- Hosted probe cannot produce a mock trace/backtest labeled as live success.
- Capability actions return governed command/job ids or are disabled.
- Runtime runner contracts have tests and documented limits.
- Nav placement matches production readiness.
- Mock-visible surfaces from the supplemental route/control audit are either
  hidden/demoted, fixture-gated, or backed by a real BFF/runtime readback path.
