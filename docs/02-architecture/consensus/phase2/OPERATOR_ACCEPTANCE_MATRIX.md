# Operator Acceptance Matrix

**Task**: BG-006 — Blueprint Gap P1 closure artifact
**Owner**: Codex
**Reviewer**: Qwen
**Date**: 2026-04-13
**Status**: draft — aligned to shared truth and ready for reviewer sign-off
**Tier**: L2 Planning & Execution

> This document consolidates all operator-facing surfaces, access paths, permissions, degraded behaviors, and drill status into one acceptance matrix. It does **not** invent new surfaces or shadow objects. Every entry traces to an existing canonical contract or a documented support artifact.

---

## 1. Purpose

This matrix answers five questions for every operator-facing surface:

1. Is it **authoritative**, **composed**, **fallback**, or **support-only**?
2. Which **canonical objects** does it expose or act upon?
3. What **permissions and MFA rules** apply?
4. What happens in **degraded / fallback** conditions?
5. What is its current **test and drill status**?

Source documents:
- `services/control-plane/bff/BFF_API_CONTRACT.md` — primary read path contract
- `services/control-plane/bff/BFF_SURFACE_INVENTORY.md` — surface inventory
- `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` — degraded behavior
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` — resilience policy
- `support/sidecars/APP-002/APP-002-OPERATOR-ACTION-CONTRACT.md` — command contract
- `support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md` — CLI/internal API spec
- `support/sidecars/APP-002/APP-002-FRONTEND-STATE-MATRIX.md` — frontend state gating
- `tools/pantheon_admin/cli.py` — CLI implementation
- `services/control_plane/internal_api.py` — internal API implementation

---

## 2. Surface Classification

### 2.1 Classification Vocabulary

| Class | Meaning | Operator trust level |
|-------|---------|---------------------|
| **authoritative** | Data served directly from the canonical source system; no caching or reconstruction | Trust for immediate action |
| **composed** | BFF assembles multiple authoritative surfaces into a convenience view; staleness markers are required | Trust for most actions; verify sub-surfaces before irreversible commands |
| **fallback** | Secondary access path when BFF is unavailable; CLI or internal API; executes real actions with full audit trail | Trust for write operations when BFF is down |
| **support-only** | Design artifact, sidecar, or non-runtime spec; not an operator runtime surface | Read for governance record; do not use as operational truth |

---

## 3. BFF Read Surfaces (Primary Path)

### 3.1 Persona Surfaces

Source: `PERSONA_RUNTIME_MODEL.md` · BFF owner: Qwen · Status: done

| Surface ID | Name | Class | Canonical Objects | Required Role | Degraded Behavior |
|------------|------|-------|-------------------|--------------|-------------------|
| PS-01 | Persona List | authoritative | `Persona` | `viewer` | Serve from read-replica with staleness marker; never show "0 personas" when service is down |
| PS-02 | Persona Detail | authoritative | `Persona`, `PersonaCapitalBinding[]` | `operator` | Persona metadata from read-replica; binding unavailability shown explicitly |
| PS-03 | Persona Sessions | authoritative | `SessionPersona[]` | `operator` | No cache; show "session data unavailable" with last-check timestamp |
| PS-04 | Session Detail | authoritative | `SessionPersona`, `CapabilitySnapshot` | `operator` | No cache; surface unavailable individually |
| PS-05 | Teaching History | authoritative | `TeachingSession[]` | `operator` | No cache; show "unavailable" |
| PS-06 | Capability View | authoritative | `CapabilitySnapshot` | `operator` | No cache; show "unavailable" |

**Drill status**: PS-01 and PS-02 covered by `test_persona_management.py`. PS-03 through PS-06 have unit coverage; degraded-path drill is pending (open item §7).

### 3.2 Capital Pool & Binding Surfaces

Source: `BINDING_AND_DEPLOYMENT_SEMANTICS.md` · Status: done

| Surface ID | Name | Class | Canonical Objects | Required Role | Degraded Behavior |
|------------|------|-------|-------------------|--------------|-------------------|
| CP-01 | Capital Pool List | authoritative | `CapitalPool` | `operator` | Read-replica; staleness marker required |
| CP-02 | Pool Detail | composed | `CapitalPool`, `PersonaCapitalBinding[]` | `operator` | Pool data from replica; binding data unavailability surfaced in `meta.surfaces`; never show "no bindings" when service is down |
| CP-03 | Binding List | authoritative | `PersonaCapitalBinding[]` | `operator` | Read-replica fallback; staleness marker |
| CP-04 | Binding Detail | authoritative | `PersonaCapitalBinding`, `Persona` | `operator` | If unavailable: show "binding state unverifiable"; do not show empty |

**Drill status**: Read-store deployment tests cover CP-01–CP-04 (`test_read_store_deployment.py`). Degraded-mode path confirmed in smoke test.

### 3.3 Deployment Surfaces

Source: `BINDING_AND_DEPLOYMENT_SEMANTICS.md` · Status: done

| Surface ID | Name | Class | Canonical Objects | Required Role | Degraded Behavior |
|------------|------|-------|-------------------|--------------|-------------------|
| DP-01 | Deployment Plan List | authoritative | `DeploymentPlan[]` | `operator` | Read-replica; staleness marker |
| DP-02 | Plan Detail | authoritative | `DeploymentPlan`, `ApprovalDecision` | `operator` | Read-replica for plan data; if approval state unverifiable, surface warning |
| DP-03 | Approval Decision List | authoritative | `ApprovalDecision[]` | `operator` | Governance-critical: show "approval state unverifiable" rather than stale decision |
| DP-04 | Approval Detail | authoritative | `ApprovalDecision` | `operator` | Same as DP-03 |

**Drill status**: `test_read_store_deployment.py` covers all DP surfaces. Governance-critical degraded path confirmed in smoke test.

### 3.4 Runtime & Incident Surfaces

Source: `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `TARGET_ARCHITECTURE.md` · Status: done

