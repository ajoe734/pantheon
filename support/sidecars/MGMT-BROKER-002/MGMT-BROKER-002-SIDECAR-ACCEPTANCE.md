# MGMT-BROKER-002 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `MGMT-BROKER-002-SIDECAR-ACCEPTANCE`
**Helper parent:** `MGMT-BROKER-002` — Shioaji account readiness check
**Parent owner:** `Gemini2`
**Parent reviewer:** `Gemini`
**Prepared by:** `Claude`
**Reviewer:** `Gemini2`
**Date:** `2026-05-15`
**Status:** `closed` — review_approved by Gemini2 on 2026-05-15; finalized by Claude (owner closeout)

> Scope constraint: support artifact only. This packet does not modify canonical truth, L1 policy files, runtime implementation, registry state, or governance semantics. It packages the acceptance surface for the `MGMT-BROKER-002` account readiness check slice so the assigned reviewer can validate completion criteria and unblock downstream tasks without re-scanning full task history.

---

## 1. Purpose

This sidecar packet gives `Gemini2` (parent owner) and `Gemini` (parent reviewer) a compact acceptance surface for the blocked parent task `MGMT-BROKER-002`:

1. Restate the parent acceptance criteria against the current task and adapter state.
2. Document the active blocker — missing real broker credentials — and its resolution path.
3. Map the concrete dependencies and downstream tasks that are gated on this task's completion.
4. Summarize the account readiness check constraints to preserve fail-closed live safety.
5. Provide a reviewer handoff checklist for formalizing completion proof.

---

## 2. Parent Task Overview

| Field | Value |
|---|---|
| Task ID | `MGMT-BROKER-002` |
| Title | Shioaji account readiness check |
| Phase | Track E / EPIC-05 Shioaji Sandbox |
| Owner | `Gemini2` |
| Reviewer | `Gemini` |
| Status | `blocked` |
| Blocker | Waiting for broker credentials (`BROKER_SHIOAJI_API_KEY` / `BROKER_SHIOAJI_SECRET_KEY`) |
| Waiting for | `Gemini` (credentials escalation / operator provision) |
| Live status | FAIL-CLOSED — `production_live_enabled: false`, `capital_binding_enabled: false` |

**What this task proves:** That a real Shioaji sandbox account can be reached and is in a valid `signed` / `ready` state, with credentials verified non-interactively through the `ShioajiBrokerAdapter.connect()` + `account_status()` path — before the formal place/cancel/readback/reconcile smoke is allowed to run against real credentials.

---

## 3. Active Blocker and Resolution Path

| Blocker | Blocking since | Required to unblock |
|---|---|---|
| `BROKER_SHIOAJI_API_KEY` not set | 2026-05-15T15:15:06Z | Operator or `Gemini` must provision API key into the sandbox environment |
| `BROKER_SHIOAJI_SECRET_KEY` not set | 2026-05-15T15:15:06Z | Same — both must be set before `adapter.connect()` can proceed |

### How the credentials gate works

From `services/broker/shioaji/adapter.py:159–166`:

```python
api_key = os.getenv("BROKER_SHIOAJI_API_KEY", "")
secret_key = os.getenv("BROKER_SHIOAJI_SECRET_KEY", "")
if not api_key or not secret_key:
    raise ShioajiBrokerError(
        _ERR_CREDENTIALS_MISSING,
        "BROKER_SHIOAJI_API_KEY and BROKER_SHIOAJI_SECRET_KEY must be set.",
        status_code=503,
    )
```

The adapter raises `_ERR_CREDENTIALS_MISSING` (error code `"CREDENTIALS_MISSING"`) with HTTP 503 if either env var is absent. The account readiness check cannot proceed until both are present.

### Resolution path

1. Operator / `Gemini` provides `BROKER_SHIOAJI_API_KEY` and `BROKER_SHIOAJI_SECRET_KEY` for the sandbox environment.
2. `Gemini2` runs the readiness check (see §5 below).
3. `Gemini2` records results in `support/evidence/MGMT-BROKER-002/` and unblocks the task.

---

## 4. Acceptance Criteria Checklist

The following criteria must be satisfied for MGMT-BROKER-002 to be considered complete:

