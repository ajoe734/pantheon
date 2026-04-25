# Pantheon Gap Closure Plan

> Planning and execution record: this document captures the 2026-04-18 actionable gap-closure plan derived from the current repo state. It is not immutable blueprint truth.

## Goal

Close the remaining gap between Pantheon's foundational platform completion and the full system blueprint by sequencing:

1. planning-governance repair
2. canonical document completion
3. backend and BFF closure
4. workbench and product-surface completion
5. runtime and execution-proof hardening

## Current Diagnosis

Pantheon is not blocked on basic architecture anymore. The main remaining problems are:

- planning mode is still too permissive and does not force blueprint reconciliation before execution materialization
- canonical L2 docs still do not fully express workbench backlog, delivery-loop closure semantics, or execution-proof maturity
- several operator and workbench surfaces remain only partially productized
- frontend and coordination loops remain open even when foundational contracts are already done
- runtime proof is still below final live execution proof

## Wave 0: Planning Governance Repair

Objective:
Make planning mode follow a strict two-stage gate:

1. `blueprint_reconciliation`
2. `execution_planning`

Required changes:

- extend `scripts/planning_state.py` to require document reconciliation before `ready_for_human`
- block `materialize` unless reconciliation is complete and human gate is approved
- persist reconciliation status in planning state and task materialization metadata
- add tests that prevent planning sessions from skipping blueprint reconciliation

Exit criteria:

- planning mode cannot directly jump from readouts to materialized execution tasks
- the active session must explicitly record either:
  - canonical docs updated
  - or canonical docs reviewed and no change required

## Wave 1: Canonical L2 Completion

Objective:
Make the missing system-level truth explicit in canonical planning documents.

Required new canonical docs:

- `WORKBENCH_DELIVERY_BACKLOG.md`
  - formalize remaining module-level delivery scope for Operator Console, Governance follow-on, Evolution, Research, Knowledge, Consultation, and Trainer workbenches
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
  - define `contract_ready`, `ui_done_received`, `frontend_feedback_received`, `follow-up-required`, `loop-complete`, and related closure semantics
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
  - define the ladder from local health through final live execution proof

Required updates:

- `ROADMAP.md`
  - clearly separate foundational completion from product closure and execution proof
- `DEVELOPMENT_WORKBREAKDOWN.md`
  - clarify that it is the foundational backlog, not the full productization ledger
- `AI_COLLABORATION_GUIDE.md`
  - align planning mode rules with the two-stage planning gate

Exit criteria:

- canonical L2 documents can answer:
  - what remains unproductized
  - when a loop is truly closed
  - what execution proof level has actually been reached

## Wave 2: Backend and BFF Closure

Objective:
Close the remaining backend gaps that still block honest product closure.

Priority items:

- implement or complete `approval-queue` operator-composed BFF route
- implement or complete `deployment-diff` operator-composed BFF route
- verify governance follow-on payloads against packet families
- deepen `evaluation`, `feedback`, and `memory` from minimal service stubs into real integration paths
- close any remaining incident/runtime data gaps that still force degraded or blocked packet flows

Exit criteria:

- all known blocking BFF packet gaps are resolved
- the remaining governance and operator surfaces have live backend-composed routes
- minimal service placeholders are no longer the limiting explanation

## Wave 3: Delivery Loop Closure

Objective:
Convert open coordination state into genuinely closed delivery loops.

Required work:

- sweep `.coordination` for nonterminal packet states
- resolve `spec_request`, `ui_done_received`, `frontend_feedback_received`, and `follow-up-required` items
- repair YAML parse errors
- explicitly retire stale loops instead of leaving them open
- align dashboard and `current-work.md` semantics with the canonical closure-state model

Exit criteria:

- no stale or invalid coordination artifacts remain in the active path
- every packet is either:
  - loop-complete
  - still active with a clear next action
  - or explicitly retired

## Wave 4: Workbench and Product Surface Completion

Objective:
Finish the product surfaces that still lag behind the foundational platform.

Priority order:

1. Operator Console Wave 2
2. Governance follow-on closure
3. Evolution Workbench `EW-04` and `EW-05`
4. Research Workbench
5. Knowledge Workbench
6. Consultation Workbench
7. Trainer Workbench

Guiding rule:

- no workbench counts as done merely because its underlying domain objects exist
- each workbench needs contract-ready surfaces, backend routes, example payloads, degraded-state handling, and closed delivery loops

Exit criteria:

- no major workbench remains only at packet-family or placeholder level
- module-level backlog can move from "known gap" to "closed product surface"

## Wave 5: Runtime and Execution-Proof Hardening

Objective:
Move from acceptance-harness confidence to stronger runtime proof.

Required work:

- stabilize OpenClaw-authenticated smoke path
- clean up execution-side runtime proof and evidence
- explicitly validate the boundary between paper bootstrap and real live execution proof
- write or update evidence docs to show the highest honestly proven level

Exit criteria:

- the repo can state its current proof level without ambiguity
- remaining unproven claims are explicit and narrow

## Recommended Execution Order

1. Wave 0
2. Wave 1
3. Wave 2
4. Wave 3
5. Wave 4
6. Wave 5

Parallelism guidance:

- Wave 1 doc work can partly overlap with Wave 2 backend work
- Wave 3 coordination cleanup can run in parallel once Wave 1 closure semantics are defined
- Wave 5 should begin only after the relevant backend and delivery surfaces are stable enough to measure honestly

## Immediate Next Actions

1. Refactor `scripts/planning_state.py` into a strict reconciliation-first planning flow.
2. Publish the three missing canonical L2 docs.
3. Finish the two known missing governance follow-on BFF routes.
4. Run a coordination sweep and classify every nonterminal packet.
