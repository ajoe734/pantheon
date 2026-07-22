# Acceptance Packet: LOOP-AUTO-EVO-005

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE`
**Parent task:** `LOOP-AUTO-EVO-005` — Prove evolution rollback and follow-through
**Parent owner:** Gemini2
**Parent reviewer:** Claude
**Prepared by:** Claude
**Date:** 2026-06-27
**Packet status:** reviewed and approved — Claude2 review complete 2026-06-27

> **Scope constraint:** support artifact only. This packet does not edit canonical truth,
> L1 policy, runtime contracts, registry/governance behavior, or the parent task's
> implementation. It assembles the acceptance checklist, dependency map, evidence surface,
> and reviewer handoff guidance for LOOP-AUTO-EVO-005.

---

## 1. Purpose

This sidecar reduces onboarding friction for LOOP-AUTO-EVO-005 by:

1. Restating the formal acceptance criteria and mapping each to the current repo surface.
2. Providing a dependency map so the parent task owner (Gemini2) and reviewer (Claude) can
   confirm all upstream gates are landed before evidence collection begins.
3. Identifying what evidence the parent task must produce to satisfy `proven-live` maturity.
4. Flagging gaps and constraints that are non-obvious from the task brief alone.

---

## 2. Parent Task Truth

From `ai-status.json`:

| Field | Value |
|---|---|
| ID | `LOOP-AUTO-EVO-005` |
| Title | Prove evolution rollback and follow-through |
| Phase | Global Loop Autopilot / Wave 5 Postmortem Evolution |
| Owner | Gemini2 |
| Reviewer | Claude |
| Status | `todo` |
| Current maturity | `reconciled` |
| Target maturity | `proven-live` |
| Loop IDs | `evolution` |
| Depends on | `LOOP-AUTO-EVO-004` |

**Formal acceptance criteria:**

| # | Criterion |
|---|-----------|
| AC-1 | Evidence proves approved rollback command reaches runtime-manager or deployment |
| AC-2 | BFF shows proposed, reviewed, approved, dispatched, and executed stages |
| AC-3 | Failure path records blocked reason and retry state |

**Proof required (from `dispatch_rules`):**

- Unit tests
- Contract tests
- Local service smoke
- Restart or replay evidence when worker or runtime behavior changes

**Non-goals:**

- No live-capital execution
- No approval gate bypass
- No panel-only closure
- No seed fixture as live proof

---

## 3. Dependency Map

```
LOOP-AUTO-000 (todo) — loop catalog schema and maturity registry
  └─ Foundation: loop_id, desired state, actual state, evidence fields
       │
       ├─ LOOP-AUTO-EVO-001 (todo) — create postmortem drafts from resolved incidents
       │     └─ resolved IncidentCase → Postmortem draft (services/postmortems)
       │           │
       │           └─ LOOP-AUTO-EVO-002 (todo) — bridge postmortems to evolution proposals
       │                 └─ Postmortem published → EvolutionDecision proposed
       │                       │
       │                       └─ LOOP-AUTO-EVO-003 (todo) — daily sweep worker [parallel path]
       │                             └─ Threshold/cooldown sweep → EvolutionDecision
       │
       ├─ LOOP-AUTO-DEP-001 (todo) — deployment saga outbox consumer
       │     └─ Durable outbox consumer required before evolution dispatches to deployment plane
       │
       └─ LOOP-AUTO-EVO-004 (todo) — dispatch approved evolution actions through gates
             └─ Provides: dispatch_worker.py + /api/evolution/proposals/{id}/execute
                  │
                  └─ LOOP-AUTO-EVO-005 (todo) ← THIS TASK
                        └─ Prove the rollback/follow-through path end-to-end