| Surface ID | Name | Class | Canonical Objects | Required Role | Degraded Behavior |
|------------|------|-------|-------------------|--------------|-------------------|
| RT-01 | Runtime Binding List | authoritative | `RuntimeBinding[]` | `operator` | Read-replica; staleness marker; never show "no active runtimes" when service is down |
| RT-02 | Runtime Binding Detail | authoritative | `RuntimeBinding` | `operator` | Read-replica or "unavailable" |
| RT-03 | Runtime Status Summary | composed | `RuntimeBinding[]`, `TelemetryEvent[]` | `operator` | Partial degradation: show which sub-surfaces are degraded |
| RT-04 | Kill-Switch Status | authoritative | `FreezeOrder`, `KillSwitchOrder` | `admin` | **Never** show "kill-switch inactive" when data is unverifiable; always show "state unknown" with fallback path guidance |
| IN-01 | Incident List | authoritative | `IncidentCase[]` | `operator` | **Never** show "no incidents" when service is down; show "incident state unverifiable" |
| IN-02 | Incident Detail | authoritative | `IncidentCase` | `operator` | Read-replica; show staleness |
| IN-03 | Incident Timeline | composed | `IncidentCase`, `TelemetryEvent[]` | `operator` | Partial: show which sub-surfaces degraded |
| IN-04 | Post-Incident Report | authoritative | `PostmortemReport` | `operator` | Read-replica; staleness marker |
| IN-05 | Active Runtime Snapshot | composed | `RuntimeBinding[]`, `TelemetryEvent[]` | `operator` | Partial degradation allowed; sub-surface health in `meta.surfaces` |

**Drill status**: `test_read_store_incident.py` and `smoke_test_incident.py` cover IN surfaces. RT surfaces covered by `test_w4_remaining_catalog.py`. Kill-switch degraded path explicitly covered in `DEGRADED_OPERATOR_PATH.md §5`.

### 3.5 Telemetry & Lineage Surfaces

Source: `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` · Status: done

