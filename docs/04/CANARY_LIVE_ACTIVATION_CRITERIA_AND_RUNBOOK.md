# Canary/Live Activation Criteria and Runbook

Document type: Activation readiness runbook
Task: P1-LIVE-PLAN-001; P2-LIVE-KERNEL-001 addendum
Status: P2 live-kernel readiness plan - broker sandbox/test integration required; production live remains fail-closed
Owner: Claude
Reviewer: Codex
Last updated: 2026-05-01
Source policy: PAPER_CANARY_LIVE_POLICY.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md, ROLLBACK_AND_POSITION_SEMANTICS.md, OPENCLAW_RUNTIME_CONTRACT.md

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

### 1.2 P2 live-kernel addendum scope

`P2-LIVE-KERNEL-001` adds a production readiness plan for the future full Lean
Launcher plus broker SDK path. This addendum does not activate canary or live,
does not authorize real broker orders, and does not mark broker entitlement or
capital authorization as satisfied.

It does require the broker order API to be connected and tested as early as
possible with broker paper accounts, sandbox endpoints, simulation mode, or
test credentials. That evidence belongs before production live activation, not
after it. The only path that remains fail-closed by default is the production
live side-effect path for real order placement, cancel/replace, position
changes, and capital movement.

The readiness target is narrower:

- define which launcher, broker SDK, entitlement, capital, kill-switch, and
  drill evidence must exist before any real broker stage can be enabled;
- keep missing entitlement, subaccount, credential, or capital approval evidence
  as explicit fail-closed blockers;
- keep OpenClaw-compatible runtimes outside the execution kernel.

### 1.3 Hard invariants that must remain true through activation

From `SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md`:

1. `paper/canary/live` are deployment stages, not artifact states.
2. Every deployment-managed runtime has a `RuntimeBinding`.
3. Paper telemetry includes runtime identity when a binding exists.
4. No broker secret appears in frontend, artifact payload, launch manifest, telemetry, or OpenClaw memory.
5. BFF/front are not canonical runtime truth.
6. OpenClaw/LLM may research and review, but cannot directly operate broker/runtime.
7. Live broker execution remains disabled until a separate activation plan passes all gates in §4–§7 below.
8. Full LEAN Launcher and broker SDK paths may only be invoked by Runtime Manager
   against an approved `RuntimeBinding` and `DeploymentPlan`.
9. Broker SDK credentials, entitlement proofs, and account refs remain
   execution-plane/operator-owned; raw secrets must not enter repo-tracked
   docs, launch manifests, telemetry, artifacts, frontend, or OpenClaw memory.
10. Missing broker entitlement, subaccount isolation, or capital authorization
    evidence is a hard promotion rejection, not an operator warning.
11. Kill-switch readiness requires Runtime Manager follow-through and
    `telemetry_ack` evidence. A dispatched command without runtime/capital
    acknowledgement is not operational readiness.
12. Any `telemetry_ack.ack_status = fail_closed` record is valid audit evidence
    of safe fallback, but it does not satisfy the promotion drill gate.
13. Live fail-closed is not a broker API integration freeze. Broker sandbox,
    paper-account, simulation, validate-only, and test-key order API smoke
    should be implemented and archived before production live approval.

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
| Broker sandbox/test-key order API smoke | Runtime Manager owner + Operator | Broker API smoke covers auth, account readiness, place, cancel/replace, status/readback, execution/no-fill or fill disposition, telemetry, and reconciliation without real production capital |
| Broker entitlement check | Operator / Broker admin | Canary subaccount exists; credentials are stage-scoped (not shared with paper or live) |
| Capital authorization check | Capital pool owner / Risk Owner | Canary allotment, `capital_scale_pct`, `gross_scale_pct`, kill threshold, and rollback action are approved for this specific `DeploymentPlan` |
| Kill-switch drill and ack gate | Operator / Runtime Manager owner | Paper-stage drill evidence shows Runtime Manager follow-through and `telemetry_ack.ack_status = acknowledged`; `fail_closed` ack blocks promotion until remediated |

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
| Broker SDK readiness packet | Runtime Manager owner + Operator | Full LEAN Launcher and broker SDK path has a reviewed packet proving launcher origin, broker account binding, order lifecycle capture, and no direct broker bypass |
| Live kill-switch drill and ack gate | Operator / Runtime Manager owner | Canary or staging-live drill evidence covers pause/risk_off and applicable liquidate/terminate/replace behavior with acknowledged `telemetry_ack` |