| # | Criterion | Verification method | Current status |
|---|---|---|---|
| 1 | `BROKER_SHIOAJI_API_KEY` and `BROKER_SHIOAJI_SECRET_KEY` are set in the sandbox env | `os.getenv(...)` returns non-empty strings | **BLOCKED** |
| 2 | `adapter.connect()` succeeds without raising `_ERR_CREDENTIALS_MISSING` | Call `connect()` and verify no exception | **BLOCKED** |
| 3 | `adapter.account_status()` returns `account_status: "ready"` or `"unsigned"` (not `"missing"`) | Inspect return dict key `account_status` | **BLOCKED** |
| 4 | Account `signed` field is truthy (or `"unsigned"` is explicitly accepted as a known state) | Inspect `account_status()["signed"]` | **BLOCKED** |
| 5 | No real secret material is persisted (`raw_secret_material_persisted: false`) | Inspect `account_status()["raw_secret_material_persisted"]` | **SOURCE-READY** — enforced in `adapter.py:241` |
| 6 | `production_live_enabled` remains `false` in all facade output | Inspect `facade.run_lifecycle()` result | **SOURCE-READY** — enforced in `facade.py:204` |
| 7 | `capital_binding_enabled` remains `false` in all facade output | Inspect `facade.run_lifecycle()` result | **SOURCE-READY** — enforced in `facade.py:205` |
| 8 | `human_gate_required` is `true` in facade output | Inspect `facade.run_lifecycle()` result | **SOURCE-READY** — enforced in `facade.py:206` |
| 9 | `BROKER_SHIOAJI_SANDBOX_ENABLED` env flag is either unset (default-false) or explicitly `true` — no accidental live promotion | Inspect env at run time | **SOURCE-READY** — consumed by `sandbox_smoke.py` |
| 10 | Evidence note written to `support/evidence/MGMT-BROKER-002/` with `account_status`, `signed`, and timestamp | File present after run | **PENDING** — requires credentials first |

**Overall verdict:** Items 5–9 are source-verified and correct. Items 1–4 and 10 are blocked pending credential provision. No source changes are needed; this is purely an operator gate.

---

## 5. Verification Procedure (for owner `Gemini2` to run after credentials are set)

### 5.1 Minimal readiness check

```bash
# Set credentials in env (do NOT commit these to repo)
export BROKER_SHIOAJI_API_KEY="<sandbox_api_key>"
export BROKER_SHIOAJI_SECRET_KEY="<sandbox_secret_key>"

# Run account readiness check via adapter
cd /home/lupin/code/pantheon
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 - <<'EOF'
from services.broker.shioaji.adapter import ShioajiBrokerAdapter
a = ShioajiBrokerAdapter()
connect_result = a.connect()
account_result = a.account_status()
print("connect:", connect_result.get("status"))
print("account_status:", account_result.get("account_status"))
print("signed:", account_result.get("signed"))
print("raw_secret_material_persisted:", account_result.get("raw_secret_material_persisted"))
EOF
```

Expected output (any of these is acceptable):

```
connect: ok
account_status: ready
signed: True
raw_secret_material_persisted: False
```

or

```
connect: ok
account_status: unsigned
signed: False
raw_secret_material_persisted: False
```

(`unsigned` is an accepted state — it means credentials work but the account has not yet accepted the trading agreement; this still proves the readiness check path works.)

### 5.2 Facade-level lifecycle smoke (optional for readiness, required for MGMT-BROKER-003)

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -c "
from services.broker.shioaji.facade import ShioajiSandboxFacade
f = ShioajiSandboxFacade()
print(f.account_status())
"
```

### 5.3 Evidence note

Write a brief markdown note to `support/evidence/MGMT-BROKER-002/README.md` recording:

- `account_status` value observed
- `signed` value
- `raw_secret_material_persisted: False` confirmed
- Timestamp of run
- Confirmation that no live order was placed and no real capital was touched

---

## 6. Dependency Map

### 6.1 Upstream dependencies of MGMT-BROKER-002

| Dependency | Status | Relevance |
|---|---|---|
| `MGMT-BROKER-001` — sandbox adapter facade | **DONE** (`f988cd8d`) | Provides `ShioajiBrokerAdapter.connect()` and `account_status()` used by this task |
| Broker credentials provisioning | **MISSING** — waiting for operator | Both `BROKER_SHIOAJI_API_KEY` and `BROKER_SHIOAJI_SECRET_KEY` must be provided before this task can proceed |
| `PAPER_CANARY_LIVE_POLICY.md` (L1) | Canonical | Defines the boundary between sandbox account check (allowed) and canary/live (fail-closed) |

### 6.2 Downstream tasks gated on MGMT-BROKER-002

| Task | Title | Dependency |
|---|---|---|
| `MGMT-BROKER-003` | place/cancel/readback/reconcile smoke | Requires a verified account to run real sandbox smoke (currently uses `--mock-api` fallback) |
| `MGMT-BROKER-004` | Shioaji evidence packet | Consumes MGMT-BROKER-003 smoke evidence; DONE with mock evidence, but real-credential re-run would strengthen the packet |
| `MGMT-BROKER-006` | Shioaji canary readiness packet integration | Depends on the full sandbox evidence chain including account readiness |

### 6.3 Dependency chain

```
Operator / Gemini
  -> provision BROKER_SHIOAJI_API_KEY + BROKER_SHIOAJI_SECRET_KEY
      -> MGMT-BROKER-002 (account readiness check)
          -> account_status = ready | unsigned
              -> unblocks MGMT-BROKER-003 real-credential run
                  -> real smoke evidence
                      -> MGMT-BROKER-004 evidence packet (strengthen)
                          -> MGMT-BROKER-006 canary readiness
                              -> Human-gate (risk-owner + operator)
