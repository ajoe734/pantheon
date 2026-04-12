# APP-002-W1-FRONT-HANDOFF Acceptance Packet and Dependency Map (Sidecar)

**Parent Task**: `APP-002-W1-FRONT-HANDOFF` — Publish F-042 front-end and Lovable handoff packet
**Parent Owner**: Copilot
**Parent Reviewer**: Codex
**Parent Status**: `todo` (dependency `APP-002-W1-READ-DEPLOYMENT` is `done`)
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Qwen
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-11

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It prepares the acceptance checklist and dependency map so the parent owner can execute the F-042 front-end and Lovable handoff efficiently.

---

## 1. Parent Task Summary

`APP-002-W1-FRONT-HANDOFF` is responsible for producing the contract-ready artifacts, example payloads, front-sync guidance, and Lovable prompt packet for the **F-042 Promotion Review** screen. This enables the front-end repo (`front-ai-trading-system`) and Lovable to safely wire to Pantheon BFF without inventing client-side joins or mock data.

**Parent acceptance criteria** (from `ai-status.json`):
| # | Criterion | Meaning |
|---|---|---|
| 1 | `contract_ready_published` | BFF contract artifacts are published and front-end consumable |
| 2 | `lovable_ui_task_published` | Lovable UI task + prompt packet generated |
| 3 | `front_repo_receives_handoff_bundle` | Handoff bundle delivered to front-end repo checkout |

**Parent dependency**: `APP-002-W1-READ-DEPLOYMENT` — **`done`** ✅ (read surfaces + composed view implemented)

**Parent artifacts** (expected output):
| Artifact | Path | Purpose |
|---|---|---|
| Contract-ready signal | `.coordination/responses/` | Signals front-end that BFF is ready |
| F-042 example payload | `docs/examples/F-042-review-page.json` | Already exists (from parent dependency) |
| Delivery coordination | `docs/delivery-coordination-bus.md` | Already exists (coordination guide) |

---

## 2. Acceptance Checklist

The parent owner (Copilot) should verify each item below before handing off for review.

### 2.1 Contract-Ready Published

| # | Check | Expected state | Evidence path | Status |
|---|---|---|---|---|
| C1 | `.coordination/responses/contract-ready-F-042.yaml` exists | YAML with `type: contract-ready`, `feature_id: F-042`, references to BFF contract + example payload | `.coordination/responses/contract-ready-F-042.yaml` | ⬜ Pending |
| C2 | Contract references the live BFF endpoint | `GET /api/v1/operator/deployment-review/{plan_id}` documented as data source | `.coordination/responses/contract-ready-F-042.yaml` or response body | ⬜ Pending |
| C3 | Staleness/degradation model referenced | Contract mentions `meta.surfaces` status values (`ok`, `degraded`, `unavailable`) and frontend gating rules | Same as C1 or linked doc | ⬜ Pending |
| C4 | Design rules from F-042 BFF contract are included | CTA fields are backend-shaped; downstream failure surfaces via degradation metadata; UI must not derive promotion safety alone | `docs/bff/F-042-promotion-review.md` already exists — contract-ready should reference it | ⬜ Pending |

### 2.2 Lovable UI Task Published

| # | Check | Expected state | Evidence path | Status |
|---|---|---|---|---|
| L1 | `.coordination/responses/lovable-ui-task-F-042.yaml` exists | YAML with `type: lovable-ui-task`, `feature_id: F-042`, screen spec, constraints | `.coordination/responses/lovable-ui-task-F-042.yaml` | ⬜ Pending |
| L2 | Prompt packet generated | Markdown prompt includes: screen sections (from `docs/screens/F-042-promotion-review.md`), interaction rules, acceptance criteria, and constraints (use existing BFF client only, no raw fetch in components, no demo providers) | `.coordination/responses/lovable-prompt-F-042.md` or embedded in L2 YAML | ⬜ Pending |
| L3 | Screen spec references canonical source | Points to `docs/screens/F-042-promotion-review.md` for page sections, interaction rules, acceptance | L1 YAML or L2 prompt | ⬜ Pending |
| L4 | Lovable constraints are explicit | "Use existing BFF client only", "Do not add raw fetch in components", "Do not import demo providers", "CTA visibility from backend-shaped fields only" | L2 prompt packet | ⬜ Pending |

