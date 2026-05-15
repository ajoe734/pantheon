# SD-CONSULT-001 Codex Review

Status: changes requested
Reviewer: Codex
Date: 2026-04-27

## Findings

1. **Smoke tests do not run from the repo root.**

   `services/consultation/smoke_test.py:8` inserts the service directory on
   `sys.path` and `services/consultation/smoke_test.py:10` imports `main` as a
   top-level module. `main.py` uses package-relative imports, so
   `python3 -m unittest services.consultation.smoke_test` fails with
   `ImportError: attempted relative import with no known parent package`.
   `services/consultation/run_smoke_logic.py:10` has the same issue importing
   `store`, which then imports `.models`.

2. **The governance gate handoff acceptance criterion is not implemented.**

   The service currently exposes request, participant lookup, transcript, memo,
   and target memo routes, but there is no first-class handoff model or endpoint
   carrying gate target, evidence refs, trace id, memo refs, and audit refs.
   `services/consultation/main.py:183` is the final route in this slice, and the
   models in `services/consultation/models.py` do not define a gate handoff
   record. This misses the required "governance gate handoff carries evidence
   refs and audit trace" behavior.

3. **Published memo immutability is not enforced by the persistence boundary.**

   `services/consultation/store.py:54` through `services/consultation/store.py:56`
   overwrites any memo by id. `services/consultation/main.py:170` through
   `services/consultation/main.py:180` publishes by mutating the same memo
   record, and there is no guard preventing a published memo from being replaced
   later through the store. The public API does not expose an update route yet,
   but the domain service boundary still needs an immutable or append-only
   publication rule because this store is the service-owned lifecycle boundary.

4. **Audit coverage is too thin for the claimed lifecycle.**

   `_emit_audit` is called for request creation only
   (`services/consultation/main.py:61` through `services/consultation/main.py:68`).
   Request submission, transcript event append, memo submission, memo publication,
   and any future gate handoff do not emit audit events. That leaves the
   committee debate, memo publication, and gate handoff without replayable audit
   trace.

## Verification

- `python3 -m unittest services.consultation.smoke_test` fails with an import
  error before executing the smoke cases.
- `python3 services/consultation/run_smoke_logic.py` fails with the same
  package-relative import issue.
- `python3 - <<'PY' ... from services.consultation.main import app ... PY`
  succeeds, so the service module can be imported as a package; the failures are
  in the smoke entrypoints and their import style.

## Required Fixes

- Make the smoke tests runnable from the repo root and use the same package
  import path as the service.
- Add a service-owned governance gate handoff record and endpoint carrying
  memo/evidence refs, trace id, and audit trace.
- Enforce published memo immutability or explicit append-only supersession at
  the store/service boundary.
- Emit audit events for all lifecycle transitions covered by the task
  acceptance criteria, and cover them in the smoke test.
