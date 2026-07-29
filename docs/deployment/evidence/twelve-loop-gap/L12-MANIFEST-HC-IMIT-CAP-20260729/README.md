# L12-MANIFEST-HC-IMIT-CAP-20260729 — Worker heartbeat closure

- Owner: `Codex`
- Reviewer: `Antigravity`
- Parent integration owner: `L12-MANIFEST-001`
- Evidence manifest: `evidence.json`

## Outcome

This workstream closes the manifest health gap for:

- `policy-learning-shadow-eval-scheduler`
- `paper-signal-producer`

Both scheduler-style workers now atomically publish a JSON heartbeat after
every tick. Their Compose healthchecks fail closed until a successful tick has
been recorded, reject stale heartbeat files, and reject the wrong worker
identity.

The policy-learning probe additionally requires
`production_training=fail_closed`. The paper signal probe requires all three
capital-safety markers:

```text
execution_mode=paper
live_capital_enabled=false
live_order_submission_enabled=false
```

`paper-signal-producer` also refuses to start if either
`PANTHEON_LIVE_BROKER_ENABLED` or `PANTHEON_CANARY_EXECUTION_ENABLED` is true.
Compose supplies literal `false` values for both flags on this service. No live
broker or capital authority was added.

## Operational contract

| Worker | Heartbeat file | Tick interval | Freshness ceiling | Compose probe |
| --- | --- | ---: | ---: | --- |
| `policy-learning-shadow-eval-scheduler` | `/tmp/policy-learning-shadow-eval-scheduler-health.json` | 3600 s default | `max(60, interval × 3)` | `python services/policy-learning/scheduler_worker.py healthcheck` |
| `paper-signal-producer` | `/tmp/paper-signal-producer-health.json` | 60 s default | `max(60, interval × 3)` | `python -m services.execution.lean_runtime.paper_signal_producer healthcheck` |

The files are container-local operational state, not durable business
authority. Losing a container also loses its heartbeat, which intentionally
returns the replacement container to `starting` until it completes a real
tick.

## Verification

Focused worker, Compose, and topology tests:

```text
27 passed, 1 warning
```

Broader imitation, signal, runtime bootstrap, algorithm safety, and generic
heartbeat-contract regression:

```text
30 passed, 4 skipped, 1 warning
```

The four skips are existing optional real-Postgres / LEAN availability gates.
`docker compose ... config --format json`, the two rendered probe assertions,
`py_compile`, and `git diff --check` also passed. Both task images built, and
container-local positive health probes passed; the paper image completed one
real zero-binding tick before its probe was evaluated.

After the reviewed head was refreshed with `dev` commit
`97d9ecd85826296d56d323ad6d8298c05c07ce08`, owner closeout reran the combined
suite on refresh commit `5ba43460682dd807dc6562224618ffa696e1a003`: 57 passed,
4 skipped, and 1 warning in 29.63 seconds. `py_compile`, the exact rendered
Compose healthcheck argv assertions, literal `false` live/canary assertions,
and `git diff --check` also passed. The refresh added only the already-landed
restart-proof evidence from `dev`; it did not change this task's implementation
or evidence paths.

After the alpha workstream merged into `dev`, PR #4335 was refreshed again at
`725d61aae8d8e599ec1a67a9d13b9cef4d2a6583`. Owner closeout reran the same
combined suite on that exact refresh head: 57 passed, 4 skipped, and 1 warning
in 16.76 seconds. `py_compile`, the exact rendered Compose healthcheck argv
assertions, literal `false` live/canary assertions, and `git diff --check` also
passed. The new merge delta is confined to already-landed alpha workstream
files from `dev`; this task's implementation and evidence paths are unchanged.

For final PR #4340 closeout, the branch was refreshed to `origin/dev`
`5b3bc8aa82e91b422a8bb1cc0c63a5960a0a362a` and validated at
`503f9ca4b5172662e7bfaa6b9b3ad796dd1c6218`: 57 passed, 4 skipped, and
1 warning in 17.78 seconds. `py_compile`, the exact rendered Compose
healthcheck argv assertions, literal `false` live/canary assertions, JSON
parsing, and `git diff --check` also passed. The refresh adds the already-landed
reconciliation health workstream from `dev`; it does not change this task's
heartbeat implementation or paper-capital safety contract.

## Independent review

Antigravity approved PR #4340 exact head
`c82e9bebeecdc04605714e92141a913c41ccfdff` after verifying the full
57-test validation suite, both Compose healthcheck assertions, the paper-only
safety flags, and this task-scoped evidence manifest. The governed review gate
recorded success as status `51266263710`.

## Baseline-only failure kept out of the claim

`services/execution/lean_runtime/test_devloop_wire.py` is not counted as task
proof. Its two lifecycle assertions fail on unmodified `origin/dev`
`d9cbbbfa2b0d4076f939a6d0fcc921406993d7af` because the test defaults its
lifecycle outbox to unwritable `/data/runtime/lifecycle-outbox`; two unrelated
signal-isolation assertions pass. The task branch reproduced the same `2
failed, 2 passed` result. This workstream does not alter the lifecycle outbox or
claim that pre-existing test gap is closed.

## Composition boundary

This packet is source and Compose proof for the `L12-MANIFEST-001` owner. It
does not claim:

- the new images are hosted on the replacement dev VM;
- the parent manifest has completed restart/readback proof;
- all other loop workers have healthchecks; or
- a live-capital or canary path is enabled.

The parent manifest and runtime verifier must consume the merged task commit,
render the accepted Compose manifest, and capture hosted container health
readback separately.
