# OPENCLAW-CRON-WRITE-SCOPE Sidecar Acceptance Packet and Dependency Map

**Sidecar Task ID**: `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE`
**Parent Task**: `OPENCLAW-CRON-WRITE-SCOPE`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-07-03

> Scope constraint: this is a support artifact only. It does not modify
> canonical truth, L1 policy, the OpenClaw runtime contract, the OC-001
> permission model, the OC-002 cron package, or the control-plane router
> implementation. The parent owner decides whether and how to fold this
> packet into `OPENCLAW-CRON-WRITE-SCOPE` implementation or closeout.

---

## 1. Scope Snapshot

`OPENCLAW-CRON-WRITE-SCOPE` is **not currently present as a task entry in
`ai-status.json`**. This packet was dispatched with only a parent task id and
a target artifact path; no owner, reviewer, phase, or acceptance list exists
for the parent in the canonical task board today. This is recorded here as a
fact, not assumed away:

```bash
grep -n "OPENCLAW-CRON-WRITE-SCOPE" ai-status.json   # no matches
```

Because there is no parent acceptance list to trace against, this packet
builds a working acceptance map from the nearest canonical source: the
already-archived permission and cron foundation tasks, plus the current repo
state of the cron package and router evaluator. It also flags the concrete
scope questions the real parent owner should settle before this task is
implemented or closed.

