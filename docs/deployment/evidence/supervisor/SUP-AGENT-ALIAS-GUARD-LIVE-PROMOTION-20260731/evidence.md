# SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731 evidence

Task: Promote merged alias reassignment guard into live supervisor runtime

Current owner: `Codex`

Current reviewer: `Claude`

Review decision: **pending**

Current delivery state: **live promoted; awaiting exact-head independent
review, PR merge, and owner closeout**

## Outcome first

The governed rollback-safe transaction promoted the live supervisor to exact
accepted `origin/dev` commit
`0bd7cf8842a622fb40eca125f490d4073b3a1044` and returned exit code `0` with
state `promoted`.

The candidate supervisor is the only live supervisor:

| Field | Exact value |
|---|---|
| PID / start ticks | `3301275` / `22907439` |
| Root | `/home/lupin/pantheon-ci-deploy/command-runtimes/0bd7cf8842a622fb40eca125f490d4073b3a1044` |
| HEAD | `0bd7cf8842a622fb40eca125f490d4073b3a1044` |
| Tree | `d795eb696d0e3e2d49053f712ef353f3b1086ab8` |
| Entrypoint | exact candidate root `.orchestrator/supervisor.py` |
| Runtime SHA environment | `0bd7cf8842a622fb40eca125f490d4073b3a1044` |
| Status root | `/home/lupin/pantheon` |
| Post-promotion discover | exit `0`, all 13 invariants passed |

The task is not yet complete. This manifest must be committed and reviewed at
the exact final PR head by `Claude`; PR #4431 must then merge to `dev` before
the owner may run governed `done`.

## Source chain

The promoted source contains every required repair in order:

- alias reassignment guard: PR #4430, merge `012dab969…`;
- legacy mutable cwd / split entrypoint support: PR #4724, merge `8b77e779…`;
- cross-root same-SHA rollback non-alias guard: PR #4726, merge `37ee6c5d…`;
- collision-safe rollback destination: PR #4728, reviewed head `09378b8fd…`,
  merge `0bd7cf884…`.

Immediately before mutation, `git ls-remote origin refs/heads/dev` still
resolved exactly to `0bd7cf884…`. Both the alias guard and collision-safe
repair were proven ancestors of that candidate.

## Source verification

The checkout-scoped Python distribution was provisioned first. The current
promotion helper passed:

- collision-safe rollback materialization focus: `9 passed, 324 deselected`;
- complete `scripts/test_promote_supervisor_runtime.py`: `333 passed in
  98.23s`;
- candidate command-root validator: passed with exact HEAD, tree, canonical
  origin, matching basename, and clean Git status.

## Pre-promotion fail-closed readback

The exact live-config discover-only call ran at
`2026-08-10T19:23:04.474966Z`. As required for the legacy split process, only
these regular-mode gates failed:

- `incumbent_supervisor_process_identity_immutable`;
- `governed_supervisor_launch_contract_immutable`.

Every deciding non-layout gate passed:

- singleton incumbent PID `1393542`, start ticks `21477145`;
- healthy heartbeat and no loop error;
- readable canonical status, state, and provider documents;
- authoritative projection caught up with equal
  `1ec33712ed6d8a326a2afe5d2019a01f2b132e80dea45934e6c0873067bd7601`
  hashes;
- 14 worker records, one active queue event, 29 worktree leases, no duplicate
  active worker, and no parity reason;
- provider readiness passed;
- no orphaned in-progress task.

The incumbent was the previously recorded legacy split layout: mutable cwd
`dev-root` at commit `0305c861…`, with entrypoint from
`command-runtimes/5877b644…`. No mutation occurred during candidate creation
or discover-only.

## Collision-safe rollback

The direct SHA-derived destination was already occupied:

`/home/lupin/pantheon-ci-deploy/command-runtimes/0305c861f54c4082060120afdfbc012622e5ac0a`

