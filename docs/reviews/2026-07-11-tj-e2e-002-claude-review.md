# TJ-E2E-002 Claude Review

Reviewer: Claude
Date: 2026-07-11
Disposition: changes requested

## Scope Reviewed

- `services/control-plane/specs/trade_journey/contract.md`
- `services/control-plane/specs/trade_journey/trade_journey.schema.json`
- `services/control-plane/specs/trade_journey/correlation_envelope.schema.json`
- `services/control-plane/specs/trade_journey/research_journey.schema.json`
- `services/control-plane/specs/trade_journey/strategy_lifecycle.schema.json`
- `services/control-plane/bff/test_trade_journey_contract.py`
- `docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TJ-E2E-001-PRODUCER-CORRELATION-INVENTORY.md`
  (the approved TJ-E2E-001 dependency this task builds on)

The `TradeJourney` domain model, 14-stage roll-up machine, correction-event /
revision semantics, and cardinality rules are well specified and internally
consistent. All 8 contract tests pass and correctly exercise reject, cancel,
partial fill, replace-chain and reconciliation-variance (including the
variance-then-corrected revision-increment path). Redaction capabilities
(`account.read`, `governance.write`) are defined. This part of the deliverable
is solid.

## Blocking Finding

`correlation_envelope.schema.json` lists `journey_id` as an unconditionally
required top-level property. That contradicts this same contract's own
domain model and its approved upstream dependency:

- `contract.md` §3.1: "The `journey_id` **MUST** be generated at the
  **Signal/Decision engine boundary**" — i.e. it does not exist before stage
  7 (`signal_generation`) of the 14-stage machine.
- `contract.md` §5.1 nonetheless states "**Every** message passing through
  the trading system must include the `CorrelationEnvelope` carrying:
  `journey_id`, ..." with no pre-signal exception, and the published schema
  encodes that literally.
- TJ-E2E-001 (already reviewer-approved, listed as this task's dependency)
  explicitly requires pre-signal producers to use the same common envelope
  before a `journey_id` exists, e.g. Persona/OODA: "No research or trade
  journey ID. **Accept and emit the common envelope**; create
  `research_journey_id` at research-question creation and preserve causation
  IDs." The same applies to Research/source ingest, Evolution/optimizer,
  Ranking/promotion, Human gate, and Capital binding — all producers for
  `TradeJourney` stages 1–6 (`research_rationale` through
  `deployment_runtime`), none of which can supply a `journey_id`.

As published, any producer at those six stages cannot legally emit a
schema-conformant `CorrelationEnvelope`: they either fail validation (field
required but unknown) or would have to invent a `journey_id` early, which
`contract.md` itself forbids downstream and which contradicts the "no
invented completeness" principle in the parent gap spec. TJ-E2E-002's own
acceptance bullet is "Define required/optional correlation fields ..." —
getting the required/optional split right for pre-signal producers is this
task's job, not TJ-E2E-003's (TJ-E2E-003 is explicitly scoped to propagation
"from strategy/signal origin" onward, so it cannot silently absorb a
pre-signal fix).

Repro (pre-signal Persona/OODA envelope, matching TJ-E2E-001's producer
matrix, fails schema validation):

```
python3 -c "
import json, jsonschema
schema = json.load(open('services/control-plane/specs/trade_journey/correlation_envelope.schema.json'))
envelope = {
    'schema_version': 'trade-journey-envelope/1',
    'tenant_id': 'tenant-pantheon',
    'environment': 'paper',
    'research_journey_id': 'rj_e2e_002_sample',
    'correlation_id': 'corr-12345',
    'trace_id': 'trace-67890',
    'event_id': 'evt-1',
    'causation_event_id': 'evt-0',
    'producer': 'persona.ooda',
    'event_time': '2026-07-11T22:00:00Z',
    'received_at': '2026-07-11T22:00:01Z',
    'producer_revision': 1,
}
jsonschema.validate(instance=envelope, schema=schema)
"
```

fails with: `'journey_id' is a required property`.

## Required Changes

- Move `journey_id` out of `correlation_envelope.schema.json`'s top-level
  `required` array (keep it in `properties` so it validates once present).
  `research_journey_id` / `strategy_lifecycle_id` remain the correlation
  anchor before `journey_id` is minted.
- Add one clarifying sentence to `contract.md` §5.1 stating that `journey_id`
  is required from `signal_generation` onward and absent/omitted for
  producers at earlier stages, so TJ-E2E-003's propagation work has an
  unambiguous required/optional boundary to implement against.
- Add a contract test asserting a pre-signal envelope (no `journey_id`, only
  `research_journey_id`) validates against `correlation_envelope.schema.json`,
  so this class of regression is caught going forward (mirrors the gap that
  let this through: none of the current 8 tests exercise the envelope schema
  at all, only the `TradeJourney` schema).

## Verification Commands

- `python3 -m pytest services/control-plane/bff/test_trade_journey_contract.py -q` (8 passed)
- ad hoc `jsonschema.validate()` repro above (fails on current code, as documented)