Working interpretation of "cron write scope", derived from
`services/control-plane/permissions/contract.md` §6 rule 5 ("deny cron jobs
from writing directly to live execution surfaces"):

> Cron-triggered OpenClaw workflows (`pantheon.ingest`, `pantheon.review`,
> `pantheon.retrain`, `pantheon.deploy`) must never be able to write to a live
> execution surface directly. Any write-capable action they take must (a) stay
> within the workflow's declared `allowed_tool_classes`, and (b) go through a
> first-class governed artifact (`WorkflowHandoff`, `DeploymentPlan`,
> `ApprovalDecision`) rather than a direct execution call.

---

## 2. Sources Used

| Source | Role |
|---|---|
| `ai-status.json` | Confirmed no current tracked entry for `OPENCLAW-CRON-WRITE-SCOPE` |
| `ai-task-archive/tasks/OC-001.json` | Archived parent: tool permission model, `done` |
| `ai-task-archive/tasks/OC-002.json` | Archived parent: governed cron workflow package, `done` |
| `ai-task-archive/tasks/P4-001.json` | Archived parent: router contract, `done` |
| `services/control-plane/permissions/contract.md` | OC-001 canonical tool-class and deny-rule contract, including the cron write-scope rule |
| `services/control-plane/permissions/tool_policy_schema.json` | Machine-readable policy object shape |
| `services/control-plane/router/main.py` | `_evaluate_permission` deny-first v1 implementation (P4-001) |
| `services/control-plane/router/test_main.py` | Existing router permission test coverage |
| `services/control-plane/cron/workflows.py` | OC-002 `WorkflowDefinition` catalog: `allowed_tool_classes`, `approval_required` per workflow |
| `services/control-plane/cron/models.py` | `WorkflowDefinition` and dispatch envelope shape |
| `services/control-plane/cron/service.py` | Cron orchestration and handoff/plan projection |
| `services/control-plane/cron/persona_cron_registrar.py` | Per-persona OpenClaw cron job registration |
| `services/control-plane/cron/test_cron.py`, `test_persona_cron_registrar.py` | Existing focused test coverage |
| `OPENCLAW_RUNTIME_CONTRACT.md` §5, §7, §9, §10 | L1 responsibility boundary, isolation, and degraded-mode rules for cron/workflow execution |

---

## 3. Acceptance Checklist

| Criterion | Current read | Evidence | Status |
|---|---|---|---|
| Deny-first permission model exists and is canonical | pass | `services/control-plane/permissions/contract.md` §5–6 defines deterministic deny-first evaluation order and non-removable global deny rules | PASS |
| Cron subject type and `system` role/tier are explicitly modeled | pass | `services/control-plane/router/main.py` `_CHANNEL_TIER["cron"]="system"`, `_CHANNEL_ROLE["cron"]="system"`; `tool_policy_schema.json` `subject_scope.subject_type` enum includes `cron` | PASS |
| `lean_direct` tool class is denied regardless of channel, including cron | pass | `main.py` rule 1: `if tool_class == "lean_direct": return DENY` — unconditional, channel-independent | PASS |
| Each OC-002 workflow declares an explicit, narrow `allowed_tool_classes` | pass | `workflows.py`: ingest=`(research, status)`, review=`(status, monitoring)`, retrain=`(research, status, monitoring)`, deploy=`(deployment, status)` — none include `lean_direct` or `execution_signal` | PASS |
| High-risk workflows require approval before any write lands | pass | `workflows.py`: `REVIEW_WORKFLOW.approval_required=True`, `RETRAIN_WORKFLOW.approval_required=True`, `DEPLOY_WORKFLOW.approval_required=True` and `uses_promotion_gate=True` | PASS |
| Deploy workflow cannot call LEAN directly; it must produce a `DeploymentPlan` | pass | `services/control-plane/cron/README.md`: "deploy never calls LEAN directly; it must create a first-class DeploymentPlan and execution projection first"; `test_cron.py::test_deploy_uses_stage_planner_factory`, `test_deploy_requires_approved_artifact`, `test_deploy_requires_matching_approval_decision` | PASS |
| `execution_signal` tool class is denied for all non-operator tiers, including `system`/cron | pass | `main.py` rule 4: only `tier == "operator"` gets `ALLOW_WITH_APPROVAL`; every other tier (including `system`) is denied | PASS |
| Router-level enforcement of the cron write-scope deny rule has direct test coverage | **gap** | No test in `test_main.py` calls `_evaluate_permission` (or the route path) with `channel="cron"`. Coverage exists for `console`, and implicitly for chat channels, but not for the cron subject type that OC-001 §6 rule 5 specifically names | ATTENTION |
| `deployment` tool class is scoped correctly for the `system`/cron tier | **gap** | `main.py` rule 2 denies deployment only when `tier not in ("operator", "system")`. For `channel="cron"` (`tier="system"`) this check does **not** deny, and no other rule applies `ALLOW_WITH_APPROVAL` to a bare `deployment` intent the way it does for `execution_signal` and `governance.approve` — a cron-originated `deployment` intent falls through to the default `ALLOW` branch with no approval gate at the router layer | ATTENTION |
| Fine-grained approval gating for deploy is not lost, because it is enforced one layer down in OC-002 | pass (defense-in-depth caveat) | `DEPLOY_WORKFLOW.approval_required=True` and `test_deploy_requires_matching_approval_decision` mean the cron **service** layer still requires an `ApprovalDecision` before a `DeploymentPlan` is created, independent of the router's coarser tool-class check | PASS, but router-layer gap above should not be treated as closed by this alone |
| Degraded/quarantined OpenClaw sessions cannot trigger new cron/workflow jobs | pass (policy-level) | `OPENCLAW_RUNTIME_CONTRACT.md` §10.1: "不觸發新 workflow / cron job" while degraded; §10.3 states kill-switch safety must not depend on OpenClaw | PASS at policy level; no runtime enforcement code was located in this repo slice, since the OpenClaw-compatible runtime itself is upstream, not Pantheon-owned |
| Cron write actions carry auditable identity fields | pass | `OPENCLAW_RUNTIME_CONTRACT.md` §7.3 requires `persona_id`, `session_id`, `trace_id`, `request_id`, `actor_type`, `environment` on every runtime session; `services/control-plane/cron/models.py` dispatch envelopes carry `request_id`, `policy_id`, and governance context | PASS |

---

## 4. Dependency Map

### Upstream (already-archived) foundations

| Dependency | Current status | Why it matters |
|---|---|---|
| `OC-001` | archived `done` | Defines the tool-class table, deny-first evaluation order, and the exact rule this task is named after ("deny cron jobs from writing directly to live execution surfaces", contract §6 rule 5) |
| `OC-002` | archived `done` | Defines the four governed cron workflows and their `allowed_tool_classes` / `approval_required` fields, which is where cron write scope is actually declared today |
| `OC-003` | archived `done` | Supplies `StrategySpec` / `WorkflowHandoff` schemas validated by `schema_validation.py`, bounding what shape a cron write can even take |
| `P4-001` | archived `done` | Supplies the router's `_evaluate_permission` deny-first implementation, including the `cron` -> `system` tier/role mapping |
| `DEP-001` | archived `done` | Owns `DeploymentPlan` semantics that `pantheon.deploy` must route through instead of a direct execution call |
| `GOV-001` | archived `done` | Owns `ApprovalDecision` semantics that gate `review`, `retrain`, and `deploy` workflows |

### Adjacent/current-sprint context

| Task | Current status | Relationship |
|---|---|---|
| `LOOP-AUTO-DEP-001` / `LOOP-AUTO-DEP-002` / `LOOP-AUTO-DEP-003` | `todo` | Current deployment-saga autopilot work; any router or OC-002 write-scope change should stay consistent with the saga's idempotent dispatch and DLQ semantics |
| `OCLAW-PMEM-000..005` | `todo` / in progress across lanes | Current OpenClaw persona-memory gap closure work; touches the same adapter/runtime boundary and should not be duplicated by this task |

Recommended dependency disposition:

1. Treat OC-001/OC-002/P4-001/DEP-001/GOV-001 as the load-bearing prior art —
   do not re-derive the tool-class or deny-rule model from scratch.
2. If `OPENCLAW-CRON-WRITE-SCOPE` is meant to *close the router-layer gap*
   identified in §3 (cron + `deployment` intent falling through to plain
   `ALLOW`), that is a small, well-scoped router change plus a new
   `channel="cron"` test in `test_main.py` — it does not require touching
   OC-002's workflow catalog, which already carries the correct
   `approval_required` flags one layer down.
3. Do not let this task block on `LOOP-AUTO-DEP-*` or `OCLAW-PMEM-*`; it is
   narrower in scope (permission/router boundary, not saga orchestration or
   persona memory).

---

## 5. Verification Evidence

Focused test runs performed by this sidecar (read-only; no source files
touched):

```bash
PYTHONPATH="$PWD/services/control-plane/cron:$PWD/services/control-plane/router" \
  python3 -m pytest services/control-plane/cron/test_cron.py services/control-plane/router/test_main.py -q
# 21 passed in 3.88s

PYTHONPATH="$PWD/services/control-plane/cron" \
  python3 -m pytest services/control-plane/cron/test_persona_cron_registrar.py -q
# 19 passed in 1.34s
```

| Verification target | Evidence provided |
|---|---|
| `services/control-plane/cron/test_cron.py` | Dispatch envelope shape, workflow catalog, handoff validation, deploy approval/promotion-gate requirements all pass |
| `services/control-plane/router/test_main.py` | Existing deny-first evaluator behavior (console, chat, governance-approval paths) passes; confirms no regression, but also confirms the `channel="cron"` gap is untested rather than untestable |
| `services/control-plane/cron/test_persona_cron_registrar.py` | Per-persona cron job registration against the workflow catalog passes |

No canonical, runtime, registry, governance, router, or cron implementation
files were modified by this sidecar.

---

## 6. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| That `OPENCLAW-CRON-WRITE-SCOPE` is implemented or closed | Parent owner, once assigned |
| That the router-layer gap in §3 (cron + `deployment` falling through to plain `ALLOW`) has been fixed | Parent owner — this packet only documents the gap and a minimal-fix direction |
| That the upstream OpenClaw-compatible runtime itself enforces degraded/quarantine cron suspension in code reachable from this repo | Upstream OpenClaw runtime, per `OPENCLAW_RUNTIME_CONTRACT.md` §0/§5 responsibility boundary |
| That this packet supersedes or re-opens the archived `OC-001` / `OC-002` / `P4-001` tasks | Those remain archived `done`; this is new follow-up scope only |
| Any new acceptance criteria that override a future explicit `OPENCLAW-CRON-WRITE-SCOPE` entry once created in `ai-status.json` | Parent owner's own acceptance list takes precedence over this working interpretation |

---

## 7. Reviewer Checklist for Claude

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime/router/cron implementation edits? | Yes — only this support packet was created. |
| Is the "parent task not tracked in ai-status.json" fact stated plainly rather than papered over? | Yes — §1 shows the exact grep with no matches. |
| Is the working definition of "cron write scope" grounded in an existing canonical rule rather than invented? | Yes — traced to `permissions/contract.md` §6 rule 5. |
| Does the packet distinguish PASS items from a genuine open gap? | Yes — §3 marks two ATTENTION rows: missing `channel="cron"` router test coverage, and the `deployment` tool-class fall-through to plain `ALLOW` for the `system` tier. |
| Is verification evidence reproducible and scoped to read-only test runs? | Yes — commands and pass counts are in §5. |
| Are dependencies distinguished between archived prior art and active adjacent tasks? | Yes — §4 splits upstream archived foundations from current-sprint adjacent tasks. |

---

## 8. Handoff

**To**: `Claude`
**From**: `Claude2`
**Requested review outcome**: Approve this sidecar if the acceptance checklist
and dependency map are accurate support material for standing up
`OPENCLAW-CRON-WRITE-SCOPE` as a real tracked task.

Recommended parent-owner next steps:

1. Create the `OPENCLAW-CRON-WRITE-SCOPE` entry in `ai-status.json` with an
   explicit acceptance list (this packet's §3 can seed it) and a real
   owner/reviewer pair.
2. Decide whether the task's actual deliverable is the router-layer fix
   identified in §3 (add a `channel="cron"` deny/approval rule for the
   `deployment` tool class, plus a regression test), a broader policy-storage
   migration, or both.
3. Keep OC-002's workflow-level `approval_required` gating as the
   defense-in-depth layer regardless of what changes at the router layer.
4. Finalize this sidecar as support material only; it should not itself be
   marked `done` as if it were the parent implementation.

---

## 9. Closeout Update (2026-07-03)

Between this packet's review approval and this sidecar's own closeout, a
real `OPENCLAW-CRON-WRITE-SCOPE` entry was created in the canonical
`ai-status.json` (root `PANTHEON_STATUS_ROOT`, not the per-task worktree
copy) by `dispatch_openclaw_live_wiring_followups_2026-07-03`, owned by
`Claude`, reviewer `Codex`, status `blocked` on `Human/Ops`.

Its actual scope is **materially different** from the working
interpretation this packet built in §1–§4:

- **Real scope**: the OpenClaw gateway's paired adapter device only holds
  `operator.write` scope; `cron.add`/`update`/`remove`/`run` require
  `operator.admin` on OpenClaw 2026.6.8, so live cron registration through
  the adapter fails closed with a pairing/scope error. The fix is an
  operator-approved device scope upgrade (`scripts/openclaw-approve-adapter-cron-scope.sh`)
  plus a live smoke (`scripts/openclaw-cron-write-scope-smoke.sh`), not a
  router/tool-class permission change.
- **This packet's interpretation** (§1): a router-layer deny-first
  tool-class gap for `channel="cron"` + `deployment` intents in
  `services/control-plane/router/main.py`. That gap is real and still
  worth fixing on its own merits, but it is **not** what the now-created
  `OPENCLAW-CRON-WRITE-SCOPE` task is asking for.

Disposition: this packet's acceptance checklist (§3) and dependency map
(§4) remain accurate as independent analysis of the router/OC-002
permission surface, and the router-layer gap they surface is still an
open, real finding. But they should **not** be read as the acceptance
criteria for the actual `OPENCLAW-CRON-WRITE-SCOPE` task — that task's own
`acceptance` array in `ai-status.json` (adapter `cron.add` succeeds,
full BFF persona-create path registers 4 cron jobs, scope survives a
`openclaw-data` volume/container recreate) is the governing acceptance
list. If the router-layer gap should become its own tracked task, it
needs a new task id distinct from `OPENCLAW-CRON-WRITE-SCOPE`.