| Surface ID | Name | Class | Canonical Objects | Required Role | Degraded Behavior |
|------------|------|-------|-------------------|--------------|-------------------|
| TL-01 | Telemetry Query | authoritative | `TelemetryEvent[]` | `viewer` | Read-replica; time-range window enforced (30-day max) |
| TL-02 | Live Telemetry SSE | authoritative | `TelemetryEvent` (streaming) | `viewer` | SSE disconnects gracefully; client falls back to polling TL-01 |
| TL-03 | Telemetry Snapshot | composed | `TelemetryEvent[]`, `RuntimeBinding` | `viewer` | Partial degradation; show per-sub-surface health |
| LN-01 | Lineage Graph | composed | `LineageEdge[]` | `viewer` | Reconstruct from object references with "partial" marker; depth limited to 10 |
| LN-02 | Artifact Lineage | authoritative | `LineageEdge[]` | `viewer` | Read-replica; staleness marker |
| LN-03 | Lineage Depth Query | composed | `LineageEdge[]` (recursive) | `viewer` | Depth cap of 10; unavailability shown per segment |

**Drill status**: `test_w3_surfaces.py` and `smoke_test.py` cover TL/LN surfaces.

### 3.6 Evolution Surfaces

Source: `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md` · Status: done

| Surface ID | Name | Class | Canonical Objects | Required Role | Degraded Behavior |
|------------|------|-------|-------------------|--------------|-------------------|
| EV-01 | Evolution Decision List | authoritative | `EvolutionDecision[]` | `operator` | Read-replica; staleness marker |
| EV-02 | Evolution Decision Detail | authoritative | `EvolutionDecision` | `operator` | Read-replica; show staleness |
| EV-03 | Freeze Order List | authoritative | `FreezeOrder[]` | `operator` | Read-replica; critical — show staleness, not empty |
| EV-04 | Rollback Record List | authoritative | `RollbackRecord[]` | `operator` | Read-replica; staleness marker |

**Drill status**: EV surfaces covered by `test_w3_surfaces.py`.

---

## 4. Composed Operator Views (BFF Convenience Layer)

These are BFF-assembled multi-surface views. They carry `meta.surfaces` indicating per-sub-surface health.

| View ID | Name | Class | Constituent Surfaces | Required Role | Action Gating Rule |
|---------|------|-------|---------------------|--------------|-------------------|
| OV-D | Deployment Review Console | composed | DP-02, CP-02, CP-04, RT-02, RT-04, DP-03 | `operator` | `fresh`/`degraded` → actions enabled with warning; `stale` → confirm dialog; `unavailable` → disable Approve, route to CLI |
| OV-I | Incident Response Console | composed | IN-02, RT-03, RT-04, TL-02, EV-04, IN-05 | `operator` | Kill-switch status remains visible only to `admin`; Pause/Rollback require at least `degraded`; `unavailable` → route to CLI |
| OV-E | Post-Incident Evolution Console | composed | IN-04, EV-01, EV-02, EV-03, EV-04, LN-01, TL-03 | `operator` | Read review is available to operators; higher-privilege command submission still follows §5 role gates and EVO-004 boundaries |

**SSE feeds**: Each composed view has a corresponding SSE subscription at `/subscription/operator/{view}/{id}`. If SSE disconnects, client falls back to polling the composed view endpoint.

**Staleness rule**: The composed view response includes `meta.staleness` (null if fresh) and `meta.surfaces` (per-sub-surface state). The BFF must never suppress staleness indicators.

---

## 5. Operator Write Path (Command Submission via BFF)

Source: `APP-002-OPERATOR-ACTION-CONTRACT.md`

All operator write actions are submitted through `POST /api/v1/operator/commands`. The BFF validates, queues, and returns a receipt. Canonical state is mutated downstream — the BFF never writes directly.

| Command | Canonical Objects Affected | Required Role | MFA Required | Preconditions |
|---------|---------------------------|--------------|-------------|---------------|
| `ApproveDeployment` | `ApprovalDecision`, `DeploymentPlan` | `approver`, `admin` | If deployment affects >N runtimes (configurable threshold) | Plan in `planned`/`pending_approval`; no active incident on target |
| `PauseRuntime` | `RuntimeBinding`, `IncidentCase` | `operator`, `admin` | If non-admin caller | Runtime in `running` state; read surface `fresh` or `degraded` |
| `ResumeRuntime` | `RuntimeBinding` | `operator`, `admin` | If resuming early (before duration expiry) | Runtime in `paused` state |
| `ExecuteRollback` | `RollbackRecord`, `DeploymentPlan`, `RuntimeBinding` | `approver`, `admin` | **Always** | Prior good state available; no concurrent rollback |
| `ActivateKillSwitch` | `KillSwitchOrder`, `RuntimeBinding[]` | `admin` | **Always** | Valid scope; no active kill-switch for scope |
| `DeactivateKillSwitch` | `KillSwitchOrder` | `admin` | **Always** | Active kill-switch for scope exists |
| `ApproveEvolutionDecision` | `EvolutionDecision` | `reviewer`, `approver`, `admin` | If decision risk level is high | Decision in `reviewed` state; not superseded |
| `ExecuteEvolutionAction` | `EvolutionDecision`, target objects | `admin` | **Always** | Decision in `approved` state; EVO-004 boundary satisfied |

