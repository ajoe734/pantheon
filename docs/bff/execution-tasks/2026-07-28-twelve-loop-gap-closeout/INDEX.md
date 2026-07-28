# Twelve-Loop Gap Closeout Execution Tasks

Program: `pantheon-twelve-loop-gap-2026-07-26`

Audit packet:
`docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728`

This directory is a dispatch-facing mirror of the 2026-07-28 three-pass gap
audit. The canonical graph is:

`docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728/execution-tasks.json`

## Parallel Frontiers

| Frontier | Tasks |
| --- | --- |
| Immediate | `L12-GAP-MERGE-QUEUE-20260728`, `OPS-L12-PROVIDER-FIRST-READINESS-20260728` |
| Closeout | `L12-GAP-CLOSEOUT-RECONCILE-20260728` |
| Activation/truth | `L12-MANIFEST-001`, `L12-TRUTH-001`, `L12-FE-TRUTH-001` |
| Product verifiers | `L12-VERIFY-KNOW-001`, `L12-VERIFY-LEARN-001`, `L12-VERIFY-RUNTIME-001`, `L12-VERIFY-OBS-001` |
| Hosted/final | `L12-HOSTED-001`, `L12-CLOSE-001` |

## Dispatch Notes

- Use real supervisor/auto-worker dispatch only.
- Do not use Codex conversation subagents for this program work.
- Do not edit `.orchestrator/config.json` to fake provider priority.
- Prefer Claude and Antigravity only when live readiness is proved by the
  supervisor; otherwise keep healthy real workers draining the DAG and record
  provider blockers truthfully.
- Do not restart implementation for already merged tasks. Close them out from
  exact merged delivery evidence.

