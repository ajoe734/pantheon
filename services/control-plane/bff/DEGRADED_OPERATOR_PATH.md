# Degraded Operator Path

Last updated: 2026-04-10
Status: canonical — degraded operator path for APP-001
Tier: L2 Planning & Execution (formal degraded path contract derived from L1 policy)
Scope: operator fallback paths when BFF or downstream services are degraded, secondary control path specifications, and degradation behavior for all surface groups
Owner: Qwen
Reviewer: Codex
Derived from: BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md, BINDING_AND_DEPLOYMENT_SEMANTICS.md

---

## 1. Purpose

This document defines the **degraded operator path** for APP-001. It answers:

- What happens when the BFF is partially or totally unavailable?
- What fallback paths exist for operators?
- How should each surface degrade when its downstream service is unavailable?
- What is the secondary control path for safety-critical operations?

This is the canonical reference for APP-001 acceptance criterion: **"degraded operator path is documented."**

---

## 2. Degradation Scenarios

### 2.1 Scenario Classification

| Scenario | Scope | Severity | Operator Impact |
|---|---|---|---|
| **Partial degradation** | One or more downstream services unavailable | Medium | Some surfaces show degraded data; others work normally |
| **Total BFF outage** | BFF itself unavailable | High | No BFF UI available; operator must use secondary control path |
| **Cascading failure** | Multiple downstream services unavailable | Critical | Most surfaces degraded; secondary control path essential |
| **Network partition** | BFF can reach some services but not others | Variable | Per-surface degradation based on connectivity |

---

## 3. Partial Degradation Behavior

### 3.1 Per-Surface Degradation

When a downstream service is unavailable, the BFF degrades **only the affected surface**. All other surfaces continue to operate normally.

**Example**: If the Persona Plane is down:
- PS-01 to PS-06 (Persona surfaces): Show degraded data or "data unavailable"
- CP-01 to CP-04 (Capital Pool surfaces): Continue operating normally
- RT-01 to RT-04 (Runtime surfaces): Continue operating normally
- All other surfaces: Continue operating normally

### 3.2 Degradation Response Pattern

| Fallback Tier | Behavior | Example |
|---|---|---|
| **Tier 1: Fresh** | Primary service responds normally | Normal operation |
| **Tier 2: Read-replica** | Serve from read-replica with staleness marker | Persona metadata from replica DB |
| **Tier 3: Cache** | Serve from cache with `last_known_at` timestamp | Runtime binding cached 5 min ago |
| **Tier 4: Reconstructed** | Reconstruct from direct object references with "partial" marker | Lineage from `artifact.run_id` references |
| **Tier 5: Unavailable** | No verifiable data; show "data unavailable" with last-check timestamp | All fallbacks exhausted |

### 3.3 Surface Group Degradation Matrix

| Surface Group | Tier 2 (Read-replica) | Tier 3 (Cache) | Tier 4 (Reconstructed) | Tier 5 (Unavailable) |
|---|---|---|---|---|
| **IN-01 to IN-05** (Incident) | ✅ with perf note | ✅ with staleness | ❌ Not reconstructable | Show "data unavailable" with last-check timestamp |
| **RT-01 to RT-04** (Runtime) | ✅ with perf note | ✅ with staleness | ❌ Not reconstructable | Show "data unavailable" with last-check timestamp |
| **CP-03 to CP-04** (Binding) | ✅ with staleness | ✅ with staleness | ❌ Not reconstructable | Show "binding state unverifiable" |
| **DP-01 to DP-04** (Deployment) | ✅ with staleness | ✅ with staleness | ✅ from `deployment_plan.artifact_id` refs | Show "deployment state unverifiable" |
| **EV-01 to EV-04** (Evolution) | ✅ with staleness | ✅ with staleness | ❌ Not reconstructable | Show "evolution data unverifiable" |
| **LN-01 to LN-03** (Lineage) | ✅ with perf note | ✅ with staleness | ✅ from direct object refs | Show "lineage data unavailable" |
| **TL-01 to TL-03** (Telemetry) | ✅ from Postgres if ClickHouse down | ✅ with staleness | ❌ Not reconstructable | Show "telemetry data unavailable" |
| **PS-01 to PS-06** (Persona) | ✅ with perf note | ✅ with staleness | ❌ Not reconstructable | Show "persona data unavailable" |
| **CS-01 to CS-06** (Consultation) | ✅ with staleness | ✅ with staleness | ❌ Not reconstructable | Show "consultation data unavailable" |

### 3.4 "Never Show None" Rule

**Critical rule**: The BFF must **never** return `"data": []` or `"data": null` as a result of a downstream service failure. This prevents the dangerous misinterpretation where an operator sees "no data" and assumes "nothing wrong" when actually the monitoring system itself has failed.

