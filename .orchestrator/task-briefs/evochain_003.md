# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Independent re-review confirms all 7 remediation claims in support/reviews/EVOCHAIN-003-review-codex.md against the actual diff (dfb6628a3..7d031fb1f, PR #3699): row-scoped Postgres CAS with SELECT FOR SHARE + guarded snapshot restore (incident/pg_store.py, incident.py), CAS-leased monotonic delivery claim/reserve/applied-receipt flow with correct 503/200-replay semantics (foundation/reliable_delivery.py), prepared-intent repair never deletes a concurrent winner (incidents/main.py), postmortem consumer reuses real IDs + collision-resistant sanitization + expected_snapshot on draft merges (postmortems/consumer.py), published-terminal with fail-closed replay identity check (incident.py), control compose wiring added and postmortem_bridge.py verified zero-diff, legacy direct-close adoption scoped to the exact inert/unclaimed shape with 3-attempt drift-merge retry to 503. py_compile clean on all 7 target files; 116/116 non-DB tests pass; DB-dependent suites skip cleanly without TEST_DATABASE_URL (not independently re-run against live Postgres in this sandbox, trusted on owner's separately logged 5-passed run). Two non-blocking notes: postmortems-publish CAS-loss path has no analogous repair-intent call (asymmetry, not a violation of this task's scope), and ReliableOutboxStore.discard_prepared has no production caller. Approved.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
