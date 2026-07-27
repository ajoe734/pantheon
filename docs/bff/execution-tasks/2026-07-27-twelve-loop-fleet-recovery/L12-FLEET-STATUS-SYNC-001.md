# L12-FLEET-STATUS-SYNC-001 — Stop stale supervisor status/source_ref regressions

Owner: Codex
Reviewer: Codex2
Parallel group: wave-0-control

Repair the class of bug observed during #4273: a newer pushed PR head
`141d06ec...` was visible on GitHub, but live status returned to older
`f6d340ff...` and `in_progress` after supervisor dispatch sync.

Acceptance:

- Add a regression that simulates a newer PR-backed task row followed by stale
  dispatch sync.
- The regression must fail before the fix and pass after the fix.
- `ai-status.json` and `.orchestrator/state.json` must converge.
- No `.orchestrator/config.json` edit is allowed for this repair.
