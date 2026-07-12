# AG-GAP-005: Contract honesty — resolve 501 routes and refresh compatibility manifest

## Scope

Two contract/implementation splits:

1. `agora_v1_1.openapi.yaml` promises workshop routes that are 501
   NOT_IMPLEMENTED stubs in `strategy_workshop/router.py:1430-1483`:
   `GET/POST /workshops/{id}/versions`, `POST /versions/{id}/select`,
   `GET /workshops/{id}/research-runs`, `GET /workshops/{id}/consultations`,
   `POST /workshops/{id}/conclude`. `POST /servant/reconcile` (v1_1 path) also
   has no implementation.
2. `docs/contracts/agora/dev-compatibility-manifest.json` is frozen at
   2026-06-21: `compatibility_status: "pending"`, frontend runtime commit is an
   all-zero placeholder, and coverage stops at v1_1 while the bundle family is
   at v1_5.

## Work

1. Per stub route, decide with the reviewer: implement now (versions/select
   are natural once AG-GAP-001 gives a durable store) or formally defer.
   Deferred routes must be annotated in the OpenAPI extension
   (`x-implementation-status: not_implemented`) and excluded from any
   required-capability list. No route may stay silently promised.
2. Regenerate `dev-compatibility-manifest.json`: real execute-plans runtime
   commit, bundle/openapi coverage through v1_5, and a truthful
   `compatibility_status`. Use `scripts/agora_compat_manifest.py`.
3. Keep the additive/frozen-bundle rules: no mutation of shipped bundles;
   changes ride a new extension or annotation layer.

## Acceptance

- Zero routes in any `agora_v1*.openapi.yaml` that return 501 without an
  explicit `x-implementation-status` annotation.
- Implemented routes have contract tests and a live 200 proof on dev.
- `scripts/test_agora_compat_manifest.py` green; manifest shows a real
  frontend runtime commit and no placeholder checksums.
- Post-deploy live curl proof recorded under `docs/deployment/evidence/ag-gap-005/`.

## References

- `services/control-plane/bff/agora/strategy_workshop/router.py:795-801,1430-1483`
- `services/control-plane/openapi/agora_v1_1.openapi.yaml`
- `docs/contracts/agora/dev-compatibility-manifest.json`
- `scripts/agora_compat_manifest.py`
