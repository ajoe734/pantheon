# Twelve-Loop Gap Closeout Execution Tasks

Program: `pantheon-twelve-loop-gap-2026-07-26`

Audit packet:
`docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728`

This directory is a dispatch-facing mirror of the 2026-07-28 three-pass gap
audit. The `12:08Z` packet is historical; the current dispatch truth is the
post-#4300 `16:12Z` refresh. The canonical graph is:

`docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728/execution-tasks.json`

## Parallel Frontiers

| Frontier | Tasks |
| --- | --- |
| Wave 0 closeout | `L12-CLOSEOUT-EVIDENCE-RECON-20260728`, `L12-FLEET-STATUS-SYNC-CLOSEOUT-20260728` |
| Wave 0 BFF | `L12-BFF-REPAIR-ACCEPTANCE-20260728` |
| Wave 0 provider | `OPS-L12-PROVIDER-FIRST-READINESS-20260728` |
| Wave 1 manifest | `L12-MANIFEST-001` |
| Wave 2 truth | `L12-TRUTH-001`, then `L12-FE-TRUTH-001` |
| Wave 3 product verifiers | `L12-VERIFY-KNOW-001`, `L12-VERIFY-LEARN-001`, `L12-VERIFY-RUNTIME-001`, `L12-VERIFY-OBS-001` |
| Wave 4 hosted | `L12-HOSTED-001` |
| Wave 5 final | `L12-CLOSE-001` |

## Dispatch Notes

- Use real supervisor/auto-worker dispatch only.
- Do not use Codex conversation subagents for this program work.
- Do not edit `.orchestrator/config.json` to fake provider priority.
- Encode Claude/Antigravity as preferred lanes where appropriate, but treat
  supervisor fail-closed reassignment as authoritative if quota/auth/timeout
  makes those lanes unavailable.
- Do not restart implementation for already merged tasks. Close them out from
  exact merged delivery evidence.
- `L12-DIST-001` and `L12-FLEET-WORKER-OUTCOME-001` need reconcile-safe
  evidence updates before `reconcile_merged_done` can archive them.
- `L12-BFF-001` needs real implementation repair and proof drills; do not mark
  it done from PR #4274 alone.
