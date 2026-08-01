# SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731 evidence

Task: Promote merged alias reassignment guard into live supervisor runtime

Current owner: `Codex2`

Current reviewer: `Antigravity`

Review decision: **pending**

Current delivery state: **blocked at fail-closed preflight; no mutation performed**

## Outcome first

The earlier evidence no longer describes the live runtime. The read-only
snapshot started at `2026-08-01T15:05:39Z`; supplemental lease and provider
queries followed it. The sole supervisor was PID `2933948` in
`/home/lupin/pantheon-ci-deploy/dev-root`, at commit
`cbb36ff1fe385f3bc2690124ff22d8edc0056896`. It was not running the accepted
alias-guard target `012dab969455e7146f2437159d7d38fc5904a195`.

No restart, signal, config writer, canonical JSON edit, or queue edit was run in
this revalidation. Promotion is blocked until both live lease safety and the
governed automatic-rollback transaction are available.

The structured sample is
[`raw/revalidation-preflight-20260801T150539Z.json`](raw/revalidation-preflight-20260801T150539Z.json).

## Preserved incident chronology

1. The first attempt launched PID `3497098` from the disposable task worktree.
   Its worktree HEAD advanced to `ac4fc7fe0e03cbd125389a830efde2873aedb73e`,
   worker cleanup left the process cwd deleted, and the first evidence falsely
   claimed `active_worker_leases_preserved=0` while the Antigravity worker was
   still active.
2. Human/Ops performed the admission-lock live rescue at
   `2026-07-31T23:26:14Z`, restoring
   `cbb36ff1fe385f3bc2690124ff22d8edc0056896` as PID `3509070`. The config
   SHA remained
   `728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc`.
3. A second attempt launched PID `3523046` from the persistent `012dab969...`
   root and recorded five early loops. Automatic rollback was not exercised;
   the first incident still required Human/Ops rescue.
4. Watchdog state later recorded PID `3523046` dead at
   `2026-08-01T05:29:29Z` (`reason=pid_not_alive`) and relaunched from the
   config-bound `dev-root`. This was a watchdog fallback, not proof of the
   missing bounded postcheck rollback transaction.
5. Watchdog performed another intentional deploy restart at
   `2026-08-01T14:19:34Z`, replacing PID `777098` with current PID `2933948`
   at `cbb36ff1...`.

This chronology deliberately preserves the failed first attempt, the manual
rescue, the temporary corrected process, and the later return to the incumbent
root. The five 2026-07-31 loops are historical observations, not current live
promotion proof.

## Current exact readback

| Field | Exact value |
|---|---|
| Captured at | `2026-08-01T15:05:39Z` |
| Live supervisor count | `1` |
| PID / start | `2933948` / `2026-08-01T14:19:34Z` |
| cwd | `/home/lupin/pantheon-ci-deploy/dev-root` |
| HEAD / tree | `cbb36ff1fe385f3bc2690124ff22d8edc0056896` / `6e9f5c440c3d7c9571cfc8038464989743201998` |
| origin | `https://github.com/ajoe734/pantheon.git` |
| config SHA-256 | `728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc` |
| authoritative shadow | `ok=true`, `caught_up=true`, equal `781bcee3...` hashes |
| independent atomic verify | `ok=true`, equal `d33d6cf3...` hashes |
| duplicate active task IDs | `0`, grouped from eight active lifecycle records by `task_id` |
| Antigravity readiness | `auth_ready=true`, local CLI supported, verified |
| configured primary | `gemini-3.6-flash-low` |

The journal advanced while the first independent projection was being built.
The same read-only helper's final `verify_projection` reload succeeded and
reported equal projected/expected hashes; the raw sample preserves both the
race note and the final atomic result.

## Lease and queue sample

The current task worker was:

- run `codex-20260801T145820Z-8cc1b389`;
- PID `3211377`, alive at the sample;
- heartbeat `2026-08-01T15:07:20Z`;
- queue event `evt-20260801T145756Z-701cba39`;
- matching queue `lease_owner=codex-20260801T145820Z-8cc1b389`;
- queue lease expiry `2026-08-01T15:37:33Z`.

However, one other record still classified active,
`codex-20260801T145808Z-d58e22be`, referenced a non-live PID at capture time.
Because promotion requires the whole active worker/queue set to be safe, this
single mismatch fails closed even though the alias-task lease itself matched.

## Root identity

The live rollback root exists and has the accepted incumbent HEAD/tree, but its
git status has 47 entries. It is reported as available and currently live, not
as clean.

The old content-addressed candidate root still has HEAD
`012dab969455e7146f2437159d7d38fc5904a195` and tree
`0314e0ccfad93c0deb57cb47b189f14f12a8ac6f`, but tracked canonical/derived
state files are modified there. It cannot be called clean or reused as an
immutable candidate. The task expressly forbids resetting or patching that
root.

## Transactional rollback dependency

The rejected PR #4433 implementation is superseded and must not be used. Its
governed replacement chain currently reads:

1. `SUP-RUNTIME-PROMOTION-SNAPSHOT-INVARIANTS-20260801`: done;
2. `SUP-RUNTIME-IDENTITY-ROOT-CONFIG-GIT-V2-20260801`: in progress;
3. `SUP-RUNTIME-PROMOTION-ROLLBACK-TRANSACTION-20260801`: todo;
4. `SUP-RUNTIME-PROMOTION-FAILURE-MATRIX-INTEGRATION-20260801`: todo.

Running the old one-way `swap-supervisor.sh` now would bypass this explicit
replacement chain and repeat the gap that caused the first live rescue.

## Acceptance disposition

| Acceptance | Result |
|---|---|
| Config bytes unchanged | **pass** |
| Exactly one supervisor discovered | **pass** |
| Authoritative projection caught up with equal hashes | **pass** |
| Provider primary ready | **pass** |
| No duplicate active task IDs | **pass** |
| Live supervisor is exact `012dab969...` target | **fail** |
| Clean immutable candidate root | **fail** |
| Every active worker PID/lease safe | **fail** |
| Automatic postcheck rollback transaction | **blocked** |
| Three fresh target-runtime loops | **not run** |

Overall result: **blocked preflight, no live mutation**.
