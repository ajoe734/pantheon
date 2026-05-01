# Canary/Live Activation Criteria and Runbook

Document type: Activation readiness runbook
Task: P1-LIVE-PLAN-001
Status: P1 activation readiness — production live remains fail-closed
Owner: Claude
Reviewer: Codex
Last updated: 2026-05-01
Source policy: PAPER_CANARY_LIVE_POLICY.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md, ROLLBACK_AND_POSITION_SEMANTICS.md

---

## 1. Scope and Current Status

### 1.1 P1 scope

This document defines the criteria that must be satisfied before Pantheon can activate canary or live deployment stages. **P1 scope is activation readiness only.** Opening production live trading is not in scope for P1.

Current deployment stage posture at P1 entry:

| Stage | Status | Notes |
|---|---|---|
| paper | baseline verified (P0-LOOP-001 done) | operating loop smoke passes |
| canary | not activated | prerequisites defined here; fail-closed until all gates pass |
| live | not activated | fail-closed; requires canary graduation first |

### 1.2 Hard invariants that must remain true through activation

From `SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md`:

1. `paper/canary/live` are deployment stages, not artifact states.
2. Every deployment-managed runtime has a `RuntimeBinding`.
3. Paper telemetry includes runtime identity when a binding exists.
4. No broker secret appears in frontend, artifact payload, launch manifest, telemetry, or OpenClaw memory.
5. BFF/front are not canonical runtime truth.
6. OpenClaw/LLM may research and review, but cannot directly operate broker/runtime.
7. Live broker execution remains disabled until a separate activation plan passes all gates in §4–§7 below.

---

## 2. Canary Activation Prerequisites

Canary activation requires all conditions in §2.1–§2.5 to pass simultaneously. Any single failure blocks promotion.

### 2.1 Paper operating loop prerequisites (foundation)

The following P0 tasks must be `done` before canary can be evaluated:

| Prerequisite task | Acceptance evidence |
|---|---|
| P0-LOOP-001 | Minimum paper loop smoke: seed/approved artifact → DeploymentPlan → RuntimeBinding → paper heartbeat → projection |
| P0-TEL-PROJ-001 | BFF/runtime projection shows non-mock last heartbeat with bridge identity |
| P0-LIVE-GUARD-001 | Live role is confirmed `health_only/not_activated`; bracket event is `logged_only`, not submitted |
| P0-REC-001 | At least one paper run has produced a `ReconciliationRecord` |

### 2.2 Paper observation period (quantitative)

From `PAPER_CANARY_LIVE_POLICY.md` §6.1:

- **Standard strategy:** at least **20 trading days** of paper observation
- **High-frequency / high-turnover strategy:** at least **10 sessions + 200 paper orders**

The observation data must be on file in the `ReconciliationRecord` history and linked to a `RuntimeBinding`.

### 2.3 Stability conditions

All of the following must be true at promotion evaluation time:

- Zero unresolved Severity-1 or Severity-2 incidents
- Runtime / loader integrity issues = 0
- Reconciliation mismatch rate < 1%
- Governance / approval mismatch = 0

### 2.4 Performance conditions

- Paper maximum drawdown ≤ 1.2× research expectation
- Model estimated slippage vs paper simulated slippage deviation < 25%
- Turnover ≤ 110% of strategy-defined cap
- Risk policy breach count = 0

### 2.5 Governance and operational conditions

- `rollback_target` artifact exists and is approved
- Risk Owner has reviewed and approved canary promotion
- Operator has been designated as canary observation owner
- `DeploymentPlan` contains valid `scale.capital_scale_pct`, `scale.gross_scale_pct`, and `scale.ramp_schedule` for canary

### 2.6 Canary capital and risk limits (initial)

From `PAPER_CANARY_LIVE_POLICY.md` §6.2:

```text
canary_capital = min(5% pool NAV, strategy_canary_cap)
gross_limit    = 25% of planned live gross
single_name_limit = 50% of live single-name limit
turnover_limit = 75% of live turnover limit
```

These limits are enforced by `DeploymentPlan.scale.*`. They cannot be overridden upward without a new human-approved `DeploymentPolicy`.

### 2.7 Canary monitoring posture

Canary mode must activate with stricter defaults than live:

- Lower alert thresholds
- Stricter slippage alert
- Shorter rollback decision latency
- Higher heartbeat sensitivity
- Dedicated canary observation owner on call

---

## 3. Canary → Live Promotion Prerequisites

Live promotion requires canary graduation. All conditions in §3.1–§3.4 must pass simultaneously.

### 3.1 Canary observation period (quantitative)

