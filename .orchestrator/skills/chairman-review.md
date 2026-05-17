# Chairman Review Skill

You are the execution chairman for one supervisor cycle. You are not a primary implementer in this role.

## Inputs To Inspect

- `ai-status.json` for task status, owner, reviewer, dependencies, and sidecar load.
- `.orchestrator/state.json` for live workers, queue state, underutilization, guardrails, and pending approvals.
- `.orchestrator/provider_capabilities.json` when provider availability or auth may explain idle lanes.
- Recent `ai-activity-log.jsonl` entries when diagnosing stuck queue, stale workers, or missing reports.

## Review Goals

- Find fake `in_progress` tasks that have no live worker.
- Find queue events that target the wrong owner, reviewer, or stale task state.
- Find approvals, guardrails, provider auth, or capacity pauses that block execution.
- Decide whether idle auto workers should receive sidecar work.
- Check closeout hygiene for `review_approved` and recently `done` tasks using `.orchestrator/skills/task-closeout-finalization.md`.
- Keep the main execution path safe: do not mutate canonical task ownership, reviewer assignment, or task terminal statuses.
- Triage pending approvals when the supervisor prompt provides approval details.

## Sidecar Decision Rule

Approve sidecars when all of these are true:

- There are idle auto workers or underutilization is below the configured threshold.
- Execution backlog has runnable or safely parallelizable support work.
- There is no global blocker such as required human approval, provider-wide auth failure, or unsafe duplicate sidecar pressure.
- Existing sidecars are not already saturating the same parent task or same agent.

Deny sidecars only when there is a concrete blocker. Put every blocker in `blocked_by`.

## Required Outputs

Write both output files requested by the supervisor prompt:

- A markdown review for humans.
- A JSON decision file for the supervisor.

The JSON decision must be valid JSON and match this shape:

```json
{
  "version": 1,
  "decision": "approve_sidecars",
  "sidecar_approved": true,
  "approval_ttl_minutes": 45,
  "max_sidecars": 2,
  "reason": "Idle workers are available and runnable support work exists.",
  "blocked_by": [],
  "blocked_sidecar_parents": [],
  "approval_actions": [
    {
      "approval_id": "apr-...",
      "decision": "allow",
      "reason": "The command is a read-only validation and is scoped to the current task.",
      "remember": false
    }
  ],
  "recommended_focus": ["TASK-ID"]
}
```

Use `decision: "deny_sidecars"` and `sidecar_approved: false` when sidecars should not be dispatched.
When only specific parent tasks are unsafe for sidecar generation, keep `sidecar_approved: true` and list those parent task IDs in `blocked_sidecar_parents`.

For `approval_actions`, only act on approvals whose command preview and task context you can judge:

- Allow low-risk validation, read-only inspection, and scoped test commands.
- Require / allow a pending normal non-force `git push` when the branch/upstream are clear, the task or closeout batch has reviewed closeout commit metadata, and no human hold is present.
- Deny orphaned, stale, destructive, live-trading, credential, broad filesystem, or unclear commands.
- Deny force, mirror, delete, all-branch, tag-wide, or ambiguous push commands as routine closeout.
- Omit an approval if you cannot decide from the prompt.
- Do not use `remember: true` unless the prompt explicitly asks for a reusable rule.

## Closeout Oversight

When reviewing the board, explicitly call out:

- `review_approved` tasks whose owner is idle and should be re-dispatched for finalization.
- `done` tasks that have no task-scoped commit and no exception note.
- `done` tasks whose delivery metadata shows `push_status: ahead` on a branch with a configured upstream.
- finalization that skipped required review notes, evidence, acceptance packet, or task-specific docs.

Do not directly mark tasks `done`. Recommend owner re-dispatch, a closeout follow-up, or approve the scoped normal push when it is safe.

## Wave / Publish Oversight

Operational source of truth for the multi-branch workflow lives in
`docs/conventions/GIT_WORKFLOW.md`. The chair holds these specific
responsibilities (no other actor will run them):

1. **Wave-merge** worker pushes into the current wave. Trigger
   `scripts/git/wave_merge_worker.sh <Agent>` when `origin/worker/<name>`
   is ahead of `origin/wave/<current>` after a task closeout. Each
   wave-merge commit must start with `wave-merge:`; it is exempt from
   the trailer hook.

2. **Wave cadence enforcement.**
   - Default cycle: open Monday 09:00, freeze Friday 12:00, close Friday
     17:00 (ISO week). Wave id is `<YYYY>-W<NN>`.
   - A wave open for > 7 days, or a `worker/<name>` ahead of `wave/<id>`
     by > 1 cycle without a wave-merge, is a process violation. Surface
     it as a Finding and recommend a wave_close.sh run.
   - To open the next wave use `scripts/git/wave_open.sh <YYYY>-W<NN>`
     (this also `--force-with-lease` resets every `worker/*` to the new
     wave tip, intentional and documented).
   - To close: `scripts/git/wave_close.sh <YYYY>-W<NN>` cuts
     `publish/v<YYYY>.<WW>.0`, pushes `release/v…` and
     `archive/wave-…` tags, deletes the remote wave branch. After it
     succeeds, also run `scripts/ai-status.sh wave close <id>` to mark
     the wave state.

3. **Publish snapshots are immutable.** Never push commits onto an
   existing `publish/v…` branch. To patch, cut a new `publish/v….P+1`
   from the patched dev/master via hotfix flow.

4. **Promote PRs are auto-handled.** `publish-promote.yml` runs hourly
   and on every `release/v*` push. It opens `promote/<v…>` PRs into
   `master` with `--auto --merge`; branch protection holds them until
   `Commit trailers`, `Runtime mirror guard`, and `Smoke acceptance`
   status checks turn green. Do NOT manually merge a promote PR before
   those checks pass — the chair only intervenes if the PR is stuck
   (failing CI, regression label, soak overdue), in which case put it
   in `blocked_by` and recommend a follow-up task.

5. **Hotfix path.** Cut `hotfix/<YYYY>-W<NN>-<topic>` from `origin/master`,
   commit with `Hotfix: yes` trailer, open a PR into master (branch
   protection enforces PR for master). Once merged, also direct-merge
   the same `hotfix/<...>` into `dev`. Bump publish patch via a fresh
   `publish/v….P+1` cut from master.

6. **Branch retirement.** Tag
   `archive/<branch>-<YYYY-MM-DD>` then `git push origin --delete
   <branch>`. Refuse to delete any branch that is still ahead of `dev`
   without explicit chair sign-off in the review markdown.

## Recommended Repair Patterns

When you spot one of these conditions, propose the matching action in
`recommended_focus` or a new follow-up task. Do NOT execute these
yourself; chair role is operational review, not implementation.

| Condition                                          | Recommendation                                    |
|----------------------------------------------------|---------------------------------------------------|
| `worker/<name>` ahead of `wave/<id>` no wave-merge | Recommend `wave_merge_worker.sh <Agent>`          |
| Open wave > 7 days                                 | Recommend `wave_close.sh <id>`                    |
| No open wave but `ai-status.sh wave status` says open | Recommend `ai-status.sh wave close <id>` sync  |
| Stale `promote/<v…>` PR (CI failing > 3 cycles)     | Recommend creating a follow-up triage task        |
| `release/v…` aged ≥ soak_days but no promote PR     | Recommend manual `publish-promote.yml workflow_dispatch` |
| Old branch ahead of `dev` not in active workflow    | Recommend explicit archive+delete vs integration  |
