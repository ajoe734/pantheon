# BFF-CONSOL-013 Review - Codex

Disposition: Changes requested

## Findings

1. Actual execute-plans live gate is still bearer-storage only.

   The task acceptance requires `liveWriteGated()` to use `/bff/me` so cookie-only sessions pass the write gate. The delivered commit adds `execute-plans/src/lib/bff/runAction.ts` inside the Pantheon repo, but the real frontend repo remains unchanged at `/home/lupin/code/execute-plans`.

   Current real frontend code still gates writes on browser bearer storage:

   - `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:34` reads `readBrowserAuthStorage().token`.
   - `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:43` returns `realWritesEnabled() && authPresent()`.
   - `/home/lupin/code/execute-plans/src/lib/bff-v1/writes.ts:29` still uses the same `realWritesEnabled() && authPresent()` gate.

   Result: a valid `pantheon_session` cookie with no bearer token still fails `liveWriteGated()` in the app that operators actually run, so acceptance criteria "liveWriteGated() 不再只看 sessionStorage" and "cookie-only session 寫入 gate 通過" are not satisfied.

   Required fix: update the real execute-plans repo gate(s) to fetch `/bff/me` with `credentials: "include"` and admit `data.session.session_kind in {"cookie", "bearer"}` while blocking `stub` in production/strict mode. Cover the exported frontend helper behavior with actual execute-plans tests, not a Python-only duplicate of the logic.

## Verified

- Backend `/bff/me` implementation in Pantheon exposes `session_kind` for stub, bearer, and cookie paths.
- `python3 -m pytest services/control-plane/bff/test_bff_consol_013_cookie_session_write_gate.py services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_bff_auth_facade.py -q` -> 92 passed, 4 warnings.

## Notes

- The uncommitted `services/control-plane/bff/main.py` diff in the Pantheon worktree appears to be from a separate BFF command-route task and was not considered part of this review finding.
