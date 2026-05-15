# BP5-SVC-012 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP5-SVC-012-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP5-SVC-012` - Realize the EvolutionDecision service and governance read path
**Parent owner:** `Qwen`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Reviewer:** `Codex`
**Date:** `2026-04-15`
**Status:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, registry truth, or governance truth. It records the formal acceptance criteria,
> dependency map, and service contract scaffold for BP5-SVC-012 so the assigned reviewer can judge
> the parent slice quickly and the parent owner has a compact acceptance scaffold when implementation
> is complete.
>
> Current repo state: `services/evolution/` does not yet exist. `ai-status.json` still lists
> BP5-SVC-012 as `todo` under Qwen ownership with Codex as the assigned reviewer. This sidecar
> establishes the acceptance surface and policy anchors so the parent task can be reviewed promptly
> once implementation lands.

---

## 1. Purpose

This packet gives `Codex` a compact review surface for `BP5-SVC-012-SIDECAR-ACCEPTANCE`:

1. a criterion-by-criterion acceptance checklist for the parent EvolutionDecision service slice
2. a concrete file inventory the reviewer should confirm exists once Qwen delivers implementation
3. a dependency map showing what this parent task unblocks and which adjacent consumers depend on it
4. policy anchors the parent reviewer must consult when evaluating the implementation against L1 truth

The key point is structural: **BP5-SVC-012 must deliver a single canonical service path for
EvolutionDecision lifecycle, including actor role enforcement, cooldown/observation-window tracking,
and evidence linkage — none of these may remain as policy-only text after the task is marked done.**

---

## 2. Acceptance Checklist

Formal acceptance criteria from the phase-5 planning session:

- **AC-1:** `evolution decisions are created and queried through one canonical service path`
- **AC-2:** `cooldown, convergence, actor role, and evidence linkage rules are enforced in runtime-visible behavior`

### AC-1: Evolution decisions are created and queried through one canonical service path

| Check | Evidence needed | Status |
|---|---|---|
| `services/evolution/` directory exists with a service entry point | reviewer should confirm presence of at least `services/evolution/__init__.py` or equivalent module entry | PENDING |
| EvolutionDecision schema/model captures all seven lifecycle states | states required: `proposed`, `reviewed`, `approved`, `executed`, `rejected`, `canceled`, `superseded` per `EVOLUTION_REVIEW_AND_THRESHOLDS.md:62-84` | PENDING |
| Decision creation endpoint enforces that only Evolution Controller may propose | `EVOLUTION_REVIEW_AND_THRESHOLDS.md:42-44` specifies proposer = Evolution Controller (system); ad-hoc creation by research workers must be rejected | PENDING |
| Decision query path returns decisions scoped by target type, actor role, and lifecycle state | reviewer should confirm a read path exists covering: target-scoped listing, state filter, and actor-role filter | PENDING |
| Decision types are enumerated and normalized against both L1 taxonomy views | reviewer should confirm the service exposes one canonical enum or normalization layer that preserves both L1 views: the stage-aware taxonomy in `EVOLUTION_REVIEW_AND_THRESHOLDS.md:88-116` and the action-family taxonomy in `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:174-192`; no undeclared types may be accepted, and the implementation must not silently erase the paper/canary/live freeze distinction | PENDING |
| Reject/canceled/superseded transitions prevent re-opening without explicit re-proposal | transitions into terminal states must be one-way unless a new `proposed` record replaces the target | PENDING |
| Service is reachable via HTTP or equivalent governed interface (not CLI-only) | BP5-SVC-010 established a precedent for HTTP surface on the read path; BP5-SVC-012 should follow the same pattern | PENDING |

**AC-1 assessment (pre-implementation):** the L1 docs define a complete state machine, type taxonomy,
and role boundary. Once `services/evolution/` exists, the reviewer must confirm these requirements
are encoded in executable service logic rather than just comments or stubs.

### AC-2: Cooldown, convergence, actor role, and evidence linkage rules are enforced in runtime-visible behavior

