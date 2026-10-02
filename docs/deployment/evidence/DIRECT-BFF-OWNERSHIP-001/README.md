# Direct BFF ownership reconciliation

Scope: operator-authorized direct implementation, without supervisor or auto-worker dispatch. This is a bounded repair in the six-area closeout, **not overall completion**.

## Source and reproduced failure

- Backend base: `799e40fc8c380137515e69b59b3fe7b617ee2c77` (`origin/dev`).
- Frontend baseline (separate repository): `379456f058d6cd604269f7a52154211d3df99629`.
- Before repair, `test_mutation_route_ownership_mapping_contract` failed: 42 unmounted rows after its two-row exception filter, plus one omitted mounted route. The unfiltered inventory had 249 rows; the live application had 206 normalized mutation routes.
- Removed 44 stale inventory rows, not production handlers: retired legacy command endpoints (2), rebalance approval proxies (2), intervention/sentinel mutations (8), and Agora dead surfaces (32). Their source removal already landed; this patch does not reintroduce endpoints or relax the exact mounted-route comparison.
- Added the servant research proposal route introduced by PR #6035. Its row describes the source-observed `AgoraResearchService.create_workshop_plan` and `make_research_plan_store`, tenant/user/endpoint/key idempotency scope, and draft readback. The store factory can select memory or Postgres; the inventory is not proof that a deployment selected durable storage. `outbox_subject: null` is deliberate: draft creation emits a workshop event, not a durable dispatch outbox. This does not claim research execution has moved out of BFF.
- Removed the architecture test's ad-hoc two-route filter so it validates the same complete manifest as the CLI.
- Added negative regressions for missing, stale and duplicate rows, plus the draft-only proposal inventory contract.

## Executed validation

Backend command (Python 3.12, pytest 9.1.1):

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --disable-warnings \
  scripts/test_check_product_ownership.py \
  services/control-plane/bff/test_architecture_boundaries.py \
  services/control-plane/bff/tests/test_bff_test_architecture.py \
  services/control-plane/bff/test_read_store_service_clients.py \
  services/control-plane/bff/tests/test_journal_runtime_contract.py
```

Result: **78 passed, 2 subtests passed, 187 warnings**, exit 0, 97.48 seconds. `git diff --check`: exit 0. No assertions were skipped or weakened. The first architecture run with ambient plugin autoload exceeded its 120-second command budget; the explicit-plugin baseline and combined validation completed.

Unchanged frontend baseline, tested in its own clean worktree:

- `npm ci --include=dev --ignore-scripts --no-audit --no-fund`: exit 0.
- `npm run typecheck`: exit 0 (full `tsconfig.app.json`).
- `NODE_ENV=test npm test`: **212 files / 2,294 tests passed**, exit 0, 251.28 seconds.
- `VITE_BFF_MODE=live VITE_BFF_BASE_URL=https://api.dev.mvl-cap.tw VITE_BFF_FALLBACK=strict VITE_BFF_REAL_WRITES=false npm run build`: exit 0.
- `NODE_ENV=test PANTHEON_CONTRACT_ROOT=<backend-worktree> npm run test:contract`: exit 0.

Ambient `NODE_ENV=production` initially omitted dev dependencies and selected React's production runtime during the test command; these were environment setup failures, not frontend defects. The first contract command without `PANTHEON_CONTRACT_ROOT` failed before comparison; the explicit current-backend comparison passed. A 300-second frontend attempt timed out; the completed rerun above is the acceptance result.

## Unfulfilled wider obligations

The former simplification chain was superseded, not completed, on 2026-09-29. Do not resume its frozen PR #5998 or count archived `done/superseded` records as implementation evidence. Existing receipt-only and research-single-owner work retains its scope; no overlapping source was changed or dispatched here.

At read-only observation during this repair, the hosted manifest still served frontend `d2d3a0c7b0a1bf0943e01e129174e235f39d8039` with backend `f3e267c1fd700e9452470e43d73da3c3dd57146e`. Backend `/bff/version` agreed but reported `image_digest: unknown`; the frontend reported `VITE_BFF_REAL_WRITES=true`. A separate pre-existing nonprod deployment run `37006617043` was in progress. These observations do **not** accept current-dev deployment, safe write defaults, twelve-loop closure, or end-to-end business flows. No hosted write, deployment, data migration, credential change or second release was initiated here.

Compose/profile convergence, MinIO retirement/data inventory, retained dependency/research disposition, end-to-end owner persistence and the current twelve-loop evidence chain remain separate unfinished obligations. This patch repairs the reproducible ownership gate only. Source inventories prove routing coverage, not durable transaction semantics or hosted readiness.
