# Evidence Summary: LIFECYCLE-PROJ-BFF-001

- Task: Serve Trade Journey and loop-run reads from Postgres
- Owner: Codex
- Reviewer: Claude
- Status: implementation complete; independent review pending
- Code head covered by this evidence: `7c7b1c65ea4e9b50ca5d22a0ad3c9d6297d64ec9`

This task adds an explicitly selected, disabled-by-default BFF Postgres reader
for the relational projection schema delivered by `LIFECYCLE-PROJ-STORE-001`.
It does not cut traffic over to Postgres, change the writer/migration, or allow
JSON fallback to be labelled as Postgres truth.

## Delivered contract

- `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=json` (the default) preserves the
  current JSON/materializer reader.
- Only `postgres` activates the new reader. A malformed backend setting,
  missing DSN, absent token secret, missing driver, or database failure is
  fail-closed as unavailable; it never selects JSON instead.
- Every Postgres journey, timeline, resolve, metrics, controller, and loop-run
  read requires both `tenant_id` and `environment`. Loop routes add optional
  scope parameters only for the selected Postgres path and derive/validate the
  effective scope from the authenticated identity.
- Journey and timeline pages use signed keyset cursors. The HMAC payload binds
  cursor kind, tenant, environment, sort, and normalized filters, so a token
  cannot be reused for another scope/filter/sort or edited.
- Journey queries select at most `page_size + 1`; BFF validation caps page size
  at 200. The query carries the tenant/environment predicates before filters
  and ordering, matching the indexes from the store task.
- DTO routes for list, detail, timeline, graph, evidence, resolve, metrics,
  and v5 loop list/detail preserve their existing envelopes while exposing
  controller freshness from relational state.

## Verification

Run from the repository root with the verification venv used for this task:

```bash
/tmp/pantheon-bff-verify/bin/python -m pytest -q \
  services/control-plane/bff/test_tj_e2e_005_trade_journeys_read_api.py \
  -k lifecycle_proj_bff

/tmp/pantheon-bff-verify/bin/python -m pytest -q \
  services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
  -k loop_runs

/tmp/pantheon-bff-verify/bin/python -m pytest -q \
  services/control-plane/bff/test_lifecycle_projector_readiness.py

python3 -m py_compile \
  services/control-plane/bff/trade_journey_projection_store.py \
  services/control-plane/bff/trade_journeys.py \
  services/control-plane/bff/read_store.py \
  services/control-plane/bff/main.py

sha256sum -c \
  docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-BFF-001/SHA256SUMS
```

Recorded results at the code head:

- reader security/routing/query-contract tests: **3 passed**;
- existing loop-run contract subset: **8 passed**;
- existing lifecycle readiness suite: **3 passed**;
- syntax, whitespace, and checksum validation: **pass**.

The unpinned BFF requirements currently install FastAPI/Starlette versions
where `fastapi.routing.APIRoute` no longer subclasses `starlette.routing.Route`.
Consequently the pre-existing static-route test
`test_tj_e2e_005_static_siblings_are_registered_before_journey_id_param_route`
fails its `isinstance(Route)` collection check in this disposable environment,
while 37 neighbouring tests pass. This task does not change route order or
alter that compatibility test; review should run the repository's pinned CI
environment before treating that dependency mismatch as resolved.

## Checksum scope

`SHA256SUMS` covers the code and focused contract-test files at the stated code
head. It intentionally excludes this evidence manifest to avoid self-reference.