### 6.3 Approval is non-delegatable

Approvals cannot be delegated to automation or inferred from prior gate states. Each gate must produce a distinct, auditable approval record linked to the relevant `DeploymentPlan`.

### 6.4 Broker entitlement and subaccount isolation gate

Broker entitlement is stage-scoped. A broker account or subaccount that is valid
for one stage does not automatically satisfy another stage.

| Stage | Entitlement rule | Isolation requirement | Missing evidence behavior |
|---|---|---|---|
| paper | No real broker account is required for the basic paper operating loop, but broker API readiness should use broker paper/sandbox/test credentials | Raw production broker secrets are absent; sandbox/test refs stay VM-2/execution owned | Basic paper may continue through simulated/logged paths, but missing sandbox/test broker smoke blocks canary/live readiness claims |
| canary | A dedicated canary account/subaccount must exist before any real order | Credentials, order permissions, market-data entitlements, and account refs are not shared with paper or live | Promotion controller rejects canary activation and records the missing entitlement |
| live | A separate live account/subaccount or explicitly approved live account boundary is required | Live credentials and capital authorization are not inherited from canary; live order permissions are reviewed separately | Promotion controller rejects live activation and keeps the runtime in health-only/fail-closed mode |

Required entitlement evidence:

- broker admin or operator record naming broker, account/subaccount ref, venue or
  routing profile, stage, and permission scope;
- secret-name refs for VM-2 injection only, with no raw secret values in
  repo-tracked files;
- explicit statement that paper, canary, and live credential scopes do not share
  mutable broker sessions;
- revocation or disable procedure for the stage-scoped credential;
- broker account ref present in `DeploymentPlan.pre_checks[]` or linked
  promotion evidence before Runtime Manager starts a real broker runtime.

Current P2 status: no production live broker entitlement packet is recorded in
this runbook. Until that packet exists, canary/live broker execution remains
fail-closed.

### 6.5 Capital authorization procedure

Capital authorization is independent of broker connectivity. A reachable broker
session does not authorize a capital pool, and a capital approval does not grant
broker entitlements.

Each canary or live `DeploymentPlan` must carry or link a capital authorization
record with:

- `capital_pool_id`, `persona_capital_binding_id`, target stage, and runtime
  binding reference;
- authorized `capital_scale_pct`, `gross_scale_pct`, gross/notional limit,
  single-name limit, turnover limit, and drawdown kill threshold;
- approval identity for the capital pool owner and Risk Owner;
- effective window, expiry, revocation rule, and rollback target;
- explicit result if the requested scale exceeds policy limits.

Stage-specific behavior:

- paper requires `capital_scale_pct = 0`; any real capital request blocks the
  paper deployment.
- first canary is limited by §2.6 and must satisfy
  `0 < capital_scale_pct <= 5` and `0 < gross_scale_pct <= 25`.
- live requires canary graduation plus a fresh capital authorization. There is
  no default promotion to `100%` capital or gross exposure.

If the authorization record is missing, expired, mismatched to the plan, or
broader than policy allows, Runtime Manager must not create or activate the
real-capital `RuntimeBinding`.

### 6.6 Full LEAN Launcher and broker SDK readiness packet

The P2 live-kernel readiness packet must prove the intended production route
without enabling live by default:

```text
Approved DeploymentPlan
  -> RuntimeBinding
  -> Runtime Manager
  -> LEAN launcher
  -> broker SDK adapter
  -> broker account / subaccount
  -> telemetry, reconciliation, audit
```

The packet is acceptable only when it contains all of the following:

| Evidence area | Required contents | Fail-closed result |
|---|---|---|
| Launcher manifest | `deployment_stage`, `deployment_plan_id`, `runtime_binding_id`, `artifact_id`, `capital_pool_id`, `persona_capital_binding_id`, `rollback_target`, `trace_id`, and `idempotency_key` | Reject launch if any identity or rollback field is missing |
| Runtime ownership | Runtime Manager is the caller and writer; direct LEAN, OpenClaw, or broker SDK launch is absent | Reject launch and record governance mismatch |
| Secret boundary | Manifest, artifact payloads, telemetry, frontend, and OpenClaw memory contain secret refs only | Reject launch and open incident if raw secrets appear |
| Sandbox/test broker smoke | Broker paper account, sandbox endpoint, simulation mode, validate-only, or test credentials prove place, cancel/replace, status/readback, and execution/no-fill or fill disposition before any production live route | Reject canary/live readiness packet until sandbox/test broker API evidence is archived |
| Broker SDK lifecycle | Submit, cancel, replace, readback, reject, partial-fill, disconnect, retry, and duplicate-submit behavior is mapped to typed Runtime Manager outcomes | Reject production broker route until all covered states are explicit |
| Order identity | Broker order id, client order id, Pantheon command id, telemetry event id, `RuntimeBinding`, and `capital_pool_id` are correlated | Reject readiness packet if order/fill/reconciliation lineage cannot be joined |
| Account and capital checks | Broker entitlement evidence and capital authorization evidence match the same stage, account/subaccount, and `DeploymentPlan` | Reject promotion as `fail_closed` |
| Emergency follow-through | Kill-switch and rollback drills route through Runtime Manager and return `telemetry_ack.ack_status = acknowledged` | Reject promotion if the latest ack is missing or `fail_closed` |

