# L12-TRUTH-001 — Operator truth readback

Owner `Claude2` · Reviewer `Antigravity` · Branch `task/L12-TRUTH-001` · Base `dev`

Machine-readable manifest: [`evidence.json`](evidence.json) (digest in
[`evidence.sha256`](evidence.sha256)).

## What changed

The loop catalog used to say every one of the twelve canonical loops had
`controller_contract.status: not_implemented` with a null controller name. That
was false for three loops, and it was false in the direction that hides a gap
*and* disarms a guard: the BFF's controller-identity check compares a runtime
record's `controller_name` against the catalog's expected name, so a null name
made that check unreachable.

1. **Catalog now names the controllers that exist.** `source_ingestion`,
   `strategy_distillation`, and `alpha_replication` declare
   `status: implemented` with their real controller name, desired/actual state
   queries, restart behavior, and liveness metric, plus
   `owner.current_controller_owner`. The other nine loops stay
   `not_implemented` with null fields, because that is the truth.

2. **Catalog-to-runtime exactness, both directions.**
   `test_loop_catalog_controller_contract_matches_runtime_implementation` binds
   each declared controller to its module and its Compose service and asserts
   the module really contains the state path and identity-refresh symbols the
   declared restart behavior describes. It also asserts the reverse: every
   Compose service that sets `PANTHEON_CONTROLLER_NAME` must be declared in the
   catalog, so a new controller cannot land undeclared.

3. **All-loop record conformance and tenant-scoped readback.**
   `test_every_canonical_loop_record_conforms_and_reads_back_tenant_scoped`
   builds one controller record per canonical loop, runs each through
   `services/loop-control/conformance.py`, serves them through the durable-store
   read path, and asserts the BFF shows desired presence, controller health,
   last success, last failure, downstream actual state, and provenance for all
   twelve — under the authenticated tenant and environment, with a
   foreign-tenant record proven not to leak.

4. **Nothing is promoted.** No loop's `maturity.current` moved.
   `test_loop_catalog_stops_at_implemented_until_hosted_evidence_is_admitted`
   fails closed if any loop declares `reconciled`/`proven-live` maturity, a
   `proven_live` contract, or present live evidence. In the all-loop readback
   every loop reports `accepted_live_liveness: false` and
   `eligible_live_truth_levels: []` even though its record is current, correctly
   identified, and accepted as a controller observation. Hosted admission stays
   with `L12-HOSTED-001`.

5. **Operator-visible coverage.** `/bff/v5/loop-inventory` meta now carries
   `controller_contract_coverage` and each entry carries
   `controller_contract_declaration`, so an operator can see "controller
   declared, liveness not admitted" without inferring it.

## Commands run

```
python -m pytest tests/test_loop_catalog_registry.py -q                      # 19 passed
python -m pytest services/control-plane/bff/test_loop_inventory_read_model_contract.py \
                 services/control-plane/bff/test_loop_health_read_model_contract.py -q   # 25 passed
```

Five negative controls were run to confirm the new guards are not vacuous; each
one made exactly the intended test fail and was then reverted. They are listed
in `evidence.json` under `validation.commands`.

## Out of scope, recorded not hidden

- Nine canonical loops still have no controller. The catalog says so.
- `services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py` has one
  failing case (403 `tenant_scope`). It was reproduced at the task base commit
  `b1527e868` before any change here, its file is outside this task's artifact
  scope, and branch CI does not run it.
- The catalog-to-runtime binding table lives in the contract test rather than in
  the catalog, because `docs/deployment/loop-catalog.schema.json` forbids extra
  properties on `controller_contract` and that schema is outside this task's
  artifact scope.
- `tests/test_loop_catalog_registry.py` was repaired: it pinned its
  "controller not implemented" rejection case to `loops[0]`, which now declares
  an implemented controller. It selects a still-`not_implemented` loop instead.
