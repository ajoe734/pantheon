# L12-IMIT-001 — Tenant-Safe Imitation Loop Authority and Failure Recovery

Task ID: `L12-IMIT-001`
Phase: `Twelve Loop Remediation / Imitation`
Owner: `Codex` (closeout owner; implementation and evidence through PR #4245
were authored by `Claude`)
Reviewer: `Codex2`
Loop: `human_imitation_shadow_evaluation`

## Delivery

| | |
|---|---|
| First implementation PR | [#4235](https://github.com/ajoe734/pantheon/pull/4235), head `1719e100`, merged to `dev` as `7ae3adbb441b66ea17fd6d98db0d831b11600ced` at 2026-07-27T01:21:43Z |
| Branch CI Gate for #4235 | success — runs [30229566603](https://github.com/ajoe734/pantheon/actions/runs/30229566603) (push) and [30229568740](https://github.com/ajoe734/pantheon/actions/runs/30229568740) (pull_request); Commit trailers, Runtime mirror guard, Smoke acceptance all green |
| First evidence PR | [#4236](https://github.com/ajoe734/pantheon/pull/4236), head `3eefc3fe`, merged to `dev` as `4cb436f80f82657cbd58a8527a3ca374f41253aa` at 2026-07-27T01:35:23Z — evidence packet plus one owner-found tenant-isolation fix (see below) |
| Remediation PR | [#4237](https://github.com/ajoe734/pantheon/pull/4237), head `6c76909f`, merged to `dev` as `d8c925f3636b0aece66b156c7f63896c5eb6d127` at 2026-07-27T02:48:13Z — closes the default-compose gap the first review returned on (see [Returned review](#returned-review-default-compose-authority)) |
| Branch CI Gate for #4237 | success — runs [30232997381](https://github.com/ajoe734/pantheon/actions/runs/30232997381) (push) and [30232999094](https://github.com/ajoe734/pantheon/actions/runs/30232999094) (pull_request); Commit trailers, Runtime mirror guard, Smoke acceptance all green |
| Second evidence PR | [#4238](https://github.com/ajoe734/pantheon/pull/4238), head `a4ba6685`, merged to `dev` as `d65b87eecac09ffafa5653cf05ebeb5d526546d5` at 2026-07-27T02:55:03Z — the head Codex2 independently re-reviewed and returned on |
| Second remediation PR | [#4242](https://github.com/ajoe734/pantheon/pull/4242), head `d18a3633`, merged to `dev` as `ddd8dc5709cf45e2dd8814fd20567afda2f8d48e` at 2026-07-27T13:02:19Z — closes the published-service-token gap the re-review returned on (see [Returned re-review](#returned-re-review-a-published-credential-authenticated-a-deployment)) |
| Branch CI Gate for #4242 | success — runs [30268355592](https://github.com/ajoe734/pantheon/actions/runs/30268355592) (push) and [30268359805](https://github.com/ajoe734/pantheon/actions/runs/30268359805) (pull_request); Commit trailers, Runtime mirror guard, Smoke acceptance all green |
| Final reviewed evidence PR | [#4245](https://github.com/ajoe734/pantheon/pull/4245), head `ca620a61`, merged to `dev` as `3330e7ae955b20317f588659ad8d8f28daa43fb8` at 2026-07-27T13:12:11Z |
| Branch CI Gate for #4245 | success — runs [30269086268](https://github.com/ajoe734/pantheon/actions/runs/30269086268) (push) and [30269094352](https://github.com/ajoe734/pantheon/actions/runs/30269094352) (pull_request); Commit trailers, Runtime mirror guard, Smoke acceptance all green |
| Anchor commits | `f85bf549` Agora dataset source · `6f985c9f` scheduling and backlog leases · `507f51d3` scheduling proofs · `92863858` tenant-safe authority and recovery · `35538b0b` compose service credential wiring · `7aa5a7a0` default-compose loop proofs · `4f6e2498` published service token refusal |

### Closeout reconciliation

Codex2 independently approved the exact reviewed evidence head `ca620a61` at
2026-07-27T14:18:01Z after reproducing the production-placeholder refusal,
running the throwaway-PostgreSQL suite (190 passed), checking the schema and
all 15 source hashes, and confirming PRs #4235, #4236, #4237, #4238, #4242,
and #4245 were merged. Canonical audit event
`ai-status-event-e5df15df837bdae87d758487db357af14fdd2ed298113ca4fbdf2f7f383e0902`
records that `review_approved` decision.

Human/Ops reassigned only the approved task-owner role from Claude to Codex at
2026-07-27T16:22:07Z, retaining Codex2 as reviewer. Canonical audit event
`ai-status-event-8482fd53772180807d407ac82acae3109cbd63cc94f84219c7d06602a3338951`
records the assignment. The implementation and evidence authorship below stays
historically attributed to Claude; this reconciliation changes no product
bytes, proof result, maturity claim, or review decision.

### Returned review: default-compose authority

The first independent review failed closed on the one thing the fixtures could
not show. `docker compose --profile policy-learning-shadow-eval-scheduler
config` rendered **no** `POLICY_LEARNING_SERVICE_TOKEN`, no
`POLICY_LEARNING_SERVICE_TENANTS`, and no `POLICY_LEARNING_AGORA_TENANT_ID` for
either `policy-learning-svc` or the scheduler sidecar. Strict inbound authority
therefore reported `configured: false`, the sidecar sent neither
`Authorization` nor `X-Tenant-Id`, and its start-up restart and every tick
answered 401 — so on a default `docker compose up` the product loop discovered
and trained on nothing. The proof suite passed anyway because it injected its
own credentials.

PR #4237 closes it:

| Change | File |
|---|---|
| API and scheduler interpolate **one** service credential and **one** Agora tenant scope from the same variables; `POLICY_LEARNING_SERVICE_TENANTS` defaults to the scheduler's tenant so renaming the tenant cannot leave the API authorizing the old one; `AGORA_DATASET_STORE_*` is passed through for a dedicated read role | `docker-compose.yml` |
| The published compose token is treated as *unconfigured* once the persistence posture is staging or prod, and `authority_configuration()` reports `service_token_scope` (`local_dev_default` / `deployment_secret` / `none`) | `services/policy-learning/inbound_authority.py` |
| The sidecar refuses to start without a credential and a tenant (`SchedulerConfigurationError`, exit 2) instead of looping unauthenticated ticks | `services/policy-learning/scheduler_worker.py` |
| Deployment override documented | `.env.example` |

The compose default now renders, for both services:

```text
POLICY_LEARNING_SERVICE_TOKEN   = pantheon-local-policy-learning-service
POLICY_LEARNING_AGORA_TENANT_ID = pantheon-local
PANTHEON_PERSISTENCE_POSTURE    = dev
```

plus `POLICY_LEARNING_SERVICE_TENANTS = pantheon-local` on the API.

### Returned re-review: a published credential authenticated a deployment

The re-review at `a4ba6685` confirmed the merged delivery — schema, checksums,
all 15 source hashes, the rendered compose credential and tenant scope, and a
fresh throwaway-Postgres suite — and then failed closed on one thing the
previous remediation had left half-done.

`inbound_authority.py` refused exactly one string under an enforced posture:
`LOCAL_DEV_SERVICE_TOKEN`, the value compose interpolates. But `.env.example`
publishes a *second* credential for the same variable,
`POLICY_LEARNING_SERVICE_TOKEN=replace-me-policy-learning-service-token`, and an
operator who copies that file into a staging or production stack without editing
the line is running on a value anyone with a checkout can read. Codex2's
reproduction, with `PANTHEON_PERSISTENCE_POSTURE=production` and that exact
published placeholder:

```text
authority_configuration() → configured: true, service_token_scope: deployment_secret
resolve_authority()       → policy-learning-service, tenant pantheon-local, ACCEPTED
```

That directly contradicts the trusted-direct-route and fail-closed deployment
authority claim this task exists to establish.

PR #4242 closes it:

| Change | File |
|---|---|
| `PUBLISHED_SERVICE_TOKENS` pins both credentials this repository publishes — the compose default and the exact `.env.example` placeholder; `is_published_service_token()` additionally refuses unedited fill-me-in shapes (`replace-me`, `changeme`, `placeholder`, `example`, `sample`, `dummy`, `todo`, `your-`, and bare words like `secret`); `service_token()` downgrades any of them to `""` on a staging/prod posture; `authority_configuration()` reports the new `published_placeholder` scope | `services/policy-learning/inbound_authority.py` |
| Route-level negative for the exact `.env.example` value across all 15 protected routes with an operator-minted positive control on the same routes and posture, a drift guard that reads the tracked `docker-compose.yml` and `.env.example`, and eight parametrised placeholder shapes | `services/policy-learning/tests/test_l12_imit_001_default_compose_loop.py` |
| Operator comment states that both the compose default and this placeholder are refused on staging/prod | `.env.example` |

The same reproduction on this head:

```text
authority_configuration() → configured: false, service_token_configured: false,
                            service_token_scope: none
resolve_authority()       → REFUSED 401 AUTH_TOKEN_FORMAT
```

An operator-minted secret still authenticates every one of those routes on the
same `staging` and `prod` postures, so the refusal is the placeholder being
rejected and not the service being broken.

### Owner-found fix carried by #4236

`worker/restart` released *every* tenant's expired leases and then filtered the
report down to the caller's tenant. Releasing an already-ownerless lease is
harmless work, but it is still one tenant's routine restart writing to another
tenant's backlog rows, which contradicts the tenant-bound route guarantee this
task exists to establish. `release_expired_leases` now takes a `tenant_id` and
`worker/restart` passes the authenticated tenant, so a tenant recovers only its
own orphans. Proven by
`test_one_tenants_restart_does_not_recover_another_tenants_orphans`.

## Verification Summary

The imitation loop now schedules from tenant-scoped Agora `DatasetVersion`
content over an authenticated, tenant-bound route surface, keeps its candidate
backlog in a durable authority with an atomic `(tenant, tick, dataset version)`
key, and recovers correctly from a worker crash, a duplicate tick, and a
damaged backlog file. No path substitutes seed data for real data, and no path
lets a candidate reach a registry, deployment stage, or RuntimeBinding.

### Test Execution

Run against a throwaway `postgres:16-alpine` instance so the Postgres proofs
execute rather than skip. `docker compose config` renders the compose file and
starts nothing. No live Pantheon service, database, env file, or runtime state
was written; `docker-compose.yml` and `.env.example` are changed by this task
because the returned review required it.

```bash
docker run -d --rm --name l12-imit-pg-proof \
  -e POSTGRES_PASSWORD=… -e POSTGRES_USER=… -e POSTGRES_DB=… \
  -p 127.0.0.1:55433:5432 postgres:16-alpine

PANTHEON_TEST_POSTGRES_DSN=postgresql://…@127.0.0.1:55433/… \
  python -m pytest \
    services/policy-learning \
    services/research/imitation \
    services/foundation/tests/test_persistence_posture.py
```

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /tmp/pantheon-worker-worktrees/pantheon/l12-imit-001
configfile: pytest.ini
plugins: anyio-4.14.2, asyncio-1.4.0
collected 190 items
services/policy-learning/tests/test_l12_imit_001_authority_and_recovery.py . [  0%]
......................                                                   [ 12%]
services/policy-learning/tests/test_l12_imit_001_default_compose_loop.py . [ 12%]
................                                                         [ 21%]
services/policy-learning/tests/test_l12_imit_001_real_dataset_scheduling.py . [ 21%]
...................                                                      [ 31%]
services/policy-learning/tests/test_policy_learning_compose_activation.py . [ 32%]
..                                                                       [ 33%]
services/policy-learning/tests/test_policy_learning_gateway_routing.py . [ 33%]
.......                                                                  [ 37%]
services/policy-learning/tests/test_policy_learning_http_service.py ...  [ 38%]
services/policy-learning/tests/test_policy_learning_postgres_store.py .. [ 40%]
                                                                         [ 40%]
services/policy-learning/tests/test_policy_learning_shadow_eval_scheduler.py . [ 40%]
..............                                                           [ 47%]
services/research/imitation/test_agora_dataset_source.py ............    [ 54%]
services/research/imitation/test_bc_trainer.py ...                       [ 55%]
services/research/imitation/test_dataset_builder.py .................... [ 66%]
....                                                                     [ 68%]
services/research/imitation/test_eval_metrics.py ....                    [ 70%]
services/research/imitation/test_policy_validation_gate.py ..........    [ 75%]
services/research/imitation/test_preference_models.py .................  [ 84%]
services/research/imitation/test_trajectory_models.py ..........         [ 90%]
services/research/imitation/test_trl_bridge.py ......                    [ 93%]
services/foundation/tests/test_persistence_posture.py .............      [100%]
======================= 190 passed, 1 warning in 48.86s ========================
```

Without `PANTHEON_TEST_POSTGRES_DSN` the same suite is `185 passed, 5 skipped`;
the five skipped cases are exactly the real-Postgres proofs listed below.

### Negative control for the compose wiring

The new proofs are only worth their claim if they fail without the fix:

```bash
git show HEAD~1:docker-compose.yml > docker-compose.yml
python -m pytest services/policy-learning/tests/test_l12_imit_001_default_compose_loop.py -q
#  → 6 failed, 1 passed
git checkout HEAD -- docker-compose.yml
```

The single pass is `test_offline_compose_renderer_matches_docker_compose_config`,
which asserts the two renderings agree rather than asserting any wiring.

### Negative control for the published-token refusal

Same discipline for the re-review remediation — revert only
`inbound_authority.py` and the new assertions must all fail:

```bash
git show HEAD~1:services/policy-learning/inbound_authority.py > services/policy-learning/inbound_authority.py
python -m pytest services/policy-learning/tests/test_l12_imit_001_default_compose_loop.py -q
#  → 10 failed, 6 passed
git checkout HEAD -- services/policy-learning/inbound_authority.py
```

The 10 failures are exactly the assertions this remediation adds:
`test_every_repository_published_service_token_is_one_the_service_refuses`,
`test_the_env_example_placeholder_cannot_authenticate_a_deployment`, and the
eight parametrised cases of
`test_unedited_placeholder_shapes_are_refused_on_a_deployment_posture`.
`test_the_published_compose_token_is_refused_on_a_deployment_posture` still
**passes** against the pre-fix module, which is the point: the old rule covered
the compose default and nothing else. The module was restored immediately and
`git status --short` re-checked.

## Blocker-by-Blocker Evidence

### 1. Atomic `(tenant, tick, dataset version)` authority and collision-free IDs

`services/policy-learning/store.py` derives the candidate id from a
length-prefixed digest of the triple (`candidate_id_for`) instead of a
`sic-<date>-<backlog length>` counter, and creation goes through
`create_candidate_if_absent`:

- Postgres: `INSERT … ON CONFLICT DO NOTHING RETURNING payload` against a
  `UNIQUE (dedupe_key)` index, so a concurrent duplicate tick loses the insert
  and reads back the winner.
- JSON: the dedupe check and the insert happen inside one `flock`-held
  read-modify-write.

`services/policy-learning/main.py` no longer performs its own read-then-write
duplicate check.

| Proof | Test |
|---|---|
| Six threads ticking the same `tick_id` concurrently create each candidate exactly once | `test_concurrent_duplicate_ticks_create_exactly_one_candidate_each` |
| Same tick id + same dataset version id in two tenants produce different candidate ids | `test_same_dataset_version_in_two_tenants_is_not_deduped_together` |
| A repeated tick reports the same ids it skipped | `test_shadow_eval_tick_idempotent_same_tick_id` |
| Duplicate insert against real Postgres returns `created is False` | `test_real_postgres_two_process_crash_restart_and_reclaim` |

### 2. Lease-expiry fencing at settle

`settle_candidate` compares the presented lease token against the stored one
under `FOR UPDATE` (Postgres) or the file lock (JSON) and raises
`LeaseLostError` on mismatch. `requeue_candidate` is new and refuses
(`LeaseHeldError` → HTTP 409) while a live lease exists, closing the hole where
`retry`/`replay` could requeue a leased candidate and produce two runs that both
believed they were authoritative.

| Proof | Test |
|---|---|
| A revived worker cannot overwrite the new owner's result | `test_settling_a_lost_lease_is_rejected` |
| Retry/replay is refused while the lease is live; the live owner still settles | `test_replay_is_refused_while_a_worker_still_holds_the_lease` |
| A SIGKILLed worker's token is fenced after the survivor reclaims | `test_real_postgres_two_process_crash_restart_and_reclaim` |

### 3. Durable Postgres default candidate authority

`resolve_candidate_authority` in `store.py` resolves, in order: an explicit
`POLICY_LEARNING_CANDIDATE_AUTHORITY`; `POLICY_LEARNING_STORE_BACKEND=postgres`;
**product dataset mode with a configured DSN**; otherwise JSON, reported
truthfully as non-durable. Default compose passes
`POLICY_LEARNING_STORE_BACKEND=json` *and* `DATABASE_URL`, so the candidate
backlog — the loop's authoritative record — lands in Postgres without any
compose or env change. `/health` and `/readyz` report the resolution
(`candidate_authority`, `durable`, `reason`) with the DSN stripped.

| Proof | Test |
|---|---|
| `backend=json` + `DATABASE_URL` resolves to `postgres` / `durable: true` / `product_mode_durable_default`, writes nothing to the JSON path, and a restarted process with an empty data dir still sees the row | `test_real_postgres_is_the_default_candidate_authority_in_product_mode` |

### 4. Authenticated tenant-scoped Agora resolution with no seed fallback

`services/policy-learning/agora_dataset_authority.py` now refuses to open an
anonymous connection (`agora_authority_unauthenticated`) and pins the
cross-owner session `read_only`, so policy-learning's read of the Agora owner's
`agora.agora_dataset_records` is an authenticated, non-writing read as required
by `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` §3.2. Discovery remains
independent of `POLICY_LEARNING_STORE_BACKEND`. There is no seed, fixture, or
synthesized fallback in product mode; a lookup failure produces a `degraded`
candidate carrying an explicit `seed_fallback_used: false`.

| Proof | Test |
|---|---|
| A DSN with no identity raises `agora_authority_unauthenticated` | `test_agora_resolution_requires_an_authenticated_connection` |
| Against real Agora rows, one tenant sees only its own versions; another tenant's version is `dataset_version_not_found`; a `DELETE` on the session raises `ReadOnlySqlTransaction` and all rows survive | `test_real_postgres_agora_resolution_is_tenant_scoped_and_read_only` |
| Cross-tenant resolution degrades with no seed leak | `test_cross_tenant_dataset_resolution_degrades_without_seed` |
| An unreachable authority degrades every candidate with no seed leak | `test_unreachable_authority_degrades_every_candidate_without_seed` |
| Discovery is backend-independent | `test_dataset_authority_resolves_independently_of_policy_store_backend` |

### 5. Trusted tenant-bound direct tick/claim/replay/readback routes

`services/policy-learning/inbound_authority.py` authenticates every direct
imitation-loop route through the shared `services/runtime_auth_inbound`
validator or an in-cluster service token, and binds the request to the tenant
named by `X-Tenant-Id`. A body `tenant_id` that disagrees is refused rather
than silently overwritten; a `dataset_ref` naming another tenant is rejected
rather than re-stamped; cross-tenant reads answer 404 rather than 403 so ids
cannot be enumerated. Policy-learning JWT settings are namespaced and never
inherit `PANTHEON_RUNTIME_*`. `scheduler_worker.py` presents the service token
and tenant header, and refuses to start without both — compose supplies them
(§7).

| Proof | Test |
|---|---|
| All 15 direct routes answer 401 unauthenticated | `test_every_direct_route_rejects_an_unauthenticated_caller` |
| All 15 direct routes answer 401 for a forged token | `test_direct_routes_reject_a_forged_service_token` |
| Missing `X-Tenant-Id` → 400 `TENANT_REQUIRED` | `test_tick_requires_a_tenant_scope` |
| Conflicting tenant headers → 400 `TENANT_HEADER_CONFLICT` | `test_conflicting_tenant_headers_are_refused` |
| Tenant outside the caller's authority → 403 `TENANT_FORBIDDEN` | `test_authenticated_caller_outside_its_tenant_authority_is_refused` |
| Body tenant smuggling → 403 `TENANT_PAYLOAD_MISMATCH` | `test_body_tenant_cannot_smuggle_a_second_scope_past_the_header` |
| Cross-tenant `get` / `readback` / `governance` / `promote` / `retry` / `replay` → 404, id absent from the body, victim record untouched | `test_candidate_reads_are_invisible_across_tenants` |
| Verified JWT without tenant claims → 403 `TENANT_SCOPE_UNCONFIGURED`; wrong role → 403; tampered signature → 401 | `test_a_verified_jwt_without_tenant_authority_is_refused` |
| A runtime-manager secret does not authenticate a policy-learning caller | `test_inbound_authority_does_not_inherit_the_runtime_manager_secret` |
| No repository-published credential — compose default, the exact `.env.example` placeholder, or any unedited fill-me-in shape — authenticates any of the 15 routes on a `staging` or `prod` posture, while an operator-minted secret does | `test_the_env_example_placeholder_cannot_authenticate_a_deployment` (§7) |
| Cross-tenant `dataset_refs` rejected, not re-stamped | `test_explicit_dataset_ref_for_another_tenant_is_rejected_not_restamped` |
| Two tenants ticking and claiming concurrently stay disjoint | `test_concurrent_cross_tenant_ticks_and_claims_stay_isolated` |
| Real Postgres, three tenants claiming concurrently, zero overlap | `test_real_postgres_concurrent_cross_tenant_claims_never_overlap` |
| One tenant's restart recovers only its own orphaned leases | `test_one_tenants_restart_does_not_recover_another_tenants_orphans` |

### 6. No registry / deployment / runtime mutation

Candidates are stamped `production_training: fail_closed`,
`experiment_approval_gate: required`, `runtime_effect: none`. `promote` always
answers 409 and mutates nothing; the governance report lists
`registry_write`, `runtime_binding_mutation`, `deployment_stage_change`,
`capital_binding`, and `order_routing` as denied authorities.

| Proof | Test |
|---|---|
| Governance gates unsatisfied; refused promotion mutates nothing | `test_candidate_cannot_be_promoted_or_reach_runtime` |
| A processed candidate carries no runtime authority token | `test_processed_candidate_carries_no_runtime_authority` |

### 7. The default compose stack reaches tenant-scoped Agora data through auth

`services/policy-learning/tests/test_l12_imit_001_default_compose_loop.py` is
the proof the returned review asked for. It takes the environment
`docker-compose.yml` renders — not a fixture — and runs the sidecar against the
real FastAPI route surface. The module explicitly cancels the package-wide
autouse credential fixture from `tests/conftest.py`, so the only credentials in
play are the compose ones, and it routes `scheduler_worker`'s own `urllib`
requests into the app so the sidecar's real header construction is what
authenticates.

Only two values are supplied by the test, and they are the two a deployment
supplies from its own cluster: the mounted data directory and the database
location.

| Proof | Test |
|---|---|
| `docker compose --profile policy-learning-shadow-eval-scheduler config` renders the same token, the same tenant, and the same posture for both services, and the API's tenant list contains the scheduler's tenant | `test_default_compose_gives_api_and_scheduler_one_credential_and_tenant_scope` |
| The offline renderer the end-to-end proofs run on is byte-identical to that command's output for both services | `test_offline_compose_renderer_matches_docker_compose_config` |
| An operator tenant override widens the API's authorized list with it, and an explicit list still wins | `test_operator_tenant_override_widens_both_services_together` |
| On the compose default alone, start-up restart recovery, the tick and the claim cycle all carry `Authorization: Bearer` and `X-Tenant-Id`, none answers ≥ 400, `dataset_source` is `agora_dataset_version`, `seed_fallback_used` is `false`, and both candidates process | `test_default_compose_environment_authenticates_the_whole_sidecar_loop` |
| Real Postgres: the compose environment discovers and trains on real `<schema>.agora_dataset_records` rows for the compose tenant, ignores a neighbouring tenant's rows in the same table, resolves the candidate backlog to the durable Postgres authority, and keeps full lineage | `test_default_compose_environment_trains_on_real_tenant_scoped_agora_rows` |
| Without the wiring the sidecar refuses to start (`SchedulerConfigurationError`, `main()` → 2) and, if started anyway, its tick is 401 with no candidate created | `test_a_compose_scheduler_without_the_wiring_is_refused_before_it_ticks` |
| The published compose token is refused under a `staging` or `prod` posture (`configured: false`, tick 401), while a deployment-supplied secret is accepted | `test_the_published_compose_token_is_refused_on_a_deployment_posture` |
| Every `POLICY_LEARNING_SERVICE_TOKEN` value the tracked `docker-compose.yml` and `.env.example` actually publish is in `PUBLISHED_SERVICE_TOKENS`, and that constant is exactly that set — not a stale subset and not a widened superset | `test_every_repository_published_service_token_is_one_the_service_refuses` |
| The exact published `.env.example` placeholder answers 401 on all 15 protected routes under `staging` and `prod` (`service_token_configured: false`, `service_token_scope: none`, `configured: false`), while an operator-minted secret authenticates the same 15 routes on the same postures — and the placeholder stays refused even while that secret is the configured one | `test_the_env_example_placeholder_cannot_authenticate_a_deployment` |
| Unedited fill-me-in shapes (`REPLACE_ME_…`, `changeme`, `change-me-please`, `your-service-token-here`, `example-token`, `placeholder`, `secret`, `TBD`) resolve to no credential under `prod` and stay usable under `dev` | `test_unedited_placeholder_shapes_are_refused_on_a_deployment_posture` |
| The compose declaration itself cannot regress, and the staging stack keeps the published token fail-closed | `test_compose_wires_the_imitation_loop_credential_and_tenant_scope`, `test_staging_compose_keeps_the_published_dev_credential_fail_closed` |

The compose default token and the `.env.example` placeholder are published
values, deliberately. They are development conveniences, and
`inbound_authority.py` downgrades **both** — and any other unedited fill-me-in
shape — to "no credential" as soon as `PANTHEON_PERSISTENCE_POSTURE` (or
`PANTHEON_ENV`) is a staging or production mode, which
`docker-compose.staging-full.yml` pins. A real deployment therefore fails closed
rather than running on a credential anyone can read off GitHub. Because the
service runs in a container with no `.env.example` to consult, the refused
values are constants; the drift guard above is what keeps those constants
honest. Both the published-credential risk and the fail-closed cost of the
prefix rule are recorded as residual risks with their containment.

## Crash, Restart, and Corruption Evidence

### Real two-process Postgres crash → restart → reclaim

`test_real_postgres_two_process_crash_restart_and_reclaim` runs against a real
Postgres with real OS processes:

1. Two child processes claim concurrently through `FOR UPDATE SKIP LOCKED`;
   the claimed sets are disjoint and together cover the backlog.
2. A third child claims the whole backlog under a 2 s lease and hangs.
3. The parent sends `SIGKILL` — no shutdown hook, no settle, no lease release
   (child exit code `-9`).
4. While the lease is live the survivor claims nothing.
5. After expiry, `release_expired_leases` returns exactly the dead worker's
   candidates, the survivor reclaims them all at `attempt_count == 2`, and the
   dead worker's stale lease tokens raise `LeaseLostError`.
6. The survivor settles them once; a brand new store handle (a restarted
   container) reads back the same authoritative rows.

### Corruption and restart

`_read_candidates` raises `CandidateStoreCorrupt` for a truncated file, a
non-object document, and non-candidate entries; a missing or empty file remains
a genuine empty backlog. `main.py` maps that to HTTP 503
`CANDIDATE_STORE_CORRUPT` on every affected route. A corrupt backlog can never
present as an idle, healthy loop, and a tick will not overwrite the damaged file
with a fresh backlog. `_write_candidates` now writes to a pid-scoped temporary
file and `os.replace`s it, so a crash mid-write cannot truncate the backlog for
a peer process.

| Proof | Test |
|---|---|
| Reads and claims answer 503, a tick does not overwrite the damaged file, and restoring it recovers the original work | `test_a_corrupt_backlog_is_an_outage_not_an_empty_backlog` |
| Wrong-shape documents refused; missing/empty file is a genuine empty backlog | `test_a_backlog_of_the_wrong_shape_is_also_refused` |
| A concurrent reader never observes a partial backlog across 70 writes | `test_backlog_writes_are_atomic_under_a_concurrent_reader` |

## Scope and Non-Goals Held

- `docker-compose.yml` and `.env.example` **are** changed, because the returned
  reviews required the default compose product loop to reach tenant-scoped Agora
  data through auth and required the published `.env.example` credential to be
  refused on a deployment posture. The `.env.example` change in #4242 is comment
  text only — the published value itself is unchanged, because changing it would
  hide the very failure mode the refusal now proves. No other compose or env file
  was touched; `docker-compose.staging-full.yml` is only read, to assert that its
  inherited credential stays fail-closed.
- The refusal rule is scoped to policy-learning. The sibling
  `services/capital/inbound_authority.py` and
  `services/training-session/inbound_authority.py` are **not** changed by this
  task; whether they carry the same exposure is outside this task's boundary and
  is not claimed either way here.
- `scheduler_worker.py` is unchanged by #4242. A sidecar handed a published
  credential under an enforced posture still starts and then takes 401s from the
  API, which is the same fail-closed outcome as before, just not a fail-fast one.
- No live Pantheon service, database, or runtime state was written; the
  Postgres proofs used a throwaway container and dropped every schema they
  created, and `docker compose config` renders the file without starting
  anything.
- No registry, deployment, or RuntimeBinding surface was touched.
- The pre-existing policy-learning job and capability routes are outside this
  task's boundary and are unchanged.
