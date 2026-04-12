# APP-002 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002` — Define operator-facing deployment, incident, and evolution surfaces
**Parent Owner**: Copilot
**Parent Reviewer**: Claude
**Parent Status**: `todo`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Copilot
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-10T15:00:26Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages APP-001 outputs into an APP-002-ready operator handoff.

---

## 1. What APP-002 Can Reuse Immediately

| Source | What is already locked | Why APP-002 should reuse it |
|---|---|---|
| `services/control-plane/bff/BFF_API_CONTRACT.md` | 33 canonical GET surfaces, 4 composed operator read views, 3 SSE feeds, RBAC baseline, read-only guarantee | APP-002 should treat this as the operator read context and avoid inventing parallel read models |
| `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` | Partial degradation rules, total-outage behavior, admin CLI requirements, control-plane internal API bypass, UI degradation states | APP-002 should reuse this as the fallback baseline instead of redefining degraded behavior |
| `services/control-plane/bff/APP_001C_OPEN_QUESTIONS.md` | Q7 explicitly says admin CLI / secondary control path spec belongs to APP-002 rather than APP-001 | Confirms that APP-002 owns the operator write / fallback specification gap |
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | `EvolutionDecision` lifecycle, action classes, reviewed/approved owners, threshold policy | APP-002 operator controls must map to these governance objects rather than ad-hoc UI actions |
| `PAPER_CANARY_LIVE_POLICY.md` | Paper / canary / live stage semantics and `DeploymentPlan.rollback` expectations | APP-002 deployment and rollback UX must reflect stage-specific policy and rollback readiness |

---

## 2. Parent Readiness And Real Blockers

| Item | Status | APP-002 impact |
|---|---|---|
| `APP-001` governed BFF | `done` | Read-side operator views are ready and should be consumed as-is |
| `EVO-004` operational evolution boundaries | `todo` | Final operator command semantics for freeze / rollback / retrain / redeploy are not fully wired yet |
| APP-001 Q7 secondary control path ownership | Open, explicitly tracked for APP-002 | APP-002 should absorb admin CLI / internal API operator spec work |

**Practical consequence**:

- APP-002 can design operator screens, action drawers, fallback banners, and command/result UX now.
- APP-002 should **not** pretend the final evolution execution boundary is settled until `EVO-004` lands.

---

## 3. Reusable Operator Journeys

### 3.1 Deployment Review

**Existing read context**:

- Composed view: `GET /api/v1/operator/deployment-review/{plan_id}`
- Underlying APP-001 surfaces: `DP-02`, `CP-02`, `CP-04`, `RT-02`, `RT-04`
- Related standalone read surface: `DP-03` approval decisions list

**Operator actions APP-002 must formalize**:

- approve or reject a deployment review outcome
- decide whether runtime state is sufficiently verified to proceed
- switch to fallback verification when rollback or runtime state is degraded

**Canonical objects the action layer must target**:

- `ApprovalDecision`
- `DeploymentPlan`
- `RuntimeBinding`
- `RollbackRecord`

**Frontend handoff note**:

- The screen already has a stable read bundle.
- The missing piece is the write-side command contract and confirmation / failure UX around `ApprovalDecision`.

### 3.2 Incident Response

**Existing read context**:

- Composed view: `GET /api/v1/operator/incident-response/{incident_id}`
- Underlying APP-001 surfaces: `IN-02`, `RT-03`, `TL-02`, `RT-04`, `EV-04`, `IN-05`
- Related list surface: `IN-01` incident list

**Operator actions APP-002 must formalize**:

- activate kill-switch / force risk-off
- pause runtime
- execute rollback
- pivot from BFF path to admin CLI / internal API when runtime status is unverifiable

**Canonical objects the action layer must target**:

- `IncidentCase`
- `FreezeOrder`
- `RuntimeBinding`
- `RollbackRecord`

**Frontend handoff note**:

- APP-001 already documents that incident UI must never show "no incidents" or "kill-switch inactive" when data is unreachable.
- APP-002 should make the fallback control path visible as a first-class action panel, not a hidden runbook link.

### 3.3 Post-Incident Review And Evolution Control

**Existing read context**:

- Composed view: `GET /api/v1/operator/post-incident-review/{incident_id}`
- Underlying APP-001 surfaces: `IN-04`, `EV-01`, `EV-02`, `LN-01`, `TL-03`
- Related standalone read surfaces: `EV-03` freeze orders, `EV-04` rollback records

**Operator actions APP-002 must formalize**:

- review / approve / reject / execute evolution decisions
- inspect freeze and rollback history before operational action
- decide when an incident outcome becomes governance action versus immediate runtime mitigation

**Canonical objects the action layer must target**:

- `PostmortemReport`
- `EvolutionDecision`
- `FreezeOrder`
- `RollbackRecord`

**Dependency note**:

- `EVOLUTION_REVIEW_AND_THRESHOLDS.md` already defines owners and risk classes.
- `EVO-004` is still needed to settle the operational handoff between evolution decisions and runtime/deployment execution.

---

## 4. APP-002 Gaps That Still Need Formalization

These are the actual APP-002 gaps implied by APP-001. They are not new canonical truth; they are follow-on design work.

