# TJ-E2E-001 Producer And Correlation Inventory

Status: implementation input for `TJ-E2E-002` and `TJ-E2E-003`
Owner: Codex2
Reviewer: Claude
As inspected: 2026-07-11
Behavior change: none

## Scope and method

This inventory traces repository contracts and executable tests; it does not
infer joins that the current payloads cannot prove. The two concrete samples
are:

- paper: `tests/e2e/test_allocation_policy_to_paper_run.py`;
- broker sandbox: `tests/e2e/test_shioaji_adapter_filled_readback_memory_e2e.py`.

"Retention unspecified" means no producer contract inspected here states a
retention period. It must not be read as indefinite retention.

## Current producer matrix

| Stage / producer | Current source of truth and owner | Schema / current identifiers | Retention and ordering | Environment scope | Correlation loss and required producer change |
|---|---|---|---|---|---|
| Persona / OODA | `services/persona/cognitive_loop_runtime.py`; Persona service | `case_id`, persona IDs, generated `trace_id`, memory `source_event_id` | Store-dependent; no cross-stage ordering contract | research/paper-oriented fixtures | No research or trade journey ID. Accept and emit the common envelope; create `research_journey_id` at research-question creation and preserve causation IDs. |
| Research / source ingest | `services/research/*`, source-ingest records; Research service | research schema uses `signal_id`, `strategy_id`, optional `run_id`, `binding_id`, `runtime_id`; ingest has `ingest_run_id`, caller `trace_id` | Signal timestamp only; retention unspecified | not consistently explicit | Research signal schema has no `journey_id`, `research_journey_id`, `correlation_id`, `trace_id`, tenant or environment. Add the envelope as required fields at decision publication; preserve source evidence refs. |
| Evolution / optimizer | `services/evolution/*`; Evolution service | proposal/artifact refs; cooldown path accepts `trace_id` and `correlation_id` | Cooldown supplies temporal policy; event sequence not canonical | stage may be metadata | IDs are optional and fall back to target refs. Require upstream research journey and envelope; never synthesize correlation from an artifact target. |
| Ranking / promotion | registry and `services/promotion/*`; Registry/Promotion owner | registry/artifact/candidate IDs, state transitions | Registry lifecycle order; retention unspecified | promotion stage, not execution environment | Candidate-to-research lineage is provenance-only and not a stable envelope. Persist `research_journey_id`, causation event and sponsor/persona IDs in promotion records/events. |
| Human gate | governance ApprovalDecision / promotion-readiness stores; Governance owner | `decision_id`, target ID/version, evidence refs, persona/capital IDs | decision lifecycle; TTL exists on selected readiness packets | target stage may be present | No stable link from approval to future trade intent. Preserve research journey/correlation; emit immutable decision event ID and causation ID. Do not create a trade `journey_id` here. |
| Capital binding | `services/capital/*`; Capital service | capital pool, persona-capital binding, audit `event_id`, optional `trace_id` | binding TTL exists; audit ordering is append order only | paper/canary/live semantics in binding | `correlation_id` and journey IDs absent. Store envelope plus binding version/snapshot ID on binding decision and audit events. |
| Deployment | `services/deployment/models.py` and service; Deployment owner | `plan_id`, approval/artifact/strategy/pool/binding IDs; saga envelope has `event_id`, aggregate/sequence, causal parent, `trace_id`, idempotency key | explicit per-aggregate `sequence_no`; outbox replay/dedupe; retention unspecified | `current_stage`, `target_stage` | Best ordered envelope, but no journey IDs and correlation is not in `SagaEventEnvelopeBody`. Preserve research correlation; add `correlation_id`, environment, tenant and schema version to every saga event. |
| Runtime manager | `services/runtime-manager/service.py` and execution runtime manager; Runtime owner | runtime/binding/plan/artifact/pool IDs; foundation `trace_id` + `correlation_id` in newer service path | command/idempotency handling; retention unspecified | deployment mode/stage | Two runtime-manager implementations expose uneven fields. Standardize envelope in request, binding record, command and receipt; reject loss at dispatch boundaries. |
| Signal / decision | `services/execution/lean_runtime/signal_producer.py`, `services/research/schema.json`; Execution producer | deterministic/UUID `signal_id`, strategy/run/binding/runtime IDs | Redis pending queue; signal timestamp; no event sequence | absent | Canonical creation point for `journey_id`, but current schema lacks it, trace/correlation, decision ID, environment and tenant. Generate one `journey_id` per trade intent and require full envelope before enqueue. |
| Risk | capital risk policy and LEAN/execution guards; Capital/Execution owners | policy/check outputs are embedded in decisions or telemetry; IDs vary | evaluation order local to caller | inferred from caller | No durable `risk_decision_id` or common envelope. Emit one immutable risk evaluation event with policy/version, checks, terminal result and the incoming journey envelope. |
| Execution router / LEAN | `services/execution/lean_runtime/*`; Execution owner | signal, binding/runtime IDs; LEAN order ticket/local order IDs | queue order plus engine callbacks; no cross-service sequence | paper in sample; adapters vary | Signal IDs survive in telemetry metadata, but journey/decision/risk IDs do not. Copy full envelope into order tags/context and every execution callback. |
| Broker adapter | `services/broker/sinopac/adapter.py`; Broker owner | adapter `order_id`, Shioaji trade/broker ID, status/fill attributes | broker readback order; no durable monotonic revision in contract | explicit `sandbox`; real-order/capital flags | Submit API does not accept the envelope or client order ID. Require `client_order_id`, journey envelope and attempt ID; preserve broker IDs without replacing local IDs. |
| Fill handler | adapter readback + telemetry feedback adapter; Broker/Telemetry boundary | telemetry `event_id`, signal ID in metadata, local/broker order IDs, quantities/prices | append/idempotent by telemetry `event_id`; broker event ordering unspecified | promotion/deployment state | No distinct `fill_id`; filled readback can overwrite an order snapshot. Emit append-only fill events with broker execution/fill ID, revision, event time, received time and full envelope. |
| Ledger | paper runtime/telemetry lineage projections; Execution/Capital owner boundary | paper fill metrics and lineage IDs; no canonical ledger entry ID in sample | store append order; retention unspecified | paper sample | The paper proof calls telemetry a fill but does not prove booking. Emit immutable `ledger_entry_id`, booking status/time and envelope; never infer booking from fill. |
| Reconciliation | `services/reconciliation-drift/*`; Reconciliation Drift owner | `evaluation_id`, `recon_run_id`, `record_id`, drift report ID, source telemetry event IDs, optional trace | fixture consumer idempotent by source `event_id`; derived report `drift-{event_id}` | deployment stage present in fixtures | This is metric-drift reconciliation, not broker-vs-ledger trade reconciliation. Add trade reconciliation producer with `reconciliation_id`, compared ledger/broker refs, terminal status, revisions and envelope. |
| Audit | per-domain audit logs plus projections; each write-owning domain | commonly generated `event_id`; domain-specific target IDs | append-like, but no shared ordering/retention contract | inconsistent | Audit cannot reverse-resolve a trade reliably. Require the envelope, source event ID, actor, environment and schema version in all audit writes. |
| Incident | `services/incident/*` and `services/incidents/*`; Incident owner | incident/case IDs, binding/deployment/telemetry refs | incident lifecycle; retention unspecified | stage may be evidence | Incident correlation is reference-based and optional. Preserve journey IDs for trade incidents and allow one incident to reference multiple journeys without merging them. |
| Evidence / Learn memory | telemetry feedback + knowledge evidence/memory stores; Telemetry/Knowledge owners | evidence refs, memory source event, proposal/event IDs, lineage maps | store-specific; no canonical retention | scopes vary | Rich copied context is not authoritative correlation. Store immutable evidence refs to producer records plus envelope; copied snapshots must declare source version/time. |