### 2.3 Front Repo Receives Handoff Bundle

| # | Check | Expected state | Evidence path | Status |
|---|---|---|---|---|
| F1 | Coordination files mirrorable to front repo | `.coordination/responses/` contains contract-ready + lovable-ui-task + prompt | `.coordination/responses/` directory | ⬜ Pending |
| F2 | Screen doc mirrorable | `docs/screens/F-042-promotion-review.md` referenced in handoff | Already exists in pantheon repo | ⬜ Pending |
| F3 | BFF contract + example mirrorable | `docs/bff/F-042-promotion-review.md` and `docs/examples/F-042-review-page.json` referenced | Already exist | ⬜ Pending |
| F4 | Delivery coordination bus documented | `docs/delivery-coordination-bus.md` explains the handoff flow, GitHub commands, worker routing | Already exists | ⬜ Pending |
| F5 | Handoff ready for GitHub dispatch | A human (or orchestrator) can `/dispatch front-ui F-042` or `/contract-ready F-042` on the coordination issue | Coordination files present | ⬜ Pending |

---

## 3. Dependency Map

### 3.1 Upstream Dependencies (must be done before parent can proceed)

| ID | Task | Status | What it provides |
|---|---|---|---|
| `APP-002-W0-REBASELINE` | Rebaseline APP-002 scope | done | Scope boundary and wave sequencing |
| `APP-002-W1-READ-DEPLOYMENT` | Promotion Review read surfaces | **done** ✅ | 5 read endpoints + composed F-042 view + ReadSurfaceStore + example payload + BFF contract |

**Verdict**: All upstream dependencies are satisfied. Parent task can begin immediately.

### 3.2 Downstream Consumers (tasks that need this parent to complete)

| ID | Task | Owner | Status | What it needs from this parent |
|---|---|---|---|---|
| `APP-002-W1-COMMAND-DEPLOYMENT` | Harden deployment command execution | Qwen | in_progress | Command path is independent; not blocked by this parent |
| `APP-002-W2-READ-INCIDENT` | Incident Response read surfaces | Qwen | todo | Independent wave; not blocked |
| `APP-002-W5-LOVABLE-CUTOVER` | Production cutover to Lovable | Copilot | todo | Needs validated handoff packets from all preceding waves, including this one |
| `front-ai-trading-system` (external repo) | Front-end implementation | front-ui-worker (Copilot) | N/A | Needs contract-ready + lovable-ui-task + prompt packet from this parent |

### 3.3 Related Sidecars and Support Artifacts

| Sidecar | Parent | Kind | Status | Relationship |
|---|---|---|---|---|
| `APP-002-W1-READ-DEPLOYMENT-SIDECAR-BFF-HANDOFF` | `APP-002-W1-READ-DEPLOYMENT` | `bff_handoff_packet` | done | **Input**: this acceptance packet consumes the BFF handoff to verify front-end readiness |
| `APP-002-IMPL-BFF-SIDECAR-BFF-HANDOFF` | `APP-002-IMPL-BFF` | `bff_handoff_packet` | blocked | Independent legacy lane; superseded by Wave 1 tasks |
| This sidecar (`ACCEPTANCE`) | `APP-002-W1-FRONT-HANDOFF` | `acceptance_packet` | done | **Output**: prepares acceptance checklist so parent owner can execute efficiently |

### 3.4 Coordination File Dependencies

| File | Purpose | Must exist before parent completes |
|---|---|---|
| `.coordination/responses/contract-ready-F-042.yaml` | Signals BFF contract readiness | ✅ Required for C1–C4 |
| `.coordination/responses/lovable-ui-task-F-042.yaml` | Triggers Lovable UI generation | ✅ Required for L1–L4 |
| `.coordination/responses/lovable-prompt-F-042.md` | Human-readable Lovable prompt | ✅ Required for L2, L4 |

---

## 4. Pre-Execution Readiness Assessment