From `PAPER_CANARY_LIVE_POLICY.md` §7.1:

- At least **10 trading days** canary observation
- Or at least **50 real orders**

### 3.2 Incident conditions

- Zero unresolved Severity-1 incidents
- Zero governance / loader / binding mismatches
- Zero forced kill-switch events

### 3.3 Execution quality conditions

- Realized slippage vs paper expectation: degradation ≤ 20%
- Order reject rate < 0.5%
- Fill rate ≥ 90% (for normally liquid instruments)
- Target vs executed exposure tracking error within strategy family tolerance

### 3.4 Risk conditions

- Canary maximum drawdown < 50% of pool kill-threshold
- No risk policy hard breach during canary period
- No unresolved reconciliation anomaly

### 3.5 Human approvals required for live activation

All three approvals are required. Any single missing approval blocks live activation:

| Role | Approval required |
|---|---|
| Reviewer | sign-off on canary graduation evidence |
| Risk Owner | sign-off on risk metrics and position semantics |
| Operator | sign-off on operational readiness and runtime ownership |

Live activation without all three approvals is a governance breach and must be rejected by the promotion controller.

---

## 4. Rollback Criteria and Strategies

### 4.1 Rollback strategy selection

From `ROLLBACK_AND_POSITION_SEMANTICS.md` §3:

| Condition | Rollback strategy |
|---|---|
| Minor regression, smooth repair possible | `replace` |
| Style mismatch, need stable transition | `pause_then_replace` |
| Severe incident / unauthorized order / breach | `liquidate_then_replace` |

### 4.2 Canary rollback triggers

Immediate rollback evaluation is required when any of the following occur during canary:

- Drawdown breaches canary kill-threshold (defined in `DeploymentPlan.rollback`)
- Realized slippage > 20% worse than paper expectation
- Reconciliation mismatch rate ≥ 1%
- Fill rate drops below 90% for >1 session
- Order reject rate ≥ 0.5%
- Any Severity-1 incident
- Runtime / loader binding mismatch detected
- Forced kill-switch event

**Default action for canary rollback:** `pause_then_replace` unless severity escalates to liquidate triggers (see §5.2).

### 4.3 Live rollback triggers

Same conditions as §4.2 plus:

- Any broker runaway order risk detected
- Unauthorized artifact / binding mismatch in live context
- Operator manual emergency stop

**Default action for live rollback:** `pause_then_replace`; escalate to `liquidate_then_replace` for severe or governance events.

### 4.4 Rollback target requirements

Before canary can be activated:

- An approved `rollback_target` artifact must exist
- The rollback target must be at paper-validated maturity or better
- The target must be listed in `DeploymentPlan.rollback.artifact_id`

Rollback creates a new `RuntimeBinding` (never overwrites the old one). The old binding retains immutable core fields and moves to `retired` status after cutover. Position lineage is maintained through `opened_by_artifact_id` and `current_managed_by_binding_id`.

---

## 5. Kill Switch Criteria

### 5.1 Soft triggers (controlled degradation path)

From `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` §6.2:

The following enter **soft emergency evaluation** and may trigger kill-switch controller recommendation:

- Drift above warning threshold
- Repeated reject rate increase
- Slippage deterioration beyond tolerance
- Loader anomaly without live breach
- Canary underperformance

Soft path: `telemetry / incident / drift → recommendation → runtime-manager controlled action`

Available actions on soft path: pause new entries, reduce budget, switch risk-off, pause_then_replace, schedule rollback.

### 5.2 Hard triggers (immediate kill evaluation)

The following enter **hard emergency evaluation** and may trigger immediate kill-switch execution:

- Severity-1 incident
- Unauthorized artifact or binding mismatch
- Runtime sending unexpected order pattern
- Broker position mismatch beyond critical threshold
- Drawdown breach beyond hard kill limit
- Operator manual emergency stop

Hard path: `alert engine / runtime health / operator emergency action → kill-switch controller → runtime-manager fast path`

Available actions on hard path: immediate pause, liquidate all, hard rollback, environment risk-off, runtime terminate after safe action.

### 5.3 Kill switch routing rule

Kill switch never bypasses Runtime Manager. The minimum fast path is:

```text
Kill Switch Controller → Runtime Manager fast path → Pause/Liquidate/Replace/Risk-Off
```

All kill switch actions require an audit trail. Runtime Manager is the only authorized writer of `RuntimeBinding` state changes.

### 5.4 Kill switch operator permissions

Operators may manually trigger a pool-scoped or environment-scoped kill switch. All manual triggers require:

- RBAC authorization
- Audit trail entry
- Optional dual control (high-risk environment)