**Command receipt flow**: All commands return HTTP 202 with a `command_id`. Operator polls `GET /api/v1/operator/commands/{command_id}` or subscribes to SSE for status updates.

**Degraded-mode command behavior**: If BFF read surface for the command target is `unavailable`, the command is still accepted but the response includes staleness context. If the downstream command queue is unreachable, BFF returns HTTP 503 with secondary control path guidance.

---

## 6. Secondary Control Path (Fallback)

Source: `APP-002-SECONDARY-CONTROL-PATH.md`, `tools/pantheon_admin/cli.py`, `services/control_plane/internal_api.py`

The secondary path is the operator fallback when the BFF is unavailable. Both CLI and internal API execute **real actions** through runtime-manager components with full audit trails.

### 6.1 Access Path Summary

| Path | Transport | Auth | MFA Enforcement | When to Use |
|------|-----------|------|-----------------|-------------|
| **BFF (primary)** | HTTPS | Bearer token + RBAC | Per-command (see §5) | Normal operations; all read queries |
| **Admin CLI** (`pantheon-admin`) | SSH | SSH key + RBAC role | Required for all destructive actions | Local/SSH access; scripted deployment ops |
| **Internal API** | HTTPS | Bearer token + RBAC | Required for all destructive actions | Remote access when BFF is down; programmatic escalation |

### 6.2 CLI → Internal API Command Coverage

| CLI Command | Internal API Endpoint | MFA | Scope | Implementation Status |
|-------------|----------------------|-----|-------|----------------------|
| `pantheon-admin deployment approve <plan_id>` | `POST /api/internal/v1/deployments/{plan_id}/approve` | If approver role + large deployment | Real execution via command store | ✅ Implemented |
| `pantheon-admin deployment reject <plan_id>` | `POST /api/internal/v1/deployments/{plan_id}/approve` (decision=reject) | No | Real execution | ✅ Implemented |
| `pantheon-admin runtime pause <binding_id>` | `POST /api/internal/v1/runtimes/{binding_id}/pause` | If non-admin | `RuntimeBindingStore` state machine | ✅ Implemented |
| `pantheon-admin runtime resume <binding_id>` | `POST /api/internal/v1/runtimes/{binding_id}/resume` | If early resume | `RuntimeBindingStore` state machine | ✅ Implemented |
| `pantheon-admin runtime force-halt <binding_id>` | `POST /api/internal/v1/runtimes/{binding_id}/halt` | **Always** | `RuntimeBindingStore` state machine | ✅ Implemented |
| `pantheon-admin rollback execute <target_id>` | `POST /api/internal/v1/rollbacks/execute` | **Always** | `RuntimeBindingStore`; rollback action matrix | ✅ Implemented |
| `pantheon-admin rollback list <target_id>` | `GET /api/internal/v1/rollbacks?target_id=...` | No | Read-only | ✅ Implemented |
| `pantheon-admin rollback abort <rollback_id>` | `POST /api/internal/v1/rollbacks/{rollback_id}/abort` | **Always** | In-progress rollback only | ✅ Implemented |
| `pantheon-admin kill-switch activate` | `POST /api/internal/v1/kill-switch` (action=activate) | **Always** | `KillSwitchController` fast path | ✅ Implemented |
| `pantheon-admin kill-switch deactivate` | `POST /api/internal/v1/kill-switch` (action=deactivate) | **Always** | `KillSwitchController` | ✅ Implemented |
| `pantheon-admin kill-switch status` | `GET /api/internal/v1/kill-switch` | No | Read-only | ✅ Implemented |
| `pantheon-admin evolution approve <decision_id>` | Not yet exposed | If high-risk | **Out of scope (evolution controller API not yet exposed)** | ⚠️ Returns `EXIT_UNAVAILABLE` |
| `pantheon-admin evolution reject <decision_id>` | Not yet exposed | No | **Out of scope** | ⚠️ Returns `EXIT_UNAVAILABLE` |
| `pantheon-admin evolution execute <decision_id>` | Not yet exposed | **Always** | **Out of scope pending EVO-004** | ⚠️ Returns `EXIT_UNAVAILABLE` |