Instead:
- **List surfaces**: Return last-known or replica-backed entries with staleness metadata. If no verifiable payload is available, return HTTP 503 `DOWNSTREAM_UNAVAILABLE`.
- **Detail surfaces**: Return HTTP 503 `DOWNSTREAM_UNAVAILABLE` if the specific resource cannot be verified.
- **Composed views**: Return partial data with `meta.surfaces` showing which sub-surfaces are degraded or unavailable.

---

## 4. Total BFF Outage

### 4.1 Impact Assessment

When the BFF is totally unavailable:
- **Console UI**: Not available
- **Workbench UI**: Not available
- **Operator interaction via BFF**: Not available
- **Active runtimes**: **NOT affected** (BFF is control plane, not execution plane)
- **Telemetry collection**: **NOT affected**
- **Kill-switch**: **NOT affected** (has independent execution path)
- **Runtime-manager internal control flow**: **NOT affected**

### 4.2 Secondary Control Path

Per BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6, the following **non-BFF paths** must remain available:

| Operation | Secondary Path | Access Method | RBAC |
|---|---|---|---|
| **Kill-switch activation** | `POST /admin/kill-switch/activate` (runtime-manager protected endpoint) | Admin CLI or direct HTTPS call | `admin` (global) + MFA |
| **Kill-switch status check** | `GET /admin/kill-switch/status` (runtime-manager protected endpoint) | Admin CLI or direct HTTPS call | `operator` (global) |
| **Runtime rollback** | `POST /admin/runtimes/{runtime_id}/rollback` (runtime-manager admin endpoint) | Admin CLI or direct HTTPS call | `admin` (global) |
| **Runtime pause** | `POST /admin/runtimes/{runtime_id}/pause` (runtime-manager admin endpoint) | Admin CLI or direct HTTPS call | `admin` (global) |
| **Health diagnostics** | `GET /admin/health` (control-plane internal API) | Admin CLI or direct HTTPS call | `operator` (global) |
| **Runtime status** | `GET /admin/runtimes/{runtime_id}/status` (runtime-manager admin endpoint) | Admin CLI or direct HTTPS call | `operator` (global) |

### 4.3 Admin CLI Specification (Requirements)

The BFF documents the **required operations** for the admin CLI but does not implement it. Implementation belongs to APP-002 (operator surfaces).

**Required CLI commands**:

| Command | Purpose | Equivalent BFF Surface |
|---|---|---|
| `pantheon admin kill-switch activate --runtime={id}` | Activate kill-switch for a runtime | IN-05 (read-only on BFF) |
| `pantheon admin kill-switch status` | Check kill-switch status | IN-05 |
| `pantheon admin runtime rollback --runtime={id} --target={version}` | Rollback a runtime | RT-04 (read-only on BFF) |
| `pantheon admin runtime pause --runtime={id}` | Pause a runtime | RT-03 (read-only on BFF) |
| `pantheon admin runtime status --runtime={id}` | Check runtime status | RT-03 |
| `pantheon admin health` | Check control-plane health | N/A (composite) |
| `pantheon admin incident list` | List active incidents | IN-01 |
| `pantheon admin incident detail --incident={id}` | View incident detail | IN-02 |

**RBAC requirements**:
- All admin CLI commands require authentication
- Write commands (activate, rollback, pause) require `admin` role + MFA
- Read commands (status, health, list, detail) require `operator` role
- Audit logging is mandatory for all admin CLI commands

### 4.4 Control-Plane Internal API

The control-plane exposes an **internal API** that the admin CLI and authorized tools can call directly, bypassing the BFF:

| Internal API | Purpose | Auth |
|---|---|---|
| `GET /internal/health` | Control-plane health check | Token |
| `GET /internal/runtimes/{id}/status` | Runtime status | Token |
| `POST /internal/runtimes/{id}/pause` | Runtime pause | Token + admin scope |
| `POST /internal/runtimes/{id}/rollback` | Runtime rollback | Token + admin scope |
| `POST /internal/kill-switch/activate` | Kill-switch activation | Token + admin scope + MFA |
| `GET /internal/kill-switch/status` | Kill-switch status | Token |

This internal API is **not** user-facing. It is for authorized tools and the admin CLI.

---

## 5. Cascading Failure Behavior

### 5.1 Detection

The BFF monitors downstream service health via the **Service Adapter Layer** health checks (BFF_API_CONTRACT.md §3.3):

```
interface AdapterHealth {
  status: "healthy" | "degraded" | "unavailable";
  last_check: string;  // RFC 3339
}
```

### 5.2 Cascading Degradation Priority

When multiple services fail, the BFF degrades surfaces in this priority order:

