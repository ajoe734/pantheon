# EP5-BROKER-TW-002-RERUN-REAL — Acceptance Packet

**Sidecar Task:** EP5-BROKER-TW-002-RERUN-REAL-SIDECAR-ACCEPTANCE
**Parent Task:** EP5-BROKER-TW-002-RERUN-REAL
**Helper Kind:** acceptance_packet
**Prepared by:** Claude (2026-05-13)
**Reviewer:** Codex

---

## 1. Acceptance Criteria Checklist

The following criteria are sourced from `ai-status.json` task entry for `EP5-BROKER-TW-002-RERUN-REAL`.

| # | Criterion | Status | Evidence / Notes |
|---|---|---|---|
| 1 | real Shioaji SDK used (no mock fallback) | ✅ PASS | Both smoke files show `run_mode: shioaji_simulation_sdk` |
| 2 | stock smoke: place+cancel+readback+reconcile success with `shioaji_trade_id` | ❌ FAIL | `stock-smoke.json` → `SHIOAJI_ACCOUNT_UNSIGNED` (HTTP 403) |
| 3 | futures smoke: place+cancel+readback+reconcile success with `shioaji_trade_id` | ❌ FAIL | `futures-smoke.json` → `SHIOAJI_ACCOUNT_MISSING` (HTTP 503) |
| 4 | `signed=True` confirmed for both `stock_account` and `futopt_account` post-audit | ❌ MISSING | `support/evidence/EP5-BROKER-TW-002-RERUN-REAL/signed-status.json` not present |
| 5 | order spacing ≥1s enforced in adapter | ✅ PASS | `adapter.py:39` → `_SUBMIT_SPACING_SECONDS = 1.0`; `_enforce_submit_spacing()` implemented |
| 6 | `shioaji>=1.2.0` pinned in requirements | ✅ PASS | `services/broker/shioaji/requirements.txt` → `shioaji>=1.2.0,<2.0.0` |
| 7 | live order route still rejects with `SHIOAJI_LIVE_DISABLED` | ✅ PASS | Both smoke files show `live_gate.status: rejected` with `SHIOAJI_LIVE_DISABLED` |
| 8 | smoke ran within 08:00-20:00 Asia/Taipei window | ✅ PASS | Both smoke files show `within_window: true` at both start and end observations |
| 9 | no real capital path opened (`is_real_capital` remains `False`) | ✅ PASS (adapter code) | `adapter.py:79` → `is_real_capital: bool = False` (hardcoded invariant); smoke did not reach order placement due to account errors |

**Overall: NOT READY — 3 criteria failing / missing (criteria 2, 3, 4)**

---

## 2. Root Cause Analysis

### 2a. Criterion 2 — `SHIOAJI_ACCOUNT_UNSIGNED` (stock smoke)

The adapter resolved `api.stock_account` successfully but detected `signed=False`, raising `SHIOAJI_ACCOUNT_UNSIGNED` (HTTP 403) before any order submission.

```
error_code: "SHIOAJI_ACCOUNT_UNSIGNED"
message: "Shioaji stock account is not signed for API sandbox order placement."
```

**Root cause:** The Sinopac simulation account has not completed the API sandbox sign-on step for the stock account. The API key/secret are valid (login succeeded, SDK resolved the account object), but the sandbox order placement permission (`signed`) has not been granted.

**This is not a code defect.** The adapter correctly enforces the `signed` check (`adapter.py:190-195`).

### 2b. Criterion 3 — `SHIOAJI_ACCOUNT_MISSING` (futures smoke)

The adapter called `api.futopt_account` after login and received `None`, raising `SHIOAJI_ACCOUNT_MISSING` (HTTP 503).

```
error_code: "SHIOAJI_ACCOUNT_MISSING"
message: "Shioaji futures account is not available after login."
```

**Root cause:** The futures/options account (`futopt_account`) is not linked to the API credentials used in this run. Either (a) the Sinopac account does not have a futures sub-account, or (b) the futures account requires a separate activation step on the Sinopac web portal before it appears via the API.

**This is not a code defect.** The adapter correctly checks for `None` (`adapter.py:183-189`).

### 2c. Criterion 4 — `signed-status.json` absent

The post-smoke signed-status audit artifact was not generated. This is a downstream effect of criteria 2 and 3 failing — the Codex2 worker that ran the smoke did not reach the signed-status re-login step. The `support/evidence/EP5-BROKER-TW-002-RERUN-REAL/signed-status.json` file does not exist.

---

## 3. Dependency Map

