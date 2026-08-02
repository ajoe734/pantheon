# Task Brief: OPS-CODEX-CHATBOX-ROUTING-RULES-OPERATOR-V4-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Land account-neutral single-dispatch chatbox governance
- Status: in_progress
- Owner: Codex
- Reviewer: Human/Ops
- Base: `origin/dev` at `79e02ee059387044eec1d21a283e4848f814f49a`
- Branch: `task/OPS-CODEX-CHATBOX-ROUTING-RULES-OPERATOR-V4-20260802`
- Next: Publish the corrected replacement PR, retire stale PRs #4401 and #4405
  with links, then obtain Human/Ops exact-head approval.

## Summary
Land the useful single-supervisor dispatch and chatbox coordination rules while removing the stale hardcoded Codex/Codex2 shared-account and review-disqualification claim.

## Authoritative Operator Constraint

- `Codex` and `Codex2` are distinct configured agent identities and may use
  different accounts.
- Account relationships, quota grouping, capacity, and reviewer eligibility
  follow current configuration plus task-scoped live authentication/quota
  evidence.
- Agent names alone must never create a global identity equivalence, quota
  collapse, capacity collapse, or reviewer disqualification.

## Owned Scope

- `AGENTS.md`: repository-level chatbox classification and dispatch authority.
- This task brief: task scope, source decisions, validation, and handoff record.
- `docs/reviews/ops-codex-chatbox-routing-rules-operator-v4-20260802.md`:
  deterministic assertions and independent exact-head review manifest.

Not changing: supervisor code, config, provider policy, canonical generated
JSON, runtime state, product code, services, deployment, or the 28-task catalog.

## Required Governance

1. Direct implementation is limited to an explicitly operator-authorized,
   bounded component repair in a clean governed worktree and full PR flow.
2. Cross-component, supervisor, fleet, architecture, release, and multi-task
   work is integration/coordination: inspect read-only, deduplicate, plan, split
   governed packets, queue through governed commands, wait for supervisor
   receipt/materialization, and monitor without implementation takeover.
3. Codex extension subagents may support read-only exploration and review in
   that lane, but may not become a parallel implementation fleet.
4. The Pantheon supervisor is the only routine implementation dispatcher.
5. Ordinary coordination may not patch
   `/home/lupin/pantheon-ci-deploy/dev-root`, force rescue refs, edit canonical
   runtime state, or start/stop services.
6. The existing urgent Live Repair Rule remains the narrow exception; the
   permanent fix still uses the complete governed repository flow.
7. Configured agent identities remain distinct. Account/quota relationships
   and reviewer eligibility come from current configuration and task-scoped
   live evidence, never a global agent-name rule.

## Replacement And Review Gates

- PR #4401 is a stale policy proposal because its final paragraph hardcodes an
  account relationship and reviewer disqualification. Do not merge that patch.
- PR #4405 publishes evidence for the same stale proposal and is superseded by
  this task's corrected task brief and review manifest.
- Close both stale PRs with links only after the corrected replacement PR
  exists. Preserve their branches and history.
- Evidence remains `review_pending` until Human/Ops reviews the replacement PR
  exact head. Green CI is necessary but does not replace that decision.
- Merge target is `dev`; rollback is a revert of the replacement PR merge
  commit.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Validation Anchor (2026-08-02)

- The account-neutral policy, this brief, and the pending review manifest were
  made durable at anchor commit
  `805b8b7003162197904dbf65768f53417705cc6a`.
- The branch was created from and remains based on current `origin/dev`
  `79e02ee059387044eec1d21a283e4848f814f49a`; the exact diff contains only the
  three declared artifacts.
- `git diff --check origin/dev...HEAD` and worktree `git diff --check` passed.
- The manifest's executable policy check passed with 29 required retained
  fragments and 6 forbidden stale/global-rule fragments.
- `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD
  --skip-merge` passed for the anchor, and the runtime-mirror guard inputs found
  no generated-state or embedded frontend violation.
- PRs #4401 and #4405 were confirmed open. They remain untouched until the
  corrected replacement PR is published, per the supersession gate.
