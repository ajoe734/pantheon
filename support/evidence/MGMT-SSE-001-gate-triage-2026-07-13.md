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

## Round 3 (integration-gate run 29268056108, on 7f13fa3)

Same two pre-existing hosted flakes recurred, confirming they are not
transient one-offs but a persistent unrelated-to-this-task condition:

- `25-persona-fleet-live-linked-pages.spec.ts:33` — failed + retry, both
  chromium and mobile-chromium.
- `agora-winner-branch-hosted.spec.ts:240` (AG-DYNUI-FULL-006 rollback) —
  failed + retry, both chromium and mobile-chromium.

`F14 SSE reconnect` (this task's own domain) passed 4/4. Neither failing
spec touches `liveSse.ts` or `agora/workshops.ts`.

Separately, PR #300 had drifted `BEHIND` dev again (+18 commits, incl.
AG-UIPOL-001/004 i18n and Agora component changes touching
`src/agora/**`, `src/lib/bff-v1/management.ts`, `src/lib/bff-v1/paths.ts`).
Merged `origin/dev` non-force (7f13fa3 -> 20f6b9e), clean, no conflicts,
and pushed to `task/MGMT-SSE-001`. `mergeStateStatus` moved from `BEHIND`
back to `MERGEABLE`/`BLOCKED` (checks re-running on 20f6b9e).
