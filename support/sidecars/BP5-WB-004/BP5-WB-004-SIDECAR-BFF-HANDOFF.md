# BP5-WB-004 Evolution Workbench BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `BP5-WB-004` - Packetize Evolution Workbench follow-on surfaces
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `review`
**Sidecar Task**: `BP5-WB-004-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-16`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime, registry, governance, or control-plane
> implementations. It packages the current `EW-04` and `EW-05` BFF/frontend
> reality into a parent-owner-ready handoff packet.

---

## 1. Parent Task Summary

`BP5-WB-004` is the Evolution Workbench follow-on packetization slice for the
two blocked modules after the existing `PKT-003` baseline.

From live `ai-status.json`, the parent acceptance is still:

1. evolution follow-on screens cite canonical evolution, incident, and rollback objects
2. packet language exists for inspiration and mutation review instead of backlog-only placeholders

From the phase-3 workbench backlog and the current `EW-004` packet family:

- `EW-01 Post-Incident Review` - ready baseline via `PKT-003`
- `EW-02 Evolution Center` - ready baseline via `PKT-003`
- `EW-03 Lineage View` - ready baseline via `PKT-003`
- `EW-04 Inspiration Graph` - blocked on a dedicated BFF inspiration route and frontend handoff bundle
- `EW-05 Mutation Review` - blocked on a dedicated operator mutation-review projection, mutation-specific command vocabulary, backend-shaped `allowedActions`, and degradation wiring

This sidecar is deliberately narrow: it does **not** create new packet truth. It
only records which inputs are already live, which blocked drafts are reusable,
and which missing BFF/read-model pieces still make `EW-04` and `EW-05`
non-handoffable.

---

## 2. Source References

| Document or file | Why it matters |
|---|---|
| `ai-status.json` | Live source for `BP5-WB-004` and this sidecar task |
| `.orchestrator/task-briefs/bp5_wb_004_sidecar_bff_handoff.md` | Task-scoped sidecar instructions and artifact path |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Canonical Evolution Workbench module inventory, readiness, and dependency order |
| `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md` | Mainline packet family that already captures `EW-04` and `EW-05` at the canonical handoff level |
| `docs/bff/PKT-003-inspiration-graph.md` | Blocked draft contract for the missing inspiration route |
| `docs/examples/PKT-003-inspiration-graph.json` | Draft example payload vocabulary for `EW-04` |
| `services/control-plane/bff/main.py` | Live BFF route inventory and proof that the dedicated `EW-04` / `EW-05` routes do not exist |
| `services/control-plane/bff/models.py` | Current operator command enum; proves the absence of `ApproveMutation` / `RejectMutation` |
| `services/control-plane/bff/command_executor.py` | Current evolution command wiring; shows what is already governance-owned and what is still not a mutation-review surface |
| `services/control-plane/bff/read_store.py` | Seed data and current projection limits for evolution and lineage objects |
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Canonical evolution review and execution semantics already settled at L1 |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Canonical rollback authority and follow-through semantics referenced by `EW-05` |
| `services/control-plane/governance/evolution_decision.contract.md` | Canonical `EvolutionDecision` object anchor |
| `services/control-plane/governance/contract.md` | Canonical `ApprovalDecision` lifecycle and approval ownership |
| `services/incident/contract.md` | Canonical incident and postmortem backbone reused by mutation review |

---

## 3. Current Evolution Baseline

### 3.1 Reusable packet baseline

| Module | Packet or handoff state | Current repo reality | Use as |
|---|---|---|---|
| `EW-01 Post-Incident Review` | ready via `PKT-003` plus frontend handoff bundle | `GET /api/v1/operator/post-incident-review/{incident_id}` is live in `main.py` | evidence baseline for incident and postmortem context |
| `EW-02 Evolution Center` | ready via `PKT-003` plus frontend handoff bundle | `GET /api/v1/evolution-decisions`, `GET /api/v1/evolution-decisions/{decision_id}`, `GET /api/v1/freeze-orders`, and `GET /api/v1/rollbacks` are live | stable `decision_id`, `risk_level`, rollback history, and freeze-order context |
| `EW-03 Lineage View` | ready via `PKT-003` plus frontend handoff bundle | `GET /api/v1/lineage`, `GET /api/v1/lineage/edges/{edge_id}`, and `GET /api/v1/lineage/graph` are live | raw lineage primitives and graph context |
| `EW-04 Inspiration Graph` | blocked draft contract and example exist | no live route or frontend handoff bundle exists | blocked draft vocabulary only |
| `EW-05 Mutation Review` | no dedicated screen, contract, example, or handoff bundle | only generic evolution reads and generic evolution commands exist | blocked module only |

