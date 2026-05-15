# 2026-04-27 SD Current Implementation Maturity Assessment

Status: execution-facing assessment, not canonical blueprint truth
Tier: review / planning record
Scope: repo-local assessment of `docs/03/SD-00` through `SD-12` against the current `pantheon` implementation
Conflict rule: this file summarizes implementation maturity; L1 policies, L2 planning docs, and the SD source files remain authoritative for their own scopes
Prepared by: Codex

## Purpose

This assessment records the current gap between the `docs/03` system-design
blueprint set and the implementation that exists in this repo today.

It was created after reading all 13 SD files under `docs/03`:

- `SD-00_architecture_invariants.md`
- `SD-01_domain_model_registry_backbone.md`
- `SD-02_persona_governance.md`
- `SD-03_source_knowledge_evidence.md`
- `SD-04_research_orchestrator.md`
- `SD-05_consultation_redteam.md`
- `SD-06_capital_pool_governance.md`
- `SD-07_promotion_deployment.md`
- `SD-08_execution_runtime_binding.md`
- `SD-09_telemetry_reconciliation.md`
- `SD-10_incident_postmortem_evolution.md`
- `SD-11_bff_console_integration.md`
- `SD-12_cross_cutting_foundations.md`

The assessment intentionally avoids claiming "full system complete". The repo
has real executable slices, governed paper proof, and live broker packet
evidence, but the SD-00 through SD-12 future-state architecture is not yet fully
normalized into one complete platform implementation.

## Overall Interpretation

Current Pantheon maturity is best described as:

> a broad implementation platform with multiple governed vertical slices and
> evidence packets, not yet a fully normalized SD-00 through SD-12 operating
> system.

The strongest areas are runtime-manager, deployment and promotion, telemetry
ingest and lineage-read, BFF contract surfaces, evolution control, and governed
research adapters.

The weakest areas are unified cross-cutting foundations, the source / evidence /
search plane, consultation / red-team as a standalone governed domain service,
and runtime-manager-originated live or canary execution proof.

## SD-By-SD Maturity Matrix

| SD | Blueprint area | Current implementation maturity | Assessment |
|---|---|---:|---|
| `SD-00` | Architecture invariants | Partial | Invariants appear in docs, tests, and service-specific guardrails, but there is no single invariant registry or evaluator that all planes share. |
| `SD-01` | Domain model / registry backbone | Medium | Registry, promotion, and lineage pieces exist, but the source-to-runtime-to-telemetry graph is not yet one unified source of truth. |
| `SD-02` | Persona governance | Medium | Persona, capability, BFF, and governance surfaces exist; the full session / tool authority / runtime persona chain is still not completely end-to-end. |
| `SD-03` | Source / knowledge / evidence | Low-Medium | Research ingest, memory, and evidence references exist, but the full SourceConnector / EvidenceBundle / governed search plane is still partial. |
| `SD-04` | Research orchestrator | Medium-High | MLflow, DSPy, imitation, vectorbt, statsmodels, and QuantLib are governed production paths; Qlib, TRL, RL stack, and W&B remain activation-ready or deferred. |
| `SD-05` | Consultation / red-team | Low-Medium | BFF and workbench surfaces exist, but the consultation / red-team lifecycle is not yet a complete domain service with immutable memo, committee, and gate integration. |
| `SD-06` | Capital pool governance | Medium-High | Capital pool, persona binding, approval, and admissibility pieces exist; full live broker capability and pool-state closure remain incomplete. |
| `SD-07` | Promotion / deployment | High-ish | Approval, deployment plan, deployment saga, loader, and acceptance concepts are strong; consultation gate and policy-as-data integration still need hardening. |
| `SD-08` | Execution runtime binding | Medium-High | Runtime-manager, paper execution, broker adapters, and IBKR live packet tooling exist; deployable runtime-manager-originated live / canary proof remains unproven. |
| `SD-09` | Telemetry / reconciliation | Medium-High | Telemetry capture, ingest, buffer, DLQ, and lineage-read are substantial; order / fill / position reconciliation and drift alert closure remain partial. |
| `SD-10` | Incident / postmortem / evolution | Medium-High | Evolution is strong and incident / postmortem objects exist; the alert-to-incident-to-postmortem-to-evolution loop is not fully automatic end-to-end. |
| `SD-11` | BFF / console integration | High | BFF routes, read models, and contract tests are strong inside this repo; external frontend and deployment hardening must still be verified separately. |
| `SD-12` | Cross-cutting foundations | Low-Medium | Trace, idempotency, audit, safe mode, RBAC, secrets, outbox, and DLQ exist in local forms, but not as one canonical shared foundations layer used uniformly by all SDs. |

## Current Proof Boundary

The proof boundary remains intentionally conservative:

- `EP4` governed paper execution proof is stable and archived.
- `EP5-001` readiness material is materially complete, including canary-readiness,
  human-gate, and replay-clean telemetry event-trace artifacts.