### 6.3 CLI Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Command execution failed |
| 2 | Authorization failed (RBAC or MFA rejected) |
| 3 | CLI usage error (invalid arguments) |
| 4 | Control-plane service unavailable |
| 5 | Partial execution |

### 6.4 Internal API Authentication

- **Bearer token**: JWT issued by internal auth system; signed with control-plane private key; 1-hour validity
- **MFA header**: `X-MFA-Token: <6-digit TOTP>` required for all destructive operations
- **IP whitelisting**: Requester IP must be in control-plane whitelist (or via VPN)
- **Audit persistence**: All commands logged to JSON command store (configurable path; `/tmp/pantheon/internal_api/commands.json` default for dev)

### 6.5 Fallback Audit Consistency

Both BFF and secondary path commands create identical audit records in the central audit log. There is no CLI-vs-BFF distinction in the audit trail. Commands are **idempotent**: issuing the same command twice returns the same receipt ID without creating duplicates.

---

## 7. Degraded Mode Acceptance Criteria

Source: `DEGRADED_OPERATOR_PATH.md`, `APP-002-FRONTEND-STATE-MATRIX.md`

### 7.1 BFF Surface Degradation Tiers

| Tier | State | Served From | Max Age | Operator Action Policy |
|------|-------|------------|---------|----------------------|
| T1 | `fresh` | Source system | — | All actions enabled |
| T2 | `degraded` | Read-replica | < 5 min | Low-risk actions enabled; confirm before irreversible commands |
| T3 | `stale` | Cache | 5–30 min | Confirm dialog required; do not take irreversible actions without re-verification |
| T4 | `partial` | Mixed (some surfaces fresh, some degraded/unavailable) | Mixed | Show `meta.surfaces` breakdown; block actions that depend on unavailable sub-surfaces |
| T5 | `unavailable` | None | — | No BFF action; route to secondary control path; **never render false-positive empty state** |

### 7.2 "Never Show False-Positive Empty" Rule

The following surfaces must **never** render empty/zero/inactive when their data is unavailable:

| Surface | Forbidden false-positive | Required message |
|---------|------------------------|-----------------|
| IN-01 Incident List | "No incidents" | "Incident state unverifiable — use CLI to verify" |
| RT-04 Kill-Switch Status | "Kill-switch inactive" | "Kill-switch state unknown — verify via CLI" |
| CP-02 Pool Detail | "No bindings" | "Binding state unverifiable — service unavailable" |
| RT-01 Runtime Binding List | "No active runtimes" | "Runtime state unverifiable — use CLI" |

### 7.3 Action Gating Matrix

| Data State | Deployment Approve | Runtime Pause | Kill-Switch Activate | Evolution Approve |
|------------|-------------------|---------------|---------------------|-------------------|
| `fresh` | ✅ Enabled | ✅ Enabled | ✅ Enabled (MFA) | ✅ Enabled |
| `degraded` | ✅ Enabled + warning | ✅ Enabled | ✅ Enabled (MFA) | ✅ Enabled |
| `stale` | ⚠️ Confirm dialog | ⚠️ Confirm dialog | ✅ Enabled (MFA) | ⚠️ Confirm dialog |
| `partial` | ✅ If relevant sub-surfaces ok | ✅ If runtime data ok | ✅ Enabled (MFA) | ✅ If decision data ok |
| `unavailable` | ❌ Disabled → CLI | ❌ Disabled → CLI | ⚠️ CLI with MFA | ❌ Disabled → CLI |

> **Safety exception**: Kill-switch activate is never fully disabled at the BFF level even on `stale` data; MFA is always required. In `unavailable` state, CLI path with MFA is the canonical path.

---

## 8. Support-Only Surfaces

These are design artifacts, sidecar acceptance packets, and planning documents. They are **not** operational runtime surfaces and must not be used as operator truth during incidents.

