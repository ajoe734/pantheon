# AG-DYNUI-FULL-002 Live Workshop Cards And Readiness BFF

Owner: Codex
Reviewer: Codex2
Date: 2026-07-05

This is the owner-scoped artifact for wave 1 of the Agora DYNUI full
production recovery packet. It records the Strategy Workshop BFF routes added
to make cards and readiness live from scoped workshop state.

## Delivered Scope

- Added `GET /bff/agora/workshops/{workshop_id}/cards`.
- Added `GET /bff/agora/workshops/{workshop_id}/readiness`.
- Added `POST /bff/agora/workshops/{workshop_id}/readiness/reassess`.
- Backed readiness and typed card projection with `MemoryWorkshopStore` and
  `PostgresWorkshopStore` methods, not frontend fixtures.
- Kept workshop reads scoped by `tenant_id` and `user_id`; unknown workshops
  return `404`, cross-tenant reads return `403`.
- Kept readiness reassessment as a guarded mutation requiring `If-Match` and
  `Idempotency-Key`.
- Reused `services.research.strategy_spec.completeness` readiness semantics
  for state-map blocking items.
- Covered both production `OperatorIdentity` and test-injected dict identity
  shapes in backend tests.
- Updated Agora v1.3 capability manifest and bundle hash for the live cards
  and reassess route prefixes.

## Local Validation

```sh
python3 -m py_compile \
  services/control-plane/bff/agora/strategy_workshop/store.py \
  services/control-plane/bff/agora/strategy_workshop/router.py

pytest -q services/control-plane/bff/tests/test_agora_strategy_workshop.py
```

Result:

- `65 passed, 116 warnings in 102.93s`
- Warnings are existing FastAPI `on_event` deprecation warnings from main app
  startup/shutdown registration.

## Live Proof Status

Post-deploy live curl proof is still pending until this Pantheon PR is merged
and deployed to the dev BFF. Required probes after deploy:

```sh
curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/readiness

curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/cards
```

Expected:

- readiness response has `data.gates` for `preliminary_research`,
  `full_validation`, and `trading_room`;
- a sufficiently complete scoped state map with a Strategy Registry ref can
  reach `data.highest_ready_gate = "trading_room"`;
- cards response returns typed card objects derived from scoped workshop
  events, completeness snapshots, and readiness assessment state.

## Residual Risks

- A workshop without a completeness snapshot returns a live but not-ready
  assessment; downstream tasks still need to create or restore real workshop
  completeness evidence before production E2E can prove Trading Room handoff.
- `AG-DYNUI-FULL-003` still owns materializing ready workshop strategies into
  the Trading Room aggregate.
