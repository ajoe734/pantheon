# APP-002 Frontend View-State Matrix

**Parent Task**: APP-002 — Define operator-facing deployment, incident, and evolution surfaces  
**Created by**: Copilot  
**Date**: 2026-04-10  
**Status**: Design artifact (APP-002 support)  

> This is a support artifact derived from DEGRADED_OPERATOR_PATH.md and BFF_API_CONTRACT.md. It defines how each operator screen should display data, enable/disable buttons, and reconcile with real-time updates based on staleness and degradation state.

---

## 1. Purpose

This document specifies **what operators see on each screen** depending on data freshness, and **which buttons are enabled/disabled/redirected** at each state.

It addresses APP-002 gaps **G6: Degraded-mode action gating** and **G7: Read-after-write reconciliation**.

---

## 2. Data State Classification

From DEGRADED_OPERATOR_PATH.md §3.2, all read surfaces report one of five states:

| State | Definition | Served From | Staleness Indicator | Operator Guidance |
|-------|-----------|-------------|-------------------|------------------|
| **fresh** | Primary service responded within SLA | Source of truth (Persona, Runtime, etc.) | `null` | ✅ Trust this data; actions can proceed normally |
| **degraded** | Read-replica or cache with minor latency | Replica DB or cache (< 5 min old) | `{served_from: "replica", last_known_at: "..."}` | ⚠️ Data is reliable but not realtime; confirm before critical actions |
| **stale** | Cache-backed or reconstructed from refs | Cache (5–30 min old) or partial rebuild | `{served_from: "cache", last_known_at: "...", max_age_minutes: 15}` | ⚠️ Data is last-known; re-verify via CLI or wait for fresh data before irreversible actions |
| **partial** | Composite view missing one or more sub-surfaces | Mix of fresh, degraded, and unavailable | `{surfaces: {sub1: "ok", sub2: "degraded", sub3: "unavailable"}}` | ⚠️ Some information incomplete; review what's visible; some actions may be blocked |
| **unavailable** | No verifiable data at all | N/A | `{served_from: "none", last_check_at: "..."}` | ❌ No verifiable state; cannot take action through BFF; use secondary control path |

---

## 3. Screen-Specific View-State Matrix

### 3.1 Deployment Review Console

**Read dependencies**: `GET /api/v1/operator/deployment-review/{plan_id}` (composed view)

**Composed view includes**:
- Deployment plan details (DP-02)
- Capital pool state (CP-02, CP-04)
- Planned bindings (RT-02, RT-04)
- Approval decisions (DP-03)
- Rollback history

**Screen layout**:
```
┌─ Header: Deployment {plan_id}  [State: approved|pending_approval|rejected]
├─ Card 1: Deployment Details
│   ├─ Artifact ID: {id}
│   ├─ Created by: {operator}
│   ├─ Deployment stage: paper|canary|live
│   └─ Staleness indicator: {state}
│
├─ Card 2: Capital Pool Verification
│   ├─ Pool: {pool_id}
│   ├─ Available capital: {amount}
│   ├─ Allocation for this deployment: {amount}
│   ├─ Risk tier: {tier}
│   └─ Staleness indicator: {state}
│
├─ Card 3: Runtime Binding Preview
│   ├─ Planned runtimes: {count}
│   ├─ Personas: [{list}]
│   ├─ Binding readiness: {status}
│   └─ Staleness indicator: {state}
│
├─ Card 4: Approval Action Drawer
│   ├─ [Approve] [Reject] buttons
│   ├─ Verification notes (text input)
│   └─ Audit info: {operator}, {timestamp}
│
└─ Footer: Last refresh: {time} | SSE: connected|disconnected
```

#### 3.1.1 State Transitions & Button Gating

| Data State | Deployment Status | [Approve] Button | [Reject] Button | Recommendation | SSE Behavior |
|------------|------------------|------------------|------------------|------------------|---|
| **fresh** | pending_approval | ✅ ENABLED | ✅ ENABLED | "Safe to approve with current data" | Subscribe to `/subscription/deployment-review/{plan_id}` for live updates |
| **degraded** | pending_approval | ✅ ENABLED + ⚠️ Warning | ✅ ENABLED | "Data is reliable (replica, <5 min old). Review recommended before approving." | Show SSE overlay: "Waiting for fresh data..." |
| **stale** | pending_approval | ⚠️ YELLOW BUTTON + Confirm dialog | ✅ ENABLED | "Last verified 15 min ago. Re-verify via CLI before approving." | Show: "Data may be outdated. Waiting for refresh..." |
| **partial** | pending_approval | ✅ ENABLED but show missing sub-surfaces | ✅ ENABLED | "Pool info degraded. Runtime binding ok. You can approve based on visible data, but be aware pool state is 5 min old." | Show sub-surface status in card headers |
| **unavailable** | pending_approval | ❌ DISABLED + "Use CLI" link | ✅ ENABLED | "Cannot verify current state. Use admin CLI: `pantheon-admin deployment approve ...`" | Show: "Data unavailable. Switch to secondary control path." |

