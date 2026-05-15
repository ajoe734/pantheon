# P2-LIVE-KERNEL-001 Sidecar Acceptance Packet

Task: P2-LIVE-KERNEL-001-SIDECAR-ACCEPTANCE
Parent task: P2-LIVE-KERNEL-001
Helper kind: acceptance_packet
Owner: Codex2
Reviewer: Codex
Status: review approved; finalized for parent handoff
Last updated: 2026-05-01

## Scope Boundary

This packet supports the parent owner by summarizing acceptance criteria, dependencies, and review handoff checks for the live kernel readiness plan. It does not change canonical truth, runtime behavior, registry logic, broker integration, or deployment policy.

The parent task remains the authority for any canonical edits. This packet is a support artifact only.

## Parent Acceptance Targets

The parent task should be considered ready for review only when its deliverable demonstrates all of the following without enabling live execution by default:

1. Lean Launcher plus broker SDK production readiness is documented as a plan, not silently activated runtime behavior.
2. Broker entitlement, subaccount isolation, and capital authorization gaps are either resolved by explicit evidence or marked fail-closed with named follow-up prerequisites.
3. Paper, canary, and live promotion gates reference kill-switch telemetry ack and drill prerequisites before any live activation.

## Dependency Map

| Dependency | Current role for parent task | Acceptance implication |
|---|---|---|
| P1-LIVE-PLAN-001 | Defines canary/live activation criteria and runbook while production live remains fail-closed. | Parent readiness plan must preserve paper/canary/live as deployment stages, require canary graduation before live, and keep live disabled until all gate evidence exists. |
| P1-KILL-001 | Establishes runtime-manager secondary path and telemetry ack requirements for kill-switch follow-through. | Parent plan must require runtime-manager follow-through and `telemetry_ack` evidence before treating emergency controls as operationally ready. |
| P0-LOOP-001 | Provides paper operating loop foundation referenced by the activation runbook. | Parent plan should not claim canary readiness unless the paper loop evidence remains present and linked to runtime identity. |
| P0-TEL-PROJ-001 | Provides runtime status projection and non-mock heartbeat evidence. | Parent plan should require runtime identity and heartbeat projection before promotion checks are meaningful. |
| P0-LIVE-GUARD-001 | Confirms live role is `health_only/not_activated` and bracket event behavior is logged only. | Parent plan must not regress fail-closed live posture or turn health-only live guard into executable broker submission. |
| P0-REC-001 | Seeds paper reconciliation records and incident threshold evidence. | Parent plan should require reconciliation history for observation-period gates and mismatch thresholds. |

## Acceptance Checklist For Parent Owner

### Fail-Closed Posture

- [ ] Live broker execution remains disabled by default.
- [ ] Any launcher or broker SDK path is described as readiness planning unless explicit future activation gates are satisfied.
- [ ] Broker secrets are excluded from frontend, artifact payloads, launch manifests, telemetry, and OpenClaw memory.
- [ ] OpenClaw-compatible runtime remains outside the execution kernel and cannot directly operate broker/runtime/capital paths.

### Lean Launcher Readiness

- [ ] Launcher responsibilities are separated from canonical runtime truth ownership.
- [ ] Required launch inputs are named, including DeploymentPlan, RuntimeBinding, runtime identity, broker environment, and operator authorization context.
- [ ] Missing broker SDK credentials, entitlements, or environment configuration fail closed.
- [ ] Health/readiness checks distinguish process liveness from broker execution readiness.

### Broker SDK And Capital Authorization

- [ ] Broker entitlement gaps are listed explicitly.
- [ ] Subaccount or environment isolation requirements are named for paper, canary, and live.
- [ ] Capital authorization has an owner, approval point, and rejection behavior.
- [ ] No shared credential or implicit capital binding path is introduced.

### Promotion Gate Linkage

- [ ] Canary activation references P1-LIVE-PLAN-001 prerequisites, observation windows, stability conditions, performance limits, and governance approvals.
- [ ] Live activation requires canary graduation and human approvals from reviewer, risk owner, and operator.
- [ ] Rollback target requirements remain explicit before canary activation.
- [ ] RuntimeBinding lineage is preserved for rollback, replacement, and retirement.

### Kill Switch And Drill Evidence

- [ ] Kill-switch readiness includes both command dispatch and runtime-manager follow-through.
- [ ] `telemetry_ack.ack_status = acknowledged` is required before emergency control is considered runtime-confirmed.
- [ ] `telemetry_ack.ack_status = fail_closed` is treated as safest-state fallback, not success.
- [ ] Drill prerequisites cover at least pause/risk_off and liquidate/terminate or replacement behavior where applicable.
- [ ] Audit persistence and idempotency state are durable before acknowledged status is returned.

## Reviewer Focus

Codex should review whether the parent owner uses this packet as support without accidentally promoting it into canonical truth. High-signal review questions:

1. Does the parent deliverable preserve production live as fail-closed?
2. Are readiness gaps stated as blockers or prerequisites instead of hidden assumptions?
3. Are broker entitlement, subaccount, and capital authorization concerns independently visible?
4. Does the plan require kill-switch telemetry ack before claiming emergency control readiness?
5. Does the plan avoid modifying OpenClaw's role into an execution kernel?

## Handoff Notes

This packet was approved by Codex review as a sidecar artifact. The parent owner may absorb any checklist items into the canonical parent deliverable at their discretion, but this file itself is not canonical policy.

Suggested reviewer disposition:

- Approve if the packet remains support-only and covers parent acceptance, dependencies, and fail-closed review points.
- Request changes if any statement appears to override L1 policy, enables live execution, or assumes broker/capital authorization is already satisfied.

## Verification

Performed on 2026-05-01:

```bash
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/p2_live_kernel_001_sidecar_acceptance.md
sed -n '1,240p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,240p' ai-status.json
sed -n '1,220p' docs/04/CANARY_LIVE_ACTIVATION_CRITERIA_AND_RUNBOOK.md
sed -n '1,220p' KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md
sed -n '1,220p' OPENCLAW_RUNTIME_CONTRACT.md
sed -n '1,220p' docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/execution-materialization.md
git status --short
```

Closeout verification performed on 2026-05-01:

```bash
sed -n '1,260p' support/sidecars/P2-LIVE-KERNEL-001/P2-LIVE-KERNEL-001-SIDECAR-ACCEPTANCE.md
sed -n '1,220p' support/reviews/P2-LIVE-KERNEL-001-SIDECAR-ACCEPTANCE-codex-review.md
git status --short
```
