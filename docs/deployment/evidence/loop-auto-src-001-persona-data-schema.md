# Evidence: LOOP-AUTO-SRC-001 — Add persona data requirement schema

Task: LOOP-AUTO-SRC-001
Owner: Claude
Reviewer: Codex
Date: 2026-06-27

## Acceptance criteria verification

### 1. Persona declares required_data_sources with dataset, market, cadence, and source class

**Delivered:**
- `services/control-plane/persona/required_data_sources.schema.json` — JSON Schema defining `RequiredDataSource` with all required fields (`dataset`, `market`, `cadence`, `source_class`) plus optional `connector_candidates` and `policy_gates`.
- `services/control-plane/persona/persona_registry.py` — `RequiredDataSource` dataclass added; `Persona.required_data_sources: List[RequiredDataSource]` added as a first-class field with construction-time validation.

**Test evidence (run: `python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py'`):**
```
Ran 134 tests in 0.215s
OK
```

### 2. Schema carries connector_candidates and policy_gates

**Delivered:**
- `connector_candidates: List[str]` — ordered list of connector IDs the reconciler may use.
- `policy_gates: List[str]` — gate IDs that must pass before a binding is considered active.
- Both fields are present in `required_data_sources.schema.json` and the `RequiredDataSource` dataclass.

### 3. Seed labels no longer count as live data source binding

**Delivered:**
- `source_class` is an enum: `live_push | live_pull | seed_only`.
- `DataSourceClass.SEED_ONLY.is_live_binding()` returns `False`.
- `Persona.live_data_sources()` filters out `seed_only` entries explicitly.
- `Persona.seed_only_sources()` exposes seed-labelled entries separately.
- Test `test_seed_only_source_not_counted_as_live_binding` confirms a persona with only seed sources returns `len(live_data_sources()) == 0`.

## Files changed

| File | Change |
|---|---|
| `services/control-plane/persona/required_data_sources.schema.json` | New — `RequiredDataSource` JSON schema |
| `services/control-plane/persona/persona_registry.schema.json` | Added `required_data_sources` field with `$ref` to schema above |
| `services/control-plane/persona/persona_registry.py` | Added `DataSourceCadence`, `DataSourceClass`, `RequiredDataSource`; added `required_data_sources` field to `Persona` |
| `services/control-plane/persona/test_persona_data_sources.py` | New — 27 unit tests covering all acceptance criteria |
| `docs/03/SD-02_persona_governance.md` | Added §4.7 documenting `RequiredDataSource` and seed_only semantics |

## Test run

```
$ python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py' -v
...
Ran 134 tests in 0.215s
OK
```

All pre-existing tests continue to pass. No regressions.

## Non-goals (not done in this task)

- Source provisioning reconciler (LOOP-AUTO-SRC-002 owns connector/schedule creation)
- SourceHealth wiring into BFF (LOOP-AUTO-SRC-004)
- Live service smoke or runtime evidence (no runnable service yet for this schema change)
