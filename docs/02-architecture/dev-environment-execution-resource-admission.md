# Dev Environment Execution Resource Admission Architecture

## 1. Overview and Purpose

Pantheon development workflows involve two distinct execution profiles:
1. **Isolated Worktree Tasks**: Pure source code changes, unit testing, documentation, and local refactoring that run concurrently across worker worktrees without contending for external shared runtime infrastructure.
2. **Hosted / Release Tasks**: Tasks that interact with the live shared development environment (`pantheon-dev`), such as BFF/FE deployments, hosted smoke tests, and environment migrations.

Because the `pantheon-dev` target environment is a shared singleton host (`pantheon-lupin-dev` VM), concurrent deployment or verification operations by multiple background workers can collide, invalidate leases, or corrupt in-flight deployments.

This architecture introduces **pre-dispatch execution resource admission** in the Pantheon supervisor. It enforces capacity-1 scheduling for tasks claiming the `pantheon-dev` resource *before* any worker is spawned, while allowing unrelated functional worktree tasks to continue executing in parallel up to global and per-lane capacity limits.

---

## 2. Core Design Principles

### 2.1 Allowlisted Execution Resource Declarations

Task specifications may explicitly declare zero or more allowlisted `execution_resources`:
```yaml
execution_resources:
  - pantheon-dev
```
- Current allowlist: `{"pantheon-dev"}`.
- Tasks without execution resources (`execution_resources: []` or omitted) are pure worktree-only tasks and are never constrained by execution resource gates.
- Tasks specifying unknown or unallowlisted resources are rejected at assignment and materialization time.

### 2.2 Pre-Dispatch Capacity Reservation (Capacity 1)

The supervisor tracks execution resource utilization across:
- **Active Workers**: Currently running or starting workers whose assigned task declares `execution_resources`.
- **Queued Events**: Durable delivery events already scheduled in the queue that have not yet terminated or failed.

When a task requires `pantheon-dev`, the supervisor evaluates whether:
$$\text{active}(\text{pantheon-dev}) + \text{queued}(\text{pantheon-dev}) \ge \text{limit}(\text{pantheon-dev})$$
Where the default limit is $1$.

If the resource capacity is reached:
- The task is blocked from dispatch with reason `resource_capacity_reached`.
- No worker is spawned, preventing wasted compute, auth token consumption, or worktree thrashing.
- Unrelated functional tasks without execution resource requirements continue to be dispatched to free worker lanes.

### 2.3 Identical Admission Predicate Across Plan and Delivery

To prevent race conditions and scheduler drift, the exact same pure admission predicate (`evaluate_task_delivery_admission`) is executed:
1. In the **planning phase** (`build_dispatch_plan` / `dispatch_ready_tasks`).
2. In the **queue reservation phase** (`reserve_dispatch_plan`).
3. In the **late delivery revalidation phase** (`evaluate_queued_delivery_admission` in `process_queue`).

### 2.4 Sole Deploy Lease Preservation

`scripts/dev_environment_lease.py` remains the authoritative runtime/deploy lease on the host. Pre-dispatch execution resource admission acts as an orchestrator-level concurrency gate; it **does not introduce a secondary filesystem lock**, maintaining clear domain boundaries.

### 2.5 Pre-Dispatch External Wait Handling

If a hosted execution requires human operator authentication, session tokens, or external credentials that are absent, the task is kept in pre-dispatch `external_wait` or `Human/Ops` hold (`waiting_for`), consuming zero worker slots and zero resource claims until released.

### 2.6 Deterministic Oldest-Wins Queue Recovery

When multiple pending delivery events targeting `pantheon-dev` exist in the supervisor queue (e.g. across restart, recovery, or prior crash), queue revalidation evaluates capacity against strictly older pending events (`created_at`, `event_id`). Exactly one oldest pending event is eligible to launch while newer events remain blocked until the held resource is released.

---

## 3. Migration Guidance: PFG and SRCM Hosted Tasks

When authoring or migrating tasks within Portfolio Foundry Governance (PFG) and Source Repository Code Management (SRCM):

### 3.1 Task Classification Criteria

| Task Type | Execution Resources | Concurrency Posture | Example Tasks |
|:---|:---|:---|:---|
| **Hosted Deploy / Verification** | `["pantheon-dev"]` | Serial (Capacity 1) | BFF deployment, hosted smoke tests, hosted frontend bundle deployment, dev database migration |
| **Functional / Worktree** | `[]` (None) | Parallel (Max lanes) | Domain logic, algorithm implementation, unit tests, linting, doc generation |

### 3.2 Authoring Tasks in Task Packets

When generating development task packets via the assistant dev bridge (`.orchestrator/development_bridge/`):
- For tasks touching hosted services or requiring VM deployment, include:
  ```json
  "execution_resources": ["pantheon-dev"]
  ```
- For functional and sidecar tasks, omit `execution_resources` or specify `[]`.

### 3.3 Manual Task Assignment via CLI

When assigning tasks via `scripts/human-ops-status.sh`:
- Use `TASK_EXECUTION_RESOURCES="pantheon-dev"` or `TASK_EXECUTION_RESOURCES_JSON='["pantheon-dev"]'`:
  ```bash
  TASK_EXECUTION_RESOURCES="pantheon-dev" \
    ./scripts/human-ops-status.sh assign TASK-HOSTED-001 Codex Claude "Deploy BFF to dev"
  ```

### 3.4 Revising Execution Resources via CLI

When adding or removing execution resources on existing pre-dispatch non-active tasks (`todo`, `blocked`):
```bash
./scripts/human-ops-status.sh execution-resource TASK-001 add pantheon-dev "Admit hosted deployment resource"
```
- Requires `Human/Ops` authority (enforced automatically by `scripts/human-ops-status.sh`).
- Operates on pre-dispatch non-active tasks (`todo`, `blocked`); rejects active lifecycle states (`in_progress`, `review`, `review_approved`) and terminal states (`done`, `superseded`).
- Validates allowlisted resources (`{"pantheon-dev"}`).
- Records an audited `execution_resource_revised` event in `ai-activity-log.jsonl` and updates `contract_revision` on the canonical task row.

### 3.5 Product & Capital Safety

Resource admission affects only development tooling dispatch concurrency. It does not modify product runtime behavior, trading APIs, or capital management boundaries.
