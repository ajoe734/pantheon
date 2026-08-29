# Rollback Action Matrix

Last updated: 2026-04-10
Tasks: `RUN-001A`, `EX-002`
Status: Ready for RUN-001 / EX-002 consumption

## 1. Purpose

This document maps high-level rollback intent (from Governance/Evolution) to specific, low-level execution actions handled by the `Runtime Manager`. It defines how existing positions and telemetry are handled during each type of rollback.

---

## 2. Action Matrix

| Rollback Type | Scenario | Runtime Manager Action | Position Treatment | Telemetry Cutover |
|---|---|---|---|---|
| **`replace`** | Minor degradation, configuration fix, or style-compatible artifact. | 1. Resolve approved fallback from `DeploymentPlan.rollback`.<br>2. Create a replacement `RuntimeBinding`.<br>3. Atomically retire the old binding after the new one becomes active. | **Preserve & Inherit**: The new binding takes over the existing book. No forced pause or liquidation. | Events before cutover stay on the old binding/artifact; events at and after timestamp $T$ use the new binding/artifact. |
| **`pause_then_replace`** | Style mismatch or medium risk. Need to stabilize before switching owners. | 1. Transition current binding `active -> pending_pause`.<br>2. Send `Pause` command (no new entries).<br>3. Wait for open orders to fill/cancel and mark binding `paused`.<br>4. Create replacement binding and retire the paused binding atomically. | **Drain & Inherit**: Existing book is stabilized before management is transferred. | Telemetry reflects `pending_pause/paused` on the old binding until replacement is active; post-cutover events use the new binding. |
| **`liquidate_then_replace`** | Severe breach, bug, security incident, or risk threshold violation. | 1. Stop new entries on the old binding.<br>2. Cancel pending orders and send `Liquidate`.<br>3. Verify zero positions and zero pending orders.<br>4. Create replacement binding for the fallback/baseline, optionally in guarded mode. | **Flatten**: All exposure is removed before any new binding takes ownership. The replacement starts from a clean slate. | Liquidation / cancel events remain attributed to the old binding/artifact. Only after the runtime is flat does telemetry cut over to the new binding. |

---

## 3. Position Lineage Rules

During any rollback, the `Runtime Manager` must ensure the following fields are correctly populated in the Position Store:

1. **`opened_by_artifact_id`**: Remains immutable (pointing to the artifact that originally entered the trade).
2. **`current_managed_by_binding_id`**: Updated to the `binding_id` of the *new* `RuntimeBinding` only after cutover.
3. **`liquidate_then_replace` guard**: If positions are not fully flattened, ownership must not transfer to the replacement binding.

---

## 4. Operational Guards

- **Timeout Policy**: If `pause_then_replace` or `liquidate_then_replace` does not reach a stable state (e.g., zero positions or zero orders) within a configured `max_mitigation_window`, the `Runtime Manager` must escalate to a `Severity-1` incident and may trigger a hard Kill-Switch if policy permits.
- **Atomic Swap**: The transition from `retired` status (old binding) to `active` status (new binding) must be atomic to prevent gaps in telemetry or "orphan" positions.
- **Loader Boundary**: Artifact Loader validates fallback metadata and payload integrity before execution. It does not decide rollback action type or mutate bindings.
