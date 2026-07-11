# PTJ-003 Claude Review

Reviewer: Claude
Date: 2026-07-11
Disposition: changes requested

## Scope Reviewed

- `services/persona/trade_reflection_pipeline.py`
- `services/persona/test_trade_reflection_pipeline.py`
- `services/persona/persona_trade_reflection.schema.json` and
  `services/persona/trade_lesson_candidate.schema.json` (PTJ-001 canonical
  contracts this task depends on)
- `services/persona/test_trade_reflection_contracts.py` (PTJ-001 contract
  fixtures)
- `docs/bff/execution-tasks/2026-07-11-persona-trade-journal/INDEX.md`

Fail-close behavior, immutable facts snapshotting, retry/DLQ bookkeeping, and
no-broker/no-policy-mutation authority all check out and are exercised by the
11 pipeline + contract tests. The blocking problem is that the pipeline's own
output does not satisfy the canonical `PersonaTradeReflection` /
`TradeLessonCandidate` schemas that PTJ-001 locked and that PTJ-004/PTJ-005
are depending on as the wire contract. `test_trade_reflection_pipeline.py`
never validates `process()` output against the schema (only ad hoc dict-key
assertions), so this gap was not caught by CI.

## Blocking Findings

1. `TradeReflectionPipeline._artifact()` emits top-level keys the schema does
   not allow. `persona_trade_reflection.schema.json` sets
   `"additionalProperties": false`, but the artifact adds `request_id`,
   `supporting_episode_ids`, `attempt`, and `hindsight_guard`, none of which
   are in the schema's `properties`. Validating real pipeline output against
   the schema fails immediately with "Additional properties are not allowed".
   Repro:

   ```
   python3 -c "
   import json, jsonschema
   from services.persona.trade_reflection_pipeline import TradeReflectionPipeline, ReflectionRequest
   class Provider:
       name='p'; model='m'
       def reflect(self, *, facts, trigger):
           return {'expected_vs_actual': {'thesis': 'x'}, 'attribution': 'process',
                   'counterfactuals': [{'alternative_action': 'a','estimated_impact':'b','assumptions':'c'}],
                   'lesson_candidates': [{'scope':'s','proposed_change':'p','confidence':0.5}]}
   a = TradeReflectionPipeline(Provider()).process(ReflectionRequest(
       request_id='r1', persona_id='pa', trade_episode_ids=('e1',), trigger='episode_closed',
       facts={'status':'closed'}))
   schema = json.load(open('services/persona/persona_trade_reflection.schema.json'))
   jsonschema.validate(instance=a, schema=schema)
   "
   ```

   fails with: `Additional properties are not allowed ('attempt',
   'hindsight_guard', 'request_id', 'supporting_episode_ids' were
   unexpected)`.

2. Counterfactual marking permanently breaks schema conformance. The
   schema's `counterfactuals[]` items require exactly
   `alternative_action`/`estimated_impact`/`assumptions` with
   `additionalProperties: false`, but `_artifact()` unconditionally injects
   `is_counterfactual` and `uncertainty` into every counterfactual entry.
   This fails for every possible provider response, not just the test
   fixture — it is a structural bug, not a data issue.

3. `lesson_candidates[]` are both missing a required field and carrying
   disallowed ones. The schema requires `expiry` on every lesson candidate
   entry, but `_artifact()` never sets it. At the same time it injects
   `review_state` and `mutation_authority`, which are not in the embedded
   lesson-candidate schema used inside `persona_trade_reflection.schema.json`
   (that schema's `lesson_candidates[]` items are also
   `additionalProperties: false`). This also fails for every input.

4. `expected_vs_actual` is passed through unfiltered from the provider
   (`dict(generated.get("expected_vs_actual", {}))`), but the schema requires
   all six sub-fields (`thesis`, `entry_quality`, `exit_quality`, `sizing`,
   `timing`, `risk_adherence`) with `additionalProperties: false`. Nothing in
   the pipeline enforces or defaults the other five, so any provider that
   only fills in a subset (as the test `Provider` does) produces a
   non-conformant artifact.

## Required Changes

- Add a test that runs `jsonschema.validate()` on real `process()` output
  (not a hand-authored fixture) against
  `services/persona/persona_trade_reflection.schema.json`, covering at least
  one `episode_closed` and one `scheduled_pattern` case, so this class of
  drift is caught going forward.
- Bring the artifact shape back into conformance with the PTJ-001 contract:
  either drop `request_id`/`attempt`/`hindsight_guard` from the persisted
  record (move them to an internal/log-only envelope if they're needed for
  ops) or get the schema itself amended through the canonical-doc change
  process before depending on it — do not let this task silently diverge
  from a contract another task (PTJ-001) already locked.
- Stop injecting `is_counterfactual`/`uncertainty` into counterfactual
  entries in a way that breaks the schema, and set `expiry` (and drop
  `review_state`/`mutation_authority`, or route them through
  `TradeLessonCandidate`'s separate schema/lifecycle instead of the embedded
  one) on `lesson_candidates`.
- Ensure `expected_vs_actual` always has all six required keys before
  `_artifact()` returns (default missing ones to `None`/`"unknown"` rather
  than omitting them), since PTJ-004/PTJ-005 will read this schema as the
  wire contract.

## Verification Commands

- `python3 -m pytest services/persona/test_trade_reflection_pipeline.py services/persona/test_trade_reflection_contracts.py -q`
- ad hoc `jsonschema.validate()` repro above (fails on current code)