```
EP5-BROKER-TW-002-RERUN-REAL (Codex2, in_progress)
│
├── HARD BLOCK — Sinopac sandbox account setup
│   ├── stock_account.signed = True required
│   │   └── Action: human must visit Sinopac web portal and activate
│   │           API sandbox order placement for the stock account
│   └── futopt_account != None required
│       └── Action: human must activate futures sub-account on the
│               Sinopac portal and link it to the API credentials
│
├── After account setup resolves:
│   ├── Re-run stock smoke → expect status: passed + shioaji_trade_id present
│   ├── Re-run futures smoke → expect status: passed + shioaji_trade_id present
│   └── Run signed-status audit → expect stock_signed=True, futopt_signed=True
│
└── No code changes needed — adapter and smoke harness are correct
```

**External dependency:** Sinopac sandbox account activation (human-gate action, not automatable).

---

## 4. Evidence Inventory

| Artifact | Path | Status |
|---|---|---|
| Stock smoke evidence | `support/evidence/EP5-BROKER-TW-002-RERUN-REAL/stock-smoke.json` | Present; status=failed |
| Futures smoke evidence | `support/evidence/EP5-BROKER-TW-002-RERUN-REAL/futures-smoke.json` | Present; status=failed |
| Signed-status audit | `support/evidence/EP5-BROKER-TW-002-RERUN-REAL/signed-status.json` | **Absent** |

---

## 5. Code Quality Observations (Informational Only)

The following are observations for Codex review. None block the parent task's acceptance criteria; all are informational.

| Area | File | Observation |
|---|---|---|
| Adapter gate | `adapter.py:116-120` | Sandbox gate reads env; fail-closed default is correct |
| Submit spacing | `adapter.py:214-229` | Thread-local per-account last-submit timestamp; 1.0s default enforced |
| Live reject | `adapter.py:517-526` | `reject_live_order()` always raises unconditionally |
| Futures contract resolution | `adapter.py:251-267` | Falls back to guessing `symbol[:3]` as futures category; acceptable for smoke |
| Mock guard | `sandbox_smoke.py:199-205` | `build_adapter()` passes `_api=None` for real SDK path; correct |
| Window enforcement | `sandbox_smoke.py:80-91` | `fail_if_outside_taipei_window()` raises before any SDK call; correct |

---

## 6. Remediation Checklist (for parent task owner Codex2)

When the Sinopac sandbox account issues are resolved by the human operator:

1. Confirm `BROKER_SHIOAJI_API_KEY` and `BROKER_SHIOAJI_SECRET_KEY` are set (len=44 each per supervisor log).
2. Re-run stock smoke between 08:00-20:00 Asia/Taipei:
   ```bash
   BROKER_SHIOAJI_SANDBOX_ENABLED=1 \
   python3 services/broker/shioaji/sandbox_smoke.py \
     --account-kind stock --symbol 2330 --qty 1 --side buy \
     --order-type limit --limit-price 950 \
     --output-file support/evidence/EP5-BROKER-TW-002-RERUN-REAL/stock-smoke.json
   ```
3. Re-run futures smoke between 08:00-20:00 Asia/Taipei:
   ```bash
   BROKER_SHIOAJI_SANDBOX_ENABLED=1 \
   python3 services/broker/shioaji/sandbox_smoke.py \
     --account-kind futures --symbol TXFR1 --futures-category TXF \
     --qty 1 --side sell --order-type limit --limit-price 23000 \
     --output-file support/evidence/EP5-BROKER-TW-002-RERUN-REAL/futures-smoke.json
   ```
4. Run signed-status audit after ~5-minute sleep:
   ```bash
   BROKER_SHIOAJI_SANDBOX_ENABLED=1 \
   python3 services/broker/shioaji/sandbox_smoke.py \
     --signed-status-only --signed-status-sleep-seconds 300 \
     --output-file support/evidence/EP5-BROKER-TW-002-RERUN-REAL/signed-status.json
   ```
5. Verify all three evidence files show `status: passed`.
6. Confirm `shioaji_trade_id` is present (non-null) in both smoke files.
7. Confirm `stock_account.signed=True` and `futopt_account.signed=True` in signed-status.json.
8. Proceed with parent task review handoff to Codex.

---

## 7. Handoff Note

This acceptance packet is ready for Codex review.

The parent task `EP5-BROKER-TW-002-RERUN-REAL` is blocked on external Sinopac sandbox account setup (human-gate). The adapter code and smoke harness are correct and require no changes. Once the sandbox account issues are resolved and evidence is regenerated, the parent task can proceed to review.

This sidecar (support artifact only) does not modify any canonical truth files.