#### 3.1.2 SSE Reconciliation

**When operator clicks [Approve]**:

1. Submit command via `POST /api/v1/operator/commands` → get receipt ID
2. Show UI: "Approval submitted. Waiting for confirmation..."
3. Subscribe to SSE: `/subscription/operator/commands/{receipt_id}`
4. SSE events:
   - `command_status: processing` → Show spinner
   - `command_status: executed` → Update deployment state card to show approval
   - `approval_decision_updated: {id}` → Refresh approval history card
5. If SSE shows stale data during wait, show banner: "Real-time updates delayed; checking for fresh data..."

---

### 3.2 Incident Response Console

**Read dependencies**: `GET /api/v1/operator/incident-response/{incident_id}` (composed view)

**Composed view includes**:
- Incident case details (IN-02)
- Active runtimes affected (RT-03)
- Kill-switch status (RT-04, EV-04)
- Telemetry snapshot (TL-02)
- Rollback history (if any)

**Screen layout**:
```
┌─ Header: Incident {incident_id}  [Severity: critical|high|medium]
├─ Card 1: Incident Status
│   ├─ Reported at: {timestamp}
│   ├─ Current status: active|investigating|mitigated|resolved
│   ├─ Affected personas: {list}
│   ├─ Affected runtimes: {count}
│   └─ Staleness indicator: {state}
│
├─ Card 2: Runtime Status Dashboard
│   ├─ Healthy: {count}
│   ├─ Paused: {count}
│   ├─ Halted: {count}
│   └─ Staleness indicator: {state}
│
├─ Card 3: Kill-Switch Status
│   ├─ Current state: armed|active|deactivated
│   ├─ Scope: all|persona:{id}|pool:{id}
│   ├─ Activated by: {operator}
│   └─ Staleness indicator: {state}
│
├─ Card 4: Control Rail (Action Buttons)
│   ├─ [Pause Runtime] [Resume Runtime]
│   ├─ [Execute Rollback]
│   ├─ [Activate Kill-Switch]
│   └─ [Secondary Control Path] (fallback)
│
└─ Footer: Last refresh: {time} | SSE: connected|disconnected
```

#### 3.2.1 State Transitions & Button Gating

| Data State | Incident Status | Pause Runtime | Rollback | Kill-Switch | Secondary Path |
|------------|-----------------|---------------|----------|-------------|-----------------|
| **fresh** | active | ✅ ENABLED | ✅ ENABLED | ✅ ENABLED | 📄 Link visible (not primary) |
| **degraded** | active | ✅ ENABLED + ⚠️ "Stale runtime list" | ✅ ENABLED | ✅ ENABLED | 📄 Link visible |
| **stale** | active | ⚠️ YELLOW + Confirm | ⚠️ YELLOW + Confirm | ✅ ENABLED (show MFA required) | 📄 Link prominent |
| **partial** | active | ✅ ENABLED, show which sub-surfaces degraded | ⚠️ YELLOW if rollback targets missing data | ✅ ENABLED | 📄 Link highlighted |
| **unavailable** | active | ❌ DISABLED, show CLI command | ❌ DISABLED, show CLI command | ⚠️ YELLOW, show "Use CLI with MFA" | 📄 HIGHLIGHTED, recommended |

**Special case: Never show "no incidents" when data is unavailable**

```
❌ WRONG:
┌─ Incidents: 0 (DATA UNAVAILABLE)
  [No incidents detected]

✅ CORRECT:
┌─ Incidents: Unknown (DATA UNAVAILABLE)
  ⚠️ Unable to verify incident status. Possible scenarios:
     • System is healthy (no incidents)
     • Or: monitoring is degraded and incidents exist but are unverified
  
  Use CLI to verify: pantheon-admin incident list
  Or refresh when BFF is available
```