### 3.2 Live BFF and read-model inputs already available

| Surface | Route | Status in repo | Why it matters downstream |
|---|---|---|---|
| Evolution decision list | `GET /api/v1/evolution-decisions` | implemented in `main.py` | provides `decision_id`, `action_type`, `risk_level`, and `status` |
| Evolution decision detail | `GET /api/v1/evolution-decisions/{decision_id}` | implemented | single-decision evidence anchor for `EW-05` |
| Freeze-order list | `GET /api/v1/freeze-orders` | implemented | review context for quarantine/freeze follow-through |
| Global rollback list | `GET /api/v1/rollbacks` | implemented | rollback evidence and history |
| Post-incident review composed view | `GET /api/v1/operator/post-incident-review/{incident_id}` | implemented | incident and postmortem context reused by `EW-05` |
| Lineage edge list | `GET /api/v1/lineage` | implemented | base lineage primitives for `EW-03` |
| Lineage edge detail | `GET /api/v1/lineage/edges/{edge_id}` | implemented | detail drawer vocabulary reused conceptually by `EW-04` |
| Lineage graph | `GET /api/v1/lineage/graph` | implemented | graph baseline, but not a substitute for inspiration graph |
| Generic operator write path | `POST /api/v1/operator/commands` | implemented | current write path for governance-owned actions |

### 3.3 Repo-backed constraints the parent task should preserve

1. `EW-04` already has a blocked draft contract, but the live BFF only exposes raw lineage routes.
2. `EW-05` can cite settled L1 policy and existing evolution reads, but it still lacks a truthful operator-facing mutation-review projection.
3. The existing evolution command path is not a substitute for mutation-review CTA gating because it has no `allowedActions.canApproveMutation` or `canRejectMutation` truth source.
4. Both blocked modules still require module-specific `meta.surfaces.*` wiring. The generic `meta.staleness` returned by current evolution and lineage routes is not enough for the packet family rules in `EW-004`.

---

## 4. BFF Query Gap Matrix for `EW-04` and `EW-05`

| Module | Reusable inputs available now | Missing BFF or read-model gap | Frontend handoff status |
|---|---|---|---|
| `EW-04 Inspiration Graph` | `GET /api/v1/lineage`; `GET /api/v1/lineage/edges/{edge_id}`; `GET /api/v1/lineage/graph`; blocked draft contract in `docs/bff/PKT-003-inspiration-graph.md` | no `GET /api/v1/lineage/inspiration/{artifact_id}` route; no BFF-composed `inspiration_edges[]`; no `strategy_tags[]`; no backend-owned `influence_weight`; no `meta.surfaces.inspiration`; no frontend handoff bundle | **not ready** - draft vocabulary exists, but runtime backing and frontend bundle are both missing |
| `EW-05 Mutation Review` | `GET /api/v1/evolution-decisions`; `GET /api/v1/evolution-decisions/{decision_id}`; `GET /api/v1/freeze-orders`; `GET /api/v1/rollbacks`; `GET /api/v1/operator/post-incident-review/{incident_id}`; settled L1 policy for evolution and rollback | no `GET /api/v1/operator/mutation-review/{decision_id}` route; no composed mutation-review object; no `ApproveMutation` / `RejectMutation` command vocabulary; no `allowedActions.canApproveMutation` / `canRejectMutation`; no `meta.surfaces.mutation_review`; no screen or handoff artifacts | **not ready** - there is still no honest frontend opening for this surface |

### 4.1 `EW-04` code-backed distinctions

| Observation | Evidence | Why it blocks honest handoff |
|---|---|---|
| Current lineage edges only carry primitive graph data | `read_store.py` seed edges expose `from_artifact_id`, `to_artifact_id`, `relationship`, and `created_at` only | the frontend cannot derive `strategy_tags[]` or `influence_weight` truthfully |
| `get_lineage_graph()` is intentionally shallow in v1 | `read_store.py` documents depth as effectively `depth=1` semantics and treats `root_type` as a no-op | even the current graph endpoint is not semantically equivalent to the planned inspiration graph |
| No per-surface inspiration degradation signal exists | current lineage routes in `main.py` return `meta.staleness`, not `meta.surfaces.inspiration` | the `PKT-005` degradation banner cannot be wired correctly for `EW-04` yet |
| Draft contract already forbids client-side graph synthesis | `docs/bff/PKT-003-inspiration-graph.md` explicitly says the route must not be replaced by raw lineage traversal | parent task should absorb the draft, not let frontend improvise around the missing route |