| Artifact | Location | Type | Operational relevance |
|----------|---------|------|----------------------|
| BFF Handoff Packet | `support/sidecars/APP-002/APP-002-SIDECAR-BFF-HANDOFF.md` | design / sidecar | Gap analysis input for APP-002; no operational role |
| Operator Action Contract | `support/sidecars/APP-002/APP-002-OPERATOR-ACTION-CONTRACT.md` | contract design | Canonical reference for BFF command submission shape; absorbed into BFF implementation |
| Secondary Control Path Spec | `support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md` | design spec | Reference for CLI/internal API command surface; implementation in `cli.py` and `internal_api.py` |
| Frontend State Matrix | `support/sidecars/APP-002/APP-002-FRONTEND-STATE-MATRIX.md` | UX spec | Reference for frontend button gating; not runtime truth |
| CLI Fallback Acceptance | `support/sidecars/APP-002-W2-CLI-FALLBACK/` | acceptance packet | Verified CLI→API wiring; AC-1/AC-2/AC-3 all passed |
| Postincident Evolution Handoff | `support/sidecars/APP-002-W3-POSTINCIDENT-EVOLUTION/` | sidecar | Evolution surface design; no operational role |
| Persona Mgmt Handoff | `support/sidecars/APP-002-W4-PERSONA-MGMT/` | sidecar | Persona management UX; no operational role |
| SSE Live Handoff | `support/sidecars/APP-002-W5-SSE-LIVE/` | sidecar | SSE feed design; implementation in BFF |
| Lovable Cutover Acceptance | `support/sidecars/APP-002-W5-LOVABLE-CUTOVER/` | acceptance packet | Frontend handoff confirmation; no ongoing operational role |

---

## 9. RBAC Summary

Read-surface minimum roles below are aligned to `services/control-plane/bff/BFF_API_CONTRACT.md §8.2`. Command authority is stricter and remains defined by §5 plus the secondary control path.

| Role | Read surfaces | Command submission (BFF) | CLI / Internal API write | Kill-switch | Rollback |
|------|--------------|--------------------------|--------------------------|-------------|---------|
| `viewer` | Most read surfaces | None | None | None | None |
| `operator` | All read surfaces | PauseRuntime, ResumeRuntime | Pause/Resume only (MFA if non-admin) | Status read only | None |
| `reviewer` | No additional BFF read scope beyond `operator` | ApproveEvolutionDecision | None | None | None |
| `approver` | All read surfaces | ApproveDeployment, ApproveEvolutionDecision | Deployment approve/reject (MFA for large deployments) | Status read only | ExecuteRollback (MFA) |
| `admin` | All read surfaces | All commands | All CLI/internal API commands | Activate/deactivate (MFA **always**) | Execute (MFA **always**) |

---

## 10. Test and Drill Status

### 10.1 Automated Test Coverage

| Area | Test File(s) | Coverage Status |
|------|-------------|----------------|
| BFF read surfaces — persona | `test_persona_management.py` | ✅ Unit tests pass |
| BFF read surfaces — deployment | `test_read_store_deployment.py` | ✅ Unit tests pass |
| BFF read surfaces — incident | `test_read_store_incident.py` | ✅ Unit tests pass |
| BFF read surfaces — W3/evolution/lineage | `test_w3_surfaces.py` | ✅ Unit tests pass |
| BFF read surfaces — W4/catalog | `test_w4_remaining_catalog.py` | ✅ Unit tests pass |
| BFF command executor | `test_command_executor.py` | ✅ Unit tests pass |
| BFF smoke test (full) | `smoke_test.py`, `smoke_test_incident.py` | ✅ Smoke tests pass |
| CLI implementation | `tools/pantheon_admin/cli.py` | ✅ HTTP wiring verified; no separate test suite yet |
| Internal API | `services/control_plane/internal_api.py` | ✅ Manual verification; no pytest suite yet |

### 10.2 Degraded-Path Drill Status