Direct broker SDK proof, including a supervised order/cancel harness, can support
the evidence packet. For brokers with paper/sandbox/test-key support, that proof
should be captured before any real-money run. It does not by itself satisfy
production readiness unless Runtime Manager, `RuntimeBinding`, telemetry,
reconciliation, entitlement, capital authorization, and kill-switch
follow-through are also proven.

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
- [x] Broker entitlement model defined for canary subaccount isolation (§6.4)
- [x] Capital pool authorization procedure documented (§6.5)

### 7.3 Operational readiness

- [ ] Canary `rollback_target` artifact approved and on file
- [ ] `DeploymentPlan` template with canary scale limits prepared
- [ ] Canary observation owner designated
- [ ] Broker sandbox/test-key order API smoke completed and archived
- [ ] Kill switch drill conducted on paper stage with Runtime Manager `telemetry_ack.ack_status = acknowledged`
- [ ] Rollback drill conducted on paper stage
- [ ] Full LEAN Launcher and broker SDK readiness packet reviewed for launcher origin, broker account binding, and order lifecycle capture (§6.6)

### 7.4 Not in P1 scope (intentional fail-closed)

Broker sandbox/test-key order API smoke is not deferred by this section. It is
readiness evidence and should be run before canary/live promotion is proposed.

The following items are explicitly deferred to P2 or later:

- Actual canary broker subaccount provisioning
- Live broker SDK kernel activation (P2-LIVE-KERNEL-001 now defines readiness gates only; activation remains future-gated)
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
     │  Capital authorization check
     │  Paper kill-switch drill returns acknowledged telemetry_ack
     ▼
canary (real orders, reduced capital, strict monitoring)
     │
     │  §3.1–3.4 all pass
     │  Risk Owner + Reviewer + Operator re-approval (cannot reuse canary approval)
     │  Capital pool authorization
     │  Live subaccount entitlement isolation
     │  Full LEAN Launcher + broker SDK readiness packet
     │  Canary/staging-live kill-switch drill returns acknowledged telemetry_ack
     ▼
live (real orders, full approved exposure)
```

At any point on this path, a rollback trigger (§4) or kill switch trigger (§5) halts promotion and returns to the rollback controller for resolution.

---

## 9. P2 Full Lean Launcher + Broker SDK Production Readiness Plan

### 9.1 Readiness outcome

The P2 readiness outcome is a reviewed plan for the future production execution
kernel. It is not a runtime activation, and it does not turn `live` into an
order-capable stage.

The future live kernel is considered production-ready only when all of the
following are true at the same time:

1. Runtime Manager is the only component that can request a Lean Launcher start
   for canary/live.
2. The request references an approved `DeploymentPlan`, active
   `RuntimeBinding`, runtime identity, bridge commit, artifact checksum,
   stage-scoped broker account ref, and capital authorization.
3. The broker SDK path proves connect, submit, cancel/fill, broker position,
   telemetry, and reconciliation capture through Runtime Manager-originated
   evidence.
4. Broker entitlement, subaccount isolation, and capital authorization evidence
   are linked to the same plan and stage.
5. Kill-switch drills have acknowledged Runtime Manager follow-through for the
   relevant action set.

Until those conditions are met, production live remains health-only/fail-closed.

### 9.2 Lean Launcher authority boundary

The production Lean Launcher path must be owned by the execution plane:

```text
Promotion Controller
  -> approved DeploymentPlan
  -> Runtime Manager
  -> RuntimeBinding write path
  -> RuntimeBootstrapRequest / launch manifest
  -> pantheon/lean Launcher
  -> broker SDK adapter
  -> telemetry / reconciliation / audit
