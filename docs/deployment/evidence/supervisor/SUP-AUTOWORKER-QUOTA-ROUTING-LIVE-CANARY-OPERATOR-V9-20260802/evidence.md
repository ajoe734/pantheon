# SUP-AUTOWORKER-QUOTA-ROUTING-LIVE-CANARY-OPERATOR-V9-20260802

Status: **NO-GO preflight; task blocked**

Owner: Codex2  
Reviewer: Human/Ops

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