| Gap ID | Gap | Why APP-001 does not solve it | APP-002 implication |
|---|---|---|---|
| G1 | Operator command contract | APP-001 is GET-only by design | APP-002 must define how operator actions are submitted, validated, audited, and reconciled |
| G2 | Secondary control path spec | APP-001 documents required CLI/internal API operations but explicitly does not implement them | APP-002 should formalize admin CLI / protected API usage, UX entry points, and result states |
| G3 | Approval action semantics | APP-001 exposes `ApprovalDecision` reads only | APP-002 must define approve / reject action boundaries, required preconditions, and failure handling |
| G4 | Incident command receipts | APP-001 shows runtime / incident / kill-switch state, not command submission or execution receipts | APP-002 needs operator-visible status for submitted pause / rollback / kill-switch actions |
| G5 | Evolution execution boundary | L1 policy defines review owners and action classes, but `EVO-004` has not wired operational boundaries yet | APP-002 should separate "review decision" UX from "execute operational action" UX until `EVO-004` closes |
| G6 | Degraded-mode action gating | APP-001 defines stale / degraded / unavailable read behavior, but not button-enable policy | APP-002 must define when actions stay enabled, require re-verification, or redirect to fallback path |
| G7 | Read-after-write reconciliation | APP-001 provides SSE feeds for runtime / incidents / kill-switch, but no APP-002 action UX | APP-002 should define how command submission and SSE updates converge in the UI |

---

## 5. Frontend Handoff Materials

### 5.1 Recommended Screen Modules

| Screen / module | Reuse from APP-001 | APP-002-specific addition |
|---|---|---|
| Deployment Review Console | `/api/v1/operator/deployment-review/{plan_id}` plus `DP-03` | Approval action drawer, precondition summary, rejected / approved / pending command states |
| Incident Response Console | `/api/v1/operator/incident-response/{incident_id}` plus `IN-01` | Kill-switch / rollback / pause control rail, command receipts, direct fallback CTA |
| Post-Incident / Evolution Console | `/api/v1/operator/post-incident-review/{incident_id}`, `EV-03`, `EV-04` | Evolution decision review actions, freeze/rollback distinction, execution boundary warnings |
| Global Degradation Banner | `meta.staleness`, `meta.surfaces`, degraded-path rules | Button gating, fallback entry, "do not trust empty state" messaging |

### 5.2 Shared UI State Model

This is a **recommended APP-002 frontend handling model**, derived from APP-001 degraded-path rules.

| Read state | UI meaning | Recommended APP-002 action policy |
|---|---|---|
| `fresh` | Verified current state | Primary operator actions may use the normal APP-002 command path |
| `degraded` | Replica-backed or slower-but-verifiable state | Show warning; allow low-risk review actions, but require explicit confirmation before irreversible operator commands |
| `stale` | Cache-backed last-known state | Do not silently treat as current; require re-verification or fallback path before safety-critical action |
| `partial` | Reconstructed or incomplete composite state | Keep review visible; block action paths that rely on the missing sub-surface |
| `unavailable` | No verifiable payload | Never render an empty-success state; route to fallback instructions or hold the action |

### 5.3 Minimum Data Each APP-002 Action Drawer Should Display

- target object id and type (`DeploymentPlan`, `RuntimeBinding`, `IncidentCase`, `EvolutionDecision`)
- last read snapshot time (`snapshot_at` or `meta.staleness.last_known_at`)
- sub-surface health from `meta.surfaces`
- why the action is enabled, blocked, or redirected
- fallback path guidance when BFF is not the authoritative execution route
- audit expectation text for the operator

---

## 6. Suggested Parent Deliverables For Copilot

The parent owner can likely turn this packet into three APP-002 artifacts without changing APP-001 truth:

1. **Operator Action Contract**
   - map operator intents to canonical target objects
   - distinguish review actions from operational execution actions
   - define command/result states and audit expectations

2. **Secondary Control Path Spec**
   - admin CLI commands
   - protected internal API usage
   - RBAC + MFA expectations
   - fallback UX copy and escalation path

3. **Frontend View-State Matrix**
   - per-screen read dependencies
   - per-state button gating
   - SSE reconciliation model
   - degraded / outage banners and operator guidance

---

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | ✅ PASS | Only `support/sidecars/APP-002/APP-002-SIDECAR-BFF-HANDOFF.md` created |
| No canonical truth edited | ✅ PASS | APP-001 and L1 documents referenced only |
| Packet is anchored to current shared truth | ✅ PASS | Derived from `ai-status.json`, `current-work.md`, `ai-activity-log.jsonl`, and existing APP-001 canonical outputs |
| Reviewer handoff ready | ✅ PASS | Sections 3-6 are structured for Copilot to absorb into parent APP-002 planning |

---

## 8. Handoff To Reviewer (Copilot)

Copilot, this packet narrows APP-002 to the real remaining work:

- APP-001 already solved the read-side BFF problem
- APP-002 should focus on operator command semantics, secondary control path formalization, and frontend state/action UX
- `EVO-004` remains the only material blocker for final evolution-execution boundary lock

Recommended next step:

- review this sidecar packet
- absorb the reusable matrices into APP-002 planning
- keep APP-002 scoped to operator surfaces and fallback execution path, without reopening APP-001 read-model decisions

---

*Generated by Codex as a sidecar `bff_handoff_packet` helper for APP-002. This file is a support artifact and does not modify canonical truth.*
