# ASST-KERNEL-007 Repair Workflow Implementation

Status: implementation note for ASST-KERNEL-007

## Scope

Kernel repair mode now has a dedicated repository workflow guard in
`services/openclaw-gateway-adapter/assistant_repair_workflow.py`.  The guard is
called before the Codex CLI provider enters `workspace-write` mode.

The guard validates and records:

- task id and task worktree path;
- expected task branch, defaulting to `task/<TASK-ID>`;
- configured remote, defaulting to `origin`;
- merge target, defaulting to `dev`;
- current validation commit (`HEAD`);
- sanitized remote URL;
- declared repository scope;
- dirty and staged path state;
- optional PR metadata when closeout requires a PR.

## Required Metadata

`kernel_repair` provider requests must include:

```json
{
  "metadata": {
    "operator_id": "operator-id",
    "task_id": "ASST-KERNEL-007",
    "task_worktree": "/srv/pantheon-assistant/worktrees/task-ASST-KERNEL-007",
    "declared_scope": [
      "services/openclaw-gateway-adapter",
      "scripts/assistant_repair_smoke.py"
    ]
  }
}
```

Optional closeout validation can add:

```json
{
  "require_pr": true,
  "merge_target": "dev",
  "pull_request": {
    "number": 123,
    "url": "https://github.com/ajoe734/pantheon/pull/123",
    "state": "OPEN",
    "baseRefName": "dev",
    "headRefName": "task/ASST-KERNEL-007"
  }
}
```

If `require_pr=true` and no PR can be found or supplied, validation fails with
`REPAIR_PR_REQUIRED`.

## Guardrails

- `task_worktree` must be inside `PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT`.
- `task_worktree` must point at a git repository root.
- `HEAD` must be a named branch matching `expected_branch`.
- The configured remote must exist.
- A non-empty `declared_scope` is required.
- Staged files outside `declared_scope` fail with
  `REPAIR_STAGED_SCOPE_VIOLATION`.
- Dirty files outside `declared_scope` fail with `REPAIR_DIRTY_UNRELATED`.
- By default, any dirty worktree fails before provider execution.
- The command broker still denies destructive git commands in repair mode; the
  workflow guard does not grant direct shell authority.

## Smoke

Use the smoke helper to validate a concrete worktree:

```bash
python3 scripts/assistant_repair_smoke.py \
  --task-id ASST-KERNEL-007 \
  --worktree /srv/pantheon-assistant/worktrees/task-ASST-KERNEL-007 \
  --worktree-root /srv/pantheon-assistant/worktrees \
  --scope services/openclaw-gateway-adapter \
  --scope scripts/assistant_repair_smoke.py
```

Closeout PR validation:

```bash
python3 scripts/assistant_repair_smoke.py \
  --task-id ASST-KERNEL-007 \
  --worktree /srv/pantheon-assistant/worktrees/task-ASST-KERNEL-007 \
  --worktree-root /srv/pantheon-assistant/worktrees \
  --scope services/openclaw-gateway-adapter \
  --require-pr
```