```

**Policy sources:**

| Policy file | Relevant sections |
|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | §9–§12: approved action dispatch, rollback semantics, follow-through gating |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Rollback action types, position handling during freeze/replace |
| `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` | Evolution loop trigger model and race-condition handling |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | Fast-path constraint: rollback must route through runtime-manager, not bypass |

**Upstream dependency status at sidecar creation (2026-06-27):**

| Dependency | Status | Blocking aspect for EVO-005 |
|---|---|---|
| LOOP-AUTO-EVO-004 | `todo` | Dispatch worker (dispatch_worker.py) must be merged; rollback route must be callable |
| LOOP-AUTO-EVO-002 | `todo` | Postmortem bridge required for realistic proposal lineage in evidence; can substitute a hand-crafted approved decision for smoke |
| LOOP-AUTO-DEP-001 | `todo` | Required if the evidence scenario routes through the deployment plane; not required for rollback-only proof |

**Minimum pre-condition before EVO-005 evidence collection:**

LOOP-AUTO-EVO-004 must be merged and its dispatch_worker must reach the evolution service `/execute` endpoint successfully. The other dependencies (EVO-001, EVO-002, DEP-001) can be worked around in the evidence scenario by creating a hand-crafted `approved` EvolutionDecision directly via the proposals API.

---

## 4. Current Implementation Surface

The following files are in scope for the evidence collection and proof verification:

### 4.1 Evolution service — dispatch path

| File | Role |
|---|---|
| `services/evolution/dispatch_worker.py` | Polls `?decision_state=approved`, calls `/boundary`, calls `/execute`. This is the automated dispatch path from EVO-004. |
| `services/evolution/main.py` | `/api/evolution/proposals/{id}/execute` — transitions `approved` → `executed`; `/api/evolution/proposals/{id}/rollback-followthrough` — convenience endpoint that fixes `freeze_mode=rollback`; `/api/evolution/proposals/{id}/redeploy-followthrough` — redeploy path after retrain. |
| `services/evolution/test_dispatch_worker.py` | Unit tests for dispatch_worker; covers `run_poll` idempotency, approved→executed dispatch, observation report. |
| `services/evolution/test_evolution_service.py` | Integration tests for the proposals API lifecycle. |

### 4.2 Runtime-manager — receiving end

| File | Role |
|---|---|
| `services/runtime-manager/main.py` | `/api/rollback` (POST) — canonical rollback action (replace, pause_then_replace, liquidate_then_replace); `/api/evolution/freeze` (POST) — freeze follow-through; `/api/evolution/retrain` (POST) — retrain dispatch; `/api/evolution/redeploy` (POST) — redeploy follow-through. |
| `services/runtime-manager/service.py` | RuntimeManagerService: `rollback()`, `evolution_freeze()`, `evolution_retrain()`, `evolution_redeploy()`. |
| `services/runtime-manager/test_runtime_manager.py` | Covers rollback routes, kill-switch, fleet desired state, evolution routes. |

### 4.3 Decision state machine

The current evolution decision states are: `proposed` → `reviewed` → `approved` → `executed`.

**Key observation for AC-2:** The acceptance criterion asks BFF to show `proposed, reviewed, approved, dispatched, and executed` stages. The current state machine has no explicit `dispatched` intermediate state between `approved` and `executed`. The dispatch worker calls `/execute` which transitions `approved` → `executed` atomically. This means AC-2's `dispatched` stage is currently observable only through `execution_result` fields inside an `executed` decision, not as a separate state. The parent task (or a follow-up) must either:

- a) Add a `dispatched` state between `approved` and `executed` in the decision store; or
- b) Surface the dispatch timestamp and downstream-ack from `execution_result` as a sub-stage in the BFF read model.

This is the primary structural gap that AC-2 surfaces.

---

## 5. Acceptance Criteria Gap Analysis

### AC-1: Evidence proves approved rollback command reaches runtime-manager or deployment

**Current state: partially met (path exists; runtime integration not yet confirmed)**

The dispatch_worker calls `/api/evolution/proposals/{id}/execute` on the evolution service. The evolution service's `execute_approved()` via EvolutionController computes `DispatchCommand` (with `execution_plane`: `research`, `deployment`, or `runtime`). However, from the EVO-004 sidecar review packet (LOOP-AUTO-EVO-004-SIDECAR-REVIEW.md), the DispatchCommand objects are written into `execution_result` but not yet forwarded to the downstream runtime-manager rollback API.

If LOOP-AUTO-EVO-004 implements the actual downstream HTTP call (required by its own AC), then LOOP-AUTO-EVO-005's AC-1 becomes: produce a replay trace showing the rollback command was accepted by `POST /api/rollback` on the runtime-manager (HTTP 200, `new_binding` in response, rollback_parent set). Evidence must be a command log or test output, not a panel screenshot.

**What the parent task must produce for AC-1:**

- A replay script or test that:
  1. Creates an `approved` EvolutionDecision with `action_type=rollback` and `target_stage=paper`
  2. Runs the dispatch_worker (or calls `/execute` directly)
  3. Captures the HTTP 200 response from `POST /api/rollback` on runtime-manager showing `new_binding.rollback_parent` is set
- Output file: `docs/deployment/evidence/evo-005-rollback-followthrough-evidence.md` (or `.txt`)

### AC-2: BFF shows proposed, reviewed, approved, dispatched, and executed stages

**Current state: gap — no explicit `dispatched` state exists**

The five named stages (proposed, reviewed, approved, dispatched, executed) are not all distinct states in the current decision store. See §4.3 above.

**What the parent task must produce for AC-2:**

Choose one of the two approaches (and document which):

- **Option A (preferred):** Add `dispatched` as an explicit intermediate state. The dispatch_worker transitions `approved` → `dispatched` before calling the downstream API; the `/execute` endpoint transitions `dispatched` → `executed` after downstream confirmation. The BFF read model then natively exposes all five stages.
- **Option B (acceptable):** Keep the current `approved` → `executed` transition but surface the dispatch metadata as a sub-stage enrichment in the BFF evolution read model. The BFF endpoint (`/api/evolution/proposals/{id}`) must return a `stages` projection showing all five stages with timestamps, where `dispatched_at` comes from `execution_result.dispatch_timestamp` and `executed_at` from the existing field.

Either option requires a test that queries the BFF endpoint and asserts all five stage timestamps are present and non-null for a completed rollback scenario.

### AC-3: Failure path records blocked reason and retry state

**Current state: partial — error state exists but retry/blocked metadata unclear**

The current dispatch_worker propagates HTTP errors from the `/execute` endpoint but does not appear to write a `blocked_reason` or `retry_state` back into the decision. The runtime-manager rollback endpoint can return 422 (validation failure) or 500 (internal error), but neither of these is currently persisted into the EvolutionDecision as a structured `blocked_reason`.

**What the parent task must produce for AC-3:**

- A test or replay scenario that:
  1. Attempts to dispatch an evolution rollback where the runtime-manager returns a non-2xx response (e.g., missing `active_binding_id`)
  2. Asserts that the EvolutionDecision shows a `blocked_reason` string (not null/empty)
  3. Asserts that the decision is in a retryable state (e.g., back to `approved` or a new `blocked` state), not silently discarded
- The retry policy must be documented: how many times does the dispatch_worker retry before giving up, and what does the decision state look like after max retries?

---

## 6. Evidence Scope for `proven-live` Maturity

The parent task must produce evidence at the `proven-live` level. Per the project's maturity ladder, `proven-live` requires evidence that the path has been exercised end-to-end, not just that APIs exist. Specifically for this task:

| Evidence item | Minimum standard | Not sufficient |
|---|---|---|
| AC-1 rollback reach | Command log / test output showing `POST /api/rollback` returned 200 with `new_binding.rollback_parent` set | Panel screenshot; seed fixture; code-only assertion without runtime log |
| AC-2 stage visibility | API response or BFF response showing all five stage timestamps for a real completed scenario | Panel "looks right"; static documentation of expected shape |
| AC-3 failure path | Test output showing `blocked_reason` field non-null for a failed dispatch; assertion on retry state | Console log only; no structured field in the decision record |

The evidence packet file should include the exact commands used, full relevant output, and repo HEAD at evidence collection time.

---

## 7. Suggested File Scope for Parent Task

Files the parent task (LOOP-AUTO-EVO-005) is likely to touch for evidence production:

| File | Expected change |
|---|---|
| `services/evolution/main.py` | Add `dispatched_at` field to execution result, or add `dispatched` state transition; surface all five stages in decision response |
| `services/evolution/models.py` | Add `dispatched_at`, `blocked_reason`, `retry_count` fields to `ExecutionResult` or decision response model |
| `services/evolution/dispatch_worker.py` | Add blocked_reason write-back on downstream failure; add retry state tracking |
| `services/control-plane/governance/evolution_decision.py` | Add `dispatched` → `executed` transition if Option A is chosen for AC-2 |
| `services/runtime-manager/main.py` | No change expected unless AC-1 reveals a missing rollback route (should already exist via LOOP-AUTO-EVO-004) |
| `docs/deployment/evidence/evo-005-*.md` | New evidence packet file(s): rollback trace, stage visibility output, failure path test output |

Files that must NOT change in this task:

- `EVOLUTION_REVIEW_AND_THRESHOLDS.md` — L1 policy; no change unless a genuine policy error is found
- `ROLLBACK_AND_POSITION_SEMANTICS.md` — L1 policy; no change
- `services/control-plane/governance/evolution_controller.py` — controller is correct per EVO-004 scope; changes require a separate task
- Any deployment saga or runtime-manager core (changes must go through LOOP-AUTO-DEP-* or separate tasks)

---

## 8. Reviewer Guardrails

For Claude (parent reviewer) and Claude2 (sidecar reviewer):

**G-1: The task is a proof task, not an implementation task.**
LOOP-AUTO-EVO-005's primary deliverable is evidence, not new feature code. The reviewer should check that the evidence packet contains actual command output and runtime logs, not documentation of intent.

**G-2: AC-2 "dispatched" stage requires a structural decision.**
The choice between Option A (new `dispatched` state) and Option B (sub-stage enrichment in BFF) is non-trivial. If Option A is chosen, it changes the decision state machine and must be reviewed carefully for race conditions (what happens if the dispatch_worker crashes between `dispatched` and `executed`?). If Option B is chosen, the BFF projection must not fabricate timestamps.

**G-3: Do not accept a rollback evidence log from kill-switch as a proxy for AC-1.**
The kill-switch fast-path (DEPTH-EVO005) and the evolution rollback follow-through (LOOP-AUTO-EVO-005) are separate paths. AC-1 requires evidence from `POST /api/rollback` triggered by an evolution decision, not from `POST /api/kill-switch/dispatch`.

**G-4: The dependency on LOOP-AUTO-EVO-004 is hard.**
If LOOP-AUTO-EVO-004 has not implemented the actual downstream HTTP dispatch from the evolution service to the runtime-manager, then LOOP-AUTO-EVO-005 cannot produce real AC-1 evidence. The reviewer must confirm LOOP-AUTO-EVO-004 is merged and its downstream HTTP integration is confirmed by tests before approving LOOP-AUTO-EVO-005.

**G-5: Panel-only closure is explicitly non-goal.**
Even if the BFF panel shows green checkmarks for all five stages, that is not sufficient. The evidence must include the raw API response or test output that produced those values.

**G-6: Failure path must be exercised, not just documented.**
AC-3 requires a test that actually triggers a failure and asserts the structured `blocked_reason` field. A code comment saying "on failure, write the reason" is not evidence.

---

## 9. Handoff Recommendation

This packet is ready for Claude2 (sidecar reviewer) to review. After sidecar review:

1. Claude2 approves the sidecar and hands it back for Claude to review the parent task.
2. Gemini2 (parent task owner) should read this packet before beginning evidence collection.
3. Gemini2 should confirm LOOP-AUTO-EVO-004 is merged and its downstream dispatch is operational before starting the evidence replay.
4. Evidence collection commands (suggested):

```bash
# Confirm evolution service + runtime-manager are up
curl -s http://localhost:8010/healthz
curl -s http://localhost:8020/healthz

