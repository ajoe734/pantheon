# SUP-AUTOWORKER-QUOTA-ROUTING-LIVE-CANARY-OPERATOR-V9-20260802

Status: **NO-GO preflight; task blocked (revalidated 2026-08-10)**

Current owner: Codex
Current canonical reviewer: Codex2

Original 2026-08-02 evidence owner/reviewer: Codex2 / Human/Ops

## 2026-08-10 redispatch result

The separately governed V10 rollout worker owned the only authorized
promotion transaction while this V9 worker remained an observer. Candidate
`6607b6a706b59670009965375e0a5dd6b5824fcf` was a clean standalone runtime at
tree `5eb3d3a18ba01bb2e2f53e442842aa7c86fec23c`, with zero Git-status entries
and zero `__pycache__`, `.pyc`, or `.pyo` paths.

The transaction failed closed at `2026-08-10T14:09:14.245497Z` before
baseline capture, config mutation, process signalling, candidate launch, or
rollback. The immutable incumbent `5877b64425c8d6aede147d6cbbc6fbb9e228c259`
contains three historical `__pycache__` directories and 36 bytecode files.
The durable external transaction is
`supervisor-runtime-promotion-20260810T140901039717Z-636096.json` with SHA-256
`bbd2b6f09587225682b5ac90070a49f14f72eb230cde154c6ae9f2943fe377ec`.

Post-abort readback kept PID `2272245`, start ticks `16301949`, incumbent
runtime SHA `5877b64425c8d6aede147d6cbbc6fbb9e228c259`, and live-config SHA-256
`8168c57646339d510499dafa7f02f5f7a7aa7f24c2d05e23c68e698f6dc6662e`
unchanged. V9 did not issue a second promotion call, did not clean the
incumbent, and did not probe, reassign, or dispatch a provider.

The V10 owner anchored a source-only follow-up packet for a provenance-bound,
capture-only incumbent bytecode boundary backed by a clean rollback checkout.
Until that source task is admitted, exact-head reviewed, merged, archived, and
followed by a successful separately governed rollout, V9 cannot truthfully run
the required ten candidate cycles or live quota-routing canary.

The final review gate also remains unresolved: the immutable acceptance text
requires exact-head Human/Ops review, while the current canonical reviewer is
Codex2. No reviewer-policy change is claimed by this evidence.

## Result

All five source dependencies are independently reviewed, canonically archived,
merged, and ancestors of candidate `0343e4b76f446735a9ee35a4a4977c33ea7b696e`.
The candidate's required checks passed, and it was installed as a clean,
persistent command-runtime clone without modifying `dev-root`, live config, or
the running process.

The read-only transaction preflight then rejected promotion before intent or
`TERM`:

- the live watchdog command still points to
  `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py`;
- the immutable candidate is rooted at
  `/home/lupin/pantheon-ci-deploy/command-runtimes/0343e4b76f446735a9ee35a4a4977c33ea7b696e`;
- therefore `incumbent_supervisor_process_identity_immutable` failed with
  `Captured live config does not bind the exact canonical supervisor entrypoint`;
- without that identity, the governed candidate/rollback launch contract could
  not be assembled.

No live transition was attempted. The incumbent PID `3538768`, start ticks
`3631742`, root `dev-root`, and SHA `941c15a34208e54e96cdd148ba3a5bfcd339abab`
were not signalled. The external live config remained byte-identical at
`728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc`.

## Why this is a hard gate

Changing the watchdog entrypoint before the transaction would allow a later
watchdog restart to jump to the candidate outside the transaction. Using the
legacy swap helper to move the current mutable-root incumbent into a persistent
root would also bypass the task's required gate-before-switch path. Neither is
authorized by this canary, so both were deliberately refused.

A separate governed bootstrap repair must atomically reconcile the live config
target and mutable-root incumbent with the immutable promotion transaction. It
must preserve a qualified rollback root and prohibit watchdog bypass. This
canary can be redispatched only after that repair is exact-head reviewed,
merged, archived, and included in a newly selected candidate.

## Containment and baseline

Because preflight stopped before any signal, the incumbent continued normally.
Seven fresh successful loop markers were observed after the failed preflight:

`19:39:14`, `19:40:07`, `19:41:01`, `19:41:53`, `19:42:42`, `19:43:36`, and
`19:44:28` UTC.

The ten immediately surrounding incumbent start intervals were 54, 48, 56, 55,
53, 54, 52, 49, 54, and 52 seconds: median 53.5 seconds, linear p95 55.55
seconds, maximum 56 seconds. Against the live 30-second setting, median
overshoot remained 23.5 seconds. These are old-runtime NO-GO baseline values,
not candidate cadence proof.

