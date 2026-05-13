# EP5-BROKER-TW-002-RERUN-REAL-FIX Closeout

Owner: Codex2
Reviewer: Codex
Reviewed task commit: `306aa4f0`
Closeout date: 2026-05-13

## Approved Scope

- Simulation-mode signed precheck was removed from the Shioaji adapter path.
- `sandbox_smoke.py` no longer performs a signed-status sleep/relogin audit.
- Stock-only Shioaji simulation smoke evidence is preserved at `stock-smoke.json`.
- Futures smoke and production signed verification remain out of scope for this task.
- Live broker execution remains fail-closed with `SHIOAJI_LIVE_DISABLED`.

## Evidence Summary

- Real Shioaji simulation SDK run: `status=passed`, `account_kind=stock`, `symbol=2890`, `qty=1`, `order_type=limit`, `limit_price=18.0`.
- Place-to-cancel delay: configured and applied at `1.0` second.
- Shioaji trade id: `10054A`.
- Session response evidence: `sdk_stdout_observations.session_response_code_0_observed=true`.
- Reconciliation: `passed`.

## Closeout Verification

- `python3 -m pytest services/broker/shioaji/test_adapter.py services/broker/shioaji/test_sandbox_smoke.py -q` -> 44 passed.
- `rg` check found no `SHIOAJI_ACCOUNT_UNSIGNED`, no `signed=True` production verify audit, and no `sleep(300)` path in the adapter or smoke harness.
- `git diff -- services/broker/shioaji/adapter.py services/broker/shioaji/sandbox_smoke.py support/evidence/EP5-BROKER-TW-002-RERUN-REAL-FIX/stock-smoke.json` was empty before closeout.

## Operator Follow-up

請 operator 回 Sinopac 後台確認「python 測試與否」變為「已測試」，之後 dispatch `EP5-BROKER-TW-002-PRODUCTION-VERIFY` 驗 production mode `signed=True`。
