# BFF-B3-007 Implementation Handoff

Task: BFF-B3-007 - GET /bff/management/persona-intent redacted aggregate
Owner: Codex2
Reviewer: Claude
Status: ready for review
Date: 2026-05-23

## Delivered Scope

- Added read-role gated `GET /bff/management/persona-intent`.
- Composed redacted intent rows from persona traces, trainer sessions, and
  Agora sessions.
- Returned the standard Management aggregate envelope: `data`, `items`,
  `summary`, `page_info`, and `meta.surfaces.management_persona_intent`.
- Added `source_type`, `persona_id`, `status`, `intent`, `page_token`, and
  bounded `page_size` query handling.
- Preserved source surface metadata for persona traces, persona sessions,
  capability snapshots, teaching sessions, and Agora sessions.
- Redacted raw transcripts, message bodies, tool lists, and capability internals
  while exposing safe counts, identifiers, timestamps, status, and summaries.
- Added execute-plans BFF v1 path/type/fetch helpers and
  `managementClient.personaIntent.list()`.

## Validation

Commands run from `task/BFF-B3-007` on 2026-05-23:

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/tests/test_bff_b3_persona_intent.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py
python3 -m pytest services/control-plane/bff/tests/test_bff_b3_persona_intent.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

Results:

- Python compile passed.
- Focused backend and final live wiring contract tests passed: 10 passed, with
  3 existing `datetime.utcnow()` deprecation warnings from
  `services/control-plane/bff/read_store.py`.

## Notes

- No L1 architecture or policy document was changed.
- This repository checkout does not contain an execute-plans `package.json` or
  Vitest config, so the TypeScript client test was not runnable locally. The
  frontend route wiring is covered by the Python final live wiring contract and
  the committed execute-plans source/test updates.
