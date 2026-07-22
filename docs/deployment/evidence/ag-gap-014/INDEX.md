# AG-GAP-014 Evidence: Live restart-persistence readback (dev)

Date: 2026-07-13. Operator: Claude (interactive session), identity `op-e2e`
via dev session auth. BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`,
container `pantheon-operator-bff-1` on `pantheon-lupin-dev` (35.201.239.38).

## Verdict

- research (plans/runs) — PASS: survived two `operator-bff` restarts,
  byte-identical data, same ETag.
- candidate pools (score + member review) — PASS: same.
- dashboard recipes (accept/layout/rollback, v1→v4) — PASS: same.
- trading room workspaces — **FAIL on first attempt, PASS after fix**:
  workspace `trw_3e141c70552a` returned 404 after restart at
  2026-07-12T11:52:53Z probe. Root cause: PR #3443's merge commit
  `31094a79a` silently reverted `trading_room/store.py` to a pre-#3444
  snapshot, deleting `PostgresTradingRoomStore` — live env had
  `AGORA_TRADING_ROOM_STORE_BACKEND=postgres` while the factory ignored it.
  Fix: PR #3496 (merge `ebf24151f`) restored the store; deployed via
  Pantheon Nonprod Deploy run 29248174108 (success). Second proof:
  workspace `trw_4febf8e42846` survived restart at 12:03:17Z with
  identical ETag `"tr-workspace:trw_4febf8e42846:v2:6918fd5c"` and
  byte-identical payload; versions endpoint 200.

## Backend confirmation

- Container env: `AGORA_{WORKSHOP,TRADING_ROOM,RESEARCH,DASHBOARD}_STORE_BACKEND=postgres`
  with DSNs and schemas (`agora` / `agora_research`).
- Startup logs (post-fix, `30-postfix-startup-log.txt`): workshop,
  trading-room, and dashboard stores all log `backend=postgres` with their
  Postgres store classes; DSNs are not rendered. The research store has no
  init log line (gap noted for AG-GAP-003 follow-up); its durability is
  proven by data survival instead.

## Transcript map

- `01`–`16`: write phase (workshop, research plan/approve/run, candidate
  pool/score/review, trading-room proposal/accept/layout/rollback,
  dashboard proposal/accept/layout/rollback), request ids `aggap014-*`.
- `20-pre-*`: snapshots before restart #1 (2026-07-12 11:52Z).
- `21-restart-startup-log.txt`: post-restart-#1 store init logs.
- `22-post-*`: readback after restart #1 — trading room 404 (the failure),
  all others 200 byte-identical.
- `30-postfix-startup-log.txt`: store init logs after PR #3496 deploy.
- `31`–`34`: trading-room round 2 write phase + pre-restart snapshot.
- `35-tr2-post-*`: readback after restart #2 — trading room PASS.

## Notes

- All writes used `Idempotency-Key`, `X-Request-Id`, and `If-Match` where
  required; ETags came from `meta.etag` in response bodies.
- No code was patched inside this evidence task itself; the trading-room
  failure was filed and fixed as a scoped restore PR (#3496) reverting the
  merge clobber, exactly per the task's blocker rule.