# Create a hand-crafted approved rollback decision
curl -s -X POST http://localhost:8010/api/evolution/proposals \
  -H 'Content-Type: application/json' \
  -d '{"action_type":"rollback","actor_id":"evidence-run","target_stage":"paper","risk_level":"high"}'

# Advance to reviewed
curl -s -X POST http://localhost:8010/api/evolution/proposals/{DECISION_ID}/review \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"reviewer","actor_role":"risk_owner","review_notes":"evidence run"}'

# Advance to approved
curl -s -X POST http://localhost:8010/api/evolution/proposals/{DECISION_ID}/approve \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"approver","actor_role":"risk_owner"}'

# Trigger dispatch (or run dispatch_worker for one poll)
curl -s -X POST http://localhost:8010/api/evolution/proposals/{DECISION_ID}/execute \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"evolution_controller","actor_role":"evolution_controller","active_binding_id":"binding-paper-001"}'

# Check rollback history on runtime-manager
curl -s "http://localhost:8020/api/rollback/history?pool_id=pool-001"

# Check all five stages visible
curl -s "http://localhost:8010/api/evolution/proposals/{DECISION_ID}"
```

5. Record command output in `docs/deployment/evidence/evo-005-rollback-followthrough-evidence.md`.
6. Run the evolution and runtime-manager test suites and record results:

```bash
python3 -m pytest services/evolution/test_dispatch_worker.py -v
python3 -m pytest services/evolution/test_evolution_service.py -v
python3 -m pytest services/runtime-manager/test_runtime_manager.py -v
```

---

## 10. Packet Integrity Statement

This packet was assembled on 2026-06-27 from the following sources:

- `ai-status.json` (live task state for LOOP-AUTO-EVO-005 and all dependency tasks)
- `.orchestrator/task-briefs/loop_auto_evo_005_sidecar_acceptance.md` (task brief)
- `support/sidecars/LOOP-AUTO-EVO-004/LOOP-AUTO-EVO-004-SIDECAR-REVIEW.md` (upstream sidecar)
- `services/evolution/dispatch_worker.py`, `services/evolution/main.py`, `services/evolution/models.py`
- `services/runtime-manager/main.py`, `services/runtime-manager/service.py`
- `services/evolution/test_dispatch_worker.py`, `services/evolution/test_evolution_service.py`
- `services/runtime-manager/test_runtime_manager.py`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md` (L1 policy references)