The sanitized runtime snapshot showed authoritative projection caught up,
zero duplicate active workers, one Codex2 queue event, a 13-second
queue-to-start interval for this worker, a healthy active `codex2-1` provider,
and current runtime-lock hold of 3.919 seconds. Historical peak lock hold was
327.321 seconds; that peak is retained as existing runtime evidence and is not
claimed as candidate behavior.

## Identity and governance

The live config continues to define separate identities:

| Agent | Account / quota group | Provider home | Slots |
|---|---|---|---:|
| Codex | `codex1` | `~/.codex` | 4 |
| Codex2 | `codex2` | `~/.codex2` | 4 |

No account equivalence, global mutual-review ban, fallback-order change, or
reviewer-policy change was introduced. Human/Ops remains the explicit reviewer.
No canary probe or cache quarantine ran, and no auth or API-key material was
read or modified.

## Isolated negative qualification

The following source-level matrices passed with live command/status bindings
removed from the test environment:

| Matrix | Result | Coverage |
|---|---:|---|
| provider permissions | 100 + 7 subtests | exact/generic cache failures, unsafe paths, concurrent quarantine, quota with/without reset, revoked auth, timeout, redaction |
| focused supervisor | 136 + 13 subtests | fixed cadence, telemetry, lock accounting, distinct quota fallback, stale-cache readmission, Human/Ops preservation, PID reuse, leases and duplicates |
| ai-status + review gate | 55 + 2 subtests | overlapping artifacts, atomic batch CAS, status leases, stale/missing exact-head bindings |
| promotion + health | 209 | preflight, PID reuse, rollback/failure matrix, temporal loop ordering, unknown-child containment |

These matrices qualify the merged source. They do not replace the required
promoted-runtime canary.

## Acceptance not claimed

Candidate launch, ten candidate cycles, candidate cadence/lock/queue metrics,
live cache quarantine, live quota projection, live Codex-to-Codex2 fallback,
candidate resume-generation refresh, and candidate rollback were not run and
are not claimed. Human/Ops should review this exact evidence head as a NO-GO
record and keep the task blocked rather than approve successful completion.

## Redispatch update — 2026-08-10 (owner reassigned to Claude)

Owner reassigned from Codex to Claude by Chair; canonical reviewer remains
Codex2. Revalidated rather than re-asserted the standing blocker.

Both prior source-only blockers are now resolved: `...PYCACHE-RESIDUE-20260810`
and `...LEGACY-RESIDUE-20260810` are merged and canonically archived (PR
#4718, PR #4716), and `dev` tip `0c34a0da0` contains both.

The remaining chain is still open:

- `SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810` is `review`,
  PR #4724 (`d69c3e66e5...`), `OPEN`/`MERGEABLE`, unreviewed.
- `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808` is `in_progress`
  (owner Codex, reviewer Claude) and holds the only active promotion lease.

V9 ran only a read-only `--discover-only` preflight against the plain
dev-root checkout (not a candidate clone) to confirm no accidental candidate
binding; it failed `candidate_runtime_identity_immutable` as expected. No
signal, launch, config edit, cache probe, or dispatch was performed.

V9 remains blocked pending: split-entrypoint PR #4724 merge, V10 rollout
verify completion, and Chair/Human/Ops reconciliation of the
Codex2-vs-Human/Ops final review-gate discrepancy.

## Redispatch update — 2026-08-11 (owned_ready_dispatch)

Revalidated rather than re-asserted the standing blocker.

`SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810` is now `done`
and canonically archived; its head `d69c3e66e5...` is confirmed an ancestor
of `origin/dev` tip `8b7624999`. That prior gating PR is resolved.

However, `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808` remains
`in_progress` (owner Codex, reviewer Claude) and, as of its last update
(2026-08-11T01:51:12Z), is actively starting a separately dispatched
transactional rollout retry through `sync-dev-root` right now. It still
holds the only active promotion lease/authority.

V9 ran only a read-only `promote_supervisor_runtime.py --discover-only
--json` against this task worktree checkout (not any candidate, incumbent,
or dev-root clone) to confirm no accidental candidate/incumbent binding; no
mutation was performed.

V9 remains blocked pending: `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808`
completing or releasing its active promotion lease, and Chair/Human/Ops
reconciliation of the Codex2-vs-Human/Ops final review-gate discrepancy.
