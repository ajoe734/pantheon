# L12-IMIT-001 — Tenant-Safe Imitation Loop Authority and Failure Recovery

Task ID: `L12-IMIT-001`
Phase: `Twelve Loop Remediation / Imitation`
Owner: `Claude`
Reviewer: `Codex2`
Loop: `human_imitation_shadow_evaluation`

## Delivery

| | |
|---|---|
| First implementation PR | [#4235](https://github.com/ajoe734/pantheon/pull/4235), head `1719e100`, merged to `dev` as `7ae3adbb441b66ea17fd6d98db0d831b11600ced` at 2026-07-27T01:21:43Z |
| Branch CI Gate for #4235 | success — runs [30229566603](https://github.com/ajoe734/pantheon/actions/runs/30229566603) (push) and [30229568740](https://github.com/ajoe734/pantheon/actions/runs/30229568740) (pull_request); Commit trailers, Runtime mirror guard, Smoke acceptance all green |
| Follow-up PR | [#4236](https://github.com/ajoe734/pantheon/pull/4236) — this evidence packet plus one owner-found tenant-isolation fix (see below) |
| Anchor commits | `f85bf549` Agora dataset source · `6f985c9f` scheduling and backlog leases · `507f51d3` scheduling proofs · `92863858` tenant-safe authority and recovery |

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
execute rather than skip. No live Pantheon config, compose file, or running
service was modified.

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
collected 171 items

services/policy-learning/tests/test_l12_imit_001_authority_and_recovery.py [ 12%]
services/policy-learning/tests/test_l12_imit_001_real_dataset_scheduling.py [ 24%]
services/policy-learning/tests/test_policy_learning_compose_activation.py  [ 25%]
services/policy-learning/tests/test_policy_learning_gateway_routing.py     [ 30%]
services/policy-learning/tests/test_policy_learning_http_service.py        [ 31%]
services/policy-learning/tests/test_policy_learning_postgres_store.py      [ 32%]
services/policy-learning/tests/test_policy_learning_shadow_eval_scheduler.py [ 41%]
services/research/imitation/test_agora_dataset_source.py                   [ 48%]
services/research/imitation/test_bc_trainer.py                             [ 50%]
services/research/imitation/test_dataset_builder.py                        [ 64%]
services/research/imitation/test_eval_metrics.py                           [ 67%]
services/research/imitation/test_policy_validation_gate.py                 [ 72%]
services/research/imitation/test_preference_models.py                      [ 82%]
services/research/imitation/test_trajectory_models.py                      [ 88%]
services/research/imitation/test_trl_bridge.py                             [ 92%]
services/foundation/tests/test_persistence_posture.py                      [100%]

======================= 171 passed, 1 warning in 40.37s ========================
```

Without `PANTHEON_TEST_POSTGRES_DSN` the same suite is `167 passed, 4 skipped`;
the four skipped cases are exactly the real-Postgres proofs listed below.

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
and tenant header.

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

- No `docker-compose*.yml`, `env/*`, or other configuration file was changed.
- No live Pantheon service, database, or runtime state was written; the
  Postgres proofs used a throwaway container and dropped every schema they
  created.
- No registry, deployment, or RuntimeBinding surface was touched.
- The pre-existing policy-learning job and capability routes are outside this
  task's boundary and are unchanged.