### 4.2 `EW-05` code-backed distinctions

| Observation | Evidence | Why it blocks honest handoff |
|---|---|---|
| Existing command enum contains only `ApproveEvolutionDecision` and `ExecuteEvolutionAction` for evolution work | `services/control-plane/bff/models.py` has no `ApproveMutation` or `RejectMutation` values | the packet family's approve/reject CTA language has no runtime command surface behind it |
| Current evolution command validators are generic governance validators | `main.py` validates `approval_action` and `action_type` for generic evolution commands, not a mutation-review page contract | frontend CTA visibility would still be inferred from role and params instead of backend-shaped review authority |
| Seed `EvolutionDecision` projection is intentionally thin | `read_store.py` projects only `decision_id`, `action_type`, `risk_level`, `decision_state`, `linked_incident_id`, `artifact_id`, `created_at`, and `execution_result` | the response is missing `proposed_changes`, `risk_assessment`, `required_approvals`, rollback follow-through refs, and any mutation-review `allowedActions` |
| Current evolution commands already dispatch to governance-owned APIs | `command_executor.py` posts to `/api/evolution/proposals/{decision_id}/{approval_action}` and `/api/evolution/proposals/{decision_id}/execute` | this is useful upstream evidence, but it is not yet the operator-facing mutation-review read model required by `EW-05` |

---

## 5. Operator Journey and Frontend Handoff Notes

### 5.1 What frontend consumers can safely use right now

| Surface | What exists now | Safe consumption rule |
|---|---|---|
| `EW-02 Evolution Center` | live read routes plus published handoff | safe to use now as the mutation context baseline |
| `EW-03 Lineage View` | live read routes plus published handoff | safe to use now as raw lineage evidence only |
| `EW-04 Inspiration Graph` | blocked draft contract only | do not open a frontend task; do not synthesize the graph from `lineage` or `lineage/graph` |
| `EW-05 Mutation Review` | no page contract and no frontend bundle | do not expose approve/reject CTAs or a mutation-review screen |

### 5.2 Current operator journeys implied by repo reality

**Read-only evolution review journey available today**

1. Load `GET /api/v1/evolution-decisions` to browse decisions.
2. Open `GET /api/v1/evolution-decisions/{decision_id}` for decision detail.
3. Read `GET /api/v1/freeze-orders` and `GET /api/v1/rollbacks` for freeze and rollback history.
4. Open `GET /api/v1/operator/post-incident-review/{incident_id}` when the decision is incident-backed.
5. Open `GET /api/v1/lineage?artifact_id={artifact_id}` or `GET /api/v1/lineage/graph?root_id={artifact_id}` for raw lineage context.

**Action journey available today, but not as `EW-05`**

1. Submit `POST /api/v1/operator/commands` with `ApproveEvolutionDecision` when governance review needs an approve or reject action.
2. Submit `POST /api/v1/operator/commands` with `ExecuteEvolutionAction` when downstream execution is explicitly requested.
3. Treat both as governance-owned command surfaces, not as a packetized mutation-review page with backend-shaped CTA gating.

### 5.3 Forward journey the parent task should preserve

**Future `EW-04` journey**

1. Enter `artifact_id`.
2. Load `GET /api/v1/lineage/inspiration/{artifact_id}`.
3. Render graph, edge drawer, strategy tags, `meta.snapshot_at`, and `meta.surfaces.inspiration`.
4. Suppress graph rendering entirely when inspiration surface health is unavailable.

**Future `EW-05` journey**

1. Enter from `EW-02` with a stable `decision_id`.
2. Load `GET /api/v1/operator/mutation-review/{decision_id}`.
3. Render decision context, proposed changes, incident and postmortem evidence, rollback follow-through refs, risk assessment, required approvals, and backend-shaped `allowedActions`.
4. Show `ApproveMutation` and `RejectMutation` CTAs only when both authority and degradation checks pass.
5. Submit those actions through `POST /api/v1/operator/commands` only after the new command vocabulary exists.

### 5.4 Frontend rules that are safe to state now

1. Never compute inspiration graph structure client-side from `GET /api/v1/lineage` or `GET /api/v1/lineage/graph`.
2. Never derive mutation CTA visibility from `risk_level`, `decision_state`, or actor role alone.
3. Treat current evolution reads as evidence inputs only; they are not a substitute for the future `EW-05` composed object.
4. Keep `EW-04` and `EW-05` out of Lovable or implementation dispatch until their dedicated routes, `allowedActions`, and `meta.surfaces.*` signals exist.

