# PPL-ALLOC-010 — Per-persona attribution identity chain

## Owned layer

- Reconcile persona execution identity in the BFF read store.
- Compose PM-12 persona attribution and league evidence from execution
  `runtime_id` telemetry.
- Keep Persona Fleet performance persona-owned and telemetry-backed.
- Prove the contract with task-scoped BFF tests.

## Identity contract

The authoritative chain is:

`persona_id -> persona_capital_binding_id -> runtime_binding_id -> runtime_id -> telemetry summary`

The identifiers are not aliases. In particular, telemetry is queried by the
execution `runtime_id`, not by the runtime-binding record ID and not by a stale
session binding ID.

Resolution precedence is:

1. A canonical persona-capital binding owner overrides a stale or missing
   persona owner on the runtime record.
2. When the binding owner is absent, an exact persona-registry declaration may
   fill the owner only when the typed reference resolves to exactly one
   persona. Both snake_case and service camelCase declarations are accepted.
3. Ambiguous references, shared pools, names, and market labels never assign a
   persona. Such runtimes remain `unassigned`.

## Seed isolation

Market-default personas may retain their own registry performance metadata.
Market context hydration for another persona is restricted to market scope,
asset classes, data-source context, and research context. Runtime, capital,
rank, review, OODA, risk, work, and performance fields are persona-owned and
must not be copied from a same-market seed persona.

Persona Fleet prefers real telemetry summaries over registry or league
performance fields and labels the selected source. A persona with no telemetry
and no own performance evidence reports unavailable values rather than zeroed
market-seed performance.

An operational persona may still appear as a fallback identity row in the
Operations read model when formal attribution is absent, but its unavailable
performance remains null. Draft personas with no bound evidence remain
unavailable rather than being promoted to fallback confidence.

## Hosted baseline evidence (2026-07-13 UTC)

- Persona Fleet exposed 23 personas with 0 formal and 23 informal attribution
  rows; all ranking rows had zero evidence coverage.
- Nine legacy per-persona paper runtimes had fresh telemetry and exact
  persona-specific runtime/persona-capital binding references but no runtime
  `persona_id`.
- Two devloop runtimes had no provable persona reference and must remain
  `unassigned`; their 6,953 trades must not be fabricated as persona evidence.
- Stale sessions exposed old `rb-*` references while current telemetry was
  keyed by `runtime-persona-...-paper`, so session-only lookup was insufficient.
- The nine persona telemetry summaries currently contain identical zero
  performance values. This task makes those rows formal, eligible, and covered;
  live score differentiation still requires distinct upstream telemetry.

## Validation contract

- Canonical binding ownership corrects both stale and missing runtime owners.
- No-session paper personas are discovered through their authoritative runtime
  bindings.
- By-persona performance attribution is formal and references exact runtime
  IDs without moving unresolved devloop evidence out of `unassigned`.
- PM-12 ranking rows are eligible with nonzero coverage and use real telemetry
  metrics, including nested summary payloads.
- Persona Fleet reports telemetry performance and never same-market seed
  performance for a custom persona.
- After dev deployment, verify:
  - `GET /bff/management/performance-attribution/by-persona`
  - `GET /bff/management/persona-league/rankings`
  - `GET /bff/management/persona-fleet`

## Not changing

- No runtime, capital, deployment, promotion, or live-trading mutation.
- No heuristic assignment of unresolved runtime telemetry.
- No synthetic score differentiation when upstream telemetry values are equal.