#### 3.2.2 SSE Reconciliation

**Real-time incident updates**:

1. Subscribe to: `/subscription/operator/incident/{incident_id}`
2. Show SSE events as they arrive:
   - `incident_status: investigating` → Update status card
   - `runtime_binding_paused: {binding_id}` → Update runtime list (remove from active, add to paused)
   - `kill_switch_activated: {scope}` → Update kill-switch card, disable all other runtime buttons
   - `rollback_progress: {step}` → Show progress bar if rollback is in progress
3. If no SSE update for 30 seconds on a "processing" command, show warning: "Real-time updates delayed; data may be stale"

---

### 3.3 Post-Incident / Evolution Console

**Read dependencies**: `GET /api/v1/operator/post-incident-review/{incident_id}`

**Composed view includes**:
- Postmortem report (IN-04)
- Evolution decisions (EV-01, EV-02)
- Freeze and rollback records (EV-03, EV-04)
- Lineage of affected artifacts (LN-01)

**Screen layout**:
```
┌─ Header: Post-Incident Review {incident_id}
├─ Card 1: Incident Summary
│   ├─ Timeline: {start} to {end}
│   ├─ Root cause: {description}
│   ├─ Personas affected: {list}
│   └─ Staleness indicator: {state}
│
├─ Card 2: Evolution Decisions Proposed
│   ├─ [Decision 1: Freeze persona X]
│   │   ├─ Status: proposed|reviewed|approved|executed
│   │   ├─ Risk level: low|medium|high
│   │   ├─ [Review] [Approve/Reject] [Execute] buttons
│   │   └─ Staleness indicator: {state}
│   ├─ [Decision 2: Retrain strategy Y]
│   │   └─ ...
│   └─ ...
│
├─ Card 3: Freeze & Rollback Records
│   ├─ Freeze orders: [{order_id, target, activated_at}]
│   ├─ Rollback records: [{rollback_id, from_version, to_version, executed_at}]
│   └─ Staleness indicator: {state}
│
├─ Card 4: Audit Trail
│   ├─ All decisions logged with operator, timestamp, rationale
│   └─ Immutable record
│
└─ Footer: Last refresh: {time} | SSE: connected|disconnected
```

#### 3.3.1 State Transitions & Button Gating

| Data State | Decision Status | [Review] | [Approve/Reject] | [Execute] | EVO-004 Impact |
|------------|-----------------|----------|------------------|-----------|--------|
| **fresh** | proposed | ✅ ENABLED | ✅ ENABLED | ⚠️ DISABLED ("Requires approval first") | Separate review/execute until EVO-004 |
| **degraded** | proposed | ✅ ENABLED | ✅ ENABLED | ⚠️ DISABLED | Separate screens, awaiting EVO-004 spec |
| **stale** | proposed | ✅ ENABLED | ⚠️ YELLOW + Confirm | ❌ DISABLED | Recommend re-verify before action |
| **partial** | proposed | ✅ ENABLED | ✅ ENABLED | ⚠️ DISABLED | Show which sub-surfaces are missing |
| **unavailable** | proposed | ❌ DISABLED | ❌ DISABLED | ❌ DISABLED | Route to CLI: `pantheon-admin evolution ...` |

**Special: EVO-004 Pending Note**

Until EVO-004 lands and defines the exact execution boundary:

```
📋 EVOLUTION DECISION REVIEW FLOW (EVO-004 PENDING)

Current design (APP-002):
  Step 1: Review decision (view artifacts, understand drift)
  Step 2: Approve/Reject decision (governance sign-off)
  [LONG PAUSE] — EVO-004 will define when step 3 happens
  Step 3: Execute action (freeze, retrain, mutate)

Why separate?
  EVO-004 must specify:
    • Who can approve vs who can execute
    • Whether they're the same operator or different roles
    • Whether there's a waiting period
    • How to handle concurrent incidents

UI implication:
  [Execute] button is DISABLED until EVO-004 is resolved.
  Workaround: Use CLI: pantheon-admin evolution execute <decision_id>
  Or wait for UI update after EVO-004 lands.
```

#### 3.3.2 SSE Reconciliation for Evolution Decisions

1. Subscribe to: `/subscription/operator/evolution/{decision_id}`
2. SSE events:
   - `evolution_decision_reviewed: {by_operator}` → Update status to "reviewed"
   - `evolution_decision_approved: {by_operator}` → Update status to "approved"
   - `evolution_decision_executed: {action_type}` → Update status to "executed", show result