| Dimension | Status | Notes |
|---|---|---|
| Upstream dependency | ✅ PASS | `APP-002-W1-READ-DEPLOYMENT` is `done`; BFF composed view live |
| BFF contract | ✅ PASS | `docs/bff/F-042-promotion-review.md` exists with required fields + design rules |
| Screen specification | ✅ PASS | `docs/screens/F-042-promotion-review.md` defines page sections, interaction rules, acceptance |
| Example payload | ✅ PASS | `docs/examples/F-042-review-page.json` provides valid F-042 response shape |
| Coordination bus | ✅ PASS | `docs/delivery-coordination-bus.md` documents handoff flow, GitHub commands, worker routing |
| Lovable constraints | ✅ PASS | Known constraints: "use existing BFF client only", "no raw fetch", "no demo providers" |
| Front repo checkout | ⚠️ WARNING | `front-ai-trading-system` is not present in this workspace; mirror will require sibling repo |
| Parent owner assignment | ✅ PASS | Copilot assigned; reviewer is Codex |

**Overall**: Parent task is **ready to start**. The only risk is the front-end repo checkout not being present — the parent owner should coordinate with the orchestrator to ensure the sibling repo exists before attempting file mirroring.

---

## 5. Execution Guidance for Parent Owner (Copilot)

### Step-by-step

1. **Create `contract-ready` coordination response**
   - Write `.coordination/responses/contract-ready-F-042.yaml`
   - Include: `type: contract-ready`, `feature_id: F-042`, BFF endpoint reference, links to `docs/bff/F-042-promotion-review.md` and `docs/examples/F-042-review-page.json`

2. **Create `lovable-ui-task` coordination response**
   - Write `.coordination/responses/lovable-ui-task-F-042.yaml`
   - Include: `type: lovable-ui-task`, `feature_id: F-042`, screen spec reference to `docs/screens/F-042-promotion-review.md`

3. **Generate Lovable prompt packet**
   - Write `.coordination/responses/lovable-prompt-F-042.md`
   - Include: page sections (header, review summary, allowed actions, evidence, states), interaction rules, acceptance criteria, and constraints (BFF client only, no raw fetch, no demo providers, CTA from backend)

4. **Verify mirroring paths**
   - Confirm the delivery coordination bus paths are valid
   - If `front-ai-trading-system` checkout exists, mirror the coordination files there
   - If not, document the gap as a `bff-gap` or `front-sync` note

5. **Hand off to reviewer (Codex)**
   - Update task status to `review`
   - Reference this acceptance packet as supporting material

### Template: contract-ready-F-042.yaml

```yaml
type: contract-ready
feature_id: F-042
feature_name: "Promotion Review"
created_at: "<RFC3339 timestamp>"
created_by: Copilot
parent_task: APP-002-W1-FRONT-HANDOFF
bff_endpoint: "GET /api/v1/operator/deployment-review/{plan_id}"
artifacts:
  bff_contract: docs/bff/F-042-promotion-review.md
  screen_spec: docs/screens/F-042-promotion-review.md
  example_payload: docs/examples/F-042-review-page.json
  delivery_bus: docs/delivery-coordination-bus.md
design_rules:
  - "CTA-facing fields must be backend-shaped"
  - "Downstream failure surfaces through degradation metadata"
  - "UI must not derive promotion safety by itself"
notes: "All Wave 1 read surfaces implemented. Composed view returns meta.surfaces with per-surface status. See BFF_API_CONTRACT.md for full endpoint inventory."
```

### Template: lovable-ui-task-F-042.yaml

```yaml
type: lovable-ui-task
feature_id: F-042
feature_name: "Promotion Review"
created_at: "<RFC3339 timestamp>"
created_by: Copilot
parent_task: APP-002-W1-FRONT-HANDOFF
screen_spec: docs/screens/F-042-promotion-review.md
prompt_packet: .coordination/responses/lovable-prompt-F-042.md
data_source:
  endpoint: "GET /api/v1/operator/deployment-review/{plan_id}"
  example: docs/examples/F-042-review-page.json
constraints:
  - "Use existing BFF client only"
  - "Do not add raw fetch in components"
  - "Do not import demo providers"
  - "CTA visibility from backend-shaped fields only"
  - "Loading, empty, degraded, and error states must be explicit"
acceptance:
  - "Page renders with no mock data"
  - "Promote to Paper CTA visibility is backend-driven only"
  - "Loading, empty, degraded, and error states are explicit and visually distinct"
```