---

## 6. Seed IDs and Reviewer Smoke Anchors

These IDs come from `services/control-plane/bff/read_store.py` and are useful for
reviewer spot-checking or future UI smoke examples.

| Object | ID | Notes |
|---|---|---|
| Artifact under review | `artifact-042` | central artifact referenced by evolution, incidents, telemetry, and lineage |
| Upstream lineage artifact | `artifact-041` | source edge for `ln-edge-001` |
| Downstream lineage artifact | `artifact-043` | target edge for `ln-edge-002` |
| Evolution decision | `evo-dec-001` | `action_type: retrain`, `risk_level: medium`, `status: approved`, linked to `inc-20260410-001` |
| Open incident | `inc-20260410-001` | drawdown incident that drives the freeze and rollback evidence path |
| Published postmortem | `pm-20260409-002` | prior incident postmortem with `artifact-042` context |
| Freeze order | `fo-001` | active persona-scoped freeze tied to `inc-20260410-001` |
| Rollback record | `rb-001` | completed rollback from `v2.1.0` to `v2.0.0` for `runtime-042` |
| Lineage edge | `ln-edge-001` | `artifact-041 -> artifact-042`, relationship `derived_from` |
| Lineage edge | `ln-edge-002` | `artifact-042 -> artifact-043`, relationship `promoted_to` |

### Smoke-note caveats

1. `evo-dec-001` is enough to prove the current evolution list/detail routes, but not enough to populate `EW-05` fields like `proposed_changes` or `required_approvals`.
2. The lineage seed proves only primitive edges. It does not include `strategy_tags[]` or `influence_weight`.
3. These seed objects are good reviewer anchors, not proof that the blocked modules are frontend-ready.

---

## 7. Existing and Missing Handoff Materials

| Material type | Existing now | Missing now |
|---|---|---|
| Mainline packet family | `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md` | none at the family level |
| Ready baseline handoffs | `docs/pantheon-handoffs/PKT-003-post-incident-review/`, `docs/pantheon-handoffs/PKT-003-evolution-center/`, `docs/pantheon-handoffs/PKT-003-lineage-view/` | no additional ready baseline needed |
| Inspiration draft assets | `docs/screens/PKT-003-inspiration-graph.md`, `docs/bff/PKT-003-inspiration-graph.md`, `docs/examples/PKT-003-inspiration-graph.json` | `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md` is still missing |
| Mutation-review assets | none found under `docs/screens/`, `docs/bff/`, `docs/examples/`, or `docs/pantheon-handoffs/` | full screen spec, BFF contract, example payload, and frontend handoff are all missing |

---

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | only this sidecar file is added under `support/sidecars/BP5-WB-004/` |
| No canonical truth edited | PASS | references existing packet family, backlog, policy docs, and BFF runtime files only |
| `EW-04` and `EW-05` gaps are distinguished from ready baseline modules | PASS | Sections 3 and 4 separate ready `EW-01` to `EW-03` from blocked follow-on surfaces |
| BFF gaps are code-backed, not speculative | PASS | gap notes cite `main.py`, `models.py`, `command_executor.py`, `read_store.py`, and the blocked inspiration contract |
| Frontend guidance does not over-promise readiness | PASS | Sections 5 and 7 explicitly keep `EW-04` and `EW-05` out of implementation dispatch |

---

## 9. Handoff to Reviewer (`Claude`)

This sidecar gives the reviewer and parent owner one bounded BFF/frontend reality
map for `BP5-WB-004`:

1. `EW-04 Inspiration Graph` already has reusable blocked-draft vocabulary, but the live BFF still stops at raw lineage primitives.
2. `EW-05 Mutation Review` can now cite settled L1 evolution and rollback semantics, but it still lacks the operator-facing projection, command vocabulary, `allowedActions`, and degradation signal that would make it a real screen.
3. The current evolution command path is governance-owned and useful context, but it is not yet a packetized mutation-review UX contract.
4. The seed data provides reviewer anchors like `artifact-042`, `evo-dec-001`, `rb-001`, and `ln-edge-001`, while also proving that the missing shapes are genuinely absent.

Recommended reviewer stance:

1. Approve this sidecar if it is accurate as a support packet.
2. Let the parent owner decide whether any of these notes should be absorbed into the mainline `EW-004` packet family or follow-on work sequencing.
3. Keep all canonical decisions and packet-truth edits in the parent task, not in this sidecar.