| Drill Scenario | Status | Evidence |
|----------------|--------|---------|
| BFF partial degradation (one downstream service down) | ✅ Specified and smoke-tested | `DEGRADED_OPERATOR_PATH.md §3`, `smoke_test.py` |
| BFF total outage (all replicas down) | ✅ Specified | `DEGRADED_OPERATOR_PATH.md §5`; secondary path spec complete |
| CLI fallback drill (BFF down, operator uses CLI) | ✅ CLI wiring verified | `APP-002-W2-CLI-FALLBACK-SIDECAR-ACCEPTANCE.md §3` |
| Internal API fallback drill | ✅ Implementation verified | `APP-002-W2-CLI-FALLBACK-SIDECAR-ACCEPTANCE.md §3.2` |
| Kill-switch via CLI (BFF unavailable) | ✅ Specified and wired | `APP-002-SECONDARY-CONTROL-PATH.md §3.3.4` |
| "Never show empty" rule (incident/kill-switch unavailable) | ✅ Specified | `DEGRADED_OPERATOR_PATH.md §5`, `APP-002-FRONTEND-STATE-MATRIX.md §3.2` |
| Evolution CLI path (evolution controller not exposed) | ⚠️ Partial — returns EXIT_UNAVAILABLE | Out of scope until EVO-004 lands |

---

## 11. Open Items

| ID | Item | Blocking? | Owner | Dependency |
|----|------|-----------|-------|-----------|
| OI-1 | Evolution CLI path (approve/reject/execute) returns `EXIT_UNAVAILABLE` | No — documented and accepted for v1 | Pending EVO-004 | `EVO-004` |
| OI-2 | CLI integration test (CLI → internal API → command store end-to-end) | No — manual verification done; recommended for hardening | Codex | None |
| OI-3 | Internal API production WSGI config (gunicorn) | No — Flask dev server acceptable for v1 | Pending productionization wave | None |
| OI-4 | Command store migration from JSON file to Redis/DB | No — JSON file acceptable for v1 | Pending productionization wave | None |
| OI-5 | MFA TOTP validation (currently regex-only stub) | No — infra-level work documented as stub | Auth infra team | None |
| OI-6 | PS-03 through PS-06 degraded-path drill | No — specification complete; drill pending | Ops | None |

---

## 12. Acceptance Criteria for BG-006

| # | Criterion | Status | Evidence |
|---|-----------|--------|---------|
| AC-1 | All BFF read surfaces classified by authority (authoritative / composed) | ✅ | §3, §4 |
| AC-2 | All operator write commands enumerated with permissions and MFA rules | ✅ | §5 |
| AC-3 | CLI and internal API fallback coverage documented | ✅ | §6 |
| AC-4 | Degraded-mode behavior specified per surface and action | ✅ | §7 |
| AC-5 | "Never show false-positive empty" rule formalized | ✅ | §7.2 |
| AC-6 | Support-only surfaces distinguished from runtime surfaces | ✅ | §8 |
| AC-7 | RBAC matrix covers all paths (BFF, CLI, internal API) | ✅ | §9 |
| AC-8 | Test and drill status recorded | ✅ | §10 |
| AC-9 | Open items explicitly called out with dependency owners | ✅ | §11 |
| AC-10 | No new canonical objects or shadow models invented | ✅ | Every entry traces to existing source |

---

## 13. Reviewer Checklist (for Qwen)

- [ ] Surface classification (§3–§4) is consistent with `BFF_API_CONTRACT.md` and `BFF_SURFACE_INVENTORY.md`
- [ ] Command permissions (§5) match `APP-002-OPERATOR-ACTION-CONTRACT.md` preconditions
- [ ] CLI/internal API coverage (§6) matches `APP-002-SECONDARY-CONTROL-PATH.md` and `APP-002-W2-CLI-FALLBACK-SIDECAR-ACCEPTANCE.md`
- [ ] Degraded-mode rules (§7) match `DEGRADED_OPERATOR_PATH.md` and `APP-002-FRONTEND-STATE-MATRIX.md`
- [ ] Support-only surfaces (§8) correctly exclude from operational trust chain
- [ ] RBAC matrix (§9) is consistent with `BFF_API_CONTRACT.md §RBAC`
- [ ] Open items (§11) accurately reflect current scope boundaries (especially EVO-004 dependency)
- [ ] No canonical truth modified; all entries are packaging of existing contracts

---

*BG-006 closure artifact. Owner: Codex. Reviewer: Qwen. Source: consolidation of APP-001, APP-002, and associated sidecar evidence.*
