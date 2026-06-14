# Round 3 — Results

**Executed:** 2026-06-14 (UTC). **Target:** dev BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`.

## Breadth sweep (parameterized GET routes)

139 parameterized GET paths probed with a benign non-existent id and stub auth:

| Status | Count | Meaning |
|---|---|---|
| 404 | 110 | clean not-found (correct) |
| 200 | 21 | param not a hard lookup key (collection/sub-resource) |
| 400 | 2 | id format rejected (correct) |
| 422 | 2 | additional required params (expected) |
| 410 | 2 | intentionally retired |
| **500** | **2** | **unhandled exception — defect** |

The two 500s: `/bff/persona-league/{persona_id}` and
`/bff/management/persona-league/{persona_id}` (same handler, two routes).

## Findings

### F5 — persona-league detail 500s on unknown id; the 404 branch is itself broken (FIXED this branch)

Reproduced via TestClient with full traceback:

```
File ".../bff/main.py", line 48973, in bff_persona_league_detail
    ErrorCode.OBJECT_NOT_FOUND,
AttributeError: type object 'ErrorCode' has no attribute 'OBJECT_NOT_FOUND'
```

Root cause: the not-found branch raised `ErrorCode.OBJECT_NOT_FOUND`, but
`ErrorCode` (`services/control-plane/bff/models.py`) has no such member — the
only not-found member is `RESOURCE_NOT_FOUND`. So the *error path itself*
threw `AttributeError`, which the global handler turned into a 500
`INTERNAL_ERROR`. Operators querying an unknown persona-league id got a server
error instead of a 404; any monitoring keyed on 5xx would page falsely.

Fix: `ErrorCode.OBJECT_NOT_FOUND` → `ErrorCode.RESOURCE_NOT_FOUND`. Both routes
now return `404 RESOURCE_NOT_FOUND` (verified via TestClient).

### F5-general — static guard against invalid ErrorCode references

Generalized the class: scanned every `ErrorCode.<NAME>` reference in `main.py`
against the live `ErrorCode` enum (26 members). After the F5 fix, **zero**
invalid references remain. Locked by a static regression test so any future
typo'd ErrorCode member fails CI instead of 500ing at runtime.

## Tests

`test_persona_league_detail_not_found.py` — 2 passed:
- unknown id on both routes → 404 `RESOURCE_NOT_FOUND`;
- no invalid ErrorCode references in `main.py`.

## Net

H1: one defect class (F5, 2 routes) found and fixed — the parameterized GET
surface now degrades cleanly. H2: PASS, locked by static test. The 21 "200 on
unknown id" routes are collection/sub-resource shapes where the path segment is
not a strict lookup key — expected, not defects.
