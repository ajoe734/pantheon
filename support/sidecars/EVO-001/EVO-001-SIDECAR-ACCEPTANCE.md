# EVO-001 Sidecar Acceptance Packet

**Sidecar task:** `EVO-001-SIDECAR-ACCEPTANCE`  
**Helper parent:** `EVO-001`  
**Helper kind:** `acceptance_packet`  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Claude`  
**Generated:** `2026-05-16T11:42:52Z`  
**Review approved:** `2026-05-16T11:48:34Z` by `Claude`  
**Owner closeout verification:** `2026-05-16T12:15:00Z` by `Codex`  
**Status:** `review-approved; owner-closeout-ready`

> Scope constraint: support artifact only. This packet summarizes acceptance
> criteria, dependency routing, verification evidence, and reviewer attention
> points for `EVO-001`. It does not modify canonical truth, L1 policy, runtime
> code, registry code, governance implementation, or BFF implementation.

## Executive Summary

`EVO-001` is currently a parent `todo` task with title `EvolutionDecision
service` and no task-local acceptance criteria recorded in `ai-status.json`.
This sidecar therefore turns the existing repo and policy surface into a
reviewable acceptance map for the parent owner.

Current repo read:

1. `services/evolution/main.py` already exposes an Evolution service API for
   proposal creation, incident-derived proposals, list/get, review, approval,
   rejection, cancellation, execution, boundary lookup, observation reports,
   rollback follow-through, redeploy follow-through, action paths, threshold
   evaluation, and health.
2. `services/control-plane/governance/evolution_decision.py` already owns the
   first-class `EvolutionDecision` object, risk inference, lifecycle guards,
   actor-role matrices, cooldown/observation fields, JSON persistence, and
   single-active enforcement.
3. `services/control-plane/governance/evolution_controller.py` already owns
   normal-path routing boundaries for research, governance freeze, deployment
   follow-through, rollback companion commands, and threshold classification.
4. `services/evolution/test_evolution_service.py` provides focused API
   coverage and passed in this sidecar session: `57 passed in 29.97s`.

Important boundary: this sidecar does not declare the parent done. It says the
existing service baseline is strong enough to review, and it identifies the
scope questions that Claude should settle before implementing or closing
`EVO-001`.

## Sources Used

| Source | Role |
|---|---|
| `ai-status.json` | Durable state for parent task and this sidecar |
| `.orchestrator/task-briefs/evo_001_sidecar_acceptance.md` | Sidecar scope, reviewer, artifact target, support-only rule |
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | L1 lifecycle, action classes, threshold defaults, action routing boundary |
| `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` | L1 cooldown, observation, single-active, escalation rules |
| `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` | Evolution trigger and concurrency semantics |
| `services/control-plane/governance/evolution_decision.contract.md` | Service contract derived from the L1 policy |
| `services/control-plane/governance/evolution_decision.py` | Current domain object and store implementation |
| `services/control-plane/governance/evolution_controller.py` | Current routing, threshold, and follow-through implementation |
| `services/evolution/main.py` | Current Evolution service API surface |
| `services/evolution/models.py` | Current request/response models |
| `services/evolution/test_evolution_service.py` | Focused verification suite |
| `services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py` | Downstream BFF v5 loop/sentinel read-surface context |

The task brief listed the phase6 planning session as relevant. It contains the
accepted P0 paper-loop planning context but no direct `EVO-001` proposed task
entry; this packet therefore uses the current task state and repo-local
evolution surfaces as the concrete review sources.

## Parent Acceptance Checklist

| Criterion | Current read | Evidence | Parent reviewer action |
|---|---|---|---|
| Service is first-class, not narrative-only | pass | `services/evolution/main.py` exposes HTTP routes; `services/evolution/models.py` exposes typed request/response models | Confirm parent scope accepts the current route inventory |
| `EvolutionDecision` lifecycle is implemented | pass | API and domain object cover `proposed -> reviewed -> approved -> executed`, plus `rejected`, `canceled`, and `superseded` fields | Verify no missing parent-specific lifecycle transition is expected |
| Evidence is mandatory | pass | Proposal without `evidence_refs`, `threshold_snapshots`, `linked_incident_id`, or `linked_postmortem_id` returns 422 in tests | No parent action unless acceptance requires stricter evidence types |
| Risk is derived from action/stage | pass | Low-risk retrain, medium paper freeze, and high live freeze are tested; freeze without target stage is rejected | Confirm parent does not want caller-supplied risk to override policy |
| Actor-role gates match L1 | pass | Review/approve/execute wrong-role tests pass; medium-risk operator-alone approval is rejected | Check against L1 owner wording before approval |
| Approval linkage exists from review onward | pass | Review request requires `approval_decision_id`; response carries it through later states | Parent may decide whether a live `ApprovalDecision` service write is in scope or contract-level linkage is enough |
| Cooldown and observation windows are set on execute | pass | Low-risk `3d/7d`, medium `7d/7d`, high `14d/14d` are directly asserted | Confirm windows are anchored at authoritative downstream acceptance time for parent needs |
| Single-active rule blocks duplicate target decisions | pass | Duplicate same-target proposal is rejected while an active decision exists | No extra parent action unless queue/merge semantics are required now |
| Incident/postmortem proposal path is proposal-only | pass | `/api/evolution/proposals/from-incident` derives links and metadata while leaving runtime/broker/capital mutation disallowed | Confirm this is sufficient for post-incident entry points |
| Postmortem reverse link is synchronized | pass | Tests verify `Postmortem.linked_evolution_decision_id` after proposal | Parent should confirm incident store integration remains desired |
| Boundary/action-path endpoints expose write-owner separation | pass | `/boundary` and `/action-paths` return owner roles, cooldowns, execution plane, and follow-through families | Review wording for no shadow runtime/deployment authority |
| Rollback follow-through remains companion-only | pass | `rollback-followthrough` requires approved freeze and `active_binding_id`; it emits runtime follow-through metadata through controller | Confirm parent does not expect Evolution service to mutate `RuntimeBinding` directly |
| Redeploy follow-through is not a new evolution action | pass | Redeploy is accepted only after eligible executed parent actions; executed freeze is rejected | Confirm deployment plane will consume returned dispatch command |
| HTTP errors are controlled | pass | Invalid evidence ref and invalid freeze mode return 400/422 rather than 500 | No extra parent action |
| Focused test suite passes | pass | `python3 -m pytest services/evolution/test_evolution_service.py -q` -> `57 passed in 29.97s` | Use as current verification baseline |
| BFF v5 loop/sentinel integration is not silently implied | attention | Current BFF v5 tests derive loop runs and sentinel findings from read-store incidents/fallback data, not the Evolution service | Decide whether EVO-001 owns only the service or also a read-model bridge |

## Dependency Map

| Dependency | Direction | Why it matters for `EVO-001` |
|---|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | upstream L1 truth | Defines lifecycle, owner tiers, threshold defaults, freeze vs rollback, and action routing |
| `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` | upstream L1 truth | Defines cooldown/observation windows, single-active rule, and escalation behavior |
| `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` | upstream L1 truth | Confirms evolution is threshold-triggered plus sweep-based, and only one active decision may exist per target |
| `services/control-plane/governance/evolution_decision.contract.md` | contract | Normalizes the object shape, evidence fields, lifecycle invariants, approval linkage, and store rules |
| `services/control-plane/governance/evolution_decision.py` | domain dependency | Holds `EvolutionDecision`, role matrices, risk inference, validation, persistence, and single-active enforcement |
| `services/control-plane/governance/evolution_controller.py` | domain dependency | Holds threshold classification, boundary routing, cooldown calculation, follow-through command envelopes, and redeploy/rollback rules |
| `services/evolution/models.py` | API boundary | Defines typed request/response models for proposal, lifecycle, threshold, boundary, observation, redeploy, and rollback surfaces |
| `services/evolution/main.py` | parent service surface | Exposes the Evolution service routes that the parent can verify or extend |
| `services/incident` | upstream evidence source | Supplies `IncidentCase` and `Postmortem` records for incident-derived proposals and reverse links |
| `services/control-plane/governance/approval_decision.py` | governance dependency | Supplies `EvidenceRef`, `EvidenceRefType`, and risk-level primitives; parent service stores the approval id rather than replacing approvals |
| `services/control-plane/governance/deployment_plan.py` | downstream boundary | Supplies stage/rollback types; redeploy and freeze-stage follow-through must remain deployment-plane work |
| `services/runtime-manager/*` | downstream boundary | Runtime manager already has evolution follow-through endpoints, but `EVO-001` must not directly mutate runtime binding state |
| `services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py` | downstream read-surface context | Confirms `/bff/v5/loop-runs` and `/bff/v5/sentinel/findings` exist as BFF read surfaces, currently sourced through incidents/read-store rather than the Evolution service |

## Parent Scope Questions

These are not sidecar blockers, but they are the main review questions before
Claude decides whether to implement more code or move the parent to review.

1. Does `EVO-001` mean "land/verify the current `services/evolution` service",
   or does it require new implementation beyond the already-present service?
2. Should parent acceptance require a live `ApprovalDecision` object write, or
   is carrying `approval_decision_id` and enforcing role/state invariants enough
   for this slice?
3. Should parent acceptance require a BFF/read-model bridge from
   `EvolutionDecision` into `/bff/v5/loop-runs` or `/bff/v5/sentinel/findings`,
   or are those separate Sprint 6 tasks?
4. Should `executed` be verified only by controller dispatch metadata in this
   slice, or must it also call live deployment/runtime/research downstream
   services in integration tests?
5. Should the JSON-file store remain acceptable for this P3 service slice, or
   does parent scope require a durable Postgres-backed store now?

## Suggested Parent Review Plan

1. Re-run the focused suite:

   ```text
   python3 -m pytest services/evolution/test_evolution_service.py -q
   ```

2. Spot-check route inventory:

   ```text
   rg -n "^@app\\.(get|post)" services/evolution/main.py
   ```

3. Spot-check canonical invariants:

   ```text
   rg -n "APPROVAL_OWNER_MATRIX|REVIEW_OWNER_MATRIX|single-active-rule|infer_risk_level" \
     services/control-plane/governance/evolution_decision.py

   rg -n "boundary_for|execute_approved|create_redeploy_followthrough|ThresholdEvaluator" \
     services/control-plane/governance/evolution_controller.py
   ```

4. If BFF linkage is in scope, run the adjacent read-surface suite and decide
   whether its current incident/read-store source is enough:

   ```text
   python3 -m pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py -q
   ```

## Verification Snapshot

Sidecar verification performed:

```text
python3 -m pytest services/evolution/test_evolution_service.py -q
# 57 passed in 29.97s
```

Read-only inspection performed:

```text
jq '.tasks[] | select(.id|test("^EVO"))' ai-status.json
sed -n focused reads of EVOLUTION_REVIEW_AND_THRESHOLDS.md,
  EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md,
  LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md,
  services/evolution/main.py,
  services/evolution/models.py,
  services/evolution/test_evolution_service.py,
  services/control-plane/governance/evolution_decision.py,
  services/control-plane/governance/evolution_controller.py,
  services/control-plane/governance/evolution_decision.contract.md
rg -n focused route/test/invariant searches for evolution, loop-runs,
  and sentinel/findings surfaces
```

No canonical, runtime, registry, governance, or BFF implementation files were
modified by this sidecar.

Closeout verification performed by `Codex` after reviewer approval:

```text
python3 -m pytest services/evolution/test_evolution_service.py -q
# 57 passed in 63.05s (0:01:03)
```

Closeout worktree note: this sidecar artifact is the only task-owned support
file updated during owner closeout. Other dirty files in the worktree belong to
separate active/generated task state and are intentionally not staged for this
sidecar commit.

## Reviewer Handoff

To `Claude`, sidecar reviewer and parent owner:

1. Review this packet as a support-only acceptance/dependency map.
2. If the current `services/evolution` baseline is the intended parent
   deliverable, use the checklist and verification commands to decide the parent
   `EVO-001` review handoff.
3. If parent scope also requires BFF v5 read-model integration, live downstream
   service calls, or durable store migration, keep those as explicit parent
   follow-ups rather than reading them into this sidecar.

Recommended sidecar disposition: approve this support packet as truthful and
useful for `EVO-001` owner review. Parent implementation/finalization remains
with Claude.
