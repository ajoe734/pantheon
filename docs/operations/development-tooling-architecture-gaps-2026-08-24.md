# Development-Tooling Architecture Gaps and System Design — 2026-08-24

**Document ID:** `OPS-DEV-TOOLING-ARCH-GAP-20260824`
**Document Tier:** L3 Supporting Design & Operational Architecture Record (under `AI_COLLABORATION_GUIDE.md` § 1 / `CANONICAL_DOCUMENT_MAP.md`)
**Target Domain:** Development Tooling Control Plane (`.orchestrator/`, `scripts/`, `ai-task-archive/`, V2 TaskStore)
**Boundary Classification:** Development Tooling only. Product Runtime (`services/`, BFF, Ingestion) and Capital Pathways remain untouched.
**Conflict Rule:** In case of conflict, canonical L0/L1 documents (`AI_COLLABORATION_GUIDE.md`, `TARGET_ARCHITECTURE.md`, `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`) take precedence. This document provides gap analysis and architectural designs for future development tooling implementation tasks.

---

## 1. Executive Summary & Context

During the functional closure sprint (2026-08-20 to 2026-08-24), execution across the Pantheon backend (`ajoe734/pantheon`) and the frontend (`ajoe734/execute-plans`) exposed recurring architectural friction in the development tooling control plane. While the V2 TaskStore, supervisor, and auto-worker pipeline successfully enforce append-only state transition journals, cryptographic head validation, nonterminal row disappearance checks, and exact-head review gating, several structural gaps led to worker deadlocks, manual intervention cycles, and coordination workarounds:

1. **Task Amendment Authority Gap vs. History Integrity:** The V2 TaskStore validates append-only state deltas, cryptographic head integrity, and nonterminal row disappearance (`task-state nonterminal drop rejected`), but neither the store nor `scripts/ai_status.py` provides a governed command or event model (`task_amended`) to amend task definitions (e.g., acceptance criteria, dependencies, artifact scopes) in flight. When requirements need refinement (e.g., removing unfeasible soak ceremonies or narrowing acceptance scopes), workers had to choose between out-of-band state file edits (which break cryptographic digests or trigger nonterminal drop rejections), premature task supersession, or creating duplicate replacement tasks that break downstream dependency links.
2. **Dependency-Aware Reopen & Evidence Handoff Gaps:** When a reviewer rejected a PR head and reopened a task, the supervisor lacked a native dependency propagation mechanism to signal downstream tasks, while root tasks lacked a formal contract to hand off verified evidence to consumer tasks.
3. **Artifact Write Overlap & Live Dispatch Exclusion:** While `scripts/ai_status.py` provides static catalog/assignment-time `artifact_conflict_guard` and `enforce_artifact_conflict_admission()`, supervisor execution admission (`dispatch_admission.py`) checks quota and endpoint availability but lacks live write-scope mutual exclusion across concurrently leased tasks, resulting in worktree index contention on shared files.
4. **Cross-Repository Routing & Parent/Sidecar DAG Scheduling:** While `multi_repo_registry.py` and `target_repo` resolve repository paths and enforce integration targets (`dev`), the development control plane lacks first-class parent-child task primitives (`parent_task_id`, `task_nature=sidecar`, `subphase`) and automatic subphase dependency gating, forcing auxiliary tasks (caller inventories, contract verifications) into disconnected top-level tasks.
5. **Exact-Head Review Rejection Recovery & Stale State Stranding:** Review rejection paths failed closed when PRs were already merged or closed, and task state transitions frequently left stale `waiting_for` markers, skewing fleet dispatch metrics.

This document records the exact failure modes observed in live operations, isolates their root causes, and specifies an implementation-ready design for each area.

```
+---------------------------------------------------------------------------------------------------+
|                               PANTHEON DEVELOPMENT TOOLING CONTROL PLANE                          |
+------------------------------------+------------------------------------+-------------------------+
|        V2 TASKSTORE & STATE        |        SUPERVISOR DISPATCH         |    WORKER & REVIEW GATE |
+------------------------------------+------------------------------------+-------------------------+
| [GAP 1] Governed Task Amendment    | [GAP 3] Live Artifact Write        | [GAP 5] Exact-Head      |
|         Event Ledger               |         Overlap Gating             |         Rejection Exit  |
|                                    |                                    |                         |
| [GAP 2] DAG Reopen Propagation     | [GAP 4] Native Parent-Sidecar      | [GAP 5] Automated       |
|         & Root Evidence Handoff    |         Subphase DAG Scheduling    |         waiting_for GC  |
+------------------------------------+------------------------------------+-------------------------+
```