3. Show "waiting for approval/execution" overlay with SSE timestamp of last update

---

## 4. Global Degradation Banner

### 4.1 Header Banner Logic

Every screen shows a banner at the top that summarizes overall system health:

```
Fresh data:
  (no banner, normal operation)

Degraded:
  ⚠️  SYSTEM STATUS: SOME SERVICES DEGRADED
  Real-time data is delayed by ~5 minutes.
  [View status page] [Refresh now]

Stale:
  ⚠️  SYSTEM STATUS: LIMITED MONITORING
  Data last verified 15 minutes ago.
  Do not rely on this for critical decisions.
  [Use admin CLI] [Refresh]

Partial (mixed):
  ⚠️  SYSTEM STATUS: PARTIAL DATA
  Capital pool: OK | Runtime binding: DELAYED (5 min) | Kill-switch: UNAVAILABLE
  [View details]

Unavailable (BFF down):
  ❌ SYSTEM STATUS: CONTROL PLANE UI DOWN
  The BFF is offline. You can still manage operations via:
    • Admin CLI (SSH): pantheon-admin ...
    • Internal API (curl): https://control-plane-internal/api/internal/v1/...
  [View secondary control path guide]
```

### 4.2 Decision Tree for Banner Display

```python
if all_surfaces == fresh:
    show_nothing()
elif all_surfaces == unavailable:
    show_banner("BFF_DOWN")
elif any_surface == unavailable:
    show_banner("PARTIAL_DATA", surfaces=mixed_status)
elif all_surfaces == stale or degraded:
    show_banner("DEGRADED_OR_STALE")
elif mixed([stale, degraded]):
    show_banner("MIXED_QUALITY")
```

---

## 5. Read-After-Write Reconciliation Logic

### 5.1 Command Submission Flow (Client-Side)

```javascript
async function submitApproval() {
  // Step 1: Submit command
  const receipt = await POST /api/v1/operator/commands {
    command: "ApproveDeployment",
    target: { type: "DeploymentPlan", id: plan_id },
    action: "approve"
  }
  
  // Step 2: Show UI feedback
  showSpinner("Approval submitted...")
  
  // Step 3: Poll or subscribe to command status
  let status = "submitted"
  while (status !== "executed" && status !== "failed") {
    // Option A: Poll (simple but slower)
    const result = await GET /api/v1/operator/commands/{receipt.command_id}
    status = result.status
    
    // Option B: SSE subscription (recommended, real-time)
    // (see SSE setup below)
    
    if (status === "executed") {
      // Step 4: Read updated state from main surface
      const updated = await GET /api/v1/operator/deployment-review/{plan_id}
      // Re-render with new approval_decision shown
      showSuccess("Approval recorded")
      refreshDeploymentCard(updated)
    }
  }
}
```

### 5.2 SSE Subscription Pattern

```javascript
const eventSource = new EventSource(
  '/subscription/operator/deployment-review/{plan_id}'
)

eventSource.addEventListener('snapshot', (evt) => {
  // Full snapshot of composed view
  const data = JSON.parse(evt.data)
  // data.meta.staleness tells us freshness
  refreshUI(data)
  if (data.meta.staleness == null) {
    hideStalenessWarning()
  } else {
    showStalenessWarning(data.meta.staleness)
  }
})

eventSource.addEventListener('approval_decision_updated', (evt) => {
  // Incremental update: approval was recorded
  const approval = JSON.parse(evt.data)
  updateApprovalCard(approval)
  hideSubmitSpinner()
  showSuccess("Approval recorded at " + approval.timestamp)
})

eventSource.addEventListener('error', (evt) => {
  console.warn("SSE connection lost, reverting to polling")
  startPollingForUpdates()
})
```

### 5.3 State Reconciliation After Network Outage

If the operator's network drops during a command submission:

1. **Before submission sent**: Retry logic, user is aware nothing was sent yet
2. **After submission sent, before SSE connects**: Show UI: "Submission received, waiting for confirmation..." with polling
3. **During SSE disconnect**: Fall back to polling command status every 2 seconds
4. **When SSE reconnects**: Get fresh snapshot, compare with UI state, reconcile any mismatches
5. **If command succeeded offline**: UI shows success once SSE reconnects and reports `executed`

---

## 6. Screen Transitions (State Machine)

### 6.1 Deployment Review Screen