```

Forbidden paths:

- BFF, frontend, OpenClaw, research workers, or manual scripts directly starting
  an order-capable Lean Launcher.
- Broker SDK invocation without a Runtime Manager command envelope.
- Runtime bootstrap with raw broker secrets embedded in the request or manifest.
- Reusing a paper/canary account ref as live authorization without a fresh gate.

Manual broker harnesses may capture operator-supervised broker facts, but they
are not sufficient activation evidence unless a packet also proves Runtime
Manager origin and lifecycle capture.

### 9.3 Required launch inputs

Any future canary/live launch request must name these non-secret inputs:

| Input | Required meaning |
|---|---|
| `DeploymentPlan` | approved target stage, scale, rollback target, pre-checks, and post-checks |
| `RuntimeBinding` | authoritative runtime identity, artifact, capital pool, stage, and lifecycle state |
| bridge identity | `pantheon/lean` source path, runtime path, remote, and commit |
| artifact identity | artifact id, version, checksum, and strategy id |
| broker account ref | stage-scoped broker account/subaccount ref, never raw credentials |
| venue/routing ref | exchange, venue, or routing profile boundary |
| capital authorization | capital pool owner and Risk Owner approval for exact scale and window |
| operator context | operator id, approval decision id, idempotency key, and audit reason |
| kill-switch readiness ref | drill packet proving `telemetry_ack.ack_status = acknowledged` for required actions |

Missing or mismatched inputs fail closed before any real broker SDK action.

### 9.4 Broker SDK readiness evidence

The broker SDK readiness packet must prove all lifecycle facts without relying
on frontend or OpenClaw state:

- broker paper account, sandbox endpoint, simulation mode, validate-only, or
  test credentials were used before any production live side-effect;
- broker session reachability and authenticated account ownership;
- order-intent validation before submission;
- one minimal order lifecycle capture for the approved test envelope, including
  accepted/open state and cancel, fill, or otherwise resolved disposition;
- broker order id, RuntimeBinding id, DeploymentPlan id, telemetry event id, and
  runtime-manager command id joined in one evidence packet;
- broker position snapshot or explicit no-position/no-fill proof after closeout;
- reconciliation record showing broker truth and Pantheon telemetry agree;
- stop conditions for read-only mode, market order drift, quantity drift,
  unobserved operator state, or unexpected fill.

If any item is absent, the broker SDK path may be considered partially tested
but not production-ready for canary/live activation.

### 9.5 Fail-closed gap register

Current gaps are intentionally blocking:

| Gap | Current disposition |
|---|---|
| Production live entitlement packet | Missing; live activation rejected |
| Broker sandbox/test-key order API smoke packet | Required before canary/live readiness; missing packet is a work item, not a reason to avoid broker integration |
| Dedicated canary/live subaccount evidence | Missing in this runbook; real broker promotion rejected until archived |
| Full Lean Launcher order-capable runtime packet | Missing; live runtime remains health-only |
| Runtime Manager-originated broker lifecycle packet | Prepared by existing packet scaffolds, but not complete production evidence |
| Capital authorization for live exposure | Missing; no default full-capital activation |
| Kill-switch drill with acknowledged telemetry ack for live action set | Missing; promotion blocked until drill packet exists |

These are not documentation warnings. They are preconditions that block real
broker activation.

### 9.6 OpenClaw boundary

OpenClaw-compatible runtimes may help research, review, and prepare acceptance
packets, but they are never part of the execution kernel. OpenClaw must not:

- hold broker credentials;
- create or mutate `RuntimeBinding`;
- invoke the Lean Launcher;
- invoke broker SDK order routes;
- approve capital authorization;
- satisfy kill-switch drill evidence.

Any OpenClaw tool/workflow request that targets broker, live, paper execution,
or capital binding must remain denied by policy.

---

## 10. Relationship to Canonical Policy Files

This runbook derives directly from:

| Policy file | Role |
|---|---|
| `PAPER_CANARY_LIVE_POLICY.md` | Threshold values, stage semantics, capital limits |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | Kill switch triggers, path routing, action types |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Rollback strategy selection, binding lineage, position semantics |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Write-owner rules for `RuntimeBinding` and `DeploymentPlan` |
| `OPENCLAW_RUNTIME_CONTRACT.md` | OpenClaw boundary; confirms agent runtime cannot operate LEAN launcher or broker SDK execution |

In any conflict between this runbook and the canonical policy files, the policy files take precedence. This runbook synthesizes those policies into an actionable checklist; it does not override them.