## Proven lifecycle samples

### Paper allocation-policy lifecycle

The executable fixture proves this ID chain:

```text
proposal-persona-{a,b,c}-e2e-002
  -> artifact-mpos-e2e-002 / reg-alloc-mpos-e2e-002
  -> approval-alloc-mpos-e2e-002
  -> dp-alloc-mpos-e2e-002
  -> runtime binding created for rt-alloc-mpos-e2e-002
  -> paper LEAN fills/telemetry
  -> lineage lookup by binding, plan, pool, artifact and persona-capital binding
```

This proves joins from the approved allocation artifact through paper
telemetry when all fixture IDs are retained. It does **not** prove a Persona
research record to individual trade join: there is no `research_journey_id`,
`journey_id`, decision ID on the signal, durable risk decision ID, ledger entry
ID or trade reconciliation ID. The path is paper only and explicitly avoids a
live broker route.

### Shioaji broker-sandbox lifecycle

The executable fixture proves this separate ID chain:

```text
trace-e2e-loop-037-data-fetch / ingest_run_id
  -> signal shioaji-filled-2454-037
  -> adapter local order_id
  -> broker trade id mock-e2e-loop-037-trade
  -> telemetry order_submitted event_id
  -> telemetry order_filled event_id
  -> Learn evidence and persona/institutional memory
```