---

## 2. Operational Evidence & Failure Inventory

The architectural findings in this document are grounded in concrete operational evidence from the 2026-08-20 to 2026-08-24 sprint:

| Incident / Task Reference | Observed Symptom & Failure Mode | Tooling Root Cause |
|---|---|---|
| **Lifecycle Review Reject & PR #5147** (`LIFECYCLE-PROJ-RETIRE-001`, `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824`) | `LIFECYCLE-PROJ-RETIRE-001` contained unfeasible dev requirements (7-day soak, retirement HMAC CLI). Attempting to edit task fields out-of-band risked hash divergence; creating replacement tasks risked orphaning the original 7-task history. | Lack of a governed append-only task amendment authority and `task_amended` journal event in V2 TaskStore / `ai_status.py`. |
| **FE Caller-Matrix & Candidate Review** (`PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`, `PFG-CANDIDATE-AUTO-BINDING-20260824`) | Caller inventory sidecars had to be created as disconnected top-level tasks; frontend candidate pairing required repetitive manual authorizations due to lack of automatic pair derivation. | Multi-repo routing exists via `multi_repo_registry.py`, but sidecar subphases and parent-child task DAG relationships lacked first-class schema and supervisor dispatch gating. |
| **Management Closeout Worktree Collision** (`PPL-ALLOC-007`, `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824`) | `PPL-ALLOC-007` was blocked because the `execute-plans` worktree contained uncommitted `PPL-ALLOC-006` adapter diffs. Parallel tasks touched shared files without mutual exclusion. | Catalog-level `artifact_conflict_guard` was not evaluated as dynamic write-path mutual exclusion during concurrent supervisor dispatch. |
| **Root Deploy Reopen Livelock** (`OPS-PHASED-ROOT-DEPLOY-CLOSURE-20260824`, `OPS-SUPERVISOR-REOPEN-REDISPATCH-20260824`) | Reviewer rejected PR head across 10+ cycles. Supervisor deduplication (`seen_event_keys`) treated reopened in-progress tasks as unchanged, blocking owner redispatch under `unchanged_cooldown`. | Dispatch identity signature lacked bounded review-reopen revision tracking. |
| **Premature Merge Rejection Defect** (`docs/04/pantheon_twelve_loop_gap_2026-07-26`, PRs #4212, #4213, #4214) | PRs merged before review; when reviewers attempted rejection, `reopen` failed closed because the GitHub PR was no longer open. | GitHub review bridge lacked a pure-lifecycle rejection path for merged/closed heads. |
| **Stale `waiting_for` Stranding** (`PPL-ALLOC-007`, `TJ-E2E-012`) | Tasks reassigned from `Human/Ops` to `Claude` retained `waiting_for: Human/Ops` or `waiting_for: Antigravity` in `ai-status.json` even after moving to `in_progress`. | State transitions in `task_machine.py` and `ai_status.py` did not uniformly purge blocker metadata upon reactivation. |

---

## 3. Gap 1: Governed Task Amendment & Append-Only Event Model

### 3.1 Current Behavior & Baseline Analysis
The V2 TaskStore (`.orchestrator/rewrite/task_state_store.py`) strictly enforces append-only transition journals with cryptographic head validation and validates nonterminal row disappearance (`task-state nonterminal drop rejected`). TaskStore does not reject field corrections on individual metadata keys, but rather validates state delta transitions and prevents arbitrary row removal.

However, the governance layer (`scripts/ai_status.py` and `task_machine.py`) lacks a dedicated **amendment authority**. Task specifications (`title`, `summary_zh`, `acceptance`, `depends_on`, `artifacts`, `phase`) are initialized at genesis and can only be modified through ad-hoc manual state manipulation or task recreation.

When real-world conditions require correcting an existing task (e.g., removing a non-applicable requirement, narrowing an artifact scope, or correcting a dependency):
- Direct in-place modification of `ai-status.json` or head files bypasses audit logging and can corrupt head offsets or trigger nonterminal drop rejections if rows are malformed.
- Marking the task `superseded` terminates the task history prematurely, confusing multi-wave tracking.
- Materializing a duplicate replacement task creates orphaned rows and breaks downstream dependency links that pointed to the original task ID.

### 3.2 Architectural Design: Governed `task_amended` Event Ledger
To correct task parameters without mutating historical records, bypassing audit logging, or violating journal integrity, TaskStore and `ai_status.py` introduce the governed `task_amended` event type.

```
+-------------------------------------------------------------------------------------+
|                              APPEND-ONLY TASK JOURNAL                               |
|                                                                                     |
|  [Seq 1: Genesis] ---> [Seq 2: Dispatch] ---> [Seq 3: Task Amended] ---> [Seq 4: Handoff]
|  Task: PFG-LIFE-01     Status: in_progress    Amends: acceptance          Status: review
|  (Initial Spec)                               (Drop 7-day soak)           (Reviews amended spec)
+-------------------------------------------------------------------------------------+
```

#### Event Structure: `TaskAmendedEvent`
```json
{
  "event_version": 2,
  "event_type": "task_amended",
  "event_id": "amend-pfg-life-01-001",
  "timestamp": "2026-08-24T12:00:00Z",
  "actor": "Human/Ops",
  "task_id": "PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824",
  "amendment_seq": 1,
  "amended_fields": {
    "acceptance": [
      "Activate PostgreSQL projector and BFF relational reader; verify restart and readback; JSON generation deleted immediately after readback proof; 7-day soak and HMAC CLI removed"
    ],
    "artifacts": [
      "services/trade_journey/lifecycle_projector.py",
      "services/trade_journey/projection_store.py",
      "services/control-plane/bff/trade_journey_projection_store.py"
    ]
  },
  "rationale": "Correct dev-only Lifecycle closure scope per SA_IMPLEMENTATION_PLAN_2026-08-24",
  "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

#### Governed Command Interface
```bash
AI_NAME=Human/Ops \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" amend \
  PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824 \
  --acceptance "Activate PostgreSQL projector; remove 7-day soak" \
  --artifacts "services/trade_journey/lifecycle_projector.py,services/trade_journey/projection_store.py" \
  --rationale "Correct dev-only Lifecycle closure scope"
```

#### Replay and Snapshot Logic
1. `TaskStateStore.load_snapshot()` reads the base task definition from the genesis snapshot or head file.
2. If `task_amended` events exist in the journal tail for that `task_id`, the store applies amendments sequentially in ascending `amendment_seq` order.
3. Allowed amendable fields: `title`, `summary_zh`, `acceptance`, `artifacts`, `required_artifacts`, `depends_on`, `metadata`.
4. Forbidden amendable fields: `status`, `owner`, `reviewer`, `generation` (these remain strictly governed by `task_machine.py` lifecycle and assignment transitions).
5. The computed effective task snapshot is served to workers and supervisor without modifying historical genesis entries.

---

## 4. Gap 2: Dependency-Aware Reopen & Root-Evidence Handoff

### 4.1 Current Behavior & Failure Mode
When a reviewer executes `scripts/ai-status.sh reopen <task-id>`, the task moves from `review` back to `in_progress`. Two major issues occur:
1. **Downstream Dependency Stalling / Race Conditions:** If downstream tasks were waiting on this task, or if they had speculatively evaluated dependencies, the supervisor did not automatically propagate the reopen state to invalidate downstream readiness.
2. **Ad-Hoc Root Evidence Handoff:** When corrective root tasks (e.g., `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824`) deliver foundational code required by consumer tasks (e.g., `LIFECYCLE-PROJ-RETIRE-001`), the consumer task had no formal mechanism to verify that the root task's evidence was merged before proceeding, relying instead on manual coordination.

### 4.2 Architectural Design: DAG State Invalidation & Evidence Handoff Manifests

```
       [Corrective Root Task]
                 |
        (Review & Merged to dev)
                 |
      [Evidence Handoff Event] <-----------------+
                 |                               |
                 v                               |
       [Consumer Task Blocked] --- (Validates SHA & Artifacts) ---> [Consumer Task Unblocked]
```

#### 1. Bounded Reopen Propagation in DAG Evaluator
- When task $T$ transitions to `in_progress` via `reopen`:
  - `task_machine.py` updates $T$'s status.
  - The supervisor's dependency evaluator recomputes `dependencies_satisfied` for all tasks in $\text{Downstream}(T)$.
  - Any downstream task $D$ whose dependency requirements include $T$ is immediately marked `dependencies_satisfied = False`, preventing premature dispatch of $D$ while $T$ is under rework.
  - The supervisor incorporates `review_reopen_revision` into the dispatch signature (as established in `OPS-SUPERVISOR-REOPEN-REDISPATCH-20260824`) to admit exactly one dispatch to the owner without an infinite polling loop.

#### 2. Canonical Root-Evidence Handoff Contract
Consumer tasks specify required upstream evidence via a structured handoff binding in task metadata:

```json
{
  "root_evidence_requirement": {
    "root_task_id": "PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824",
    "required_merge_branch": "dev",
    "required_artifacts": [
      "services/trade_journey/lifecycle_projector.py",
      "services/trade_journey/projection_store.py"
    ],
    "minimum_commit_reachability": "git merge-base --is-ancestor <root_merge_sha> HEAD"
  }
}
```

The consumer task's start gate (`task_start.sh` or supervisor admission) runs a read-only check to confirm the root task is `done`, its delivery commit is reachable on the task branch, and the required artifacts are present before admitting the consumer task to `in_progress`.

---

## 5. Gap 3: Artifact Ownership & Overlap Admission

### 5.1 Existing Capabilities & Remaining Operational Delta
Currently, `scripts/ai_status.py` implements static catalog/assignment admission checks through `artifact_conflict_guard` and `enforce_artifact_conflict_admission()`. This guards against assigning overlapping tasks when an explicit allowlist or static catalog exists.

However, a critical operational delta remains at **supervisor execution dispatch time**:
- In `dispatch_admission.py` / `supervisor.py`, `evaluate_dispatch_intent` evaluates global capacity, per-lane concurrency, and endpoint health, but does not check live write-scope collisions across currently leased auto-worker worktrees.
- When multiple tasks without pre-declared `artifact_conflict_guard` allowlists run concurrently (or touch shared configuration/build files like `docker-compose.yml`, `scripts/deploy_nonprod_vm.sh`, or `execute-plans/src/App.tsx`), parallel workers experience git index conflicts and uncommitted worktree pollution (as observed in `PPL-ALLOC-007`).

### 5.2 Architectural Design: Live Path-Based Mutual Exclusion Gating

```
Active Task A Lease:    owned_write_paths = ["services/trade_journey/*"]
Candidate Task B Intent: owned_write_paths = ["services/trade_journey/projection_store.py"]

       Supervisor Dispatch Admission (dispatch_admission.py)
                    |
      [Check Live Artifact Overlap]
                    |
      Overlap Detected in services/trade_journey/!
                    |
                    v
    Decision: DispatchBlockReason.ARTIFACT_WRITE_OVERLAP (Blocked on Task A)
```

#### Task Schema Extension: Explicit Artifact Declarations
Extending the existing `artifacts` and `artifact_conflict_guard` models:
```json
{
  "artifacts_manifest": {
    "owned_write_paths": [
      "services/trade_journey/lifecycle_projector.py",
      "services/trade_journey/projection_store.py"
    ],
    "referenced_read_paths": [
      "services/telemetry/models.py",
      "services/control-plane/bff/main.py"
    ],
    "shared_composed_paths": []
  }
}
```

#### Admission Gate Algorithm in `dispatch_admission.py`
1. When evaluating a `TaskIntent` for candidate task $C$:
   - Collect $W(C) = C.\text{owned\_write\_paths}$ (defaulting to normalized `artifacts` if `artifacts_manifest` is absent).
   - Collect active write sets from all currently leased tasks: $W_{\text{active}} = \bigcup_{L \in \text{leased}} L.\text{owned\_write\_paths}$.
2. For each path $p_c \in W(C)$ and $p_a \in W_{\text{active}}$:
   - Check if $p_c$ is identical to $p_a$, or if one is a parent directory of the other.
   - If an overlap is detected and neither task has an explicit `artifact_conflict_guard` allowlist authorizing the overlap:
     - Return `DispatchDecision(eligible=False, reason=DispatchBlockReason.ARTIFACT_WRITE_OVERLAP, conflicting_task_id=L.task_id)`.
3. Paths listed under `referenced_read_paths` are shared read-only and never trigger admission blocking.
4. Paths listed under `shared_composed_paths` (e.g. `docker-compose.yml`) require an explicit single-owner integration task or a wave barrier.

---

## 6. Gap 4: Cross-Repository Routing & Parent/Sidecar Subphase Dispatch

### 6.1 Existing Capabilities & Remaining Operational Delta
Multi-repository routing is already established in `.orchestrator/multi_repo_registry.py`, which provides prefix-based path resolution, repository aliases, and target branch enforcement (`dev` for `pantheon` and `execute_plans`).

However, the development control plane lacks native primitives for **parent-child task relationships and subphase coordination**:
- Sidecar tasks (such as caller inventories, contract verification audits, or test fixtures) must currently be created as disconnected top-level tasks.
- A parent implementation task has no formal mechanism to declare that it is waiting for a child sidecar to finish, requiring manual status updates or ad-hoc blockers.

### 6.2 Architectural Design: Typed Parent/Sidecar Models & Subphase Gating

```
                   +-----------------------------------------------+
                    |      Parent Task: PFG-BE-CONSOLIDATE-20260820 |
                    |      Repo: ajoe734/pantheon                   |
                    |      Status: blocked_on_subphase              |
                    +-----------------------+-----------------------+
                                            |
                    +----------------------+----------------------+
                    |                                             |
                    v                                             v
+---------------------------------------+     +---------------------------------------+
| Sidecar 1: CALLER-INVENTORY           |     | Sidecar 2: CONTRACT-VERIFY            |
| Repo: ajoe734/pantheon                |     | Repo: ajoe734/pantheon                |
| Nature: sidecar (read-only audit)     |     | Nature: sidecar (test audit)          |
| Status: done                          |     | Status: done                          |
+---------------------------------------+     +---------------------------------------+
```

#### Typed Task Specification
```json
{
  "id": "PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY",
  "target_repo": "pantheon",
  "task_nature": "sidecar",
  "parent_task_id": "PFG-BE-CONSOLIDATE-20260820",
  "subphase": "caller_inventory",
  "delivery_contract": {
    "repository_id": "pantheon",
    "base_branch": "dev",
    "task_branch": "task/PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY",
    "output_artifact": "support/sidecars/PFG-BE-CONSOLIDATE-20260820/caller-inventory-20260824.md"
  }
}
```

#### Dispatch and Lifecycle Integration
1. Leverage `multi_repo_registry.py` for all repo path and branch resolution.
2. Parent tasks with declared child sidecars automatically enter `blocked_on_subphase` state in `task_machine.py` until all child sidecars reach `done` or `review_approved`.
3. Auto-workers dispatched to sidecar tasks inherit a lightweight read-only or narrow-audit worktree lease, ensuring zero interference with the parent task branch.

---

## 7. Gap 5: Exact-Head Review Rejection Recovery & `waiting_for` Cleanup

### 7.1 Current Behavior & Failure Mode
1. **Merged PR Rejection Livelock:** If a task PR merged into `dev` prematurely (e.g., due to branch protection bypass or CI race), the reviewer attempting to reject the work via `scripts/ai-status.sh reopen` failed closed with `GitHub PR #<N> is not open`.
2. **Stale `waiting_for` Metadata Pollution:** When a task was blocked on an agent (e.g. `Human/Ops`), the `waiting_for` field was stamped on the task row in `ai-status.json`. When the task was subsequently unblocked, reassigned, or restarted, `waiting_for` was not consistently deleted, leaving misleading indicators on the task board.

### 7.2 Architectural Design: Pure-Lifecycle Rejection & Transition Cleanup

```
+-----------------------------------------------------------------------------------+
|                        TASK STATE TRANSITION CLEANUP GATE                         |
|                                                                                   |
|  [State: BLOCKED (waiting_for: Human/Ops)]                                        |
|                     |                                                             |
|                     | Action: START / PROGRESS / REOPEN / ASSIGN                  |
|                     v                                                             |
|  [task_machine.py: transition()]                                                  |
|  1. Apply new lifecycle state (e.g. IN_PROGRESS)                                  |
|  2. Atomic Cleanup: task.pop("waiting_for", None)                                 |
|  3. Activity Audit: resolve matching open blocker records                         |
|                     |                                                             |
|                     v                                                             |
|  [State: IN_PROGRESS (waiting_for: null)]                                          |
+-----------------------------------------------------------------------------------+
```

#### 1. Merged-Head Rejection via `supersede` with Audit
When a reviewer evaluates a head that is already merged, instead of failing in `github_review_bridge.py`, the reviewer uses the canonical `supersede` path from `review`:

```bash
AI_NAME=Codex2 \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" supersede \
  <task-id> "Independent review verdict: REJECT. Defect: <details>" \
  <replacement-corrective-task-id>
```

- `task_machine.py` permits `(TaskState.REVIEW, TaskAction.SUPERSEDE) -> TaskState.DONE`.
- Records the rejection evidence in `ai-activity-log.jsonl` and archives the task.
- Unblocks the supervisor fleet from attempting to re-review a merged PR head.

#### 2. Deterministic `waiting_for` Garbage Collection
`task_machine.py` and `ai_status.py` enforce an invariant: **`waiting_for` is valid ONLY when `status == "blocked"`**.

In `ai_status.py`:
```python
def sanitize_task_blocker_state(task: dict[str, Any]) -> None:
    # Ensure waiting_for exists only when task is blocked.
    if task.get("status") != "blocked":
        task.pop("waiting_for", None)
        task.pop("block_reason", None)
```
This sanitizer runs automatically during every state transition (`start`, `progress`, `handoff`, `approve`, `reopen`, `assign`, `done`, `supersede`).

---

## 8. Verification & Validation Framework

### 8.1 Current Task Verification
For this architecture record task (`PFG-DEV-TOOLING-ARCHITECTURE-GAP-20260824`), verification validates document cleanliness, lack of trailing whitespace, boundary compliance, and regression safety across existing supervisor tests:

```bash
# 1. Exact PR branch diff cleanliness check (must be clean with exit code 0)
git diff --check origin/dev...HEAD

# 2. Existing supervisor dispatch unit test verification
PYTHONPATH=.orchestrator:. python3 -m unittest discover -s .orchestrator -p test_explain_dispatch.py
```

### 8.2 Planned Tooling Acceptance Test Matrix (To be implemented in downstream tooling implementation tasks)
The test matrix below defines the planned acceptance suites to be constructed when implementing the corresponding tooling features in future engineering tasks:

```
+---------------------------------------------------------------------------------------+
|                       FUTURE TOOLING ACCEPTANCE TEST MATRIX                           |
+----------------------+----------------------------------------------------------------+
| Test Suite (Planned) | Verification Scope & Assertions                                |
+----------------------+----------------------------------------------------------------+
| test_task_state_     | - Test TaskAmendedEvent replay and effective snapshot view     |
| store_amendments.py  | - Verify immutable base definition preservation                |
|                      | - Reject unauthorized amendment of status/owner/generation     |
+----------------------+----------------------------------------------------------------+
| test_dispatch_       | - Reject candidate task when write path overlaps leased task   |
| overlap_admission.py | - Admit parallel tasks when write paths are disjoint           |
|                      | - Allow concurrent shared read paths                           |
+----------------------+----------------------------------------------------------------+
| test_reopen_dag_     | - Downstream dependencies set to False on upstream reopen      |
| propagation.py       | - Single owner redispatch verified under review_reopen_rev     |
|                      | - Verify RootEvidenceHandoff validation against dev merge SHA  |
+----------------------+----------------------------------------------------------------+
| test_task_machine_   | - Verify waiting_for is purged on all non-blocked transitions  |
| state_sanitization.py| - Verify review rejection supersede exit on closed/merged PRs  |
|                      | - Multi-repo target resolution (pantheon vs execute-plans)     |
+----------------------+----------------------------------------------------------------+
```

---

## 9. Migration & Rollout Strategy

1. **Phase 1: TaskStore & Lifecycle Invariants (Non-Breaking)**
   - Add `TaskAmendedEvent` type definition and replay parsing in `task_state_store.py`.
   - Add `sanitize_task_blocker_state` in `task_machine.py` and `ai_status.py`.
   - Existing event logs and snapshots remain 100% backward-compatible.

2. **Phase 2: Dispatch Admission Gating (Additive Guard)**
   - Introduce live `artifacts_manifest` write-overlap checking in `dispatch_admission.py`.
   - Tasks lacking explicit `owned_write_paths` fallback to whole-directory matching without blocking existing flows.

3. **Phase 3: Multi-Repo Sidecar DAG Activation**
   - Activate typed `parent_task_id`, `task_nature`, and `subphase` resolution in supervisor planning.
   - Migrate legacy sidecar briefs to structured parent-child task records.

---

## 10. Document Authority & Boundaries

- **Authority:** This document is an L3 operational architecture record under `AI_COLLABORATION_GUIDE.md` § 1 and `CANONICAL_DOCUMENT_MAP.md`.
- **Product Safety Guarantee:** This document specifies changes strictly within the Development Tooling Control Plane (`.orchestrator/`, `scripts/`, `ai-task-archive/`). It does not modify product business services, BFF routes, data source ingestion engines, or execution/trading broker runtimes.
- **Reference Contracts:**
  - `docs/02-architecture/development-tooling-product-boundary.md`
  - `docs/04/pantheon_current_code_gap_sa_2026-08-22/SA_IMPLEMENTATION_PLAN_2026-08-24.md`
  - `.orchestrator/skills/worker-anchor-commit.md`
  - `.orchestrator/skills/task-closeout-finalization.md`
