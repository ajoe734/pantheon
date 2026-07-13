# LOOP-PROD-LEASE-001 — Protected shared-dev mutation lease and payload isolation

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `64937b9652dfd2ad3df97d49517dc90e580d2cacc65c16cae1fce6a5a2a51cc8`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 1 |
| Fleet lane | `protected-dev-environment-lease` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | lane-local mutation controls without one protected lifetime |
| Target maturity | proven-live |

## Product outcome

Dev deploy、OpenClaw repair、public smoke 與 Agora persistence 都必須持有同一個
controller-issued environment lease；候選 payload 不得繼承 runner cloud credentials，
且 release lease 前必須證明 local/remote cgroup 與 mutation payload 都為零。

## Dependencies

- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-WORKER-001`

## Loop scope

- `promotion_deployment`
- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `.github/workflows/nonprod-deploy.yml`
- `scripts/deploy_nonprod_vm.sh`
- `scripts/dev_environment_cgroup_guard.py`
- `scripts/run_gcloud_remote_mutation_scope.sh`
- `scripts/dev_agora_restart_persistence_smoke.sh`
- `scripts/test_dev_environment_lease_deploy_contract.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-LEASE-001`

## Acceptance

- all four mutation lanes bind exact lease id, candidate SHA, VM, zone, project, job, and payload identity
- deploy runner credentials and Cloud SDK state are absent from candidate processes; direct gcloud is unavailable inside the payload boundary
- OpenClaw and Agora use distinct one-shot sockets and cannot replay or substitute another lane's authorization
- secrets travel only through a non-logged protected channel and never through argv, debug logs, staging lanes, or committed artifacts
- timeout, cancellation, supersede, signal, and runner crash perform TERM-to-KILL escalation and wait for local and remote zero-member proof
- lease release is rejected until controller-authored cleanup ACK and exact target readback exist
- duplicate, wrong lease/SHA/lane, replay, network, and cleanup-failure negatives pass
- exact PR, merge SHA, checks, target-dev proof, review, and checksummed evidence are archived

## Required proof

- workflow/YAML, shell, hash, and containment tests
- target-dev candidate mutation and cleanup drill
- local and remote cgroup membership evidence
- secret/credential negative evidence
- independent exact-head review

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-LEASE-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- rebase after strict-auth workflow delivery and preserve every auth boundary
- never run an unleased shared-dev mutation as qualification evidence
- a workflow receipt without zero-member cleanup is not terminal success
- reviewer verifies exact hashes and lane isolation on the proposed head