No canonical truth files were modified during this sidecar's execution.
Parent task status was advanced from `todo` to `in_progress` via `AI_NAME=Claude ./scripts/ai-status.sh start`.

---

## 11. Sidecar Review — Claude2

**Reviewer:** Claude2
**Review date:** 2026-06-27
**Outcome:** APPROVED

### Review Summary

This acceptance packet is well-constructed and meets the standard for a `acceptance_packet` sidecar. The packet correctly identifies the key structural gap in AC-2 (missing `dispatched` intermediate state), sets appropriate `proven-live` evidence standards, and provides actionable reviewer guardrails. No canonical truth files were modified.

### Per-Section Assessment

| Section | Assessment |
|---|---|
| §1 Purpose | Clear; four deliverables correctly scoped to support-only role. |
| §2 Parent Task Truth | Acceptance criteria and dispatch rules faithfully reproduced from `ai-status.json`. |
| §3 Dependency Map | ASCII dependency tree is correct and complete through EVO-000 → EVO-005. Pre-condition note (EVO-004 must merge before evidence collection) is accurate and critical. The EVO-001/EVO-002/DEP-001 workaround approach (hand-crafted approved decision) is operationally sound. |
| §4 Implementation Surface | Files listed are the correct scope for this evidence task. The §4.3 observation about the missing `dispatched` state is the most important structural finding and is correctly identified. |
| §5 Gap Analysis | AC-1 gap (downstream HTTP call not confirmed) is valid. AC-2 two-option approach is well-structured; Option A is preferable but Option B is acceptable if BFF projection does not fabricate timestamps. AC-3 gap (no `blocked_reason` write-back) is valid and matches implementation review of `dispatch_worker.py`. |
| §6 Evidence Scope | Evidence table correctly distinguishes sufficient vs. not-sufficient for `proven-live`. Command log vs. panel screenshot distinction is enforced appropriately. |
| §7 File Scope | Change/no-change boundary is correct. Flagging `evolution_controller.py` as out-of-scope is important. |
| §8 Reviewer Guardrails | G-1 through G-6 are all actionable. G-3 (kill-switch vs. evolution rollback distinction) is non-obvious and valuable. G-4 (EVO-004 hard dependency) must be enforced. |
| §9 Handoff | Evidence collection commands look correct for local smoke. Test suite commands are appropriate. |
| §10 Integrity | Sources enumerated; no canonical mutation. |

### Observations

**Parent task state drift:** At the time of sidecar review (2026-06-27), the supervisor-root `ai-status.json` shows `LOOP-AUTO-EVO-005` in `review_approved` state with owner reassigned to Claude2 (not Gemini2 as stated in §2). This means the parent task's status in this packet is stale as of review time. The substantive technical content (gap analysis, evidence scope, guardrails) is unaffected by the ownership change and remains valid for whoever executes the parent task.

**AC-2 option selection note for parent task owner:** If the parent task owner chooses Option A (new `dispatched` state), pay careful attention to G-2's race-condition note: if the dispatch_worker crashes between writing `dispatched` and receiving a downstream confirmation, the decision will be stuck in `dispatched` indefinitely. A maximum `dispatched` age timeout or a recovery path is needed. This is not a blocker for EVO-005 if Option B is chosen.

### Approval Condition

No changes to the packet are required. The packet is approved as-is. The parent task owner should consult this packet before beginning evidence collection for LOOP-AUTO-EVO-005.