```

**Note:** MGMT-BROKER-003 has already completed using `mock_api_replay` mode. MGMT-BROKER-004 and MGMT-BROKER-006 are also done. MGMT-BROKER-002's completion would provide real-credential verification that strengthens the existing mock evidence, but is not a hard blocker for tasks that have already closed on mock evidence.

---

## 7. Safety Constraints

Per `PAPER_CANARY_LIVE_POLICY.md` and `services/broker/shioaji/adapter.py`:

### 7.1 Invariants that must hold during account readiness check

| Invariant | Enforcement | Verified |
|---|---|---|
| No real capital written | `is_real_capital: False` enforced in all order objects | Source-verified in `adapter.py` |
| No live order submitted | `reject_live_order()` raises `SHIOAJI_LIVE_DISABLED`; facade checks error code | Source-verified in `adapter.py` + `facade.py` |
| No raw secret material persisted | `raw_secret_material_persisted: False` in `account_status()` return | Source-verified in `adapter.py:241` |
| Sandbox boundary tagged | All orders have `deployment_stage: "sandbox"` | Source-verified |
| `SHIOAJI_LIVE_DISABLED` enforced | `_gate_check()` method blocks live-path operations | Source-verified in `adapter.py` |

### 7.2 Account readiness scope

This task scope is **read-only account probing only**:

- `connect()` — authenticates the API key but does not place any order
- `account_status()` — reads account metadata and returns a redacted summary
- **No orders are submitted** during the readiness check itself (those belong to MGMT-BROKER-003)

---

## 8. Sidecar Scope Declaration

- No canonical L1 or L2 document was modified by this sidecar.
- No runtime, registry, or governance implementation was modified by this sidecar.
- No broker adapter, facade, or smoke code was modified by this sidecar.
- The only artifact produced by this slice is this support packet.
- Parent execution, review approval, and closeout remain owned by the `MGMT-BROKER-002` parent task lifecycle.
- MGMT-BROKER-002 remains `blocked` until broker credentials are provisioned by the operator.

---

## 9. Reviewer Handoff Notes

**Reviewer for this sidecar:** `Gemini2`

**What to review**

1. Confirm the acceptance criteria checklist (§4) accurately represents what MGMT-BROKER-002 needs to prove.
2. Confirm the blocker description (§3) is correct — specifically that both `BROKER_SHIOAJI_API_KEY` and `BROKER_SHIOAJI_SECRET_KEY` are missing, not just one.
3. Confirm the dependency chain (§6.3) is accurate — particularly whether MGMT-BROKER-003/004/006 strictly require MGMT-BROKER-002 completion or have already closed on mock evidence.
4. Confirm the verification procedure (§5) is runnable once credentials are provisioned.
5. Confirm safety invariants (§7) are correctly stated.

**Approval command for this sidecar**

```bash
AI_NAME=Gemini2 python3 scripts/ai_status.py approve MGMT-BROKER-002-SIDECAR-ACCEPTANCE "Acceptance packet verified: checklist, blocker, dependency map, and safety invariants are correctly stated for MGMT-BROKER-002 account readiness check."
```

**Note for parent task unblocking**

Once credentials are provisioned, the parent task owner (`Gemini2`) should:
1. Run the verification procedure in §5.
2. Write evidence to `support/evidence/MGMT-BROKER-002/README.md`.
3. Remove the blocker from `ai-status.json` via `scripts/ai-status.sh`.
4. Hand the parent task to `Gemini` for review.
