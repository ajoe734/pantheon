# Runtime-Manager Write Authority Matrix

Last updated: 2026-04-10
Task: `RUN-001A`
Status: Draft Support Slice

## 1. Purpose

This document defines the write authority boundaries for the `Runtime Manager` (Execution Plane). 
It ensures that only the authorized service can modify the canonical state of runtime bindings and position lineage, 
preventing split-brain scenarios or unauthorized state mutations.

---

## 2. Authority Matrix

| Object | Field(s) | Write Owner | Authority Source / Trigger |
|---|---|---|---|
| **RuntimeBinding** | `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `deployment_mode`, `plan_id`, `metadata` | `Runtime Manager` | `DeploymentPlan` (status: approved/executing) |
| **RuntimeBinding** | `status` (`active`, `retired`, `failed`, `paused`) | `Runtime Manager` | Runtime Lifecycle Events / `DeploymentPlan` |
| **RuntimeBinding** | `effective_at`, `retired_at` | `Runtime Manager` | System Clock (on state transition) |
| **RuntimeBinding** | `rollback_parent`, `rollback_action_type` | `Runtime Manager` | `DeploymentPlan.rollback` |
| **Position Lineage**| `opened_by_artifact_id` | `Runtime Manager` | Active `RuntimeBinding` at time of entry |
| **Position Lineage**| `current_managed_by_binding_id` | `Runtime Manager` | Active `RuntimeBinding` (updated on `replace`) |
| **Runtime Health** | `last_heartbeat`, `heartbeat_status` | `Runtime Manager` | LEAN Runtime Heartbeat |
| **Execution Event** | `artifact_id`, `deployment_stage`, `plan_id` | `Runtime Manager` | `RuntimeBinding` Context |

---

## 3. Governance Constraints

1. **Deny-First for Deployment:** `Runtime Manager` must NOT create or update a `RuntimeBinding` unless it can resolve an active `DeploymentPlan` in `approved` or `executing` status.
2. **Immutable History:** Once a `RuntimeBinding` status is set to `retired` or `failed`, its core fields (`artifact_id`, `plan_id`, etc.) must not be modified.
3. **Lineage Preservation:** When performing a `replace` rollback, the `Runtime Manager` is responsible for updating the `current_managed_by_binding_id` of all active positions in the pool to point to the new binding.
4. **Stage Enforcement:** `Runtime Manager` must verify that the `DeploymentPlan.target_stage` matches the `RuntimeBinding.deployment_mode`.

---

## 4. Conflict Resolution

- In the event of a conflict between a `DeploymentPlan` and local `RuntimeBinding` state (e.g., plan says `promote` but runtime is `failed`), the `Runtime Manager` must transition the plan to `failed` and emit a diagnostic event rather than attempting an unsafe state recovery.
- If multiple `DeploymentPlan` objects target the same `capital_pool_id` simultaneously, the `Runtime Manager` must enforce sequential execution based on `plan_id` or `created_at` to prevent race conditions in `RuntimeBinding` creation.