### Template: lovable-prompt-F-042.md

```markdown
# Lovable Prompt: F-042 Promotion Review

## Screen: Promotion Review Page

Build the F-042 Promotion Review screen per the spec at `docs/screens/F-042-promotion-review.md`.

### Page Sections
1. Header with feature title, artifact identity, target stage, and readiness badge
2. Review summary with governance outcome, risk summary, and last run progress
3. Allowed actions panel — CTA visibility from backend `allowedActions` only
4. Supporting evidence with example payload and trace references
5. Loading, empty, degraded, and error states — no mock fallback

### Data Source
- All data from `GET /api/v1/operator/deployment-review/{plan_id}`
- Example payload: `docs/examples/F-042-review-page.json`
- Do NOT construct mock providers or hardcode sample data

### Constraints
- Use the existing BFF client — do not add raw fetch/axios in components
- Do not import demo providers or test fixtures into production code
- `canPromoteToPaper` CTA enabled/disabled state comes ONLY from backend `allowedActions`
- Degradation: if `meta.surfaces.*.status` is not `ok`, show warning banner
- Error states must be visually distinct from loading/empty states

### Acceptance
- Page renders with real Pantheon data only (no mocks)
- "Promote to Paper" CTA is backend-driven
- All states (loading, empty, degraded, error) are explicit and visually distinct
```

---

## 6. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Front repo (`front-ai-trading-system`) not present | Medium | Document as coordination gap; parent owner should trigger orchestrator to bootstrap mirror path |
| BFF endpoint shape changes before front-end integration | Low | Composed view contract is locked; any change requires a new contract version + updated example payload |
| Lovable generates UI that bypasses BFF client | Medium | Prompt packet explicitly constrains to existing BFF client; reviewer (Codex) should verify no raw fetch |
| Staleness metadata not handled by front-end | Low | Contract-ready references degradation model; prompt packet includes staleness handling constraints |

---

## 7. Reviewer Checklist (Codex)

When reviewing the parent task completion, verify:

| Check | Status | Evidence |
|---|---|---|
| Support artifact only — no canonical truth modified | ✅ PASS (this file) | Only `support/sidecars/APP-002-W1-FRONT-HANDOFF/APP-002-W1-FRONT-HANDOFF-SIDECAR-ACCEPTANCE.md` created |
| Parent dependency satisfied | ✅ PASS | `APP-002-W1-READ-DEPLOYMENT` is `done` |
| Contract-ready coordination file references live BFF | ⬜ Pending parent execution | Verify `.coordination/responses/contract-ready-F-042.yaml` references composed view endpoint |
| Lovable prompt includes all constraints | ⬜ Pending parent execution | Verify no raw fetch, BFF client only, no demo providers |
| Handoff bundle is mirrorable to front repo | ⬜ Pending parent execution | Verify coordination files exist in `.coordination/responses/` |
| All 3 parent acceptance criteria met | ⬜ Pending parent execution | `contract_ready_published`, `lovable_ui_task_published`, `front_repo_receives_handoff_bundle` |

---

## 8. Handoff To Reviewer (Codex)

Codex, this acceptance packet provides:

1. **A structured checklist** (Section 2) — 12 checks across 3 acceptance criteria, ready for the parent owner to fill in
2. **A dependency map** (Section 3) — upstream satisfied, downstream identified, related sidecars catalogued
3. **Execution templates** (Section 5) — ready-to-use YAML/Markdown templates the parent owner can copy directly
4. **Risk assessment** (Section 6) — front repo absence is the primary risk; all others are low

**Recommended next step**:
- Parent owner (Copilot) executes the 5 steps in Section 5
- Parent owner updates task status to `review`
- You (Codex) review the produced coordination files against this checklist
- If all 12 checks pass, mark parent task as `review_approved` → `done`

---

*Generated by Codex as a sidecar `acceptance_packet` helper for APP-002-W1-FRONT-HANDOFF. This file is a support artifact and does not modify canonical truth.*
