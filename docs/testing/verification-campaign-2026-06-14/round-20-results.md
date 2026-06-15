# Round 20 — Results

**Executed:** 2026-06-15 (UTC).

## H1 — regression consolidation: PASS

All campaign regression test files run together on latest `dev`:

```
test_incident_stream_route_order.py          (F3)
test_persona_league_detail_not_found.py      (F5 + ErrorCode guard)
test_route_resolution_no_shadowing.py        (F9)
test_idempotency_concurrency_guard.py        (F11 concurrency)
test_audit_timestamp_filter_no_500.py        (F12)
test_no_undefined_call_symbols.py            (F12 generalization guard)
test_runtime_auth_inbound_attack_matrix.py   (F10/JWT)
.orchestrator/test_task_archive_index_legacy_id.py  (F8)
→ 25 passed in 17s
```

(The Phase-1 OODA-card test for F2 lives in the pre-existing
`test_mgmt_ooda_005_control_room_card.py`, also green.) The fixes are mutually
consistent and did not regress one another.

## H2 — live dev health: PASS (with deploy-lag note)

`/health` 200, `/readyz` 200, OpenAPI 447 paths, `/bff/v5/control-room` 200.

**Deploy-lag (same pattern as F6):** `/bff/audit/events?from_ts=-1` still returns
500 on **live**, because the running BFF image predates the F12 merge. The fix
is in `dev` and verified in-process; closing the live gap needs a BFF redeploy
(OPS). The Phase-1 fixes (F2/F3/F5) are in the same state — merged to `dev`,
awaiting the next BFF deploy to take live effect.

## Net

Phase 2 is internally consistent and green on `dev`. The live environment is
healthy; the merged BFF code fixes (F12 this phase; F2/F3/F5 from Phase 1) will
take live effect on the next BFF redeploy. See `SUMMARY-PHASE2.md`.