- IBKR live broker interaction now has packet tooling and evidence direction, but
  this is still not the same as a runtime-manager-originated governed live or
  canary execution proof.
- `EP5-002` should not be marked complete until a governed live / canary order
  lifecycle packet is archived with operator signoff, broker acknowledgement,
  cancel or fill lifecycle, telemetry trace, runtime-manager lifecycle, and
  closeout evidence.

Relevant repo anchors:

- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/`
- `docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/`
- `docs/deployment/evidence/ep5-human-gate-input/20260426T100542Z/`
- `docs/deployment/evidence/ep5-event-trace-replay-clean/20260426T100542Z/`
- `docs/deployment/evidence/ibkr-live-order-cancel/`
- `scripts/validate_ep5_live_order_cancel.py`

## Research / OSS Activation Boundary

The production research path is real but not complete-blueprint final:

- Governed production paths: MLflow, DSPy, imitation, vectorbt, statsmodels,
  QuantLib.
- Governed but still activation-bound runtime layer: OpenClaw.
- Smoke-tested / activation-ready paths: Qlib and TRL.
- Deferred or gate-closed paths: FinRL, RLlib, Ray Tune, W&B.

This preserves the distinction between "governed or smoke-tested baseline" and
"production-activated default path". Do not promote Qlib, TRL, RL stack, or W&B
without the evidence gates named in `RESEARCH_BACKEND_MATURITY_MATRIX.md` and
the 2026-04-26 activation gate report.

## Residual Gap Classes

### 1. Unified foundations gap

`SD-00` and `SD-12` expect one cross-plane foundations layer for:

- trace context
- command envelope
- idempotency
- policy decision
- audit action
- secret reference
- error envelope
- event outbox
- DLQ
- safe mode and kill switch primitives

Current implementation has many of these pieces locally, especially around
runtime-manager, BFF, telemetry, and signal storage, but they are not normalized
as one shared `services/foundations` layer.

### 2. Registry and lineage single-truth gap

Registry, promotion, telemetry, and lineage-read exist, but the SD target is a
single traceable chain:

```text
source -> strategy -> experiment -> artifact -> approval -> deployment
  -> runtime binding -> order / fill / position -> telemetry -> evolution
```

The current repo supports many of these edges, but it should still be treated as
partially distributed truth until the chain is queryable and replayable as one
operator-facing lineage model.

### 3. Source / evidence / search gap

The repo has research ingest and memory surfaces, but the full governed
SourceConnector, EvidenceBundle, KnowledgeObject, and SearchGateway architecture
from `SD-03` is not complete.

This gap is separate from workbench route-live status. A route can be live while
the underlying source / evidence platform is still only partially implemented.

### 4. Consultation / red-team domain gap

Consultation workbench routes and BFF packets are materially stronger than the
older backlog suggested, but `SD-05` describes a richer domain service:

- consultation request lifecycle
- committee debate
- red-team memo publication
- immutable evidence attachment
- governance gate integration
- replayable consultation decision record

Current repo maturity should remain Low-Medium until those are owned by a
first-class service boundary rather than mostly by BFF and packet surfaces.

### 5. Live execution proof gap

The IBKR live order / cancel packet path is valuable evidence, but `SD-08` and
the proof ladder require a governed live / canary runtime path. The remaining
gap is not merely "can the broker API receive a request"; it is:

- runtime-manager emits the live / canary lifecycle
- deployment plan and runtime binding are attached
- operator approval and closeout are archived
- broker acknowledgement is captured
- cancel or fill lifecycle is captured
- telemetry and lineage can replay the lifecycle
- rollback or risk-off semantics remain available

### 6. Cross-repo verification gap

The SD set is explicitly multi-repo: `pantheon`, `front-ai-trading-system`, and
`lean-platform`. This assessment only verifies the current `pantheon` workspace
directly. Any claim about full SD completion must additionally verify frontend
operator UX behavior and lean-platform runtime hooks.

## Recommended Next Work Order

1. Materialize the canonical foundations layer for `SD-00` and `SD-12`.
2. Route BFF commands, runtime-manager actions, telemetry ingest, and registry
   writes through the same trace / command / idempotency / audit envelope.
3. Normalize registry and lineage so the source-to-runtime-to-telemetry chain is
   queryable as one operator-facing read model.
4. Complete the runtime-manager-originated IBKR live / canary proof packet before
   promoting `EP5-002`.
5. Promote Qlib and TRL only through their activation gates; keep RL and W&B
   gate-closed until their deferred evidence exists.
6. Upgrade `SD-03` source / evidence / search and `SD-05` consultation / red-team
   from BFF-visible surfaces into first-class governed domain services.

The materializable task packet for this sequence is
`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`.

## Operator-Facing Summary

Pantheon is past pure blueprint. It has real execution proof, real BFF surfaces,
real telemetry, and real governed research paths.

Pantheon is not yet full-system complete. The remaining work is about making the
strong slices behave like one coherent governed platform: shared foundations,
single-truth lineage, complete source / evidence and consultation domains, and a
runtime-manager-originated live / canary proof.
