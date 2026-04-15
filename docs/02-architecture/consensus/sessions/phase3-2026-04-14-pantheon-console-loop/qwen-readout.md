# Qwen Readout

## Lane

- Agent: Qwen
- Capability focus: Audit canonical object boundaries, BFF and contract truth, and screen-to-contract alignment for APP-002 slices and workbench backlog claims.

## Canonical Sources Read

- L0: `ai-status.json`, `AI_COLLABORATION_GUIDE.md`, `ai-activity-log.jsonl`
- L1: `OPERATOR_ACCEPTANCE_MATRIX.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
- L2: `docs/delivery-coordination-bus.md`, `docs/orchestrator-state-plane-redesign.md`, `Pantheon_Blueprint_Gap_Review_v1.md`, `CANONICAL_CONTRACT_MIGRATION_DECISION.md`
- L3: `Pantheon_總索引版系統分析文件.md`, `Pantheon_API_Service_Contract_設計版.md`
- Coordination: `.coordination/README.md`, all APP-002 sidecar handoff packets (APP-002 base, W2-READ-INCIDENT, W2-CONTROL-INCIDENT, W3-POSTINCIDENT-EVOLUTION, W4-PERSONA-MGMT, W4-REMAINING-CATALOG, W5-SSE-LIVE)
- Session: `planning-session.json`, `starter-draft.md`, `consensus-packet.md`

## Working Interpretation

### Architecture Summary

Pantheon Console is not a single admin page — it is an 8-workbench surface: Operator Console, Persona Workbench, Research Workbench, Knowledge Workbench, Trainer Workbench, Consultation Workbench, Governance Workbench, and Evolution Workbench (總索引版系統分析文件 §4.1, §9.3.1).

The BFF plane is the **sole aggregated frontend entry point** (L1 `OPERATOR_ACCEPTANCE_MATRIX.md` §3 surface `S-BFF` typed as `composed`). All operator-facing composed views route through the BFF, which itself is **not** the authoritative executor — it is a façade over internal services.

The `.coordination` bus already supports `bff-gap`, `ui-done`, `contract-ready`, `lovable-ui-task`, and `dispatch-request` payload types (`.coordination/README.md`, `docs/delivery-coordination-bus.md`). The closed-loop gap is that only `F-042` has a true Lovable-ready packet; the rest of APP-002 remains sidecar handoff truth rather than canonical screen packets.

### Delivery Order

The starter draft's proposed wave order is sound:

1. **Closed-loop infra** (LOOP-001 to LOOP-003): protocol extension, GitHub dispatch, front-repo bootstrap — must land first because no screen packet is actionable without the loop mechanics.
2. **Packetize APP-002-backed screens** (PKT-001 to PKT-005): all five packet groups already have BFF read surfaces and sidecar handoff packets. The missing piece is formalizing them into canonical screen-packet requirements.
3. **8-workbench backlog** (WB-001 to WB-008): expand beyond APP-002 into Research, Knowledge, Trainer, Consultation workbenches that currently have no Pantheon-side packet spec.
4. **Materialize after human gate**: as the orchestrator redesign mandates (§4.1 Planning Plane → §4.2 Execution Plane), planning drafts must not mutate `ai-status.json` until the human acceptance gate is passed.

### Ownership Boundaries

From the sidecar inventory, the following BFF surfaces are **already implemented and code-verified**:

| Surface Group | BFF Endpoints | Sidecar Status | Gaps Remaining |
|---|---|---|---|
| Incident Response Read (W2) | `GET /api/v1/operator/incident-response/{id}`, IN-01–05, RT-04, IN-05 | Done, approved | RT-03, TL-02, EV-04 endpoints missing (§3 of W2-READ-INCIDENT sidecar) |
| Incident Control (W2) | `POST /api/v1/operator/commands`, degraded guidance | Done, approved | Rollback action type not forwarded to internal API |
| Post-Incident/Evolution (W3) | `GET /api/v1/operator/post-incident-review/{id}`, EV-01–04, LN-01–03, TL-01–03 | Done, approved | TL-01/02/03 filtering partial; LN-03 root_type no-op |
| Persona Management (W4) | `GET /api/v1/operator/persona-management/{id}` | Done, approved | `snapshot` param accepted but not enforced; standalone PS surfaces deferred |
| Remaining Catalog (W4) | PS-01–06, CP-01/03/04, DP-01–04, RT-01–04, TL-01–03, LN-01–03, IN-01–05, EV-01–04 | Done | Mostly catalog coverage; no new composed views |
| SSE Live (W5) | 3 SSE streams + frontend reconciler | Done, approved | Buffers in-memory; no server-side runtime filtering on runtime stream |

This means PKT-001 through PKT-005 have **concrete BFF anchors** — they are not blueprint-only claims. The workbench backlog (WB-001 to WB-008) must distinguish between:
- workbenches with existing BFF backing (Operator, Persona) — packetization is primarily a front-end/Lovable task
- workbenches without BFF backing (Research, Knowledge, Trainer, Consultation) — require new BFF surfaces before packetization is meaningful

## Risks / Contradictions

### Risk 1: `backend-delivery` version-lock fields before SDK exists

The starter draft raises whether `contracts_version` and `sdk_version` should remain mandatory in `backend-delivery` when the front repo uses direct BFF client wiring.

**Assessment**: These fields should be **optional until a published SDK exists**, but the `contract_ref` field (pointing to the BFF contract doc + example payload) must remain mandatory. Requiring a non-existent SDK version creates a permanent validation gap. This aligns with the `.coordination` philosophy that the bus should be contract-driven, not version-locked.

### Risk 2: GitHub dispatch vs. legacy issue bus primacy

Whether `repository_dispatch` should be the primary trigger immediately or only after the legacy GitHub issue bus labels are stabilized.

**Assessment**: The legacy issue bus (`docs/delivery-coordination-bus.md` §3–5) is already scaffolded in the repo with `.coordination/` mirroring. Dispatch should be the **primary path from day one**, with label bootstrap as a **compatibility shim for the old bus**, not a hard prerequisite. The `workflow_dispatch` replay workflow already covers manual recovery, so label stability is a nice-to-have for audit trail, not a hard gate.

### Risk 3: Non-APP-002 workbench packetization depth

The starter draft is vague about how much of Operator Home, Research, Knowledge, Trainer, Consultation, Governance, and Evolution should be packetized before backend gaps are closed.

**Assessment**: The following workbenches have **no BFF backing today** and should be listed in the backlog but marked as `backend_required` before packetization:

| Workbench | BFF Surfaces | Status | Packetization Readiness |
|---|---|---|---|
| Operator Console (WB-001) | Deployment Review, Incident Response, Post-Incident — all exist | Has BFF backing | **Ready** for PKT-001/002/003 |
| Persona Workbench (WB-002) | Persona Management composed view — exists | Has BFF backing | **Ready** for PKT-004 |
| Research Workbench (WB-003) | No BFF surfaces | Blueprint only (§10 of 總索引) | **Not ready** — needs BFF first |
| Knowledge Workbench (WB-004) | Registry read surfaces exist in catalog (EV/LN/KNO endpoints) | Partial — no composed views | **Partially ready** — list/detail endpoints exist, composed views missing |
| Trainer Workbench (WB-005) | Teaching session read via Persona Management | Partial — teaching session is a sub-surface | **Partially ready** — read-only teaching history exists, write-side trainer flow needs BFF |
| Consultation Workbench (WB-006) | No BFF surfaces | Blueprint only (§9.3.5 of 總索引) | **Not ready** — needs BFF first |
| Governance Workbench (WB-007) | Deployment Review composed view — exists | Has BFF backing | **Ready** for PKT-001 |
| Evolution Workbench (WB-008) | Post-Incident Review composed view — exists | Has BFF backing (EVO-004 blocks execution boundary) | **Ready for read-only packet**, execution actions deferred to EVO-004 |

This means WB-003 (Research) and WB-006 (Consultation) should be listed in the backlog but explicitly marked as `blocked_on_bff`. WB-004 (Knowledge) and WB-005 (Trainer) are `partially_ready`.

### Risk 4: EVO-004 dependency on Evolution execution boundary

The W3-POSTINCIDENT-EVOLUTION sidecar (§2 Parent Readiness) and the APP-002 base sidecar (§2 Parent Readiness) both call out that `EVO-004` operational evolution boundaries are `todo` and block final operator command semantics for freeze/rollback/retrain/redeploy.

**Assessment**: PKT-003 (Post-Incident/Evolution screens) should explicitly split **read-only evidence panels** (postmortem, lineage, telemetry, evolution decision list) from **actionable mutation review panels** (approve/execute evolution decisions). The read-only half is packet-ready; the mutation half should record EVO-004 as a hard dependency. This is already captured in the starter draft's PKT-003 acceptance criteria, and I agree with it.

### Risk 5: `.coordination` must not introduce a second source of truth

The README states: "`.coordination` remains the canonical machine protocol; this session must not introduce `.ai-loop` as a second source of truth."

**Assessment**: This is correct and must be preserved. The `planning-session.json` is the machine-readable source of truth for planning state; `.orchestrator/planning-state.json` is the derived dashboard state. The closed-loop spec must route through `.coordination/requests/` and `.coordination/responses/` as the only handoff surface, not through any parallel `.ai-loop/` directory.

## Suggested Task Slices

### Slice 1: LOOP-001 — `.coordination` Protocol Extension

**Agree with starter draft**. The protocol must add:
- `frontend-feedback` payload type: captures Lovable output, BFF client wiring changes, and screen-spec gap reports
- `backend-delivery` payload type: captures BFF contract readiness, endpoint URLs, example payloads, and required feedback fields
- Backward compatibility for existing `contract-ready`, `lovable-ui-task`, `bff-gap`, `ui-done`
- Explicit `workbench`, `screen_id`, `ui_spec_path`, `frontend_change_spec_path`, `required_feedback`, and `delivery_dependencies` fields in `lovable-ui-task`

### Slice 2: LOOP-002 — GitHub Dispatch Workflows

**Agree with starter draft, with one clarification**: `repository_dispatch` is the primary path; `workflow_dispatch` is the replay/recovery path. Label bootstrap is a compatibility shim, not a hard gate. The dispatch event name family should be:
- `pantheon.contract_ready`
- `pantheon.lovable_task`
- `pantheon.frontend_feedback`
- `pantheon.backend_delivery`
- `pantheon.ui_done`
- `pantheon.bff_gap`

### Slice 3: LOOP-003 — Front-Repo Bootstrap

**Agree with starter draft**. Must record `../front-ai-trading-system` as a hard prerequisite, define `pantheon-bus` and `coordination-bus` label bootstrap, and create a mirror validation checklist.

### Slice 4: PKT-001 through PKT-005 — Screen Packet Families

**Agree with all five packet groups**, with the following clarifications:

- **PKT-001 (Governance/Deployment Review)**: Must capture the `S-BFF` composed nature — BFF is a façade, not the authoritative writer. Approval actions target `ApprovalDecision` via `promotion-review-svc` (OPERATOR_ACCEPTANCE_MATRIX §4.4).
- **PKT-002 (Incident Response/Control)**: Must include degraded-state button gating from the W2 sidecars and the "never show empty-success when data is unreachable" rule (APP-002-FRONTEND-STATE-MATRIX §3.2.1).
- **PKT-003 (Post-Incident/Evolution)**: Must explicitly split read-only evidence panels from mutation review panels. EVO-004 dependency is a hard gate for execution actions.
- **PKT-004 (Persona Management/Catalog)**: Must capture the remaining catalog endpoints as drilldown modules, not a catch-all. The `allowedActions` payload now exists (§4.3 of W4-PERSONA-MGMT sidecar).
- **PKT-005 (SSE/Degradation Banner)**: Must define the global degradation banner as a cross-cutting concern referenced by all packets, not a per-screen duplication. SSE reconciliation must be described as a frontend integration slice, not pure visual work.

### Slice 5: WB-001 through WB-008 — 8-Workbench Backlog

**Agree with the 8-workbench structure**, with the readiness classifications from Risk 3 above:

| Workbench | Packet Readiness | BFF Status | Recommended Wave |
|---|---|---|---|
| WB-001 Operator Console | Ready | Has BFF | Wave 1 (after LOOP-001/003) |
| WB-002 Persona Workbench | Ready | Has BFF | Wave 1 |
| WB-003 Research Workbench | Blocked on BFF | Blueprint only | Wave 3+ |
| WB-004 Knowledge Workbench | Partial | List/detail exist, no composed views | Wave 2 |
| WB-005 Trainer Workbench | Partial | Teaching history read exists | Wave 2 |
| WB-006 Consultation Workbench | Blocked on BFF | Blueprint only | Wave 3+ |
| WB-007 Governance Workbench | Ready | Deployment Review exists | Wave 1 |
| WB-008 Evolution Workbench | Partial (read-only ready) | Post-Incident exists, EVO-004 blocks actions | Wave 1 (read-only), Wave 2 (actions) |

## Citations

- [總索引版系統分析文件 §4.1] 8-workbench architecture diagram
- [總索引版系統分析文件 §9.3.1] Pantheon Console Plane module inventory
- [OPERATOR_ACCEPTANCE_MATRIX.md §3] Five operator surface paths (S-BFF, S-IAPI, S-CLI, S-EMRG, S-SUPP)
- [OPERATOR_ACCEPTANCE_MATRIX.md §4.1–4.7] Per-operation canonical objects and surface types
- [docs/delivery-coordination-bus.md §3–6] `.coordination` payload types, Lovable lane flow, GitHub commands
- [.coordination/README.md] Machine-readable handoff surface rules and payload types
- [docs/orchestrator-state-plane-redesign.md §4.1–4.5] Plane separation: planning, execution, runtime, evidence, narrative
- [APP-002-SIDECAR-BFF-HANDOFF.md §3–5] Operator journeys, frontend handoff materials, screen modules
- [APP-002-FRONTEND-STATE-MATRIX.md §2–3] Five data states and per-screen button gating rules
- [APP-002-W2-READ-INCIDENT §3] RT-03, TL-02, EV-04 endpoint gaps
- [APP-002-W2-CONTROL-INCIDENT §2–4] Command surfaces, operator journey, frontend handoff
- [APP-002-W3-POSTINCIDENT-EVOLUTION §2–4] Wave 3 surfaces, post-incident composed view, UI gating
- [APP-002-W4-PERSONA-MGMT §2–4] Persona Management composed view, allowedActions payload
- [APP-002-W4-REMAINING-CATALOG §2] Full catalog surface inventory (PS/CP/DP/RT/TL/LN/IN/EV)
- [APP-002-W5-SSE-LIVE §2–3] SSE endpoints, replay semantics, frontend reconciliation
- [planning-session.json §lane_focus.Qwen] "Audit canonical object boundaries, BFF and contract truth, and screen-to-contract alignment for APP-002 slices and workbench backlog claims."
- [starter-draft.md §Open disagreements] Three open questions on version-lock, dispatch primacy, and non-APP-002 packetization depth
- [consensus-packet.md §Open Questions] Three human-gate questions matching the starter draft