The fill readback retains local and broker IDs, quantity, price, status and
explicit `sandbox` / no-real-capital flags. It cannot be joined without
fixture knowledge to an approval, deployment plan, runtime binding, risk
decision, ledger booking or reconciliation terminal. The ingest trace is not
shown propagating into broker submission or telemetry; no `journey_id` exists.

The samples are intentionally not presented as one lifecycle: current
contracts provide no evidence-safe join between them.

## Data-quality and terminal risks

| Risk | Current failure mode | Detection / contract requirement |
|---|---|---|
| Orphan | fill/telemetry has a signal or order ID but no plan/binding/journey; reconciliation drift may key only on telemetry event | Quarantine events missing environment-scoped journey plus producer identity; report orphan rate, never silently attach by time/symbol. |
| Duplicate | adapter retries, outbox replay and telemetry re-ingest use different local dedupe keys | Globally unique `event_id` plus producer idempotency key; uniqueness scoped by tenant/environment/producer; retain duplicate receipts. |
| Late event | broker callback or reconciliation arrives after a displayed terminal | Carry event time, received time, producer revision and causation; materializer accepts late append and recomputes with an auditable revision. |
| Conflicting terminal | cancel/reject can race with fill; fill can be mistaken for completion before booking/reconciliation | Define producer-specific terminal precedence; preserve both facts; journey completes only after execution, ledger and reconciliation terminals agree. |
| Ambiguous ID | local order ID, adapter order ID and broker trade ID may coincide in fixtures but are distinct namespaces | Envelope fields must be typed; reverse index key includes ID type, tenant and environment and returns ambiguity rather than guessing. |
| Environment bleed | schemas often omit environment while sandbox/paper meaning lives in metadata | Require `environment` (`paper`, `broker_sandbox`, `canary`, `live`) and capital-impact flags on creation and every emitted event. |

## Correlation envelope freeze input

`TJ-E2E-002` should version the exact domain contract. Every producer in the
matrix must at minimum propagate these semantics in `TJ-E2E-003`:

```json
{
  "schema_version": "trade-journey-envelope/1",
  "tenant_id": "...",
  "environment": "paper|broker_sandbox|canary|live",
  "research_journey_id": "rj_...",
  "journey_id": "tj_...",
  "correlation_id": "...",
  "trace_id": "...",
  "event_id": "...",
  "causation_event_id": "...",
  "producer": "service-name",
  "event_time": "...",
  "received_at": "...",
  "producer_revision": 1
}
```

Creation rule: Persona/research creates `research_journey_id`; the
signal/decision boundary creates `journey_id`. Downstream producers only copy
those IDs. Missing IDs are an explicit incomplete state, never regenerated
from timestamp, symbol, artifact or order IDs.

## Retention and ordering decision debt

The inspected producer contracts do not define one end-to-end retention
period. Deployment saga has the strongest ordering (`aggregate_id` plus
`sequence_no`, causal parent and idempotency key); telemetry is idempotent by
event ID; broker readback and most domain stores lack a cross-producer order.
Consequently `TJ-E2E-002` must freeze minimum journey/event/index retention and
ordering semantics before a materializer or SLO can make completeness claims.

## Verification

Focused commands for this inventory:

```sh
pytest -q tests/e2e/test_allocation_policy_to_paper_run.py
pytest -q tests/e2e/test_shioaji_adapter_filled_readback_memory_e2e.py
```

These tests validate the two cited samples. They do not upgrade either sample
into a canonical end-to-end journey.