| Check | Evidence needed | Status |
|---|---|---|
| Single-active-rule enforced per target | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:130-131` specifies that the same target may only hold one active EvolutionDecision at a time; new proposals must be rejected while an active decision exists | PENDING |
| Cooldown windows are stored and enforced per action family | three action families with distinct cooldown/observation windows (3/7/14 days) must be tracked by the service per `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:136-142` | PENDING |
| Observation window clock starts from downstream plane acceptance, not from the `executed` write | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:143-150` specifies that the authoritative clock is when the downstream plane accepts the work item, not when the record is written | PENDING |
| Actor role validated against L1 tier definitions at `reviewed` and `approved` transitions | tier enforcement: low-risk → Reviewer on Duty; mid-risk → Reviewer + Risk Owner; high-risk → Governance Committee; per `EVOLUTION_REVIEW_AND_THRESHOLDS.md:120-154` and `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:50-93` | PENDING |
| Evidence linkage to upstream artifacts is required — decisions must cite `target_id` and `target_type` | the EvolutionDecision must carry a reference to the subject artifact scope defined in `EVOLUTION_REVIEW_AND_THRESHOLDS.md:24-34`, and the reference shape must align with canonical lineage projection fields in `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:145-189` rather than narrative-only strings | PENDING |
| Escalation from cooldown to freeze/rollback path is available when severity conditions are met | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:152-155` requires that repeated mutate attempts within cooldown escalate rather than repeat; the service must expose or trigger this escalation path | PENDING |
| Governance / incident thresholds route directly to high-risk path without low/mid pass-through | Severity-1 incidents, two Severity-2 incidents on the same artifact within 30 days, and unresolved loader/binding/approval mismatches must bypass low/mid tiers per `EVOLUTION_REVIEW_AND_THRESHOLDS.md:190-196` | PENDING |

**AC-2 assessment (pre-implementation):** the cooldown, convergence, and actor-role rules are fully
specified across two L1 documents. The reviewer must confirm each rule is verifiably enforced at the
service boundary — rejection codes, not just advisory logging.

---

## 3. Expected File Inventory

The following files should exist once the parent task is delivered. The reviewer should confirm their
presence and confirm each file's role matches the description.

| Expected path | Expected role |
|---|---|
| `services/evolution/__init__.py` (or equivalent) | Module entry point / package marker |
| `services/evolution/evolution_decision.py` (or equivalent model file) | EvolutionDecision data model, state machine, and type enum |
| `services/evolution/evolution_svc.py` (or equivalent service file) | Create, transition, and query logic; enforces single-active-rule, cooldown, and actor-role validation |
| `services/evolution/cooldown.py` (or equivalent) | Cooldown/observation-window tracking and enforcement per action family |
| `services/evolution/actor_roles.py` (or equivalent) | Actor role definitions and tier lookup (Reviewer on Duty / Risk Owner / Governance Committee) |
| `services/evolution/test_evolution_svc.py` (or equivalent) | Unit/integration tests covering lifecycle transitions, cooldown enforcement, and role validation |
| `services/evolution/smoke_test_evolution.py` (or equivalent) | Runnable smoke script for reviewer quick-check |

> Note: exact file names may differ. The reviewer should evaluate whether the substance of each
> role is covered by whatever files are actually present, not match file names mechanically.

---

## 4. Dependency Map

### 4.1 Upstream dependency already satisfied

| Dependency | Status | Relevance |
|---|---|---|
| `BP5-SVC-010` | done | EvolutionDecision evidence linkage requires canonical lineage/binding reads; the lineage read service and performance path delivered by BP5-SVC-010 establish the read model that evolution queries must cite |

### 4.2 Direct downstream dependencies

| Task | Depends on BP5-SVC-012 for | Evidence |
|---|---|---|
| `BP5-SVC-013` | freeze, rollback, retrain, and redeploy orchestration must be grounded in an accepted EvolutionDecision record; without BP5-SVC-012, the runtime-manager action path has no governed decision to reference | `ai-status.json` lists `BP5-SVC-013` with `depends_on: [BP5-SVC-008, BP5-SVC-011, BP5-SVC-012]` |
| `BP5-OSS-004` | deferred OSS rows (Qlib, TRL, FinRL, RLlib, W&B) need an EvolutionDecision path to progress from criteria-only to governed activation | `ai-status.json` lists `BP5-OSS-004` with `depends_on: [BP5-SVC-012, BP5-OSS-003]` |
| `BP5-WB-004` | Evolution Workbench follow-on surfaces (inspiration/mutation review) must cite canonical evolution decisions; packet language cannot be truthful without a live service path | `ai-status.json` lists `BP5-WB-004` with `depends_on: [BP5-SVC-012, BP5-SVC-013]` |
| `BP5-WB-008` | Consultation Workbench surfaces include governance debate and evolution-related approval flows that require canonical EvolutionDecision semantics | `ai-status.json` lists `BP5-WB-008` with `depends_on: [BP5-SVC-003, BP5-SVC-012, BP5-SVC-014]` |
| `BP5-LUV-006` | the evolution-center Lovable screen must cite canonical evolution decision and action semantics; Lovable-readiness is gated on BP5-SVC-012 and BP5-SVC-013 | `ai-status.json` lists `BP5-LUV-006` with `depends_on: [BP5-SVC-012, BP5-SVC-013, BP5-SVC-015]` |

### 4.3 Adjacent consumers that benefit once parent semantics are accepted

| Consumer | Benefit |
|---|---|
| Operator Console runtime-state and alert surfaces | can consume governed evolution state (freeze, pending review, observation window) rather than deriving these from raw telemetry signals |
| BFF read paths | can answer "is there an active evolution decision for this artifact?" with an authoritative service call rather than a UI-local heuristic |
| Audit / postmortem surfaces | can cite specific EvolutionDecision records as root-cause evidence without shadow copies or narrative-only links |

### 4.4 Policy dependencies the reviewer must keep in view

| Policy source | Specific sections | What to confirm |
|---|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | §4 (state machine), §5 (types), §6 (reviewed/approved owners), §7 (thresholds), §11-§14 (routing boundary, executed semantics, and API shape) | The service encodes the state machine, type mapping, actor-role gating, and downstream routing boundary faithfully — not just structurally correct fields that pass no validation |
| `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` | §3 (risk tiers), §4 (trigger conditions), §5 (cooldown/observation windows), §6 (freeze vs rollback), §8 (v1 decisions) | Cooldown windows and observation-window start clocks are enforced in code, not advisory; single-active-rule produces a rejection response on violation |
| `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` | relevant sections on outbox/inbox and saga consistency | EvolutionDecision state changes that propagate downstream (e.g., freeze triggering a governance quarantine) must go through an outbox/saga boundary, not a direct synchronous call |
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | relevant sections on governance evidence linkage | EvolutionDecision evidence citations must resolve against canonical lineage records, not narrative strings |

---

## 5. Open Acceptance Questions for the Parent Reviewer

The following items are not blocking this sidecar's review but must be resolved during parent-task
implementation and review:

1. **Decision taxonomy normalization** — `EVOLUTION_REVIEW_AND_THRESHOLDS.md` uses stage-aware
   types such as `freeze_paper`, `freeze_canary`, and `freeze_live_strategy`, while
   `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` uses generic action families such as `freeze`,
   `retire`, and `revive`. Does the implementation declare an explicit canonical enum plus mapping
   layer, or does it leave this normalization implicit?

2. **Downstream plane clock handoff** — does the service provide a mechanism to accept an
   external timestamp from the downstream plane when an `executed` decision's work item is accepted?
   Without this, the observation-window clock cannot start at the correct authoritative moment.

3. **Rollback companion semantics** — `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:141` specifies
   that a rollback companion command does not open a new cooldown window and instead reuses the
   parent decision's window. Does the service model this relationship explicitly (e.g., via a
   `companion_decision_id` or `parent_decision_id` field)?

4. **manual-only strategy-family flag** — `EVOLUTION_REVIEW_AND_THRESHOLDS.md:129-130` specifies
   that if a strategy family is marked `manual-only`, auto-approval of low-risk decisions is
   prohibited. Does the service consult this flag before applying auto-review rules?

5. **HTTP surface completeness** — BP5-SVC-010 established an HTTP surface for the lineage read
   service. BP5-SVC-012 should provide an analogous HTTP surface for EvolutionDecision creation and
   query. The parent reviewer should confirm the surface exists and is not CLI-only.

---

## 6. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No evolution service implementation file was created or modified by this sidecar
- No runtime-manager, registry, or governance truth was edited by this sidecar
- The only artifact created by this slice is this reviewer packet
- Reviewed and approved by `Codex`, this packet is handed back to the parent owner (`Qwen`) and
  remains available to the parent reviewer (`Codex`) as a compact acceptance scaffold once
  `services/evolution/` is implemented
