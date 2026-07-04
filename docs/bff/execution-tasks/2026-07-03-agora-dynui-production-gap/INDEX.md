# Agora Dynamic UI Production Gap Execution Packet - 2026-07-03

Status: dispatchable production-gap tasks

Source audit:

- `docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md`

Dispatch command:

```sh
python3 scripts/dispatch_agora_dynui_production_gap_2026-07-03.py
```

The dispatch script is idempotent. It creates or refreshes the task set below,
preserves progress fields for tasks already started, and assigns owner/reviewer
pairs for fleet execution.

Do not run `python3 scripts/ai_status.py sync` for this packet until the board
sync pruning behavior is repaired. During this audit, sync incorrectly removed
unrelated active task rows from the local generated board.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `AG-DYNUI-PROD-001` | Codex | Claude | Restore source/task truth and archive continuity. |
| 1 | `AG-DYNUI-PROD-002` | Claude | Codex | Rework Agora into an intentional standalone workbench shell. |
| 1 | `AG-DYNUI-PROD-003` | Claude2 | Codex | Make default Trading Room URL enter a dynamic design-pack workflow. |
| 1 | `AG-DYNUI-PROD-004` | Codex2 | Claude | Add production-grade error diagnostics and stale-bundle recovery. |
| 2 | `AG-DYNUI-PROD-005` | Claude | Codex2 | Close the proposal/grid/revision/version/rollback dynamic workflow. |
| 3 | `AG-DYNUI-PROD-006` | Codex | Claude2 | Hosted E2E and publish gate for production-level closeout. |

## Global Rules

- Do not rebuild from imagination.
- Do not downgrade the design pack to static screenshots or hardcoded mock
  cards.
- Do not bypass BFF auth, scope isolation, widget allowlists, or validators.
- Do not close a task from local-only validation.
- If the original design zip or a required spec is missing, raise a blocker
  with the exact missing file and continue only on non-conflicting work.
- Repo changes require branch, commit, PR, checks, merge, and post-merge live
  evidence.

## Required Closeout Evidence

- Worker branch and PR URL.
- Local validation command summary.
- Reviewer approval.
- Merge commit SHA.
- Relevant CI/check URLs.
- Dev FE deploy URL and result for execute-plans changes.
- Hosted browser probe and screenshots for `/agora/trading-room`.
- Explicit statement whether the default route is still embedded in
  PlatformShell.
- Residual risks with owner and expiry.