1. **Safety-critical surfaces** (last to degrade): IN-05 (kill-switch), RT-01 to RT-04 (runtime)
2. **Governance-critical surfaces**: DP-01 to DP-04 (deployment), EV-01 to EV-04 (evolution), CP-03 to CP-04 (binding)
3. **Audit surfaces**: LN-01 to LN-03 (lineage), IN-01 to IN-04 (incident/postmortem)
4. **Observability surfaces**: TL-01 to TL-03 (telemetry)
5. **Informational surfaces**: PS-01 to PS-06 (persona), CS-01 to CS-06 (consultation)

### 5.3 Composed View Degradation

When a composed view has multiple surface dependencies and some fail:

| Failure Count | Behavior |
|---|---|
| 0 surfaces degraded | Return full composed view with `snapshot_at` |
| 1-2 surfaces degraded | Return partial composed view with `meta.surfaces` showing degraded surfaces |
| 3+ surfaces degraded | Return partial composed view with prominent degradation warning |
| All surfaces degraded | Return HTTP 503 with `DOWNSTREAM_UNAVAILABLE` and last-check timestamps |

---

## 6. Operator Journey Under Degradation

### 6.1 Pre-Deployment Review (Degraded)

```
Operator → DP-02 (deployment plan: OK)
         → CP-02 (pool detail: OK)
         → CP-04 (binding detail: DEGRADED — served from cache, last_known_at: 10 min ago)
         → RT-02 (runtime binding: UNAVAILABLE — runtime-manager timeout)
         → RT-04 (rollback history: DEGRADED — served from read-replica)

BFF Response: Partial composed view
- deployment_plan: ✅ fresh
- capital_pool: ✅ fresh
- bindings: ⚠️ stale (cache, 10 min)
- runtime_binding: ❌ unavailable (DOWNSTREAM_TIMEOUT)
- rollbacks: ⚠️ degraded (read-replica)

Operator action: Proceed with caution — runtime binding state unverifiable.
Use admin CLI: `pantheon admin runtime status --runtime={id}` to verify.
```

### 6.2 Incident Response (Degraded)

```
Operator → IN-02 (incident detail: OK)
         → RT-03 (runtime status: UNAVAILABLE)
         → TL-02 (telemetry summary: DEGRADED — Postgres only, ClickHouse down)
         → RT-04 (rollback history: UNAVAILABLE)
         → EV-04 (rollback records: OK)
         → IN-05 (kill-switch status: OK)

BFF Response: Partial composed view
- incident: ✅ fresh
- runtime_status: ❌ unavailable
- telemetry_summary: ⚠️ degraded (Postgres aggregate only)
- rollback_history: ❌ unavailable
- evolution_decisions: ✅ fresh
- kill_switch: ✅ fresh

Operator action: Kill-switch status is available and healthy.
Runtime state is unverifiable — use admin CLI to check runtime directly.
```

### 6.3 Total BFF Outage

```
BFF: Totally unavailable (HTTP 502/503 on all BFF endpoints)

Operator action:
1. Switch to admin CLI
2. Check kill-switch status: `pantheon admin kill-switch status`
3. Check runtime status: `pantheon admin runtime status --runtime={id}`
4. If incident response needed: `pantheon admin incident list`
5. If rollback needed: `pantheon admin runtime rollback --runtime={id}`

All admin CLI commands bypass the BFF and call the control-plane internal API directly.
```

---

## 7. Degradation Communication

### 7.1 UI Indicators

| State | UI Indicator | Color | Meaning |
|---|---|---|---|
| Fresh | No indicator | — | Data is current and verified |
| Degraded | "⚠ Degraded performance" | Yellow | Data from read-replica; may be slower |
| Stale | "⚠ Stale data — last known: {timestamp}" | Orange | Data from cache; may not reflect current state |
| Partial | "⚠ Partial data — reconstructed from references" | Orange | Data reconstructed; incomplete |
| Unavailable | "❌ Data unavailable — {service} not responding" | Red | No verifiable data available |

### 7.2 Composed View Surface Status

In composed views, each sub-surface shows its status:

| Status | Indicator | UI Treatment |
|---|---|---|
| `ok` | Green check | Normal rendering |
| `degraded` | Yellow warning | Render with degradation note |
| `error` | Red X | Show error details; placeholder for missing data |
| `unavailable` | Red X | Show "unavailable" placeholder; link to admin CLI guidance |

---

## 8. Verification Checklist

| APP-001 Acceptance Criterion | Status | Evidence |
|---|---|---|
| Degraded operator path is documented | ✅ | This document — covers partial degradation (§3), total BFF outage (§4), cascading failure (§5), operator journeys under degradation (§6), and secondary control path (§4.2-§4.4) |
| Secondary control path defined | ✅ | §4.2-§4.4 — admin CLI and control-plane internal API specified with RBAC |
| "Never show none" rule enforced | ✅ | §3.4 — explicit prohibition of empty/null responses on downstream failure |
| Per-surface degradation behavior | ✅ | §3.3 — degradation matrix for all surface groups |

---

*End of Degraded Operator Path*
