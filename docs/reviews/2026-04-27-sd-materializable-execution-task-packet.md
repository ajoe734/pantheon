# 2026-04-27 SD Materializable Execution Task Packet

Status: execution-ready packet; tasks are materializable but not automatically activated
Source: `docs/reviews/2026-04-27-sd-current-implementation-maturity-assessment.md`
Prepared by: Codex

## Purpose

This packet converts the SD-00 through SD-12 implementation maturity assessment
into concrete execution tasks that can be copied into `ai-status.json`,
delegated to agents, or opened as follow-up work.

It does not itself activate tasks. It also does not promote any proof level,
research backend, or production path. Activation still requires the normal board
or operator gate.

## Materialization Rules

1. Use this packet to create follow-up tasks, not to reinterpret existing closed
   rows as unfinished.
2. Keep `EP5-002` live / canary execution behind explicit human approval.
3. Keep Qlib and TRL activation evidence-gated.
4. Keep RL and W&B reopen work deferred until their named gates clear.
5. Do not claim "full system complete" until the cross-cutting foundations,
   single-truth lineage, source / evidence / search, consultation / red-team
   domain service, and live / canary proof gaps are all closed.

## Recommended Activation Order

1. `SD-FND-001`, `SD-LIN-TRACE-001`, `SD-SRC-EVIDENCE-001`, and
   `SD-CONSULT-001` - safe parallel first wave.
2. `SD-FND-002` and `SD-FND-003` - foundation adoption and durable primitives
   after the foundation package boundary is chosen.
3. `SD-RECON-001` - reconciliation after the first lineage trace shape exists.
4. `EP5-002-PACKET-PREP-001` - live / canary proof packet prep with no broker
   side effects.
5. `EP5-002-RUNTIME-LIVE-PROOF-001` - only after explicit human approval.
6. In parallel when evidence exists: `OSS-ACT-QLIB-001` and
   `OSS-ACT-TRL-001`.

## Materializable Execution Tasks

| Task ID | Owner | Reviewer | Depends On | Scope |
|---|---|---|---|---|
| `SD-FND-001` | Codex | Claude | - | Materialize the canonical foundation package boundary for `SD-00` / `SD-12`: choose and document the package path, add shared TraceContext, CommandEnvelope, ErrorEnvelope, IdempotencyRecord, PolicyDecision, AuditAction, SecretRef, and baseline tests. |
| `SD-FND-002` | Codex | Gemini | `SD-FND-001` | Adopt the shared foundation envelope in one BFF command path and one runtime-manager action path, proving trace propagation, idempotency, audit emission, policy denial, and stable error envelopes. |
| `SD-FND-003` | Gemini | Codex | `SD-FND-001` | Add shared outbox / DLQ / schema-registry primitives and one replay test that proves audited, idempotent DLQ replay without weakening existing telemetry storage semantics. |
| `SD-LIN-TRACE-001` | Codex2 | Claude | - | Build the first operator-facing lineage trace query that joins source, strategy, experiment, artifact, approval, deployment plan, runtime binding, broker/order lifecycle, telemetry, and evolution references as a derived read model. |
| `SD-SRC-EVIDENCE-001` | Copilot | Codex | - | Upgrade `SD-03` from partial ingest / memory surfaces to a governed SourceConnector / EvidenceBundle / KnowledgeObject / SearchGateway slice with contract tests and replayable evidence refs. |
| `SD-CONSULT-001` | Gemini | Codex | - | Extract consultation / red-team lifecycle from mostly BFF-visible surfaces into a first-class governed domain service with request, committee debate, immutable memo, evidence attachment, gate handoff, and replay record. |
| `SD-RECON-001` | Gemini | Codex | `SD-LIN-TRACE-001` | Extend telemetry / reconciliation for order, fill, cancel, position, paper-live drift, and alert closure so `SD-09` can prove lifecycle reconciliation rather than only ingest / read-model availability. |
| `EP5-002-PACKET-PREP-001` | Codex | Gemini | `SD-FND-002`, `SD-LIN-TRACE-001` | Prepare the runtime-manager-originated live / canary proof packet without placing live orders: dry-run command envelope, operator checklist, telemetry refs, runtime lifecycle schema, IBKR packet manifest, validator expectations, and closeout template. |
| `EP5-002-RUNTIME-LIVE-PROOF-001` | Codex | Gemini | `EP5-002-PACKET-PREP-001` + explicit human approval | Execute and archive the first governed runtime-manager-originated live / canary lifecycle proof, including operator signoff, broker acknowledgement, cancel or fill lifecycle, telemetry trace, runtime-manager lifecycle, and closeout evidence. |
| `OSS-ACT-QLIB-001` | Copilot | Codex | governed dataset and RS-003 candidate evidence | Promote Qlib from smoke-tested to the first production-activated supervised-alpha lane only through the gate verifier: target StrategySpec, >=50 instruments, >=2 years governed OHLCV, archived activation run, and registry artifact envelope. |
| `OSS-ACT-TRL-001` | Codex2 | Codex | sufficient FB-002 runtime evidence and approved imitation artifact | Promote TRL from smoke-tested to the first production-activated preference-learning lane only through the gate verifier: >=200 feedback events, >=100 valid pairs, >=2 strategy families, baseline metrics, downstream consumer, and archived DPO packet. |
| `CROSS-REPO-SD-VERIFY-001` | Codex | Codex2 | `SD-FND-002`, `SD-LIN-TRACE-001` | Verify the multi-repo SD boundary across `pantheon`, `front-ai-trading-system`, and `lean-platform`: frontend command authority, trace/error UX, runtime telemetry hooks, and no parallel authority paths. |