```
Initial Load
  ├─→ Fetch composed view from BFF
  ├─→ Subscribe to SSE
  └─→ Render with staleness badge

User clicks [Approve]
  ├─→ Show confirmation dialog (if stale data)
  ├─→ Submit command
  ├─→ Show spinner "Approval submitted..."
  ├─→ Poll/listen for execution
  └─→ Update screen with new approval state

SSE: approval_decision_updated
  ├─→ Hide spinner
  ├─→ Show success banner
  ├─→ Update approval card
  ├─→ Disable [Approve]/[Reject] buttons (already approved)
  └─→ Maybe enable [Rollback] button if workflow allows

User refreshes page
  ├─→ Fetch fresh composed view (start over)
  └─→ Show current state
```

### 6.2 Incident Response Screen

```
Initial Load (incident is active)
  ├─→ Fetch incident details + runtime list
  ├─→ Subscribe to SSE for incident/{id}
  └─→ Show [Pause Runtime], [Rollback], [Kill-Switch] buttons ENABLED

User clicks [Activate Kill-Switch]
  ├─→ Prompt for MFA
  ├─→ Show [Confirm] dialog
  ├─→ Submit command
  └─→ Show spinner "Kill-switch activating..."

SSE: kill_switch_activated
  ├─→ Update kill-switch card: state = "active"
  ├─→ Disable [Pause Runtime], [Rollback] buttons
  ├─→ Show success banner: "Kill-switch active. All runtimes halting."
  ├─→ Show [Deactivate Kill-Switch] button instead
  └─→ Auto-refresh runtime list to show halting progress

SSE: runtime_binding_halted
  ├─→ Update runtime list (move from active to halted)
  ├─→ Show counter: "45 of 47 runtimes halted..."
```

---

## 7. Data Staleness Display Patterns

### 7.1 In Response to "Fresh" Data

```json
{
  "data": {
    "deployment_plan": { "id": "dp-123", "...": "..." }
  },
  "meta": {
    "staleness": null
  }
}
```

**UI**: No staleness indicator; proceed normally

### 7.2 In Response to "Degraded" Data

```json
{
  "data": {
    "runtime_binding": { "id": "rb-456", "...": "..." }
  },
  "meta": {
    "staleness": {
      "served_from": "cache",
      "last_known_at": "2026-04-10T15:00:00Z",
      "age_seconds": 45,
      "max_age_seconds": 300
    }
  }
}
```

**UI**:
```
Runtime Binding: {id}
  Status: {state}
  ⏱️  Data age: 45 seconds (cache, up to 5 minutes old)
  [Re-verify via CLI]
```

### 7.3 In Response to "Unavailable" Data

```json
{
  "data": null,
  "meta": {
    "staleness": {
      "served_from": "none",
      "last_check_at": "2026-04-10T15:00:30Z",
      "error": "downstream_service_unavailable"
    }
  }
}
```

**UI**:
```
Runtime Binding: Unknown
  ⚠️  Unable to verify current state
  Last check: 15:00:30 UTC
  
  Options:
    1. Wait for service recovery and [Refresh]
    2. Use admin CLI: pantheon-admin runtime show {binding_id}
```

---

## 8. Acceptance Criteria for APP-002

✅ All three operator screens (deployment, incident, evolution) have defined view-state matrices  
✅ Button gating rules are explicit based on data staleness  
✅ SSE reconciliation logic is specified  
✅ Degradation banner decision tree is clear  
✅ "Never show none" rule is enforced  
✅ Read-after-write flow handles network outages  
✅ EVO-004 dependency is called out (separate review/execute until spec lands)  
✅ No canonical truth is modified — pure UI specification  

---

## 9. Implementation Notes

This spec is **design-phase** work. Implementation will:

1. Build React/Vue components for each screen
2. Implement SSE subscription and polling fallback
3. Add staleness badge rendering logic
4. Build button state manager based on data state enum
5. Implement confirmation dialogs for degraded/stale actions
6. Add real-time update animation / reconciliation
7. Build offline queue for commands if network drops

---

## 10. Dependencies

- **APP-001 (done)**: Stable read surfaces that populate these screens
- **Operator Action Contract (APP-002 sibling)**: Defines command submission contract
- **Secondary Control Path (APP-002 sibling)**: Fallback paths shown in UI
- **EVO-004 (todo)**: Will refine the evolution review/execute boundary

---

*Generated by Copilot as support artifact for APP-002. Ready for parent task absorption.*
