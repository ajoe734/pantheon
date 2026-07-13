# MGMT-SSE-001 — integration-gate triage (2026-07-13)

execute-plans PR #300 (`task/MGMT-SSE-001` -> `dev`) went `BEHIND` twice
during closeout wait. Both times unstuck with a non-force `git merge
origin/dev` on the task branch (ad206b2 -> 7f13fa3 for the second
round).

The integration-gate run on ad206b2 failed on:

- `Live deep: SSE long reconnect has no duplicate replay` — reconnect
  attempt got `502` from the hosted BFF gateway. Reviewed
  `src/lib/bff-v1/sse/liveSse.ts` reconnect/backoff/Last-Event-ID logic
  directly; it is correct. Transient hosted-gateway failure, not a
  regression from this diff.
- `25-persona-fleet-live-linked-pages.spec.ts` (ranking table showed
  `無資料`) and `agora-winner-branch-hosted.spec.ts` (rollback button
  click intercepted by the event queue overlay) — both outside this
  task's scope (`liveSse.ts`, `agora/workshops.ts`); pre-existing
  hosted-environment flakes unrelated to the SSE auth transport change.

No code change made in response; re-merged dev and let the gate rerun.