## Human-Gated Task Detail

`EP5-002-RUNTIME-LIVE-PROOF-001` is materializable as a named task, but it is
not auto-dispatchable. It must remain blocked until a human operator explicitly
approves the live / canary execution attempt for the exact account, instrument,
quantity, price, session, and rollback plan.

The prep task may create files, validators, dry-run packets, and checklists. It
must not submit, modify, or cancel live broker orders.

## Deferred / Not Materialized

| Item | Reason |
|---|---|
| `OSS-RL-REOPEN-001` | Do not materialize until Qlib is approved and stable for at least 90 days, the target problem is sequential, an intraday/order-fill dataset exists, and the RL approval gate is accepted. |
| `OSS-WANDB-REOPEN-001` | Do not materialize before the MLflow 30-day history gate, operator-preference gate, adapter review, canonical-state migration, SDK compatibility proof, and network / self-host readiness all exist. |
| "full-system-complete" closeout task | Premature. This should only be materialized after the foundation, lineage, source/evidence, consultation, reconciliation, research activation, and EP5 proof gaps have closed. |

## Acceptance Shape By Gap Class

### Foundations

- shared foundation package path is documented
- trace context and command envelope are imported by at least BFF and
  runtime-manager
- policy denials and validation errors return stable error envelopes
- idempotency and audit behavior are covered by tests

### Registry / Lineage

- operator can query one trace and see deployment plan, runtime binding,
  telemetry, and lifecycle refs
- read model remains derived-only and does not become a parallel truth source
- replay can prove missing IDs or missing edges explicitly

### Source / Evidence / Search

- source connectors produce evidence bundles with provenance and trace refs
- search results point back to governed evidence, not raw undocumented blobs
- BFF routes consume this truth rather than inventing shadow payloads

### Consultation / Red-Team

- consultation request, committee debate, and red-team memo have service-owned
  lifecycle records
- memo publication is immutable or append-only
- governance gate handoff carries evidence refs and audit trace

### EP5 Live / Canary Proof

- runtime-manager is the origin of the governed live / canary lifecycle
- operator signoff is archived before side effects
- broker acknowledgement and cancel or fill lifecycle are archived
- telemetry and lineage refs replay cleanly
- closeout records whether the order was canceled, filled, partially filled, or
  otherwise resolved

### Research Activation

- activation status changes only after
  `scripts/run_research_activation_gates.py` returns a passing result with
  archived evidence
- deferred-prep scaffolds do not promote production status

## Expected Outcome

After the materialized tasks close in the recommended order:

- SD-00 / SD-12 become a real shared foundation rather than distributed local
  conventions
- SD-01 / SD-09 can expose one replayable trace from research evidence to
  runtime telemetry
- SD-03 and SD-05 move from surface-visible slices to governed domain services
- `EP5-002` can be evaluated from an archived runtime-manager-originated proof
  packet instead of direct broker packet evidence alone
- research activation remains truthful and evidence-gated