---

## 6. Human Approval and Risk Pass Gates

### 6.1 Gates required before canary activation

| Gate | Who holds it | How to pass |
|---|---|---|
| Risk Owner approval | Risk Owner | Written approval citing paper metrics and drawdown evaluation |
| Reviewer approval | Task Reviewer | Accepts evidence package including reconciliation records |
| Operator designation | Operator | Named canary observation owner logged in `DeploymentPlan` |
| DeploymentPolicy check | Rollback Controller / Promotion Controller | `DeploymentPlan.pre_checks[]` pass; scale limits within policy |
| Broker entitlement check | Operator / Broker admin | Canary subaccount exists; credentials are stage-scoped (not shared with paper or live) |

Any missing gate blocks canary activation. The promotion controller must record rejection reason.

### 6.2 Gates required before live activation

All canary gates plus:

| Gate | Who holds it | How to pass |
|---|---|---|
| Canary graduation evidence | Reviewer + Risk Owner | Written summary of canary period with all §3.1–3.4 metrics |
| Risk Owner live sign-off | Risk Owner | Explicit sign-off; cannot reuse canary approval |
| Operator live sign-off | Operator | Explicit sign-off; cannot reuse canary approval |
| Governance Committee (if required) | Governance Committee | Required for strategies above pool governance threshold |
| Capital pool authorization | Capital pool owner | Pool exposure limit set and approved for `full_live` or ramp stage |

### 6.3 Approval is non-delegatable

Approvals cannot be delegated to automation or inferred from prior gate states. Each gate must produce a distinct, auditable approval record linked to the relevant `DeploymentPlan`.

---

## 7. Activation Readiness Checklist (P1 Deliverable)

This checklist represents P1 activation readiness. All items must be `ready` before canary scheduling can be proposed to human decision-makers.

### 7.1 Foundation (required done before evaluation)

- [ ] P0-LOOP-001 done: paper operating loop smoke passes
- [ ] P0-TEL-PROJ-001 done: non-mock runtime projection visible in BFF
- [ ] P0-LIVE-GUARD-001 done: live is confirmed `health_only/not_activated`
- [ ] P0-REC-001 done: at least one `ReconciliationRecord` exists for paper run

### 7.2 Documentation (required for activation proposal)

- [x] Canary prerequisites documented (§2)
- [x] Live prerequisites documented (§3)
- [x] Rollback criteria named with strategy selection matrix (§4)
- [x] Kill switch criteria named with hard/soft trigger matrix (§5)
- [x] Human approval gates enumerated (§6)
- [ ] Broker entitlement model defined for canary subaccount isolation
- [ ] Capital pool authorization procedure documented

### 7.3 Operational readiness

- [ ] Canary `rollback_target` artifact approved and on file
- [ ] `DeploymentPlan` template with canary scale limits prepared
- [ ] Canary observation owner designated
- [ ] Kill switch drill conducted on paper stage
- [ ] Rollback drill conducted on paper stage

### 7.4 Not in P1 scope (intentional fail-closed)

The following items are explicitly deferred to P2 or later:

- Actual canary broker subaccount provisioning
- Live broker SDK kernel activation (P2-LIVE-KERNEL-001)
- Full RBAC implementation for kill switch dual control
- Production database posture for live telemetry (P1-PERSIST-001)
- KillSwitchBridge secondary path (P1-KILL-001)

---

## 8. Stage Promotion Flow (Summary)

```text
paper baseline (P0-LOOP-001 done)
     │
     │  §2.1–2.5 all pass
     │  Risk Owner + Reviewer + Operator approval
     │  Broker entitlement check
     ▼
canary (real orders, reduced capital, strict monitoring)
     │
     │  §3.1–3.4 all pass
     │  Risk Owner + Reviewer + Operator re-approval (cannot reuse canary approval)
     │  Capital pool authorization
     ▼
live (real orders, full approved exposure)
```

At any point on this path, a rollback trigger (§4) or kill switch trigger (§5) halts promotion and returns to the rollback controller for resolution.

---

## 9. Relationship to Canonical Policy Files

This runbook derives directly from:

| Policy file | Role |
|---|---|
| `PAPER_CANARY_LIVE_POLICY.md` | Threshold values, stage semantics, capital limits |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | Kill switch triggers, path routing, action types |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Rollback strategy selection, binding lineage, position semantics |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Write-owner rules for `RuntimeBinding` and `DeploymentPlan` |

In any conflict between this runbook and the canonical policy files, the policy files take precedence. This runbook synthesizes those policies into an actionable checklist; it does not override them.