The merged PR #4728 contract materialized the captured incumbent into the
separate rollback parent:

`/home/lupin/pantheon-ci-deploy/rollback-command-runtimes/0305c861f54c4082060120afdfbc012622e5ac0a`

The rollback checkout is clean, has canonical origin, HEAD `0305c861…`, tree
`249eecac…`, and device/inode `2049/13480162`. The occupied direct checkout is
device/inode `2049/13894090`. The transaction therefore preserved the
cross-root non-alias rule: it did not delete, move, overwrite, or guess-reuse
the occupied root.

## Governed transaction

The transaction used:

```text
--promote --bootstrap-mutable-incumbent
--postcheck-timeout 600 --poll-interval 1
--lock-timeout 30 --termination-timeout 15
```

The durable state history was:

1. `prepared`
2. `admission_locked`
3. `intent_recorded`
4. `incumbent_terminated`
5. `candidate_config_installed`
6. `candidate_launched`
7. `candidate_verifying`
8. `promoted`

Restart intent bound old PID `1393542` to target `0bd7cf884…` under the
promotion lock and runtime-admission lock. Candidate PID `3301275` launched at
`2026-08-10T19:27:06.570265Z`. Neither `original_failure` nor
`rollback_failure` was recorded, so the prepared rollback runtime was not
launched.

The external durable transaction file is:

`/home/lupin/pantheon-ci-deploy/runtime/promotion-evidence/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731-0bd7cf8842a6-20260810T1924Z.json`

It is 19,938 bytes with SHA-256
`53de8dcc178db71689639661fe8afa7e142e9b28129473bf1fa03b5d28e3ae4b`.
A review-sized transcription is committed as
[`raw/promotion-20260810T1924Z.json`](raw/promotion-20260810T1924Z.json).

## Config transaction truth

The original 2026-07-31 task baseline `728a6d90…` was historical and had
already changed before this dispatch. The exact live pre-transaction bytes
were SHA-256 `61c8aed0…`.

The governed transaction intentionally replaced the supervisor command root
in the live config. The installed candidate config is SHA-256 `54ec4bef…`.
That hash stayed exact across all three candidate observations and the final
discover-only readback. The rollback variant `afebcf72…` was prepared but
never installed.

No `sync-dev-root.sh`, `check_config_drift.py --fix`, manual config edit,
provider config change, canonical task/queue JSON edit, or scheduler policy
change ran.

## Three-loop acceptance

The transaction accepted three distinct successful markers, all from PID
`3301275`, exact candidate root/tree, config `54ec4bef…`, with no invariant
failure:

| Observation | Successful loop marker |
|---|---|
| `2026-08-10T19:28:45.386159Z` | `2026-08-10T19:28:28Z` |
| `2026-08-10T19:30:10.416625Z` | `2026-08-10T19:30:04Z` |
| `2026-08-10T19:33:19.070194Z` | `2026-08-10T19:31:51Z` |

Each observation retained projection hash `1ec33712…`. The worker/queue hash
changed as normal coordination state advanced, but every observation reran the
lease-parity invariant and found no failure.

## Final readback

The post-promotion discover-only call completed at
`2026-08-10T19:34:05.146582Z` with exit `0`,
`eligible_for_promotion=true`, and all 13 invariants green. It proved:

- one live candidate process with exact PID generation, cwd, entrypoint, Git
  identity, config, and environment contract;
- immutable governed relaunch contract bound to `origin/dev`, canonical status
  root, external task-state journal, and task worktree root;
- healthy lifecycle, held supervisor lock, and fresh successful loop;
- authoritative projection caught up with equal hashes and no error;
- 14 worker records, two queue events, 29 leases, no duplicates, and no parity
  reasons;
- Antigravity installed, authenticated, local CLI capable, and selected model
  `gemini-3.6-flash-low`; provider configuration was not changed.

Machine-readable review truth is in [`evidence.json`](evidence.json).
