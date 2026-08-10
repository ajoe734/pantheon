# SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731 evidence

Task: Promote merged alias reassignment guard into live supervisor runtime

Current owner: `Codex`

Current reviewer: `Claude`

Review decision: **pending**

Current delivery state: **blocked at distinct rollback destination preflight;
no live mutation performed**

## Outcome first

The exact accepted `origin/dev` candidate is prepared and clean, and every
runtime-health, projection, lease, duplicate-task, and provider gate is green.
The live supervisor still cannot be promoted safely.

Bootstrap preparation stops before the promotion lock, runtime-admission lock,
restart intent, config write, signal, launch, or rollback launch:

```text
ValueError: Fresh rollback runtime destination already exists:
/home/lupin/pantheon-ci-deploy/command-runtimes/0305c861f54c4082060120afdfbc012622e5ac0a
```

PR #4726 correctly removed cross-root same-SHA reuse. The existing `0305c861…`
checkout is not the same path/device/inode as the captured mutable incumbent
`dev-root`, so it cannot be guessed as rollback merely because commit and tree
match.

No config writer, `sync-dev-root.sh`, process signal, service restart,
canonical state edit, queue edit, provider change, or scheduler change ran.

## Exact accepted candidate

| Field | Exact value |
|---|---|
| Root | `/home/lupin/pantheon-ci-deploy/command-runtimes/37ee6c5dfe43a60763b6d799b9dfa28bb8ea5a7d` |
| HEAD | `37ee6c5dfe43a60763b6d799b9dfa28bb8ea5a7d` |
| Tree | `231c719f5c7a1c774532983901046f1fefb40444` |
| Origin | `https://github.com/ajoe734/pantheon.git` |
| Status | clean |
| Basename = HEAD | true |
| Accepted `origin/dev` | exact |
| Immutable command-root validation | passed |

The alias guard merge `012dab969…` is an ancestor. The live entrypoint,
previous clean `0305c861…` runtime, and exact-tip candidate all have supervisor
blob `4a6136f2…`; the running bytes include the guard.

Creating this immutable checkout did not switch the live runtime. The live
config hash remained
`61c8aed05539a7fbd445e6696aff1bc3caf8b7995cf1ae59487e92f88f068aea`
before and after preparation.

## Source verification

The source chain is:

- alias guard: PR #4430, merge `012dab969…`;
- split mutable cwd/entrypoint capture: PR #4724, merge `8b77e779…`;
- cross-root same-SHA non-alias guard: PR #4726, reviewed head `51eb129f…`,
  merge `37ee6c5d…`.

Local source verification passed:

- full `scripts/test_promote_supervisor_runtime.py`: `329 passed in 69.70s`;
- four PR #4430 alias-guard focused tests: `4 passed in 1.85s`.

## Fresh exact-tip discover-only result

The exact-tip probe completed at `2026-08-10T18:03:30.514208Z` against the
live config. It made no writes and returned ineligible, as expected for the
legacy split process layout.

Passing invariants:

- immutable exact-tip candidate identity;
- readable live config and canonical state;
- healthy singleton supervisor with PID lock held;
- authoritative projection caught up with equal `bb6de6a0…` hashes;
- fresh loop sequence;
- worker/queue/worktree-lease parity with no duplicate active workers;
- required provider readiness;
- no orphaned in-progress tasks.

The only regular discover-only failures were the expected split-layout pair:

| Gate | Result |
|---|---|
| incumbent process identity | `Captured live config does not bind the exact canonical supervisor entrypoint` |
| governed launch contract | `Candidate/process identity is unavailable` |

The regular mode intentionally does not enable mutable-incumbent bootstrap.
The separate prepare-only call is therefore the deciding pre-mutation gate.

## Bootstrap preparation result

`OSPromotionBackend.prepare(candidate, bootstrap_mutable_incumbent=True)`
successfully captured the legacy split layout, then resolved the rollback
commit and tree:

| Identity | Root | Device / inode | HEAD |
|---|---|---|---|
| mutable incumbent cwd | `/home/lupin/pantheon-ci-deploy/dev-root` | `2049 / 1876888` | `0305c861…` |
| live entrypoint root | `command-runtimes/5877b644…` | `2049 / 12655621` | checkout HEAD `0305c861…` |
| occupied rollback destination | `command-runtimes/0305c861…` | `2049 / 13894090` | `0305c861…` |
| candidate | `command-runtimes/37ee6c5d…` | `2049 / 6665320` | `37ee6c5d…` |

The occupied rollback destination has tree `249eecac…`, but it is a different
root and inode from the mutable snapshot. PR #4726 therefore rejects it. This
is the safe behavior.

The archived PR #4711 task does not authorize reuse here. Its contract allows
reuse only when the destination is the exact snapshot root with identical
path/device/inode. Deleting, moving, overwriting, or accepting the sibling
checkout would violate that reviewed contract.

## No-mutation readback

After prepare failed:

- supervisor PID remained `1393542`;
- process generation remained start ticks `21477145`;
- cwd remained `/home/lupin/pantheon-ci-deploy/dev-root`;
- entrypoint remained `command-runtimes/5877b644…/.orchestrator/supervisor.py`;
- config SHA-256 remained `61c8aed0…`;
- authoritative projection remained `ok=true`, `caught_up=true`, with equal
  `bb6de6a0…` hashes;
- no admission lock, restart intent, signal, candidate launch, or rollback
  launch occurred.

The original 2026-07-31 config hash `728a6d90…` is historical and was already
not current before this dispatch. This worker preserved the exact current bytes
instead of rewriting config to imitate that old baseline.

Antigravity readiness also remains healthy: installed, authenticated, local
CLI worker supported, and selected model `gemini-3.6-flash-low` (last probe
`2026-08-10T17:52:01Z`). No provider configuration changed.

## Blocker and resume gate

Human/Ops or the supervisor must admit a narrow source-only follow-up that
defines a collision-safe, descriptor-bound rollback destination when the
commit-derived path is already occupied. This task does not invent or dispatch
that task, and the follow-up must preserve PR #4726's cross-root same-SHA
rejection.

It must not solve the collision by deleting, moving, overwriting, or
guess-reusing the existing `0305c861…` runtime.

This live-promotion task may resume only after:

1. the source-only follow-up is independently reviewed and merged;
2. exact-tip discover-only health/projection/lease/provider gates remain green;
3. prepare-only returns distinct immutable candidate and rollback roots plus
   exact launch contracts;
4. the governed `--promote --bootstrap-mutable-incumbent` transaction is then
   allowed to run with automatic rollback;
5. post-promotion readback proves one exact runtime, three fresh loops, equal
   projection hashes, lease parity, provider readiness, and unchanged
   transaction-bound config bytes.

Overall result: **blocked preflight, no live mutation**.

Machine-readable details are in
[`evidence.json`](evidence.json) and
[`raw/resume-preflight-20260810T180330Z.json`](raw/resume-preflight-20260810T180330Z.json).
