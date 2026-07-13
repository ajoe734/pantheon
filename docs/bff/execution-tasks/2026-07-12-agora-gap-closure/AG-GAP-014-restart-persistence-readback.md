# AG-GAP-014: Live restart-persistence readback for Agora Postgres stores

## Scope

Evidence-closeout task. AG-GAP-002 (trading_room), AG-GAP-003 (research), and
AG-GAP-004 (dashboard recipes) merged durable Postgres stores, but each
closeout explicitly deferred the hosted restart proof:

- AG-GAP-002: "live Postgres restart proof remains an explicit deployment
  environment gate."
- AG-GAP-003: "The hosted restart proof is intentionally post-merge ...
  before moving this task to done." (The task was archived done anyway.)
- AG-GAP-004: "Deployment acceptance still requires creating/editing a recipe,
  restarting the BFF container, reading it back."

This task records those three proofs on the dev deployment. It changes no
runtime code. If any proof fails, file the failure as a blocker on this task
and do not patch code here — a fix becomes its own task.

## Work

On dev (`pantheon-lupin-dev`, BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`):

1. Confirm the running `operator-bff` container has
   `AGORA_TRADING_ROOM_STORE_BACKEND=postgres`,
   `AGORA_RESEARCH_STORE_BACKEND=postgres`, and
   `AGORA_DASHBOARD_STORE_BACKEND=postgres` (plus DSNs) and that startup logs
   show `backend=postgres` for all three, credential-safe.
2. Trading room: with a scoped operator session, generate and accept a
   workspace proposal, capture the ETag; restart `operator-bff`; read the
   workspace and its version history back with the same ETag lineage.
3. Research: create a plan (draft -> approve -> run) and a candidate pool with
   one score and one member review; restart; read both aggregates back
   unchanged.
4. Dashboard: create/edit a recipe, perform one rollback; restart; read back
   the recipe with intact version history (rollback appended, not overwritten).
5. Archive authenticated request/response transcripts and container log
   excerpts under `docs/deployment/evidence/ag-gap-014/`, one file per store,
   plus a summary INDEX.

## Acceptance

- All three restart proofs pass and are archived with request ids, timestamps,
  and the deployed BFF SHA.
- Backend env/log confirmation recorded for all three stores.
- No code changes in this task; evidence PR merged to dev.
- Any failed proof is recorded as a blocker with the exact failing step, not
  silently retried into a pass.

## References

- `docs/bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-002-trading-room-postgres.md`
- `docs/bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-003-research-postgres.md`
- `docs/bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-004-dashboard-postgres.md`
- AG-GAP-001 live evidence pattern: GitHub Actions run 29196187981
